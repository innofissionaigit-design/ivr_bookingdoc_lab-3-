"""tests/telephony/test_telephony_twilio_auth.py -- Twilio credential handling: which secret does which job.

What this file is for: Twilio has two credential pairs that look interchangeable and are not. The Auth Token is the ONLY thing
that can verify an inbound X-Twilio-Signature; an API Key SID ("SK...") + secret can only authenticate REST calls. Both SIDs are
34 characters, so pasting one into the other's variable is the likeliest misconfiguration. These tests pin that down, plus the
explicit development override that allows a first test call before the Auth Token is available.

No real Twilio is involved. See tests/telephony/test_telephony_twilio.py for the message-level adapter tests.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from http import HTTPStatus  # noqa: E402

from providers.telephony.twilio import (  # noqa: E402
    TwilioCall,
    TwilioProvider,
    create_provider,
    twilio_signature,
)
from telephony.config import ConfigError, ProviderSettings  # noqa: E402

TOKEN = "canary-auth-token-123"
ACCOUNT_SID = "AC00000000000000000000000000000000"
KEY_SID = "SK11111111111111111111111111111111"
KEY_SECRET = "canary-api-key-secret-456"
PUBLIC = "wss://voice.example.com/twilio/media"
STREAM = "MZ0000000000000000000000000000000a"
CALL = "CA0000000000000000000000000000000a"

NO_ENV: dict[str, str] = {}
ALLOW_ENV = {"TELEPHONY_TWILIO_ALLOW_UNVERIFIED": "1"}


class FakeClosed(Exception):
    pass


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    async def close(self, code: int | None = None) -> None:
        self.closed = True


class Recorder:
    """Stands in for the one REST POST the hand-off makes."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[tuple[str, dict[str, str], tuple[str, str], float]] = []

    async def __call__(self, url, data, auth, timeout):  # noqa: ANN001, ANN204
        self.calls.append((url, data, auth, timeout))
        return self.status


def make_call(**over) -> tuple[FakeWS, TwilioCall]:
    ws = FakeWS()
    kwargs: dict = {"account_sid": ACCOUNT_SID, "transfer_to": "+15551234567"}
    kwargs.update(over)
    return ws, TwilioCall(ws, STREAM, CALL, FakeClosed, **kwargs)


class Req:
    def __init__(self, path: str, signature: str | None) -> None:
        self.path = path
        self.headers = {} if signature is None else {"X-Twilio-Signature": signature}


class Conn:
    def respond(self, status, text):  # noqa: ANN001, ANN204
        return (status, text)


# --- which credentials sign the hand-off REST call ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handoff_uses_the_api_key_pair_when_it_is_configured():
    post = Recorder()
    _, call = make_call(api_key=KEY_SID, api_secret=KEY_SECRET, http_post=post)
    assert await call.transfer("human") is True
    url, data, auth, _ = post.calls[0]
    assert auth == (KEY_SID, KEY_SECRET)
    # the Account SID still identifies the account in the REST path, even when an API key authenticates the request
    assert url == f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/Calls/{CALL}.json"
    assert data["Twiml"] == "<Response><Dial>+15551234567</Dial></Response>"


@pytest.mark.asyncio
async def test_handoff_falls_back_to_account_sid_and_auth_token():
    post = Recorder()
    _, call = make_call(auth_token=TOKEN, http_post=post)
    assert await call.transfer("human") is True
    assert post.calls[0][2] == (ACCOUNT_SID, TOKEN)


@pytest.mark.asyncio
async def test_api_key_is_preferred_so_the_auth_token_need_not_be_deployed():
    post = Recorder()
    _, call = make_call(auth_token=TOKEN, api_key=KEY_SID, api_secret=KEY_SECRET, http_post=post)
    assert await call.transfer("human") is True
    assert post.calls[0][2] == (KEY_SID, KEY_SECRET)


@pytest.mark.asyncio
async def test_handoff_without_any_rest_credentials_is_refused_not_attempted():
    post = Recorder()
    _, call = make_call(http_post=post)
    assert await call.transfer("human") is False
    assert post.calls == []


@pytest.mark.asyncio
async def test_a_half_configured_api_key_is_not_used_as_credentials():
    post = Recorder()
    _, call = make_call(api_key=KEY_SID, http_post=post)  # secret missing
    assert await call.transfer("human") is False
    assert post.calls == []


