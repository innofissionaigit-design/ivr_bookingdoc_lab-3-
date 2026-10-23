# telephony/ -- provider-independent phone connector

**What this is for:** lets a phone call reach the existing voice agent without changing it. The connector is one more client of
`main_pcm.py`'s `/ws/audio` (the same protocol the browser page and `scripts/e2e_call.py` use). `agent/`, `main.py` and
`main_pcm.py` are untouched. **No real telephony provider is implemented or tested here** -- only the interface, the connector
and an offline test adapter.

## Architecture

```
Client Telephony
      ↓
MASTER TELEPHONY RESOLVER     telephony/loader.py  (python -m telephony)
      ↓
+-----------+-----------+-----------+-----------+
| WebSocket |    SIP    |  Twilio   |  WAV test |   providers/telephony/<name>.py  (one small adapter each)
|  adapter  |   stub    |  adapter  |  adapter  |
+-----------+-----------+-----------+-----------+
      ↓
Telephony Connector           telephony/connector.py   (provider-independent, one session per call)
      ↓
/ws/audio                     ws://127.0.0.1:8101/ws/audio
      ↓
main_pcm.py
      ↓
Existing AI
```

Rule: provider-specific logic lives in `providers/telephony/`; provider-independent logic lives in `telephony/`. The agent and
`main_pcm.py` know nothing about the client's telephony system. Adding a compatible provider never changes the connector or the AI.

| File | Role |
|---|---|
| `telephony/__main__.py` | the ONE entry point: config -> resolve provider -> validate -> run connector -> clean shutdown |
| `telephony/loader.py` | master resolver: explicit name or safe auto-detection, using the registry |
| `providers/telephony/registry.py` | the table of known adapters (+ which URL schemes may be auto-detected) |
| `telephony/adapter.py` | `TelephonyProvider` / `TelephonyCall` interface (telephony operations only) |
| `telephony/connector.py` | per-call session, `/ws/audio` protocol, playback order, `playback_done`, interrupt, hand-off |
| `telephony/audio.py` | WAV parsing, PCM16, resampling, G.711 mu-law/A-law, continuous-stream gap filling |
| `telephony/agent_link.py` | the WebSocket to `/ws/audio` (swappable for tests) |
| `telephony/config.py` | env config, endpoint redaction for logs/errors |
| `providers/telephony/websocket.py` | generic client WebSocket media adapter (**implemented**) |
| `providers/telephony/wavfile.py` | offline test adapter (**implemented**) |
| `providers/telephony/twilio.py` | Twilio Media Streams adapter, server mode (**implemented from Twilio's docs, simulated-Twilio tests only; not yet run against live Twilio**) |
| `providers/telephony/sip.py`, `custom_http.py` | reserved slots, **not implemented** (fail with a clear error) |

## What the master connector does

`python -m telephony` loads the configuration, resolves the provider, lets the provider validate its own settings **before any call
is accepted**, then starts it. For every call the provider hands over, the connector opens its own `/ws/audio` WebSocket, sends
exactly `{"type":"hello","sampleRate":16000,"format":"pcm_s16le","channels":1}`, streams continuous mono PCM16LE 16 kHz, parses
each WAV clip's header (the TTS rate is never assumed), converts to the adapter's declared format, plays clips in order, sends
`playback_done` once after the last clip has played, and handles `stop_playback`, `interrupt`, `handoff_human` and socket close.
Sessions share no state and no call limit is built in. SIGINT/SIGTERM end live calls cleanly.

## What provider adapters do

Everything specific to the outside system: connecting or accepting calls, authentication, receiving caller audio, sending audio
back, hangup, optional transfer and caller ID, and declaring the audio format (`pcm_s16le`, `mulaw`, `alaw` + sample rate). An
adapter knows nothing about `/ws/audio` or agent messages. Provider-side authentication/signature checks belong here.

## What is automatically detected, and what is not

`TELEPHONY_PROVIDER=auto` looks only at the scheme of `TELEPHONY_ENDPOINT`:

| Endpoint | Result |
|---|---|
| `ws://...`, `wss://...` | `websocket` adapter |
| `sip:...`, `sips:...` | `sip` -- recognised, but **no SIP transport is implemented** (clear error) |
| `http://...`, `https://...` | **refused**: could be REST, a webhook, a vendor API... set `TELEPHONY_PROVIDER` explicitly |
| anything else / no scheme / missing | refused with an explanation |

Not detectable from a URL: the protocol behind an HTTP(S) URL, any vendor platform (e.g. a cloud voice service has no URL scheme of
its own, so it is **never** auto-detected), the audio codec/rate/framing, how calls arrive, authentication scheme, transfer
mechanism. Nothing is guessed; the client has to say.

## Smart probe (optional): "what does this URL actually speak?"

A URL alone cannot tell us the protocol, so nothing is ever redirected blindly. If you want the system to *look* first, switch the
probe on. It is **off by default** because it contacts the client's system (opening a WebSocket can start a session on some
platforms) -- get the client's agreement first.

