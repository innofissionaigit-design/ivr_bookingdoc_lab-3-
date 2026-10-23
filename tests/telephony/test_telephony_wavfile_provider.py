"""tests/telephony/test_telephony_wavfile_provider.py -- tests the offline test adapter (providers/telephony/wavfile.py).

What this file is for: runs the whole chain WAV file -> adapter -> connector -> fake agent -> adapter -> reply WAV with no phone
provider and no real agent, proving the connector can be exercised end to end without any provider.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _telephony_fakes import LinkRecorder, make_wav, tone, wait_until  # noqa: E402

from providers.telephony import wavfile  # noqa: E402
from providers.telephony.wavfile import WavFileCall, WavFileProvider  # noqa: E402
from telephony.audio import parse_wav  # noqa: E402
from telephony.config import ConnectorSettings, ProviderSettings  # noqa: E402
from telephony.connector import TelephonyConnector  # noqa: E402


async def test_wavfile_call_is_real_time_then_hangs_up(tmp_path):
    samples = tone(400, 8000, 0.1)
    call = WavFileCall(samples, 8000, None, tail_s=0.04)
    chunks = []
    while (c := await call.read_audio()) is not None:
        chunks.append(c)
    assert len(b"".join(chunks)) >= len(samples) * 2
    await call.hangup()
    await call.hangup()  # idempotent
    assert await call.read_audio() is None


async def test_end_to_end_with_fake_agent(tmp_path):
    caller = tmp_path / "q.wav"
    caller.write_bytes(make_wav(tone(300, 16000, 0.3), 16000))
    provider = wavfile.create_provider(ProviderSettings(endpoint=str(caller)))
    assert isinstance(provider, WavFileProvider)
    links = LinkRecorder()
    connector = TelephonyConnector(ConnectorSettings(), link_factory=links)

    import asyncio

    runner = asyncio.create_task(connector.run(provider))
    await wait_until(lambda: links.links)
    link = links.links[0]
    await wait_until(lambda: len(link.audio) > 2000)
    link.server_send(make_wav(tone(600, 22050, 0.2), 22050))
    await wait_until(lambda: '{"type": "playback_done"}' in link.texts, timeout=5)
    link.server_close()
    await asyncio.wait_for(runner, 10)
    reply = caller.with_suffix(".reply.wav")
    assert reply.is_file()
    with wave.open(str(reply)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
    assert parse_wav(reply.read_bytes()).sample_rate == 16000
