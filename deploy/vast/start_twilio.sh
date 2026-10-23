#!/usr/bin/env bash
# Bring up the Twilio telephony connector on a Vast.ai pod, behind an ngrok tunnel.
#
#   bash /workspace/kolkata-care-voice-agent/deploy/vast/start_twilio.sh
#   bash .../start_twilio.sh --print-only     # set up the tunnel, print the TwiML, start nothing
#
# Run deploy/vast/start_vast.sh FIRST: this script only adds the phone path in front of an already-running agent.
#
# WHAT IS EXPOSED, AND WHY THAT IS THE POINT
# ------------------------------------------
# deploy/vast/README.md refuses a public ingress tunnel, and it is right about the thing it refuses: tunnelling the
# voice entrypoints (10100/10200) puts an UNAUTHENTICATED /ws/audio -- which reaches the clinic API and patient data --
# on the open internet. This script does not do that. It tunnels port 8200, the Twilio Media Streams adapter, whose
# every connection must carry a valid X-Twilio-Signature or be refused with 403 before any WebSocket is accepted:
#
#   caller -> Twilio -> wss://<id>.ngrok-free.app/twilio/media -> ngrok -> 127.0.0.1:8200  (signature checked HERE)
#                                                                              |
#                                                                              v
#                                                      connector -> 127.0.0.1:10100 /ws/audio  (stays on loopback)
#
# So the public surface is signature-gated and /ws/audio is never reachable from outside. That property depends
# ENTIRELY on the Auth Token being set: with TELEPHONY_TWILIO_ALLOW_UNVERIFIED=1 the gate is off and the tunnel is
# exactly the open door the README refuses. This script therefore refuses to start in that combination unless
# I_ACCEPT_AN_OPEN_VOICE_ENDPOINT=1 is also passed, so it can never happen by accident.
set -euo pipefail

REPO="${REPO:-/workspace/kolkata-care-voice-agent}"
PRINT_ONLY=0
[ "${1:-}" = "--print-only" ] && PRINT_ONLY=1

# ---------------------------------------------------------------------------------------------------------------------
# 1. configuration: platform ports first, then this pod's Twilio credentials (which win)
# ---------------------------------------------------------------------------------------------------------------------
# shellcheck disable=SC1091
[ -f /workspace/.host_env ] && . /workspace/.host_env
# shellcheck disable=SC1091
. "$REPO/deploy/env.sh" >/dev/null 2>&1 || true

if [ ! -f /workspace/.twilio_env.sh ]; then
    echo "FATAL: /workspace/.twilio_env.sh is missing." >&2
    echo "Create it (mode 600) with your Twilio settings -- see telephony/README.md. It is deliberately NOT in the" >&2
    echo "repo and never pushed, so the Auth Token and API key secret exist only on this pod." >&2
    exit 2
fi
# shellcheck disable=SC1091
. /workspace/.twilio_env.sh

: "${TELEPHONY_LISTEN_PORT:=8200}"
: "${VOICE_PCM_PORT:=10100}"
PY=/workspace/venv/bin/python3

# ---------------------------------------------------------------------------------------------------------------------
# 2. the safety interlock
# ---------------------------------------------------------------------------------------------------------------------
if [ -z "${TELEPHONY_PASSWORD:-}" ]; then
    if [ "${TELEPHONY_TWILIO_ALLOW_UNVERIFIED:-}" = "1" ] && [ "${I_ACCEPT_AN_OPEN_VOICE_ENDPOINT:-}" != "1" ]; then
        cat >&2 <<'WARN'
FATAL: refusing to put an unverified voice endpoint on the public internet.

TELEPHONY_PASSWORD (the Twilio Auth Token) is empty and TELEPHONY_TWILIO_ALLOW_UNVERIFIED=1, so every connection to
the tunnel would be accepted without proof it came from Twilio. On this pod that reaches the clinic API and the
patient data behind it, which is the exact exposure deploy/vast/README.md refuses.

Do this instead: put the Auth Token in /workspace/.twilio_env.sh (Twilio console home page, next to the Account SID).
It is one value and it closes the hole completely.

If you understand the above and still want it -- a throwaway pod with no real data, a tunnel you kill within minutes:
    I_ACCEPT_AN_OPEN_VOICE_ENDPOINT=1 bash deploy/vast/start_twilio.sh
WARN
        exit 2
    fi
    if [ "${TELEPHONY_TWILIO_ALLOW_UNVERIFIED:-}" != "1" ]; then
        echo "FATAL: TELEPHONY_PASSWORD (Twilio Auth Token) is not set in /workspace/.twilio_env.sh." >&2
        echo "Every inbound call would be refused with 403. Add it and re-run." >&2
        exit 2
    fi