```
TELEPHONY_PROVIDER=auto
TELEPHONY_AUTO_PROBE=1
TELEPHONY_ENDPOINT=wss://client.example.com/media     # place your url here
python -m telephony            # probes, then starts if the verdict allows it
python -m telephony --probe    # only prints the verdict, starts no calls
```

| URL | What the probe does | Verdict |
|---|---|---|
| `ws://`, `wss://` | opens the handshake, waits ~4 s for the server's first message (we send nothing) | binary frame = `raw_audio` (fits the websocket adapter); JSON with an `event` field = `json_envelope` (**start is refused**: a vendor-style stream, needs its own adapter); other text = `text_other`; nothing = `silent` (inconclusive, many servers wait for us); 401/403 = `auth_required`; no connection = `unreachable` |
| `http://`, `https://` | one `HEAD` request, redirects not followed | status code + content type only; **never selects a provider** (still needs `TELEPHONY_PROVIDER` set explicitly) |
| `sip:`, `sips:` | not probed (no SIP stack here) | `not_probed` |

What the probe will not do: prove a call will work, discover the audio codec/rate, detect a vendor platform from an https URL, or
choose a provider it cannot justify. Message contents are never logged or stored; only the verdict, the HTTP status and, for a
JSON first message, a short event name. Credentials are never sent over unencrypted URLs to a public host.

## Twilio adapter (`TELEPHONY_PROVIDER=twilio`)

Twilio **dials into us**: your number's TwiML must contain `<Connect><Stream url="wss://your-public-host/twilio/media"/></Connect>`
(a TwiML Bin or your own webhook; setting that up in the Twilio console is an operator step -- this repo serves no webhook).
Twilio then opens a WebSocket to that address for each call. The adapter listens for those connections, **refuses any whose
`X-Twilio-Signature` is not valid for your Auth Token**, converts Twilio's JSON/base64 mu-law 8 kHz messages to call audio and back,
uses Twilio's `mark` echo to know when audio has really finished playing, uses `clear` for stop/interrupt, and closes the stream to
end the call.

```
Caller -> Twilio number -> Twilio cloud
                              | wss://voice.example.com/twilio/media   (public, TLS)
                              v
                     reverse proxy / load balancer (terminates TLS)
                              | ws://127.0.0.1:8200/twilio/media        (private hop)
                              v
              python -m telephony  (twilio adapter, checks signature)  ->  connector  ->  /ws/audio (private)  ->  AI
```

```
TELEPHONY_PROVIDER=twilio
TELEPHONY_ENDPOINT=wss://voice.example.com/twilio/media    # the PUBLIC url in your TwiML. place your url here
TELEPHONY_PASSWORD=place your token here                   # Twilio Auth Token -- required; verifies every call
TELEPHONY_USERNAME=place your username here                # Account SID (AC...) -- needed for hand-off; if set, other
                                                           #   accounts' streams are refused
TELEPHONY_API_KEY=place your key here                      # optional API Key SID (SK...) + secret: authenticates the
TELEPHONY_API_SECRET=place your secret here                #   hand-off REST call instead of the Auth Token
TELEPHONY_TRANSFER_TO=+15551234567                         # optional: human hand-off number (needs the Account SID too)
TELEPHONY_LISTEN_HOST=127.0.0.1                            # default; keep private, expose only through the proxy
TELEPHONY_LISTEN_PORT=8200                                 # default
```

