#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

usage() {
  echo "Usage: $0 <ctranslate2-wheel-path-or-URL> <onnxruntime-wheel-path-or-URL>" >&2
  echo "Activate the project Python virtual environment first (3.12 for" >&2
  echo "JetPack 7.2 wheels, 3.10 for JetPack 6 wheels)." >&2
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  echo "error: no active virtual environment" >&2
  usage
  exit 2
fi

python_bin="$VIRTUAL_ENV/bin/python"
ct2_wheel="$1"
ort_wheel="$2"

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "error: these wheels require an aarch64 Jetson" >&2
  exit 1
fi

# Derive the required CPython tag from the wheel filenames (cp312 for
# JetPack 7.2, cp310 for JetPack 6). This keeps the installer reusable
# across both platforms instead of hard-coding a single Python.
cp_tag="$(basename "$ort_wheel" | grep -oE 'cp3[0-9]{2}' | head -1)"
if [[ -z "$cp_tag" ]]; then
  echo "error: cannot read a CPython tag from the ONNX Runtime wheel name" >&2
  exit 1
fi

"$python_bin" - "$cp_tag" <<'PY'
import sys

required = sys.argv[1]  # e.g. "cp312"
major, minor = int(required[3:5]), int(required[5:7])
if sys.version_info[:2] != (major, minor):
    raise SystemExit(
        f"error: wheels require Python {major}.{minor}, "
        f"found {sys.version.split()[0]}"
    )
PY

# L4T guard: accept either JetPack 6 (R36) or JetPack 7 (R39) Tegra boards.
# The real compatibility gate is the exact wheel-manifest match performed by
# setup_jetson.sh; this only rejects obviously-wrong (non-Tegra / x86) hosts.
if [[ -r /etc/nv_tegra_release ]] && ! grep -qE '^# R(36|39) ' /etc/nv_tegra_release; then
  echo "error: these wheels target a JetPack 6/7 Tegra board" >&2
  exit 1
fi

"$python_bin" -m pip uninstall -y onnxruntime onnxruntime-gpu ctranslate2
"$python_bin" -m pip install --no-deps "$ct2_wheel" "$ort_wheel"

"$python_bin" - <<'PY'
import os

import ctranslate2
import onnxruntime as ort

cuda_devices = ctranslate2.get_cuda_device_count()
providers = ort.get_available_providers()
print(f"CTranslate2: {ctranslate2.__version__}; CUDA devices: {cuda_devices}")
print(f"ONNX Runtime: {ort.__version__}; providers: {providers}")
expected_ct2 = os.environ.get("REACHY_EXPECTED_CTRANSLATE2_VERSION")
expected_ort = os.environ.get("REACHY_EXPECTED_ONNXRUNTIME_VERSION")
if expected_ct2 and ctranslate2.__version__ != expected_ct2:
    raise SystemExit(
        f"error: CTranslate2 version {ctranslate2.__version__} != {expected_ct2}"
    )
if expected_ort and ort.__version__ != expected_ort:
    raise SystemExit(f"error: ONNX Runtime version {ort.__version__} != {expected_ort}")
if cuda_devices < 1:
    raise SystemExit("error: CTranslate2 cannot see a CUDA device")
if "CUDAExecutionProvider" not in providers:
    raise SystemExit("error: ONNX Runtime has no CUDAExecutionProvider")
PY

echo "JP7.2 GPU runtime validation passed."
echo "Note: packages that declare 'onnxruntime' may make pip check report that"
echo "name as missing even though the onnxruntime-gpu module is installed."
