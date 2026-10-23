#!/bin/bash
# From-scratch provisioning of the whole stack on a Vast.ai GPU instance.
#
#   bash /workspace/kolkata-care-voice-agent/deploy/vast/provision.sh          # everything
#   bash .../provision.sh venv-main asr                                        # named stages
#
# WHY THIS EXISTS AND setup_addon.sh DOES NOT COVER IT
# ----------------------------------------------------
# setup_addon.sh is an ADD-ON: it assumes voice-to-rx-repo already set up
# NeMo, Ollama and Silero on the same pod, and it exits early if
# /workspace/voice-to-rx-repo is missing. On a fresh Vast instance none of
# that exists, so this script builds the full stack instead. It is the Vast
# equivalent of deploy/bootstrap_pod.sh, which likewise assumes a pod whose
# /workspace already carries the venvs and checkpoints.
#
# EVERY VERSION PIN BELOW IS A FAILURE THAT WAS ACTUALLY HIT, IN ORDER
# -------------------------------------------------------------------
# These are not defensive guesses. Each one is a traceback from this box,
# and removing any of them puts that traceback back:
#
#   python 3.11              torch 2.1.0 publishes no cp312 wheel, and the
#                            Vast image's python is 3.12.
#   pytorch-lightning 2.2.1  (main venv) PL 2.6 removed NeptuneLogger, which
#                            nemo/utils/exp_manager.py imports unconditionally.
#   pytorch-lightning 2.0.7  (english venv) mainline nemo_toolkit 1.23.0 pins
#                            pytorch-lightning>=2.0,<=2.0.7 -- the AI4Bharat
#                            fork's pin is looser, which is why the two venvs
#                            legitimately differ here.
#   setuptools<81            lightning_fabric calls pkg_resources.declare_
#                            namespace(); uv venvs ship no setuptools at all,
#                            and setuptools 81 drops pkg_resources.
#   numpy 1.26.4             torch 2.1.0 is built against the numpy 1.x ABI.
#                            On numpy 2 it imports but every tensor<->array
#                            conversion dies with "Failed to initialize
#                            NumPy: _ARRAY_API not found". agent/asr.py's
#                            np.sctypes shim then no-ops harmlessly (it is
#                            hasattr-guarded), so nothing is lost by pinning.
#   pyarrow 14.0.2 +         datasets 2.16 builds a class off
#   datasets 2.16.1          pa.PyExtensionType, removed in pyarrow 15.
#   huggingface_hub 0.23.2   NeMo imports HfFolder and ModelFilter; ModelFilter
#                            went in hf_hub 0.24 and HfFolder in 1.0. This is
#                            the same constraint tts_server.py's docstring
#                            names -- and the reason TTS cannot share this venv.
#   transformers 4.46.3      (main/english venvs) newer transformers imports
#                            ResolvedRevision from huggingface_hub, which
#                            0.23.2 does not export.
#   speechbrain 1.0.0        speechbrain 1.1.x uses torch.amp.custom_fwd,
#                            added only in torch 2.4. On torch 2.1 LID raises
#                            AttributeError at INFERENCE, not at load -- so
#                            /api/health still said lid_loaded:true while
#                            every turn logged "LID failed -- treating as
#                            unknown" and silently fell back to dual ASR.
#   youtokentome             declares no build backend dependency on Cython,
#   --no-build-isolation     so its sdist cannot build under isolation.
#
# THE TTS VENV IS THE ONE DELIBERATE EXCEPTION
# --------------------------------------------
# It gets coqui-tts + torch 2.6.0, NOT the shared torch 2.1.0. TTS 0.22.0
# (the last official Coqui release, the obvious era match for these 2022
# checkpoints) has an upstream bug that makes multi-speaker FastPitch
# unusable on GPU: TTS/tts/utils/synthesis.py calls
# id_to_torch(speaker_id, device=device) while the speaker id stays on CPU,
# so forward_tts.py's `g = self.emb_g(g)` raises "Expected all tensors to be
# on the same device". Verified it is not a placement problem on our side:
# every model parameter, emb_g.weight included, reported cuda:0, and an
# explicit .cuda() on the whole model changed nothing. The maintained
# coqui-tts fork has it fixed but version-checks torch and reports a modern
# torch as "not found", hence 2.6.0. Patching vendored library code was
# rejected: CLAUDE.md keeps vendor code behind a boundary, and an edit inside
# site-packages is invisible to every later session and lost on any reinstall.
# Cost of the exception is one unshared torch copy, ~4 GB.
#
# DISK IS THE BINDING CONSTRAINT -- READ BEFORE ADDING ANYTHING
# ------------------------------------------------------------
# This instance has 32 GB total and /workspace is NOT a separate volume; it
# is the same overlay as /. A three-language build fits only because the
# three venvs HARDLINK one torch instead of copying it, which needs the uv
# settings in host_env.vast.sh (the image pins UV_NO_CACHE=1 and
# UV_LINK_MODE=copy, and a login shell re-applies them over any export).
# Measured: 3 venvs sharing torch = 3.9 GB total, link count 4. Copying =
# 11.7 GB. Verify with:
#     stat -c "links=%h inode=%i" /workspace/*venv*/lib/python3.11/site-packages/torch/lib/libtorch_cuda.so
# If those inodes ever differ, the dedupe silently regressed and the disk
# will fill. The TTS venv is expected to differ (see above).
set -u

