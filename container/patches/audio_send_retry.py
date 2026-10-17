#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Idempotent patch for reachy-mini 1.8.0: retry WebRTC audio-send setup.

The one-shot ``_setup_audio_send_chain()`` fires from the audio src-pad
``pad-added`` callback, which precedes the moment the C webrtcbin's local
description creates the OPUS send sink pad (~1 s after PLAYING). The first
attempt therefore fails with 'No OPUS sink pad found' and audio send
(TTS -> robot speaker) stays disabled for the whole session.

This patch adds a bounded retry (40 x 250 ms via GLib timeout) to the
installed SDK file. Containerfile usage:

    python /build/patches/audio_send_retry.py

Prints SDK_AUDIO_RETRY_OK on success; exits non-zero if an anchor is
missing (the SDK file has changed and the patch must be re-derived).
"""
import os
import sys


def main() -> None:
    env_override = os.environ.get("SDK_FILE")
    if env_override:
        path = env_override
    else:
        candidates = [
            "/opt/venv/lib/python3.10/site-packages/reachy_mini/media/webrtc_client_gstreamer.py",
        ]
        path = None
        for cand in candidates:
            if os.path.exists(cand):
                path = cand
                break
        if path is None:
            import reachy_mini.media.webrtc_client_gstreamer as mod

            path = mod.__file__
    print("target:", path, file=sys.stderr)

    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if "_retry_audio_send_setup" in src:
        print("already patched; nothing to do")
        print("SDK_AUDIO_RETRY_OK")
        return

    # 1. init the retry counter next to _audio_send_ready
    old = (
        "        self._webrtcbin = None\n"
        "        self._audio_send_ready = False\n"
        "        self._appsrc = None\n"
    )
    new = (
        "        self._webrtcbin = None\n"
        "        self._audio_send_ready = False\n"
        "        self._audio_send_retry = 0\n"
        "        self._appsrc = None\n"
    )
    assert src.count(old) == 1, "anchor 1 (init block) not unique/found"
    src = src.replace(old, new)

    # 2. failure branch: bounded retry instead of giving up for the session
    old = (
        "        if sink_pad is None:\n"
        "            self.logger.error(\n"
        "                \"No OPUS sink pad found on webrtcbin, audio send disabled\"\n"
        "            )\n"
        "            self._audio_send_ready = False\n"
        "            return\n"
    )
    new = (
        "        if sink_pad is None:\n"
        "            # The OPUS send sink pad only exists after webrtcbin's\n"
        "            # local description is set (~1 s after PLAYING), which\n"
        "            # is later than the src-pad callback that triggers this\n"
        "            # first attempt. Retry with a bounded budget instead of\n"
        "            # disabling audio send for the whole session.\n"
        "            self._audio_send_ready = False\n"
        "            self._audio_send_retry += 1\n"
        "            if self._audio_send_retry > 40:\n"
        "                self.logger.error(\n"
        "                    \"No OPUS sink pad found on webrtcbin after %d \"\n"
        "                    \"retries, audio send disabled\",\n"
        "                    self._audio_send_retry,\n"
        "                )\n"
        "                self._audio_send_retry = 0\n"
        "                return\n"
        "            self.logger.info(\n"
        "                \"OPUS sink pad not ready yet (retry %d/40); \"\n"
        "                \"rechecking in 250 ms\",\n"
        "                self._audio_send_retry,\n"
        "            )\n"
        "            GLib.timeout_add(250, self._retry_audio_send_setup)\n"
        "            return\n"
    )
    assert src.count(old) == 1, "anchor 2 (failure branch) not unique/found"
    src = src.replace(old, new)

    # 3. reset the counter on success
    old = (
        "        self._appsrc = appsrc\n"
        "        self.logger.info(\"Audio send chain ready (bidirectional audio enabled)\")\n"
    )
    new = (
        "        self._appsrc = appsrc\n"
        "        self._audio_send_retry = 0\n"
        "        self.logger.info(\"Audio send chain ready (bidirectional audio enabled)\")\n"
    )
    assert src.count(old) == 1, "anchor 3 (success log) not unique/found"
    src = src.replace(old, new)

    # 4. add the retry callback before start_playing
    old = (
        "    def start_playing(self) -> None:\n"
        "        \"\"\"No-op \u2014 audio send chain is set up automatically on WebRTC connection.\"\"\"\n"
        "        pass\n"
    )
    new = (
        "    def _retry_audio_send_setup(self) -> bool:\n"
        "        \"\"\"Retry the audio-send chain setup until the OPUS sink\n"
        "        pad exists (or the bounded budget is exhausted).\n"
        "\n"
        "        GLib timeout callback; returns True to keep retrying.\n"
        "        \"\"\"\n"
        "        try:\n"
        "            if self._pipeline_record.get_state(0)[1] == Gst.State.NULL:\n"
        "                return False\n"
        "            if self._audio_send_ready:\n"
        "                return False\n"
        "            self._setup_audio_send_chain()\n"
        "            return not self._audio_send_ready\n"
        "        except Exception:\n"
        "            self.logger.exception(\"audio send retry failed\")\n"
        "            return False\n"
        "\n"
        "    def start_playing(self) -> None:\n"
        "        \"\"\"No-op \u2014 audio send chain is set up automatically on WebRTC connection.\"\"\"\n"
        "        pass\n"
    )
    assert src.count(old) == 1, "anchor 4 (start_playing) not unique/found"
    src = src.replace(old, new)

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print("patched OK")

    import py_compile

    py_compile.compile(path, doraise=True)
    print("SDK_AUDIO_RETRY_OK")


if __name__ == "__main__":
    main()
