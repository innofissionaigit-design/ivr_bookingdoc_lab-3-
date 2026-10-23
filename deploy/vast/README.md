# Running this stack on a Vast.ai instance

Verified on instance `C.54484067` (RTX 4090 24 GB, Ubuntu 24.04, 32 GB disk)
on 2026-10-06: all six services up, Bengali call answered end to end at a
**245 ms** turn (`lid 4 ms | asr 180 ms | reply 59 ms`).

Three files do the work:

| File | Job |
|---|---|
| `podlink.py` | Push the working tree from a laptop; run commands on the pod |
| `provision.sh` | Build the stack from scratch (staged, idempotent) |
| `start_vast.sh` | Bring the stack up (the one entrypoint) |
| `host_env.vast.sh` | Platform overrides, installed as `/workspace/.host_env` |

## First-time setup

```bash
cp deploy/vast/pod.env.example deploy/vast/pod.env   # then fill it in
python deploy/vast/podlink.py push
python deploy/vast/podlink.py run 'bash /workspace/kolkata-care-voice-agent/deploy/vast/provision.sh'
python deploy/vast/podlink.py run 'bash /workspace/kolkata-care-voice-agent/deploy/vast/start_vast.sh'
```

`provision.sh` needs `/workspace/.hf_token` to exist first (mode 600, no
trailing newline) for the two gated Bengali/Hindi checkpoints.

## Keeping the pod in sync with a laptop

```bash
python deploy/vast/podlink.py watch
```

Pushes on every save, prunes files deleted locally, and honours
`.gitignore`. One-way by design: the laptop is the source of truth, so a pod
edit is overwritten on the next push rather than silently merged. It runs
over the Jupyter token, so it works before SSH is set up.

`push` is the same thing once, on demand.

## Testing it

### In a browser (the mic works) — SSH local forward

Both entrypoints serve a browser client at `/`. Open one over an SSH
forward, **not** over the public port:

```bash
ssh -i ~/.ssh/vast_kolkata -p 22177 \
    -L 10100:localhost:10100 -L 10200:localhost:10200 \
    root@194.14.47.19
```

Leave that open and visit:

| URL | Client |
|---|---|
| http://localhost:10100/ | PCM / telephony transport (`static/pcm/`) |
| http://localhost:10200/ | WebM browser prototype (`static/`) |

The forward is what makes this work at all, for a reason that is easy to
lose an hour to: both clients call `getUserMedia`, which browsers refuse on
an insecure origin — so the page loads fine over
`http://194.14.47.19:22154/` and the microphone is silently blocked.
`localhost` is treated as a **secure origin** regardless of scheme, so the
mic works through the forward with no TLS, no tunnel and no certificate.
Both clients build their WebSocket URL from `window.location`, so they
follow the forward with no edit.

It is also the safer option: nothing new is exposed, and the public ports
stay unauthenticated-but-unused.

### Headless, no browser

```bash
# synthesize a caller utterance with the live TTS, then drive a real call
curl -sS -X POST http://localhost:8002/synthesize \
  -H 'Content-Type: application/json' \
  -d '{"text": "রক্ত পরীক্ষার দাম কত?", "lang": "bn"}' -o /tmp/q.wav
ffmpeg -y -i /tmp/q.wav -ar 16000 -ac 1 -sample_fmt s16 /tmp/caller.wav
/workspace/venv/bin/python scripts/e2e_call.py --port 10100 /tmp/caller.wav
```

A healthy run looks like this (measured 2026-10-06):

```
heard: রক্ত পরীক্ষার দাম কত
reply: আমাদের এখানে রক্তের যে পরীক্ষাগুলো হয় তা হল Blood Grouping & Rh Typing, ...
latency_s: 1.81
reply_audio_bytes: 2674082
```

`reply_audio_bytes` in the hundreds of thousands rather than the millions
usually means the reply was **blocked** and you are hearing the system-busy
fallback — check the log for `unspeakable spans` before suspecting ASR.

### A public URL

Not set up, deliberately. A public ingress tunnel (ngrok, cloudflared)
would put an **unauthenticated** `/ws/audio` that reaches patient data on
the open internet; see the next section. If you want one anyway, add auth
first.

## Ports

Vast allocates external ports at instance creation and **none can be added
later**. The container-side numbers are not the public ones.

| Service | Container | Public | Notes |
|---|---|---|---|
| voice-pcm (`main_pcm.py`) | 10100 | **22123** | telephony path |
| voice-agent (`main.py`) | 10200 | **22154** | browser prototype |
| clinic-api | 8081 | — | loopback; **not** 8080 |
| tts | 8002 | — | loopback |
| english-asr | 8003 | — | loopback |
| ollama | 11434 | — | loopback |
| *(platform)* Jupyter | 8080 | 22174 | **owns 8080** |
| *(platform)* SSH | 22 | 22177 | |

Two consequences worth knowing before changing anything:

- **The clinic API cannot use its default 8080** — the platform's own
  Jupyter is already bound there. Hence `CLINIC_API_PORT=8081`.