fi

# ---------------------------------------------------------------------------------------------------------------------
# 3. the agent has to be up first, or the caller just hears silence
# ---------------------------------------------------------------------------------------------------------------------
if ! curl -sf -m 5 "http://127.0.0.1:${VOICE_PCM_PORT}/api/health" >/dev/null 2>&1; then
    echo "FATAL: no voice agent on 127.0.0.1:${VOICE_PCM_PORT} (/api/health did not answer)." >&2
    echo "Start the stack first:  bash $REPO/deploy/vast/start_vast.sh" >&2
    exit 2
fi
echo "[ok] voice agent answering on 127.0.0.1:${VOICE_PCM_PORT}"

# ---------------------------------------------------------------------------------------------------------------------
# 4. the telephony connector needs the websockets package; the main venv was not built with it
# ---------------------------------------------------------------------------------------------------------------------
if ! "$PY" -c "import websockets" >/dev/null 2>&1; then
    echo "installing websockets into /workspace/venv (telephony only; the AI pipeline does not use it)"
    "$PY" -m pip install --quiet "websockets>=13"
fi
echo "[ok] websockets $("$PY" -c 'import websockets; print(websockets.__version__)')"

# ---------------------------------------------------------------------------------------------------------------------
# 5. ngrok: install if absent, authenticate from /workspace/.ngrok_token, tunnel the ADAPTER port
# ---------------------------------------------------------------------------------------------------------------------
if ! command -v ngrok >/dev/null 2>&1; then
    echo "installing ngrok"
    curl -sSL https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz -o /tmp/ngrok.tgz
    tar -xzf /tmp/ngrok.tgz -C /usr/local/bin
    rm -f /tmp/ngrok.tgz
fi
if [ -f /workspace/.ngrok_token ]; then
    ngrok config add-authtoken "$(cat /workspace/.ngrok_token)" >/dev/null 2>&1 || true
fi

mkdir -p /workspace/logs
if ! curl -sf -m 3 http://127.0.0.1:4040/api/tunnels >/dev/null 2>&1; then
    echo "starting ngrok on port ${TELEPHONY_LISTEN_PORT}"
    nohup setsid ngrok http "${TELEPHONY_LISTEN_PORT}" --log=stdout \
        > /workspace/logs/ngrok.log 2>&1 &
    for _ in $(seq 1 30); do
        sleep 1
        curl -sf -m 3 http://127.0.0.1:4040/api/tunnels >/dev/null 2>&1 && break
    done
fi

PUBLIC_HOST=$(curl -sf -m 10 http://127.0.0.1:4040/api/tunnels \
    | "$PY" -c 'import json,sys,urllib.parse
tunnels = json.load(sys.stdin).get("tunnels", [])
urls = [t["public_url"] for t in tunnels if t.get("public_url","").startswith("https://")]
print(urllib.parse.urlsplit(urls[0]).netloc if urls else "")' 2>/dev/null || true)

if [ -z "$PUBLIC_HOST" ]; then
    echo "FATAL: ngrok did not report an https tunnel. Last log lines:" >&2
    tail -20 /workspace/logs/ngrok.log >&2 || true
    exit 2
fi

export TELEPHONY_ENDPOINT="wss://${PUBLIC_HOST}/twilio/media"
echo "[ok] tunnel up: ${TELEPHONY_ENDPOINT}"

# ---------------------------------------------------------------------------------------------------------------------
# 6. the one manual step, printed with the live URL already in it
# ---------------------------------------------------------------------------------------------------------------------
cat <<EOF

--------------------------------------------------------------------------------------
PASTE THIS INTO TWILIO  (console > TwiML Bins > Create, then point your number at it)

<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="${TELEPHONY_ENDPOINT}" />
  </Connect>
</Response>

Then: Phone Numbers > Manage > Active numbers > your number > Voice Configuration
      "A call comes in"  ->  that TwiML Bin  ->  Save

The url must match character for character: the signature is computed over it.
NOTE: a free ngrok URL changes every restart, so re-paste it after each restart.
--------------------------------------------------------------------------------------

EOF

cd "$REPO"
"$PY" scripts/twilio_preflight.py || true

if [ "$PRINT_ONLY" = "1" ]; then
    echo "--print-only: tunnel is up, connector NOT started."
    exit 0
fi

echo "starting the telephony connector (Ctrl+C to stop)"
exec "$PY" -m telephony
