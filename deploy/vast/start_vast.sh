#!/bin/bash
# The one entrypoint on the Vast.ai pod. Equivalent of
# deploy/bootstrap_pod.sh, which is RunPod-shaped and does things this
# platform does not need.
#
#   bash /workspace/kolkata-care-voice-agent/deploy/vast/start_vast.sh
#   FORCE_RESTART=1 bash .../start_vast.sh      # restart services already up
#   ONLY_PCM=1 bash .../start_vast.sh           # PCM entrypoint only
#
# Safe to re-run. It does NOT reinstall Ollama the way bootstrap_pod.sh
# does: on RunPod the container overlay is wiped on every restart, so the
# ~2 GB ollama binary had to be re-fetched each time. On Vast a stop/start
# preserves the whole container filesystem, so the binary is still there.
#
# What is NOT preserved here is more dangerous, and bootstrap_pod.sh's
# assumption is inverted: on this instance /workspace is NOT a mounted
# volume (vast-capabilities reports workspace_is_volume=false), so a
# RECYCLE or DESTROY wipes everything -- venvs, checkpoints, clinic.db, the
# token files. A stop/start is safe; a recycle means re-running
# deploy/vast/provision.sh. The source of truth for the CODE is the laptop
# that pushes to this box (deploy/vast/podlink.py), so only the ~20 GB of
# downloaded models and the seeded DB are actually at risk.
set -u

REPO=/workspace/kolkata-care-voice-agent

if [ ! -f /workspace/.host_env ]; then
    cp "$REPO/deploy/vast/host_env.vast.sh" /workspace/.host_env
fi
. /workspace/.host_env

echo "== ollama =="
if [ -x /workspace/bin/ollama ]; then
    if pgrep -x ollama >/dev/null; then
        echo "  already running"
    else
        setsid /workspace/bin/ollama serve > /workspace/logs/ollama.log 2>&1 < /dev/null &
        echo "  starting"; sleep 5
    fi
else
    echo "  !! /workspace/bin/ollama missing -- run deploy/vast/provision.sh ollama"
fi

bash "$REPO/deploy/start_all.sh"

echo
echo "== public endpoints (raw TCP, NOT behind the Caddy auth edge) =="
echo "  PCM   ws://${PUBLIC_IPADDR:-<ip>}:${VOICE_PCM_PUBLIC_PORT:-22123}/ws/audio"
echo "  WebM  ws://${PUBLIC_IPADDR:-<ip>}:${VOICE_AGENT_PUBLIC_PORT:-22154}/ws/audio"
echo "  Both are UNAUTHENTICATED -- see deploy/vast/README.md before using"
echo "  this with anything but synthetic test audio."
echo
bash "$REPO/deploy/status.sh"
