# Reachy Mini Wireless Setup (no cable)

Run the Jetson Assistant so it drives a **wireless Reachy Mini** over the
network — camera, microphone, and speaker all stream over WebRTC. Nothing
plugs into the Jetson.

This is the validated path we ran on an **Orin Nano Super (L4T r36.5)** with
a wireless Reachy Mini: the assistant heard the robot's mic, and its TTS came
out the robot's speaker, at ~28 fps video.

## 1. What each machine does

Two independent devices talk to each other over IP:

| Machine | Runs | Owns |
|---|---|---|
| **Jetson** (Orin Nano / Thor) | the assistant: VLM, STT (whisper/CUDA), TTS (Kokoro), VAD, face tracking, movement | the GPU inference; **no local mic/speaker** |
| **Reachy Mini** (Raspberry Pi) | the robot daemon (`reachy-mini-daemon`) | the robot's camera, microphone, speaker, motors |

The Jetson's SDK connects to the daemon by **explicit IP** and negotiates
WebRTC. The `rswebrtc` GStreamer plugin (`webrtcsrc` in / `webrtcsink` out) is
built into the container images — it carries the robot's mic and camera to the
Jetson and the TTS audio back to the robot's speaker.

> **SDK version note.** The last Python-3.10-compatible SDK is **1.8.0**, which
> **dropped Zenoh discovery** — you must give the daemon's IP explicitly
> (`reachy.host`). The robot itself ships a newer SDK (≥ 1.9); control + WebRTC
> media are verified to work cross-version (Jetson 1.8.0 ↔ robot 1.11.0).

## 2. Bring up the Reachy Mini (robot)

1. Power the wireless Reachy Mini on. On SDK ≥ 1.9 the daemon starts
   automatically; on an older SDK start it in a terminal on the robot with
   `reachy-mini-daemon`.
2. Verify it's serving: open `http://<robot-ip>:8000/docs` — you should see the
   Reachy SDK FastAPI docs.
3. Note the robot's IP (e.g. `192.168.2.113`). The robot and Jetson must be able
   to route to each other.

> **Network split gotcha.** If the robot and Jetson sit on different subnets
> that rely on mDNS/zeroconf (e.g. a `.0.x` ↔ `.2.x` split), name-based
> discovery won't cross it. That's expected — we connect by explicit IP, which
> is exactly why `reachy.host` exists. Confirm IP routing with a
> `ping <robot-ip>` from the Jetson.

## 3. Build the assistant image on the Jetson

On an aarch64 Jetson with podman or docker. Two Containerfiles ship in
`container/`:

| JetPack | Base | Suggested tag | Build file |
|---|---|---|---|
| 6 (L4T r36.5, Py 3.10) | `nvcr.io/nvidia/l4t-jetpack:r36.4.0` + r36.5 apt | `localhost/reachy-mini-jetson-assistant:jp6-r365` | `container/Containerfile.r36.5` |
| 7.2 (L4T r39.2, Py 3.12) | `whitesscott/l4t-jetpack:r39.2.1` | `localhost/reachy-mini-jetson-assistant:jp72-r392` | `container/Containerfile.r39.2` |

```sh
# from the repo root, on the Jetson
podman build -f container/Containerfile.r36.5 \
  -t localhost/reachy-mini-jetson-assistant:jp6-r365 .
```

> **App-only changes are seconds, not minutes.** Once the full image exists,
> re-syncing just the assistant source (a fix in `app/`, a config tweak) uses
> `container/Containerfile.appsync.r36.5` — it `FROM`s the full image and
> `COPY`s the repo on top, skipping the ~5-min Rust rswebrtc build. Build it
> with the next tag (`-t …:jp6-r365-b`) after the full one (`…:jp6-r365-a`).

The build compiles the `rswebrtc` plugin against the base's **system**
GStreamer (no source-built GStreamer) and, for r36.5, installs the CUDA wheels
staged in `wheels/` (required — see `container/README.md` for the honest JP6
wheel provenance). r39.2 pulls the published GPU wheels from the manifest
instead.

Smoke test that the WebRTC elements exist in the built image:

```sh
podman run --rm localhost/reachy-mini-jetson-assistant:jp6-r365 \
  python -c "import gi; gi.require_version('Gst','1.0'); \
from gi.repository import Gst; Gst.init(None); \
assert Gst.ElementFactory.make('webrtcsrc'); assert Gst.ElementFactory.make('webrtcsink'); \
print('RSWERTC_OK')"
```

## 4. Configure the daemon IP

The image's default config is `config/settings.wireless.yaml`. The only thing
you must change is the robot's IP:

