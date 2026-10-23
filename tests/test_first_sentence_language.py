"""The language of the caller's FIRST real sentence is the language of the whole call (docs/adr/0003).

    bn first -> every reply Bengali, even when later turns are recognised as English or Hindi
    hi first -> Hindi throughout          en first -> English throughout

Not fixed by a bare "hello" / "হ্যাঁ" / one word (they are the same sound in every language), not by a turn that could
not be read; moved only by an explicit request ("speak in Hindi"). `REPLY_LANGUAGE_MODE=per_utterance` is still there.

    python -m pytest tests/test_first_sentence_language.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent.booking_flow import classify_yes_no  # noqa: E402
from agent.language_policy import establishes_call_language  # noqa: E402

PARKING = {
    "bn": "ক্লিনিকের নিজস্ব পার্কিং আছে, কোনো চার্জ লাগে না।",
    "hi": "क्लिनिक की अपनी पार्किंग है, कोई शुल्क नहीं लगता।",
    "en": "The clinic has its own parking, and there is no charge.",
}
ASKS = {
    "bn": "আপনাদের ক্লিনিকে গাড়ি পার্কিং আছে?",
    "hi": "क्या आपके क्लिनिक में गाड़ी की पार्किंग है?",
    "en": "do you have car parking at the clinic",
}


# ======================================================================== what counts as "a real sentence"
@pytest.mark.parametrize(
    "said",
    ["আমি একটা অ্যাপয়েন্টমেন্ট নিতে চাই", "आपके क्लिनिक में गाड़ी की पार्किंग है", "I want to book a test",
     "hello I need an appointment", "হ্যালো আমি ডাক্তার দেখাব", "ক্লিনিক কোথায়"],
)
def test_a_real_sentence_establishes_the_language(said):
    assert establishes_call_language(said) is True


@pytest.mark.parametrize(
    "said",
    ["hello", "Hello!", "hi", "হ্যালো", "হ্যালো হ্যালো", "হ্যাঁ", "না", "ঠিক আছে", "নমস্কার", "नमस्ते", "हाँ जी",
     "yes please", "okay", "ডাক্তার", "appointment", "", "   ", "১২৩৪"],
)
def test_a_greeting_a_yes_or_one_word_does_not(said):
    assert establishes_call_language(said) is False


# =============================================================================== the rule itself
@pytest.fixture(scope="module")
def m():
    from _pod_stubs import pod_stubs

    with pod_stubs(REPO_ROOT) as imp:
        yield imp("main_pcm")


class _WS:
    async def send_text(self, t):
        pass

    async def send_bytes(self, b):
        pass

    async def close(self):
        pass


@pytest.fixture
def session(m):
    s = m.CallSession(_WS())
    yield s
    s.cleanup()


def test_sticky_is_the_default(m):
    assert m.REPLY_LANGUAGE_MODE == "sticky"


def _replies(m, s, turns):
    return [m._resolve_reply_language(s, lang, text) for lang, text in turns]


def test_bengali_first_sentence_keeps_the_whole_call_bengali(m, session):
    turns = [("bn", "আমি একটা টেস্ট করাতে চাই"), ("en", "what is the price of a CBC test"), ("hi", "मुझे डॉक्टर से मिलना है"),
             ("bn", "ধন্যবাদ আপনাকে")]
    assert _replies(m, session, turns) == ["bn", "bn", "bn", "bn"]
    assert session.lang_locked and session.lang == "bn"


def test_hindi_first_sentence_keeps_the_whole_call_hindi(m, session):
    turns = [("hi", "मुझे डॉक्टर से मिलना है"), ("bn", "আমি একটা টেস্ট করাতে চাই"), ("en", "what is the price")]
    assert _replies(m, session, turns) == ["hi", "hi", "hi"]


def test_english_first_sentence_keeps_the_whole_call_english(m, session):
    turns = [("en", "I want to book an appointment"), ("bn", "আমি একটা টেস্ট করাতে চাই"), ("hi", "मुझे डॉक्टर से मिलना है")]
    assert _replies(m, session, turns) == ["en", "en", "en"]


def test_a_bare_hello_does_not_fix_the_language_the_next_sentence_does(m, session):
    turns = [("en", "hello"), ("bn", "আমি একটা টেস্ট করাতে চাই"), ("en", "what is the price of a CBC test")]
    # the greeting is answered as it was said (nothing fixed yet); the first real sentence fixes it
    assert _replies(m, session, turns) == ["en", "bn", "bn"]
    assert session.lang_locked


def test_a_one_word_yes_before_the_sentence_fixes_nothing(m, session):
    assert _replies(m, session, [("hi", "हाँ"), ("hi", "हाँ जी"), ("bn", "আমি ডাক্তার দেখাব")]) == ["hi", "hi", "bn"]


def test_an_explicit_request_moves_a_fixed_call(m, session):
    assert _replies(m, session, [("bn", "আমি একটা টেস্ট করাতে চাই")]) == ["bn"]
    m._lock_reply_language(session, "hi")  # "speak in Hindi"
    assert _replies(m, session, [("bn", "আমি ডাক্তার দেখাব")]) == ["hi"]


def test_a_turn_that_could_not_be_read_does_not_fix_the_language(m, session):
    """The first turn is fixed in _resolve_reply_language, BEFORE the transcript is judged unreadable -- the
    unreadable branch (_keep_language) must take it back, or the call is stuck in the greeting language."""
    session.lang_locked_before_turn = session.lang_locked  # what _dispatch_turn does at the top of a turn
    prior = session.lang
    m._resolve_reply_language(session, "hi", "मुझे डॉक्टर से मिलना है")  # fixes it ...
    assert session.lang_locked and session.lang == "hi"
    m._keep_language(session, prior)  # ... and the turn is then found unreadable
    assert not session.lang_locked and session.lang == "bn"
    assert _replies(m, session, [("hi", "मुझे डॉक्टर से मिलना है")]) == ["hi"], "the next readable sentence decides"


def test_per_utterance_mode_is_still_available(m, session, monkeypatch):
    monkeypatch.setattr(m, "REPLY_LANGUAGE_MODE", "per_utterance")
    turns = [("bn", "আমি একটা টেস্ট করাতে চাই"), ("en", "what is the price"), ("hi", "मुझे डॉक्टर से मिलना है")]
    assert _replies(m, session, turns) == ["bn", "en", "hi"]
    assert not session.lang_locked


# ====================================================== an answer is understood in the language it was given in
def test_a_yes_or_no_said_in_another_language_than_the_call_is_still_an_answer():
    assert classify_yes_no("yes", "bn") == "yes"
    assert classify_yes_no("okay", "bn") == "yes"
    assert classify_yes_no("no", "bn") == "no"
    assert classify_yes_no("हाँ", "bn") == "yes"
    assert classify_yes_no("नहीं", "bn") == "no"
    assert classify_yes_no("হ্যাঁ", "en") == "yes"


def test_the_answers_in_the_calls_own_language_are_unchanged():
    assert [classify_yes_no(x, "bn") for x in ("হ্যাঁ", "ঠিক আছে", "না", "ঠিক না", "কী বললেন")] == [
        "yes", "yes", "no", "no", None]
    assert [classify_yes_no(x, "hi") for x in ("हाँ", "ठीक है", "नहीं", "pata nahi")] == ["yes", "yes", "no", None]
    assert [classify_yes_no(x, "en") for x in ("yes", "no", "not sure", "I don't know")] == ["yes", "no", None, None]
    assert classify_yes_no("maybe", "bn") is None and classify_yes_no("", "bn") is None


# =============================================================== the whole call, through the real orchestrator
def _say(call, text, lang, topic="parking"):
    call._state["lang"] = lang
    return " ".join(call.say(text, "clinic_faq", {"faq_topic": topic}))


@pytest.fixture
def call():
    from _conversation import conversation

    with conversation("bn") as c:
        c.session.lang_locked = False  # a call that has not yet heard its first sentence
        yield c


def test_a_call_that_opens_in_bengali_stays_bengali_when_the_caller_then_speaks_english_and_hindi(call):
    assert PARKING["bn"] in _say(call, ASKS["bn"], "bn")
    assert PARKING["bn"] in _say(call, ASKS["en"], "en")
    assert PARKING["bn"] in _say(call, ASKS["hi"], "hi")
    assert call.session.lang == "bn"


def test_a_call_that_opens_in_hindi_stays_hindi(call):
    assert PARKING["hi"] in _say(call, ASKS["hi"], "hi")
    spoken = _say(call, ASKS["bn"], "bn")
    assert PARKING["hi"] in spoken and PARKING["bn"] not in spoken
    assert PARKING["hi"] in _say(call, ASKS["en"], "en")


def test_a_call_that_opens_in_english_stays_english(call):
    assert PARKING["en"] in _say(call, ASKS["en"], "en")
    spoken = _say(call, ASKS["bn"], "bn")
    assert PARKING["en"] in spoken and PARKING["bn"] not in spoken
    assert PARKING["en"] in _say(call, ASKS["hi"], "hi")


def test_an_english_hello_then_a_bengali_question_makes_a_bengali_call(call):
    call._state["lang"] = "en"
    call.say("hello", "greeting", {})
    assert not call.session.lang_locked, "a greeting fixes nothing"
    assert PARKING["bn"] in _say(call, ASKS["bn"], "bn")
    assert PARKING["bn"] in _say(call, ASKS["en"], "en")


@pytest.mark.parametrize(
    "request_text,request_lang",
    [("हिंदी में बोलिए", "hi"), ("আপনি হিন্দিতে বলুন", "bn"), ("please speak in english", "en"), ("speak in hindi", "en"),
     ("ইংরেজিতে বলুন", "bn"), ("अंग्रेज़ी में बोलिए", "hi")],
)
def test_no_request_moves_a_call_that_is_already_in_its_first_language(call, request_text, request_lang):
    """The call stays in the language of its first sentence -- there is no way to move it mid-call."""
    from agent.phrases import phrase

    assert PARKING["bn"] in _say(call, ASKS["bn"], "bn")
    call._state["lang"] = request_lang
    said = " ".join(call.say(request_text, "unclear", {}))
    assert phrase("language_stays", "bn") in said
    assert call.session.lang == "bn"
    assert PARKING["bn"] in _say(call, ASKS["hi"], "hi")  # and the next answer is still Bengali


def test_a_request_that_IS_the_first_sentence_sets_the_language(call):
    call._state["lang"] = "hi"
    said = " ".join(call.say("आप अंग्रेज़ी में बोलिए", "unclear", {}))  # the first real sentence is the request
    assert call.session.lang == "en" and call.session.lang_locked
    assert "switching to English" in said


def test_an_unreadable_turn_is_re_asked_in_the_calls_language_only(call, monkeypatch):
    assert PARKING["bn"] in _say(call, ASKS["bn"], "bn")
    seen = {}

    async def fake(session, decision, lang, languages=None, **kw):
        seen["languages"], seen["lang"] = languages, lang

    monkeypatch.setattr(call.m, "_reask_or_handoff", fake)

    async def unidentifiable(session, wav, early=None):
        return None, None

    monkeypatch.setattr(call.m, "_route_and_transcribe", unidentifiable)
    call.say("...", "unclear", {})
    assert seen["languages"] == ("bn",) and seen["lang"] == "bn"


def test_an_abusive_turn_in_another_language_is_answered_in_the_calls_language(call):
    from agent import abuse
    from agent.phrases import phrase

    assert PARKING["bn"] in _say(call, ASKS["bn"], "bn")
    call._state["lang"] = "hi"
    said = " ".join(call.say("तू भोसड़ी का", "unclear", {}))  # Hindi-script swearing on a Bengali call
    assert phrase(abuse.response_key(1), "bn") in said
    assert phrase(abuse.response_key(1), "hi") not in said
    assert call.session.lang == "bn"


# ============================== a turn said in another language than the call is understood in the language it was said in
@pytest.fixture(scope="module")
def catalogue():
    from _clinic_app import clinic_app

    from agent.fast_path import Catalogue

    with clinic_app(sample_patients=False) as (_app, client):
        return Catalogue(client.get("/api/v1/catalogue").json())


@pytest.mark.parametrize("said", [ASKS["en"], ASKS["hi"]])
def test_a_faq_asked_in_another_language_is_matched_on_a_bengali_call(catalogue, said):
    assert catalogue.match(said, "faq", "bn", 0.72, prefer_longer=True)[0] == "parking"


def test_the_bengali_match_itself_is_unchanged(catalogue):
    assert catalogue.match(ASKS["bn"], "faq", "bn", 0.72, prefer_longer=True)[0] == "parking"
    assert catalogue.match("ধন্যবাদ আপনাকে", "faq", "bn", 0.72, prefer_longer=True)[0] is None


def test_an_english_test_name_is_grounded_on_a_bengali_call(catalogue):
    from agent.entity_grounding import ground_tests

    got = ground_tests("what is the price of a CBC test", "bn", catalogue, ["CBC"])
    assert got.names == ("Complete Blood Count (CBC)",) and got.dropped == ()


def test_a_bengali_sentence_is_not_re_read_in_another_language(catalogue):
    from agent.entity_grounding import ground_tests
    from agent.language_policy import foreign_script_language

    assert foreign_script_language("আমি CBC টেস্ট করব", "bn") is None  # one English word in a Bengali sentence
    assert foreign_script_language("what is the price", "bn") == "en"
    assert foreign_script_language("क्लिनिक कहाँ है", "bn") == "hi"
    assert foreign_script_language("১২৩", "bn") is None
    assert ground_tests("সিবিসি টেস্টের দাম কত", "bn", catalogue, []).names == ("Complete Blood Count (CBC)",)


def test_the_fast_path_gives_an_intent_across_languages_but_never_a_canned_reply(catalogue):
    """A FAQ topic / test / doctor key is language-independent, so it may be matched across languages; a canned reply
    (smalltalk, "you are welcome") is written in the language of the table that produced it and the call speaks its
    own -- the table for one language still never answers in another (also pinned in test_fast_path_languages)."""
    import datetime

    from agent.fast_path import FastPath

    fp = FastPath(catalogue, today=datetime.date(2026, 9, 25))
    hit = fp.resolve(ASKS["en"], "bn")
    assert hit.intent == "clinic_faq" and hit.slots["faq_topic"] == "parking" and hit.direct_reply_bn is None
    assert fp.resolve("thank you", "en").direct_reply_bn, "English has a canned reply for this one"
    assert fp.resolve("thank you", "bn") is None


def test_thanks_said_in_another_language_gets_the_call_languages_own_thanks(m):
    import datetime

    from agent import fast_path_cues as cues
    from agent.fast_path import FastPathResult

    english = FastPathResult("smalltalk", {}, 1.0, None, cues.table_for("en").thanks_reply)
    got = m._canned_reply_in_call_language(english, "bn", "en")
    assert got.direct_reply_bn == cues.table_for("bn").thanks_reply and got.intent == "smalltalk"
    greeting = FastPathResult("smalltalk", {}, 1.0, None, cues.table_for("en").greeting_reply)
    assert m._canned_reply_in_call_language(greeting, "hi", "en").direct_reply_bn == cues.table_for("hi").greeting_reply
    other = FastPathResult("smalltalk", {}, 1.0, None, "something else entirely")
    assert m._canned_reply_in_call_language(other, "bn", "en") is None


def test_the_recognisers_prior_follows_what_is_spoken_not_the_language_the_call_replies_in(m, session):
    """Once the call is fixed to Bengali, a Hindi turn must still be recognised as Hindi: the router's prior is the
    spoken language. Pinned to the reply language, Hindi needed language ID >= 0.97 to leave the Bengali recogniser."""
    m._resolve_reply_language(session, "bn", "আমি একটা টেস্ট করাতে চাই")
    assert session.lang_router.previous_language == "bn"
    assert m._resolve_reply_language(session, "hi", "मुझे सीबीसी टेस्ट का रेट बताइए") == "bn"  # still replied to in Bengali
    assert session.lang_router.previous_language == "hi"  # ... but recognised as what it was
    assert m._resolve_reply_language(session, "en", "what is the price of a CBC test") == "bn"
    assert session.lang_router.previous_language == "en"
    assert session.call_state.language == "bn"


def test_a_first_sentence_that_is_a_language_request_is_answered_only_in_that_language(call):
    from agent.phrases import phrase

    call.session.disclosed_langs.discard("hi")
    call._state["lang"] = "en"
    said = " ".join(call.say("speak in hindi please", "unclear", {}))
    assert said == phrase("language_switched", "hi"), said  # no English notice in front of it
    assert call.session.lang == "hi" and call.session.lang_locked


# ===== recognition of a call that is already fixed (live 2026-10-14: Bengali speech labelled Hindi 0.99 -> Devanagari)
class _R:
    def __init__(self, text, agreement=0.9):
        self.text, self.decoder_agreement, self.decoder_used = text, agreement, "rnnt"


def _routing(m, monkeypatch, chosen, locked, fixed_before=True):
    seen = {}

    async def raw(session, wav, early=None):
        return chosen, _R("राउटर का पाठ")

    class Router:
        async def transcribe(self, lang, path):
            seen["asked"] = lang
            return _R(f"{lang} transcript")

    monkeypatch.setattr(m, "_route_and_transcribe_raw", raw)
    monkeypatch.setattr(m, "_asr_router", Router())
    s = m.CallSession(_WS())
    s.lang, s.lang_locked, s.lang_locked_before_turn = locked, fixed_before, fixed_before
    return s, seen


@pytest.mark.asyncio
async def test_a_bengali_call_is_recognised_in_bengali_even_when_language_id_says_hindi(m, monkeypatch):
    s, seen = _routing(m, monkeypatch, chosen="hi", locked="bn")
    try:
        lang, result = await m._route_and_transcribe(s, "x.wav")
        assert lang == "bn" and result.text == "bn transcript" and seen["asked"] == "bn"
        assert s.lang_router.previous_language == "bn"
    finally:
        s.cleanup()


@pytest.mark.asyncio
async def test_a_hindi_call_is_recognised_in_hindi_even_when_language_id_says_bengali(m, monkeypatch):
    s, seen = _routing(m, monkeypatch, chosen="bn", locked="hi")
    try:
        lang, _ = await m._route_and_transcribe(s, "x.wav")
        assert lang == "hi" and seen["asked"] == "hi"
    finally:
        s.cleanup()


@pytest.mark.asyncio
async def test_a_real_english_sentence_still_wins_on_a_fixed_call_and_an_unfixed_call_is_left_to_the_routing(m, monkeypatch):
    s, seen = _routing(m, monkeypatch, chosen="en", locked="bn")

    async def raw(session, wav, early=None):
        return "en", _R("do you have a parking space at the clinic today", 0.9)

    monkeypatch.setattr(m, "_route_and_transcribe_raw", raw)
    try:
        assert (await m._route_and_transcribe(s, "x.wav"))[0] == "en" and "asked" not in seen
    finally:
        s.cleanup()
    s, seen = _routing(m, monkeypatch, chosen="hi", locked="bn", fixed_before=False)
    try:
        assert (await m._route_and_transcribe(s, "x.wav"))[0] == "hi" and "asked" not in seen  # not fixed yet
    finally:
        s.cleanup()


def test_a_first_sentence_the_recognisers_own_decoders_disagree_on_fixes_nothing(m, session):
    said = "मुझे सीबीसी टेस्ट का रेट बताइए"
    # answered in the language it was SPOKEN in (a Hindi first sentence with one odd word is not answered in Bengali) ...
    assert m._resolve_reply_language(session, "hi", said, 0.3) == "hi"
    assert not session.lang_locked  # ... but a reading the decoders disagree on does not FIX the call's language
    assert m._resolve_reply_language(session, "hi", said, 0.9) == "hi"  # a convincing one does
    assert session.lang_locked and session.lang == "hi"


def test_a_shaky_first_hindi_sentence_is_answered_in_hindi_but_does_not_fix_the_call(m, session):
    said = "आपका वहा पे कौन कौन टेस्ते होता है"
    assert m._resolve_reply_language(session, "hi", said, 0.4) == "hi"
    assert not session.lang_locked
    assert m._resolve_reply_language(session, "bn", "আমি একটা টেস্ট করাতে চাই", 0.9) == "bn"  # a convincing one fixes it
    assert session.lang_locked and session.lang == "bn"


@pytest.mark.asyncio
async def test_a_short_phrase_heard_as_english_stays_in_the_calls_language(m, monkeypatch):
    """Live 2026-10-14, a Hindi call: "और बताइए" came back as the English text "all but a year" (Latin, decoders agree,
    four 'English' words) and the English recogniser won outright."""
    s, seen = _routing(m, monkeypatch, chosen="en", locked="hi")

    async def raw(session, wav, early=None):
        return "en", _R("all but a year", 1.0)

    monkeypatch.setattr(m, "_route_and_transcribe_raw", raw)
    try:
        lang, result = await m._route_and_transcribe(s, "x.wav")
        assert lang == "hi" and seen["asked"] == "hi" and result.text == "hi transcript"
    finally:
        s.cleanup()
