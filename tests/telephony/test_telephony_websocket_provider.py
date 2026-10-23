"""tests/telephony/test_telephony_websocket_provider.py -- tests for providers/telephony/websocket.py (generic WebSocket adapter).

What this file is for: proves the one real new adapter does exactly what its docstring says and nothing more: configuration
validation (endpoint, auth headers, cleartext-credential refusal, audio format), reading/writing/pacing/flush/hangup on a fake
socket, the full chain through the unchanged connector, a real in-process WebSocket server, independent concurrent calls, and
that no credential leaks into logs or errors.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _telephony_fakes import LinkRecorder, make_wav, tone, wait_until  # noqa: E402

from providers.telephony.websocket import WebSocketCall, WebSocketProvider, create_provider  # noqa: E402
from telephony.adapter import AudioFormat  # noqa: E402
from telephony.audio import encode_codec  # noqa: E402
from telephony.config import ConfigError, ConnectorSettings, ProviderSettings  # noqa: E402
from telephony.connector import TelephonyConnector  # noqa: E402

CANARY = "canary-value-123"
_CLOSE = object()


class FakeClosed(Exception):
    """Stands in for websockets' ConnectionClosed."""


class FakeWS:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[object] = asyncio.Queue()
        self.sent: list[bytes] = []
        self.closed = 0

    async def recv(self):
        item = await self.incoming.get()
        if item is _CLOSE:
            raise FakeClosed
        return item

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed += 1
        self.incoming.put_nowait(_CLOSE)


# ---- configuration ----------------------------------------------------------------------------------------------------------


def test_requires_ws_endpoint():
    with pytest.raises(ConfigError, match="TELEPHONY_ENDPOINT"):
        create_provider(ProviderSettings())
    for bad in ("https://x.example.com/m", "sip:a@b.example.com", "client.example.com", "ws://"):
        with pytest.raises(ConfigError, match=r"ws://|wss://"):
            create_provider(ProviderSettings(endpoint=bad))


def test_default_format_is_the_agents_own():
    provider = create_provider(ProviderSettings(endpoint="wss://c.example.com/m"))
    assert isinstance(provider, WebSocketProvider)
    assert provider._format == AudioFormat("pcm_s16le", 16000)


def test_format_override_and_validation():
    provider = create_provider(
        ProviderSettings(endpoint="wss://c.example.com/m", audio_codec="mulaw", audio_sample_rate=8000)
    )
    assert provider._format == AudioFormat("mulaw", 8000)  # type: ignore[attr-defined]
    with pytest.raises(ConfigError, match="audio format"):
        create_provider(ProviderSettings(endpoint="wss://c.example.com/m", audio_codec="opus"))


def test_bearer_and_basic_auth_headers():
    bearer = create_provider(ProviderSettings(endpoint="wss://c.example.com/m", api_key="k123"))
    assert bearer._headers == {"Authorization": "Bearer k123"}  # type: ignore[attr-defined]
    basic = create_provider(ProviderSettings(endpoint="wss://c.example.com/m", username="u", password="p"))
    token = base64.b64encode(b"u:p").decode()
    assert basic._headers == {"Authorization": f"Basic {token}"}  # type: ignore[attr-defined]
    assert create_provider(ProviderSettings(endpoint="wss://c.example.com/m"))._headers == {}  # type: ignore[attr-defined]


def test_ambiguous_or_incomplete_credentials_are_errors():
    with pytest.raises(ConfigError, match="not both"):
        create_provider(ProviderSettings(endpoint="wss://c.example.com/m", api_key="k", username="u", password="p"))
    with pytest.raises(ConfigError, match="both be set"):
        create_provider(ProviderSettings(endpoint="wss://c.example.com/m", username="u"))


