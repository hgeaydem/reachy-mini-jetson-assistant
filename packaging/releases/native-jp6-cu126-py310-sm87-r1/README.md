# JP6 native wheel release evidence

Status: **WHEELS AUDITED AND RUNTIME-VALIDATED ON TARGET** (Orin Nano Super,
L4T R36 REV 5.0, in the r36.4.0 container; see `runtime-validation.json` and
`BUILD.md` § "Runtime validation on the target"). The wheel audit passes for
the compatibility tuple below.

This directory is the evidence bundle for `native-jp6-cu126-py310-sm87-r1`. The wheel files remain external release assets and are identified by `SHA256SUMS` and `packaging/jetson-wheels.json`.

`EVIDENCE-SHA256SUMS` authenticates every file in this evidence directory except itself. The nested checksum files under `LICENSES/`, `PATCHES/`, and `build-config/` make those release-bundle sections independently verifiable.

## Validation status

| # | Validation item | Status | Evidence |
|---|---|---|---|
| 1 | Confirm the wheels do not bundle NVIDIA CUDA/cuDNN binaries | Pass | `wheel-inventory.json` inventories every member by content magic and filename; both wheels report zero bundled NVIDIA library candidates (`bundled_nvidia_library_count: 0`). |
| 2 | Inventory every shared object and dependency | Pass | The CT2 wheel inventories its 32 members (two AArch64 ELF shared objects); the ORT wheel inventories its 325 members (four AArch64 ELF shared objects). Zero static libraries/object files. Python `Requires-Dist` entries, ELF `DT_NEEDED` entries, and build dependencies are in `wheel-inventory.json`, `source-dependencies.json`, and `sbom.spdx.json`. |
| 3 | Include upstream MIT licenses in each wheel and release bundle | Pass | CTranslate2's MIT license is the wheel member `LICENSES/LICENSE` (SHA-256 `54aa79d9…`, required by the manifest and verified by the audit) and is mirrored under `LICENSES/ctranslate2/`. ONNX Runtime's MIT license is the wheel member `LICENSE` (SHA-256 `2f07c727…`) mirrored under `LICENSES/onnxruntime/`. |
| 4 | Preserve ONNX Runtime's complete third-party notices | Pass | The wheel copy of `ThirdPartyNotices.txt` (SHA-256 `e9e90971…`) is byte-for-byte the upstream v1.23.2 file and is mirrored under `LICENSES/onnxruntime/`. |
| 5 | Record all downstream modifications | Pass | No source patches exist for JP6. `PATCHES/` contains only a `README` stating this, plus its checksum file. The CT2 modifications (wheel packaging, local version, `$ORIGIN` rpath) and the ORT modifications (build flags, numpy 2.x re-link, repackaging as the `onnxruntime` distribution) are itemized in `packaging/jetson-wheels.json → source.modifications` and in `BUILD.md`. |
| 6 | Record upstream tags and commit SHAs | Pass | `BUILD.md`, `packaging/jetson-wheels.json`, and `source-dependencies.json`. |
| 7 | Provide reproducible commands and complete build environment | Pass | `BUILD.md`, `build-environment.txt`, `dpkg-packages.txt`, `python-packages.txt`, `environment-SHA256SUMS`, and both exact CMake configure caches in `build-config/`. |
| 8 | Confirm dynamic use of JetPack CUDA/cuDNN | Pass (target) | ELF resolution in `wheel-inventory.json` points outside the wheels to the container's CUDA 12.6 runtime and cuDNN 9.3.0; no dependency is unresolved. `runtime-validation.json` is the live record from the Orin Nano Super target (L4T R36 REV 5.0, driver 540.5.0, r36.4.0 container, rootful podman): CTranslate2 reports 1 CUDA device, `CUDAExecutionProvider` is active, and the CUDA-bound ONNX Runtime convolution produces its output on `cuda` with no fallback. See `BUILD.md` § "Runtime validation on the target" for the rootful-podman / `--device /dev/nvmap` requirements. |

## Release artifacts

| Artifact | Size | SHA-256 |
|---|---:|---|
| `ctranslate2-4.8.1+reachy.jp6.cu126.sm87-cp310-cp310-linux_aarch64.whl` | 18,373,084 | `05e51ceefca862542eef28511c2fb90a7cf1dacd300338e1e787a253b2b08d1b` |
| `onnxruntime-1.23.2+reachy.jp6.cu126.sm87-cp310-cp310-linux_aarch64.whl` | 79,138,779 | `caa7f1576125bda97247b73bf06041f995b0236bcce7435842255c9587acf458` |

## Native artifact summary

CTranslate2 contains `ctranslate2/_ext.cpython-310-aarch64-linux-gnu.so` and `ctranslate2/libctranslate2.so.4`. ONNX Runtime contains `libonnxruntime.so.1.23.2`, `libonnxruntime_providers_cuda.so`, `libonnxruntime_providers_shared.so`, and `onnxruntime_pybind11_state.so`. All six are dynamically linked AArch64 ELF files, and neither wheel contains a `.a`, `.o`, `.obj`, NVIDIA runtime library, or NVIDIA driver library.

The ONNX Runtime build linked against the container's CUDA 12.6 runtime; the finished CUDA provider resolves `libcublas.so.12` and `libcudart.so.12` at runtime from the JetPack userspace. The wheel audit fails if an unresolved dependency or a bundled NVIDIA library is found.

## Recreate the evidence

```bash
./scripts/capture_jetson_build_environment.sh packaging/releases/native-jp6-cu126-py310-sm87-r1 /opt/wheel-out/venv3/bin/python3
python scripts/audit_jetson_wheels.py --manifest packaging/jetson-wheels.json --platform-id jp6-l4t36.5-cu12.6-py3.10-sm87 --wheel-dir /opt/wheel-out --output-dir packaging/releases/native-jp6-cu126-py310-sm87-r1 --source-dependencies packaging/releases/native-jp6-cu126-py310-sm87-r1/source-dependencies.json
python scripts/validate_jp72_gpu_wheels.py --config ctranslate2==4.8.1+reachy.jp6.cu126.sm87,onnxruntime==1.23.2+reachy.jp6.cu126.sm87 --platform-id jp6-l4t36.5-cu12.6-py3.10-sm87 --output packaging/releases/native-jp6-cu126-py310-sm87-r1/runtime-validation.json
```

The validator script is shared with the JP7.2 release: the `--config` flag inlines the expected package versions so it runs on a build machine that does not carry the manifest's JP6 platform entry.

`release.published` in `packaging/jetson-wheels.json` is set to `true` once the immutable release assets are available to the exact-match installer.