# --- the two SIDs are not interchangeable -------------------------------------------------------------------------------


def test_an_api_key_sid_in_the_username_names_the_mistake():
    with pytest.raises(ConfigError, match="API Key SID") as info:
        create_provider(ProviderSettings(endpoint=PUBLIC, password=TOKEN, username=KEY_SID), env=NO_ENV)
    message = str(info.value)
    assert "TELEPHONY_API_KEY" in message and "AC" in message
    assert TOKEN not in message


def test_an_account_sid_in_the_api_key_names_the_mistake():
    with pytest.raises(ConfigError, match="Account SID") as info:
        create_provider(
            ProviderSettings(endpoint=PUBLIC, password=TOKEN, api_key=ACCOUNT_SID, api_secret=KEY_SECRET), env=NO_ENV
        )
    assert KEY_SECRET not in str(info.value)


def test_a_malformed_account_sid_is_refused():
    with pytest.raises(ConfigError, match="Account SID"):
        create_provider(ProviderSettings(endpoint=PUBLIC, password=TOKEN, username="AC123"), env=NO_ENV)


def test_api_key_and_secret_must_be_set_together():
    with pytest.raises(ConfigError, match="go together"):
        create_provider(ProviderSettings(endpoint=PUBLIC, password=TOKEN, api_key=KEY_SID), env=NO_ENV)
    with pytest.raises(ConfigError, match="go together"):
        create_provider(ProviderSettings(endpoint=PUBLIC, password=TOKEN, api_secret=KEY_SECRET), env=NO_ENV)
    assert create_provider(
        ProviderSettings(endpoint=PUBLIC, password=TOKEN, api_key=KEY_SID, api_secret=KEY_SECRET), env=NO_ENV
    )


def test_an_api_key_is_not_accepted_in_place_of_the_auth_token():
    """The whole point: API key present, Auth Token absent -> still refused, and the message explains why."""
    with pytest.raises(ConfigError) as info:
        create_provider(
            ProviderSettings(endpoint=PUBLIC, api_key=KEY_SID, api_secret=KEY_SECRET), env=NO_ENV
        )
    message = str(info.value)
    assert "Auth Token" in message
    assert "API Key secret cannot" in message
    assert KEY_SECRET not in message


# --- hand-off credential requirements at configuration time -------------------------------------------------------------


def test_handoff_may_be_configured_with_an_api_key_and_no_auth_token():
    provider = create_provider(
        ProviderSettings(
            endpoint=PUBLIC,
            username=ACCOUNT_SID,
            api_key=KEY_SID,
            api_secret=KEY_SECRET,
            transfer_to="+15551234567",
        ),
        env=ALLOW_ENV,
    )
    assert isinstance(provider, TwilioProvider)


def test_handoff_without_rest_credentials_is_refused_at_startup():
    with pytest.raises(ConfigError, match="REST credentials"):
        create_provider(
            ProviderSettings(endpoint=PUBLIC, username=ACCOUNT_SID, transfer_to="+15551234567"), env=ALLOW_ENV
        )


# --- the development override ------------------------------------------------------------------------------------------


def test_without_the_override_a_missing_auth_token_stops_startup():
    with pytest.raises(ConfigError) as info:
        create_provider(ProviderSettings(endpoint=PUBLIC), env=NO_ENV)
    assert "TELEPHONY_TWILIO_ALLOW_UNVERIFIED" in str(info.value)


def test_the_override_is_announced_loudly_at_startup(caplog):
    with caplog.at_level(logging.WARNING, logger="telephony.provider.twilio"):
        provider = create_provider(ProviderSettings(endpoint=PUBLIC), env=ALLOW_ENV)
    assert isinstance(provider, TwilioProvider)
    assert "WITHOUT signature verification" in caplog.text


@pytest.mark.parametrize("value", ["", "0", "no", "false", "off", "place your flag here"])
def test_the_override_is_off_unless_explicitly_enabled(value):
    with pytest.raises(ConfigError):
        create_provider(ProviderSettings(endpoint=PUBLIC), env={"TELEPHONY_TWILIO_ALLOW_UNVERIFIED": value})


