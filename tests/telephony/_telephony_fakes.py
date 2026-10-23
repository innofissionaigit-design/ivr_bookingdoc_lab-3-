"""tests/telephony/_telephony_fakes.py -- in-memory stand-ins used by the telephony tests (no provider, no network, no agent).

What this file is for: the connector needs two things from the outside world -- a phone call (telephony.adapter.TelephonyCall)
and a WebSocket to the voice agent (telephony.agent_link.AgentLink). FakeCall and FakeLink are controllable fakes of each, plus
helpers to build WAV bytes and tone audio. Nothing here depends on Twilio, Zoiper, Asterisk, a phone number or main_pcm.py.
"""

from __future__ import annotations

import asyncio
import json
import math
import struct
import sys
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from telephony.adapter import AudioFormat, TelephonyCall  # noqa: E402
from telephony.agent_link import AgentLinkClosed  # noqa: E402
from telephony.audio import encode_codec  # noqa: E402

_CLOSE = object()


# sourav: tests need audio with a known frequency so they can prove what came out of the connector. Flow: a sine wave as int16.
def tone(freq_hz: float, rate: int, seconds: float, amplitude: float = 8000.0) -> np.ndarray:
    n = int(rate * seconds)
    return (amplitude * np.sin(2 * math.pi * freq_hz * np.arange(n) / rate)).astype(np.int16)


# sourav: the dominant frequency of a signal, used to check that audio survived conversion. Flow: FFT peak.
def dominant_freq(samples: np.ndarray, rate: int) -> float:
    spectrum = np.abs(np.fft.rfft(samples.astype(np.float64) * np.hanning(len(samples))))
    return float(np.argmax(spectrum) * rate / len(samples))


# sourav: the agent sends complete WAV files; tests need to fabricate them with any rate / width / channel count.
# Flow: write a standard 44-byte RIFF header by hand (so odd formats can be produced) followed by the sample data.
def make_wav(samples: np.ndarray, rate: int, bits: int = 16, channels: int = 1, float_format: bool = False) -> bytes:
    if float_format:
        payload = (samples.astype(np.float32) / 32768.0).astype("<f4").tobytes()
        tag, bits = 3, 32
    elif bits == 16:
        payload, tag = samples.astype("<i2").tobytes(), 1
    elif bits == 8:
        payload, tag = ((samples.astype(np.int32) >> 8) + 128).astype(np.uint8).tobytes(), 1
    elif bits == 24:
        wide = samples.astype(np.int32) << 8
        payload = b"".join(struct.pack("<i", int(v))[:3] for v in wide)
        tag = 1
    else:
        raise ValueError(bits)
    if channels > 1:
        frame = bits // 8
        payload = b"".join(payload[i * frame : (i + 1) * frame] * channels for i in range(len(samples)))
    block = channels * bits // 8
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


# sourav: tests must wait for something asynchronous to happen without sleeping a fixed time. Flow: poll the predicate.
async def wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


