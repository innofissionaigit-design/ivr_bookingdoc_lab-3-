"""telephony/connector.py -- the provider-independent bridge between a phone call and the voice agent's /ws/audio.

What this file is for
---------------------
main_pcm.py already serves callers over a WebSocket (/ws/audio): the browser page and scripts/e2e_call.py both talk to it.
This connector is one more client of that SAME protocol, used for phone calls. It reads/writes the call through a
TelephonyCall (telephony/adapter.py) and speaks to the agent through an AgentLink (telephony/agent_link.py). It contains no
provider names and no agent logic. One CallSession exists per call; sessions share nothing.

The /ws/audio protocol, exactly as main_pcm.py implements it today (nothing here is invented):

  connector -> agent   text  {"type":"hello","sampleRate":16000,"format":"pcm_s16le","channels":1}   once, first
                       bytes mono signed 16-bit little-endian PCM, 16 kHz, continuous
                       text  {"type":"playback_done"}   after the last queued reply clip has finished playing
                       text  {"type":"interrupt"}       the caller asked to take the floor (the connector stops its own
                                                         playback first, as the browser does)
  agent -> connector   bytes one complete WAV file per reply clip (sample rate read from each header)
                       text  {"type":"stop_playback"}   drop queued/playing audio, caller took the floor
                       text  {"type":"handoff_human","reason":...}   a person should take over; the agent then closes
                       text  {"type":"config","aec":...}            ignored (acoustic echo cancellation is not supported)
                       text  {"sender":...,"text":...}  transcripts / "_ping" heartbeat; ignored (never logged: personal data)
                       socket close                      the call is over

Browser behaviour that is mirrored on purpose (static/pcm/index.html): while reply audio is playing the caller's audio is
replaced by silence (the "half-duplex gate" described in agent/playback_gate.py), and `playback_done` is sent once, when no
reply clip is left to play.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Callable

from telephony.adapter import TelephonyCall, TelephonyProvider
from telephony.agent_link import AgentLink, AgentLinkClosed, LinkFactory, agent_ws_url, websocket_link_factory
from telephony.audio import (
    AGENT_SAMPLE_RATE,
    UPLINK_FRAME_SAMPLES,
    AudioError,
    ContinuousPcmStream,
    UplinkConverter,
    wav_to_provider_audio,
)
from telephony.config import DEFAULT_DRAIN_TIMEOUT_S, ConnectorSettings

logger = logging.getLogger("telephony.connector")

# The first message of every call, byte for byte what the browser client and scripts/e2e_call.py send.
HELLO_MESSAGE = {"type": "hello", "sampleRate": AGENT_SAMPLE_RATE, "format": "pcm_s16le", "channels": 1}
# Uplink pacing: one tick per browser-sized block (512 samples = 32 ms), see static/pcm/pcm-worklet.js.
UPLINK_TICK_S = UPLINK_FRAME_SAMPLES / AGENT_SAMPLE_RATE


# sourav: the connector must open every call with exactly the agent's hello message. Flow: serialise the constant.
def hello_frame() -> str:
    return json.dumps(HELLO_MESSAGE)


class _Playback:
    """Agent reply clips -> provider audio, in order, with `playback_done` sent once when the last clip has really played.

    Mirrors the browser: playback_done is sent when no clip is left (static/pcm/index.html onPlaybackIdle). Clips are
    written to the adapter back to back; "really played" is asked of the adapter via wait_playout()."""

    # sourav: one playback pipeline per call. Flow: set up the ordered queue, counters and events; on_idle is the callback
    # that tells the agent playback finished.
    def __init__(self, call: TelephonyCall, on_idle: Callable[[], object], session_id: str):
        self._call = call
        self._on_idle = on_idle
        self._sid = session_id
        self._queue: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()
        self._epoch = 0  # bumped by flush(): anything queued/converting under an older epoch is discarded
        self._busy = 0  # clips accepted but not yet handed to the adapter
        self._written = 0  # clips handed to the adapter
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self.active = False  # True from the first clip of a speech burst until playback is idle or flushed

    # sourav: the downlink loop calls this for every binary frame. Flow: mark playback active (this is what mutes the
    # caller's uplink), count the clip, queue it under the current epoch. Never blocks.
    def enqueue(self, wav: bytes) -> None:
        self.active = True
        self._idle.clear()
        self._busy += 1
        self._queue.put_nowait((self._epoch, wav))

    # sourav: converts and writes clips strictly one after another, which is what preserves playback order. Flow: take the
    # next clip, convert WAV -> provider codec/rate in a worker thread (so one call's maths never stalls the event loop for
    # the others), hand it to the adapter; an unplayable clip is dropped but still counted as handled.
    async def run_worker(self) -> None:
        fmt = self._call.audio_format
        while True:
            epoch, wav = await self._queue.get()
            try:
                if epoch == self._epoch:
                    audio = await asyncio.to_thread(wav_to_provider_audio, wav, fmt.codec, fmt.sample_rate)
                    if epoch == self._epoch and audio:
                        await self._call.write_audio(audio)
                        self._written += 1
            except AudioError as e:
                logger.warning("[%s] dropped an unplayable reply clip (%s)", self._sid, e)
            finally:
                self._busy -= 1
                self._wake.set()

    # sourav: decides WHEN `playback_done` may be sent. Flow: wake when a clip was handled, ask the adapter to wait until
    # everything written has played; if more clips arrived or were written meanwhile, wait again; if the queue was flushed,
    # say nothing; otherwise playback is idle -> send playback_done exactly once.
    async def run_watcher(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while True:
                epoch, written = self._epoch, self._written
                await self._call.wait_playout()
                if epoch != self._epoch or self._busy:
                    break
                if self._written != written:
                    continue
                if self.active:
                    self.active = False
                    self._idle.set()
                    result = self._on_idle()
                    if asyncio.iscoroutine(result):
                        await result
                break

    # sourav: handles `stop_playback` and local interrupts: the caller took the floor, so whatever is queued or playing is
    # abandoned. Flow: bump the epoch (invalidates clips being converted), empty the queue, mark inactive, tell the adapter
    # to stop. No playback_done is sent, same as the browser's stopPlayback().
    async def flush(self) -> None:
        self._epoch += 1
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._busy -= 1
        self.active = False
        if self._busy == 0:
            self._idle.set()
        self._wake.set()
        await self._call.flush_playback()

    # sourav: after the agent closes the socket its last words (goodbye, hand-off notice) may still be playing. Flow: wait
    # until the pipeline is idle (all clips played) or was flushed.
    async def drained(self) -> None:
        await self._idle.wait()


class CallSession:
    """Everything for ONE call: its own agent WebSocket, its own converters, its own playback pipeline and tasks.

    Nothing is shared with other calls, so calls cannot affect each other. Lifetime: run() returns when the call is over."""

    # sourav: one session per accepted call. Flow: remember the adapter call and settings, give the session a short random
    # id for log lines (never the caller's number), build the per-call converter and playback pipeline.
    def __init__(
        self,
        call: TelephonyCall,
        link_factory: LinkFactory,
        drain_timeout_s: float = DEFAULT_DRAIN_TIMEOUT_S,
        tick_s: float = UPLINK_TICK_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.id = uuid.uuid4().hex[:8]
        self.end_reason = "not_started"
        self._call = call
        self._link_factory = link_factory
        self._drain_timeout_s = drain_timeout_s
        self._tick_s = tick_s
        self._clock = clock
        fmt = call.audio_format
        self._uplink = UplinkConverter(fmt.codec, fmt.sample_rate)
        self._playback = _Playback(call, self._send_playback_done, self.id)
        self._pacer: ContinuousPcmStream | None = None
        self._link: AgentLink | None = None
        self._link_alive = False
        self._stop = asyncio.Event()
        self._handoff_reason: str | None = None
        self._transferred = False
        self._bad_chunks = 0
        self._tasks: list[asyncio.Task[None]] = []

    # sourav: lets the owner (connector shutdown) end a call without cancelling tasks from outside. Flow: set the stop event.
    def request_stop(self) -> None:
        self._stop.set()

    # sourav: the whole life of a call. Flow: open this call's own WebSocket to the agent, send hello, start the uplink,
    # downlink, interrupt and playback tasks, wait until the call ends, then always clean everything up.
    async def run(self) -> None:
        try:
            link = await self._link_factory()
        except Exception as e:  # noqa: BLE001 - any failure to reach the agent ends this call, not the process
            logger.error("[%s] cannot reach the voice agent (%s); ending the call", self.id, type(e).__name__)
            self.end_reason = "agent_unreachable"
            await self._hangup()
            return
        self._link = link
        self._link_alive = True
        logger.info("[%s] call connected", self.id)
        try:
            await link.send_text(hello_frame())
            self._pacer = ContinuousPcmStream(clock=self._clock)
            self._tasks = [
                asyncio.create_task(self._uplink_reader(), name=f"{self.id}-uplink-read"),
                asyncio.create_task(self._uplink_sender(), name=f"{self.id}-uplink-send"),
                asyncio.create_task(self._downlink(), name=f"{self.id}-downlink"),
                asyncio.create_task(self._interrupt_watcher(), name=f"{self.id}-interrupt"),
                asyncio.create_task(self._playback.run_worker(), name=f"{self.id}-play"),
                asyncio.create_task(self._playback.run_watcher(), name=f"{self.id}-playout"),
            ]
            await self._supervise()
        except AgentLinkClosed:
            self.end_reason = "agent_closed"
        finally:
            await self._teardown()

    # sourav: a Task[None] version of waiting for the stop request. Flow: await the event.
    async def _wait_stop(self) -> None:
        await self._stop.wait()

    # sourav: decides what ends the call and how gracefully. Flow: wait for the first of -- caller hung up / provider
    # failed (end now), stop requested (end now), agent closed the socket (let queued speech finish, then hand off if the
    # agent asked for it).
    async def _supervise(self) -> None:
        reader, sender, downlink, _interrupts, worker, watcher = self._tasks
        stop_wait = asyncio.create_task(self._wait_stop(), name=f"{self.id}-stop")
        self._tasks.append(stop_wait)
        caller_side = {reader, worker, watcher}
        agent_side = {downlink, sender}
        done, _ = await asyncio.wait(caller_side | agent_side | {stop_wait}, return_when=asyncio.FIRST_COMPLETED)
        if stop_wait in done:
            self.end_reason = "stopped"
            return
        if done & caller_side:
            ended = next(iter(done & caller_side))
            self.end_reason = "caller_hung_up" if ended is reader else "provider_error"
            self._log_task_failure(ended)
            return
        self.end_reason = "agent_closed"
        self._link_alive = False
        for task in done:
            self._log_task_failure(task)
        await self._finish_after_agent_close(reader, stop_wait)

    # sourav: the agent closes the socket right after its last spoken line (goodbye / hand-off notice); cutting the phone
    # call at that instant would clip the sentence. Flow: keep reading (and discarding) caller audio while the queued clips
    # finish playing, with a time limit; if the caller hung up meanwhile do nothing more; otherwise honour a pending
    # handoff_human.
    async def _finish_after_agent_close(self, reader: asyncio.Task[None], stop_wait: asyncio.Task[None]) -> None:
        drain = asyncio.create_task(self._playback.drained(), name=f"{self.id}-drain")
        try:
            await asyncio.wait(
                {reader, drain, stop_wait}, timeout=self._drain_timeout_s, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            drain.cancel()
        if reader.done() or stop_wait.done():
            return
        if not drain.cancelled() and not self._playback_idle():
            logger.warning(
                "[%s] reply audio did not finish within %.0fs of the agent closing", self.id, self._drain_timeout_s
            )
        if self._handoff_reason is not None:
            await self._try_transfer(self._handoff_reason)

    # sourav: tiny helper for the drain log line. Flow: read the playback pipeline's state.
    def _playback_idle(self) -> bool:
        return not self._playback.active

    # sourav: the agent asked for a person (handoff_human) and then closed; the repository has no SIP transfer, so the
    # provider adapter decides. Flow: ask the adapter to transfer; if it cannot, say so in the log and let the normal
    # hang-up end the call (the same thing that happens when the agent closes the socket on a browser caller).
    async def _try_transfer(self, reason: str) -> None:
        try:
            transferred = await self._call.transfer(reason)
        except Exception as e:  # noqa: BLE001
            logger.error("[%s] transfer failed (%s)", self.id, type(e).__name__)
            transferred = False
        if transferred:
            self._transferred = True
            logger.info("[%s] call transferred to a person (reason: %s)", self.id, reason)
        else:
            logger.warning(
                "[%s] agent asked for a human (%s) but this provider cannot transfer; ending the call", self.id, reason
            )

    # sourav: log WHY a task ended unexpectedly without ever logging exception text (it can carry personal data).
    # Flow: ignore cancellation and the normal "agent closed" signal, log the exception type otherwise.
    def _log_task_failure(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None and not isinstance(exc, AgentLinkClosed):
            logger.error("[%s] %s stopped with %s", self.id, task.get_name(), type(exc).__name__)

    # sourav: caller audio -> agent. Flow: read a provider chunk (None = caller hung up), convert codec/rate to 16 kHz PCM,
    # replace it by silence while the agent's reply is playing (the half-duplex gate), queue it on the continuous stream.
    # A malformed chunk is skipped and counted, never fatal.
    async def _uplink_reader(self) -> None:
        assert self._pacer is not None
        while True:
            chunk = await self._call.read_audio()
            if chunk is None:
                return
            if not chunk or not self._link_alive:
                continue
            try:
                pcm = self._uplink.convert(chunk)
            except AudioError:
                self._bad_chunks += 1
                if self._bad_chunks in (1, 10, 100, 1000):
                    logger.warning("[%s] skipped a malformed caller-audio chunk (%d so far)", self.id, self._bad_chunks)
                continue
            if self._playback.active:
                pcm = bytes(len(pcm))
            self._pacer.push(pcm)

    # sourav: the agent needs a continuous stream. Flow: every tick, take what the continuous stream has (real audio plus
    # any silence needed to stay aligned with the clock) and send it as one binary frame.
    async def _uplink_sender(self) -> None:
        assert self._pacer is not None and self._link is not None
        while True:
            await asyncio.sleep(self._tick_s)
            data = self._pacer.pull()
            if data:
                await self._link.send_bytes(data)

    # sourav: agent -> caller. Flow: read frames until the socket closes; binary frames are reply clips (queued in order),
    # text frames are control messages.
    async def _downlink(self) -> None:
        assert self._link is not None
        while True:
            try:
                message = await self._link.recv()
            except AgentLinkClosed:
                return
            if isinstance(message, bytes | bytearray):
                self._playback.enqueue(bytes(message))
            else:
                await self._handle_text(message)

    # sourav: interpret one agent control message. Flow: parse JSON (malformed or non-object frames are logged and ignored),
    # then act on `type`: stop_playback flushes playback; handoff_human remembers the reason (acted on after the last
    # reply has played and the agent closes); config / transcripts / unknown types are ignored. Transcript text is never logged.
    async def _handle_text(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except ValueError:
            logger.warning("[%s] ignored a malformed control frame from the agent", self.id)
            return
        if not isinstance(message, dict):
            logger.warning("[%s] ignored a control frame that is not a JSON object", self.id)
            return
        kind = message.get("type")
        if kind == "stop_playback":
            await self._playback.flush()
        elif kind == "handoff_human":
            reason = message.get("reason")
            self._handoff_reason = reason[:64] if isinstance(reason, str) and reason else "unspecified"
            logger.info("[%s] agent requested a human hand-off (%s)", self.id, self._handoff_reason)
        elif kind == "config" or "sender" in message:
            return
        else:
            logger.debug("[%s] ignored an unknown control frame type %r", self.id, str(kind)[:32])

    # sourav: the provider may tell us the caller wants the floor (key press, provider-side barge-in). The agent expects the
    # client to stop its own playback first for a manual interrupt, then send `interrupt`. Flow: wait for the adapter's
    # signal, flush playback, send the message; repeat. An adapter without this feature never fires it.
    async def _interrupt_watcher(self) -> None:
        assert self._link is not None
        while True:
            try:
                await self._call.wait_interrupt()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - a broken optional signal must not end the call
                logger.error("[%s] interrupt signal failed (%s); caller interrupts disabled", self.id, type(e).__name__)
                return
            await self._playback.flush()
            if self._link_alive:
                with contextlib.suppress(AgentLinkClosed):
                    await self._link.send_text(json.dumps({"type": "interrupt"}))

    # sourav: callback for the playback pipeline. Flow: tell the agent the last reply clip finished (unless the link is gone).
    async def _send_playback_done(self) -> None:
        if self._link is not None and self._link_alive:
            with contextlib.suppress(AgentLinkClosed):
                await self._link.send_text(json.dumps({"type": "playback_done"}))

    # sourav: adapters promise hangup() is idempotent, but a failing hangup must never break clean-up. Flow: call, swallow.
    async def _hangup(self) -> None:
        try:
            await self._call.hangup()
        except Exception as e:  # noqa: BLE001
            logger.error("[%s] hangup failed (%s)", self.id, type(e).__name__)

    # sourav: free everything the call owns, whatever way it ended. Flow: cancel all tasks and wait for them, stop adapter
    # playback, close the agent socket (this is how the agent learns the caller left), hang up unless the call was handed to
    # a person, log the outcome.
    async def _teardown(self) -> None:
        self._link_alive = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await self._playback.flush()
        if self._link is not None:
            await self._link.close()
        if not self._transferred:
            await self._hangup()
        logger.info("[%s] call ended (%s)", self.id, self.end_reason)


class TelephonyConnector:
    """Creates one CallSession per call the provider hands over. Holds no per-call state of its own."""

    # sourav: the connector needs to know where the agent is and how to open a connection to it. Flow: keep the settings;
    # build the default WebSocket link factory from host/port unless a test supplies a fake one.
    def __init__(self, settings: ConnectorSettings, link_factory: LinkFactory | None = None):
        self._settings = settings
        self._link_factory = link_factory or websocket_link_factory(
            agent_ws_url(settings.agent_host, settings.agent_port)
        )
        self._sessions: set[CallSession] = set()

    # sourav: shutdown and tests need to know how many calls are live. Flow: size of this connector's own set.
    @property
    def active_calls(self) -> int:
        return len(self._sessions)

    # sourav: the function the provider calls for every accepted call. Flow: build a session for the call, run it until the
    # call ends, always forget it afterwards. Concurrent calls simply mean concurrent invocations of this coroutine.
    async def handle_call(self, call: TelephonyCall) -> None:
        session = CallSession(call, self._link_factory, drain_timeout_s=self._settings.drain_timeout_s)
        self._sessions.add(session)
        try:
            await session.run()
        finally:
            self._sessions.discard(session)

    # sourav: the production entry point: let the provider accept calls and route each to handle_call. Flow: serve until
    # cancelled, then release the provider.
    async def run(self, provider: TelephonyProvider) -> None:
        try:
            await provider.serve(self.handle_call)
        finally:
            await provider.close()

    # sourav: graceful shutdown. Flow: ask every live session to stop; their run() calls then clean up and return.
    def stop_all(self) -> None:
        for session in list(self._sessions):
            session.request_stop()
