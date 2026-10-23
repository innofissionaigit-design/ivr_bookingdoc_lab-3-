"""telephony/audio.py -- audio helpers shared by the telephony connector and every provider adapter.

What this file is for
---------------------
The voice agent (main_pcm.py :: /ws/audio) speaks exactly ONE audio format on the way in -- mono, signed 16-bit
little-endian PCM at 16 kHz (agent/pcm_buffer.py) -- and sends replies back as COMPLETE WAV files whose sample rate must be
read from each file's own header (tts_server.py picks it per language). Phone providers speak something else, very often
G.711 at 8 kHz, but not always. Everything needed to translate between the two worlds lives here, with no provider names
and no network code, so it can be tested with plain numpy arrays:

  parse_wav             complete WAV message  -> mono int16 samples + the sample rate found in its header
  PcmResampler          windowed-sinc sample-rate conversion that is safe to call chunk by chunk (keeps its own history)
  mulaw_* / alaw_*      G.711 companding in numpy (the stdlib audioop module no longer exists in Python 3.13)
  UplinkConverter       provider audio chunks -> 16 kHz PCM bytes for the agent
  wav_to_provider_audio agent WAV clip        -> provider audio bytes (codec + sample rate the provider declared)
  ContinuousPcmStream   turns bursty / gappy caller audio into a gap-free stream whose length tracks the wall clock

Why a continuous stream matters: on the agent side the sample index IS the call timeline (agent/pcm_buffer.py), so
dropped packets or a provider that stops sending during silence would shift every later timestamp without any error.

Place your provider's audio details in the adapter, not here: nothing in this file knows which provider is in use.
"""

from __future__ import annotations

import math
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

# The format the voice agent requires on its input (agent/pcm_buffer.py: SAMPLE_RATE, BYTES_PER_SAMPLE).
AGENT_SAMPLE_RATE = 16_000
AGENT_BYTES_PER_SAMPLE = 2
# The browser client sends 512-sample blocks (static/pcm/pcm-worklet.js); the connector paces its uplink the same way.
UPLINK_FRAME_SAMPLES = 512

CODEC_PCM_S16LE = "pcm_s16le"
CODEC_MULAW = "mulaw"
CODEC_ALAW = "alaw"
SUPPORTED_CODECS = (CODEC_PCM_S16LE, CODEC_MULAW, CODEC_ALAW)


class AudioError(ValueError):
    """Audio bytes that cannot be interpreted (odd length, unknown codec, bad rate)."""


class WavError(AudioError):
    """A WAV message that is not a usable RIFF/WAVE file."""


# --------------------------------------------------------------------------------------------------------- PCM16 bytes


# sourav: provider chunks and WAV payloads arrive as bytes; the rest of this module works on int16 arrays.
# Flow: reject an odd byte count (half a sample means a corrupted or mis-framed chunk), then view as little-endian int16.
def pcm16_from_bytes(data: bytes) -> np.ndarray:
    if len(data) % AGENT_BYTES_PER_SAMPLE:
        raise AudioError(f"PCM16 data has an odd length ({len(data)} bytes)")
    return np.frombuffer(data, dtype="<i2").astype(np.int16)


# sourav: the agent wants raw little-endian int16 bytes on its socket.
# Flow: clip to the int16 range (never wrap around) and serialise little-endian.
def pcm16_to_bytes(samples: np.ndarray) -> bytes:
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


# ------------------------------------------------------------------------------------------------------------ WAV parse


@dataclass(frozen=True)
class WavAudio:
    """What parse_wav returns: mono int16 samples and the sample rate the file itself declared."""

    samples: np.ndarray
    sample_rate: int
    source_channels: int