def test_credentials_refused_over_cleartext_to_public_hosts():
    with pytest.raises(ConfigError, match="unencrypted"):
        create_provider(ProviderSettings(endpoint="ws://client.example.com/m", api_key=CANARY))
    assert create_provider(ProviderSettings(endpoint="ws://127.0.0.1:9/m", api_key=CANARY))  # private network is fine
    assert create_provider(ProviderSettings(endpoint="ws://client.example.com/m"))  # no credentials -> nothing to leak


# ---- the call on a fake socket ----------------------------------------------------------------------------------------------


def make_call(codec="mulaw", rate=8000):
    ws = FakeWS()
    return ws, WebSocketCall(ws, AudioFormat(codec, rate), FakeClosed)


async def test_read_audio_returns_binary_ignores_text_and_ends_on_close():
    ws, call = make_call()
    ws.incoming.put_nowait("a text frame with personal data")
    ws.incoming.put_nowait(b"\x01\x02")
    ws.incoming.put_nowait(_CLOSE)
    assert await call.read_audio() == b"\x01\x02"
    assert await call.read_audio() is None


async def test_writes_are_paced_in_order_and_playout_is_estimated():
    ws, call = make_call()  # 8 kHz mulaw: 160 bytes = 20 ms
    audio = bytes(range(256)) * 4  # 1024 bytes = 128 ms
    started = asyncio.get_running_loop().time()
    await call.write_audio(audio)
    await call.wait_playout()
    elapsed = asyncio.get_running_loop().time() - started
    assert b"".join(ws.sent) == audio  # nothing lost, nothing reordered
    assert all(len(f) <= 160 for f in ws.sent)
    assert elapsed >= 0.10  # not sent as one instant burst: playout follows real time
    await call.hangup()


async def test_flush_drops_unsent_audio_and_releases_wait_playout():
    ws, call = make_call()
    await call.write_audio(bytes(8000))  # one second
    await asyncio.sleep(0.05)
    await call.flush_playback()
    await asyncio.wait_for(call.wait_playout(), 1)
    sent = sum(len(f) for f in ws.sent)
    assert sent < 8000 * 0.4  # the bulk of the second was never sent
    await call.hangup()


async def test_hangup_is_idempotent_and_closes_the_socket_once():
    ws, call = make_call()
    await call.write_audio(bytes(800))
    await call.hangup()
    await call.hangup()
    assert ws.closed == 1
    await call.write_audio(bytes(10))  # ignored after hangup, no error
    assert call.caller_id is None
    assert await call.transfer("x") is False  # honest: transfer is not supported


async def test_dead_socket_while_sending_does_not_crash():
    ws, call = make_call()

    async def broken(_data):
        raise FakeClosed

    ws.send = broken  # type: ignore[method-assign]
    await call.write_audio(bytes(800))
    await asyncio.wait_for(call.wait_playout(), 2)
    await call.hangup()


# ---- through the unchanged connector ----------------------------------------------------------------------------------------


def provider_for(ws: FakeWS, codec="mulaw", rate=8000, headers=None) -> WebSocketProvider:
    async def connect(url, hdrs):
        connect.calls.append((url, hdrs))  # type: ignore[attr-defined]
        return ws

    connect.calls = []  # type: ignore[attr-defined]
    provider = WebSocketProvider("wss://c.example.com/m", headers or {}, AudioFormat(codec, rate), connect, FakeClosed)
    provider.connect_calls = connect.calls  # type: ignore[attr-defined]
    return provider