```yaml
reachy:
  enabled: true
  mode: wireless
  host: 192.168.2.113        # <-- your robot's IP (edit before building)
  connection_mode: network
  spawn_daemon: false
  media_backend: webrtc
```

Everything else (STT on CUDA, face tracking, movement) is already set for
wireless. Either edit the file in the repo before building, or mount an edited
copy into the container:

```sh
podman run -d --net=host \
  -v "$PWD/config/settings.wireless.yaml:/opt/assistant/config/settings.wireless.yaml" \
  --name reachy-assistant localhost/reachy-mini-jetson-assistant:jp6-r365
```

## 5. Start the assistant

**`--net=host` is required.** WebRTC ICE must advertise the Jetson's real
routable IP; a bridge network would advertise the container's private address
and the robot couldn't reach it for media.

The image's `CMD` is `python run_web_vision_chat.py`, so start it (with the
llama.cpp VLM server already running — see README → Quick Start, Terminal 1 —
or the assistant has nothing to answer with):

```sh
podman run -d --net=host --name reachy-assistant localhost/reachy-mini-jetson-assistant:jp6-r365
# SELinux-enforcing hosts (e.g. RHEL-based L4T / bootc) also need:
#   --security-opt label=disable
```

Watch startup:

```sh
podman logs -f reachy-assistant
```

You should see the SDK connect to the daemon at `host`, then the wireless
camera come up and the WebRTC media establish. Open
`http://<jetson-ip>:8090` in a browser, talk to the robot — it hears you
through the robot's mic (WebRTC) and answers through the robot's speaker.

## 6. What's already handled (no action needed)

- **TTS → robot speaker.** SDK 1.8.0's audio-send chain is one-shot and races
  the `webrtcbin` OPUS sink-pad creation, so the first attempt fails and
  audio-out stays dead for the session. The r36.5 image bakes a bounded-retry
  fix into the installed SDK (`container/patches/audio_send_retry.py`, applied
  in Containerfile step 5b). Verified wire-level: tone + real TTS chunks reach
  the robot's speaker. If you upgrade the SDK past 1.8.0, the patch script exits
  non-zero on a moved anchor — re-derive it.
- **Robot mic → user input.** `RemoteMicRecorder` (`app/remote_media.py`)
  converts the float32 WebRTC audio to int16 with the correct ×32768 scaling,
  feeds Silero VAD, then whisper STT. It's part of every entry point. Ambient
  should read a small non-zero RMS; speech spikes it.

## 7. Verify it's actually listening

A minimal probe that drives the exact production receive path and reports what
the assistant hears from the robot mic:

```python
import sys, time
import numpy as np
sys.path.insert(0, "/opt/assistant")
from reachy_mini import ReachyMini
from rich.console import Console
from app.remote_media import RemoteMicRecorder
from app.pipeline import chunk_rms

r = ReachyMini(host="192.168.2.113", connection_mode="network",
                media_backend="webrtc", log_level="INFO")
mic = RemoteMicRecorder(Console(), chunk_ms=32)
mic.attach(r.media, None)
print("mic.start ->", mic.start("hw:dummy", "Reachy Mini Audio"), flush=True)
n = 0
for _ in range(10):
    raw = mic.audio_q.get(timeout=1.0)
    n += 1
    print(f"chunk {n}: rms={chunk_rms(raw):.5f}", flush=True)
mic.stop(); r.media.close()
```

A healthy robot mic prints `~32 chunks/s` with a **fluctuating** RMS (varies
with the room). The old bug printed a constant near-zero RMS — that's how you
tell dead from alive.

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| ICE / media never establishes | not `--net=host`, or IP can't route | use `--net=host`; `ping <robot-ip>` from the Jetson |
| `connecting to daemon at <host>…` then fails | daemon down, wrong IP, or SDK auto-spawn | confirm robot power + daemon up; check `host`; keep `spawn_daemon: false` |
| Camera works but TTS never reaches the speaker | SDK audio-send one-shot | confirm the image has the retry patch (step 5b); you're on SDK ≤ 1.8.0 |
| Hears nothing / STT empty | mic scaling or no speech | check ambient RMS is non-zero (§7); speak clearly near the robot |
| Used `setup_jetson.sh` on JP6 | that's the bare-metal wired path | wireless = container; build `container/Containerfile.r36.5` |

## Related

- Container build internals + validation evidence: [`container/README.md`](../container/README.md)
- JetPack 7.2 (wired / Lite) setup: [`JETPACK_7_2_SETUP.md`](JETPACK_7_2_SETUP.md)
- Legacy JetPack 6 wired setup: [`SETUP.md`](../SETUP.md)
