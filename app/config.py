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

"""Configuration — loads settings.yaml into typed dataclasses."""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from pathlib import Path
import yaml


@dataclass
class LLMConfig:
    model: str = ""
    base_url: str = "http://localhost:8080"
    backend: str = "openai"
    max_tokens: int = 512
    temperature: float = 0.7
    timeout: float = 120.0
    system_prompt: str = "You are a helpful AI assistant."
    system_prompt_no_rag: str = "You are a helpful AI assistant. Answer from your own knowledge."


@dataclass
class STTConfig:
    model: str = "base.en"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str = "en"
    beam_size: int = 1


@dataclass
class TTSConfig:
    voice: str = "af_sarah"
    speed: float = 1.0
    lang: str = "en-us"
    first_chunk_words: int = 3
    max_chunk_words: int = 8


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 2
    input_device: Optional[str] = "Reachy Mini Audio"
    output_device: Optional[str] = None
    echo_cancellation: bool = True


@dataclass
class VADConfig:
    speech_threshold: float = 0.008
    silence_duration_ms: int = 500
    lookback_ms: int = 250
    max_speech_secs: int = 15
    chunk_ms: int = 30
    min_utterance_secs: float = 0.3
    min_utterance_rms: float = 0.001
    silero_threshold: float = 0.5


@dataclass
class VisionConfig:
    camera_device: int = 0
    width: int = 640
    height: int = 480
    jpeg_quality: int = 80
    frames: int = 3
    capture_fps: float = 10.0
    system_prompt: str = (
        "You are a vision assistant on an NVIDIA Jetson device with a live camera. "
        "Answer in one to two sentences. Be direct and concise."
    )
    few_shot: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class ReachyConfig:
    enabled: bool = True
    mode: str = "lite"                  # "lite" = USB on this machine, "wireless" = network
    robot_name: str = "reachy_mini"     # Wireless: robot identity label
    # Explicit daemon IP. SDK 1.8.0 dropped Zenoh discovery: connection_mode=
    # "network" connects to ws://<host>:8000 directly, so the robot's IP must
    # be set here (mDNS/zenoh cannot cross the .0.x -> .2.x subnet split).
    host: str = "192.168.2.113"
    connection_mode: str = "network"    # Wireless: SDK connection mode
    spawn_daemon: bool = True
    timeout: float = 30.0
    media_backend: str = "no_media"
    automatic_body_yaw: bool = False
    wake_on_start: bool = True
    sleep_on_exit: bool = False
    antenna_rest_position: List[float] = field(
        default_factory=lambda: [-0.1745, 0.1745]
    )
    daemon_retry_attempts: int = 3
    daemon_startup_wait: float = 15.0
    face_tracking: bool = True
    tracking_motion_enabled: bool = True
    tracking_fps: float = 15.0
    tracking_dead_zone: float = 0.12
    tracking_lock_zone: float = 0.18
    tracking_reacquire_zone: float = 0.45
    tracking_good_frame_zone: float = 0.18
    tracking_min_face_size: float = 0.06
    tracking_stable_frames: int = 2
    tracking_face_lost_delay: float = 3.0
    tracking_head_yaw_max_deg: float = 20.0
    tracking_head_yaw_gain: float = 18.0
    tracking_head_yaw_step: float = 1.4
    tracking_soft_center_head_yaw_max_deg: float = 12.0
    tracking_soft_center_head_yaw_step: float = 0.75
    tracking_pose_smoothing: float = 0.18
    tracking_pose_max_step_deg: float = 6.0
    tracking_body_max_deg: float = 30.0
    tracking_body_gain: float = 18.0
    tracking_body_step: float = 0.7
    tracking_invert_body: bool = True
    tracking_body_enabled: bool = True
    tracking_vertical: bool = True
    tracking_return_to_neutral: bool = False
    tracking_scan_enabled: bool = True
    tracking_scan_body_range_deg: float = 20.0
    tracking_scan_speed_deg_per_sec: float = 5.0
    tracking_capture_settle_secs: float = 0.35
    tracking_capture_acquire_timeout_secs: float = 0.4
    speaking_movements_enabled: bool = True
    speaking_movement_excitement_probability: float = 0.4


@dataclass
class RAGConfig:
    enabled: bool = True
    knowledge_dir: str = "./knowledge_base"
    persist_dir: str = "./data/chromadb"
    embedding_backend: str = "llamacpp"
    embedding_model: str = "bge-small-en-v1.5"
    embedding_base_url: str = "http://localhost:8081"
    n_results: int = 3
    min_relevance: float = 0.5
    chunk_size: int = 200
    chunk_overlap: int = 20


@dataclass
class WebConfig:
    ui_fps: float = 10.0
    host: str = "0.0.0.0"
    port: int = 8090


