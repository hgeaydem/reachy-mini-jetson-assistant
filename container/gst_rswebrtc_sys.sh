#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# gst_rswebrtc_sys.sh — build ONLY the Rust rswebrtc plugin (webrtcsrc/webrtcsink)
# against the SYSTEM GStreamer that the L4T base already ships.
#
# The base provides:
#   libgstreamer1.0-0 + gstreamer1.0-plugins-{base,good,bad,ugly} + gstreamer1.0-tools
#   -> runtime .so incl. libgstwebrtc-1.0.so (the C webrtc lib)
# noble (r39.2 base): GStreamer 1.24.2   jammy (r36.4 base): GStreamer 1.20.3
#
# Crate generation: gst-plugins-rs @ gstreamer-1.24.0 (workspace gstreamer-rs
# branch 0.22). Every gst-* dep is requested with feature "v1_20" (FFI floor
# GStreamer 1.20), and the plugin's own default feature "v1_22" is dropped with
# --no-default-features (it only raises the floor; webrtcsrc/webrtcsink register
# unconditionally in src/lib.rs). So one recipe builds on BOTH bases:
#   jammy  1.20.3 >= 1.20  OK
#   noble  1.24.2 >= 1.20  OK
# The plugin ships its own Cargo.lock; the 2024 pin of `time 0.3.34` does not
# compile on rustc >= 1.80 (E0282), so it is bumped to 0.3.47 (the floor used by
# the 1.28.3 generation) before building.
#
# Thor-validated 2026-10-13: RSWERTC_OK on both bases (see container/README.md).
#
# NOTE: must be run under bash (`bash gst_rswebrtc_sys.sh`): the base image's
# /bin/sh is dash, which rejects `set -o pipefail`.
#
# Usage (root, inside the target image):  bash gst_rswebrtc_sys.sh
set -euxo pipefail

PLUGIN_DIR=/usr/lib/aarch64-linux-gnu/gstreamer-1.0
GSPLUGRS_TAG="${GSPLUGRS_TAG:-gstreamer-1.24.0}"

# --- deps ----------------------------------------------------------------
apt-get update
apt-get install -y --no-install-recommends \
    pkg-config build-essential curl ca-certificates git \
    libssl-dev \
    libgstreamer1.0-dev libgstreamer-plugins-bad1.0-dev gstreamer1.0-tools
# git is required: gstreamer-rs is a cargo *git* dependency (GitLab branch 0.22);
# the noble base ships git, the jammy (r36.4) base does not.
# libssl-dev is required: reqwest (default-tls) -> native-tls -> openssl-sys.

GSTPC="$(pkg-config --modversion gstreamer-1.0)"
echo "building rswebrtc against system GStreamer ${GSTPC}"

# refuse if the webrtc C lib is absent (should never happen on L4T base)
pkg-config --exists gstreamer-webrtc-1.0 || { echo "no gstreamer-webrtc-1.0"; exit 2; }

if ! command -v cargo >/dev/null 2>&1; then
    curl -sSf https://sh.rustup.rs -o /tmp/rustup.sh
    sh /tmp/rustup.sh -y --default-toolchain stable --profile minimal
fi
. "$HOME/.cargo/env"
# NOTE: no cargo-c. `cargo build` already emits the cdylib libgstrswebrtc.so;
# cargo-c (a wrapper) drags openssl-sys/perl in for nothing. We install it by
# hand below.

# --- build the rswebrtc plugin -------------------------------------------
# default features (janus/whip/web_server/whep) pull warp/reqwest/aws; the SDK
# only uses webrtcsrc + webrtcsink with its own signaller -> --no-default-features.
# [lib] name = "gstrswebrtc", cdylib; install_subdir = "gstreamer-1.0".
curl -fsSL "https://github.com/GStreamer/gst-plugins-rs/archive/refs/tags/${GSPLUGRS_TAG}.tar.gz" \
    -o /tmp/gst-plugins-rs.tar.gz
tar -xf /tmp/gst-plugins-rs.tar.gz -C /tmp
cd /tmp/gst-plugins-rs-${GSPLUGRS_TAG}

export GST_PLUGIN_PATH="${PLUGIN_DIR}"
# The 1.24.0 tag's Cargo.lock (May 2024) pins `time 0.3.34`, which does not
# compile on rustc >= 1.80 (E0282 type inference). Bump it to 0.3.47 — the
# version already proven by the 1.28.3 generation's lockfile.
cargo update -p time --precise 0.3.47
cargo build -p gst-plugin-webrtc --no-default-features --release

# install the cdylib into the plugin dir (cargo-c would do this; by hand it's
# one copy — the [lib] name is gstrswebrtc, install_subdir = gstreamer-1.0)
install -m 0755 target/release/libgstrswebrtc.so "${PLUGIN_DIR}/libgstrswebrtc.so"

ldconfig
echo "rswebrtc .so installed:"
ls -la "${PLUGIN_DIR}" | grep -Ei 'rswebrtc|webrtc' || true

# --- verify ---------------------------------------------------------------
gst-inspect-1.0 webrtcsrc  >/dev/null 2>&1 && echo "webrtcsrc PRESENT" \
  || { echo "webrtcsrc ABSENT"; exit 1; }
gst-inspect-1.0 webrtcsink >/dev/null 2>&1 && echo "webrtcsink PRESENT" \
  || { echo "webrtcsink ABSENT"; exit 1; }
echo "RSWERTC_OK gst=${GSTPC}"
