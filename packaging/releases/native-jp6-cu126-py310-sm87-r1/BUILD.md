# Reproducing the JP6 wheel candidates

This record describes how to reproduce the artifacts named in `packaging/jetson-wheels.json`.

## Compatibility tuple

| Field | Value |
|---|---|
| Release tag | `native-jp6-cu126-py310-sm87-r1` |
| Architecture | `aarch64` |
| Jetson | Orin (Nano / NX / AGX) |
| L4T / JetPack | `36.5.0` / JetPack 6.2 |
| Userspace | Ubuntu 22.04 (jammy), provided by the build container |
| CUDA | 12.6 (12.6.68) |
| cuDNN | 9.3.0 |
| Python ABI | CPython 3.10, `cp310-cp310-linux_aarch64` |
| GPU target | Orin, SM 8.7 |

The exact installed APT packages, Python packages, OS, L4T release, compiler
versions, and CUDA compiler output are archived in `build-environment.txt`,
`dpkg-packages.txt`, and `python-packages.txt`. The configure state used by each
build is preserved in `build-config/ctranslate2-CMakeCache.txt` and
`build-config/onnxruntime-CMakeCache.txt`.

> **Build host vs. target L4T.** The wheels were built *inside* the
> `nvcr.io/nvidia/l4t-jetpack:r36.4.0` container. That container ships Ubuntu
> 22.04 / Python 3.10.12 / CUDA 12.6 — the genuine JetPack 6 toolchain — but it
> does **not** carry its own `/etc/nv_tegra_release`. Any L4T string that appears
> in host-facing evidence (the `[l4t]` section of `build-environment.txt` and the
> `host.l4t` field of `wheel-inventory.json`) is read from the *build host's*
> flash (an AGX reporting R39). The matching `l4t` value in
> `packaging/jetson-wheels.json` is the **target** device's L4T (`36.5.0`), which
> is what `scripts/setup_jetson.sh` compares against on the device being
> installed. The container toolchain is authoritative for the wheel ABI; the
> host L4T is provenance only.

## Source identity

| Project | Upstream tag | Tag object | Commit | Downstream status |
|---|---|---|---|---|
| CTranslate2 | `v4.8.1` | `399239a790ad0da4e4363e0dcbb83495b5abd742` | `0d8bcd362ac75ef860ef161d6f0efad0ae439ff0` | C++ source unmodified; wheel packaging and local version modified |
| ONNX Runtime | `v1.23.2` | lightweight tag | `a83fc4d58cb48eb68890dd689f94f28288cf2278` | source unmodified; build-level changes and repackaging only |

There are **no source patches** for JP6. ONNX Runtime is built from the clean
`v1.23.2` source; the only downstream modifications are build flags (re-link
against the numpy 2.x ABI, enable `CUDAExecutionProvider` at SM 8.7, shared
library) and repackaging as the `onnxruntime` distribution with the local version
`1.23.2+reachy.jp6.cu126.sm87`. (JP6 ships no `patches/` member because nothing
was applied to the source tree.)

## Build environment

Both wheels are built inside `nvcr.io/nvidia/l4t-jetpack:r36.4.0`, the newest
JetPack 6 container NVIDIA publishes. It already contains the JetPack-optimized
GStreamer, CUDA 12.6, and cuDNN 9.3, so no JetPack components are reinstalled.

```bash
# container is already running as `jp6_final6` on the AGX build host
podman exec -it jp6_final6 bash

# ORT build environment (numpy 2.x ABI) — the final build
python3 -m venv /opt/wheel-out/venv3
source /opt/wheel-out/venv3/bin/activate
pip install "pip==26.2.1" "setuptools==84.0.0" "wheel==0.48.0" "build==1.5.0" \
    "pybind11==2.11.1" "numpy==2.2.6" "cmake==3.31.10" "packaging==26.3"

# CT2 build environment
python3 -m venv /opt/wheel-out/venv
source /opt/wheel-out/venv/bin/activate
pip install "pybind11==2.11.1" "numpy==1.26.4"
```

Toolchain recorded in `build-environment.txt`: GCC/G++ 11.4.0, GNU ld 2.38,
glibc 2.35, CMake 3.31 (venv), CUDA compiler 12.6.68, cuDNN 9.3.0.

## CTranslate2

Clones `v4.8.1` with submodules, builds the shared core for SM 8.7, and stages a
Python wheel with the downstream local version. Use new empty directories for
every release candidate.

