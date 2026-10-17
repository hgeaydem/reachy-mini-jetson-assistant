# SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Remote (network) camera + audio for the Reachy Mini Wireless.

On the Lite the daemon and media hardware sit on the Jetson, so the app drives
a local V4L2 camera (``app.camera.Camera``) and a local ALSA/PulseAudio mic and
speaker (``app.pipeline.MicRecorder``). On the Wireless the daemon runs on the
robot's own Raspberry Pi and there is no USB link, so every byte of camera and
audio crosses the network through the SDK's WebRTC media channel
(``ReachyMini.media``).

This module provides drop-in replacements that expose the *same* surface the
rest of the codebase already consumes, so the VAD loop, the VLM capture
helper, the face tracker and the entry points work unchanged:

  * ``RemoteCamera``   — replaces ``app.camera.Camera``
  * ``RemoteMicRecorder`` — replaces ``app.pipeline.MicRecorder``
  * ``RemoteSpeaker`` — the TTS sink object passed where a PulseAudio sink id
    used to be (understood by ``app.pipeline.play_audio``).

Contract notes (verified against the SDK source):
  * ``media.get_frame()``           -> BGR ``uint8`` ``(H, W, 3)`` or ``None``
  * ``media.get_audio_sample()``    -> float32 ``(N, 2)`` stereo @ 16 kHz
  * ``media.push_audio_sample(x)``  -> float32 ``(N,)`` mono or ``(N, ch)``;
    the SDK resamples/converts to the output stream on the daemon side.
  * ``media.start_recording()`` / ``start_playing()`` / ``stop_playing()``
    manage the bidirectional WebRTC audio chain; ``clear_player()`` (barge-in)
    lives on the underlying audio object (``media.audio``), not on the
    ``MediaManager`` facade.
"""

import base64
import queue
import threading
import time
from collections import deque
from typing import Optional

import numpy as np

from app.camera import MAX_SPEECH_SECS, PRE_SPEECH_SECS
from app.pipeline import CHANNELS, SAMPLE_RATE


# ── small helpers ─────────────────────────────────────────────────

def _resample_linear(x: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Linear-resample a 1-D float signal between two sample rates.

    NumPy-only (no scipy dependency). For the common 24 kHz -> 16 kHz TTS
    path this is a simple fractional-step interpolation.
    """
    if from_rate == to_rate or x.size == 0:
        return x
    n_out = int(round(x.size * to_rate / from_rate))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    positions = np.linspace(0.0, x.size - 1.0, n_out, dtype=np.float64)
    idx = positions.astype(np.int64)
    idx[np.clip(idx, 0, x.size - 1, out=idx)] = idx
    frac = (positions - idx).astype(np.float64)
    nxt = np.minimum(idx + 1, x.size - 1)
    out = x.astype(np.float64)[idx] + (x.astype(np.float64)[nxt] - x.astype(np.float64)[idx]) * frac
    return out.astype(np.float32)


def _stereo_to_mono(samples: np.ndarray) -> np.ndarray:
    """float32 ``(N, 2)`` stereo -> 1-D mono by averaging both channels."""
    if samples.ndim == 2:
        return samples.mean(axis=1)
    return samples


# ── Remote camera ─────────────────────────────────────────────────