class FakeCall(TelephonyCall):
    """A controllable TelephonyCall. Tests push caller audio with feed() and inspect `written`, `flush_count`, `hangups`...

    hold_playout=True makes wait_playout() block until release_playout() (or a flush), which simulates audio that is still
    playing to the caller."""

    # sourav: configure the fake call. Flow: choose the provider audio format and behaviours (transfer result, held playout).
    def __init__(
        self,
        codec: str = "mulaw",
        sample_rate: int = 8000,
        hold_playout: bool = False,
        transfer_result: bool | None = None,
        caller_id: str | None = None,
    ):
        self._format = AudioFormat(codec, sample_rate)
        self._caller_id = caller_id
        self.inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.interrupts: asyncio.Queue[None] = asyncio.Queue()
        self.written: list[bytes] = []
        self.flush_count = 0
        self.hangups = 0
        self.transfers: list[str] = []
        self.transfer_result = transfer_result  # None = use the abstract default (unsupported)
        self.hold_playout = hold_playout
        self._released = asyncio.Event()

    @property
    def audio_format(self) -> AudioFormat:
        return self._format

    @property
    def caller_id(self) -> str | None:
        return self._caller_id

    # sourav: provider -> connector audio. Flow: wait for the next chunk the test fed (None = caller hung up).
    async def read_audio(self) -> bytes | None:
        return await self.inbound.get()

    # sourav: connector -> provider audio. Flow: record the clip in order.
    async def write_audio(self, audio: bytes) -> None:
        self.written.append(audio)

    # sourav: simulate "still playing". Flow: return immediately unless the test asked to hold playout.
    async def wait_playout(self) -> None:
        if self.hold_playout:
            await self._released.wait()

    # sourav: the connector asked playback to stop. Flow: count it and release anything waiting on playout.
    async def flush_playback(self) -> None:
        self.flush_count += 1
        self._released.set()

    # sourav: the test decides when the caller has "heard" everything. Flow: release the held wait_playout().
    def release_playout(self) -> None:
        self._released.set()

    # sourav: the provider-side "caller wants the floor" signal. Flow: block until the test fires one.
    async def wait_interrupt(self) -> None:
        await self.interrupts.get()

    # sourav: transfer is optional. Flow: with transfer_result unset, behave like an adapter that does not support it.
    async def transfer(self, reason: str) -> bool:
        self.transfers.append(reason)
        if self.transfer_result is None:
            return await super().transfer(reason)
        return self.transfer_result

    # sourav: count hang-ups so tests can check exactly-once clean-up. Flow: increment.
    async def hangup(self) -> None:
        self.hangups += 1

    # sourav: convenience for tests: push a tone as provider-format audio in 20 ms chunks. Flow: encode with the call's codec.
    def feed_tone(self, freq_hz: float, seconds: float, chunk_s: float = 0.02) -> None:
        rate = self._format.sample_rate
        samples = tone(freq_hz, rate, seconds)
        step = int(rate * chunk_s)
        for i in range(0, len(samples), step):
            self.inbound.put_nowait(encode_codec(samples[i : i + step], self._format.codec))


class FakeLink:
    """An in-memory AgentLink. Tests play the part of main_pcm.py with server_send()/server_close()."""

    # sourav: set up empty logs. Flow: `events` keeps everything the connector sent, in order, as ("text"|"bytes", payload).
    def __init__(self) -> None:
        self.events: list[tuple[str, str | bytes]] = []
        self.closed = False
        self._incoming: asyncio.Queue[object] = asyncio.Queue()

    @property
    def texts(self) -> list[str]:
        return [str(p) for kind, p in self.events if kind == "text"]

    @property
    def messages(self) -> list[dict]:
        return [json.loads(t) for t in self.texts]

    @property
    def audio(self) -> bytes:
        return b"".join(p for kind, p in self.events if kind == "bytes" and isinstance(p, bytes))

    # sourav: connector -> agent text frame. Flow: refuse if closed, else record.
    async def send_text(self, text: str) -> None:
        if self.closed:
            raise AgentLinkClosed("closed")
        self.events.append(("text", text))

    # sourav: connector -> agent audio frame. Flow: refuse if closed, else record.
    async def send_bytes(self, data: bytes) -> None:
        if self.closed:
            raise AgentLinkClosed("closed")
        self.events.append(("bytes", data))

    # sourav: agent -> connector. Flow: wait for the next frame the test sends; the close marker raises AgentLinkClosed.
    async def recv(self) -> str | bytes:
        item = await self._incoming.get()
        if item is _CLOSE:
            raise AgentLinkClosed("closed by agent")
        return item  # type: ignore[return-value]

    # sourav: connector closing its side. Flow: mark closed and wake any reader.
    async def close(self) -> None:
        self.closed = True
        self._incoming.put_nowait(_CLOSE)

    # sourav: the test speaks as the agent. Flow: queue a text or binary frame for recv().
    def server_send(self, message: str | bytes) -> None:
        self._incoming.put_nowait(message)

    # sourav: the agent hangs up the socket (end of call from its side). Flow: queue the close marker.
    def server_close(self) -> None:
        self._incoming.put_nowait(_CLOSE)


class LinkRecorder:
    """A LinkFactory that hands out one FakeLink per call and remembers them all."""

    # sourav: start with no links. Flow: `fail` makes the factory raise, to simulate an unreachable agent.
    def __init__(self, fail: bool = False) -> None:
        self.links: list[FakeLink] = []
        self.fail = fail

    # sourav: the connector awaits this once per call. Flow: create, remember and return a fresh link.
    async def __call__(self) -> FakeLink:
        if self.fail:
            raise OSError("agent unreachable")
        link = FakeLink()
        self.links.append(link)
        return link