REPO=/workspace/kolkata-care-voice-agent
MARKERS=/workspace/.provision
mkdir -p "$MARKERS" /workspace/logs /workspace/bin

# host_env must exist before env.sh is sourced anywhere: it carries the port
# overrides and the uv settings that everything below depends on.
if [ ! -f /workspace/.host_env ]; then
    cp "$REPO/deploy/vast/host_env.vast.sh" /workspace/.host_env
    echo "installed /workspace/.host_env"
fi
. /workspace/.host_env

PY=3.11
TORCH_SHARED="torch==2.1.0 torchaudio==2.1.0"
TORCH_INDEX=https://download.pytorch.org/whl/cu121

# Pins shared by the two NeMo venvs. Reasons are in the header block.
COMMON_PINS='numpy==1.26.4 pyarrow==14.0.2 datasets==2.16.1 huggingface_hub==0.23.2
             transformers==4.46.3 setuptools<81 wheel
             fastapi==0.104.1 uvicorn[standard] pydantic==2.5.0 soundfile==0.12.1'

done_marker() { [ -f "$MARKERS/$1.done" ]; }
mark()        { touch "$MARKERS/$1.done"; }
step()        { echo; echo "===== $1 ====="; }

# Each stage is idempotent and resumable: a completed stage is skipped on a
# re-run, so a failure halfway through costs only the stage it died in.
run_stage() {
    local name=$1
    if done_marker "$name"; then echo "  [skip] $name (already done)"; return 0; fi
    step "$name"
    if "stage_$name"; then mark "$name"; echo "  [ok] $name"; else
        echo "  [FAIL] $name"; return 1
    fi
}

# --------------------------------------------------------------------------

stage_python() {
    uv python install "$PY" || return 1
    uv python find "$PY" >/dev/null || return 1
    echo "  python $PY available via uv (installed under $UV_PYTHON_INSTALL_DIR)"
}

stage_venv_main() {
    # bn/hi ASR + LID + the orchestrator + clinic-api all live here.
    uv venv --python "$PY" /workspace/venv -q || return 1
    uv pip install --python /workspace/venv/bin/python -q $TORCH_SHARED \
        --index-url "$TORCH_INDEX" || return 1
    # The AI4Bharat fork, not mainline: mainline cannot load the
    # IndicConformer aggregate tokenizer at all (agent/asr.py docstring).
    uv pip install --python /workspace/venv/bin/python -q \
        "nemo_toolkit[asr] @ git+https://github.com/AI4Bharat/NeMo.git@nemo-v2" || return 1
    uv pip install --python /workspace/venv/bin/python -q \
        $COMMON_PINS "pytorch-lightning==2.2.1" \
        "speechbrain==1.0.0" "sqlalchemy==2.0.23" "httpx==0.27.0" || return 1
    /workspace/venv/bin/python -c 'import nemo.collections.asr, torch
assert torch.cuda.is_available(), "no CUDA in main venv"
import numpy; torch.randn(2).numpy()' 2>/dev/null || return 1
    echo "  main venv OK (nemo fork imports, CUDA up, torch<->numpy ABI clean)"
}