```bash
export REACHY_BUILD_ROOT=/opt/wheel-out
export CT2_SOURCE="$REACHY_BUILD_ROOT/CTranslate2/src"
export CT2_BUILD="$REACHY_BUILD_ROOT/CTranslate2/build"
export CT2_PREFIX="$REACHY_BUILD_ROOT/ctranslate2-stage/prefix"
export CT2_STAGE="$REACHY_BUILD_ROOT/ctranslate2-stage/python"
git clone --branch v4.8.1 --recurse-submodules https://github.com/OpenNMT/CTranslate2.git "$CT2_SOURCE"
test "$(git -C "$CT2_SOURCE" rev-parse HEAD)" = 0d8bcd362ac75ef860ef161d6f0efad0ae439ff0
test "$(git -C "$CT2_SOURCE" rev-parse refs/tags/v4.8.1^{})" = 0d8bcd362ac75ef860ef161d6f0efad0ae439ff0
source "$REACHY_BUILD_ROOT/venv/bin/activate"
cmake -S "$CT2_SOURCE" -B "$CT2_BUILD" -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=ON -DWITH_CUDA=ON -DWITH_CUDNN=ON -DCUDA_ARCH_LIST=8.7 \
    -DWITH_MKL=OFF -DWITH_OPENBLAS=OFF -DOPENMP_RUNTIME=NONE \
    -DBUILD_CLI=OFF -DBUILD_TESTS=OFF \
    -DCMAKE_INSTALL_PREFIX="$CT2_PREFIX"
cmake --build "$CT2_BUILD" --parallel
cmake --install "$CT2_BUILD"
```

Stage the Python package: copy `$CT2_SOURCE/python` to `$CT2_STAGE`, install
`libctranslate2.so.4.8.1` as `ctranslate2/libctranslate2.so.4`, set
`__version__ = "4.8.1+reachy.jp6.cu126.sm87"`, point `license_files` at the
release-bundle `LICENSES/ctranslate2` tree, and add an `$ORIGIN` rpath so the
bundled core library is found at load time. Build with
`python -m build --wheel --no-isolation`.

The resulting wheel contains `ctranslate2/_ext.cpython-310-aarch64-linux-gnu.so`
and `ctranslate2/libctranslate2.so.4`. It must not contain CUDA, cuBLAS, or
cuDNN shared/static libraries (the audit reports zero bundled NVIDIA library
candidates).

## ONNX Runtime

Builds the clean `v1.23.2` source against the container's CUDA 12.6 and
cuDNN 9.3 (exposed to CMake through a symlinked `cudnn_jp6` include/lib dir).
The final build directory is `build-jp6-np2` (numpy 2.x ABI, `SHARED_LIB=ON`).

```bash
export REACHY_BUILD_ROOT=/opt/wheel-out
export ORT_SOURCE="$REACHY_BUILD_ROOT/onnxruntime"
export ORT_BUILD="$ORT_SOURCE/build-jp6-np2"
export CUDNN_DISCOVERY="$REACHY_BUILD_ROOT/cudnn_jp6"
git clone --branch v1.23.2 --recursive https://github.com/microsoft/onnxruntime.git "$ORT_SOURCE"
test "$(git -C "$ORT_SOURCE" rev-parse HEAD)" = a83fc4d58cb48eb68890dd689f94f28288cf2278
mkdir -p "$CUDNN_DISCOVERY/include" "$CUDNN_DISCOVERY/lib"
ln -s /usr/include/aarch64-linux-gnu/cudnn.h "$CUDNN_DISCOVERY/include/"
ln -s /usr/lib/aarch64-linux-gnu/libcudnn.so.9 "$CUDNN_DISCOVERY/lib/"
source "$REACHY_BUILD_ROOT/venv3/bin/activate"
"$ORT_SOURCE/build.sh" --build_dir "$ORT_BUILD" --config Release --update \
    --build --build_wheel --skip_tests --skip_submodule_sync \
    --parallel --nvcc_threads 1 --compile_no_warning_as_error \
    --use_cuda --disable_ml_ops --cuda_home /usr/local/cuda \
    --cudnn_home "$CUDNN_DISCOVERY" \
    --cmake_extra_defines CMAKE_CUDA_ARCHITECTURES=87 onnxruntime_BUILD_SHARED_LIB=ON
```

The generated upstream `onnxruntime_gpu` wheel is repacked to the
`onnxruntime` distribution with the local version
`1.23.2+reachy.jp6.cu126.sm87`. The complete upstream `LICENSE` and
`ThirdPartyNotices.txt` are copied byte-for-byte into `.dist-info/licenses/`,
and a `BUILD-AND-LICENSE-NOTICE.txt` records that the wheel is a downstream
rebuild. This matters because kokoro-onnx's `onnxruntime-gpu` dependency is
x86_64-only: on aarch64 the GPU provider must come from the `onnxruntime`
package itself, or pip would install a CPU wheel on top.

