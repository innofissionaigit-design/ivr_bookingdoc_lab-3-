"""providers/telephony/wavfile.py -- TEST / PLACEHOLDER telephony adapter that plays a WAV file as the "caller".

What this file is for
---------------------
There is no real phone provider yet (the client has not said which one they use), but the connector still has to be exercised
end to end. This adapter stands in for one: it is NOT a phone integration and carries no real call. It

  * "answers" one call when started, feeds the WAV file named by TELEPHONY_ENDPOINT to the connector in real time
    (that setting is a FILE PATH for this adapter -- in a real adapter it would be your provider's URL: place your url here),
    followed by silence so the agent has time to reply,
  * records everything the agent says back into <file>.reply.wav (what a caller would have heard),
  * hangs up when the file and the silence are done.

It is also the smallest worked example of an adapter: copy its shape (a TelephonyProvider that calls on_call, and a TelephonyCall
that implements read/write/wait_playout/flush/hangup) when writing the real one. Real adapters add the provider-specific parts
this one does not need: authentication, signature checks, call setup, transfer, caller ID.

Run it against a running main_pcm.py (private network only):

    TELEPHONY_PROVIDER=wavfile TELEPHONY_ENDPOINT=/path/to/question.wav python -m telephony
"""

from __future__ import annotations

import asyncio
import time
import wave
from pathlib import Path

import numpy as np

from telephony.adapter import AudioFormat, CallHandler, TelephonyCall, TelephonyProvider
from telephony.audio import CODEC_PCM_S16LE, WavError, parse_wav, pcm16_to_bytes
from telephony.config import ConfigError, ProviderSettings

CHUNK_S = 0.02  # a typical telephony packet interval
TAIL_SILENCE_S = 12.0  # silence after the file, long enough for the agent to answer, then the call ends


class WavFileCall(TelephonyCall):
    """A call whose caller audio is a WAV file and whose playback is written to a WAV file. Plays in real time."""

    # sourav: build a call from an already-parsed caller recording. Flow: remember the samples/rate, set up the playback
    # clock and the buffer that collects the agent's reply audio.
    def __init__(self, samples: np.ndarray, sample_rate: int, reply_path: Path | None, tail_s: float = TAIL_SILENCE_S):
        self._samples = samples
        self._format = AudioFormat(CODEC_PCM_S16LE, sample_rate)
        self._chunk = max(1, int(sample_rate * CHUNK_S))
        self._position = 0
        self._tail_left_s = tail_s
        self._reply_path = reply_path
        self._reply = bytearray()
        self._play_until = 0.0
        self._hung_up = False

    @property
    def audio_format(self) -> AudioFormat:
        return self._format

    # sourav: the connector pulls caller audio at telephony speed. Flow: sleep one packet interval, return the next slice of
    # the file; after the file, return silence until the tail is used up, then None (= caller hung up).
    async def read_audio(self) -> bytes | None:
        if self._hung_up:
            return None
        await asyncio.sleep(CHUNK_S)
        if self._position < len(self._samples):
            piece = self._samples[self._position : self._position + self._chunk]
            self._position += self._chunk
            return pcm16_to_bytes(piece)
        if self._tail_left_s <= 0:
            return None
        self._tail_left_s -= CHUNK_S
        return bytes(self._chunk * 2)

    # sourav: record what the agent says and remember how long it takes to "play". Flow: append the audio to the reply
    # buffer and push the simulated playout end time forward by the clip's duration.
    async def write_audio(self, audio: bytes) -> None:
        self._reply += audio
        now = time.monotonic()
        self._play_until = max(self._play_until, now) + self._format.duration_s(len(audio))

    # sourav: honest playback semantics even for a file: audio "finishes playing" when its duration has elapsed. Flow: sleep
    # until the simulated playout end time (re-checking, because more audio may be written or the queue flushed meanwhile).
    async def wait_playout(self) -> None:
        while True:
            remaining = self._play_until - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.05))

    # sourav: stop_playback / interrupt support. Flow: the simulated playout ends now.
    async def flush_playback(self) -> None:
        self._play_until = 0.0

    # sourav: end the call and save what the caller heard. Flow: mark hung up, write <file>.reply.wav once (idempotent).
    async def hangup(self) -> None:
        if self._hung_up:
            return
        self._hung_up = True
        if self._reply_path is not None and self._reply:
            with wave.open(str(self._reply_path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(self._format.sample_rate)
                out.writeframes(bytes(self._reply))


class WavFileProvider(TelephonyProvider):
    """Accepts exactly one call (the file) and returns when it ends."""

    # sourav: remember which file to play. Flow: store the path.
    def __init__(self, wav_path: Path):
        self._path = wav_path

    # sourav: the provider side of the contract: "call on_call for each call". Flow: parse the file, build one WavFileCall,
    # await the connector's handler until the call ends.
    async def serve(self, on_call: CallHandler) -> None:
        parsed = parse_wav(self._path.read_bytes())
        call = WavFileCall(parsed.samples, parsed.sample_rate, self._path.with_suffix(".reply.wav"))
        await on_call(call)


# sourav: the hook telephony/loader.py looks for. Flow: require TELEPHONY_ENDPOINT (a WAV path for this adapter), check the file
# is readable audio, return the provider. Only the settings this provider needs are used.
def create_provider(settings: ProviderSettings) -> TelephonyProvider:
    if settings.endpoint is None:
        raise ConfigError("the wavfile adapter needs TELEPHONY_ENDPOINT set to the path of a caller WAV file")
    path = Path(settings.endpoint)
    if not path.is_file():
        raise ConfigError(f"TELEPHONY_ENDPOINT does not point to a file: {path}")
    try:
        parse_wav(path.read_bytes())
    except WavError as e:
        raise ConfigError(f"TELEPHONY_ENDPOINT is not a usable WAV file: {e}") from e
    return WavFileProvider(path)