stage_venv_en() {
    # Mainline NeMo for the English FastConformer. Cannot share the venv
    # above -- see english_asr_server.py's module docstring.
    uv venv --python "$PY" /workspace/venv-en-nemo -q || return 1
    uv pip install --python /workspace/venv-en-nemo/bin/python -q $TORCH_SHARED \
        --index-url "$TORCH_INDEX" || return 1
    uv pip install --python /workspace/venv-en-nemo/bin/python -q \
        Cython "setuptools<81" wheel "numpy==1.26.4" || return 1
    uv pip install --python /workspace/venv-en-nemo/bin/python -q \
        --no-build-isolation-package youtokentome \
        "nemo_toolkit[asr]==1.23.0" "pytorch-lightning==2.0.7" $COMMON_PINS || return 1
    /workspace/venv-en-nemo/bin/python -c 'import nemo.collections.asr, torch
assert torch.cuda.is_available(), "no CUDA in english venv"' 2>/dev/null || return 1
    echo "  english venv OK (mainline nemo imports, CUDA up)"
}

stage_venv_tts() {
    # The deliberate exception: its own newer torch. See the header.
    uv venv --python "$PY" /workspace/tts_venv -q || return 1
    uv pip install --python /workspace/tts_venv/bin/python -q \
        torch==2.6.0 torchaudio==2.6.0 \
        --index-url https://download.pytorch.org/whl/cu124 || return 1
    # transformers is pinned DOWN here, the opposite direction from the NeMo
    # venvs: coqui-tts imports isin_mps_friendly from transformers.pytorch_
    # utils, which transformers 5.x removed.
    uv pip install --python /workspace/tts_venv/bin/python -q \
        coqui-tts "transformers==4.57.1" \
        "fastapi==0.104.1" "uvicorn[standard]" "pydantic==2.5.0" soundfile || return 1
    /workspace/tts_venv/bin/python -c 'from TTS.utils.synthesizer import Synthesizer
import torch; assert torch.cuda.is_available(), "no CUDA in tts venv"' 2>/dev/null || return 1
    echo "  tts venv OK (coqui-tts Synthesizer imports, CUDA up)"
}

stage_ollama() {
    if [ ! -x /workspace/bin/ollama ]; then
        bash "$REPO/deploy/install_ollama.sh" >/dev/null 2>&1 || return 1
    fi
    pgrep -x ollama >/dev/null || {
        setsid /workspace/bin/ollama serve > /workspace/logs/ollama.log 2>&1 < /dev/null &
        sleep 6
    }
    /workspace/bin/ollama list 2>/dev/null | grep -q "qwen2.5:7b" \
        || /workspace/bin/ollama pull qwen2.5:7b || return 1
    echo "  ollama + qwen2.5:7b present ($(/workspace/bin/ollama --version 2>&1 | tail -1))"
}

stage_vad() {
    # v4.0 pinned: agent/vad_stream.py takes utils[0] as
    # get_speech_timestamps, which is that release's hubconf contract.
    [ -d /workspace/silero-vad/.git ] || \
        git clone -q --depth 1 --branch v4.0 \
            https://github.com/snakers4/silero-vad.git /workspace/silero-vad || return 1
    [ -f /workspace/silero-vad/hubconf.py ] || return 1
    echo "  silero-vad v4.0 at /workspace/silero-vad"
}

