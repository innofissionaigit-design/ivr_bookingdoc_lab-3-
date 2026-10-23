"""providers/telephony/probe.py -- optional "what does this URL actually speak?" check (the smart probe).

What this file is for
---------------------
A URL alone does not say which protocol is behind it. Instead of guessing, the operator can switch on a PROBE
(TELEPHONY_AUTO_PROBE=1 with TELEPHONY_PROVIDER=auto, or `python -m telephony --probe`). The probe contacts the endpoint
briefly, looks only at the SHAPE of what comes back, and reports a verdict. It never sends audio, never follows redirects,
never logs message contents, and never picks a provider it cannot justify.

What it can tell:
  ws:// wss://   open the WebSocket handshake and wait a few seconds for the server's FIRST message (we send nothing):
                   binary frame            -> raw_audio     (what providers/telephony/websocket.py expects)
                   JSON with an "event"    -> json_envelope (a vendor media-stream style; the generic adapter would produce
                                              garbage, so auto-start is refused and a dedicated adapter is needed)
                   other text              -> text_other    (unknown text framing; warn, the generic adapter ignores text)
                   nothing in time         -> silent        (many servers wait for us to speak first: inconclusive, NOT a
                                              guarantee that the generic adapter fits)
                   handshake 401/403       -> auth_required ; cannot connect -> unreachable
  http:// https:  one HEAD request (no body, redirects not followed) -> http: status code and content type only. The protocol
                   behind an HTTP URL cannot be determined this way, so this never selects a provider.
  sip: sips:      not probed (needs a SIP stack that does not exist here)            -> not_probed

Honest limit: a probe contacts the client's system. Opening a WebSocket can, on some platforms, start a session on their side,
so it is opt-in and the operator should have the client's agreement. A good verdict does not prove a call will work.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from providers.telephony.websocket import Connect, _auth_headers
from telephony.config import ConfigError, ProviderSettings, is_private_host, redact_endpoint

logger = logging.getLogger("telephony.probe")

PROBE_TIMEOUT_S = 4.0
KIND_RAW_AUDIO = "raw_audio"
KIND_JSON_ENVELOPE = "json_envelope"
KIND_TEXT_OTHER = "text_other"
KIND_SILENT = "silent"
KIND_AUTH = "auth_required"
KIND_UNREACHABLE = "unreachable"
KIND_HTTP = "http"
KIND_NOT_PROBED = "not_probed"

# (url, headers, timeout) -> (status code, content type) ; tests inject a fake, production uses urllib in a thread
HttpHead = Callable[[str, dict[str, str], float], Awaitable[tuple[int, str]]]


@dataclass(frozen=True)
class ProbeResult:
    endpoint: str  # already redacted
    kind: str
    detail: str

    # sourav: one human line for logs and the --probe report. Flow: join the redacted endpoint, the verdict and the detail.
    def summary(self) -> str:
        return f"{self.endpoint}: {self.kind} - {self.detail}"

    @property
    def reachable(self) -> bool:
        return self.kind not in (KIND_UNREACHABLE, KIND_AUTH)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # sourav: a redirect could send our credentials to another host. Flow: never follow; the 3xx status is the answer.
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


# sourav: the production HEAD request. Flow: blocking urllib call run in a worker thread; HTTP errors still carry a status code,
# which is the useful answer; any other failure becomes an OSError for the caller to classify.
async def _default_http_head(url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
    def run() -> tuple[int, str]:
        request = urllib.request.Request(url, method="HEAD", headers=headers)  # noqa: S310 - scheme checked by the caller
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=timeout) as response:  # noqa: S310
                return response.status, response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", "") if e.headers else ""

    return await asyncio.to_thread(run)


# sourav: the production WebSocket connection (same library and limits as the generic adapter). Flow: lazy import, short timeout.
async def _default_ws_connect(url: str, headers: dict[str, str]) -> Any:
    import websockets

    return await websockets.connect(
        url, additional_headers=headers or None, max_size=1024 * 1024, open_timeout=PROBE_TIMEOUT_S
    )


# sourav: classify the first server message by SHAPE only, never storing or logging its content. Flow: binary -> raw audio;
# JSON object with a string "event" -> envelope (event name, capped, is the only thing reported); anything else -> other text.
def _classify_first_message(message: object) -> tuple[str, str]:
    if isinstance(message, bytes | bytearray):
        return KIND_RAW_AUDIO, f"first frame is binary ({len(message)} bytes): raw audio style"
    try:
        data = json.loads(str(message))
    except ValueError:
        return KIND_TEXT_OTHER, "first frame is non-JSON text; the generic adapter ignores text frames"
    if isinstance(data, dict) and isinstance(data.get("event"), str):
        return (
            KIND_JSON_ENVELOPE,
            f"first frame is a JSON message with event {data['event'][:24]!r}: a media-stream envelope style",
        )
    return KIND_TEXT_OTHER, "first frame is JSON without an 'event' field; unknown text framing"


# sourav: WebSocket probe. Flow: open the handshake, wait briefly for the server's first message WITHOUT sending anything,
# classify it, always close. Handshake rejections and connection errors become verdicts (status code / error TYPE only).
async def _probe_ws(url: str, headers: dict[str, str], connect: Connect, timeout: float) -> tuple[str, str]:
    try:
        ws = await connect(url, headers)
    except Exception as e:  # noqa: BLE001
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (401, 403):
            return KIND_AUTH, f"the server rejected the handshake with HTTP {status}: credentials needed or wrong"
        extra = f" (HTTP {status})" if status else ""
        return KIND_UNREACHABLE, f"could not open the WebSocket: {type(e).__name__}{extra}"
    try:
        try:
            message = await asyncio.wait_for(ws.recv(), timeout)
        except TimeoutError:
            return KIND_SILENT, "connected, but the server sent nothing first (it may wait for us): inconclusive"
        except Exception as e:  # noqa: BLE001 - closed right after the handshake, etc.
            return KIND_SILENT, f"connected, then the connection ended ({type(e).__name__}): inconclusive"
        return _classify_first_message(message)
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001, S110 - best effort
            pass


# sourav: HTTP probe. Flow: one HEAD request, report status and content type only; never select a provider from it.
async def _probe_http(url: str, headers: dict[str, str], head: HttpHead, timeout: float) -> tuple[str, str]:
    try:
        status, content_type = await head(url, headers, timeout)
    except Exception as e:  # noqa: BLE001
        return KIND_UNREACHABLE, f"could not reach the HTTP endpoint: {type(e).__name__}"
    if status in (401, 403):
        return KIND_AUTH, f"the server answered HTTP {status}: credentials needed or wrong"
    ctype = content_type.split(";")[0].strip()[:60] or "unknown"
    return (
        KIND_HTTP,
        f"HTTP {status}, content type {ctype}. The voice protocol behind an HTTP URL cannot be determined this way",
    )


# sourav: the public entry point. Flow: require an endpoint, build the same auth headers the adapter would use, never send
# credentials over cleartext to a public host, dispatch by URL scheme, return a verdict (never raises for network problems).
async def probe_endpoint(
    settings: ProviderSettings,
    timeout_s: float = PROBE_TIMEOUT_S,
    connect: Connect | None = None,
    http_head: HttpHead | None = None,
) -> ProbeResult:
    if settings.endpoint is None:
        raise ConfigError("nothing to probe: set TELEPHONY_ENDPOINT")
    parts = urlsplit(settings.endpoint)
    scheme = parts.scheme.lower()
    shown = redact_endpoint(settings.endpoint)
    if scheme in ("sip", "sips"):
        return ProbeResult(shown, KIND_NOT_PROBED, "SIP endpoints are not probed (no SIP stack is available)")
    if scheme not in ("ws", "wss", "http", "https") or not parts.hostname:
        return ProbeResult(shown, KIND_NOT_PROBED, "unknown or missing URL scheme; nothing to probe")
    headers = _auth_headers(settings)
    if headers and scheme in ("ws", "http") and not is_private_host(parts.hostname):
        headers = {}  # never send credentials in clear text to a public host
        note = " (credentials were NOT sent: the URL is not encrypted)"
    else:
        note = ""
    if scheme in ("ws", "wss"):
        kind, detail = await _probe_ws(settings.endpoint, headers, connect or _default_ws_connect, timeout_s)
    else:
        kind, detail = await _probe_http(settings.endpoint, headers, http_head or _default_http_head, timeout_s)
    result = ProbeResult(shown, kind, detail + note)
    logger.info("probe result: %s", result.summary())
    return result


# sourav: decide whether auto-start may continue after a probe. Flow: a JSON-envelope server, a credential problem or an
# unreachable server stop the start with an explanation; everything else lets the normal static detection decide.
def require_probe_allows_start(result: ProbeResult) -> None:
    if result.kind == KIND_JSON_ENVELOPE:
        raise ConfigError(
            f"probe: {result.summary()}. The generic websocket adapter expects raw audio frames and would produce garbage "
            "audio here. Ask the client for the exact media protocol and write a dedicated adapter (README: How to add a "
            "new provider)."
        )
    if result.kind in (KIND_AUTH, KIND_UNREACHABLE):
        raise ConfigError(f"probe: {result.summary()}. Fix the endpoint or credentials, then start again.")