def test_with_the_override_an_unsigned_handshake_is_accepted(caplog):
    provider = create_provider(ProviderSettings(endpoint=PUBLIC), env=ALLOW_ENV)
    with caplog.at_level(logging.WARNING, logger="telephony.provider.twilio"):
        assert provider._process_request(Conn(), Req("/twilio/media", None)) is None
    assert "UNVERIFIED" in caplog.text


def test_the_override_does_not_disable_checking_when_a_token_is_configured():
    """An Auth Token always wins: the override only covers having no token at all, so a bad signature is still refused."""
    provider = create_provider(ProviderSettings(endpoint=PUBLIC, password=TOKEN), env=ALLOW_ENV)
    assert provider._process_request(Conn(), Req("/twilio/media", None))[0] == HTTPStatus.FORBIDDEN
    assert provider._process_request(Conn(), Req("/twilio/media", "AAAA="))[0] == HTTPStatus.FORBIDDEN
    good = twilio_signature(TOKEN, PUBLIC)
    assert provider._process_request(Conn(), Req("/twilio/media", good)) is None


def test_the_override_does_not_open_other_paths():
    provider = create_provider(ProviderSettings(endpoint=PUBLIC), env=ALLOW_ENV)
    assert provider._process_request(Conn(), Req("/not-the-stream", None))[0] == HTTPStatus.NOT_FOUND


# --- the override, end to end through the real connector ----------------------------------------------------------------
# This is the mode a first live test call uses before the Auth Token is in place, so it gets the same real-server treatment
# as the signed path in test_telephony_twilio.py: a real WebSocket, the unmodified connector, audio both ways.


@pytest.mark.asyncio
async def test_an_unverified_call_reaches_the_agent_and_is_answered():
    pytest.importorskip("websockets")
    import asyncio
    import base64
    import json

    from websockets.asyncio.client import connect

    from _telephony_fakes import LinkRecorder, make_wav, tone, wait_until
    from telephony.audio import decode_codec, encode_codec
    from telephony.config import ConnectorSettings
    from telephony.connector import TelephonyConnector

    provider = create_provider(ProviderSettings(endpoint=PUBLIC, listen_port=0), env=ALLOW_ENV)
    links = LinkRecorder()
    connector = TelephonyConnector(ConnectorSettings(), link_factory=links)
    task = asyncio.create_task(connector.run(provider))
    await wait_until(lambda: provider.bound_port is not None)

    start = json.dumps(
        {
            "event": "start",
            "streamSid": STREAM,
            "start": {
                "accountSid": ACCOUNT_SID,
                "streamSid": STREAM,
                "callSid": CALL,
                "tracks": ["inbound"],
                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            },
        }
    )
    try:
        # no X-Twilio-Signature header at all: accepted only because the override is on
        async with connect(f"ws://127.0.0.1:{provider.bound_port}/twilio/media") as twilio:
            await twilio.send(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
            await twilio.send(start)
            await wait_until(lambda: links.links)
            link = links.links[0]

            # caller speaks -> the agent receives it
            caller = encode_codec(tone(1000, 8000, 1.0), "mulaw")
            for i in range(0, len(caller), 160):
                await twilio.send(
                    json.dumps(
                        {
                            "event": "media",
                            "streamSid": STREAM,
                            "media": {"track": "inbound", "payload": base64.b64encode(caller[i : i + 160]).decode()},
                        }
                    )
                )
            await wait_until(lambda: len(link.audio) > 4000, timeout=5)
            assert link.messages[0] == {"type": "hello", "sampleRate": 16000, "format": "pcm_s16le", "channels": 1}

            # the agent answers -> Twilio is sent mu-law audio, then a mark
            link.server_send(make_wav(tone(500, 22050, 0.2), 22050))
            reply = b""
            mark_name = None
            while mark_name is None:
                message = json.loads(await asyncio.wait_for(twilio.recv(), 5))
                if message["event"] == "media":
                    reply += base64.b64decode(message["media"]["payload"])
                elif message["event"] == "mark":
                    mark_name = message["mark"]["name"]
            assert abs(len(decode_codec(reply, "mulaw")) - 1600) < 200  # ~0.2 s at 8 kHz
            await twilio.send(json.dumps({"event": "mark", "streamSid": STREAM, "mark": {"name": mark_name}}))
            await wait_until(lambda: {"type": "playback_done"} in link.messages, timeout=5)
    finally:
        task.cancel()
