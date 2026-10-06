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

echo "== services (languages: $VOICE_AGENT_LANGUAGES) =="
probe ollama      "http://localhost:11434/api/tags"
probe tts         "http://localhost:8002/health"
probe clinic-api  "http://localhost:8080/api/health"
probe voice-agent "http://localhost:8100/api/health"
probe voice-pcm   "http://localhost:8101/api/health"
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
