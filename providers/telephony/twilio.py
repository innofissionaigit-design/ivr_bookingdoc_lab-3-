"""providers/telephony/twilio.py -- Twilio Media Streams adapter (bidirectional <Connect><Stream>), SERVER mode.

What this file is for
---------------------
Lets a Twilio phone number talk to the voice agent. Twilio dials INTO us: for each call it opens a WebSocket to the public wss://
address configured in the TwiML (<Connect><Stream url="wss://..."/>), then exchanges JSON messages. This adapter listens for those
connections, checks they really come from Twilio, turns the JSON/base64 messages into plain call audio for the connector (and back),
and ends or transfers the call. It uses no Twilio SDK -- only the documented Media Streams messages and, for hand-off, one REST call.

Status: written from Twilio's public documentation and unit-tested with a simulated Twilio. It has NOT been run against live Twilio.
Treat the first real call as a test (see the checklist at the bottom).

Twilio facts relied on (https://www.twilio.com/docs/voice/media-streams/websocket-messages):
  * Twilio -> us: `connected`, `start` (streamSid, callSid, accountSid, mediaFormat = audio/x-mulaw 8000 Hz mono), `media`
    (track inbound/outbound, base64 mu-law payload), `dtmf`, `mark`, `stop`.
  * us -> Twilio: `media` (base64 mu-law 8 kHz, no file header; any size, played in order), `mark` (Twilio echoes the same name when
    the audio before it has finished playing, or the buffer was cleared), `clear` (drop buffered audio).
  * closing the WebSocket ends the call; the X-Twilio-Signature header on the handshake must be validated with the Auth Token.

Configuration (read only for this provider; see telephony/config.py):
  TELEPHONY_ENDPOINT   the PUBLIC wss:// URL Twilio connects to (exactly what is in your TwiML). Used to check the signature
                       and to choose the path this server answers on. place your url here
  TELEPHONY_PASSWORD   your Twilio Auth Token (REQUIRED: calls are refused without a valid signature). place your token here
  TELEPHONY_USERNAME   your Twilio Account SID, the "AC..." one (needed for hand-off; if set, calls from other accounts are
                       refused). An "SK..." value is an API Key SID, NOT an Account SID -- it belongs in TELEPHONY_API_KEY.
  TELEPHONY_API_KEY / TELEPHONY_API_SECRET   optional Twilio API Key SID ("SK...") + its secret. Used ONLY to authenticate the
                       hand-off REST call, in place of Account SID + Auth Token, so the Auth Token need not be deployed for
                       hand-off. An API key CANNOT verify X-Twilio-Signature (Twilio signs with the Auth Token only), so it is
                       never a substitute for TELEPHONY_PASSWORD on inbound calls.
  TELEPHONY_TRANSFER_TO  E.164 number to hand a call to on handoff_human (e.g. +15551234567). Unset = hand-off unsupported.
  TELEPHONY_LISTEN_HOST / TELEPHONY_LISTEN_PORT   where this process listens (default 127.0.0.1:8200). Twilio needs wss on the
                       public internet, so put a TLS-terminating reverse proxy (or load balancer) in front and forward to this.
  TELEPHONY_TWILIO_ALLOW_UNVERIFIED   DEVELOPMENT ONLY, default off. Set to 1 to accept streams WITHOUT checking
                       X-Twilio-Signature, so a first test call can be made before the Auth Token is available. Anyone who
                       learns the URL can then talk to your agent and run up your bill. Never set it in production.

Limits, stated plainly:
  * No TwiML webhook is served: the number must already point at TwiML containing <Connect><Stream url="..."/> (a TwiML Bin or
    your own webhook). Setting that up in the Twilio console is an operator step, not done here.
  * Caller ID is not available from the stream (and the connector does not forward it). DTMF digits are ignored.
  * Hand-off uses Twilio's REST API (update call with <Dial>); without a transfer number it is unsupported and the call just ends.
  * No built-in call limit; each call is an independent connection.

First live-call checklist: (1) the handshake is accepted -- if every call is refused, the signature URL variant differs, see
`signature_candidates`; (2) caller audio reaches the agent; (3) replies are audible and `playback_done` follows the Twilio mark
echo; (4) hang-up from either side ends both; (5) hand-off dials the transfer number.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from html import escape
from http import HTTPStatus
from typing import Any

from telephony.adapter import AudioFormat, CallHandler, TelephonyCall, TelephonyProvider
from telephony.audio import CODEC_MULAW
from telephony.config import ConfigError, ProviderSettings, redact_endpoint

logger = logging.getLogger("telephony.provider.twilio")

DEFAULT_LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 8200
TWILIO_FORMAT = AudioFormat(CODEC_MULAW, 8000)  # fixed by Twilio Media Streams
MAX_MESSAGE_BYTES = 256 * 1024
MAX_OUT_CHUNK_BYTES = 16000  # 2 s of 8 kHz mu-law per outbound media message
PRELUDE_TIMEOUT_S = 10.0
PLAYOUT_SLACK_S = 5.0  # if Twilio never echoes a mark, give up waiting this long after the audio should have ended
_E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")

# (url, form fields, (user, password), timeout) -> HTTP status ; tests inject a fake, production uses urllib in a thread
HttpPost = Callable[[str, dict[str, str], tuple[str, str], float], Awaitable[int]]


# sourav: Twilio proves a request is genuine with X-Twilio-Signature = base64(HMAC-SHA1(AuthToken, full URL [+ sorted POST params])).
# A WebSocket handshake has no POST params. Flow: sign the URL string.
def twilio_signature(auth_token: str, url: str) -> str:
    digest = hmac.new(auth_token.encode(), url.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


# sourav: Twilio's docs say the handshake URL can need a trailing "/" and that the port is dropped for callbacks, and the scheme
# Twilio signs may be wss or https. Rather than guess which, accept ANY of these spellings of the configured public URL; each is
# still unforgeable without the Auth Token. Flow: build the small set of equivalent URL spellings.
def signature_candidates(endpoint: str) -> list[str]:
    parts = urllib.parse.urlsplit(endpoint)
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host
    netlocs = {parts.netloc, host}
    schemes = {parts.scheme, "https" if parts.scheme == "wss" else "http"}
    paths = {parts.path, parts.path.rstrip("/"), parts.path.rstrip("/") + "/"}
    found: list[str] = []
    for scheme in sorted(schemes):
        for netloc in sorted(netlocs):
            for path in sorted(paths):
                url = urllib.parse.urlunsplit((scheme, netloc, path, parts.query, ""))
                if url not in found:
                    found.append(url)
    return found


# sourav: the handshake gate. Flow: compare the header with every accepted URL spelling in constant time; no header -> refused.
def verify_signature(auth_token: str, endpoint: str, header: str | None) -> bool:
    if not header:
        return False
    ok = False
    for url in signature_candidates(endpoint):
        ok |= hmac.compare_digest(twilio_signature(auth_token, url), header)
    return ok


class TwilioCall(TelephonyCall):
    """One Twilio call = one Media Streams WebSocket."""

    # sourav: wrap an accepted, validated stream as a call. Flow: remember the socket, stream id, hand-off settings and the
    # exception type meaning "connection closed"; playback bookkeeping starts empty.
    def __init__(
        self,
        ws: Any,
        stream_sid: str,
        call_sid: str,
        closed_error: type[Exception],
        account_sid: str | None = None,
        auth_token: str | None = None,
        transfer_to: str | None = None,
        http_post: HttpPost | None = None,
        clock: Callable[[], float] = time.monotonic,
        api_key: str | None = None,
        api_secret: str | None = None,
    ):
        self._ws = ws
        self._stream_sid = stream_sid
        self._call_sid = call_sid
        self._closed_error = closed_error
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._api_key = api_key
        self._api_secret = api_secret
        self._transfer_to = transfer_to
        self._http_post = http_post or _default_http_post
        self._clock = clock
        self._pending: set[str] = set()  # mark names sent and not yet echoed back by Twilio
        self._marks_sent = 0
        self._expected_end = 0.0  # when all queued audio should have finished, by Twilio's real-time playback
        self._changed = asyncio.Event()
        self._closed = False
        self._stopped = False
        self.bad_messages = 0

    @property
    def audio_format(self) -> AudioFormat:
        return TWILIO_FORMAT

    # sourav: caller audio -> connector. Flow: read messages; inbound `media` -> decode base64 mu-law; `mark` -> release the
    # matching playout wait; `stop` or a closed socket -> None (caller hung up); everything else (dtmf, unknown, malformed, binary)
    # is ignored and counted, never fatal, never logged with content.
    async def read_audio(self) -> bytes | None:
        while not self._stopped:
            try:
                raw = await self._ws.recv()
            except self._closed_error:
                break
            if not isinstance(raw, str):
                self.bad_messages += 1
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                self.bad_messages += 1
                continue
            event = message.get("event") if isinstance(message, dict) else None
            if event == "media":
                media = message.get("media")
                if isinstance(media, dict) and media.get("track", "inbound") == "inbound":
                    try:
                        audio = base64.b64decode(str(media.get("payload", "")), validate=True)
                    except (binascii.Error, ValueError):
                        self.bad_messages += 1
                        continue
                    if audio:
                        return audio
            elif event == "mark":
                mark = message.get("mark")
                name = mark.get("name") if isinstance(mark, dict) else None
                if isinstance(name, str):
                    self._pending.discard(name)
                    self._changed.set()
            elif event == "stop":
                break
        self._stopped = True
        self._changed.set()
        return None

    # sourav: connector -> caller. Flow: send the audio as base64 `media` messages (Twilio buffers and plays in order), then one
    # `mark`; Twilio echoes the mark when that audio has finished playing. A dead socket is treated as the caller leaving.
    async def write_audio(self, audio: bytes) -> None:
        if self._closed or self._stopped or not audio:
            return
        try:
            for i in range(0, len(audio), MAX_OUT_CHUNK_BYTES):
                piece = audio[i : i + MAX_OUT_CHUNK_BYTES]
                await self._send(
                    {
                        "event": "media",
                        "streamSid": self._stream_sid,
                        "media": {"payload": base64.b64encode(piece).decode()},
                    }
                )
            self._marks_sent += 1
            name = f"m{self._marks_sent}"
            self._pending.add(name)
            await self._send({"event": "mark", "streamSid": self._stream_sid, "mark": {"name": name}})
        except self._closed_error:
            self._stopped = True
            self._changed.set()
            return
        self._expected_end = max(self._expected_end, self._clock()) + TWILIO_FORMAT.duration_s(len(audio))

    # sourav: "everything written has finished playing" comes from Twilio's mark echo. Flow: wait until no mark is pending, the call
    # ended, or (failsafe if an echo is lost) the audio should have ended PLAYOUT_SLACK_S ago.
    async def wait_playout(self) -> None:
        while self._pending and not self._stopped and not self._closed:
            remaining = self._expected_end + PLAYOUT_SLACK_S - self._clock()
            if remaining <= 0:
                self._pending.clear()
                return
            self._changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), min(remaining, 1.0))

    # sourav: stop_playback / interrupt. Flow: tell Twilio to drop buffered audio (it echoes pending marks), forget local waits.
    async def flush_playback(self) -> None:
        self._pending.clear()
        self._expected_end = 0.0
        self._changed.set()
        if self._closed or self._stopped:
            return
        with contextlib.suppress(Exception):
            await self._send({"event": "clear", "streamSid": self._stream_sid})

    # sourav: which credentials sign the hand-off REST call. Flow: prefer an API Key SID + secret (so the Auth Token need not be
    # deployed just for hand-off), else fall back to Account SID + Auth Token; neither available -> None and hand-off is skipped.
    # Twilio accepts either pair as HTTP Basic on the REST API; only the Auth Token can verify inbound signatures, never this.
    def _rest_auth(self) -> tuple[str, str] | None:
        if self._api_key and self._api_secret:
            return (self._api_key, self._api_secret)
        if self._account_sid and self._auth_token:
            return (self._account_sid, self._auth_token)
        return None

    # sourav: human hand-off. Flow: needs a transfer number, the Account SID (it is in the REST path) and REST credentials;
    # replace the live call's TwiML with <Dial> via the REST API (Twilio then ends the stream by itself). Anything missing or
    # failing -> False so the connector just ends the call.
    async def transfer(self, reason: str) -> bool:
        auth = self._rest_auth()
        if not (self._transfer_to and self._account_sid and auth and self._call_sid):
            logger.warning("hand-off requested but no transfer number / account credentials are configured")
            return False
        url = f"https://api.twilio.com/2010-04-01/Accounts/{urllib.parse.quote(self._account_sid)}/Calls/{urllib.parse.quote(self._call_sid)}.json"
        twiml = f"<Response><Dial>{escape(self._transfer_to)}</Dial></Response>"
        try:
            status = await self._http_post(url, {"Twiml": twiml}, auth, 10.0)
        except Exception as e:  # noqa: BLE001
            logger.error("hand-off request failed (%s)", type(e).__name__)
            return False
        if 200 <= status < 300:
            return True
        logger.error("hand-off request refused by Twilio (HTTP %d)", status)
        return False

    # sourav: end the call. Flow: idempotent; closing the stream WebSocket ends a <Connect><Stream> call.
    async def hangup(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._changed.set()
        with contextlib.suppress(Exception):
            await self._ws.close()

    # sourav: one place that serialises messages to Twilio. Flow: JSON text frame.
    async def _send(self, message: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(message, separators=(",", ":")))


# sourav: production REST call. Flow: form-encoded POST with HTTP Basic (AccountSid:AuthToken) in a worker thread; an HTTP error
# still returns its status code. The credentials only ever go in the Authorization header of this one HTTPS request.
async def _default_http_post(url: str, data: dict[str, str], auth: tuple[str, str], timeout: float) -> int:
    def run() -> int:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request = urllib.request.Request(  # noqa: S310 - https URL built above
            url, data=urllib.parse.urlencode(data).encode(), method="POST", headers={"Authorization": f"Basic {token}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return int(response.status)
        except urllib.error.HTTPError as e:
            return int(e.code)

    return await asyncio.to_thread(run)


class TwilioProvider(TelephonyProvider):
    """Listens for Twilio's Media Stream WebSocket connections and hands each validated one to the connector as a call."""

    # sourav: keep everything needed to serve. Flow: store the public URL, secrets (memory only), listen address and options.
    def __init__(
        self,
        endpoint: str,
        auth_token: str | None,
        account_sid: str | None,
        transfer_to: str | None,
        host: str,
        port: int,
        http_post: HttpPost | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        allow_unverified: bool = False,
    ):
        self._endpoint = endpoint
        self._path = urllib.parse.urlsplit(endpoint).path or "/"
        self._auth_token = auth_token
        self._account_sid = account_sid
        self._api_key = api_key
        self._api_secret = api_secret
        self._transfer_to = transfer_to
        self._host = host
        self._port = port
        self._http_post = http_post
        self._allow_unverified = allow_unverified
        self._on_call: CallHandler | None = None
        self.bound_port: int | None = None

    # sourav: the handshake gate, run before any WebSocket is accepted. Flow: wrong path -> 404; missing/invalid Twilio signature
    # -> 403 (reason never says which part failed and never echoes the header); otherwise accept. With an Auth Token configured the
    # signature is ALWAYS checked, even if the development override is on -- the override only covers having no token at all.
    def _process_request(self, connection: Any, request: Any) -> Any:
        if urllib.parse.urlsplit(request.path).path.rstrip("/") != self._path.rstrip("/"):
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        if self._auth_token is not None:
            if not verify_signature(self._auth_token, self._endpoint, request.headers.get("X-Twilio-Signature")):
                logger.warning("refused a connection: missing or invalid Twilio signature")
                return connection.respond(HTTPStatus.FORBIDDEN, "forbidden\n")
        elif not self._allow_unverified:  # create_provider forbids this combination; belt and braces
            logger.error("refused a connection: no Twilio Auth Token is configured")
            return connection.respond(HTTPStatus.FORBIDDEN, "forbidden\n")
        else:
            logger.warning(
                "accepting an UNVERIFIED stream: TELEPHONY_TWILIO_ALLOW_UNVERIFIED is set and no Auth Token is configured. "
                "Anyone who knows this URL can talk to the agent. Development only."
            )
        return None

    # sourav: read Twilio's opening messages. Flow: skip `connected`, wait (bounded) for `start`, check the media format is the
    # documented mu-law 8 kHz mono and (if configured) the account matches; return the ids or None to refuse.
    async def _read_start(self, ws: Any, closed_error: type[Exception]) -> tuple[str, str] | None:
        deadline = time.monotonic() + PRELUDE_TIMEOUT_S
        for _ in range(8):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                raw = await asyncio.wait_for(ws.recv(), remaining)
                message = json.loads(raw) if isinstance(raw, str) else None
            except (TimeoutError, ValueError, closed_error):
                return None
            if not isinstance(message, dict):
                continue
            if message.get("event") != "start":
                continue
            start = message.get("start")
            if not isinstance(start, dict):
                return None
            fmt = start.get("mediaFormat") or {}
            if fmt.get("encoding") != "audio/x-mulaw" or fmt.get("sampleRate") != 8000 or fmt.get("channels", 1) != 1:
                logger.error("refused a stream: unexpected media format")
                return None
            if self._account_sid and start.get("accountSid") != self._account_sid:
                logger.error("refused a stream: it belongs to a different Twilio account")
                return None
            stream_sid = start.get("streamSid") or message.get("streamSid")
            call_sid = start.get("callSid") or ""
            if not isinstance(stream_sid, str) or not stream_sid or not isinstance(call_sid, str):
                return None
            return stream_sid, call_sid
        return None

    # sourav: one Twilio connection = one call. Flow: read the start message, build the call, give it to the connector until it
    # ends, always hang up. Each connection runs in its own task (websockets does this), so calls are independent.
    async def _handle(self, ws: Any) -> None:
        from websockets.exceptions import ConnectionClosed

        assert self._on_call is not None
        ids = await self._read_start(ws, ConnectionClosed)
        if ids is None:
            await ws.close(code=1008)
            return
        call = TwilioCall(
            ws,
            ids[0],
            ids[1],
            ConnectionClosed,
            self._account_sid,
            self._auth_token,
            self._transfer_to,
            self._http_post,
            api_key=self._api_key,
            api_secret=self._api_secret,
        )
        try:
            await self._on_call(call)
        finally:
            await call.hangup()

    # sourav: the provider side of the contract. Flow: start the WebSocket server (lazy import), serve until cancelled.
    async def serve(self, on_call: CallHandler) -> None:
        from websockets.asyncio.server import serve

        self._on_call = on_call
        async with serve(
            self._handle, self._host, self._port, process_request=self._process_request, max_size=MAX_MESSAGE_BYTES
        ) as server:
            self.bound_port = server.sockets[0].getsockname()[1]
            logger.info(
                "listening on %s:%d for Twilio streams (public URL %s)",
                self._host,
                self.bound_port,
                redact_endpoint(self._endpoint),
            )
            await asyncio.get_running_loop().create_future()


