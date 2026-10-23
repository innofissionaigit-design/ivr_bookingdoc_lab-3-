# Vast.ai pilot pod overrides. provision.sh installs this as
# /workspace/.host_env, where deploy/env.sh sources it before anything else.
#
# Three things the platform dictates, none of them a property of the stack:
#
# 1. PORTS. Vast allocates external ports when the instance is created and
#    they cannot be added at runtime. On this instance only two normal
#    container ports are NAT'd to a public port:
#        container 10100 -> public 22123
#        container 10200 -> public 22154
#    and the platform's own Jupyter already holds 8080, so the clinic API
#    cannot keep its default. The PCM entrypoint gets 10100 because it is
#    the one that matters when real capacity limits do (deploy/start_all.sh,
#    ONLY_PCM note); the WebM browser-prototype path gets 10200.
#
# 2. uv. This image pins UV_NO_CACHE=1 and UV_LINK_MODE=copy in
#    /etc/environment, and a login shell re-applies them over anything
#    exported earlier. With those two settings each venv gets its own
#    private copy of the torch stack: measured 3.9 GB x 3 = 11.7 GB on a
#    32 GB disk. With a real cache and hardlink mode the three venvs share
#    one inode -- measured 3.9 GB total, link count 3. That difference is
#    what makes a three-language build fit here at all.
#
# 3. CUDA arch. The pod is an RTX 4090 (sm_89), not the L4 (sm_89 too) that
#    docs/adr/0001 assumes -- same compute capability, so nothing to
#    override, recorded here only so the next session does not re-derive it.

# ---- uv: must beat /etc/environment --------------------------------------
unset UV_NO_CACHE
export UV_CACHE_DIR=/workspace/.cache/uv
export UV_LINK_MODE=hardlink
export UV_PYTHON_INSTALL_DIR=/workspace/.uv-python

# ---- matplotlib: must beat the Jupyter kernel's inline backend -----------
# Vast's only reachable entry point on a fresh instance is its Jupyter
# server, so deploy/vast/podlink.py runs every remote command through a
# Jupyter KERNEL -- and a kernel exports
# MPLBACKEND=module://matplotlib_inline.backend_inline into everything it
# launches. /workspace/tts_venv has no matplotlib_inline (it is not a
# notebook venv), and coqui-tts imports matplotlib deep in
# TTS.vocoder.utils.generic_utils, so that inherited value makes matplotlib
# raise at import time:
#     ValueError: Key backend: 'module://matplotlib_inline.backend_inline'
#                 is not a valid value for backend
# which surfaced as provision.sh's venv_tts stage failing its import check
# while the install itself had succeeded perfectly (RC=0). The stage looks
# broken, the venv is fine, and the difference is only visible if you run
# the same command from an SSH shell. Agg is correct here anyway: nothing on
# this pod has a display.
export MPLBACKEND=Agg

# ---- ports ---------------------------------------------------------------
export CLINIC_API_PORT=8081
export VOICE_PCM_PORT=10100
export VOICE_AGENT_PORT=10200

# TTS (8002) and English ASR (8003) keep their defaults: both are loopback
# only (INTERNAL_BIND_HOST), nothing on this box contends for them.

# ---- pronunciation: TEST SETTING, NOT A RELEASE SETTING ------------------
# deploy/env.sh ships this OFF, and that is right for production: every entry
# in agent/pronunciation.py's lexicon is an unreviewed first pass by a
# non-native speaker, and a wrong pronunciation of a clinical term is the
# same class of failure as a wrong fact.
#
# It is ON here because this pod is a test box and OFF makes it untestable.
# Measured on this instance: a Bengali "blood test price" query was
# transcribed correctly, looked up correctly, and then the reply was BLOCKED
# by agent/tts.py (UnspeakableTextError) on
#   ['Grouping', 'Rh Typing', 'Sugar Fasting', 'Calcium']
# so the caller heard only the system-busy fallback and nothing downstream of
# the template layer could be exercised at all. env.sh's own docstring names
# this exact case and names this flag as the way to hear the drafts on a pod
# so a native listener can review them (agent/pronunciation.review_sheet()).
#
# main.py logs a warning on every start while this is on. Turn it OFF before
# this box takes a real caller. The way out is the native review, not this
# flag: see docs/adr/0002.
export PRONUNCIATION_ALLOW_DRAFT=1

# ---- public addresses, for the record ------------------------------------
# Raw TCP forwards, NOT behind the Caddy auth edge, so the WebSocket upgrade
# on /ws/audio passes through unchanged and no token is needed:
#     PCM   wss://194.14.47.19:22123/ws/audio
#     WebM  wss://194.14.47.19:22154/ws/audio
# Exposed with no authentication of their own -- see deploy/vast/README.md.
export VOICE_PCM_PUBLIC_PORT=12839
export VOICE_AGENT_PUBLIC_PORT=12836