# ── Jetson device classes ─────────────────────────────────────────
# Per-device model + runtime recommendations for the VLM/STT pair.
#
# The VLM is NOT served from config: the actual GGUF is decided by what
# you launch separately in terminal 1 via run_llama_cpp.sh (or by
# DEVICE_CLASS=... run_llama_cpp.sh). Each profile below names the
# recommended VLM spec for its Jetson class, the llama.cpp image tag
# (Orin vs Thor), and the CTX/BATCH/UBATCH/CACHE_RAM bounds that fit
# the device's unified memory.
#
# `model` is the served model id on the llama.cpp OpenAI API — i.e. the
# local filename minus .gguf (matches hf_expected_filename()). The
# assistant pins it on load so a wrong VLM in terminal 1 fails fast
# instead of being silently auto-picked (see app/llm.py LLM.load()).

@dataclass
class DeviceProfile:
    key: str
    device: str
    memory_note: str
    vlm_spec: str
    model: str
    stt_model: str
    image: str
    ctx: int
    batch: int
    ubatch: int
    cache_ram: int


DEVICE_CLASSES: Dict[str, DeviceProfile] = {
    "orin-nano": DeviceProfile(
        key="orin-nano",
        device="Orin Nano 8GB",
        memory_note="~6 GB GPU budget after OS + camera + web + tracking",
        vlm_spec="unsloth/gemma-4-E2B-it-GGUF:Q4_K_M",
        model="gemma-4-E2B-it-Q4_K_M",
        stt_model="small.en",
        image="ghcr.io/nvidia-ai-iot/llama_cpp:latest-jetson-orin",
        ctx=1024,
        batch=128,
        ubatch=128,
        cache_ram=0,
    ),
    "orin-nx": DeviceProfile(
        key="orin-nx",
        device="Orin NX 16GB",
        memory_note="~13 GB GPU budget; doubles the Orin Nano headroom",
        vlm_spec="unsloth/gemma-4-E4B-it-GGUF:Q4_K_M",
        model="gemma-4-E4B-it-Q4_K_M",
        stt_model="small.en",
        image="ghcr.io/nvidia-ai-iot/llama_cpp:latest-jetson-orin",
        ctx=4096,
        batch=256,
        ubatch=256,
        cache_ram=256,
    ),
    "agx-orin": DeviceProfile(
        key="agx-orin",
        device="AGX Orin 64GB",
        memory_note="~60 GB GPU budget; MoE fits comfortably",
        vlm_spec="unsloth/gemma-4-26B-A4B-it-GGUF:UD-Q4_K_M",
        model="gemma-4-26B-A4B-it-UD-Q4_K_M",
        stt_model="medium.en",
        image="ghcr.io/nvidia-ai-iot/llama_cpp:latest-jetson-orin",
        ctx=8192,
        batch=256,
        ubatch=256,
        cache_ram=256,
    ),
    "thor": DeviceProfile(
        key="thor",
        device="Thor 128GB",
        memory_note="~100 GB+ GPU budget; needs the Blackwell image",
        vlm_spec="unsloth/gemma-4-31B-it-GGUF:Q4_K_M",
        model="gemma-4-31B-it-Q4_K_M",
        stt_model="medium.en",
        image="ghcr.io/nvidia-ai-iot/llama_cpp:latest-jetson-thor",
        ctx=16384,
        batch=256,
        ubatch=256,
        cache_ram=512,
    ),
}

DEFAULT_DEVICE_CLASS = "orin-nano"


def resolve_device_class(key: Optional[str]) -> DeviceProfile:
    """Return the DeviceProfile for a known key, or the default.

    An unknown non-None key is a caller error (typo) — fail loudly
    rather than silently picking the default.
    """
    if key is None:
        return DEVICE_CLASSES[DEFAULT_DEVICE_CLASS]
    k = key.strip().lower()
    if k not in DEVICE_CLASSES:
        known = ", ".join(sorted(DEVICE_CLASSES))
        raise SystemExit(
            f"Unknown --device-class '{key}'. Known classes: {known}"
        )
    return DEVICE_CLASSES[k]


_SECTIONS = [
    ("llm", "llm", LLMConfig),
    ("stt", "stt", STTConfig),
    ("tts", "tts", TTSConfig),
    ("audio", "audio", AudioConfig),
    ("vad", "vad", VADConfig),
    ("vision", "vision", VisionConfig),
    ("reachy", "reachy", ReachyConfig),
    ("rag", "rag", RAGConfig),
    ("web", "web", WebConfig),
]


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    reachy: ReachyConfig = field(default_factory=ReachyConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    web: WebConfig = field(default_factory=WebConfig)

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "Config":
        default_path = Path(__file__).parent.parent / "config" / "settings.yaml"
        profile_path = config_path or os.environ.get("REACHY_ASSISTANT_CONFIG")
        config_paths = [default_path]
        if profile_path and Path(profile_path).resolve() != default_path.resolve():
            config_paths.append(Path(profile_path))

        config = cls()
        try:
            for path in config_paths:
                if not path.exists():
                    continue
                with path.open() as f:
                    data = yaml.safe_load(f) or {}
                for yaml_key, attr_name, _ in _SECTIONS:
                    section_obj = getattr(config, attr_name)
                    for k, v in data.get(yaml_key, {}).items():
                        if hasattr(section_obj, k):
                            setattr(section_obj, k, v)
        except Exception as e:
            print(f"Error loading config: {e}")
        return config