class RemoteCamera:
    """Camera backed by the robot's WebRTC video stream.

    Mirrors the public surface of ``app.camera.Camera`` so the three entry
    points and the face tracker keep working: ``start``/``close``/``read_live``/
    ``read_raw_live``/``capture_single``/``get_speech_frames``/``latest_raw``/
    ``health_check`` plus the ``width``/``height``/``buffer_count``/``actual_fps``
    attributes.
    """

    def __init__(
        self,
        media,
        width: int = 640,
        height: int = 480,
        jpeg_quality: int = 80,
        capture_fps: float = 3.0,
        device: int = 0,
    ):
        # ``device`` is accepted for constructor parity with ``Camera`` but is
        # ignored over the network.
        self.media = media
        self.device = device
        self.width = width
        self.height = height
        self.jpeg_quality = jpeg_quality
        self.capture_fps = capture_fps
        self.camera_specs = None
        self.camera_K: Optional[np.ndarray] = None
        self.camera_D: Optional[np.ndarray] = None
        # Same timestamped JPEG ring as the local camera, so VLM lookback works.
        self._ring: deque[tuple[float, bytes]] = deque(
            maxlen=max(1, int(capture_fps * (MAX_SPEECH_SECS + PRE_SPEECH_SECS)))
        )
        self._latest: Optional[tuple[float, np.ndarray]] = None
        self._latest_jpeg: Optional[bytes] = None
        self._lock = threading.Lock()
        self._alive = False
        self._ready = False
        self._thread: Optional[threading.Thread] = None
        self._actual_fps: float = 0.0

    # -- capture ---------------------------------------------------------
    def open(self) -> bool:
        """Confirm the remote video stream is delivering frames."""
        if self.media is None:
            return False
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            frame = self._grab()
            if frame is not None:
                self._sync_geometry(frame)
                self._ready = True
                return True
            time.sleep(0.2)
        return False

    def _grab(self) -> Optional[np.ndarray]:
        try:
            return self.media.get_frame()
        except Exception:
            return None

    def _sync_geometry(self, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        if w > 0:
            self.width = w
        if h > 0:
            self.height = h

    def start(self) -> bool:
        if not self.open():
            return False
        self._alive = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        return True

    def _capture_loop(self):
        import cv2

        interval = 1.0 / self.capture_fps
        t_last = 0.0
        n_frames = 0
        t_start = time.monotonic()
        while self._alive:
            now = time.monotonic()
            sleep_for = interval - (now - t_last)
            if sleep_for > 0:
                time.sleep(sleep_for)
            t_last = time.monotonic()

            frame = self._grab()
            if frame is None:
                continue
            t_frame = time.monotonic()
            ok, jpg = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
            )
            jpg_bytes = jpg.tobytes() if ok else None
            with self._lock:
                self._latest = (t_frame, frame)
                if jpg_bytes is not None:
                    self._latest_jpeg = jpg_bytes
                    self._ring.append((t_frame, jpg_bytes))
            n_frames += 1
            elapsed = time.monotonic() - t_start
            if elapsed > 0:
                self._actual_fps = n_frames / elapsed

    # -- public read surface (same as Camera) ----------------------------
    @staticmethod
    def _base64_jpeg(jpg: bytes) -> str:
        return base64.b64encode(jpg).decode("ascii")

    def get_speech_frames(
        self, speech_start: float, speech_end: float, max_frames: int = 3
    ) -> list[str]:
        if max_frames > 1:
            window_start = speech_start - PRE_SPEECH_SECS
        else:
            window_start = speech_start
        with self._lock:
            candidates = [
                (t, jpg) for t, jpg in self._ring if window_start <= t <= speech_end
            ]
        if not candidates:
            with self._lock:
                if self._ring:
                    candidates = [(self._ring[-1][0], self._ring[-1][1])]
                elif self._latest_jpeg is not None:
                    candidates = [(speech_end, self._latest_jpeg)]
                else:
                    return []
        if max_frames == 1:
            selected = [candidates[-1][1]]
        elif len(candidates) <= max_frames:
            selected = [jpg for _, jpg in candidates]
        else:
            step = len(candidates) / max_frames
            selected = [candidates[int(i * step)][1] for i in range(max_frames)]
        return [self._base64_jpeg(jpg) for jpg in selected]

    def capture_single(self) -> Optional[str]:
        with self._lock:
            jpg = self._latest_jpeg
        return self._base64_jpeg(jpg) if jpg is not None else None

    def latest_raw(self, *, copy: bool = True) -> Optional[np.ndarray]:
        with self._lock:
            if self._latest is None:
                return None
            _, frame = self._latest
            return frame.copy() if copy else frame

    def read_live(self) -> Optional[str]:
        with self._lock:
            jpg = self._latest_jpeg
        return self._base64_jpeg(jpg) if jpg is not None else None

    def read_raw_live(self) -> Optional[np.ndarray]:
        return self.latest_raw()

    @property
    def buffer_count(self) -> int:
        with self._lock:
            return len(self._ring)

    @property
    def actual_fps(self) -> float:
        return self._actual_fps

    def health_check(self) -> bool:
        return self._alive and self._ready

    def close(self):
        self._alive = False
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self._ready = False
        with self._lock:
            self._ring.clear()
            self._latest = None
            self._latest_jpeg = None


