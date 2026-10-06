"""Story "Caller has reached the hospital by mistake" -- the REAL orchestrator (main_pcm._dispatch_turn), not just helpers.

# ADDED BY SOURAV: every test here drives the real `_dispatch_turn` with the same harness the booking / abuse / call-end suites use
# (fake tools, ASR and intent extraction mocked at their seams -- Ollama is not reachable from the test environment, so what the REAL
# language model would answer for a given sentence is NOT proven here; what IS proven is what the orchestrator does with each possible
# answer, and that the deterministic rules and the hospital veto hold regardless of the model).
#
# What is verified per scenario: the spoken reply, the consecutive-unrelated counter, the call outcome, the call-score flag, whether
# the websocket was closed, whether the model / a lookup was (not) reached, and that no personal data was asked for or stored.

  python -m pytest tests/test_orchestrator_unrelated_caller.py -v
"""

import os
import re
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_orchestrator_booking_flow import (  # noqa: F401  (the harness: real dispatch, fake tools)
    BACK,
    PRICE,
    book,
    env,
    m,
    text_of,
)

from agent import abuse
from agent.phrases import phrase

FIRST = {lang: phrase("unrelated_first", lang) for lang in ("bn", "hi", "en")}
FINAL = {lang: phrase("unrelated_final", lang) for lang in ("bn", "hi", "en")}
BN_ABUSE = ["খানকির ছেলে", "হারামজাদা", "খানকির ছেলে"]


@pytest.fixture
def e(m, env):
    """The booking harness with every language already disclosed (so no disclosure line precedes a reply) and the model made to
    FAIL LOUDLY when it is asked -- individual tests that want a model answer set `env.model`."""
    env.session.disclosed_langs.update({"bn", "hi", "en"})
    return env


async def _say(env, text, lang, intent="unclear", slots=None, **extra):
    env.state["lang"] = lang
    return await env.say(text, intent, slots, **extra)


def _clean_of_personal_data(env, said):
    s = env.session
    assert s.identity.phone is None and s.booking is None and s.history_state is None and s.home_collection is None
    assert not getattr(env.tools, "callbacks", None) and not env.tools.holds and not env.tools.confirms
    spoken = text_of(said)
    assert not re.search(r"\d", spoken)  # no number asked for or read out


# ============================================================================= wrong number: the deterministic cues, 3 languages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,lang",
    [
        ("Sorry, wrong number.", "en"),
        ("Galat number lag gaya.", "en"),  # Hinglish arrives in Latin script, so ASR/LID tags it "en"
        ("गलत नंबर लग गया", "hi"),
        ("ভুল নম্বরে ফোন হয়ে গেছে।", "bn"),
        ("Mujhe laga ye Suresh ka number hai", "en"),
    ],
)
async def test_a_wrong_number_gets_one_polite_reply_then_the_second_closes_the_call(m, e, monkeypatch, text, lang):
    async def no_model(session, t, lg):
        raise AssertionError("a clear wrong number must be decided without the model")

    monkeypatch.setattr(m, "_resolve_intent", no_model)
    s = e.session
    said = await _say(e, text, lang)
    assert said == [FIRST[lang]]
    assert s.unrelated_count == 1 and s.outcome == "unrelated_caller" and s.signals.unrelated_caller is True
    assert not s.ws.closed  # the caller gets one chance
    _clean_of_personal_data(e, said)

    said = await _say(e, text, lang)
    assert said == [FINAL[lang]]
    assert s.unrelated_count == 2 and s.outcome == "unrelated_caller" and s.ws.closed


@pytest.mark.asyncio
async def test_a_caller_who_hangs_up_after_the_first_sorry_is_still_filed_as_unrelated_not_completed(m, e, monkeypatch):
    s = e.session
    await _say(e, "Sorry, wrong number.", "en")
    # ws_audio's finally block files `session.outcome if session.turn_count else "abandoned"`
    assert s.turn_count >= 1 and s.outcome == "unrelated_caller"


@pytest.mark.asyncio
async def test_the_unrelated_call_is_recorded_with_its_own_reason_and_no_score(m, e):
    s = e.session
    await _say(e, "Sorry, wrong number.", "en")
    queued = []
    s.recorder = type("R", (), {"satisfaction": lambda self, p: queued.append(p), "flush": lambda self: None})()
    m._write_satisfaction(s)
    assert len(queued) == 1 and queued[0]["score"] is None and queued[0]["reason"] == "unrelated_caller"
    assert queued[0]["signals"]["unrelated_caller"] is True


