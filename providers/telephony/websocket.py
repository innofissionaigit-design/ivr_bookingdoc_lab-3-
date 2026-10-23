"""providers/telephony/websocket.py -- generic adapter for a client-provided WebSocket media endpoint.

What this file is for
---------------------
Some client telephony platforms expose a plain WebSocket that carries one call's audio. This adapter connects to it as a CLIENT
and presents it to the connector as one TelephonyCall. It is NOT the agent's /ws/audio protocol (that stays inside
telephony/connector.py) and it does NOT understand any vendor's message format (no Twilio/Telnyx/... JSON envelopes).

The wire contract it supports -- if the client's endpoint does not match, a different adapter is needed (nothing is guessed):

  * one WebSocket connection = one call; this process connects to TELEPHONY_ENDPOINT (ws:// or wss://)
  * BINARY frames from the endpoint = the caller's audio; BINARY frames we send = audio for the caller
  * both directions use ONE mono format: TELEPHONY_AUDIO_CODEC (pcm_s16le | mulaw | alaw, default pcm_s16le) at
    TELEPHONY_AUDIO_SAMPLE_RATE (default 16000, i.e. the agent's own format, so no conversion). The defaults are a convenience,
    not knowledge of the client's system -- confirm them with the client. The chosen format is logged at start-up.
  * TEXT frames from the endpoint are ignored (never logged: they may carry personal data); we send none
  * the endpoint closing the socket = the caller hung up; hangup() closes the socket
  * authentication, if any: TELEPHONY_API_KEY is sent as `Authorization: Bearer <key>`, or TELEPHONY_USERNAME +
    TELEPHONY_PASSWORD as HTTP Basic. Credentials are refused over unencrypted ws:// to a non-private host. Anything else
    (signed URLs, custom headers, token-in-first-message) needs its own adapter. A token inside the URL query works because the
    URL is used as given -- and is redacted in logs and errors.

Honest limits, not hidden:
  * transfer / human hand-off is NOT supported (returns False): the connector then ends the call.
  * the endpoint gives no "finished playing" signal, so playout is ESTIMATED: audio is sent in real time (20 ms chunks, 60 ms
    ahead) and is assumed to play as it is sent. stop_playback/interrupt drop what has not been sent yet; the last ~60 ms already
    on the wire cannot be recalled.
  * caller ID is not available; DTMF is not handled.
  * one start = one call: serve() connects once and returns when that call ends (restart the process, or run several, for more).

Endpoint placeholder: place your url here
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from telephony.adapter import AudioFormat, CallHandler, TelephonyCall, TelephonyProvider
from telephony.audio import CODEC_PCM_S16LE
from telephony.config import ConfigError, ProviderSettings, is_private_host, redact_endpoint

logger = logging.getLogger("telephony.provider.websocket")

DEFAULT_CODEC = CODEC_PCM_S16LE
DEFAULT_SAMPLE_RATE = 16000
CHUNK_S = 0.02  # outbound packet interval
LEAD_S = 0.06  # how far ahead of real time chunks may be sent (jitter cushion)
MAX_FRAME_BYTES = 1024 * 1024
CONNECT_TIMEOUT_S = 10.0

# (url, headers) -> an open connection exposing send/recv/close. Tests inject a fake; production uses `websockets`.
Connect = Callable[[str, dict[str, str]], Awaitable[Any]]


class WebSocketCall(TelephonyCall):
    """One call over one WebSocket. Reads binary frames, paces replies out in real time, estimates playout."""

    # sourav: wrap an open WebSocket as a call. Flow: remember the socket, the audio format and the exception class that means
    # "connection closed"; nothing is sent until the connector writes audio.
    def __init__(
        self,
        ws: Any,
        fmt: AudioFormat,
        closed_error: type[Exception],
        clock: Callable[[], float] = time.monotonic,
    ):
        self._ws = ws
        self._format = fmt
        self._closed_error = closed_error
        self._clock = clock
        self._bytes_per_sample = 2 if fmt.codec == CODEC_PCM_S16LE else 1
        self._chunk_bytes = max(1, int(fmt.sample_rate * CHUNK_S)) * self._bytes_per_sample
        self._outbox = bytearray()
        self._next_t = 0.0  # clock time when everything sent so far has finished playing
        self._wake = asyncio.Event()
        self._sender: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def audio_format(self) -> AudioFormat:
        return self._format

    # sourav: caller audio -> connector. Flow: wait for the next frame; binary = audio, text = ignored; socket closed = None.
    async def read_audio(self) -> bytes | None:
        while True:
            try:
                message = await self._ws.recv()
            except self._closed_error:
                return None
            if isinstance(message, bytes | bytearray):
                return bytes(message)

    # sourav: connector -> caller. Flow: queue the clip and make sure the pacing task is running; it sends it in real time so
    # a later stop_playback can still drop the unsent part.
    async def write_audio(self, audio: bytes) -> None:
        if self._closed:
            return
        self._outbox += audio
        if self._sender is None or self._sender.done():
            self._sender = asyncio.create_task(self._send_loop(), name="ws-provider-send")
        self._wake.set()

    # sourav: real-time pacing. Flow: when idle wait for audio; otherwise send one 20 ms chunk whenever the schedule is within
    # LEAD_S of now, advance the schedule by the chunk's duration. A dead socket empties the queue and ends the task.
    async def _send_loop(self) -> None:
        try:
            while not self._closed:
                if not self._outbox:
                    self._wake.clear()
                    await self._wake.wait()
                    continue
                now = self._clock()
                self._next_t = max(self._next_t, now)
                delay = self._next_t - LEAD_S - now
                if delay > 0:
                    await asyncio.sleep(delay)
                if not self._outbox:  # flushed while sleeping
                    continue
                piece = bytes(self._outbox[: self._chunk_bytes])
                del self._outbox[: self._chunk_bytes]
                await self._ws.send(piece)
                self._next_t += self._format.duration_s(len(piece))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - a dead socket must not crash the call task; read_audio sees the close
            logger.warning("outbound audio stopped (%s)", type(e).__name__)
            self._outbox.clear()

    # sourav: "everything written has played" is ESTIMATED (the endpoint sends no signal). Flow: wait until the queue is empty
    # and the schedule's end time has passed.
    async def wait_playout(self) -> None:
        while not self._closed and (self._outbox or self._clock() < self._next_t):
            await asyncio.sleep(0.01 if self._outbox else min(0.05, max(0.005, self._next_t - self._clock())))

    # sourav: stop_playback / interrupt. Flow: drop everything not yet sent and reset the schedule. Already-sent audio cannot
    # be recalled over a plain WebSocket.
    async def flush_playback(self) -> None:
        self._outbox.clear()
        self._next_t = 0.0
        self._wake.set()

    # sourav: end the call. Flow: idempotent; stop the pacing task, close the socket, ignore errors on an already-dead socket.
    async def hangup(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._outbox.clear()
        if self._sender is not None:
            self._sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._sender
        with contextlib.suppress(Exception):
            await self._ws.close()


class WebSocketProvider(TelephonyProvider):
    """Connects to the client's endpoint and hands the connection to the connector as one call."""

    # sourav: keep everything needed to connect later. Flow: store endpoint, headers (secrets stay in memory only) and format;
    # `connect` is injectable so tests need no network.
    def __init__(
        self,
        endpoint: str,
        headers: dict[str, str],
        fmt: AudioFormat,
        connect: Connect | None = None,
        closed_error: type[Exception] | None = None,
    ):
        self._endpoint = endpoint
        self._headers = headers
        self._format = fmt
        self._connect = connect
        self._closed_error = closed_error

    # sourav: the production connection. Flow: import websockets lazily (only this provider needs it), open with a timeout.
    async def _default_connect(self, url: str, headers: dict[str, str]) -> Any:
        import websockets

        return await websockets.connect(
            url, additional_headers=headers or None, max_size=MAX_FRAME_BYTES, open_timeout=CONNECT_TIMEOUT_S
        )

    # sourav: the provider side of the contract. Flow: connect, wrap as a call, give it to the connector until it ends, always
    # hang up. A connection failure is reported with the redacted endpoint and the error TYPE only (never the URL or headers).
    async def serve(self, on_call: CallHandler) -> None:
        closed_error = self._closed_error
        if closed_error is None:
            from websockets.exceptions import ConnectionClosed

            closed_error = ConnectionClosed
        connect = self._connect or self._default_connect
        try:
            ws = await connect(self._endpoint, self._headers)
        except Exception as e:  # noqa: BLE001
            raise ConfigError(
                f"cannot connect to the telephony endpoint {redact_endpoint(self._endpoint)} ({type(e).__name__}); "
                "check TELEPHONY_ENDPOINT, the credentials and that the endpoint is reachable"
            ) from None
        logger.info(
            "connected to %s (audio %s @ %d Hz)",
            redact_endpoint(self._endpoint),
            self._format.codec,
            self._format.sample_rate,
        )
        call = WebSocketCall(ws, self._format, closed_error)
        try:
            await on_call(call)
        finally:
            await call.hangup()


