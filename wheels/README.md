# GPU wheelhouse (build input)

Staging area for **prebuilt CUDA wheels** used by `container/Containerfile.r36.5`
(JetPack 6, CP310, aarch64). The r36.5 Containerfile does
`COPY wheels/ /opt/gpu-wheels/` and hard-fails if the two files below are
missing — so stage them before building that image.

## Why a wheelhouse

`packaging/jetson-wheels.json` publishes a wheel set for **JetPack 7.2 only**
(`jp72-l4t39.2-cu13.2-py3.12-sm87`, CP312). There is no JetPack 6 platform entry,
and the `jp6/cu126` devpi index currently serves 0 packages. CUDA-enabled
CP310 aarch64 wheels are therefore not available from a public index and must be
supplied locally.

This is the same wheelhouse `scripts/setup_jetson.sh --wheel-dir` consumes on a
bare Orin Nano — see `docs/JETPACK_7_2_SETUP.md` § "Adding a new JetPack/runtime
tuple" and § "Install candidate GPU wheels manually" for how a wheel set is built
and published.

## Required files

Place in this directory before running the r36.5 build:

```
wheels/
├── ctranslate2-<ver>+<local>-cp310-cp310-linux_aarch64.whl   # CUDA STT
└── onnxruntime_gpu-<ver>+<local>-cp310-cp310-linux_aarch64.whl  # CUDA TTS
```

The exact local version tags are your own (e.g. `+reachy.jp6.cu124.sm87`); the
Containerfile globs on `ctranslate2-*-cp310-*.whl` and
`onnxruntime_gpu-*-cp310-*.whl`.

> `.gitignore` does **not** exclude `wheels/`, but the `.whl` files themselves are
> large binaries you build/stage locally. This README is the tracked placeholder
> that makes the `COPY wheels/` step valid even before wheels are present (the
> build then stops at the explicit FATAL check with a clear message, rather than
> failing on a missing COPY source).
