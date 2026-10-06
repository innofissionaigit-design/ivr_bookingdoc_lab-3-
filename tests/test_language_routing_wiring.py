"""The orchestrator's language wiring, off-pod: early language ID, the hand-off after repeated ambiguity, and
the reply language following the caller turn by turn (upgrade prompt sections 6.1, 6.3-6.5; docs/adr/0002).

Same approach as tests/test_orchestrator_persona.py: the REAL functions of main_pcm.py with the pod-only
libraries stubbed (tests/_pod_stubs.py) and the models replaced by fakes. What is real is the wiring.

    python -m pytest tests/test_language_routing_wiring.py -v
"""

import asyncio
import io
import json
import os
import sys
import wave

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from _pod_stubs import pod_stubs
from _synth_voices import FATHER, utterance

from agent import early_lid
from agent.lid import LIDResult

SR = 16000
ACTIVE = ("bn", "hi", "en")


class FakeWS:
    def __init__(self):
        self.texts, self.audio = [], []

    async def send_text(self, t):
        self.texts.append(t)

    async def send_bytes(self, b):
        self.audio.append(b)

    async def close(self):
        pass

    def spoken(self):
        out = []
        for t in self.texts:
            try:
                d = json.loads(t)
            except ValueError:
                continue
            if d.get("sender") == "AI":
                out.append(d["text"])
        return out


