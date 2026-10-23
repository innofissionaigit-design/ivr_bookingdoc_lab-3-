"""Replay a whole phone conversation through the REAL orchestrator and the
REAL clinic API, off-pod, and hear exactly what the caller would hear.

    with conversation(lang="bn") as call:
        call.say("আমি একজন ডক্টর দেখাব", "book_appointment", {"doctor_name": "একজন ডক্টর"})
        call.silence(6)          # the caller says nothing for six seconds
        print(call.transcript())

WHY THIS EXISTS
---------------
tests/test_orchestrator_booking_flow.py already drives the real `_dispatch_turn`,
but against a FakeTools whose every doctor lookup answers `found: True`. That fake
cannot reproduce the failure that reached a real caller on 2026-10-06:

    caller: আমি একজন ডক্টর দেখাব           ("I'll see a doctor")
    agent : অ্যাপয়েন্টমেন্টের জন্য কোন দিন আর কোন সময় বলবেন?
    caller: পরশু দিন ... সকাল সাড়ে দশটা
    agent : দুঃখিত, 'একজন ডক্টর' নামে কোনো ডাক্তার খুঁজে পেলাম না।

The model put the words "a doctor" in the doctor_name slot, and only a real
doctor lookup says "nobody is called that". So the clinic API here is the real
one -- tests/_clinic_app.py's seeded, in-process FastAPI app -- reached by the
real agent/tools_client.ClinicToolsClient over an in-memory ASGI transport. The
wiring, the slot handling and every clinic answer are production code.

WHAT IS STILL FAKED, AND WHY THAT IS THE RIGHT LINE
---------------------------------------------------
* ASR: the transcript is given, because the subject here is what the agent DOES
  with words, not whether it heard them.
* The intent model: the caller's intent and slots are given per turn, exactly as
  qwen produced them on the live call. The model is not deterministic and is not
  on this machine; scripting its output is what makes a regression test of the
  FLOW possible at all. Feed it what the live model actually emitted -- including
  its mistakes -- because the flow has to survive those.
* TTS: the spoken text is captured instead of synthesised.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _clinic_app import clinic_app  # noqa: E402
from _pod_stubs import pod_stubs  # noqa: E402
from _synth_voices import FATHER, utterance  # noqa: E402
from test_orchestrator_persona import ASRResult, FakeTTS, FakeWS, _wav  # noqa: E402

_LANG_CODE = {"bn": "bn", "hi": "hi", "en": "en"}


class Call:
    """One phone call. Every method returns the lines the agent spoke in reply."""

    def __init__(self, m, session, state, tmp_dir: str, loop: asyncio.AbstractEventLoop):
        self.m = m
        self.session = session
        self._loop = loop
        self._state = state
        self._tmp = pathlib.Path(tmp_dir)
        self._voice = utterance(FATHER, dur=2.0, seed=1, amp=0.3)
        self._log: list[tuple[str, str]] = []
        self._clock_offset = 0.0
        self.lang = session.lang
        # Lines production would NOT have spoken: agent/tts.py raises UnspeakableTextError on them
        # and the caller hears the system-busy fallback instead. FakeTTS never raises, so without
        # this check a replay reports a line as said that a real caller would never have heard --
        # exactly how a blocked booking read-back passed the first version of this harness.
        self.blocked: list[tuple[str, list[str]]] = []

    def _run(self, coro):
        # One loop per call, owned here: the session's asyncio primitives bind to the first loop
        # that uses them, and relying on asyncio.get_event_loop() fails under pytest's asyncio mode.
        return self._loop.run_until_complete(coro)

    def greet(self) -> list[str]:
        before = len(self.session.ws.spoken())
        self._run(self.m._greet(self.session)) if hasattr(self.m, "_greet") else None
        out = self.session.ws.spoken()[before:]
        self._record(out)
        return out

    def say(
        self,
        text: str,
        intent: str = "unclear",
        slots: dict | None = None,
        *,
        agreement: float = 0.9,
        decoder: str | None = None,
        **extra,
    ) -> list[str]:
        """One caller turn: what ASR heard, and what the intent model made of it.

        `agreement` / `decoder` are the recogniser's own confidence (agent/asr.py ASRResult). Real
        calls produce low agreement routinely -- 0.33 on the live booking of 2026-10-06 -- and the
        orchestrator takes a different path then, so a replay must be able to say so."""
        self._state["text"] = text
        self._state["agreement"] = agreement
        self._state["decoder"] = decoder
        self._state["data"] = {"intent": intent, "slots": dict(slots or {}), **extra}
        self.session.turn_epoch = self.session.speak_epoch
        path = _wav(self._tmp, self._voice, f"u{len(self._log)}.wav")
        before = len(self.session.ws.spoken())
        self._log.append(("User", text))
        self._run(self.m._dispatch_turn(self.session, path))
        out = self.session.ws.spoken()[before:]
        self._record(out)
        return out

    def silence(self, seconds: float) -> list[str]:
        """The caller says nothing for `seconds`. Drives the real silence
        handler with a moved clock instead of sleeping, so a 90-second test
        takes milliseconds and the timing is exact."""
        before = len(self.session.ws.spoken())
        real = time.monotonic
        # The offset only ever GROWS across silence() calls. Resetting it per call made a
        # timestamp saved in the last call sit seconds in the future, so the next wait never
        # elapsed -- a clock running backwards, which no real call can do.
        self.m.time.monotonic = lambda: real() + self._clock_offset
        try:
            # First tick only starts the clock (that is what _handle_silence does).
            self._run(self.m._handle_silence(self.session, True))
            step = 0.5
            waited = 0.0
            while waited < seconds:
                waited += step
                self._clock_offset += step
                ended = self._run(self.m._handle_silence(self.session, True))
                if ended:
                    break
        finally:
            self.m.time.monotonic = real
        out = self.session.ws.spoken()[before:]
        if out:
            self._log.append(("User", f"<silent {seconds:g}s>"))
            self._record(out)
        return out

    def _gate(self, line: str) -> list[str]:
        from agent.tts import unspeakable_spans, verbalize

        return unspeakable_spans(verbalize(line, self.lang), self.lang)

    def _record(self, lines: list[str]) -> None:
        for line in lines:
            spans = self._gate(line)
            if spans:
                self.blocked.append((line, spans))
                self._log.append(("AI BLOCKED", f"{line}   <-- unspeakable {spans}"))
            else:
                self._log.append(("AI", line))

    def transcript(self) -> str:
        return "\n".join(f"[{who}] {line}" for who, line in self._log)

    @property
    def ended(self) -> bool:
        return getattr(self.session, "outcome", None) == "silence_timeout" or bool(
            getattr(self.session, "ended", False)
        )


@contextlib.contextmanager
def conversation(lang: str = "bn", tmp_dir: str | None = None):
    """A fresh call against a freshly seeded clinic database."""
    import tempfile

    import httpx

    tmp = tmp_dir or tempfile.mkdtemp(prefix="call-")
    with pod_stubs(REPO_ROOT) as imp:
        m = imp("main_pcm")
        with clinic_app(sample_patients=True) as (app, _client):
            from agent.tools_client import ClinicToolsClient

            tools = ClinicToolsClient("http://clinic.test")
            tools._client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://clinic.test"
            )

            saved = {k: getattr(m, k) for k in (
                "_admission", "_tts_router", "CONDITION_INPUT", "_tools",
                "_route_and_transcribe", "_resolve_intent",
            )}
            state = {"text": "", "lang": _LANG_CODE[lang], "data": {}}

            async def route(session, wav):
                result = ASRResult(state["text"], state.get("agreement", 0.9))
                result.decoder_used = state.get("decoder")
                return state["lang"], result

            async def resolve(session, text, lng):
                return {"secondary_intent": None, "direct_reply_bn": None, **state["data"]}

            m._admission = None
            m._tts_router = FakeTTS()
            m.CONDITION_INPUT = "off"
            m._tools = tools
            m._route_and_transcribe = route
            m._resolve_intent = resolve

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            session = m.CallSession(FakeWS())
            session.release_gate()
            session.disclosed_langs.add(_LANG_CODE[lang])
            session.lang = _LANG_CODE[lang]
            session.call_state = m.new_call_state()
            try:
                yield Call(m, session, state, tmp, loop)
            finally:
                session.cleanup()
                for k, v in saved.items():
                    setattr(m, k, v)
                with contextlib.suppress(Exception):
                    loop.run_until_complete(tools._client.aclose())
                loop.close()
                asyncio.set_event_loop(None)
