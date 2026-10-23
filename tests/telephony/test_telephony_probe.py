"""tests/telephony/test_telephony_probe.py -- tests for the optional endpoint probe (providers/telephony/probe.py).

What this file is for: proves the probe classifies what a URL speaks by the SHAPE of its first message, never guesses, never
sends credentials in clear text or follows redirects, stops auto-start for envelope-style servers, and that `--probe` and
TELEPHONY_AUTO_PROBE work through the single entry point. Uses fakes plus real local servers on loopback.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import logging
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from providers.telephony.probe import (  # noqa: E402
    KIND_AUTH,
    KIND_HTTP,
    KIND_JSON_ENVELOPE,
    KIND_NOT_PROBED,
    KIND_RAW_AUDIO,
    KIND_SILENT,
    KIND_TEXT_OTHER,
    KIND_UNREACHABLE,
    probe_endpoint,
    require_probe_allows_start,
)
from telephony.__main__ import _run, main  # noqa: E402
from telephony.config import ConfigError, ProviderSettings, load_connector_settings  # noqa: E402

CANARY = "canary-value-123"
WS = "ws://127.0.0.1:9/media"


class FakeWS:
    def __init__(self, first=None, hang=False):
        self.first, self.hang, self.closed = first, hang, 0

    async def recv(self):
        if self.hang:
            await asyncio.sleep(30)
        if isinstance(self.first, Exception):
            raise self.first
        return self.first

    async def close(self):
        self.closed += 1


def connecting(ws):
    async def connect(url, headers):
        connecting.seen = (url, headers)  # type: ignore[attr-defined]
        return ws

    return connect


@pytest.mark.parametrize(
    ("first", "kind"),
    [
        (b"\x00\x01" * 80, KIND_RAW_AUDIO),
        (json.dumps({"event": "connected", "protocol": "Call"}), KIND_JSON_ENVELOPE),
        (json.dumps({"hello": 1}), KIND_TEXT_OTHER),
        ("plain greeting", KIND_TEXT_OTHER),
    ],
)
async def test_classifies_first_message_shape(first, kind):
    ws = FakeWS(first)
    result = await probe_endpoint(ProviderSettings(endpoint=WS), connect=connecting(ws))
    assert result.kind == kind
    assert ws.closed == 1  # always closed, and we sent nothing (FakeWS has no send at all)


async def test_silent_server_is_inconclusive_not_a_green_light():
    ws = FakeWS(hang=True)
    result = await probe_endpoint(ProviderSettings(endpoint=WS), timeout_s=0.05, connect=connecting(ws))
    assert result.kind == KIND_SILENT and "inconclusive" in result.detail
    require_probe_allows_start(result)  # does not block: static detection decides


async def test_handshake_failures_become_verdicts():
    class Rejected(Exception):
        class response:  # noqa: N801
            status_code = 403

    async def reject(url, headers):
        raise Rejected

    async def refuse(url, headers):
        raise ConnectionRefusedError

    assert (await probe_endpoint(ProviderSettings(endpoint=WS), connect=reject)).kind == KIND_AUTH
    assert (await probe_endpoint(ProviderSettings(endpoint=WS), connect=refuse)).kind == KIND_UNREACHABLE


async def test_json_envelope_blocks_auto_start_with_a_clear_reason():
    ws = FakeWS(json.dumps({"event": "start"}))
    result = await probe_endpoint(ProviderSettings(endpoint=WS), connect=connecting(ws))
    with pytest.raises(ConfigError, match="would produce garbage"):
        require_probe_allows_start(result)


async def test_unprobeable_endpoints_are_reported_not_guessed():
    for endpoint in ("sip:a@b.example.com", "ftp://x.example.com", "plain-text"):
        assert (await probe_endpoint(ProviderSettings(endpoint=endpoint))).kind == KIND_NOT_PROBED
    with pytest.raises(ConfigError):
        await probe_endpoint(ProviderSettings())


async def test_credentials_are_sent_over_private_or_encrypted_only():
    ws = FakeWS(b"\x00\x00")
    await probe_endpoint(ProviderSettings(endpoint="ws://127.0.0.1:9/m", api_key=CANARY), connect=connecting(ws))
    assert connecting.seen[1] == {"Authorization": f"Bearer {CANARY}"}  # type: ignore[attr-defined]
    result = await probe_endpoint(
        ProviderSettings(endpoint="ws://client.example.com/m", api_key=CANARY), connect=connecting(ws)
    )
    assert connecting.seen[1] == {}  # type: ignore[attr-defined]
    assert "NOT sent" in result.detail and CANARY not in result.summary()


async def test_http_probe_reports_status_and_never_selects_a_provider():
    async def head(url, headers, timeout):
        return 200, "application/json; charset=utf-8"

    result = await probe_endpoint(ProviderSettings(endpoint="https://api.example.com/v?k=" + CANARY), http_head=head)
    assert result.kind == KIND_HTTP and "HTTP 200" in result.detail and "application/json" in result.detail
    assert CANARY not in result.summary()

    async def denied(url, headers, timeout):
        return 401, ""

    assert (
        await probe_endpoint(ProviderSettings(endpoint="https://x.example.com"), http_head=denied)
    ).kind == KIND_AUTH

    async def down(url, headers, timeout):
        raise TimeoutError

    assert (
        await probe_endpoint(ProviderSettings(endpoint="https://x.example.com"), http_head=down)
    ).kind == KIND_UNREACHABLE


def serve_http(handler_cls):
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def test_real_http_server_head_and_redirect_not_followed():
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self):  # noqa: N802
            hits.append(self.path)
            if self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
            self.end_headers()

        def log_message(self, *a):
            pass

    server = serve_http(Handler)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        ok = await probe_endpoint(ProviderSettings(endpoint=base + "/voice"))
        assert ok.kind == KIND_HTTP and "HTTP 200" in ok.detail
        moved = await probe_endpoint(ProviderSettings(endpoint=base + "/redir"))
        assert "HTTP 302" in moved.detail
        assert "/elsewhere" not in hits  # the redirect was not followed
    finally:
        server.shutdown()


# ---- through the single entry point -----------------------------------------------------------------------------------------


async def ws_server(first):
    websockets = pytest.importorskip("websockets")

    async def handler(ws):
        if first is not None:
            await ws.send(first)
        await asyncio.sleep(0.3)

    return websockets.serve(handler, "127.0.0.1", 0)


async def test_probe_flag_is_read_from_the_environment():
    assert load_connector_settings({}).auto_probe is False
    assert load_connector_settings({"TELEPHONY_AUTO_PROBE": "1"}).auto_probe is True


async def test_auto_with_probe_refuses_an_envelope_style_server():
    async with await ws_server(json.dumps({"event": "connected"})) as server:
        port = server.sockets[0].getsockname()[1]
        env = {
            "TELEPHONY_PROVIDER": "auto",
            "TELEPHONY_AUTO_PROBE": "1",
            "TELEPHONY_ENDPOINT": f"ws://127.0.0.1:{port}/media",
            "VOICE_PCM_PORT": "1",
        }
        with pytest.raises(ConfigError, match="would produce garbage"):
            await _run(env)


async def test_auto_without_probe_does_not_contact_the_endpoint():
    # nothing listens on :1; without the probe flag the only contact is the normal adapter connection, which fails clearly
    env = {"TELEPHONY_PROVIDER": "auto", "TELEPHONY_ENDPOINT": "ws://127.0.0.1:1/media", "VOICE_PCM_PORT": "1"}
    with pytest.raises(ConfigError, match="cannot connect"):
        await _run(env)


def test_probe_cli_prints_a_verdict_without_secrets(capsys, caplog):
    env = {"TELEPHONY_ENDPOINT": f"ws://127.0.0.1:1/media/{CANARY}?t={CANARY}", "TELEPHONY_API_KEY": CANARY}
    with caplog.at_level(logging.DEBUG):
        code = main(env, ["--probe"])
    out = capsys.readouterr().out
    assert code == 2  # nothing listens on :1
    assert "unreachable" in out and "ws://127.0.0.1:1/..." in out
    assert CANARY not in out and CANARY not in caplog.text


def test_unknown_cli_argument_is_rejected():
    assert main({}, ["--nonsense"]) == 2
