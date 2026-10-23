"""tests/telephony/test_telephony_adapter.py -- tests for telephony/adapter.py and telephony/loader.py.

What this file is for: proves the adapter interface is telephony-only (no agent vocabulary), that its defaults are safe
(no transfer, no interrupt, no caller ID), that AudioFormat rejects bad formats early, and that providers load by name.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _telephony_fakes import FakeCall  # noqa: E402

from telephony.adapter import AudioFormat, TelephonyCall, TelephonyProvider  # noqa: E402
from telephony.config import ConfigError, ProviderSettings  # noqa: E402
from telephony.loader import load_provider  # noqa: E402


def test_audio_format_validates():
    assert AudioFormat("mulaw", 8000).duration_s(8000) == 1.0
    assert AudioFormat("pcm_s16le", 16000).duration_s(32000) == 1.0
    with pytest.raises(ValueError):
        AudioFormat("opus", 8000)
    with pytest.raises(ValueError):
        AudioFormat("mulaw", 5)


def test_abstract_classes_cannot_be_instantiated():
    with pytest.raises(TypeError):
        TelephonyCall()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        TelephonyProvider()  # type: ignore[abstract]


def test_adapter_interface_has_no_agent_vocabulary():
    # the interface (every method/property docstring and name) must not mention the agent protocol
    members = [
        getattr(TelephonyCall, n)
        for n in ("read_audio", "write_audio", "wait_playout", "flush_playback", "hangup", "transfer")
    ]
    text = (
        " ".join((m.__doc__ or "") + m.__name__ for m in members)
        + " ".join(dir(TelephonyCall))
        + " ".join(dir(TelephonyProvider))
    )
    for word in ("playback_done", "stop_playback", "handoff_human", "ws/audio", "main_pcm", "twilio"):
        assert word not in text.lower()
    for name in ("read_audio", "write_audio", "wait_playout", "flush_playback", "hangup", "transfer", "caller_id"):
        assert hasattr(TelephonyCall, name)


async def test_defaults_are_safe():
    call = FakeCall()
    base = TelephonyCall
    assert await base.transfer(call, "x") is False
    assert base.caller_id.fget(call) is None  # type: ignore[attr-defined]
    waiter = asyncio.create_task(base.wait_interrupt(call))
    await asyncio.sleep(0.02)
    assert not waiter.done()  # never fires by default
    waiter.cancel()


def test_loader_loads_wavfile(tmp_path):
    from _telephony_fakes import make_wav, tone

    wav = tmp_path / "q.wav"
    wav.write_bytes(make_wav(tone(300, 16000, 0.1), 16000))
    provider = load_provider("wavfile", ProviderSettings(endpoint=str(wav)))
    assert isinstance(provider, TelephonyProvider)


def test_loader_rejects_bad_names_and_missing_providers():
    for bad in ("../x", "a.b", "Twilio Inc", ""):
        with pytest.raises(ConfigError):
            load_provider(bad, ProviderSettings())
    with pytest.raises(ConfigError):
        load_provider("does_not_exist", ProviderSettings())


def test_wavfile_requires_endpoint():
    with pytest.raises(ConfigError):
        load_provider("wavfile", ProviderSettings())
    with pytest.raises(ConfigError):
        load_provider("wavfile", ProviderSettings(endpoint="/no/such/file.wav"))