# ============================================================================= named person: never a doctor, never a patient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,lang",
    [
        ("Suresh hai kya?", "en"),
        ("Bhai Amit se baat karwao.", "en"),
        ("Ramesh ko phone diya tha.", "en"),
        ("Dada Suresh ache?", "en"),
        ("सुरेश है क्या?", "hi"),
        ("অমিত আছে?", "bn"),
    ],
)
async def test_a_bare_name_is_a_person_and_never_reaches_doctor_lookup_even_if_the_model_would_have_looked_it_up(
    m, e, monkeypatch, text, lang
):
    looked_up = []

    async def lookup(intent, slots, lg):
        looked_up.append((intent, slots))
        return "Dr Suresh is available tomorrow."

    monkeypatch.setattr(m, "_answer_enquiry_intent", lookup)
    said = await _say(
        e, text, lang, "doctor_availability", {"doctor_name": "Suresh"}
    )  # what a model might wrongly return
    assert said == [FIRST[lang]] and looked_up == []
    assert e.session.unrelated_count == 1 and e.session.booking is None
    assert e.session.signals.unrelated_caller is True


@pytest.mark.asyncio
async def test_a_doctor_named_with_a_doctor_word_stays_a_real_hospital_request(m, e):
    said = await _say(e, "Dr Suresh ka appointment chahiye", "en", "book_appointment", {"doctor_name": "Suresh"})
    s = e.session
    assert s.booking is not None and s.unrelated_count == 0 and s.outcome == "completed"
    assert FIRST["en"] not in text_of(said)


# ============================================================================= soft unrelated speech (the model's intent), 3 languages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first,second,lang",
    [
        ("Arre bhai kaise ho?", "Aur batao kya chal raha hai?", "en"),
        ("How are you brother?", "so what is going on with you", "en"),
        ("Dada kemon acho?", "ki khobor bolo", "en"),
        ("अरे भाई कैसे हो?", "और बताओ क्या चल रहा है", "hi"),
        ("দাদা কেমন আছো?", "আর বলো কী খবর", "bn"),
        ("office mein aaj bahut kaam tha", "weather kaisa hai", "en"),
    ],
)
async def test_casual_unrelated_chat_gets_one_polite_reply_and_the_second_consecutive_turn_closes(
    m, e, first, second, lang
):
    s = e.session
    said = await _say(e, first, lang, "unrelated_caller")
    assert said == [FIRST[lang]] and s.unrelated_count == 1 and not s.ws.closed and s.outcome == "unrelated_caller"
    _clean_of_personal_data(e, said)
    said = await _say(e, second, lang, "unrelated_caller")
    assert said == [FINAL[lang]] and s.unrelated_count == 2 and s.ws.closed and s.outcome == "unrelated_caller"


@pytest.mark.asyncio
async def test_a_third_turn_unrelated_pair_after_a_greeting_closes_on_the_second_of_the_pair(m, e):
    s = e.session
    await _say(e, "hello", "en", "smalltalk")  # turn 1: an ordinary greeting, existing behaviour
    assert s.unrelated_count == 0 and not s.ws.closed
    said = await _say(e, "Arre bhai kaise ho?", "en", "unrelated_caller")  # turn 2
    assert said == [FIRST["en"]] and s.unrelated_count == 1
    said = await _say(e, "Aur batao kya chal raha hai?", "en", "unrelated_caller")  # turn 3
    assert said == [FINAL["en"]] and s.ws.closed


# ============================================================================= a plain greeting is NOT a wrong caller


@pytest.mark.asyncio
@pytest.mark.parametrize("text,lang", [("hello", "en"), ("नमस्ते", "hi"), ("নমস্কার", "bn"), ("good morning", "en")])
async def test_an_ordinary_greeting_keeps_the_existing_smalltalk_behaviour(m, e, text, lang):
    said = await _say(e, text, lang, "smalltalk")
    assert said == [phrase("smalltalk_default", lang)]
    assert e.session.unrelated_count == 0 and not e.session.ws.closed and e.session.outcome == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("text,lang", [("hello", "en"), ("नमस्ते", "hi"), ("নমস্কার", "bn")])
async def test_even_if_the_model_calls_a_plain_greeting_unrelated_it_is_sent_back_to_smalltalk(m, e, text, lang):
    said = await _say(e, text, lang, "unrelated_caller")
    assert said == [phrase("smalltalk_default", lang)]
    assert e.session.unrelated_count == 0 and not e.session.ws.closed and e.session.outcome == "completed"