The resulting wheel contains `libonnxruntime.so.1.23.2`,
`libonnxruntime_providers_cuda.so`, `libonnxruntime_providers_shared.so`, and
`onnxruntime_pybind11_state.so` — all dynamically linked AArch64 ELF. Neither
wheel contains a `.a`, `.o`, `.obj`, NVIDIA runtime library, or NVIDIA driver
library.

## Audit and runtime validation

Place both resulting wheels in one wheelhouse. Update the candidate filenames,
sizes, and SHA-256 values in `packaging/jetson-wheels.json` before running the
audit; a rebuild is a new artifact and must never reuse stale checksums.

```bash
python scripts/audit_jetson_wheels.py \
    --manifest packaging/jetson-wheels.json \
    --platform-id jp6-l4t36.5-cu12.6-py3.10-sm87 \
    --wheel-dir /opt/wheel-out \
    --output-dir packaging/releases/native-jp6-cu126-py310-sm87-r1 \
    --source-dependencies packaging/releases/native-jp6-cu126-py310-sm87-r1/source-dependencies.json

python scripts/validate_jp72_gpu_wheels.py \
    --config ctranslate2==4.8.1+reachy.jp6.cu126.sm87,onnxruntime==1.23.2+reachy.jp6.cu126.sm87 \
    --platform-id jp6-l4t36.5-cu12.6-py3.10-sm87 \
    --output packaging/releases/native-jp6-cu126-py310-sm87-r1/runtime-validation.json
```

### Runtime validation on the target (Orin Nano Super)

`runtime-validation.json` in this release dir is the record of a live run on
the target device: an Orin Nano Super, L4T R36 REV 5.0 (`l4t 36.5.0`),
driver 540.5.0, executed inside a `nvcr.io/nvidia/l4t-jetpack:r36.4.0`
container (the host itself is RHEL 9 / Python 3.9, so the cp310 wheels can
only be exercised from the container).

Container recipe that works on that host:

```bash
sudo podman run -d --name gpuroot \
    --device nvidia.com/gpu=all \
    --device /dev/nvmap \
    --net=host \
    --security-opt label=disable \
    nvcr.io/nvidia/l4t-jetpack:r36.4.0 sleep 3600
```

Two host-specific requirements, both observed failures without them:

- **Rootful podman is mandatory.** The Jetson GPU device nodes are owned
  `root:video` with mode `0640` (`/dev/nvmap`, `/dev/nvhost-*`). In a
  *rootless* container (podman-as-user) those owners fall outside the
  user-namespace UID/GID map, so the node remaps to `nobody:nogroup` inside
  the container and even `--privileged` / `--group-add video` /
  `--security-opt label=disable` cannot restore access: the CUDA driver
  aborts with `NvRmMemInitNvmap failed with Permission denied` →
  `cuInit == 999` → zero devices. Under *rootful* podman the container's
  root is host uid 0, the node stays `root:video` and opens normally;
  `cuInit` returns 0 and `cuDeviceGetCount == 1` ("Orin").
- **`--device /dev/nvmap` must be passed explicitly.** The CDI
  `nvidia.com/gpu` spec on L4T lists the `nvhost-*` nodes but not
  `/dev/nvmap`, so the Tegra memory manager is unreachable without the extra
  device flag, regardless of SELinux/AppArmor state.

The host's SELinux is enforcing, hence `--security-opt label=disable`.
`python3.10-venv` + `python3.10-dev` are not shipped in the image and are
installed with apt before the venv is created. The recorded result
(`created 2026-10-16T13:07:53Z`, `passed: true`) shows CTranslate2 seeing 1
CUDA device, `CUDAExecutionProvider` active, and the CUDA-bound ONNX
convolution producing its output on `cuda`.

The validator is shared with JP7.2: the `--config` flag inlines the expected
versions so the manifest's platform entry is not required on the build machine,
and it accepts either the `onnxruntime_gpu` (JP7.2) or `onnxruntime` (JP6)
distribution key. It runs a CUDA-bound ONNX Runtime convolution with
`disable_fallback()` (so a CPU fallback would fail) and asserts
`CUDAExecutionProvider` plus at least one CTranslate2 CUDA device.

The release audit must report zero bundled NVIDIA library candidates, zero
static libraries/object files in either wheel, no unresolved ELF dependencies,
and JetPack package ownership for every CUDA/cuBLAS/cuDNN resolution.
