# Container images — Jetson build recipe

Container images for the Reachy Mini Jetson Assistant, built **on an aarch64
Jetson host** (Thor) with podman. Two Containerfiles, one shared, Thor-validated
rswebrtc build script, and the GPU-wheel provenance.

## Files

| File | Purpose |
|---|---|
| `Containerfile.r39.2` | **Default, fully validated.** JetPack 7.2 / L4T r39.2 (noble, Py 3.12). GPU wheels pulled from the published `packaging/jetson-wheels.json` release. |
| `Containerfile.r36.5` | **Legacy.** JetPack 6 / L4T r36.5 (jammy, Py 3.10). rswebrtc validated; GPU wheels must be staged in `wheels/` (no published JP6 set — see below). |
| `gst_rswebrtc_sys.sh` | Builds **only** the Rust rswebrtc plugin (`libgstrswebrtc.so`) against the system GStreamer. Self-contained: installs its own Rust toolchain + GStreamer dev headers. Thor-validated on both bases. |
| `build_thor.sh` | Orchestration: pushes these files to Thor, builds the image(s), and runs the smoke tests. Uses SSH key auth (`THOR_*` vars, default user `mana`). |

The build context is the **repository root** (the Containerfiles `COPY . /build`,
`COPY wheels/ ...`, `COPY container/gst_rswebrtc_sys.sh ...`). A `.dockerignore`
at the root keeps `.git`, model data, caches and secrets out of the context.

## Base images

| Target | Base | GStreamer (system) | Python |
|---|---|---|---|
| r39.2 | `docker.io/whitesscott/l4t-jetpack:r39.2.1` | 1.24.2 (nv* plugins present) | 3.12 |
| r36.5 | `nvcr.io/nvidia/l4t-jetpack:r36.4.0` + r36.5 apt repo | 1.20.3 (nv* plugins present) | 3.10 |

NVIDIA publishes **no** L4T container image for r39.2, so r39.2 uses the
community `whitesscott/l4t-jetpack` base (r39.2.1-matched). For r36.5 the newest
`nvidia/l4t-jetpack` is r36.4.0; its JetPack-optimized GStreamer 1.20.3 is kept as
is, with the r36.5 apt repo layered on for r36.5-matched userspace libs.

Both bases are verified at build time: if the `nv*` GStreamer plugins are missing
the Containerfile installs them from the NVIDIA apt repo (a guard, not the normal
path).

## Why we build on the system GStreamer

The wireless (no-cable) mode streams the camera and mic over WebRTC and renders
audio back out. That uses the **rswebrtc** elements `webrtcsrc` (in) and
`webrtcsink` (out) — the same elements the SDK's
`webrtc_client_gstreamer.py` uses. No apt package ships rswebrtc for aarch64, so
we build just that one Rust plugin against the GStreamer that is already in the
base image. Earlier prototypes compiled GStreamer 1.28 from source into a side
prefix (`/opt/gst128`) with rpath tweaks; that is **gone** — the plugin now links
against the distro GStreamer, which is simpler, smaller, and what the JetPack
toolchain actually ships.

## The rswebrtc recipe (validated)

`container/gst_rswebrtc_sys.sh`:

1. Installs the Rust toolchain (stable) and the GStreamer dev headers
   (`libgstreamer1.0-dev`, `libgstreamer-plugins-bad1.0-dev`, plus `libssl-dev` on
   jammy; `pkg-config`).
2. Clones `gst-plugins-rs` at tag **`gstreamer-1.24.0`** (workspace on
   `gstreamer-rs` branch 0.22) and builds the `net/webrtc` crate
   (`gst-plugin-webrtc`) with `--no-default-features`.
3. Bumps the lockfile's `time` crate from `0.3.34` (2024, won't compile on
   rustc ≥ 1.80) to `0.3.47` before building.
4. `cargo build -p gst-plugin-webrtc --release`, then installs
   `target/release/libgstrswebrtc.so` into the system GStreamer plugin dir
   (`/usr/lib/aarch64-linux-gnu/gstreamer-1.0/`) and runs `gst-inspect-1.0`.

**Why `gstreamer-1.24.0` + `--no-default-features`:** gst-plugins-rs gates its
FFI minimum GStreamer version behind `v1_XX` features. The 1.24.0-generation
`net/webrtc` crate defaults to `v1_22` (floor GStreamer 1.22), which would break
the r36.4 base (1.20.3). Building with `--no-default-features` drops that plugin
default and leaves the floor at **GStreamer 1.20**, so the *one* recipe builds on
both bases. `webrtcsrc`/`webrtcsink` are registered unconditionally in `lib.rs`, so
no feature is needed for them to appear.