@pytest.mark.asyncio
async def test_thanks_and_smalltalk_with_a_model_reply_still_work_as_before(m, e):
    said = await _say(e, "thank you", "en", "smalltalk", direct_reply_bn="You are welcome. Is there anything else?")
    assert said == ["You are welcome. Is there anything else?"] and e.session.unrelated_count == 0


# ============================================================================= HOSPITAL FIRST: casual wording never closes a real request


@pytest.mark.asyncio
async def test_a_casual_sentence_with_a_price_question_is_a_normal_hospital_request(m, e):
    said = await _say(e, "Bhai kaise ho, CBC ka rate kya hai?", "en", "test_rate", {"test_name": "CBC"})
    assert said == [PRICE] and e.session.unrelated_count == 0 and e.session.outcome == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Bhai kaise ho, CBC ka rate kya hai?",
        "Arre dada kemon acho, amar test ta bari theke collect hobe?",
        "Sorry bhai, actually mera appointment check karna tha.",
        "Mujhe laga Suresh ka number hai, but actually CBC report ke baare mein poochna tha.",
        "Sorry, actually I need my appointment details.",
        "sorry wrong number, actually I wanted my report",
    ],
)
async def test_a_hospital_sentence_is_never_closed_as_unrelated_even_if_the_model_wrongly_says_unrelated(m, e, text):
    """Hospital-first: the model's `unrelated_caller` is only a proposal. Any hospital cue turns it into a plain re-ask, never a close."""
    s = e.session
    said = await _say(e, text, "en", "unrelated_caller")
    # either the model's proposal was vetoed (a plain re-ask) or an existing deterministic hospital handler (e.g. the history
    # flow for "my appointment details") answered first -- in no case is it treated as an unrelated caller
    assert said == [phrase("unclear", "en")] or "phone number" in text_of(said)
    assert FIRST["en"] not in text_of(said) and FINAL["en"] not in text_of(said)
    assert (
        s.unrelated_count == 0 and not s.ws.closed and s.outcome == "completed" and s.signals.unrelated_caller is False
    )


@pytest.mark.asyncio
async def test_a_filled_slot_overrules_the_model_even_with_no_cue_word_in_the_sentence(m, e):
    said = await _say(e, "Aur batao yaar", "en", "unrelated_caller", {"test_name": "TSH"})
    assert said == [phrase("unclear", "en")] and e.session.unrelated_count == 0 and not e.session.ws.closed


@pytest.mark.asyncio
async def test_a_mixed_sentence_that_the_model_reads_correctly_runs_the_real_hospital_flow(m, e):
    said = await _say(e, "Sorry bhai, actually mera appointment check karna tha", "en", "lookup_booking", {})
    assert e.session.unrelated_count == 0 and e.session.outcome == "completed" and not e.session.ws.closed
    assert FIRST["en"] not in text_of(said)


# ============================================================================= the counter resets on a genuine request


@pytest.mark.asyncio
async def test_unrelated_then_a_real_question_resets_the_counter_the_outcome_and_the_score_flag(m, e):
    s = e.session
    await _say(e, "Arre bhai kaise ho?", "en", "unrelated_caller")
    assert s.unrelated_count == 1 and s.outcome == "unrelated_caller" and s.signals.unrelated_caller is True
    said = await _say(e, "Actually CBC ka rate kya hai?", "en", "test_rate", {"test_name": "CBC"})
    assert said == [PRICE] and not s.ws.closed
    assert s.unrelated_count == 0 and s.outcome == "completed" and s.signals.unrelated_caller is False
    # the earlier unrelated turn does not poison the next one: it is a FIRST unrelated turn again, not a close
    said = await _say(e, "Aur batao kya chal raha hai?", "en", "unrelated_caller")
    assert said == [FIRST["en"]] and s.unrelated_count == 1 and not s.ws.closed


@pytest.mark.asyncio
async def test_wrong_number_then_an_appointment_request_resets_and_continues_normally(m, e):
    s = e.session
    await _say(e, "Wrong number.", "en")
    assert s.unrelated_count == 1
    said = await _say(e, "Wait actually I need my appointment details.", "en", "lookup_booking", {})
    assert s.unrelated_count == 0 and s.outcome == "completed" and not s.ws.closed
    assert FINAL["en"] not in text_of(said)