# sourav: the two Twilio identifier prefixes operators mix up. An Account SID and an API Key SID are both "SID"s and both 34
# characters, so pasting one into the other's variable is the single most likely configuration mistake; name it in the error.
_ACCOUNT_SID = re.compile(r"^AC[0-9a-fA-F]{32}$")
_API_KEY_SID = re.compile(r"^SK[0-9a-fA-F]{32}$")


# sourav: read this provider's own env flags. Flow: provider-specific variables never belong in the shared config loader, so
# twilio reads them here (injectable for tests); blank and "place your ..." placeholders count as unset, as elsewhere.
def _flag(env: Mapping[str, str], name: str) -> bool:
    raw = (env.get(name) or "").strip().lower()
    return raw in ("1", "true", "yes")


# sourav: the hook telephony/loader.py calls. Flow: require the public wss URL; require the Auth Token UNLESS the operator has
# explicitly set the development override; keep an API key pair together; reject an Account SID / API Key SID mix-up by name;
# validate the optional transfer number, fill listen defaults. No network access here.
def create_provider(
    settings: ProviderSettings,
    http_post: HttpPost | None = None,
    env: Mapping[str, str] | None = None,
) -> TelephonyProvider:
    env = os.environ if env is None else env
    allow_unverified = _flag(env, "TELEPHONY_TWILIO_ALLOW_UNVERIFIED")
    if settings.endpoint is None:
        raise ConfigError(
            "the twilio provider needs TELEPHONY_ENDPOINT = the public wss:// URL that your TwiML <Stream> points at"
        )
    parts = urllib.parse.urlsplit(settings.endpoint)
    if parts.scheme.lower() != "wss" or not parts.hostname:
        raise ConfigError(
            "TELEPHONY_ENDPOINT for twilio must be the public wss:// URL Twilio connects to (Twilio requires wss)"
        )
    if settings.username is not None and _API_KEY_SID.match(settings.username):
        raise ConfigError(
            "TELEPHONY_USERNAME holds an API Key SID (SK...). Twilio's Account SID starts with AC... -- copy that one from the "
            "console instead, and put the SK... value in TELEPHONY_API_KEY with its secret in TELEPHONY_API_SECRET"
        )
    if settings.username is not None and not _ACCOUNT_SID.match(settings.username):
        raise ConfigError(
            "TELEPHONY_USERNAME for twilio must be your Account SID: 'AC' followed by 32 hex characters (Twilio console home)"
        )
    if settings.api_key is not None and _ACCOUNT_SID.match(settings.api_key):
        raise ConfigError(
            "TELEPHONY_API_KEY holds an Account SID (AC...). An API Key SID starts with SK... -- create one under Account > API "
            "keys & tokens, or leave TELEPHONY_API_KEY unset and use the Auth Token"
        )
    if (settings.api_key is None) != (settings.api_secret is None):
        raise ConfigError(
            "TELEPHONY_API_KEY and TELEPHONY_API_SECRET go together: set both (API Key SID + its secret) or neither"
        )
    if settings.password is None:
        if not allow_unverified:
            extra = ""
            if settings.api_key is not None:
                extra = (
                    " An API Key secret cannot do this: Twilio signs inbound requests with the Auth Token only, so the API key "
                    "authenticates hand-off REST calls but never verifies a call."
                )
            raise ConfigError(
                "the twilio provider needs TELEPHONY_PASSWORD = your Twilio Auth Token (used to verify every call)." + extra
                + " For a first test call without it, set TELEPHONY_TWILIO_ALLOW_UNVERIFIED=1 -- development only, it lets "
                "anyone who knows the URL talk to your agent."
            )
        logger.warning(
            "starting WITHOUT signature verification: no TELEPHONY_PASSWORD (Auth Token) and "
            "TELEPHONY_TWILIO_ALLOW_UNVERIFIED=1. Every caller is trusted. Development only -- set the Auth Token before "
            "this is reachable by anyone else."
        )
    if settings.transfer_to is not None:
        if not _E164.match(settings.transfer_to):
            raise ConfigError("TELEPHONY_TRANSFER_TO must be an E.164 number such as +15551234567")
        if settings.username is None:
            raise ConfigError("hand-off needs TELEPHONY_USERNAME = your Twilio Account SID")
        if settings.password is None and settings.api_key is None:
            raise ConfigError(
                "hand-off needs REST credentials: either TELEPHONY_PASSWORD (Auth Token) or "
                "TELEPHONY_API_KEY + TELEPHONY_API_SECRET"
            )
    return TwilioProvider(
        settings.endpoint,
        settings.password,
        settings.username,
        settings.transfer_to,
        settings.listen_host or DEFAULT_LISTEN_HOST,
        settings.listen_port if settings.listen_port is not None else DEFAULT_LISTEN_PORT,
        http_post,
        api_key=settings.api_key,
        api_secret=settings.api_secret,
        allow_unverified=allow_unverified,
    )
