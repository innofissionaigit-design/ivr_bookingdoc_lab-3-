"""Live 2026-10-10: "ছটার সময়" ("at six"), said to a booking that had just asked "কোন সময়ের জন্য?", carries
"সময়" -- the bare keyword of the clinic's `hours` FAQ -- so the keyword path answered the OPENING HOURS with no
model call, three turns in a row, and the time was never taken.

The fix is deliberately narrow (agent/booking_time_answer.py + one guarded block in _dispatch_turn). Most of this
file is therefore the other side: a caller who asks the clinic's timing, or when a doctor sits, in the MIDDLE of a
booking must still be answered as before.

    python -m pytest tests/test_booking_time_answer.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.booking_time_answer import is_a_time_answer  # noqa: E402

# ================================================================== 1. the pure cue


@pytest.mark.parametrize(
    "text",
    [
        "ছটার সময়",  # the live sentence
        "সাড়ে ছটার সময়",
        "ঐদিনই করুন সাড়ে ছটার সময়",  # the live sentence that also said the day
        "সন্ধ্যা সাতটার সময়",
        "ছটায়",
        "six pm",
        "at six thirty in the evening",
        "शाम छह बजे",
        "छह बजे के समय",
    ],
)
def test_a_clock_time_said_as_an_answer_is_one(text):
    assert is_a_time_answer(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        # no clock time at all: these are the hours FAQ's own questions
        "ক্লিনিকের সময় কত",
        "ক্লিনিক কটায় খোলে",
        "আপনাদের সময় কখন",
        "ক্লিনিক রবিবার কখন বন্ধ হয়",
        "what time do you open",
        "what are the clinic timings",
        "क्लिनिक कब खुलता है",
        # a clock time AND a question about hours / a doctor / availability: still a question
        "সকাল আটটায় খোলে?",
        "আটটায় খোলা থাকে কি",
        "do you open at eight",
        "आठ बजे खुलता है क्या",
        "ছটার সময় ডাক্তার আছেন",
        "ডাক্তার সেন কখন বসেন",
        "সকাল আটটার সময় ক্লিনিক",  # talks about the clinic: addressed to it, not the booking's answer
    ],
)
def test_anything_that_asks_about_hours_a_doctor_or_availability_is_not_an_answer(text):
    assert is_a_time_answer(text) is False


# ================================================================== 2. through the real orchestrator
# Same harness as tests/test_orchestrator_booking_flow.py: the real _dispatch_turn / _speak / helpers from
# main_pcm.py, the extractor and the clinic tools faked. `_resolve_intent` is replaced, so the intent the fast path
# would have produced ("clinic_faq", topic "hours") is injected directly -- exactly the live failure.
from test_orchestrator_booking_flow import FakeTools, m, env, book, text_of  # noqa: E402,F401

HOURS = "HOURS-FAQ-ANSWER"


def _hours(env):
    env.state["reply"] = HOURS  # what _answer_enquiry_intent returns for the FAQ

    # FakeTools predates the department lookup _ask_which_doctor makes; the clinic knows no department here.
    async def doctors_by_department(query):
        return {"found": False}

    async def route_department(query, lang):
        return {"matched": False}

    env.tools.doctors_by_department = doctors_by_department
    env.tools.route_department = route_department


@pytest.mark.asyncio
async def test_a_time_given_to_an_open_booking_is_taken_not_answered_as_the_hours(m, env):
    _hours(env)
    await book(env, doctor_name="Sen")
    await env.say("tomorrow", "book_appointment", {"date": "2026-10-01"})
    assert env.session.booking.slots.get("time_slot") is None  # the booking is waiting for the time
    said = await env.say("ছটার সময়", "clinic_faq", {"faq_topic": "hours"})
    assert HOURS not in text_of(said)
    assert env.session.booking.slots.get("time_slot") == "18:00"


@pytest.mark.asyncio
async def test_a_time_with_the_day_in_one_sentence_is_taken_too(m, env):
    _hours(env)
    await book(env, doctor_name="Sen")
    said = await env.say("ঐদিনই করুন সাড়ে ছটার সময়", "clinic_faq", {"faq_topic": "hours"})
    assert HOURS not in text_of(said)
    assert env.session.booking.slots.get("time_slot") == "18:30"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "ক্লিনিক কটায় খোলে",
        "ক্লিনিকের সময় কত",
        "সকাল আটটায় খোলে?",
        "what time do you open",
        "do you open at eight",
    ],
)
async def test_the_clinics_timing_asked_in_the_middle_of_a_booking_is_still_answered(m, env, text):
    """The case the fix must not break: a caller mid-booking asks the clinic's hours and gets them."""
    _hours(env)
    await book(env, doctor_name="Sen")
    await env.say("tomorrow", "book_appointment", {"date": "2026-10-01"})
    said = await env.say(text, "clinic_faq", {"faq_topic": "hours"})
    assert HOURS in text_of(said)
    assert env.session.booking is not None and env.session.booking.slots.get("time_slot") is None


@pytest.mark.asyncio
async def test_a_time_outside_any_booking_is_still_the_hours_faq(m, env):
    _hours(env)
    said = await env.say("what time does the clinic open at six", "clinic_faq", {"faq_topic": "hours"})
    assert HOURS in text_of(said)  # unchanged: with no booking open there is nothing for it to be the answer to


@pytest.mark.asyncio
async def test_a_time_while_the_doctor_is_still_being_asked_is_left_to_the_faq(m, env):
    """Not waiting for a day or a time yet, so the rule does not apply (conservative)."""
    _hours(env)
    await env.say("I want to book an appointment", "book_appointment", {})
    assert env.session.booking.slots.get("doctor_name") is None
    said = await env.say("ছটার সময়", "clinic_faq", {"faq_topic": "hours"})
    assert HOURS in text_of(said)


@pytest.mark.asyncio
async def test_another_faq_topic_is_never_taken_as_a_time_answer(m, env):
    _hours(env)
    await book(env, doctor_name="Sen")
    await env.say("tomorrow", "book_appointment", {"date": "2026-10-01"})
    said = await env.say("ছটার সময়", "clinic_faq", {"faq_topic": "contact_number"})
    assert HOURS in text_of(said)
    assert env.session.booking.slots.get("time_slot") is None