@pytest.mark.asyncio
async def test_unrelated_then_a_real_enquiry_never_closes_the_call(m, e):
    s = e.session
    await _say(e, "Arre bhai kaise ho?", "en", "unrelated_caller")
    said = await _say(e, "CBC ka rate kya hai?", "en", "test_rate", {"test_name": "CBC"})
    assert said == [PRICE] and not s.ws.closed and s.unrelated_count == 0


@pytest.mark.asyncio
async def test_a_greeting_between_two_unrelated_turns_breaks_the_streak(m, e):
    s = e.session
    await _say(e, "Arre bhai kaise ho?", "en", "unrelated_caller")
    await _say(e, "hello", "en", "smalltalk")
    assert s.unrelated_count == 0 and s.outcome == "completed"
    said = await _say(e, "Aur batao kya chal raha hai?", "en", "unrelated_caller")
    assert said == [FIRST["en"]] and not s.ws.closed  # consecutive means consecutive


# ============================================================================= abuse keeps working and is never merged with this counter


@pytest.mark.asyncio
async def test_abuse_alone_follows_the_existing_three_strike_policy_and_never_touches_the_unrelated_state(m, e):
    s = e.session
    a = await _say(e, BN_ABUSE[0], "bn")
    b = await _say(e, BN_ABUSE[1], "bn")
    assert a == [phrase("abuse_first", "bn")] and b == [phrase("abuse_second", "bn")] and not s.ws.closed
    c = await _say(e, BN_ABUSE[2], "bn")
    assert c == [phrase("abuse_final", "bn")] and s.ws.closed and s.outcome == "abusive_caller"
    assert s.unrelated_count == 0 and s.signals.unrelated_caller is False


@pytest.mark.asyncio
async def test_abuse_with_no_hospital_request_is_still_the_abuse_guard_even_when_the_model_says_unrelated(m, e):
    assert abuse.is_abusive(BN_ABUSE[0])
    said = await _say(e, BN_ABUSE[0], "bn", "unrelated_caller")
    assert said == [phrase("abuse_first", "bn")] and e.session.abuse_count == 1 and e.session.unrelated_count == 0


@pytest.mark.asyncio
async def test_an_abusive_turn_neither_advances_nor_resets_the_unrelated_streak(m, e):
    """Two separate counters, two separate closing rules: abuse does its own thing and leaves the unrelated count exactly where it was."""
    s = e.session
    await _say(e, "Arre bhai kaise ho?", "en", "unrelated_caller")
    assert s.unrelated_count == 1
    await _say(e, BN_ABUSE[0], "bn")
    assert s.abuse_count == 1 and s.unrelated_count == 1 and s.outcome == "unrelated_caller"


@pytest.mark.asyncio
async def test_abuse_followed_by_a_genuine_request_keeps_the_existing_policy(m, e):
    s = e.session
    await _say(e, BN_ABUSE[0], "bn")
    said = await _say(e, "CBC ka rate kya hai?", "en", "test_rate", {"test_name": "CBC"})
    assert said == [PRICE] and s.abuse_count == 1 and s.unrelated_count == 0 and not s.ws.closed


# ============================================================================= an active booking is never disturbed


@pytest.mark.asyncio
@pytest.mark.parametrize("model_says", ["unrelated_caller", "smalltalk"])
async def test_a_casual_sentence_in_the_middle_of_a_booking_does_not_reset_or_close_anything(m, e, model_says):
    s = e.session
    await book(e, doctor_name="Sen")
    booking, slots_before, stage_before = s.booking, dict(s.booking.slots), s.booking.stage
    for _ in range(3):  # repeated casual turns: still never a closing streak while a task is open
        said = await _say(e, "Achha bhai, kemon acho?", "en", model_says)
        assert not s.ws.closed
        assert FINAL["en"] not in text_of(said) and FIRST["en"] not in text_of(said)
    assert s.booking is booking and s.booking.slots == slots_before and s.booking.stage == stage_before
    assert s.unrelated_count == 0 and s.outcome == "completed" and s.signals.unrelated_caller is False
    assert said == [
        phrase("smalltalk_default", "en")
    ]  # exactly what plain smalltalk says in this state (the existing path)


@pytest.mark.asyncio
async def test_a_wrong_number_phrase_during_a_booking_is_left_to_the_normal_flow_not_to_the_closing_path(m, e):
    s = e.session
    await book(e, doctor_name="Sen")
    said = await _say(e, "Sorry wrong number", "en", "unclear")
    assert not s.ws.closed and s.booking is not None and s.unrelated_count == 0 and s.outcome == "completed"
    assert FIRST["en"] not in text_of(said)


