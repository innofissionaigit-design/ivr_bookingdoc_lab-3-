#!/bin/bash
# One-line-per-service readiness check. Safe to run repeatedly while the
# models are still loading.
set -u

probe() {  # probe <name> <url>
    local name=$1 url=$2
    local body
    body=$(curl -sf -m 4 "$url" 2>/dev/null)
    if [ -n "$body" ]; then
        printf '  %-13s UP    %s\n' "$name" "$(echo "$body" | head -c 120)"
    else
        printf '  %-13s ..... not ready\n' "$name"
    fi
}

# Same source of truth start_all.sh uses (deploy/env.sh), without sourcing the whole file.
if [ -f /workspace/.voice_agent_languages ]; then
    VOICE_AGENT_LANGUAGES=$(tr -d ' \r\n' < /workspace/.voice_agent_languages)
fi
VOICE_AGENT_LANGUAGES=${VOICE_AGENT_LANGUAGES:-bn,hi,en}

# The PLATFORM's port overrides, if this box installed any. Not env.sh -- this script still deliberately does not
# source that, for the reason the language list above gives. But /workspace/.host_env is exactly the file that moves
# these ports off their RunPod defaults, and without it this script probed 8080/8100/8101 on a Vast instance whose
# services were on 8081/10200/10100 and reported three healthy services as "not ready" (2026-10-09). A readiness
# check that lies about a running stack is worse than no check: the next session chases a restart it does not need.
# shellcheck disable=SC1091
[ -f /workspace/.host_env ] && . /workspace/.host_env >/dev/null 2>&1

# Same defaults deploy/env.sh declares, repeated for the same reason the
# language list above is: this script deliberately does not source env.sh,
# so it can be run against a stack whose environment it did not launch.
TTS_PORT=${TTS_PORT:-8002}
CLINIC_API_PORT=${CLINIC_API_PORT:-8080}
VOICE_AGENT_PORT=${VOICE_AGENT_PORT:-8100}
VOICE_PCM_PORT=${VOICE_PCM_PORT:-8101}

echo "== services (languages: $VOICE_AGENT_LANGUAGES) =="
probe ollama      "http://localhost:11434/api/tags"
probe tts         "http://localhost:$TTS_PORT/health"
probe clinic-api  "http://localhost:$CLINIC_API_PORT/api/health"
probe voice-agent "http://localhost:$VOICE_AGENT_PORT/api/health"
probe voice-pcm   "http://localhost:$VOICE_PCM_PORT/api/health"
case ",${VOICE_AGENT_LANGUAGES:-bn,hi,en}," in
    *,en,*) probe english-asr "http://localhost:8003/health" ;;
    *) printf '  %-13s off   (VOICE_AGENT_LANGUAGES=%s)\n' english-asr "${VOICE_AGENT_LANGUAGES}" ;;
esac

echo
echo "== resident models =="
/workspace/bin/ollama ps 2>/dev/null | tail -n +1 || echo "  ollama not reachable"

echo
echo "== gpu =="
nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader
