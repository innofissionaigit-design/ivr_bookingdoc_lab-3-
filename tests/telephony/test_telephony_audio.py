"""tests/telephony/test_telephony_audio.py -- tests for telephony/audio.py (WAV parsing, resampling, G.711, continuous stream).

What this file is for: proves the audio layer is correct on its own, with plain numpy data: the sample rate really comes from
the WAV header, conversion preserves the signal, G.711 matches the standard's reference values, and gaps are filled in step with
the clock.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _telephony_fakes import dominant_freq, make_wav, tone  # noqa: E402

from telephony.audio import (  # noqa: E402
    AudioError,
    ContinuousPcmStream,
    PcmResampler,
    UplinkConverter,
    WavError,
    alaw_decode,
    alaw_encode,
    decode_codec,
    encode_codec,
    mulaw_decode,
    mulaw_encode,
    parse_wav,
    pcm16_from_bytes,
    pcm16_to_bytes,
    resample_pcm16,
    wav_to_provider_audio,
)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


# ------------------------------------------------------------------------------------------------------------ PCM16


# sourav: checks the agent's wire format -- little-endian int16 -- survives a bytes round trip and clips instead of wrapping.
def test_pcm16_round_trip_and_clipping():
    x = np.array([0, 1, -1, 32767, -32768], dtype=np.int16)
    assert pcm16_to_bytes(x) == x.astype("<i2").tobytes()
    assert np.array_equal(pcm16_from_bytes(pcm16_to_bytes(x)), x)
    assert np.array_equal(pcm16_from_bytes(pcm16_to_bytes(np.array([40000, -40000]))), [32767, -32768])


# sourav: half a sample means a corrupted chunk; it must be rejected, not silently misaligned.
def test_pcm16_odd_length_is_rejected():
    with pytest.raises(AudioError):
        pcm16_from_bytes(b"\x01\x02\x03")


# ------------------------------------------------------------------------------------------------------------- WAV


# sourav: the central requirement -- never assume the TTS sample rate. Every rate must come back exactly as written.
@pytest.mark.parametrize("rate", [8000, 16000, 22050, 24000, 44100])
def test_wav_sample_rate_is_read_from_the_header(rate):
    parsed = parse_wav(make_wav(tone(440, rate, 0.1), rate))
    assert parsed.sample_rate == rate
    assert parsed.samples.shape == (int(rate * 0.1),)
    assert parsed.samples.dtype == np.int16


# sourav: other encodings must decode to the same int16 signal (within each format's precision).
@pytest.mark.parametrize("kwargs", [{"bits": 8}, {"bits": 24}, {"float_format": True}])
def test_wav_other_encodings_decode_to_int16(kwargs):
    original = tone(440, 16000, 0.1)
    parsed = parse_wav(make_wav(original, 16000, **kwargs))
    tolerance = 300 if kwargs.get("bits") == 8 else 2
    assert np.abs(parsed.samples.astype(int) - original.astype(int)).max() <= tolerance


# sourav: stereo must be mixed to mono so the rest of the pipeline stays mono.
def test_wav_stereo_is_mixed_to_mono():
    original = tone(440, 16000, 0.1)
    parsed = parse_wav(make_wav(original, 16000, channels=2))
    assert parsed.source_channels == 2
    assert parsed.samples.shape == original.shape
    assert np.abs(parsed.samples.astype(int) - original.astype(int)).max() <= 1


# sourav: a streaming-style data size and a cut-off file are both playable as far as they go, with no partial trailing sample.
def test_wav_streaming_placeholder_and_truncated_data_are_tolerated():
    wav = bytearray(make_wav(tone(440, 16000, 0.1), 16000))
    wav[40:44] = b"\xff\xff\xff\xff"  # data size placeholder used by streaming writers
    assert parse_wav(bytes(wav)).samples.size == 1600
    assert parse_wav(bytes(wav[:-3])).samples.size == 1598  # cut mid-sample: the partial sample is dropped


# sourav: malformed audio must raise WavError (so the connector can drop one clip) rather than crash or return garbage.
@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"garbage",
        b"RIFF\x00\x00\x00\x00WAVE",
        b"RIFF\x04\x00\x00\x00WAVEfmt ",
        make_wav(tone(440, 16000, 0.01), 16000)[:30],
    ],
)
def test_wav_malformed_input_raises_wav_error(bad):
    with pytest.raises(WavError):
        parse_wav(bad)


# sourav: an encoding the connector cannot play (here IEEE format tag 2 = ADPCM) must be refused, not misread as PCM.
def test_wav_unsupported_encoding_is_refused():
    wav = bytearray(make_wav(tone(440, 16000, 0.01), 16000))
    wav[20:22] = b"\x02\x00"
    with pytest.raises(WavError):
        parse_wav(bytes(wav))


# ------------------------------------------------------------------------------------------------------- resampling


# sourav: conversion must preserve the signal -- same pitch, same loudness, exact output length -- for the rates this system
# really meets (8k phone, 16k agent, 22.05k/24k TTS).
@pytest.mark.parametrize(
    "src,dst,freq",
    [(8000, 16000, 1000), (16000, 8000, 1000), (22050, 8000, 1000), (24000, 8000, 500), (22050, 16000, 700)],
)
def test_resample_preserves_tone(src, dst, freq):
    x = tone(freq, src, 1.0)
    y = resample_pcm16(x, src, dst)
    assert len(y) == dst
    core = y[400:-400]
    assert abs(dominant_freq(core, dst) - freq) < 5
    assert abs(_rms(core) / _rms(tone(freq, dst, 1.0)[400:-400]) - 1.0) < 0.02


# sourav: converting down must remove what the lower rate cannot represent (aliasing) -- a 5 kHz tone sampled at 22.05 kHz cannot
# survive as an 8 kHz signal and must be strongly attenuated, not folded into the band.
def test_resample_down_attenuates_frequencies_above_the_new_nyquist():
    y = resample_pcm16(tone(5000, 22050, 1.0), 22050, 8000)
    assert _rms(y[400:-400]) < 0.02 * _rms(tone(5000, 22050, 1.0))


# sourav: live audio arrives in small chunks of any size; chunked conversion must equal one-shot conversion bit for bit,
# otherwise every chunk edge would click.
@pytest.mark.parametrize("src,dst,chunk", [(8000, 16000, 160), (22050, 8000, 777), (16000, 8000, 1)])
def test_streaming_resample_equals_one_shot(src, dst, chunk):
    x = tone(300, src, 0.5)
    one_shot = resample_pcm16(x, src, dst)
    resampler = PcmResampler(src, dst)
    parts = [resampler.push(x[i : i + chunk]) for i in range(0, len(x), chunk)] + [resampler.flush()]
    assert np.array_equal(np.concatenate(parts), one_shot)


# sourav: equal rates must be a no-op and invalid rates must be rejected up front.
def test_resample_same_rate_is_identity_and_bad_rate_is_rejected():
    x = tone(440, 16000, 0.1)
    assert np.array_equal(resample_pcm16(x, 16000, 16000), x)
    with pytest.raises(AudioError):
        PcmResampler(0, 16000)


# ------------------------------------------------------------------------------------------------------------ G.711


# sourav: reference values from ITU-T G.711 -- silence, positive full scale and negative full scale -- for both laws.
def test_mulaw_reference_values():
    assert mulaw_encode(np.array([0, 32767, -32768])) == bytes([0xFF, 0x80, 0x00])
    assert list(mulaw_decode(bytes([0xFF, 0x80, 0x00]))) == [0, 32124, -32124]


def test_alaw_reference_values():
    assert alaw_encode(np.array([0, 32767, -32768])) == bytes([0xD5, 0xAA, 0x2A])
    assert list(alaw_decode(bytes([0xD5, 0xAA, 0x2A]))) == [8, 32256, -32256]


# sourav: encode -> decode must reproduce speech-like audio within G.711's quantisation step (a few percent), and decode must be
# the exact inverse of encode on every possible code word.
@pytest.mark.parametrize("encode,decode", [(mulaw_encode, mulaw_decode), (alaw_encode, alaw_decode)])
def test_g711_round_trip(encode, decode):
    x = tone(440, 8000, 1.0, amplitude=12000)
    y = decode(encode(x))
    error = np.abs(y.astype(int) - x.astype(int))
    assert (error <= 0.04 * np.abs(x.astype(int)) + 140).all()
    codes = bytes(range(256))
    assert encode(decode(codes)) in (codes, encode(decode(encode(decode(codes)))))  # idempotent after one pass
    assert decode(encode(decode(codes))).tolist() == decode(codes).tolist()


# sourav: the codec dispatch must accept exactly the declared codecs and reject anything else.
def test_codec_dispatch():
    x = tone(440, 8000, 0.05)
    for codec in ("pcm_s16le", "mulaw", "alaw"):
        assert len(decode_codec(encode_codec(x, codec), codec)) == len(x)
    with pytest.raises(AudioError):
        decode_codec(b"\x00\x00", "opus")
    with pytest.raises(AudioError):
        encode_codec(x, "opus")


# ----------------------------------------------------------------------------------------- provider <-> agent converters


# sourav: uplink contract -- whatever the provider sends (mu-law @ 8 kHz here) comes out as 16 kHz mono PCM16LE bytes, with
# the right pitch and loudness.
def test_uplink_converter_output_is_agent_pcm():
    converter = UplinkConverter("mulaw", 8000)
    source = tone(1000, 8000, 1.0, amplitude=10000)
    out = b"".join(converter.convert(encode_codec(source[i : i + 160], "mulaw")) for i in range(0, len(source), 160))
    pcm = np.frombuffer(out, dtype="<i2")
    assert len(out) % 2 == 0
    assert 15_000 <= len(pcm) <= 16_000  # ~1 s at 16 kHz, minus the filter's start-up latency
    assert abs(dominant_freq(pcm[1000:9000], 16000) - 1000) < 10
    assert abs(_rms(pcm[1000:9000]) / _rms(tone(1000, 16000, 1.0, amplitude=10000)) - 1.0) < 0.05


# sourav: a provider that sends PCM16 at another rate (24 kHz here) must also be handled -- the adapter, not this module, declares it.
def test_uplink_converter_other_provider_rates():
    converter = UplinkConverter("pcm_s16le", 24000)
    out = converter.convert(pcm16_to_bytes(tone(600, 24000, 0.5)))
    assert abs(len(out) / 2 - 8000) < 40  # 0.5 s at 16 kHz
    with pytest.raises(AudioError):
        UplinkConverter("opus", 8000)


# sourav: downlink contract -- an agent WAV at ANY rate becomes provider bytes in the provider's codec and rate.
@pytest.mark.parametrize("wav_rate", [16000, 22050, 24000])
@pytest.mark.parametrize(
    "codec,rate,bytes_per_sample", [("mulaw", 8000, 1), ("alaw", 8000, 1), ("pcm_s16le", 16000, 2)]
)
def test_wav_to_provider_audio(wav_rate, codec, rate, bytes_per_sample):
    audio = wav_to_provider_audio(make_wav(tone(500, wav_rate, 1.0), wav_rate), codec, rate)
    assert len(audio) == rate * bytes_per_sample
    decoded = decode_codec(audio, codec)
    assert abs(dominant_freq(decoded[200:-200], rate) - 500) < 5


# ------------------------------------------------------------------------------------------------ continuous stream


class _Clock:
    # sourav: a manual clock so the gap-filling tests are exact and need no sleeping. Flow: start at 0, advance on demand.
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# sourav: audio that keeps up with the clock must pass through untouched -- no silence inserted.
def test_continuous_stream_passes_steady_audio_unchanged():
    clock = _Clock()
    stream = ContinuousPcmStream(clock=clock)
    block = bytes([1, 0]) * 320  # 20 ms at 16 kHz
    total = b""
    for _ in range(50):
        stream.push(block)
        clock.now += 0.02
        total += stream.pull()
    assert total == block * 50


# sourav: when the provider goes quiet (packet loss, voice-activity suppression) the stream must still advance with the clock:
# silence is inserted so the agent's timeline stays correct, and it is only silence (never repeated audio).
def test_continuous_stream_fills_gaps_with_silence():
    clock = _Clock()
    stream = ContinuousPcmStream(clock=clock)
    clock.now = 2.0  # two seconds with nothing from the provider
    out = stream.pull()
    assert len(out) // 2 >= 2.0 * 16000 - 2 * 512
    assert out == bytes(len(out))
    assert len(out) % (512 * 2) == 0  # whole frames


# sourav: after a gap, real audio resumes in the right place: silence first, then the data -- and the total length tracks time.
def test_continuous_stream_silence_precedes_late_audio():
    clock = _Clock()
    stream = ContinuousPcmStream(clock=clock)
    clock.now = 1.0
    data = bytes([7, 0]) * 320
    stream.push(data)
    out = stream.pull()
    assert out.endswith(data)
    assert out[: -len(data)] == bytes(len(out) - len(data))
    assert abs((len(out) // 2) - 16000) <= 512 + 320


# sourav: a burst (audio ahead of the clock) must never be dropped; it is passed through and later gaps are absorbed by it.
def test_continuous_stream_never_drops_burst_audio():
    clock = _Clock()
    stream = ContinuousPcmStream(clock=clock)
    burst = bytes([3, 0]) * 16000  # one second delivered at t=0
    stream.push(burst)
    assert stream.pull() == burst
    clock.now = 0.5
    assert stream.pull() == b""  # still ahead of the clock: nothing to add
