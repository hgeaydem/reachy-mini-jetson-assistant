# Known Issues

Hardware limitations of the L4T/NVRM stack that affect this project, with
evidence. These are NOT application bugs — they survive every app-level
workaround and must be worked around at the image/JetPack level.

## 1. GPU V4L2 video decode/encode is broken on L4T r39.2 (JetPack 7.2)

**Status:** confirmed broken on r39.2.0 and r39.2.1, on Orin Nano 8GB (sm_87),
AGX Orin 64GB (sm_87) and Thor (sm_101) — i.e. on the entire r39.2
generation, not a single board or single revision. **No r39.2.x has fixed it
as of 2026-10-19.**

**Impact:** every `nv*` GStreamer element backed by the GPU V4L2 device —
`nvv4l2decoder`, `nvv4l2h264enc`/`nvv4l2h265enc`, `nvvidconv` with NVMM
buffers, and anything routing through the Argus stack (`nvarguscamerasrc`) —
fails. For this project that affects the **WebRTC video path**: the robot's
camera stream is H.264 over WebRTC, and GStreamer's `webrtcbin` decodes it on
the Jetson with the hardware decoder, so the JP7.2 vision path (see
`app/remote_media.py` / the SDK `webrtc_client_gstreamer.py` pipeline) cannot
run. What is **not** affected: the plain `v4l2src` OpenCV camera path
(Lite mode), and the SDK's UDP raw-camera debug path
(`gstreamer_udp_camera.py` = `appsrc ! rtpvrawpay ! udpsink`, CPU-side
encode/decode — no GPU V4L2 element).

### Root cause

The r39.2 NVRM open kernel module is a `TempVersion` build (r595_00). At boot
its DCE (Display & Camera Engine / GPU coprocessor) initialization fails with
RM control-call errors:

```
NVRM: loading NVIDIA UNIX Open Kernel Module for aarch64  TempVersion  Release Build  (r595_00)
NVRM: GPU1 rpcRmApiControl_dce: NVRM_RPC_DCE: Failed RM ctrl call cmd:0x731341
       result 0xffff: Failure: Generic Error [NV_ERR_GENERIC]
NVRM: GPU1 rpcRmApiControl_dce: NVRM_RPC_DCE: Failed RM ctrl call cmd:0x731341
       result 0x65: Call timed out [NV_ERR_TIMEOUT]
```

`/dev/v4l2-nvdec` (the GPU decode node) is owned by that DCE engine; on Thor
`nvidia-smi -i <dce-gpu>` reports "No devices were found". The failure then
surfaces at two stages in `nvv4l2decoder`:

| Stage | No explicit caps filter | With explicit caps filter |
|---|---|---|
| Device capability probe | `Unable to try format: Unknown error -1` for every pixelformat (34×) | skipped (caps pinned) |
| GStreamer negotiation | `Filter caps do not completely specify the output format` → dies at preroll | passes |
| First frame submitted | — | kernel: `NV_ERR_NO_MEMORY` (Thor) / 41× fresh `rpcRmApiControl_dce cmd:0x731341 [NV_ERR_GENERIC]` during the test (AGX) → `nvv4l2decoder: Failed to process frame.` |

