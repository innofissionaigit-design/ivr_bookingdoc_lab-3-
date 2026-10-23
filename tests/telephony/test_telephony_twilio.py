"""tests/telephony/test_telephony_twilio.py -- tests for providers/telephony/twilio.py using a SIMULATED Twilio.

What this file is for: proves the adapter does what Twilio's documentation says it must (message handling, marks, clear, signature
check, hand-off request) and that it plugs into the unchanged connector. NO real Twilio is involved, so passing these tests does
not prove live-Twilio compatibility; see the checklist in the adapter's docstring for the first real call.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _telephony_fakes import LinkRecorder, make_wav, tone, wait_until  # noqa: E402

from providers.telephony.twilio import (  # noqa: E402
    TwilioCall,
    TwilioProvider,
    create_provider,
    signature_candidates,
    twilio_signature,
    verify_signature,
)
from telephony.audio import decode_codec, encode_codec  # noqa: E402
from telephony.config import ConfigError, ConnectorSettings, ProviderSettings, load_provider_settings  # noqa: E402
from telephony.connector import TelephonyConnector  # noqa: E402

CANARY = "canary-auth-token-123"
SID = "AC00000000000000000000000000000000"
PUBLIC = "wss://voice.example.com/twilio/media"
STREAM = "MZ0000000000000000000000000000000a"
CALL = "CA0000000000000000000000000000000a"
_CLOSE = object()


class FakeClosed(Exception):
    pass


def media_msg(audio: bytes, track="inbound") -> str:
    return json.dumps(
        {"event": "media", "streamSid": STREAM, "media": {"track": track, "payload": base64.b64encode(audio).decode()}}
    )


def start_msg(**over) -> str:
    start = {
        "accountSid": SID,
        "streamSid": STREAM,
        "callSid": CALL,
        "tracks": ["inbound"],
        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
    }
    start.update(over)
    return json.dumps({"event": "start", "streamSid": STREAM, "start": start})


class FakeWS:
    def __init__(self):
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = 0

    async def recv(self):
        item = await self.incoming.get()
        if item is _CLOSE:
            raise FakeClosed
        return item

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def close(self, code=1000):
        self.closed += 1
        self.incoming.put_nowait(_CLOSE)


def make_call(**kw):
    ws = FakeWS()
    return ws, TwilioCall(ws, STREAM, CALL, FakeClosed, **kw)


# ---- configuration ----------------------------------------------------------------------------------------------------------


def test_create_provider_requires_wss_endpoint_and_auth_token():
    with pytest.raises(ConfigError, match="TELEPHONY_ENDPOINT"):
        create_provider(ProviderSettings(password=CANARY))
    with pytest.raises(ConfigError, match="wss://"):
        create_provider(ProviderSettings(endpoint="https://voice.example.com/x", password=CANARY))
    with pytest.raises(ConfigError, match="Auth Token") as info:
        create_provider(ProviderSettings(endpoint=PUBLIC))
    assert CANARY not in str(info.value)
    assert isinstance(create_provider(ProviderSettings(endpoint=PUBLIC, password=CANARY)), TwilioProvider)


def test_transfer_settings_are_validated():
    base = {"endpoint": PUBLIC, "password": CANARY}
    with pytest.raises(ConfigError, match="E.164"):
        create_provider(ProviderSettings(**base, username=SID, transfer_to="0123"))
    with pytest.raises(ConfigError, match="Account SID"):
        create_provider(ProviderSettings(**base, transfer_to="+15551234567"))
    assert create_provider(ProviderSettings(**base, username=SID, transfer_to="+15551234567"))


def test_listen_settings_come_from_the_environment():
    s = load_provider_settings(
        {"TELEPHONY_LISTEN_HOST": "0.0.0.0", "TELEPHONY_LISTEN_PORT": "9443", "TELEPHONY_TRANSFER_TO": "+15551234567"}
    )
    assert (s.listen_host, s.listen_port, s.transfer_to) == ("0.0.0.0", 9443, "+15551234567")
    with pytest.raises(ConfigError):
        load_provider_settings({"TELEPHONY_LISTEN_PORT": "70000"})
    provider = create_provider(ProviderSettings(endpoint=PUBLIC, password=CANARY))
    assert (provider._host, provider._port) == ("127.0.0.1", 8200)  # type: ignore[attr-defined]  # private by default


# ---- signature --------------------------------------------------------------------------------------------------------------


def test_signature_matches_the_documented_algorithm():
    import hashlib
    import hmac

    expected = base64.b64encode(hmac.new(CANARY.encode(), PUBLIC.encode(), hashlib.sha1).digest()).decode()
    assert twilio_signature(CANARY, PUBLIC) == expected


def test_verify_accepts_url_spellings_and_rejects_everything_else():
    assert verify_signature(CANARY, PUBLIC, twilio_signature(CANARY, PUBLIC))
    assert verify_signature(
        CANARY, PUBLIC, twilio_signature(CANARY, PUBLIC + "/")
    )  # Twilio's documented trailing-slash note
    assert verify_signature(CANARY, PUBLIC, twilio_signature(CANARY, PUBLIC.replace("wss://", "https://")))
    assert not verify_signature(CANARY, PUBLIC, twilio_signature("wrong-token", PUBLIC))
    assert not verify_signature(CANARY, PUBLIC, twilio_signature(CANARY, "wss://evil.example.com/twilio/media"))
    assert not verify_signature(CANARY, PUBLIC, None) and not verify_signature(CANARY, PUBLIC, "")
    assert PUBLIC in signature_candidates(PUBLIC)


# ---- the call ---------------------------------------------------------------------------------------------------------------


async def test_read_audio_decodes_inbound_media_and_ignores_the_rest():
    ws, call = make_call()
    audio = encode_codec(tone(500, 8000, 0.02), "mulaw")
    for msg in (
        "not json",
        "[1]",
        json.dumps({"event": "dtmf", "dtmf": {"digit": "5"}}),
        media_msg(b"\x01\x02", track="outbound"),
        json.dumps({"event": "media", "media": {"payload": "!!!not-base64!!!"}}),
        b"binary",
        media_msg(audio),
    ):
        ws.incoming.put_nowait(msg)
    assert await call.read_audio() == audio
    assert call.bad_messages >= 3
    ws.incoming.put_nowait(json.dumps({"event": "stop", "stop": {"callSid": CALL}}))
    assert await call.read_audio() is None
    assert call.audio_format.codec == "mulaw" and call.audio_format.sample_rate == 8000


async def test_closed_socket_means_caller_hung_up():
    ws, call = make_call()
    ws.incoming.put_nowait(_CLOSE)
    assert await call.read_audio() is None


async def test_write_audio_sends_media_then_mark_and_waits_for_the_echo():
    ws, call = make_call()
    audio = bytes(range(256)) * 80  # 20480 B -> two media messages (16000 B cap)
    await call.write_audio(audio)
    kinds = [m["event"] for m in ws.sent]
    assert kinds == ["media", "media", "mark"]
    assert all(m["streamSid"] == STREAM for m in ws.sent)
    assert (
        b"".join(base64.b64decode(m["media"]["payload"]) for m in ws.sent[:2]) == audio
    )  # order preserved, nothing lost
    name = ws.sent[2]["mark"]["name"]
    waiter = asyncio.create_task(call.wait_playout())
    await asyncio.sleep(0.05)
    assert not waiter.done()  # Twilio has not said the audio finished
    ws.incoming.put_nowait(json.dumps({"event": "mark", "streamSid": STREAM, "mark": {"name": name}}))
    reader = asyncio.create_task(call.read_audio())  # marks arrive through the read loop
    await asyncio.wait_for(waiter, 2)
    reader.cancel()


async def test_playout_failsafe_if_twilio_never_echoes_the_mark(monkeypatch):
    import providers.telephony.twilio as mod

    monkeypatch.setattr(mod, "PLAYOUT_SLACK_S", 0.1)
    ws, call = make_call()
    await call.write_audio(bytes(160))  # 20 ms
    await asyncio.wait_for(call.wait_playout(), 3)


async def test_flush_sends_clear_and_releases_the_wait():
    ws, call = make_call()
    await call.write_audio(bytes(8000))
    waiter = asyncio.create_task(call.wait_playout())
    await asyncio.sleep(0.02)
    await call.flush_playback()
    await asyncio.wait_for(waiter, 1)
    assert ws.sent[-1] == {"event": "clear", "streamSid": STREAM}


async def test_hangup_is_idempotent():
    ws, call = make_call()
    await call.hangup()
    await call.hangup()
    assert ws.closed == 1
    await call.write_audio(bytes(10))
    assert ws.sent == []


async def test_transfer_posts_a_dial_to_the_rest_api_and_hides_credentials(caplog):
    posted = []

    async def post(url, data, auth, timeout):
        posted.append((url, data, auth))
        return 200

    ws, call = make_call(account_sid=SID, auth_token=CANARY, transfer_to="+15551234567", http_post=post)
    with caplog.at_level(logging.DEBUG):
        assert await call.transfer("caller_request") is True
    url, data, auth = posted[0]
    assert url == f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Calls/{CALL}.json"
    assert data == {"Twiml": "<Response><Dial>+15551234567</Dial></Response>"}
    assert auth == (SID, CANARY)
    assert CANARY not in caplog.text


async def test_transfer_unsupported_or_failing_returns_false(caplog):
    _, bare = make_call()
    assert await bare.transfer("x") is False

    async def refuse(url, data, auth, timeout):
        return 401

    async def boom(url, data, auth, timeout):
        raise RuntimeError(CANARY)

    for post in (refuse, boom):
        _, call = make_call(account_sid=SID, auth_token=CANARY, transfer_to="+15551234567", http_post=post)
        with caplog.at_level(logging.DEBUG):
            assert await call.transfer("x") is False
    assert CANARY not in caplog.text


# ---- a simulated Twilio dialling the real server ----------------------------------------------------------------------------


async def run_server(**provider_kw):
    pytest.importorskip("websockets")
    provider = create_provider(ProviderSettings(endpoint=PUBLIC, password=CANARY, listen_port=0, **provider_kw))
    links = LinkRecorder()
    connector = TelephonyConnector(ConnectorSettings(), link_factory=links)
    task = asyncio.create_task(connector.run(provider))
    await wait_until(lambda: provider.bound_port is not None)  # type: ignore[attr-defined]
    return provider, links, task


def good_headers():
    return {"X-Twilio-Signature": twilio_signature(CANARY, PUBLIC)}


async def test_handshake_without_valid_signature_is_refused():
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    provider, links, task = await run_server()
    port = provider.bound_port  # type: ignore[attr-defined]
    for headers, path, status in (
        ({}, "/twilio/media", 403),
        ({"X-Twilio-Signature": twilio_signature("wrong", PUBLIC)}, "/twilio/media", 403),
        (good_headers(), "/other/path", 404),
    ):
        with pytest.raises(InvalidStatus) as info:
            await connect(f"ws://127.0.0.1:{port}{path}", additional_headers=headers)
        assert info.value.response.status_code == status
    assert links.links == []  # no session was ever opened toward the agent
    task.cancel()


async def test_simulated_twilio_call_end_to_end():
    from websockets.asyncio.client import connect

    provider, links, task = await run_server()
    async with connect(
        f"ws://127.0.0.1:{provider.bound_port}/twilio/media", additional_headers=good_headers()
    ) as twilio:  # type: ignore[attr-defined]
        await twilio.send(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
        await twilio.send(start_msg())
        await wait_until(lambda: links.links)
        link = links.links[0]
        pcm = encode_codec(tone(1000, 8000, 1.0), "mulaw")
        for i in range(0, len(pcm), 160):
            await twilio.send(media_msg(pcm[i : i + 160]))
        await wait_until(lambda: len(link.audio) > 4000, timeout=5)
        assert link.messages[0] == {"type": "hello", "sampleRate": 16000, "format": "pcm_s16le", "channels": 1}
        link.server_send(make_wav(tone(500, 22050, 0.2), 22050))
        got_audio = b""
        mark_name = None
        while mark_name is None:
            msg = json.loads(await asyncio.wait_for(twilio.recv(), 5))
            if msg["event"] == "media":
                got_audio += base64.b64decode(msg["media"]["payload"])
            elif msg["event"] == "mark":
                mark_name = msg["mark"]["name"]
        assert len(got_audio) > 1000 and abs(len(decode_codec(got_audio, "mulaw")) - 1600) < 200  # ~0.2 s at 8 kHz
        assert {"type": "playback_done"} not in link.messages  # Twilio has not yet said it finished playing
        await twilio.send(json.dumps({"event": "mark", "streamSid": STREAM, "mark": {"name": mark_name}}))
        await wait_until(lambda: {"type": "playback_done"} in link.messages, timeout=5)
        await twilio.send(json.dumps({"event": "stop", "streamSid": STREAM, "stop": {"callSid": CALL}}))
        await wait_until(lambda: link.closed, timeout=5)
    task.cancel()


async def test_wrong_media_format_or_account_is_refused():
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    provider, links, task = await run_server(username=SID)
    port = provider.bound_port  # type: ignore[attr-defined]
    for bad in (
        start_msg(mediaFormat={"encoding": "audio/L16", "sampleRate": 16000, "channels": 1}),
        start_msg(accountSid="ACother"),
    ):
        async with connect(f"ws://127.0.0.1:{port}/twilio/media", additional_headers=good_headers()) as twilio:
            await twilio.send(bad)
            with pytest.raises(ConnectionClosed):  # the server closes the connection
                await asyncio.wait_for(twilio.recv(), 3)
    assert links.links == []
    task.cancel()


async def test_two_simultaneous_calls_are_independent():
    from websockets.asyncio.client import connect

    provider, links, task = await run_server()
    url = f"ws://127.0.0.1:{provider.bound_port}/twilio/media"  # type: ignore[attr-defined]
    async with (
        connect(url, additional_headers=good_headers()) as a,
        connect(url, additional_headers=good_headers()) as b,
    ):
        await a.send(start_msg(streamSid="MZa", callSid="CAa"))
        await b.send(start_msg(streamSid="MZb", callSid="CAb"))
        await wait_until(lambda: len(links.links) == 2)
        links.links[0].server_send(make_wav(tone(500, 22050, 0.1), 22050))
        first = json.loads(await asyncio.wait_for(a.recv(), 5))
        assert first["event"] == "media" and first["streamSid"] == "MZa"
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(b.recv(), 0.3)  # the other call heard nothing
    task.cancel()