# ── Remote speaker (TTS sink) ─────────────────────────────────────

class RemoteSpeaker:
    """Plays TTS to the robot's speaker over the WebRTC audio send chain.

    ``app.pipeline.play_audio`` detects a non-string ``sink`` and calls
    ``sink.play(audio, sample_rate)``. Audio is resampled to the robot's 16 kHz
    input rate, converted to mono float32 and pushed to the daemon; the call
    blocks for the real-time duration so the VAD can pause correctly.
    """

    def __init__(self, media, volume: int = 100):
        self.media = media
        self.volume = volume
        self._lock = threading.Lock()
        self._playing = False

    def _ensure_playing(self) -> None:
        if self._playing:
            return
        try:
            self.media.start_playing()
        except Exception:
            pass
        self._playing = True

    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        """Play int16 mono audio and block until its duration elapses."""
        if audio is None or audio.size == 0 or self.media is None:
            return
        mono = audio.reshape(-1).astype(np.float32) / 32768.0
        mono = _resample_linear(mono, sample_rate, SAMPLE_RATE)
        if self.volume != 100:
            mono = mono * (self.volume / 100.0)
        self._ensure_playing()
        expected = mono.size / SAMPLE_RATE if SAMPLE_RATE else 0.0
        started = time.monotonic()
        try:
            # Push in small slices so the daemon's send queue stays live and we
            # can interleave with barge-in ``clear()``.
            slice_n = max(1, int(SAMPLE_RATE * 0.05))  # 50 ms slices
            for i in range(0, mono.size, slice_n):
                if not self._playing:
                    break
                self.media.push_audio_sample(mono[i : i + slice_n])
        except Exception:
            pass
        remaining = expected - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)

    def clear(self) -> None:
        """Drop queued remote playback (barge-in)."""
        with self._lock:
            self._playing = False
        if self.media is None:
            return
        # ``clear_player()`` lives on the underlying audio object
        # (GstWebRTCClient / GStreamerAudio), not on the MediaManager facade.
        audio = getattr(self.media, "audio", None)
        if audio is not None and hasattr(audio, "clear_player"):
            try:
                audio.clear_player()
            except Exception:
                pass
        try:
            self.media.stop_playing()
        except Exception:
            pass

    def stop(self) -> None:
        self.clear()

    # speaker-selection surface (single fixed destination over the network)
    def state(self) -> dict:
        return {
            "speakers": [{"id": "reachy", "label": "Reachy Mini (wireless)"}],
            "selected": "reachy",
            "volume": self.volume,
            "aec_enabled": True,
        }


# ── Remote microphone ─────────────────────────────────────────────

