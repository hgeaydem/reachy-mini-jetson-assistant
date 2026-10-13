#!/usr/bin/env bash
# build_thor.sh — delegate the container build to the NVIDIA Thor (podman).
#
# The two Containerfiles here target L4T userspace (r36.5 / r39.2). The actual
# `podman build` must run on the Thor, because (a) Thor is the only box with the
# matching aarch64 L4T userspace to base-test against, and (b) the base images
# are already present/pullable there.
#
# Usage (from the repo root, on any dev box with ssh to Thor):
#   ./build_thor.sh                 # build r39.2 only (Thor target)
#   ./build_thor.sh r36.5           # build r36.5 only
#   ./build_thor.sh both            # build both
#   WEBRTC=1 ./build_thor.sh both   # also build the rswebrtc plugin into the
#                                   # image (BUILD_WEBRTC=1; default 0 = faster
#                                   # build, plugin layer skipped)
#
# Thor-side env (set in ~/.bashrc or pass through): THOR_HOST, THOR_USER.
# Credentials: ssh-agent / key based. No password in this script.
set -euo pipefail

cd "$(dirname "$0")/.."          # repo root
REPO_ROOT="$(pwd)"
TARGETS="${1:-r39.2}"
WEBRTC="${WEBRTC:-0}"

: "${THOR_HOST:?set THOR_HOST (Thor ssh host)}"
: "${THOR_USER:?set THOR_USER (Thor ssh user)}"

# Where the repo lands on Thor.
REMOTE_DIR="/tmp/rmja-build-$(date +%Y%m%d%H%M%S)"

# ---- 1. Stage the repo (with wheels/ if present) onto Thor -------------------
echo ">> staging repo -> ${THOR_USER}@${THOR_HOST}:${REMOTE_DIR}"
ssh "${THOR_USER}@${THOR_HOST}" "mkdir -p ${REMOTE_DIR}"
# Use tar over ssh to avoid scp permission issues; exclude venvs + heavy caches.
tar --exclude='.venv*' --exclude='**/.venv*' --exclude='.git' --exclude='__pycache__' \
    --exclude='*.egg-info' --exclude='models' --exclude='logs' \
    -cf - . | ssh "${THOR_USER}@${THOR_HOST}" "tar -xf - -C ${REMOTE_DIR}"

# ---- 2. Run the podman build(s) on Thor --------------------------------------
build_one() {
  local rel="$1"
  local cf="container/Containerfile.${rel}"
  local tag="rmja-assistant:${rel}"
  echo ">> building ${tag} (Containerfile: ${cf}, WEBRTC=${WEBRTC})"
  ssh "${THOR_USER}@${THOR_HOST}" "set -e; cd ${REMOTE_DIR}; \
      podman pull $(grep -m1 'ARG BASE_IMAGE=' ${cf} | sed -E 's/ARG BASE_IMAGE=//') ; \
      podman build --pull missing \
          -t ${tag} \
          --build-arg BUILD_WEBRTC=${WEBRTC} \
          -f ${cf} . ; \
      echo '>> build complete: ${tag}'"
}

case "${TARGETS}" in
  r36.5|both) build_one r36.5 ;;
  *) : ;;
esac
case "${TARGETS}" in
  r39.2|both) build_one r39.2 ;;
  r36.5) : ;;
  *) echo "unknown target: ${TARGETS} (use r36.5, r39.2, or both)"; exit 2 ;;
esac

# ---- 3. Smoke test the built image on Thor (imports + gstreamer + rswebrtc) -
smoke() {
  local tag="$1"
  echo ">> smoke test ${tag}"
  ssh "${THOR_USER}@${THOR_HOST}" "set -e; podman run --rm ${tag} bash -c '
      echo reachy:;  python3 -c \"import reachy_mini; print(reachy_mini.__version__)\";
      echo gst:;     python3 -c \"import gi; gi.require_version(chr(71)+chr(115)+chr(116),chr(49)+chr(46)+chr(48)); from gi.repository import Gst; Gst.init(None); print(Gst.version_string())\";
      echo ctranslate2:; python3 -c \"import ctranslate2; print(ctranslate2.__version__)\" || echo no-ctranslate2;
      echo rswebrtc:;  python3 -c \"import gi; gi.require_version(chr(71)+chr(115)+chr(116),chr(49)+chr(46)+chr(48)); from gi.repository import Gst; Gst.init(None); print(\\\"webrtcsrc\\\", Gst.ElementFactory.make(\\\"webrtcsrc\\\") is not None); print(\\\"webrtcsink\\\", Gst.ElementFactory.make(\\\"webrtcsink\\\") is not None)\" || echo rswebrtc-elements-missing;
  '"
}
case "${TARGETS}" in
  r36.5|both) smoke "rmja-assistant:r36.5" ;;
  *) : ;;
esac
case "${TARGETS}" in
  r39.2|both) smoke "rmja-assistant:r39.2" ;;
  r36.5) : ;;
  *) : ;;
esac

# ---- 4. Optional cleanup ------------------------------------------------------
read -r -p ">> keep staged repo on Thor? [y/N] " ans
if [[ "${ans}" != [yY] ]]; then
  echo ">> removing ${REMOTE_DIR}"
  ssh "${THOR_USER}@${THOR_HOST}" "rm -rf ${REMOTE_DIR}"
fi
echo ">> done."