# sourav: tts_server.py chooses the output rate per language (22.05 kHz fallback), so the connector must never assume it.
# Flow: check the RIFF/WAVE header, walk the chunks to find 'fmt ' and 'data', accept integer PCM (8/16/24/32 bit) and
# float (32/64 bit), tolerate WAVE_FORMAT_EXTENSIBLE and a streaming-style data size, drop a partial trailing frame, and
# mix multi-channel audio down to mono. Anything else raises WavError so one bad clip never crashes a call.
def parse_wav(data: bytes) -> WavAudio:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("not a RIFF/WAVE file")
    fmt: tuple[int, int, int, int] | None = None
    payload: bytes | None = None
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        (size,) = struct.unpack_from("<I", data, pos + 4)
        body = pos + 8
        if chunk_id == b"fmt ":
            if size < 16 or body + 16 > len(data):
                raise WavError("truncated fmt chunk")
            tag, channels, rate, _byte_rate, _align, bits = struct.unpack_from("<HHIIHH", data, body)
            if (
                tag == 0xFFFE
            ):  # WAVE_FORMAT_EXTENSIBLE: the real format tag is the first two bytes of the sub-format GUID
                if size < 26 or body + 26 > len(data):
                    raise WavError("truncated extensible fmt chunk")
                (tag,) = struct.unpack_from("<H", data, body + 24)
            fmt = (tag, channels, rate, bits)
        elif chunk_id == b"data":
            end = body + size
            if size == 0xFFFFFFFF or end > len(
                data
            ):  # streaming writers leave a placeholder; a cut-off file is played as far as it goes
                end = len(data)
            payload = data[body:end]
            break
        pos = body + size + (size & 1)  # RIFF chunks are padded to an even size
    if fmt is None:
        raise WavError("no fmt chunk")
    if payload is None:
        raise WavError("no data chunk")
    tag, channels, rate, bits = fmt
    if channels < 1 or channels > 8:
        raise WavError(f"unsupported channel count {channels}")
    if rate < 1000 or rate > 192_000:
        raise WavError(f"implausible sample rate {rate}")
    if bits % 8 or bits < 8:
        raise WavError(f"unsupported sample size {bits} bits")
    frame_bytes = (bits // 8) * channels
    payload = payload[: len(payload) - len(payload) % frame_bytes]
    if tag == 1 and bits in (8, 16, 24, 32):
        values = _int_pcm_to_int16(payload, bits)
    elif tag == 3 and bits in (32, 64):
        floats = np.frombuffer(payload, dtype="<f4" if bits == 32 else "<f8")
        values = np.rint(np.clip(np.nan_to_num(floats), -1.0, 1.0) * 32767.0).astype(np.int16)
    else:
        raise WavError(f"unsupported WAV encoding (format tag {tag}, {bits} bit)")
    if channels > 1:
        values = np.rint(values.reshape(-1, channels).astype(np.int32).mean(axis=1)).astype(np.int16)
    return WavAudio(samples=values, sample_rate=rate, source_channels=channels)


# sourav: helper for parse_wav -- integer WAV samples come in four widths but the connector only works in int16.
# Flow: decode each width to int32, then shift/scale down to the int16 range.
def _int_pcm_to_int16(payload: bytes, bits: int) -> np.ndarray:
    if bits == 16:
        return np.frombuffer(payload, dtype="<i2").astype(np.int16)
    if bits == 8:  # 8-bit WAV is unsigned, centred on 128
        return ((np.frombuffer(payload, dtype=np.uint8).astype(np.int32) - 128) << 8).astype(np.int16)
    if bits == 24:
        raw = np.frombuffer(payload, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        value = raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
        value = np.where(value & 0x800000, value - 0x1000000, value)
        return (value >> 8).astype(np.int16)
    return (np.frombuffer(payload, dtype="<i4").astype(np.int64) >> 16).astype(np.int16)


# ----------------------------------------------------------------------------------------------------------- resampling


class PcmResampler:
    """Windowed-sinc (Kaiser) sample-rate converter for mono int16 audio, usable one chunk at a time.

    Chosen over linear interpolation because linear interpolation aliases and dulls speech (agent/telephony_augment.py
    itself calls it "not for production playback"), and over scipy because nothing here may add a dependency. State
    (the last few input samples and the position of the next output sample) is kept between push() calls, so chunked
    conversion gives bit-identical output to converting the whole signal at once. Latency is ~16 input samples."""

    _HALF_CROSSINGS = 16  # zero crossings of the sinc on each side of the output point
    _KAISER_BETA = 8.0
    _BLOCK = 4096  # outputs computed per vectorised step (bounds memory)

    # sourav: the connector converts 8 kHz phone audio up to 16 kHz and 22.05 kHz TTS audio down to 8 kHz, always the same
    # way per call, so a small stateful object is the natural unit. Flow: pre-compute the kernel width from the rate
    # ratio, start with zero history so the first outputs see silence before the call began.
    def __init__(self, src_rate: int, dst_rate: int):
        if src_rate <= 0 or dst_rate <= 0:
            raise AudioError(f"sample rates must be positive (got {src_rate} -> {dst_rate})")
        self.src_rate = src_rate
        self.dst_rate = dst_rate
        self._passthrough = src_rate == dst_rate
        self._cutoff = min(1.0, dst_rate / src_rate)  # low-pass at the lower of the two Nyquist frequencies
        self._half_width = self._HALF_CROSSINGS / self._cutoff  # kernel half width, in INPUT samples
        self._reach = math.ceil(self._half_width)
        self._offsets = np.arange(2 * self._reach, dtype=np.int64)
        self._buf = np.zeros(self._reach + 1, dtype=np.float64)
        self._buf_offset = -(self._reach + 1)  # absolute input index of _buf[0]
        self._out_count = 0  # outputs produced so far
        self._total_in = 0
        self._i0_beta = float(np.i0(self._KAISER_BETA))

    # sourav: produce output samples n = _out_count .. last_n, the only place the filter maths happens.
    # Flow: for each block, find each output's position in input time, gather the surrounding input samples, weight them
    # with the Kaiser-windowed sinc (normalised so DC gain is exactly 1), sum, round to int16.
    def _emit(self, last_n: int) -> np.ndarray:
        if last_n < self._out_count:
            return np.zeros(0, dtype=np.int16)
        pieces = []
        for start in range(self._out_count, last_n + 1, self._BLOCK):
            n = np.arange(start, min(start + self._BLOCK, last_n + 1), dtype=np.float64)
            t = (n * self.src_rate) / self.dst_rate  # exact when the position is a whole input sample
            base = np.floor(t).astype(np.int64) - self._reach + 1
            idx = base[:, None] + self._offsets[None, :]
            x = self._buf[idx - self._buf_offset]
            d = idx - t[:, None]
            arg = np.clip(d / self._half_width, -1.0, 1.0)
            window = np.i0(self._KAISER_BETA * np.sqrt(1.0 - arg * arg)) / self._i0_beta
            weights = self._cutoff * np.sinc(self._cutoff * d) * np.where(np.abs(d) < self._half_width, window, 0.0)
            pieces.append((x * weights).sum(axis=1) / weights.sum(axis=1))
        self._out_count = last_n + 1
        return np.clip(np.rint(np.concatenate(pieces)), -32768, 32767).astype(np.int16)

    # sourav: feed one more chunk of input audio and get back every output sample that is now fully determined.
    # Flow: append, compute how many outputs have all their input neighbours available (integer maths, no float drift),
    # emit them, then drop input history that no later output can reach.
    def push(self, pcm: np.ndarray) -> np.ndarray:
        x = np.asarray(pcm)
        if self._passthrough:
            return x.astype(np.int16)
        self._total_in += x.size
        self._buf = np.concatenate([self._buf, x.astype(np.float64)])
        newest = self._buf_offset + self._buf.size - 1  # absolute index of the newest sample we hold
        last_n = (((newest - self._reach) + 1) * self.dst_rate - 1) // self.src_rate
        out = self._emit(last_n)
        keep_from = (self._out_count * self.src_rate) // self.dst_rate - self._reach - 1
        if keep_from > self._buf_offset:
            self._buf = self._buf[keep_from - self._buf_offset :]
            self._buf_offset = keep_from
        return out

    # sourav: after the last chunk the filter still owes the tail of the signal; without this a clip would end early.
    # Flow: pad with zeros, emit up to the exact expected output length (round(total_in * ratio)).
    def flush(self) -> np.ndarray:
        if self._passthrough:
            return np.zeros(0, dtype=np.int16)
        expected = int(round(self._total_in * self.dst_rate / self.src_rate))
        self._buf = np.concatenate([self._buf, np.zeros(self._reach + 2, dtype=np.float64)])
        return self._emit(expected - 1)


# sourav: one-shot conversion for a whole TTS clip (it arrives complete, unlike the live caller audio).
# Flow: new resampler, push everything, flush the tail, join.
def resample_pcm16(pcm: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    resampler = PcmResampler(src_rate, dst_rate)
    return np.concatenate([resampler.push(pcm), resampler.flush()]).astype(np.int16)


# ------------------------------------------------------------------------------------------------------------- G.711


_MULAW_BIAS = 0x84
_MULAW_CLIP = 32635
_ALAW_SEGMENT_END = np.array([0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF], dtype=np.int32)
_MULAW_EXPONENT = np.array([max(0, i.bit_length() - 1) for i in range(256)], dtype=np.int32)


# sourav: build the mu-law decode table once (256 possible bytes) so decoding a chunk is a single table lookup.
# Flow: invert the byte, split sign/exponent/mantissa, rebuild the linear value per ITU-T G.711.
def _build_mulaw_decode_table() -> np.ndarray:
    u = (~np.arange(256, dtype=np.int32)) & 0xFF
    sample = (((u & 0x0F) << 3) + _MULAW_BIAS) << ((u >> 4) & 0x07)
    sample = sample - _MULAW_BIAS
    return np.where(u & 0x80, -sample, sample).astype(np.int16)


# sourav: same idea for A-law (the other G.711 variant, standard in Europe and much of the world outside North America).
# Flow: XOR 0x55, split segment/mantissa, expand per ITU-T G.711.
def _build_alaw_decode_table() -> np.ndarray:
    a = np.arange(256, dtype=np.int32) ^ 0x55
    t = (a & 0x0F) << 4
    seg = (a & 0x70) >> 4
    t = np.where(seg == 0, t + 8, np.where(seg == 1, t + 0x108, (t + 0x108) << np.maximum(seg - 1, 0)))
    return np.where(a & 0x80, t, -t).astype(np.int16)


_MULAW_DECODE = _build_mulaw_decode_table()
_ALAW_DECODE = _build_alaw_decode_table()


# sourav: phone providers that use mu-law (North America, Japan) send one byte per sample.
# Flow: look every byte up in the pre-built table.
def mulaw_decode(data: bytes) -> np.ndarray:
    return _MULAW_DECODE[np.frombuffer(data, dtype=np.uint8)]


# sourav: the reverse direction, for sending the agent's voice to a mu-law provider.
# Flow: clip to the G.711 maximum, add the bias, find the exponent from a table, pack sign/exponent/mantissa, invert.
def mulaw_encode(pcm: np.ndarray) -> bytes:
    x = np.asarray(pcm).astype(np.int32)
    sign = np.where(x < 0, 0x80, 0)
    magnitude = np.minimum(np.abs(x), _MULAW_CLIP) + _MULAW_BIAS
    exponent = _MULAW_EXPONENT[(magnitude >> 7) & 0xFF]
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return ((~(sign | (exponent << 4) | mantissa)) & 0xFF).astype(np.uint8).tobytes()


# sourav: A-law counterpart of mulaw_decode.
# Flow: table lookup.
def alaw_decode(data: bytes) -> np.ndarray:
    return _ALAW_DECODE[np.frombuffer(data, dtype=np.uint8)]


# sourav: A-law counterpart of mulaw_encode.
# Flow: drop to 13-bit precision, find the segment (which power-of-two range the value falls in), pack segment/mantissa,
# apply the sign mask and the even-bit inversion G.711 A-law requires.
def alaw_encode(pcm: np.ndarray) -> bytes:
    x = np.asarray(pcm).astype(np.int32) >> 3
    negative = x < 0
    mask = np.where(negative, 0x55, 0xD5)
    v = np.where(negative, -x - 1, x)
    segment = np.searchsorted(_ALAW_SEGMENT_END, v, side="left")
    mantissa = np.where(segment < 2, (v >> 1) & 0x0F, (v >> np.minimum(segment, 7)) & 0x0F)
    value = np.where(segment >= 8, 0x7F, (segment << 4) | mantissa)
    return ((value ^ mask) & 0xFF).astype(np.uint8).tobytes()


# sourav: the connector asks "decode whatever the provider declared" without caring which codec it is.
# Flow: dispatch on the codec name; PCM16 is validated for odd lengths; unknown names fail loudly.
def decode_codec(data: bytes, codec: str) -> np.ndarray:
    if codec == CODEC_PCM_S16LE:
        return pcm16_from_bytes(data)
    if codec == CODEC_MULAW:
        return mulaw_decode(data)
    if codec == CODEC_ALAW:
        return alaw_decode(data)
    raise AudioError(f"unsupported codec {codec!r}")


# sourav: mirror of decode_codec for the agent -> caller direction.
# Flow: dispatch on the codec name and return provider-ready bytes.
def encode_codec(pcm: np.ndarray, codec: str) -> bytes:
    if codec == CODEC_PCM_S16LE:
        return pcm16_to_bytes(pcm)
    if codec == CODEC_MULAW:
        return mulaw_encode(pcm)
    if codec == CODEC_ALAW:
        return alaw_encode(pcm)
    raise AudioError(f"unsupported codec {codec!r}")


# ------------------------------------------------------------------------------------ provider <-> agent conversions


class UplinkConverter:
    """Caller audio from the provider (declared codec + rate) -> the agent's format (mono PCM16LE @ 16 kHz).

    One instance per call: the resampler inside carries filter history from chunk to chunk."""

    # sourav: each call needs its own resampler state, so the converter is created per call.
    # Flow: remember the provider's declared codec and build the matching resampler.
    def __init__(self, codec: str, sample_rate: int):
        if codec not in SUPPORTED_CODECS:
            raise AudioError(f"unsupported codec {codec!r}")
        self.codec = codec
        self._resampler = PcmResampler(sample_rate, AGENT_SAMPLE_RATE)

    # sourav: called for every chunk the provider delivers.
    # Flow: decode the codec to int16, resample to 16 kHz (state carried over), return little-endian bytes.
    def convert(self, data: bytes) -> bytes:
        return pcm16_to_bytes(self._resampler.push(decode_codec(data, self.codec)))


# sourav: the agent's reply is a whole WAV; the provider wants its own codec/rate, so this is the one place that bridges them.
# Flow: parse the WAV (rate from its header), resample to the provider's rate, encode with the provider's codec.
def wav_to_provider_audio(wav: bytes, codec: str, sample_rate: int) -> bytes:
    parsed = parse_wav(wav)
    return encode_codec(resample_pcm16(parsed.samples, parsed.sample_rate, sample_rate), codec)


# ---------------------------------------------------------------------------------------------------- continuous uplink


class ContinuousPcmStream:
    """Makes caller audio a gap-free stream whose total length follows the wall clock.

    The agent treats the number of samples received as elapsed call time. If the provider sends in bursts, skips packets
    or stops sending during silence (voice-activity suppression), the stream falls behind the clock; this object puts
    digital silence back where audio is missing so every later timestamp stays correct. Audio that arrives early (a
    burst) is passed through untouched -- speech is never dropped.

    Usage: push() whenever converted audio arrives; pull() on a fixed tick and send whatever it returns."""

    # sourav: built per call; the clock is injectable so tests do not have to sleep. Flow: remember the start time and
    # how far behind real time the stream may drift (gap_frames frames) before silence is inserted.
    def __init__(
        self,
        sample_rate: int = AGENT_SAMPLE_RATE,
        frame_samples: int = UPLINK_FRAME_SAMPLES,
        gap_frames: int = 2,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._rate = sample_rate
        self._frame = frame_samples
        self._threshold = gap_frames * frame_samples
        self._clock = clock
        self._t0 = clock()
        self._sent = 0  # samples handed out so far, including inserted silence
        self._pending = bytearray()

    # sourav: the connector's reader task calls this for every converted chunk. Flow: just queue the bytes.
    def push(self, pcm: bytes) -> None:
        self._pending += pcm

    # sourav: the connector's sender task calls this on every tick. Flow: take all whole samples queued, compare the
    # total sent so far with what the wall clock says should have been sent, and if the stream is more than the
    # threshold behind, put whole frames of silence in front of the data to catch up.
    def pull(self) -> bytes:
        usable = len(self._pending) - (len(self._pending) % AGENT_BYTES_PER_SAMPLE)
        data = bytes(self._pending[:usable])
        del self._pending[:usable]
        data_samples = usable // AGENT_BYTES_PER_SAMPLE
        expected = int((self._clock() - self._t0) * self._rate)
        behind = expected - self._sent - data_samples
        fill = (behind // self._frame) * self._frame if behind >= self._threshold else 0
        self._sent += fill + data_samples
        return bytes(fill * AGENT_BYTES_PER_SAMPLE) + data