### The two credential pairs are not interchangeable

Twilio issues two pairs that both look like "a SID and a secret", and both SIDs are 34 characters:

| Pair | Looks like | Can verify an inbound call? | Can authenticate a REST call? |
|---|---|---|---|
| Account SID + Auth Token | `AC` + 32 hex, token on the console home page | **yes -- only this one** | yes |
| API Key SID + secret | `SK` + 32 hex, from Account > API keys & tokens | **no** | yes |

`X-Twilio-Signature` is an HMAC over the URL keyed with the **Auth Token**, so an API key can never stand in for it: without the
Auth Token there is no way to tell a real Twilio connection from anyone else who guessed the URL. The API key is still useful --
set `TELEPHONY_API_KEY` / `TELEPHONY_API_SECRET` and the hand-off REST call uses it, so the Auth Token need not be deployed just
to enable hand-off. Pasting an `SK...` value into `TELEPHONY_USERNAME`, or an `AC...` value into `TELEPHONY_API_KEY`, is refused
at startup with a message naming the mistake.

### First test call before you have the Auth Token (development only)

```
TELEPHONY_TWILIO_ALLOW_UNVERIFIED=1
```

Accepts incoming streams **without** checking the signature, so an end-to-end call can be proven while the Auth Token is being
fetched. It is off by default, it is announced with a warning at startup and on every accepted call, and it is **ignored when
`TELEPHONY_PASSWORD` is set** -- an Auth Token always means signatures are checked. While it is on, anyone who learns the URL can
talk to the agent and spend your Twilio balance: use a short-lived tunnel and turn it off as soon as the token is in place.

### Checking a setup before calling it

```
source deploy/telephony-twilio.env.sh      # fill this in first; it holds real secrets and is gitignored
python scripts/twilio_preflight.py         # exit 0 = ready; lists every blocker and prints the TwiML to paste
python -m telephony
```

`scripts/twilio_preflight.py` contacts Twilio never. It validates the configuration, confirms the listen port is free and that
`main_pcm.py` is actually answering on `/ws/audio` (a call that connects to a stopped agent just hears silence), and prints the
exact `<Connect><Stream>` TwiML for whatever `TELEPHONY_ENDPOINT` is set to -- a mismatch between the two is the most common
cause of every call being refused with 403. It reports secrets as present/absent and by length, never by value.

Limits: no caller ID, no DTMF handling, hand-off only via the configured number (Twilio REST call update), no built-in call limit.
**Untested against live Twilio**: the unit tests simulate Twilio from its public documentation. Do a first test call and check:
handshake accepted (if every call is refused, the signature URL spelling differs -- see `signature_candidates` in the adapter),
caller audio reaches the agent, replies are audible, hang-up works from both sides, hand-off dials the number.

## Example configuration

```
# client gave us a WebSocket media endpoint
TELEPHONY_PROVIDER=websocket
TELEPHONY_ENDPOINT=wss://client.example.com/media        # place your url here
TELEPHONY_API_KEY=place your key here                    # optional: sent as "Authorization: Bearer <key>"
TELEPHONY_AUDIO_CODEC=mulaw                              # confirm with the client (default pcm_s16le)
TELEPHONY_AUDIO_SAMPLE_RATE=8000                         # confirm with the client (default 16000)

# same thing, detected from the URL
TELEPHONY_PROVIDER=auto
TELEPHONY_ENDPOINT=wss://client.example.com/media

# offline, no provider at all
TELEPHONY_PROVIDER=wavfile
TELEPHONY_ENDPOINT=/path/to/question.wav
```

SIP (`TELEPHONY_PROVIDER=sip`, `TELEPHONY_ENDPOINT=sip:...`, `TELEPHONY_USERNAME`, `TELEPHONY_PASSWORD`) is a **reserved name only**:
the exact SIP settings are not defined until the client's SIP setup is known.

