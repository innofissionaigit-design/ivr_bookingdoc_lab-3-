"""telephony/adapter.py -- the provider-neutral telephony interface (what a provider adapter must implement).

What this file is for
---------------------
The connector (telephony/connector.py) bridges a phone call to the voice agent. It must not know which phone system the
call came from, so everything it needs from the outside world is expressed here as two small abstract classes:

  TelephonyProvider  listens for / accepts calls and hands each one to the connector as a TelephonyCall
  TelephonyCall      one live call: caller audio in, agent audio out, hangup, optional transfer, optional caller ID

These classes describe TELEPHONY operations only. They deliberately know nothing about main_pcm.py, /ws/audio,
`playback_done`, `stop_playback`, `handoff_human` or any other agent message: translating those is the connector's job.

Everything provider-specific (SIP, a cloud voice API, a PBX socket, signature checks, credentials, call transfer commands)
lives in providers/telephony/<name>.py, never here and never in the connector.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from telephony.audio import CODEC_PCM_S16LE, SUPPORTED_CODECS


@dataclass(frozen=True)
class AudioFormat:
    """The mono audio format a provider uses on its side of the call (both directions).

    codec        "pcm_s16le" (16-bit little-endian PCM), "mulaw" (G.711 mu-law) or "alaw" (G.711 A-law)
    sample_rate  samples per second as the provider sends and expects them (G.711 telephony is usually 8000)
    """

    codec: str
    sample_rate: int

    # sourav: fail at adapter start-up, not in the middle of a call, if an adapter declares a format the connector
    # cannot convert. Flow: check the codec name and a plausible sample-rate range.
    def __post_init__(self) -> None:
        if self.codec not in SUPPORTED_CODECS:
            raise ValueError(f"unsupported codec {self.codec!r}; expected one of {SUPPORTED_CODECS}")
        if not 1000 <= self.sample_rate <= 192_000:
            raise ValueError(f"implausible sample rate {self.sample_rate}")

    # sourav: adapters that pace audio in real time need to know how long a chunk lasts. Flow: bytes / bytes-per-second.
    def duration_s(self, num_bytes: int) -> float:
        bytes_per_sample = 2 if self.codec == CODEC_PCM_S16LE else 1
        return num_bytes / (bytes_per_sample * self.sample_rate)


class TelephonyCall(ABC):
    """One live phone call, as seen by the connector. One instance per call; never shared between calls.

    Playback contract: write_audio() hands audio to the provider IN ORDER; wait_playout() returns when everything written
    so far has actually been played to the caller (or, for providers with no playout notification, when it is safe to
    assume so -- document the assumption in the adapter); flush_playback() discards audio not yet played.
    """

    @property
    @abstractmethod
    def audio_format(self) -> AudioFormat:
        """Codec and sample rate used by read_audio() and write_audio()."""

    @property
    def caller_id(self) -> str | None:
        """The caller's number when the provider supplies it, else None. Treat as personal data: never log it."""
        return None

    @abstractmethod
    async def read_audio(self) -> bytes | None:
        """Next chunk of caller audio in audio_format (any size, whole samples), or None once the caller has hung up.

        May block until audio is available. Raising an exception is treated as the call having ended."""

    @abstractmethod
    async def write_audio(self, audio: bytes) -> None:
        """Queue agent audio (already in audio_format) for playback. Clips arrive in playback order. Adapters that must
        deliver in real time (e.g. 20 ms packets) may pace inside this method or buffer internally."""

    @abstractmethod
    async def wait_playout(self) -> None:
        """Return when all audio passed to write_audio() has finished playing to the caller. If more audio is written
        while waiting, keep waiting until the playout buffer is empty again. Must also return promptly after
        flush_playback()."""

    @abstractmethod
    async def flush_playback(self) -> None:
        """Stop playing and discard anything queued but not yet played. Must be safe to call when nothing is playing."""

    # sourav: some providers can tell us "the caller wants to talk now" (a key press, provider-side barge-in). The agent
    # protocol has an `interrupt` message for exactly that, but the connector must not invent a trigger, so the default
    # is "never"; an adapter that can detect it overrides this. Flow: wait forever unless overridden.
    async def wait_interrupt(self) -> None:
        """Return each time the caller asks to take the floor. Default: never."""
        await asyncio.Event().wait()

    # sourav: handing a call to a human needs provider-specific commands (SIP transfer, API call), which this repository
    # does not have, so the safe default is "not supported". Flow: return False; adapters that can transfer override it.
    async def transfer(self, reason: str) -> bool:
        """Hand the call to a person. Return True if the provider took the call over (the connector then does NOT hang
        up), False if transfer is unsupported or failed (the connector then ends the call)."""
        return False

    @abstractmethod
    async def hangup(self) -> None:
        """End the call. Must be idempotent (safe to call twice, and after the caller already hung up)."""


# The function the connector gives a provider: the provider calls it once per accepted call and awaits it until the call ends.
CallHandler = Callable[[TelephonyCall], Awaitable[None]]


class TelephonyProvider(ABC):
    """A source of calls: owns the listening socket / webhook / PBX connection and any provider-side authentication."""

    @abstractmethod
    async def serve(self, on_call: CallHandler) -> None:
        """Accept calls until cancelled. For each call, create a TelephonyCall and run `await on_call(call)` (typically as
        its own task so calls proceed concurrently); tear the provider-side call down when that coroutine returns."""

    # sourav: optional clean-up hook for providers holding sockets. Flow: nothing by default.
    async def close(self) -> None:
        """Release provider resources. Default: nothing to release."""
        return None