class RemoteMicRecorder:
    """Drop-in replacement for ``app.pipeline.MicRecorder``.

    A background thread pulls the robot's stereo WebRTC audio, converts it to
    mono int16 at ``SAMPLE_RATE`` and enqueues fixed-size chunks (``chunk_ms``)
    into ``audio_q`` — exactly the shape ``vad_loop`` / Silero expect. Speaker
    selection is a no-op that routes TTS to the robot (``pa_sink`` is the
    ``RemoteSpeaker`` object, understood by ``play_audio``).
    """

    def __init__(self, console, chunk_ms: int = 32):
        self.console = console
        self.chunk_ms = chunk_ms
        self.chunk_samples = max(1, int(SAMPLE_RATE * chunk_ms / 1000))
        self.chunk_bytes = self.chunk_samples * CHANNELS * 2
        self.audio_q: queue.Queue[bytes] = queue.Queue()
        self.listening = threading.Event()
        self.listening.set()
        self.alive = True
        self.pa_source: Optional[str] = None
        self.pa_sink: Optional[object] = None  # RemoteSpeaker (non-str)
        self.speaker_selector = None
        self.aec = None
        self._media = None
        self._thread: Optional[threading.Thread] = None
        self._carry = np.zeros(0, dtype=np.float32)
        self._route_lock = threading.RLock()

    def attach(self, media, speaker: RemoteSpeaker) -> None:
        self._media = media
        self.pa_sink = speaker

    def start(
        self,
        hw: str,
        mic_hint: str,
        speaker_hint: Optional[str] = None,
        echo_cancellation: bool = True,
    ) -> bool:
        # ``hw`` / ``mic_hint`` / ``speaker_hint`` are the local-USB discovery
        # values; over the network they are accepted for signature parity and
        # ignored.
        if self._media is None:
            self.console.print("[red]  ✗ No remote media channel (Wireless not connected)[/red]")
            return False
        try:
            self._media.start_recording()
        except Exception:
            pass
        self.alive = True
        self.listening.set()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        # Verify that audio actually flows before declaring success.
        deadline = time.monotonic() + 8.0
        got = False
        while time.monotonic() < deadline and not got:
            try:
                sample = self._media.get_audio_sample()
                got = sample is not None and sample.size > 0
            except Exception:
                got = False
            if not got:
                time.sleep(0.1)
        if got:
            self.console.print("  ✓ Remote mic live (Wireless WebRTC audio)")
            return True
        self.console.print("[red]  ✗ No audio from robot microphone[/red]")
        self._stop()
        return False

    def _reader(self):
        while self.alive:
            if not self.listening.is_set():
                time.sleep(0.02)
                continue
            try:
                sample = self._media.get_audio_sample()
            except Exception:
                sample = None
            if sample is None or sample.size == 0:
                time.sleep(0.005)
                continue
            mono = _stereo_to_mono(sample)
            # Concatenate with leftover samples, then emit full fixed chunks.
            buf = np.concatenate((self._carry, mono)) if self._carry.size else mono
            n_full = buf.size // self.chunk_samples
            emit = buf[: n_full * self.chunk_samples].reshape(-1, self.chunk_samples)
            self._carry = buf[n_full * self.chunk_samples :]
            for chunk in emit:
                pcm = np.clip(chunk * 32768.0, -32768.0, 32767.0).astype(np.int16)
                if self.listening.is_set():
                    self.audio_q.put(pcm.tobytes())

    def _stop(self):
        self.alive = False
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        try:
            self._media.stop_recording()
        except Exception:
            pass

    def stop(self):
        self._stop()

    def flush(self):
        while not self.audio_q.empty():
            try:
                self.audio_q.get_nowait()
            except queue.Empty:
                break

    def pause(self):
        """Stop queuing audio and drain the buffer."""
        self.listening.clear()
        self.flush()

    def resume(self):
        """Drain any stale audio and resume queuing."""
        self.flush()
        self.listening.set()

    # -- speaker routing (fixed to the robot over the network) ----------
    def get_pa_sink(self):
        with self._route_lock:
            return self.pa_sink

    def speaker_state(self) -> dict:
        if self.pa_sink is None:
            return {"speakers": [], "selected": None, "aec_enabled": True}
        return self.pa_sink.state()

    def select_speaker(self, sink_id: str) -> dict:
        state = self.speaker_state()
        state["error"] = "Speaker is fixed to the robot over the network"
        return state

    def set_speaker_volume(self, volume: int) -> dict:
        volume = max(0, min(100, int(volume)))
        if self.pa_sink is not None:
            self.pa_sink.volume = volume
        return self.speaker_state()