## Generic WebSocket adapter: the contract it supports

One WebSocket connection = one call, and this process connects to the client's endpoint. Binary frames from the endpoint are the
caller's audio; binary frames we send are the reply audio; both in one mono format (`TELEPHONY_AUDIO_CODEC` /
`TELEPHONY_AUDIO_SAMPLE_RATE`). Text frames are ignored; the endpoint closing the socket means the caller hung up. Auth: API key as
Bearer, or username+password as Basic; credentials are refused over `ws://` to a non-private host. It does **not** understand any
vendor message format, so an endpoint that wraps audio in JSON/base64 envelopes needs its own adapter. Limits: no transfer, no
caller ID, no DTMF; playout is estimated (audio is paced in real time) because the endpoint sends no "finished playing" signal;
one start serves one call.

## Run locally

```
# terminal 1: the agent as usual (main_pcm.py on :8101)
# terminal 2: the single entry point
python -m telephony            # with TELEPHONY_PROVIDER etc. set in the environment / .env
# example without any provider: play a recorded question and save the answer as question.reply.wav
TELEPHONY_PROVIDER=wavfile TELEPHONY_ENDPOINT=/path/to/question.wav python -m telephony
```

Exit code 2 = configuration problem (the message says what to fix and never prints credentials).

## Test without a provider

```
python -m pytest tests/telephony -q
```

Fakes for the call and the agent socket, plus in-process `websockets` servers. Note: CI shards only run `tests/test_*.py`, so add
`tests/telephony` to the CI test command if you want these in CI.

## How to add a new provider

1. Write `providers/telephony/<name>.py` with `create_provider(settings) -> TelephonyProvider` (validate its own settings there; see
   `websocket.py` as the worked example): a `TelephonyProvider.serve()` that calls `on_call(call)` per call, and a `TelephonyCall`.
2. Add one line to `PROVIDERS` in `providers/telephony/registry.py` (and, only if the URL scheme alone is unambiguous, one to
   `SCHEME_TO_PROVIDER`).
3. Add tests. Do not touch `telephony/`, `agent/`, `main.py` or `main_pcm.py`.

## Information required from client

Ask the client for all of the following before a real adapter can be written or configured:

- provider / platform name
- endpoint / URL
- protocol (WebSocket, SIP/RTP, REST/webhook, vendor media-stream, ...)
- authentication method
- credentials (provided securely; never committed)
- audio codec
- sample rate
- audio framing (raw frames, packet size, JSON/base64 envelopes, ...)
- inbound call mechanism (how a call reaches us)
- outbound audio mechanism (how reply audio goes back)
- hangup behaviour
- transfer / human hand-off
- caller ID requirements
- DTMF requirements
- expected concurrent calls

## Behaviour to know

- **Hand-off:** on `handoff_human` the connector waits for the notice to finish, then calls the adapter's `transfer(reason)`.
  If the adapter does not support it, it logs "cannot transfer" and ends the call. No SIP transfer is invented.
- **Caller ID:** not forwarded. The `hello` message must stay exact, and the agent's `hello` handler has no such field.
  Adding it needs a change to `main.py` and `tools/make_pcm_variant.py`.
- **DTMF/keypad:** not part of the protocol; only the optional `wait_interrupt` hook exists.
- **Logs** never contain transcripts, caller numbers or exception text.

## Security

`/ws/audio` has **no authentication**. Do not expose it publicly. The connector refuses non-private `VOICE_PCM_HOST` values
unless `TELEPHONY_ALLOW_REMOTE_AGENT=1`; that is a guard against mistakes, not authentication. Before exposing it beyond the
host/private network, add authentication (token or mTLS plus a TLS-terminating proxy) on `/ws/audio`. Verifying the caller's
provider (signatures, credentials) is the adapter's job.

The provider-facing side may be public if a provider requires it; the internal agent WebSocket stays private. API keys, secrets,
passwords, authorization headers and tokens are never logged, and endpoints in errors/logs are shown as `scheme://host[:port]/...`.
Keep real credentials in the environment or a secrets manager, never in source or `.env.example`.
