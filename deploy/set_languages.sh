#!/bin/bash
# The language rollback lever (upgrade prompt section 9; docs/adr/0002).
#
#   bash deploy/set_languages.sh bn          # Bengali only: no language ID, no Hindi/English ASR, no English ASR process
#   bash deploy/set_languages.sh bn,hi,en    # back to the three-language build
#   bash deploy/set_languages.sh             # show what is set
#
# Writes the choice to /workspace/.voice_agent_languages (persistent: it survives a pod restart, like the token
# files; deploy/env.sh reads it) and restarts the stack so every service -- orchestrator, TTS server, English ASR --
# comes up under it. No code revert, no redeploy of old code: the rollback is one setting and a restart.
set -u

REPO=/workspace/kolkata-care-voice-agent
FILE=/workspace/.voice_agent_languages

if [ $# -eq 0 ]; then
    if [ -f "$FILE" ]; then echo "set: $(cat "$FILE")"; else echo "not set (default: bn,hi,en)"; fi
    exit 0
fi

# Validate before writing anything: a typo must not take every service down on the next restart.
wanted=$(echo "$1" | tr -d ' ' | tr 'A-Z' 'a-z')
case ",$wanted," in
    *,bn,*) ;;
    *) echo "refusing: '$1' has no 'bn'. Bengali is always active (the clinic's primary language)." >&2; exit 2 ;;
esac
IFS=',' read -ra parts <<< "$wanted"
for p in "${parts[@]}"; do
    case "$p" in
        bn|hi|en) ;;
        *) echo "refusing: unknown language '$p' (have bn, hi, en)." >&2; exit 2 ;;
    esac
done

echo "$wanted" > "$FILE"
echo "VOICE_AGENT_LANGUAGES=$wanted (written to $FILE)"
# FORCE_RESTART: services that are already up must be restarted to pick the change up.
FORCE_RESTART=1 bash "$REPO/deploy/start_all.sh"
echo
echo "Models load for 1-3 minutes. Watch readiness with:  bash $REPO/deploy/status.sh"