class FakeTTS:
    async def synthesize(self, lang, text, speed=1.0):
        b = io.BytesIO()
        with wave.open(b, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(b"\x00\x00" * (SR // 4))
        return b.getvalue()


class Rec:
    def __init__(self, text, agreement=0.9):
        self.text, self.decoder_agreement, self.decoder_used = text, agreement, "rnnt"


@pytest.fixture(scope="module")
def m():
    with pod_stubs(REPO_ROOT) as imp:
        yield imp("main_pcm")


# ======================================================== repeated ambiguity -> stop guessing

UNSURE = dict(language="hi", confidence=0.40, scores={"bn": 0.35, "hi": 0.40, "en": 0.25})
SURE = dict(language="bn", confidence=0.97, scores={"bn": 0.97, "hi": 0.02, "en": 0.01})


class FakeLID:
    def __init__(self, **fields):
        self.fields, self.calls = fields, 0

    def identify_path(self, path):
        self.calls += 1
        return LIDResult(**self.fields)


class FakeASRRouter:
    def __init__(self, text, agreement):
        self.text, self.agreement = text, agreement

    async def transcribe(self, lang, path):
        return Rec(self.text, self.agreement)


@pytest.fixture
def routing(m, monkeypatch):
    monkeypatch.setattr(m, "_languages_active", ACTIVE)

    class Driver:
        def __init__(self):
            self.session = m.CallSession(FakeWS())

        def use(self, lid, asr):
            monkeypatch.setattr(m, "_lid", lid)
            monkeypatch.setattr(m, "_asr_router", asr)

        async def turn(self, **kw):
            return await m._route_and_transcribe(self.session, "unused.wav", **kw)

    d = Driver()
    yield d
    d.session.cleanup()


@pytest.mark.asyncio
async def test_a_call_that_keeps_being_ambiguous_stops_being_guessed_at(m, routing):
    routing.use(FakeLID(**UNSURE), FakeASRRouter("abc", 0.2))  # language ID torn AND decoders disagree
    for _ in range(m.ASRLanguageRouter().max_ambiguous_streak):
        lang, result = await routing.turn()
        assert lang is not None and result is not None, "tolerated, not yet given up on"
    assert await routing.turn() == (None, None), "one more in a row: stop guessing"
    # ...and it stays that way until a turn is clear, so the re-ask / hand-off policy takes over
    assert await routing.turn() == (None, None)


@pytest.mark.asyncio
async def test_a_clear_turn_in_between_resets_it(m, routing):
    unsure = FakeLID(**UNSURE)
    routing.use(unsure, FakeASRRouter("abc", 0.2))
    for _ in range(m.ASRLanguageRouter().max_ambiguous_streak):
        await routing.turn()
    routing.use(FakeLID(**SURE), FakeASRRouter("হ্যাঁ", 1.0))
    lang, _ = await routing.turn()
    assert lang == "bn"
    routing.use(unsure, FakeASRRouter("abc", 0.2))
    lang, result = await routing.turn()
    assert lang is not None, "the count started again from zero"


@pytest.mark.asyncio
async def test_a_short_yes_with_low_language_id_but_agreeing_decoders_is_not_ambiguity(m, routing):
    routing.use(FakeLID(**UNSURE), FakeASRRouter("হ্যাঁ", 1.0))
    for _ in range(m.ASRLanguageRouter().max_ambiguous_streak + 3):
        lang, result = await routing.turn()
        assert result is not None, "language ID alone being unsure must never cost the caller their turn"


# ============================================================ early language ID (section 6.1)


def _done(value):
    fut = asyncio.get_running_loop().create_future()
    fut.set_result(value)
    return fut


@pytest.mark.asyncio
async def test_a_decisive_early_answer_replaces_identifying_the_whole_utterance(m, routing):
    lid = FakeLID(**UNSURE)  # would be unsure -- but must not even be asked
    routing.use(lid, FakeASRRouter("সিবিসি টেস্টের রেট কত", 0.9))
    early = early_lid.EarlyLID(key=0.0, task=_done(LIDResult(**SURE)))
    lang, result = await routing.turn(early=early)
    assert lang == "bn" and result is not None
    assert lid.calls == 0, "the whole-utterance identification was skipped"


@pytest.mark.asyncio
async def test_an_indecisive_early_answer_falls_back_to_the_whole_utterance(m, routing):
    lid = FakeLID(**SURE)
    routing.use(lid, FakeASRRouter("সিবিসি", 0.9))
    trap = LIDResult("hi", 0.90, {"bn": 0.01, "hi": 0.90, "en": 0.09})  # accented English labelled Hindi
    await routing.turn(early=early_lid.EarlyLID(key=0.0, task=_done(trap)))
    assert lid.calls == 1


@pytest.mark.asyncio
async def test_a_failed_early_identification_never_reaches_the_call(m, routing):
    lid = FakeLID(**SURE)
    routing.use(lid, FakeASRRouter("সিবিসি", 0.9))
    fut = asyncio.get_running_loop().create_future()
    fut.set_exception(RuntimeError("model fell over"))
    fut.exception()  # mark retrieved
    lang, _ = await routing.turn(early=early_lid.EarlyLID(key=0.0, task=fut))
    assert lang == "bn" and lid.calls == 1


class _Clip:
    def __init__(self, n):
        self.n = n

    def clone(self):
        return self


class _Tail:
    def __init__(self):
        self.slices = []

    def __getitem__(self, s):
        self.slices.append((s.start, s.stop))
        return _Clip(s.stop - s.start)


class _Model:
    def identify(self, clip):
        return LIDResult(**SURE)


@pytest.mark.asyncio
async def test_the_turn_detector_starts_language_id_once_per_turn_on_the_first_words(m, monkeypatch):
    monkeypatch.setattr(m, "_lid", _Model())
    monkeypatch.setattr(m, "_languages_active", ACTIVE)
    session = m.CallSession(FakeWS())
    try:
        tail = _Tail()
        speaking = [{"start": 0.4, "end": 2.6}]

        m._maybe_start_early_lid(session, tail, SR, [{"start": 0.4, "end": 1.0}])
        assert session.early_lid is None, "not enough speech yet"

        m._maybe_start_early_lid(session, tail, SR, speaking)
        first = session.early_lid
        assert first is not None and first.matches(session.processed_until_s)
        assert tail.slices == [(int(0.4 * SR), int(1.6 * SR))], "only the first 1.2 s of speech is heard"
        assert (await first.task).language == "bn"

        m._maybe_start_early_lid(session, tail, SR, speaking + [{"start": 3.0, "end": 4.0}])
        assert session.early_lid is first and len(tail.slices) == 1, "once per turn"

        session.processed_until_s = 7.5  # the turn was consumed; the next one starts a new identification
        m._maybe_start_early_lid(session, tail, SR, speaking)
        assert session.early_lid is not first
    finally:
        session.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["bengali_only", "switched_off", "no_model"])
async def test_it_does_nothing_where_it_cannot_help(m, monkeypatch, why):
    monkeypatch.setattr(m, "_lid", None if why == "no_model" else _Model())
    monkeypatch.setattr(m, "_languages_active", ("bn",) if why == "bengali_only" else ACTIVE)
    if why == "switched_off":
        monkeypatch.setenv("EARLY_LID", "off")
    session = m.CallSession(FakeWS())
    try:
        m._maybe_start_early_lid(session, _Tail(), SR, [{"start": 0.0, "end": 3.0}])
        assert session.early_lid is None
    finally:
        session.cleanup()


# ===================================================== reply language follows the utterance (6.3/6.5)

SAYS = {
    "bn": "সিবিসি টেস্টের রেট কত?",
    "hi": "सीबीसी टेस्ट का रेट कितना है?",
    "en": "what is the price of a CBC test?",
}
REPLY = {
    "bn": "সিবিসি টেস্টের রেট তিনশো পঞ্চাশ টাকা।",
    "hi": "सीबीसी टेस्ट का रेट तीन सौ पचास रुपये है।",
    "en": "The CBC costs three hundred and fifty rupees.",
}


def test_per_utterance_is_the_default(m):
    assert m.REPLY_LANGUAGE_MODE == "per_utterance"


def test_the_reply_language_follows_each_utterance(m):
    s = m.CallSession(FakeWS())
    try:
        assert [m._resolve_reply_language(s, x) for x in ("bn", "en", "hi", "bn", "bn", "en")] == [
            "bn", "en", "hi", "bn", "bn", "en",
        ]
        assert s.lang == "en" and s.lang_router.previous_language == "en"
        assert s.call_state.language == "en"
    finally:
        s.cleanup()


def test_an_explicit_request_holds_for_its_turn_and_seeds_the_next_prior_then_the_caller_decides(m):
    s = m.CallSession(FakeWS())
    try:
        m._resolve_reply_language(s, "bn")
        m._lock_reply_language(s, "hi")  # "speak in Hindi"
        s.lang_router.note_response_language("hi")
        assert s.lang == "hi" and s.lang_router.previous_language == "hi"
        assert m._resolve_reply_language(s, "bn") == "bn", "what the caller actually speaks decides the next reply"
    finally:
        s.cleanup()


def test_sticky_mode_keeps_the_old_behaviour_available(m, monkeypatch):
    monkeypatch.setattr(m, "REPLY_LANGUAGE_MODE", "sticky")
    s = m.CallSession(FakeWS())
    try:
        assert m._resolve_reply_language(s, "bn") == "bn"
        assert m._resolve_reply_language(s, "en") == "bn", "locked to the first language of the call"
        m._lock_reply_language(s, "hi")
        assert m._resolve_reply_language(s, "en") == "hi", "moved only by an explicit request"
    finally:
        s.cleanup()


@pytest.fixture
def call(m, monkeypatch, tmp_path):
    """A call whose recogniser hears whatever `state["lang"]` says and whose clinic lookup answers in the
    language it is asked in -- so what is spoken shows which language the REPLY was built for."""
    monkeypatch.setattr(m, "_admission", None)
    monkeypatch.setattr(m, "_tts_router", FakeTTS())
    monkeypatch.setattr(m, "CONDITION_INPUT", "off")
    state = {"lang": "bn", "asked_in": []}

    async def route(session, wav):
        return state["lang"], Rec(SAYS[state["lang"]])

    async def resolve(session, text, lang):
        return {"intent": "test_rate", "slots": {"test_name": "CBC"}, "secondary_intent": None, "direct_reply_bn": None}

    async def answer(intent, slots, lang):
        state["asked_in"].append(lang)
        return REPLY[lang]

    async def handoff(session, reason, languages=None):
        raise AssertionError(f"unexpected hand-off: {reason}")

    monkeypatch.setattr(m, "_route_and_transcribe", route)
    monkeypatch.setattr(m, "_resolve_intent", resolve)
    monkeypatch.setattr(m, "_answer_enquiry_intent", answer)
    monkeypatch.setattr(m, "_handoff_to_human", handoff)
    session = m.CallSession(FakeWS())
    session.release_gate()
    session.disclosed_langs.update(ACTIVE)  # these tests are not about the disclosure notice
    session.call_state = m.new_call_state()
    voice = utterance(FATHER, dur=3.0, seed=1, amp=0.3)

    class Call:
        pass

    c = Call()
    c.session, c.state = session, state

    async def say(lang):
        state["lang"] = lang
        session.turn_epoch = session.speak_epoch
        path = str(tmp_path / f"u{len(session.ws.texts)}.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((np.clip(voice, -1, 1) * 32767).astype(np.int16).tobytes())
        await m._dispatch_turn(session, path)

    c.say = say
    yield c
    session.cleanup()


@pytest.mark.asyncio
async def test_a_caller_who_changes_language_is_answered_in_it_with_that_languages_template_set(m, call):
    for lang in ("bn", "hi", "en", "bn"):
        await call.say(lang)
    assert call.state["asked_in"] == ["bn", "hi", "en", "bn"]
    assert call.session.ws.spoken() == [REPLY[x] for x in ("bn", "hi", "en", "bn")]


@pytest.mark.asyncio
async def test_sticky_mode_still_answers_in_the_first_language(m, call, monkeypatch):
    monkeypatch.setattr(m, "REPLY_LANGUAGE_MODE", "sticky")
    for lang in ("bn", "hi", "en"):
        await call.say(lang)
    assert call.state["asked_in"] == ["bn", "bn", "bn"]