### Validation evidence (Thor, 2026-10-13)

| Base | GStreamer | Result | `webrtcsrc` / `webrtcsink` |
|---|---|---|---|
| `whitesscott/l4t-jetpack:r39.2.1` (noble) | 1.24.2 | `RSWERTC_OK` | PRESENT / PRESENT |
| `nvcr.io/nvidia/l4t-jetpack:r36.4.0` (jammy) | 1.20.3 | `RSWERTC_OK` | PRESENT / PRESENT |

Both logs end with the plugin installed and `gst-inspect-1.0 webrtcsrc` /
`webrtcsink` returning full element descriptions. The Containerfiles additionally
re-check at build time via PyGObject:
`Gst.ElementFactory.make('webrtcsrc')` and `make('webrtcsink')` must be non-`None`.

## Python / SDK layers

- **`reachy-mini==1.3.1[gstreamer]`** — the version the project docs pin
  (SETUP.md, JETPACK_7_2_SETUP.md). The `[gstreamer]` extra pulls
  `PyGObject>=3.42.2,<=3.46.0`.
- **PyGObject 3.46.0 is built from sdist** — there are **no** aarch64 wheels for
  PyGObject on PyPI, so the base needs `libglib2.0-dev` +
  `libgirepository1.0-dev` + `gobject-introspection` to compile it against the
  system GStreamer/GLib.
- **`sounddevice`** (in `requirements.txt`) links PortAudio on Linux → base needs
  `libportaudio2` / `libportaudio-dev`.
- The assistant is **not** pip-installable (no `pyproject.toml` / `setup.py`); it
  runs in place from `/opt/assistant`, entry point `python run_vision_chat.py`.
  (There is no `python -m app.cli run-vision` command.)

## GPU runtimes (CTranslate2 STT + ONNX Runtime TTS)

### r39.2 — published, manifest-driven

`packaging/jetson-wheels.json` platform
`jp72-l4t39.2-cu13.2-py3.12-sm87` (release
`native-jp72-cu132-py312-sm87-r1`, `published: true`):

| Wheel | Version | SHA-256 | Size (B) |
|---|---|---|---|
| `ctranslate2` | `4.8.1+reachy.jp72.cu132.sm87` | `cd553ca1…38185a` | 17,999,182 |
| `onnxruntime_gpu` | `1.28.0+reachy.jp72.cu132.sm87` | `a4dd9c97…6ba5` | 55,961,323 |

The r39.2 Containerfile **downloads these two wheels from that manifest,
verifies SHA-256 + size, then installs** — so the image cannot drift from the
manifest. (The dead `pypi.jetson-ai-lab.io/jp72/cu132/` devpi index is intentionally
*not* used; the GitHub release is the source of truth.)

### r36.5 — no published set (the honest gap)

`jetson-wheels.json` has **no** JP6 platform entry, and the `jp6/cu126` devpi index
is live but serves **0 packages**. CUDA-enabled CP310 aarch64 wheels are therefore
not available from any public index. The r36.5 Containerfile requires them staged
in **`wheels/`** in the build context (see `wheels/README.md`) and hard-fails if
they are missing — it will not silently build CPU-only. To produce a JP6 set,
follow `docs/JETPACK_7_2_SETUP.md` § "Adding a new JetPack/runtime tuple" /
"Install candidate GPU wheels manually", then add a `jp6-…` platform to the
manifest and switch the r36.5 Containerfile to the same manifest-driven download.

## Build (on Thor)

`build_thor.sh` runs everything; for a manual build of just the r39.2 image:

```sh
# from the repo root, on the aarch64 Thor host
podman build -f container/Containerfile.r39.2 -t reachy-jetson:r39.2 .
```

Smoke test after build:

```sh
podman run --rm reachy-jetson:r39.2 \
  python -c "import gi; gi.require_version('Gst','1.0'); \
from gi.repository import Gst; Gst.init(None); \
assert Gst.ElementFactory.make('webrtcsrc'); assert Gst.ElementFactory.make('webrtcsink'); \
print('RSWERTC_OK')"
```

`BUILD_WEBRTC=0` skips the Rust plugin build (image without wireless media).
