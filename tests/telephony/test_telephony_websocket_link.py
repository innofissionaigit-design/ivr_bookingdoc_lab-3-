"""tests/telephony/test_telephony_websocket_link.py -- tests the real WebSocket link against an in-process stand-in agent.

What this file is for: starts a tiny local `websockets` server on a random loopback port that behaves like /ws/audio, and
runs the real connector (no fakes on the network side) against it. Proves the bytes on the wire: the exact hello, binary
PCM frames, WAV clips played back, playback_done after the clip, and a clean failure when nothing is listening.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _telephony_fakes import FakeCall, make_wav, tone, wait_until  # noqa: E402

from telephony.agent_link import agent_ws_url, websocket_link_factory  # noqa: E402
from telephony.config import ConnectorSettings  # noqa: E402
from telephony.connector import TelephonyConnector  # noqa: E402

websockets = pytest.importorskip("websockets")


def test_agent_ws_url():
    assert agent_ws_url("127.0.0.1", 8101) == "ws://127.0.0.1:8101/ws/audio"
    assert agent_ws_url("::1", 8101) == "ws://[::1]:8101/ws/audio"


async def test_real_websocket_round_trip():
    received: list = []
    done = asyncio.Event()

    async def handler(ws):
        received.append(await ws.recv())  # hello
        for _ in range(3):
            received.append(await ws.recv())  # binary frames
        await ws.send(make_wav(tone(500, 22050, 0.2), 22050))
        msg = await ws.recv()
        while isinstance(msg, bytes):
            msg = await ws.recv()
        received.append(msg)  # playback_done
        done.set()
        await ws.close()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        connector = TelephonyConnector(ConnectorSettings(agent_port=port))
        call = FakeCall()
        task = asyncio.create_task(connector.handle_call(call))
        await asyncio.wait_for(done.wait(), 8)
        await asyncio.wait_for(task, 8)
    assert received[0] == '{"type": "hello", "sampleRate": 16000, "format": "pcm_s16le", "channels": 1}'
    assert all(isinstance(m, bytes) and len(m) % 2 == 0 for m in received[1:4])
    assert json.loads(received[4]) == {"type": "playback_done"}
    assert len(call.written) == 1
    assert call.hangups == 1


async def test_unreachable_agent_is_a_clean_failure():
    # port 1 on loopback: nothing listens there
    factory = websocket_link_factory(agent_ws_url("127.0.0.1", 1))
    connector = TelephonyConnector(ConnectorSettings(), link_factory=factory)
    call = FakeCall()
    await asyncio.wait_for(connector.handle_call(call), 8)
    assert call.hangups == 1
    assert connector.active_calls == 0


async def test_wait_until_helper_times_out():
    with pytest.raises(AssertionError):
        await wait_until(lambda: False, timeout=0.05)
