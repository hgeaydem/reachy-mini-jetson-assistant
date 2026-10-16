# JetPack 6 native GPU wheels for Reachy Mini Jetson Assistant

This release provides the exact native GPU runtime wheels used by `./scripts/setup_jetson.sh` on the JetPack 6 compatibility tuple below.

## Supported tuple

- NVIDIA Jetson Orin family (`sm_87`) — Orin Nano / NX / AGX
- JetPack 6.2 / L4T R36 (target device reports `36.5.0`)
- CUDA 12.6 and cuDNN 9.3 supplied by JetPack (resolved from the container/userspace at runtime)
- Ubuntu 22.04 on `aarch64`
- CPython 3.10

These wheels are not compatible with JetPack 7, Thor (`sm_110`), another Python ABI, or a generic aarch64 machine. The installer fails closed when the host does not exactly match the manifest.

## Assets

- `ctranslate2-4.8.1+reachy.jp6.cu126.sm87-cp310-cp310-linux_aarch64.whl`
- `onnxruntime-1.23.2+reachy.jp6.cu126.sm87-cp310-cp310-linux_aarch64.whl`
- `native-jp6-cu126-py310-sm87-r1-SHA256SUMS`

The wheels dynamically use CUDA, cuBLAS, and cuDNN from JetPack; they do not bundle NVIDIA CUDA/cuDNN runtime libraries. The evidence directory `packaging/releases/native-jp6-cu126-py310-sm87-r1/` contains the complete wheel member and ELF dependency inventory, SPDX 2.3 SBOM, upstream revisions, build commands and environment, runtime validation, licenses, third-party notices, and nested checksums. No source patches were applied (see `PATCHES/README`).

Note: the ONNX Runtime wheel is the `onnxruntime` distribution (not `onnxruntime_gpu`): kokoro-onnx's `onnxruntime-gpu` dependency is x86_64-only, so on aarch64 the GPU provider must come from the `onnxruntime` package itself.

## Install

On a bare JetPack 6 Orin:

```bash
git clone --branch jp6-cuda-wheels-device-presets https://github.com/hgeaydem/reachy-mini-jetson-assistant.git
cd reachy-mini-jetson-assistant
./scripts/setup_jetson.sh --check-only
./scripts/setup_jetson.sh
```

On a RHEL-based Orin (no Ubuntu userspace), run inside the JetPack container, which provides the CPython 3.10 and CUDA 12.6 toolchain the wheels were built against:

```bash
podman pull nvcr.io/nvidia/l4t-jetpack:r36.4.0
podman run --rm -it --net=host --device nvidia.com/gpu=all \
    --security-opt label=disable nvcr.io/nvidia/l4t-jetpack:r36.4.0 bash
# inside the container:
python3 -m venv /opt/venv && . /opt/venv/bin/activate
pip install ctranslate2==4.8.1+reachy.jp6.cu126.sm87 onnxruntime==1.23.2+reachy.jp6.cu126.sm87 \
    --no-index --find-links <wheel-dir>
python -c "import onnxruntime as o; assert 'CUDAExecutionProvider' in o.get_available_providers()"
python -c "import ctranslate2; assert ctranslate2.get_cuda_device_count() > 0"
```

## Release status

The wheel audit (member inventory, license hashes, ELF dependency resolution, zero bundled NVIDIA libraries) passes. Target-device runtime validation inside the r36.4.0 container is recorded in `packaging/releases/native-jp6-cu126-py310-sm87-r1/runtime-validation.json`.
