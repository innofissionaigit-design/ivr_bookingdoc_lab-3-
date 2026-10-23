"""tests/telephony/test_telephony_connector.py -- tests for telephony/connector.py (the provider-independent core).

What this file is for: drives the connector with a FakeCall (the phone side) and a FakeLink (playing main_pcm.py) and checks
the /ws/audio behaviour: exact hello, PCM16 16 kHz uplink, ordered playback, playback_done only after audio has played,
stop_playback, interrupt, handoff_human, caller hang-up, agent close, independent sessions and malformed input.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _telephony_fakes import FakeCall, LinkRecorder, dominant_freq, make_wav, tone, wait_until  # noqa: E402

from telephony.audio import pcm16_from_bytes  # noqa: E402
from telephony.config import ConnectorSettings  # noqa: E402
from telephony.connector import HELLO_MESSAGE, CallSession, TelephonyConnector, hello_frame  # noqa: E402

TICK = 0.01


def start(call: FakeCall, links: LinkRecorder | None = None, **kw):
    links = links or LinkRecorder()
    session = CallSession(call, links, tick_s=TICK, **kw)
    task = asyncio.create_task(session.run())
    return session, links, task


async def finish(task: asyncio.Task, call: FakeCall) -> None:
    call.inbound.put_nowait(None)
    await asyncio.wait_for(task, 5)


def clip(freq: float = 440, seconds: float = 0.3, rate: int = 22050) -> bytes:
    return make_wav(tone(freq, rate, seconds), rate)


async def test_hello_is_first_and_exact():
    call = FakeCall()
    _, links, task = start(call)
    await wait_until(lambda: links.links and links.links[0].events)
    assert links.links[0].events[0] == (
        "text",
        '{"type": "hello", "sampleRate": 16000, "format": "pcm_s16le", "channels": 1}',
    )
    assert json.loads(hello_frame()) == {"type": "hello", "sampleRate": 16000, "format": "pcm_s16le", "channels": 1}
    assert HELLO_MESSAGE["sampleRate"] == 16000
    await finish(task, call)


async def test_uplink_is_mono_pcm16_16k_and_keeps_the_signal():
    call = FakeCall(codec="mulaw", sample_rate=8000)
    _, links, task = start(call)
    call.feed_tone(1000, 1.0)
    link_ready = lambda: links.links and len(links.links[0].audio) >= 2 * 16000 * 0.8  # noqa: E731
    await wait_until(link_ready, timeout=4)
    pcm = pcm16_from_bytes(links.links[0].audio)
    assert len(links.links[0].audio) % 2 == 0
    body = pcm[4000:12000]
    assert abs(dominant_freq(body, 16000) - 1000) < 30
    await finish(task, call)


async def test_uplink_stays_continuous_when_caller_audio_pauses():
    call = FakeCall()
    _, links, task = start(call)
    await asyncio.sleep(0.5)  # no caller audio at all
    assert len(links.links[0].audio) >= 2 * 16000 * 0.3  # silence keeps the agent's timeline moving
    assert not np.any(pcm16_from_bytes(links.links[0].audio))
    await finish(task, call)


async def test_playback_is_converted_ordered_and_done_sent_once_after_playout():
    call = FakeCall(codec="mulaw", sample_rate=8000, hold_playout=True)
    _, links, task = start(call)
    await wait_until(lambda: links.links)
    link = links.links[0]
    link.server_send(clip(300, 0.2, 22050))  # agent rates are read from the header, not assumed
    link.server_send(clip(900, 0.2, 16000))
    await wait_until(lambda: len(call.written) == 2)
    assert (
        dominant_freq(__import__("telephony.audio", fromlist=["x"]).decode_codec(call.written[0], "mulaw"), 8000) < 450
    )
    assert (
        dominant_freq(__import__("telephony.audio", fromlist=["x"]).decode_codec(call.written[1], "mulaw"), 8000) > 800
    )
    await asyncio.sleep(0.1)
    assert '{"type": "playback_done"}' not in link.texts  # still "playing"
    call.release_playout()
    await wait_until(lambda: link.texts.count('{"type": "playback_done"}') == 1)
    await asyncio.sleep(0.2)
    assert link.texts.count('{"type": "playback_done"}') == 1
    await finish(task, call)


async def test_caller_audio_is_muted_while_reply_plays():
    call = FakeCall(hold_playout=True)
    _, links, task = start(call)
    await wait_until(lambda: links.links)
    links.links[0].server_send(clip())
    await wait_until(lambda: call.written)
    mark = len(links.links[0].audio)
    call.feed_tone(1000, 0.3)
    await asyncio.sleep(0.6)
    muted = pcm16_from_bytes(links.links[0].audio[mark:])
    assert len(muted) > 0 and not np.any(muted)
    await finish(task, call)


async def test_stop_playback_flushes_and_sends_no_playback_done():
    call = FakeCall(hold_playout=True)
    _, links, task = start(call)
    await wait_until(lambda: links.links)
    link = links.links[0]
    link.server_send(clip())
    await wait_until(lambda: call.written)
    link.server_send(json.dumps({"type": "stop_playback"}))
    await wait_until(lambda: call.flush_count >= 1)
    await asyncio.sleep(0.2)
    assert '{"type": "playback_done"}' not in link.texts
    await finish(task, call)


async def test_interrupt_from_adapter_flushes_then_notifies_agent():
    call = FakeCall(hold_playout=True)
    _, links, task = start(call)
    await wait_until(lambda: links.links)
    link = links.links[0]
    link.server_send(clip())
    await wait_until(lambda: call.written)
    call.interrupts.put_nowait(None)
    await wait_until(lambda: '{"type": "interrupt"}' in link.texts)
    assert call.flush_count >= 1
    await finish(task, call)


async def test_handoff_waits_for_playout_then_transfers_and_does_not_hang_up_first():
    call = FakeCall(hold_playout=True, transfer_result=True)
    session, links, task = start(call)
    await wait_until(lambda: links.links)
    link = links.links[0]
    link.server_send(json.dumps({"type": "handoff_human", "reason": "caller_request"}))
    link.server_send(clip())
    await wait_until(lambda: call.written)
    link.server_close()
    await asyncio.sleep(0.2)
    assert call.transfers == []  # the notice is still playing
    call.release_playout()
    await asyncio.wait_for(task, 5)
    assert call.transfers == ["caller_request"]
    assert call.hangups == 0  # handed over, not dropped


async def test_handoff_without_transfer_support_ends_call_safely():
    call = FakeCall()
    _, links, task = start(call)
    await wait_until(lambda: links.links)
    links.links[0].server_send(json.dumps({"type": "handoff_human"}))
    links.links[0].server_close()
    await asyncio.wait_for(task, 5)
    assert call.transfers == ["unspecified"]
    assert call.hangups == 1


async def test_transfer_exception_is_contained():
    call = FakeCall()

    async def boom(reason: str) -> bool:
        raise RuntimeError("secret detail")

    call.transfer = boom  # type: ignore[method-assign]
    _, links, task = start(call)
    await wait_until(lambda: links.links)
    links.links[0].server_send(json.dumps({"type": "handoff_human", "reason": "x"}))
    links.links[0].server_close()
    await asyncio.wait_for(task, 5)
    assert call.hangups == 1


async def test_caller_hangup_closes_agent_socket_and_cleans_up():
    call = FakeCall()
    session, links, task = start(call)
    await wait_until(lambda: links.links)
    await finish(task, call)
    assert links.links[0].closed
    assert call.hangups == 1
    assert session.end_reason == "caller_hung_up"


async def test_agent_close_ends_call_after_drain():
    call = FakeCall()
    session, links, task = start(call)
    await wait_until(lambda: links.links)
    links.links[0].server_close()
    await asyncio.wait_for(task, 5)
    assert session.end_reason == "agent_closed"
    assert call.hangups == 1


async def test_drain_timeout_bounds_a_stuck_playout():
    call = FakeCall(hold_playout=True)
    _, links, task = start(call, drain_timeout_s=0.2)
    await wait_until(lambda: links.links)
    links.links[0].server_send(clip())
    await wait_until(lambda: call.written)
    links.links[0].server_close()
    await asyncio.wait_for(task, 5)
    assert call.hangups == 1


async def test_agent_unreachable_hangs_up_without_crashing():
    call = FakeCall()
    session, _, task = start(call, LinkRecorder(fail=True))
    await asyncio.wait_for(task, 5)
    assert session.end_reason == "agent_unreachable"
    assert call.hangups == 1


async def test_malformed_input_is_ignored():
    call = FakeCall(codec="pcm_s16le", sample_rate=16000)
    session, links, task = start(call)
    await wait_until(lambda: links.links)
    link = links.links[0]
    link.server_send("not json")
    link.server_send("[1, 2]")
    link.server_send(json.dumps({"type": "something_new"}))
    link.server_send(json.dumps({"type": "config", "aec": True}))
    link.server_send(json.dumps({"sender": "agent", "text": "hello"}))
    link.server_send(b"this is not a wav file")
    call.inbound.put_nowait(b"\x01")  # odd byte count: not whole samples
    call.inbound.put_nowait(b"")
    link.server_send(clip())
    await wait_until(lambda: len(call.written) == 1)  # the good clip still plays
    assert not task.done()
    await finish(task, call)


async def test_sessions_are_independent():
    links = LinkRecorder()
    calls = [FakeCall(hold_playout=True), FakeCall(hold_playout=True)]
    sessions = [CallSession(c, links, tick_s=TICK) for c in calls]
    tasks = [asyncio.create_task(s.run()) for s in sessions]
    await wait_until(lambda: len(links.links) == 2)
    links.links[0].server_send(clip(300))
    await wait_until(lambda: calls[0].written)
    assert calls[1].written == []
    calls[0].release_playout()
    await wait_until(lambda: '{"type": "playback_done"}' in links.links[0].texts)
    assert '{"type": "playback_done"}' not in links.links[1].texts
    calls[0].inbound.put_nowait(None)
    await asyncio.wait_for(tasks[0], 5)
    assert not tasks[1].done() and not links.links[1].closed
    calls[1].inbound.put_nowait(None)
    await asyncio.wait_for(tasks[1], 5)
    assert sessions[0].id != sessions[1].id


async def test_connector_tracks_calls_and_stop_all():
    links = LinkRecorder()
    connector = TelephonyConnector(ConnectorSettings(), link_factory=links)
    calls = [FakeCall(), FakeCall(), FakeCall()]
    tasks = [asyncio.create_task(connector.handle_call(c)) for c in calls]
    await wait_until(lambda: connector.active_calls == 3)
    connector.stop_all()
    await asyncio.wait_for(asyncio.gather(*tasks), 5)
    assert connector.active_calls == 0
    assert [c.hangups for c in calls] == [1, 1, 1]
    assert all(link.closed for link in links.links)


async def test_connector_run_serves_provider_and_closes_it():
    from telephony.adapter import TelephonyProvider

    class P(TelephonyProvider):
        closed = False

        async def serve(self, on_call):
            await on_call(FakeCall())

        async def close(self):
            P.closed = True

    links = LinkRecorder()
    connector = TelephonyConnector(ConnectorSettings(), link_factory=links)
    provider = P()

    async def hangup_soon():
        await wait_until(lambda: links.links)
        links.links[0].server_close()

    helper = asyncio.create_task(hangup_soon())
    await asyncio.wait_for(connector.run(provider), 5)
    await helper
    assert P.closed


def test_connector_core_has_no_provider_names():
    import telephony.connector as c

    text = Path(c.__file__).read_text(encoding="utf-8").lower()
    for vendor in ("twilio", "zoiper", "asterisk", "freeswitch", "kamailio"):
        assert vendor not in text