- **Only 10100 and 10200 are NAT'd to a public port**, so the two
  orchestrator entrypoints must bind those rather than 8100/8101. This is
  why `deploy/env.sh` now takes every port from a variable.

## Security: the public ports are unauthenticated

Both voice endpoints answer `/api/health` with **HTTP 200 and no token**.
They are raw TCP forwards, deliberately *not* behind Vast's Caddy auth edge
— which is what lets the `/ws/audio` WebSocket upgrade through unchanged,
and also what leaves them open to the internet.

That is acceptable for synthetic test audio and not acceptable for anything
else: `/ws/audio` reaches the clinic API, and `admission_open:true` means
any caller is admitted. Before this instance sees a real call, pick one:

- put the ports behind the Caddy edge (a `/etc/portal.yaml` entry) and pass
  `?token=` on the WebSocket URL;
- set `CLINIC_API_TOKEN` via `/workspace/.clinic_api_token` *and*
  `CLINIC_API_REQUIRE_TOKEN=1`, so the data path refuses unauthenticated
  requests even if the socket is reachable;
- or stop the public entrypoints and drive the stack only over SSH.

## What survives a restart

`vast-capabilities | jq '.instance.workspace_is_volume'` → **`false`** on
this instance. So, unlike the RunPod assumption baked into `deploy/env.sh`
and `deploy/bootstrap_pod.sh`:

- **stop/start** preserves the entire container filesystem, including
  `/workspace`. Nothing to rebuild; just run `start_vast.sh`.
- **recycle/destroy** wipes everything. Re-run `provision.sh` (~20 GB of
  downloads). The *code* is never at risk — the laptop is its source of
  truth — but the models, `clinic.db`, and the token files are.

## Disk is the binding constraint

32 GB total, and `/workspace` is the same overlay as `/`. It fits only
because the two NeMo venvs **hardlink** one torch rather than copying it:

```
3 venvs sharing torch   3.9 GB     link count 4
3 venvs copying torch  11.7 GB     measured, not estimated
```

This needs the uv settings in `host_env.vast.sh`, because the Vast image
pins `UV_NO_CACHE=1` and `UV_LINK_MODE=copy` in `/etc/environment` and a
**login shell re-applies them over anything you export**. Check the dedupe
is still holding with:

```bash
stat -c "links=%h inode=%i" /workspace/{venv,venv-en-nemo}/lib/python3.11/site-packages/torch/lib/libtorch_cuda.so
```

Same inode = fine. Different inodes = the dedupe regressed and the disk will
fill. `tts_venv` is *expected* to differ; it has its own torch 2.6.0 for the
reason given in `provision.sh`'s header.

Rough budget as built: venvs + shared torch ~9 GB, qwen2.5:7b 4.7 GB, ollama
runtime 2.1 GB, ASR checkpoints 1.4 GB, TTS checkpoints 5 GB, leaving ~7 GB.

## Known-good state at hand-over

| Component | Status |
|---|---|
| Bengali ASR | works — loads 12.3 s, 0.55 GB VRAM, verified via `TurnASR` |
| Hindi ASR | works — loads 16.7 s |
| English ASR | own venv, mainline NeMo, `{"status":"ok","device":"cuda"}` |
| LID | works — Bengali at 1.00 confidence, early-LID path used live |
| TTS (bn/hi/en) | works — 0.37 s cold / 0.05 s warm for a 5 s utterance |
| qwen2.5:7b | resident, 100 % GPU, `KEEP_ALIVE=-1` honoured |
| clinic-api | seeded: 8 departments, 32 doctors, 34 lab tests |
| GPU at idle | 13.1 GB of 24.5 GB used |

Two things are **not** verified, and both are pre-existing, not artifacts of
this port:

1. **ASR accuracy.** The checkpoints load and decode; nothing here measures
   word error rate. `docs/adr/0001` section 8 defines what "verified" means
   and the English FastConformer decoding strategy in particular is flagged
   there as unconfirmed.
2. **The pronunciation gate blocks real replies.** A live Bengali
   "blood test price" query was understood, looked up correctly, and then
   **blocked** by `agent/tts.py`'s `UnspeakableTextError`:
   `unspeakable spans ['Grouping', 'Rh Typing', 'Sugar Fasting', ...]`.
   That is exactly what `deploy/env.sh` documents for
   `PRONUNCIATION_ALLOW_DRAFT=0`, and the caller hears the system-busy
   fallback. The way out is the native-speaker review of
   `agent/pronunciation.py`'s lexicon, not a default change.

## Gotcha that will cost you an hour

`speechbrain` 1.1.x fails at **inference**, not at load, on torch 2.1
(`torch.amp.custom_fwd` arrived in torch 2.4). While it was installed,
`/api/health` cheerfully reported `lid_loaded:true` and every single turn
logged `LID failed -- treating as unknown` and fell back to dual ASR. The
health endpoint is not evidence that LID works; the turn log is. Pinned to
`speechbrain==1.0.0`.
