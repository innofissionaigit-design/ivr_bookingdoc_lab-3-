#!/bin/bash
# Put the browser client (the index page with the microphone) on a public HTTPS URL via ngrok.
#
#   bash /workspace/kolkata-care-voice-agent/deploy/vast/start_ngrok.sh          # PCM client, port 10100
#   PORT=10200 bash .../start_ngrok.sh                                          # WebM prototype instead
#
# WHY NGROK AT ALL: both clients call getUserMedia, which browsers refuse on a non-secure origin, so the page
# loads fine over http://<ip>:<port>/ and the microphone is silently blocked (deploy/vast/README.md). An ngrok
# https:// URL is a secure origin, and the clients build their WebSocket URL from window.location, so the
# wss:// upgrade follows with no edit.
#
# THE TRADE-OFF, stated plainly: this makes /ws/audio -- which reaches the clinic API and has no authentication
# of its own -- reachable by anyone who has the URL. The data on this pod is the synthetic seed, and the URL is a
# random subdomain, but treat it as open. Stop it with:   pkill -x ngrok
#
# The authtoken is read from /workspace/.ngrok_token (mode 600, never committed or printed). Safe to re-run: an
# existing tunnel is replaced.
set -u

PORT=${PORT:-10100}
LOG=/workspace/logs/ngrok.log
mkdir -p /workspace/logs /workspace/bin

if ! command -v ngrok >/dev/null 2>&1 && [ ! -x /workspace/bin/ngrok ]; then
    echo "installing ngrok"
    curl -fsSL https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz -o /tmp/ngrok.tgz || exit 1
    tar -xzf /tmp/ngrok.tgz -C /workspace/bin && rm -f /tmp/ngrok.tgz
fi
NGROK=$(command -v ngrok || echo /workspace/bin/ngrok)

if [ ! -s /workspace/.ngrok_token ]; then
    echo "!! /workspace/.ngrok_token missing -- ngrok needs an authtoken"; exit 1
fi
"$NGROK" config add-authtoken "$(cat /workspace/.ngrok_token)" >/dev/null 2>&1

# Exact process name, never a command-line pattern (deploy/start_all.sh says why).
pkill -x ngrok >/dev/null 2>&1; sleep 1

if ! curl -sf -m 3 "http://127.0.0.1:$PORT/api/health" >/dev/null; then
    echo "!! nothing healthy on :$PORT yet -- start the stack first (deploy/vast/start_vast.sh)"
fi

setsid "$NGROK" http "$PORT" --log=stdout --log-format=logfmt > "$LOG" 2>&1 < /dev/null &

for _ in $(seq 1 30); do
    URL=$(curl -sf -m 2 http://127.0.0.1:4040/api/tunnels 2>/dev/null \
        | python3 -c 'import sys,json; t=json.load(sys.stdin)["tunnels"]; print(next((x["public_url"] for x in t if x["public_url"].startswith("https")), ""))' 2>/dev/null)
    if [ -n "${URL:-}" ]; then
        echo "ngrok up: $URL  ->  localhost:$PORT"
        echo "open that URL in a browser; the mic works because it is https"
        exit 0
    fi
    sleep 1
done
echo "!! ngrok did not report a tunnel within 30 s -- see $LOG"
tail -5 "$LOG"
exit 1
