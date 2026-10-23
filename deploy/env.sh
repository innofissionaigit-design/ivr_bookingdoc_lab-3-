# Environment for every service in the stack. Source before launching.
#
# Everything referenced here MUST live under /workspace. RunPod wipes the
# container's overlay filesystem ("/" and "/root") on every restart and
# keeps only the network volume, so anything installed to /usr/local/bin
# or stored in /var/lib is gone the next time the pod boots. That is not a
# hypothetical: the ollama binary and the entire Postgres installation
# have each been lost to it three times.

# Host-specific overrides, sourced FIRST so every `${X:-default}` below sees
# them and keeps them. These are deliberately not written into this file:
# they are properties of the box the stack happens to run on, not of the
# stack. The Vast.ai pilot pod needs three (deploy/vast/provision.sh writes
# them): the platform's own Jupyter already owns 8080 so the clinic API
# cannot have it, only container ports 10100/10200 are NAT'd to a public
# port so the orchestrator must bind those, and that image pins
# UV_NO_CACHE=1 + UV_LINK_MODE=copy in /etc/environment, which would give
# every venv its own 3.9 GB copy of torch instead of a hardlink to one.
if [ -f /workspace/.host_env ]; then
    . /workspace/.host_env
fi

# Headless, always. coqui-tts's vocoder package imports matplotlib at module
# scope (TTS/vocoder/utils/generic_utils.py -> pyplot), so whatever backend
# the surrounding environment names is loaded just to import a TTS model --
# and matplotlib raises on a backend whose package is absent rather than
# falling back. Concretely: a Jupyter kernel exports
# MPLBACKEND=module://matplotlib_inline.backend_inline, matplotlib_inline is
# not in tts_venv, and tts_server.py died at import with
# "Key backend: ... is not a valid value" -- but only when the stack was
# started FROM Jupyter, which on a Vast.ai box is the default way in. Started
# over ssh the same code came up fine, which is exactly the kind of
# works-on-my-terminal difference that costs an afternoon. Agg needs no
# display and no extra package.
export MPLBACKEND=Agg
# The clinic's clock. The agent works out "today" in India time itself (agent/spoken_date.clinic_today), but
# clinic-api's date checks ("date in the past", today's chamber already over) use the process clock -- on a
# pod left on UTC that is still yesterday until 05:30 in Kolkata, and the two would disagree about "আজ".
export TZ="${TZ:-Asia/Kolkata}"
# "What have I booked?" is verified by the number booked from AND the patient's name on the booking -- the two
# things a phone booking collects (main.py _lookup_verification says why the security questions cannot work for
# a phone-booked patient). Unset, or "security", restores the date-of-birth / patient-ID questions.
export LOOKUP_VERIFICATION="${LOOKUP_VERIFICATION:-name}"

export HF_HOME=/workspace/.cache/huggingface
export TORCH_HOME=/workspace/.cache/torch
export HF_HUB_ENABLE_HF_TRANSFER=0
export PIP_CACHE_DIR=/workspace/.cache/pip

export OLLAMA_MODELS=/workspace/.ollama/models
# -1 keeps models resident indefinitely. Without it Ollama unloads after
# five idle minutes and the next caller pays a 47s cold start mid-call.
export OLLAMA_KEEP_ALIVE=-1

export PYTHONPATH=/workspace/kolkata-care-voice-agent:

# ---- service ports -------------------------------------------------------
# Defaults are the ports this stack has always used; every one is
# overridable because the host does not always get to choose them. On the
# Vast.ai pilot pod, concretely: the platform's own Jupyter already owns
# 8080, so the clinic API cannot have it, and only two container ports
# (10100/10200) are NAT'd to a public port, so the orchestrator has to bind
# those rather than 8100/8101. Hardcoding them made the stack unportable
# for no benefit -- see deploy/vast/start_vast.sh for the override set.
# Keep these as `${X:-default}`: start_all.sh and the Vast entrypoint both
# rely on a pre-set value winning.
export TTS_PORT=${TTS_PORT:-8002}
export CLINIC_API_PORT=${CLINIC_API_PORT:-8080}
export VOICE_AGENT_PORT=${VOICE_AGENT_PORT:-8100}
export VOICE_PCM_PORT=${VOICE_PCM_PORT:-8101}

# ASR checkpoints -- pilot single-L4 architecture (docs/adr/0001), three
# routed models. Bengali and Hindi are the AI4Bharat NeMo-fork checkpoints
# and load IN-PROCESS in the main venv (same fork, no conflict between
# the two languages -- only English needs mainline NeMo, hence its own
# venv and its own process below). Explicit paths, not the glob
# agent/asr.py's _resolve_nemo_file() used when there was only one
# checkpoint on the box -- with two "*indicconformer*" directories now
# coexisting under HF_HOME, an unqualified glob is ambiguous.
export VOICE_AGENT_NEMO_FILE_BN=/workspace/.cache/huggingface/hub/indicconformer_bn/snap/indicconformer_stt_bn_hybrid_rnnt_large.nemo
export VOICE_AGENT_NEMO_FILE_HI=/workspace/.cache/huggingface/hub/indicconformer_hi/snap/indicconformer_stt_hi_hybrid_rnnt_large.nemo

# English ASR runs as its OWN process in /workspace/venv-en-nemo (mainline
# NVIDIA NeMo -- cannot share a Python environment with the AI4Bharat fork
# above; see agent/asr.py's module docstring for exactly why). Reached
# over HTTP by agent/asr_router.py's English engine adapter.
export VOICE_AGENT_NEMO_FILE_EN=/workspace/.cache/huggingface/hub/fastconformer_en/snap/stt_en_fastconformer_hybrid_large_streaming_multi.nemo
export ENGLISH_ASR_URL=http://localhost:8003