# sourav: build the Authorization header from the optional credentials. Flow: api key -> Bearer; username+password -> Basic;
# both or a half pair -> error (ambiguous / incomplete); none -> no header.
def _auth_headers(settings: ProviderSettings) -> dict[str, str]:
    has_pair = settings.username is not None or settings.password is not None
    if settings.api_key is not None and has_pair:
        raise ConfigError(
            "set either TELEPHONY_API_KEY or TELEPHONY_USERNAME/TELEPHONY_PASSWORD for the websocket adapter, not both"
        )
    if settings.api_key is not None:
        return {"Authorization": f"Bearer {settings.api_key}"}
    if has_pair:
        if settings.username is None or settings.password is None:
            raise ConfigError("TELEPHONY_USERNAME and TELEPHONY_PASSWORD must both be set")
        token = base64.b64encode(f"{settings.username}:{settings.password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}
    return {}


# sourav: the hook telephony/loader.py calls. Flow: require a ws:// or wss:// endpoint, build the headers, refuse credentials
# over cleartext to a non-private host, validate the audio format, return the provider. No network access happens here.
def create_provider(settings: ProviderSettings, connect: Connect | None = None) -> TelephonyProvider:
    if settings.endpoint is None:
        raise ConfigError(
            "the websocket provider needs TELEPHONY_ENDPOINT set to the client's ws:// or wss:// media endpoint"
        )
    parts = urlsplit(settings.endpoint)
    if parts.scheme.lower() not in ("ws", "wss") or not parts.hostname:
        raise ConfigError("the websocket provider needs a ws:// or wss:// TELEPHONY_ENDPOINT")
    headers = _auth_headers(settings)
    if headers and parts.scheme.lower() == "ws" and not is_private_host(parts.hostname):
        raise ConfigError("refusing to send credentials over unencrypted ws:// to a non-private host; use wss://")
    try:
        fmt = AudioFormat(settings.audio_codec or DEFAULT_CODEC, settings.audio_sample_rate or DEFAULT_SAMPLE_RATE)
    except ValueError as e:
        raise ConfigError(f"invalid audio format: {e}") from None
    return WebSocketProvider(settings.endpoint, headers, fmt, connect)
