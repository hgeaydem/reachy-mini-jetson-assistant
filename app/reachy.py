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

"""Reachy Mini connection helpers.

Shared across run_vision_chat.py, run_web_vision_chat.py, and any
future entry point that needs robot control.

Two modes (config.reachy.mode):
  * "lite"     — daemon on this machine (USB), spawnable locally.
  * "wireless" — daemon on the robot's own Raspberry Pi, reached over the
                 network; camera + audio stream through the SDK's WebRTC
                 media channel (``reachy.media``).
"""

import os
import signal
import subprocess
import time
from typing import Optional

from rich.console import Console

try:
    from reachy_mini import ReachyMini
    import psutil
    HAS_REACHY = True
except ImportError:
    HAS_REACHY = False
    ReachyMini = None  # type: ignore[assignment,misc]
    psutil = None


def is_wireless(config) -> bool:
    """True when the config targets a Reachy Mini Wireless over the network."""
    return getattr(config.reachy, "mode", "lite") == "wireless"


def is_daemon_running() -> bool:
    """Check if a reachy-mini-daemon process exists."""
    if not psutil:
        return False
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            for part in cmdline:
                if "reachy-mini-daemon" in part:
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
            continue
    return False


def kill_daemon(console: Console) -> bool:
    """Kill a stale reachy-mini-daemon process. Returns True if one was found."""
    if not psutil:
        return False
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            for part in cmdline:
                if "reachy-mini-daemon" in part:
                    pid = proc.pid
                    console.print(f"  [yellow]Killing stale Reachy daemon (PID {pid})[/yellow]")
                    os.kill(pid, signal.SIGKILL)
                    time.sleep(2)
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
            continue
    return False


def kill_stale_camera_holders(device: int, console: Console) -> None:
    """Kill any process holding /dev/video<device> (except ourselves)."""
    try:
        r = subprocess.run(
            ["fuser", f"/dev/video{device}"],
            capture_output=True, text=True, timeout=5,
        )
        pids = r.stdout.strip().split()
        my_pid = str(os.getpid())
        for pid in pids:
            pid = pid.strip().rstrip("m")
            if pid and pid != my_pid:
                console.print(f"  [yellow]Killing stale process {pid} holding /dev/video{device}[/yellow]")
                os.kill(int(pid), signal.SIGKILL)
        if pids:
            time.sleep(0.5)
    except Exception:
        pass


def disable_sdk_background_motion(reachy) -> list[str]:
    """Disable daemon-owned motion features when the SDK provides them."""
    disabled = []
    for method_name in ("disable_wobbling", "stop_head_tracking"):
        method = getattr(reachy, method_name, None)
        if not callable(method):
            continue
        try:
            method()
        except Exception:
            continue
        disabled.append(method_name)
    return disabled


def connect(config, console: Console) -> Optional["ReachyMini"]:
    """Connect to Reachy Mini using config.reachy settings.

    Handles daemon discovery, retries, wake-up, and antenna positioning.
    Returns a connected ReachyMini instance, or None if unavailable.
    """
    if not HAS_REACHY or not config.reachy.enabled:
        return None

    rcfg = config.reachy
    daemon_already_running = is_daemon_running()

    for attempt in range(rcfg.daemon_retry_attempts):
        try:
            if attempt == 0:
                console.print("  Connecting to Reachy Mini...")
            elif attempt == 1:
                console.print(f"  [dim]Daemon may still be starting, waiting {rcfg.daemon_startup_wait:.0f}s...[/dim]")
                time.sleep(rcfg.daemon_startup_wait)
                console.print("  Retrying connection to Reachy Mini...")
            else:
                kill_daemon(console)
                console.print("  Retrying connection (fresh daemon)...")

            if is_wireless(config):
                # Daemon lives on the robot's RPi: never spawn it locally,
                # discover it over the network and stream media via WebRTC.
                # The SDK finds the daemon by Zenoh multicast/gossip scouting
                # (connection_mode="network"); `robot_name` is the Zenoh topic
                # prefix and must match the daemon's `--robot-name`. No host or
                # port is needed: the WebRTC signalling endpoint (ws://<wlan_ip>:8443)
                # is resolved by the SDK from the daemon's own status payload.
                console.print(f"  Wireless: discovering daemon '{rcfg.robot_name}' on the network...")
                reachy = ReachyMini(
                    robot_name=rcfg.robot_name,
                    connection_mode=rcfg.connection_mode,
                    spawn_daemon=False,
                    use_sim=False,
                    timeout=rcfg.timeout,
                    media_backend=rcfg.media_backend,
                    automatic_body_yaw=rcfg.automatic_body_yaw,
                )
            else:
                reachy = ReachyMini(
                    spawn_daemon=rcfg.spawn_daemon,
                    use_sim=False,
                    timeout=rcfg.timeout,
                    media_backend=rcfg.media_backend,
                    automatic_body_yaw=rcfg.automatic_body_yaw,
                )

            disabled = disable_sdk_background_motion(reachy)
            if disabled:
                console.print(
                    "  ✓ SDK background motion disabled: " + ", ".join(disabled)
                )
            reachy.enable_motors()
            if rcfg.wake_on_start:
                if daemon_already_running:
                    console.print("  Ensuring Reachy Mini is awake...")
                elif is_wireless(config):
                    console.print("  Waking up Reachy Mini (remote)...")
                else:
                    console.print("  Waking up Reachy Mini...")
                reachy.wake_up()
                time.sleep(0.5)
                try:
                    reachy.set_target_antenna_joint_positions(rcfg.antenna_rest_position)
                    time.sleep(0.2)
                except Exception:
                    pass
                console.print("  [green]✓ Reachy Mini awake (head up, camera ready)[/green]")
            else:
                console.print("  [green]✓ Reachy Mini connected (wake_on_start=false)[/green]")
            return reachy

        except Exception as e:
            err_msg = str(e)
            if ("localhost and network" in err_msg or "both localhost" in err_msg.lower()) and attempt < rcfg.daemon_retry_attempts - 1:
                continue
            console.print(f"  [yellow]⚠ Reachy Mini unavailable: {e}[/yellow]")
            console.print("  [yellow]  Continuing without robot control[/yellow]")
            return None
    return None
