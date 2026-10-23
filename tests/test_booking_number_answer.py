"""Live 2026-10-10: the booking asked "রোগীর নাম আর একটা ফোন নম্বর বলবেন?" and the caller answered "রোগীর নাম রাজস্বী
ফোন নম্বার ওন ওন টু টু থ্রি থ্রি ফোর ফোর ফাইভ ফাইভ". "ফোন নম্বর" is the keyword of the clinic's `contact_number` FAQ, so
the keyword path read out the HELPDESK number and the booking's name and number were never taken.

The number was read correctly (agent/spoken_digits gave 1122334455). What was off was the guard that keeps a number
given where one is asked for away from the keyword path (`_number_expected`): it only held while the booking's FIRST
missing field was the phone -- and with a grouped "name and number" question the first missing field is still the
name.

Narrow on purpose, so most of this file is the other side: the clinic's contact number asked for in the middle of a
booking is still answered, and so is a number given when nothing asked for one.

    python -m pytest tests/test_booking_number_answer.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_orchestrator_booking_flow import ASK_DETAILS, FakeTools, m, env, book, text_of  # noqa: E402,F401

FAQ = "CONTACT-FAQ-ANSWER"
LIVE = "রোগীর নাম রাজস্বী ফোন নম্বার ওন ওন টু টু থ্রি থ্রি ফোর ফোর ফাইভ ফাইভ"
GOOD = "রোগীর নাম রাজস্বী ফোন নম্বর নয় আট তিন শূন্য এক দুই তিন চার পাঁচ ছয়"


async def _asking_name_and_number(env):
    """A booking at the grouped 'patient name and a phone number?' question."""
    env.state["reply"] = FAQ
    await book(env, doctor_name="Sen")
    said = await env.say("tomorrow at ten", "book_appointment", {"date": "2026-10-01", "time_slot": "10:00"})
    assert ASK_DETAILS in text_of(said)
    return env.session


# ------------------------------------------------------------------ the guard itself
@pytest.mark.asyncio
async def test_a_number_in_the_answer_to_name_and_number_is_expected(m, env):
    session = await _asking_name_and_number(env)
    assert m._number_expected(session, LIVE) is True
    assert m._number_expected(session, GOOD) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "আপনাদের ফোন নম্বর কত",  # a question about the clinic: no number in it
        "আপনাদের ফোন নম্বরটা কী",
        "রোগীর নাম রাজস্বী",  # the name alone
        "what is your phone number",
    ],
)
async def test_a_sentence_with_no_number_is_never_taken_as_one(m, env, text):
    session = await _asking_name_and_number(env)
    assert m._number_expected(session, text) is False


@pytest.mark.asyncio
async def test_a_number_before_the_booking_asks_for_one_is_not_expected(m, env):
    """Waiting for the DAY, not the name and number: the guard stays off, as it always did."""
    env.state["reply"] = FAQ
    await book(env, doctor_name="Sen")
    assert m._number_expected(env.session, LIVE) is False


# ------------------------------------------------------------------ through the real orchestrator
@pytest.mark.asyncio
async def test_the_model_filing_the_answer_as_the_contact_faq_is_taken_back(m, env):
    session = await _asking_name_and_number(env)
    said = await env.say(GOOD, "clinic_faq", {"faq_topic": "contact_number"})
    assert FAQ not in text_of(said)
    assert session.booking is not None and session.booking.slots.get("phone") == "9830123456"


@pytest.mark.asyncio
async def test_the_clinics_contact_number_asked_mid_booking_is_still_answered(m, env):
    session = await _asking_name_and_number(env)
    said = await env.say("আপনাদের ফোন নম্বর কত", "clinic_faq", {"faq_topic": "contact_number"})
    assert FAQ in text_of(said)
    assert session.booking is not None and not session.booking.slots.get("phone")


@pytest.mark.asyncio
async def test_a_number_outside_any_booking_is_still_the_faq(m, env):
    env.state["reply"] = FAQ
    said = await env.say("what is your phone number 9830123456", "clinic_faq", {"faq_topic": "contact_number"})
    assert FAQ in text_of(said)  # nothing open for it to be the answer to