@pytest.mark.asyncio
async def test_a_finished_booking_no_longer_counts_as_an_active_task(m, e):
    s = e.session
    s.booking = None
    assert m._unrelated_flow_active(s) is False
    await _say(e, "Arre bhai kaise ho?", "en", "unrelated_caller")
    assert s.unrelated_count == 1


# ============================================================================= other deterministic neighbours still win


@pytest.mark.asyncio
async def test_the_caller_saying_sorry_bye_after_an_unrelated_turn_ends_through_the_existing_call_end_path(m, e):
    s = e.session
    await _say(e, "Arre bhai kaise ho?", "en", "unrelated_caller")
    said = await _say(e, "ok sorry bye", "en")
    assert said == [phrase("silence_goodbye", "en")] and s.ws.closed
    assert s.outcome == "unrelated_caller"  # still filed as the unrelated call it was


@pytest.mark.asyncio
async def test_an_emergency_always_wins_over_the_unrelated_path(m, e):
    s = e.session
    said = await _say(e, "sorry wrong number but my father is not breathing", "en")
    assert s.outcome == "emergency" and s.unrelated_count == 0 and FIRST["en"] not in text_of(said)


@pytest.mark.asyncio
async def test_a_request_for_a_person_by_role_is_still_a_handoff_not_a_wrong_number(m, e):
    s = e.session
    await _say(e, "I want to speak to a person", "en")
    assert s.outcome == "handed_off" and s.unrelated_count == 0


@pytest.mark.asyncio
async def test_an_unreadable_turn_between_two_unrelated_ones_is_neutral(m, e):
    s = e.session
    await _say(e, "Arre bhai kaise ho?", "en", "unrelated_caller")
    assert s.unrelated_count == 1
    # a Latin-script turn tagged Hindi is rejected by the script guard BEFORE intent resolution, exactly as before this story
    await _say(e, "haan woh achha phir kya hua", "hi")
    assert s.unrelated_count == 1 and s.outcome == "unrelated_caller" and not s.ws.closed
    said = await _say(e, "Aur batao kya chal raha hai?", "en", "unrelated_caller")
    assert said == [FINAL["en"]] and s.ws.closed


@pytest.mark.asyncio
async def test_random_fragmented_speech_that_reaches_the_model_as_unclear_stays_unclear(m, e):
    said = await _say(e, "umm the thing you know", "en", "unclear")
    assert said == [phrase("unclear", "en")] and e.session.unrelated_count == 0 and e.session.outcome == "completed"


# ============================================================================= a normal hospital call is unchanged


@pytest.mark.asyncio
async def test_a_normal_hospital_call_never_touches_the_unrelated_state(m, e):
    s = e.session
    await _say(e, "hello", "en", "smalltalk")
    said = await _say(e, "how much is the CBC", "en", "test_rate", {"test_name": "CBC"})
    assert said == [PRICE]
    assert (
        s.unrelated_count == 0 and s.outcome == "completed" and s.signals.unrelated_caller is False and not s.ws.closed
    )


# ============================================================================= the live catalogue's half of the veto (real catalogue)


@pytest.fixture(scope="module")
def real_fast_path():
    import datetime

    from _clinic_app import clinic_app

    from agent.fast_path import Catalogue, FastPath

    with clinic_app(sample_patients=False) as (_app, client):
        payload = client.get("/api/v1/catalogue").json()
    return FastPath(Catalogue(payload), today=datetime.date(2026, 10, 5))


def test_a_real_test_name_from_the_catalogue_vetoes_a_wrong_number(m, monkeypatch, real_fast_path):
    from agent import unrelated_caller

    monkeypatch.setattr(m, "_fast_path", real_fast_path)
    assert m._catalogue_entity_in("tsh", "en") is True  # a real test in the seed catalogue
    assert m._catalogue_entity_in("suresh hai kya", "en") is False  # a bare first name never counts
    assert unrelated_caller.classify("sorry wrong number TSH", "en", m._catalogue_entity_in) is None
    assert (
        unrelated_caller.classify("sorry wrong number", "en", m._catalogue_entity_in) == unrelated_caller.WRONG_NUMBER
    )
    assert unrelated_caller.classify("Suresh hai kya", "en", m._catalogue_entity_in) == unrelated_caller.PERSON_DIRECTED


def test_with_no_catalogue_loaded_the_entity_check_just_answers_no(m, monkeypatch):
    monkeypatch.setattr(m, "_fast_path", None)
    assert m._catalogue_entity_in("tsh", "en") is False