stage_asr() {
    . "$REPO/deploy/env.sh"
    local token=""
    [ -f /workspace/.hf_token ] && token=$(cat /workspace/.hf_token)
    # bn and hi are gated:auto on HF -- the token's account must have clicked
    # through each repo's terms page SEPARATELY. Accepting one does not
    # accept the other; an unaccepted repo answers 403 with a valid token.
    _fetch() {  # _fetch <url> <dest> <label>
        if [ -s "$2" ]; then echo "  $3 already present"; return 0; fi
        mkdir -p "$(dirname "$2")"
        if curl -fL -sS -H "Authorization: Bearer $token" "$1" -o "$2.part"; then
            mv "$2.part" "$2"; echo "  $3 downloaded ($(du -h "$2" | cut -f1))"
        else
            rm -f "$2.part"
            echo "  !! $3 FAILED -- if this is bn or hi, accept the gate at"
            echo "     https://huggingface.co/ai4bharat/indicconformer_stt_${3}_hybrid_ctc_rnnt_large"
            return 1
        fi
    }
    local rc=0
    _fetch "https://huggingface.co/ai4bharat/indicconformer_stt_bn_hybrid_ctc_rnnt_large/resolve/main/indicconformer_stt_bn_hybrid_rnnt_large.nemo" \
           "$VOICE_AGENT_NEMO_FILE_BN" bn || rc=1
    _fetch "https://huggingface.co/ai4bharat/indicconformer_stt_hi_hybrid_ctc_rnnt_large/resolve/main/indicconformer_stt_hi_hybrid_rnnt_large.nemo" \
           "$VOICE_AGENT_NEMO_FILE_HI" hi || rc=1
    # en is ungated: it downloads with no token at all.
    _fetch "https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi/resolve/main/stt_en_fastconformer_hybrid_large_streaming_multi.nemo" \
           "$VOICE_AGENT_NEMO_FILE_EN" en || rc=1
    return $rc
}

stage_tts_checkpoints() {
    . "$REPO/deploy/env.sh"
    mkdir -p "$TTS_CHECKPOINTS_ROOT/models/v1"
    cd "$TTS_CHECKPOINTS_ROOT" || return 1
    for lang in bn hi en; do
        if [ -d "$lang/fastpitch" ]; then echo "  $lang already present"; else
            # Download and extraction are 1.5 GB + 1.65 GB; the zip is
            # deleted immediately so both never coexist on a 32 GB disk.
            curl -fL -sS -o "$lang.zip" \
              "https://github.com/AI4Bharat/Indic-TTS/releases/download/v1-checkpoints-release/$lang.zip" \
              || return 1
            unzip -q -o "$lang.zip" || return 1
            rm -f "$lang.zip"
            echo "  $lang fetched and extracted"
        fi
        # The checkpoint's speaker manager resolves a path baked in at save
        # time -- "models/v1/<lang>/fastpitch/speakers.pth" -- relative to
        # the process CWD, which tts_server.py pins to CHECKPOINTS_ROOT.
        # This symlink is what makes that relative path resolve.
        [ -e "models/v1/$lang" ] || ln -s "../../$lang" "models/v1/$lang"
    done
    echo "  tts checkpoints at $TTS_CHECKPOINTS_ROOT (bn, hi, en + models/v1 aliases)"
}

stage_clinic_db() {
    . "$REPO/deploy/env.sh"
    # DATABASE_URL stays unset on purpose: clinic-api/db.py then uses sqlite
    # at /workspace/clinic.db. Postgres cannot run on this kind of mount.
    cd "$REPO/clinic-api" || return 1
    /workspace/venv/bin/python seed.py || return 1
}

stage_verify() {
    . "$REPO/deploy/env.sh"
    echo "  torch sharing (expect one inode across the two NeMo venvs):"
    stat -c "    links=%h inode=%i  %n" \
        /workspace/venv/lib/python3.11/site-packages/torch/lib/libtorch_cuda.so \
        /workspace/venv-en-nemo/lib/python3.11/site-packages/torch/lib/libtorch_cuda.so 2>/dev/null
    echo "  checkpoints:"
    for v in BN HI EN; do
        eval local p=\$VOICE_AGENT_NEMO_FILE_$v
        [ -s "$p" ] && echo "    $v OK" || echo "    $v MISSING ($p)"
    done
    echo "  disk:"; df -h / | tail -1 | sed 's/^/    /'
    echo
    echo "  Start the stack with:"
    echo "    bash $REPO/deploy/vast/start_vast.sh"
}

STAGES="python venv_main venv_en venv_tts ollama vad asr tts_checkpoints clinic_db verify"
TARGETS=${*:-$STAGES}
FAILED=""
for s in $TARGETS; do
    s=${s//-/_}
    if ! declare -F "stage_$s" >/dev/null; then
        echo "unknown stage '$s'; known: $STAGES"; exit 2
    fi
    run_stage "$s" || FAILED="$FAILED $s"
done

echo
if [ -n "$FAILED" ]; then
    echo "FAILED stages:$FAILED"
    echo "Fix the cause and re-run just those: bash $0$FAILED"
    exit 1
fi
echo "provisioning complete for:$TARGETS"