async def test_full_chain_caller_audio_reaches_agent_and_reply_reaches_socket():
    ws = FakeWS()
    provider = provider_for(ws, headers={"Authorization": "Bearer k"})
    links = LinkRecorder()
    connector = TelephonyConnector(ConnectorSettings(), link_factory=links)
    runner = asyncio.create_task(connector.run(provider))
    await wait_until(lambda: links.links)
    link = links.links[0]
    for i in range(0, 8000, 160):
        ws.incoming.put_nowait(encode_codec(tone(1000, 8000, 1.0)[i : i + 160], "mulaw"))
    await wait_until(lambda: len(link.audio) > 4000)
    assert link.messages[0] == {"type": "hello", "sampleRate": 16000, "format": "pcm_s16le", "channels": 1}
    link.server_send(make_wav(tone(500, 22050, 0.2), 22050))
    await wait_until(lambda: {"type": "playback_done"} in link.messages, timeout=5)
    assert sum(len(f) for f in ws.sent) > 1000  # the reply was delivered, converted to 8 kHz mu-law (~0.2 s = 1600 B)
    assert provider.connect_calls[0][1] == {"Authorization": "Bearer k"}  # type: ignore[attr-defined]
    ws.incoming.put_nowait(_CLOSE)  # caller hangs up
    await asyncio.wait_for(runner, 5)
    assert ws.closed >= 1
    assert link.closed


async def test_multiple_independent_calls_on_one_connector():
    links = LinkRecorder()
    connector = TelephonyConnector(ConnectorSettings(), link_factory=links)
    sockets = [FakeWS(), FakeWS()]
    runners = [asyncio.create_task(connector.run(provider_for(ws))) for ws in sockets]
    await wait_until(lambda: len(links.links) == 2)
    links.links[0].server_send(make_wav(tone(500, 22050, 0.2), 22050))
    await wait_until(lambda: sockets[0].sent)
    assert sockets[1].sent == []  # the other call heard nothing
    sockets[0].incoming.put_nowait(_CLOSE)
    await asyncio.wait_for(runners[0], 5)
    assert not runners[1].done() and not links.links[1].closed
    sockets[1].incoming.put_nowait(_CLOSE)
    await asyncio.wait_for(runners[1], 5)


# ---- real WebSocket server --------------------------------------------------------------------------------------------------


async def test_real_websocket_endpoint_with_auth_header():
    websockets = pytest.importorskip("websockets")
    seen: dict[str, object] = {}

    async def handler(ws):
        seen["auth"] = ws.request.headers.get("Authorization")
        await ws.send("ignored text frame")
        for _ in range(10):
            await ws.send(encode_codec(tone(800, 8000, 0.02), "mulaw"))
            await asyncio.sleep(0.02)
        await ws.close()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        provider = create_provider(
            ProviderSettings(
                endpoint=f"ws://127.0.0.1:{port}/media", api_key="k-abc", audio_codec="mulaw", audio_sample_rate=8000
            )
        )
        links = LinkRecorder()
        connector = TelephonyConnector(ConnectorSettings(), link_factory=links)
        await asyncio.wait_for(connector.run(provider), 8)
    assert seen["auth"] == "Bearer k-abc"
    assert len(links.links[0].audio) > 0


async def test_unreachable_endpoint_is_a_clear_error_without_secrets(caplog):
    provider = create_provider(
        ProviderSettings(endpoint=f"ws://127.0.0.1:1/media/{CANARY}?token={CANARY}", api_key=CANARY)
    )
    connector = TelephonyConnector(ConnectorSettings(), link_factory=LinkRecorder())
    with caplog.at_level(logging.DEBUG), pytest.raises(ConfigError) as info:
        await connector.run(provider)
    assert "cannot connect to the telephony endpoint ws://127.0.0.1:1/..." in str(info.value)
    assert CANARY not in str(info.value) and CANARY not in caplog.text


async def test_logs_never_contain_credentials(caplog):
    ws = FakeWS()
    provider = provider_for(ws, headers={"Authorization": f"Bearer {CANARY}"})
    links = LinkRecorder()
    connector = TelephonyConnector(ConnectorSettings(), link_factory=links)
    with caplog.at_level(logging.DEBUG):
        runner = asyncio.create_task(connector.run(provider))
        await wait_until(lambda: links.links)
        ws.incoming.put_nowait(_CLOSE)
        await asyncio.wait_for(runner, 5)
    assert CANARY not in caplog.text