# HF token for the gated IndicConformer bn/hi checkpoints -- read from a
# file, not committed anywhere, never printed. Only needed to (re-)download
# the checkpoints; not needed to load an already-downloaded .nemo file.
if [ -f /workspace/.hf_token ]; then
    export HF_TOKEN
    HF_TOKEN=$(cat /workspace/.hf_token)
fi

# TTS -- all three languages are AI4Bharat Indic-TTS checkpoints (FastPitch
# + HiFi-GAN, coqui-tts format), including English: AI4Bharat's own
# v1-checkpoints-release ships en.zip in the same format as bn/hi, so one
# coqui-tts process serves all three languages -- no second TTS stack
# needed. See tts_server.py's TTS_CHECKPOINTS_ROOT.
export TTS_CHECKPOINTS_ROOT=/workspace/tts_checkpoints

# DATABASE_URL is deliberately NOT set: clinic-api/db.py then defaults to
# sqlite:////workspace/clinic.db, which persists across restarts. Postgres
# physically cannot run on this volume -- see that file's docstring. Set
# this only when pointing at a real external Postgres.
export CLINIC_API_BASE=http://localhost:${CLINIC_API_PORT}

# The internal services (TTS, clinic API, English ASR) are reached only by the orchestrator on this
# machine, so they bind to loopback unless told otherwise. Only the orchestrator ports are public.
export INTERNAL_BIND_HOST=${INTERNAL_BIND_HOST:-127.0.0.1}

# Service token for the clinic API -- read from a file, never committed or printed, like the HF token
# above. When set, every /api/v1/* request must carry it (clinic-api/main.py) and the orchestrator
# sends it (agent/tools_client.py). Set CLINIC_API_REQUIRE_TOKEN=1 to make the API refuse everything
# if no token is configured, instead of running open.
if [ -f /workspace/.clinic_api_token ]; then
    export CLINIC_API_TOKEN
    CLINIC_API_TOKEN=$(cat /workspace/.clinic_api_token)
fi
export TTS_URL=http://localhost:${TTS_PORT}/synthesize
export SILERO_VAD_REPO=/workspace/silero-vad

# /workspace/bin first: that is where the persistent ollama binary lives
# (deploy/install_ollama.sh -- NOT the official installer, which ignores
# OLLAMA_INSTALL_DIR and writes to /usr/local, on the ephemeral overlay).
export PATH=/workspace/bin:/workspace/venv/bin:${PATH:-}

# ---- live language routing (main.py, tts_server.py, start_all.sh) ----------
# THE rollback lever (upgrade prompt section 9). "bn" alone = Bengali only, everywhere:
#   * main.py / main_pcm.py: no language ID, no Hindi or English ASR loaded, replies in Bengali;
#   * deploy/start_all.sh: the English ASR process is not started (and is stopped on a forced restart);
#   * tts_server.py: only the Bengali voice is loaded.
# One setting, no code revert, no redeploy of old code:
#     bash /workspace/kolkata-care-voice-agent/deploy/set_languages.sh bn          # roll back
#     bash /workspace/kolkata-care-voice-agent/deploy/set_languages.sh bn,hi,en    # restore
# which writes the persistent file below (so the choice survives a pod restart, like the token
# files) and restarts the stack. The file wins over the environment variable.
if [ -f /workspace/.voice_agent_languages ]; then
    VOICE_AGENT_LANGUAGES=$(tr -d ' \r\n' < /workspace/.voice_agent_languages)
fi
export VOICE_AGENT_LANGUAGES=${VOICE_AGENT_LANGUAGES:-bn,hi,en}
# Where the SpeechBrain VoxLingua107 LID model lives (persistent, ~86 MB).
export VOICE_AGENT_LID_DIR=/workspace/lid_model

# ---- pronunciation lexicon (agent/pronunciation.py) -----------------------
# OFF by default (upgrade prompt section 7; docs/adr/0002). Every entry in the lexicon is an
# unreviewed first pass by a non-native speaker (REVIEWED is empty); a wrong pronunciation of a
# clinical term is the same class of failure as a wrong fact, so it is not spoken silently.
# Consequence while it is off: a reply naming an English lab term with no approved spoken form is
# BLOCKED (agent/tts.py UnspeakableTextError) and the caller hears the system-busy fallback --
# e.g. the blood-test-list reply of the live call of 2026-09-28.
# To HEAR the drafts on a pod, so a native listener can review them (agent/pronunciation.review_sheet()),
# start with PRONUNCIATION_ALLOW_DRAFT=1 in the environment. main.py logs a warning whenever it is on.
# It is a listening aid, never a release setting. The way out of it is the native review, not a default.
export PRONUNCIATION_ALLOW_DRAFT=${PRONUNCIATION_ALLOW_DRAFT:-0}

# ---- admission control (agent/admission.py) -------------------------------
# Cap is per process; the WebM and PCM services each count their own calls.
export ADMISSION_MAX_CALLS=${ADMISSION_MAX_CALLS:-28}
# `touch` this file = human-only mode for every NEW call, no deploy needed.
export ADMISSION_BYPASS_FILE=/workspace/.ai_bypass
# Turn-latency load shedding is OFF until calibrated against a real load
# test; set e.g. ADMISSION_SHED_P95_S=2.5 once p95 is measured.
# The HTTP kill-switch (POST /api/admission/bypass) stays disabled unless a
# token exists -- read from a file, never committed:
if [ -f /workspace/.admission_token ]; then
    export ADMISSION_ADMIN_TOKEN
    ADMISSION_ADMIN_TOKEN=$(cat /workspace/.admission_token)
fi