The caps-filter experiment ("Google suggests adding
`video/x-h264,stream-format=byte-stream,width=...,height=...` before
`nvv4l2decoder`") was tested on both Thor and AGX (2026-10-19): it moves the
pipeline from the negotiation failure to the hardware-submission failure.
It is **not a fix** — it is, however, the right way to reproduce and observe
the underlying DCE errors.

### Evidence matrix (all captured 2026-10-16 → 2026-10-19)

| Machine | L4T | Boot DCE RPC failures | Decoder probe | Decoder frame submit |
|---|---|---|---|---|
| Orin Nano 8GB | r39.2.0 | `TempVersion r595_00`, NVRM banner in dmesg | 34× `Unknown error -1` (`GST_DEBUG=4` trace) | — |
| Orin Nano 8GB (rootless podman) | r39.2.0 | — | — | `NvRmMemInitNvmap failed: Permission denied` / `NvRmMemMgrInit failed` (separate container-access issue, see §2) |
| **Thor** | **r39.2.1** (GCID 46758480) | **88×** `rpcRmApiControl_dce` on GPU1 (DCE coproc; `nvidia-smi -i 1` → no device) | 34× `Unknown error -1` | `NV_ERR_NO_MEMORY` ×6 from `_memdescAllocInternal` → `Failed to process frame` |
| **AGX Orin 64GB** | **r39.2.1** (GCID 46758480, driver rebuilt Oct 16) | **61×** `rpcRmApiControl_dce` on GPU0 | `VIDIOC_CROPCAP: Unknown error -1` → negotiation dies | **41× fresh `cmd:0x731341 [NV_ERR_GENERIC]` fired during the decode test** → `Failed to process frame` |

Reproducer (host, any r39.2 machine):

```bash
# 30-frame 1280x720 H.264 byte stream (e.g. from local ffmpeg)
gst-launch-1.0 filesrc location=/tmp/test.h264 \
  ! "video/x-h264,width=1280,height=720,framerate=30/1,stream-format=byte-stream,alignment=au" \
  ! nvv4l2decoder ! fakesink
# → "Failed to process frame" + fresh DCE RPC failures in `sudo dmesg -w`
```

### Workaround in this project

**Stay on JetPack 6 / r36.5 for anything that decodes H.264 on GPU** — which
is the live deployment: the assistant runs in the `jp6` image
(`Containerfile.r36.5`) on both the AGX Orin and the Orin Nano, including
Wireless mode where the robot camera stream is GPU-decoded. The JP6 stack has
no DCE RPC failures and `nvv4l2decoder` probes and decodes normally.

If/when an r39.2 image must be used, replace the GPU decode path with CPU
decoding (e.g. `avdec_h264` from `gstreamer1-plugins-ugly`/ffmpeg) at the cost
of CPU load and loss of NVMM zero-copy. That has **not** been validated in
this project and is a separate decision.

Watch for the fix: NVIDIA's r39.2.x notes + re-run the reproducer above. The
tell of a fix is a non-`TempVersion` NVRM build, no boot `rpcRmApiControl_dce`
failures, and `gst_v4l2_object_get_nearest_size` returning real sizes.

Community reports of the same root cause (DCE RPC init failure on r39.2):

- Orin Nano / r39.2.0 — `nvarguscamerasrc` segfault, `NvRmGpuLibOpen error=4`,
  DCE RPC failures at boot (Argus-side view):
  https://forums.developer.nvidia.com/t/jetson-orin-nano-devkit-jetpack-7-2-r39-2-0-nvarguscamerasrc-fails-with-nvrmgpulibopen-error-4-dce-rpc-control-call-failures-at-boot/377392
- Same DCE RPC signature, kernel NULL-pointer oops in
  `tegra_camera_platform` on V4L2 STREAMON:
  https://forums.developer.nvidia.com/t/bug-report-tc358743-hdmi-to-csi2-v4l2-streamon-crashes-kernel-via-tegra-camera-platform-null-pointer-same-dce-rpc-failure-as-thread-377392/380587
- Related: r39.2.0 ISO GPU clock stuck at 624 MHz (BPMP):
  https://forums.developer.nvidia.com/t/jetpack-7-2-l4t-39-2-gpu-frequency-stuck-at-624-mhz-on-orin-nano-8gb-p3767-0005-bpmp-hard-limit-super-mode-not-unlocking/377003

## 2. Rootless podman cannot reach the GPU memory manager on L4T

`/dev/nvmap` and `/dev/nvhost-*` are `root:video 0640`. In a **rootless**
container (podman run as a regular user) those owners fall outside the
user-namespace UID/GID map, so the nodes remap to `nobody:nogroup` inside the
container and even `--privileged` / `--group-add video` /
`--security-opt label=disable` cannot restore access. The CUDA driver then
aborts with:

```
NvRmMemInitNvmap failed: error Permission denied
NvRmMemMgrInit failed: Memory Manager Not supported, line 340
Cuda failure: status=100
```

(`cuInit` → 999 / no devices for the app.)

**Fix:** run the container **rootful**, and pass `--device /dev/nvmap`
explicitly — the CDI `nvidia.com/gpu` spec on L4T lists the `nvhost-*` nodes
but not `/dev/nvmap`, so the Tegra memory manager stays unreachable without
the extra flag. Validated on the Orin Nano (rootful podman +
`--device /dev/nvmap` → `cuInit == 0`, 1 CUDA device, CUDAExecutionProvider
active — see `packaging/releases/native-jp6-cu126-py310-sm87-r1/BUILD.md`).

On the AGX Orin (SELinux enforcing host) add
`--security-opt label=disable` as well.
