"""Live 2026-10-10: Department -> doctor -> booking was not one flow.

    "আপনাদের ওখানে কোন কোন ডিপার্টমেন্ট রয়েছে"          the departments were read out
    "আমি জেনারেল মেডিসিনের একজনকে দেখাব"                  the department's doctors were read out, "who would you like an
                                                          appointment with?" -- and NO booking was opened
    "অরিন্দম সেনকে দেখাতে চাই"                            answered with when Dr Sen sits, then "anything else?"
    "পৌনে এগারোটার সময় দেখাবো"                           answered with the clinic's OPENING HOURS ("সময়")
    "পৌনে এগারোটার সময় দেখাবো ডক্টরকে"                    "there is no doctor called পৌনে এগারোটার"

Each fix is gated tightly, so most of this file is the other side: a caller who only ASKS (a department's doctors,
when a doctor sits, the clinic's hours) is answered exactly as before.

    python -m pytest tests/test_department_to_booking.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_orchestrator_booking_flow import FakeTools, m, env, text_of  # noqa: E402,F401

AVAIL = "AVAILABILITY-ANSWER"
HOURS = "HOURS-FAQ-ANSWER"

SEN = {"name": "Dr. A. Sen", "full_name": "Dr. Arindam Sen", "full_name_bn": "অরিন্দম সেন", "full_name_hi": "अरिंदम सेन"}
GHOSH = {"name": "Dr. P. Ghosh", "full_name": "Dr. Probir Ghosh", "full_name_bn": "প্রবীর ঘোষ", "full_name_hi": "प्रबीर घोष"}
OTHER = {"name": "Dr. T. Bose", "full_name": "Dr. Tapan Bose", "full_name_bn": "তাপস বসু", "full_name_hi": "तापस बोस"}
GENMED = {"found": True, "department": "General Medicine", "department_name": "General Medicine",
          "doctors": [SEN, GHOSH]}


def no_enquiry_answer(env):
    """A department question with no symptom: the real _answer_enquiry_intent returns None, which is what sends the
    turn to the department-list / booking branch. The harness stub returns state["reply"], so set it to None."""
    env.state["reply"] = None


def speak_as(env, text):
    """Bengali calls are the live case; an English sentence needs the call to be English, or the harness's
    script-mismatch check answers before the code under test runs."""
    if text.isascii():
        env.state["lang"] = "en"


def clinic(env, mentions=None):
    """The clinic's department lookup and doctor-mention reading, for this test. `mentions`: the doctor dicts the
    clinic would say a sentence names (default: nobody)."""

    async def doctors_by_department(query):
        return GENMED if "মেডিসিন" in query or "medicine" in query.lower() else {"found": False}

    async def route_department(query, lang):
        return {"matched": False}

    async def doctors_mentioned(text):
        named = list(mentions or [])
        one = len(named) == 1
        return {"found": one, "count": len(named), "doctors": named, "doctor_name": named[0]["name"] if one else None}

    env.tools.doctors_by_department = doctors_by_department
    env.tools.route_department = route_department
    env.tools.doctors_mentioned = doctors_mentioned
    env.state["reply"] = AVAIL
    # The real calls are Bengali: a Bengali transcript under a fake "en" language trips the script-mismatch check
    # outside a booking before any of the code under test runs.
    env.state["lang"] = "bn"
    env.session.disclosed_langs.add("bn")


# ===================================================== department -> the booking opens
@pytest.mark.asyncio
async def test_a_department_named_by_a_caller_who_wants_to_see_someone_opens_the_booking(m, env):
    clinic(env)
    no_enquiry_answer(env)
    said = await env.say("আমি জেনারেল মেডিসিনের একজনকে দেখাব", "department_query", {"doctor_name": "জেনারেল মেডিসিন"})
    assert "অরিন্দম সেন" in text_of(said) and "প্রবীর ঘোষ" in text_of(said)  # the department's doctors were read out
    st = env.session.booking
    assert st is not None and st.action == "book_appointment"
    assert [c[0] for c in st.doctor_choices] == ["Dr. A. Sen", "Dr. P. Ghosh"]


@pytest.mark.asyncio
async def test_the_doctor_chosen_from_that_list_continues_the_same_booking(m, env):
    clinic(env)
    no_enquiry_answer(env)
    await env.say("আমি জেনারেল মেডিসিনের একজনকে দেখাব", "department_query", {"doctor_name": "জেনারেল মেডিসিন"})
    env.state["reply"] = AVAIL
    said = await env.say("অরিন্দম সেনকে দেখাতে চাই", "book_appointment", {"doctor_name": "অরিন্দম সেন"})
    st = env.session.booking
    assert st is not None and st.action == "book_appointment"
    assert st.slots.get("doctor_name") in ("Dr. A. Sen", "অরিন্দম সেন")
    assert AVAIL not in text_of(said)


@pytest.mark.asyncio
async def test_a_department_asked_about_without_wanting_to_see_anyone_is_only_the_list(m, env):
    """Unchanged: an information question is answered, and no booking is opened behind the caller's back."""
    clinic(env)
    no_enquiry_answer(env)
    said = await env.say("জেনারেল মেডিসিন বিভাগ", "department_query", {"doctor_name": "জেনারেল মেডিসিন"})
    assert "অরিন্দম সেন" in text_of(said)  # the list is still read out ...
    assert env.session.booking is None  # ... and nothing is opened behind the caller's back


# ===================================================== a doctor named -> the booking
@pytest.mark.asyncio
async def test_the_doctor_named_as_the_answer_to_the_list_just_read_out_is_booked(m, env):
    clinic(env, mentions=[SEN])
    no_enquiry_answer(env)
    await env.say("জেনারেল মেডিসিন বিভাগ", "department_query", {"doctor_name": "জেনারেল মেডিসিন"})
    assert env.session.booking is None
    env.state["reply"] = AVAIL
    await env.say("অরিন্দম সেন", "doctor_availability", {"doctor_name": "অরিন্দম সেন"})
    st = env.session.booking
    assert st is not None and st.action == "book_appointment"
    assert st.slots.get("doctor_name") == "Dr. A. Sen"  # the doctor was carried into the booking, not just opened


@pytest.mark.asyncio
async def test_the_same_name_with_no_list_read_out_is_still_an_availability_question(m, env):
    clinic(env, mentions=[SEN])
    said = await env.say("অরিন্দম সেন", "doctor_availability", {"doctor_name": "অরিন্দম সেন"})
    assert env.session.booking is None
    assert AVAIL in text_of(said)


@pytest.mark.asyncio
async def test_a_doctor_who_was_not_in_the_list_is_not_booked_on_its_strength(m, env):
    clinic(env, mentions=[OTHER])
    no_enquiry_answer(env)
    await env.say("জেনারেল মেডিসিন বিভাগ", "department_query", {"doctor_name": "জেনারেল মেডিসিন"})
    env.state["reply"] = AVAIL
    await env.say("তাপস বসু", "doctor_availability", {"doctor_name": "তাপস বসু"})
    assert env.session.booking is None


@pytest.mark.asyncio
async def test_a_name_that_is_not_exactly_one_doctor_is_never_booked(m, env):
    clinic(env, mentions=[SEN, GHOSH])  # the clinic cannot tell which: ask, never pick
    no_enquiry_answer(env)
    await env.say("জেনারেল মেডিসিন বিভাগ", "department_query", {"doctor_name": "জেনারেল মেডিসিন"})
    env.state["reply"] = AVAIL
    await env.say("সেন", "doctor_availability", {"doctor_name": "সেন"})
    assert env.session.booking is None


@pytest.mark.asyncio
async def test_i_want_to_see_dr_sen_opens_a_booking_without_a_list_first(m, env):
    clinic(env, mentions=[SEN])
    await env.say("অরিন্দম সেনকে দেখাতে চাই", "doctor_availability", {"doctor_name": "অরিন্দম সেন"})
    st = env.session.booking
    assert st is not None and st.action == "book_appointment"
    assert st.slots.get("doctor_name") == "Dr. A. Sen"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "অরিন্দম সেন কবে বসেন",  # when does he sit
        "ডাক্তার সেন কখন বসেন দেখাতে চাই",  # asks when AND wants: the question wins (conservative)
        "is Dr Sen available tomorrow",
    ],
)
async def test_a_question_about_when_a_doctor_sits_stays_an_availability_answer(m, env, text):
    clinic(env, mentions=[SEN])
    speak_as(env, text)
    await env.say(text, "doctor_availability", {"doctor_name": "অরিন্দম সেন"})
    # What the original code does with these sentences is unchanged (a "দেখাতে চাই" may still open an EMPTY booking,
    # and "tomorrow" is still confirmed first). What must not happen is the new conversion: the doctor booked.
    st = env.session.booking
    assert st is None or not st.slots.get("doctor_name")


# ===================================================== a time with "I will see"
@pytest.mark.asyncio
async def test_a_time_with_i_will_see_outside_a_booking_starts_one_not_the_hours_faq(m, env):
    clinic(env)
    env.state["reply"] = HOURS
    said = await env.say("পৌনে এগারোটার সময় দেখাবো", "clinic_faq", {"faq_topic": "hours"})
    assert HOURS not in text_of(said)
    st = env.session.booking
    assert st is not None and st.action == "book_appointment"
    assert st.slots.get("time_slot") == "10:45"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["ক্লিনিক কটায় খোলে", "ক্লিনিকের সময় কত", "ছটার সময়", "what time do you open"])
async def test_the_hours_questions_and_a_bare_time_outside_a_booking_are_still_the_faq(m, env, text):
    clinic(env)
    env.state["reply"] = HOURS
    speak_as(env, text)
    said = await env.say(text, "clinic_faq", {"faq_topic": "hours"})
    assert env.session.booking is None
    assert HOURS in text_of(said)


# ===================================================== a department filed as the doctor's name
# Live 2026-10-10: "আমি জেনারেল মেডিসিনের একজন ডক্টরকে দেখাব" came back doctor_availability with doctor_name
# "জেনারেল মেডিসিন" (reproduced on the pod), and the caller heard "there is no doctor called জেনারেল মেডিসিন".
DEPT_AS_DOCTOR = {"doctor_name": "জেনারেল মেডিসিন"}


@pytest.mark.asyncio
async def test_a_department_in_the_doctor_slot_of_a_booking_request_opens_the_booking(m, env):
    clinic(env)  # the clinic's reading names no doctor; its department table knows General Medicine
    said = await env.say("আমি জেনারেল মেডিসিনের একজন ডক্টরকে দেখাব", "doctor_availability", dict(DEPT_AS_DOCTOR))
    assert AVAIL not in text_of(said)  # not looked up as a person
    assert "অরিন্দম সেন" in text_of(said) and "প্রবীর ঘোষ" in text_of(said)
    st = env.session.booking
    assert st is not None and st.action == "book_appointment"
    assert [c[0] for c in st.doctor_choices] == ["Dr. A. Sen", "Dr. P. Ghosh"]


@pytest.mark.asyncio
async def test_a_department_in_the_doctor_slot_of_a_plain_question_is_the_list_only(m, env):
    clinic(env)
    said = await env.say("জেনারেল মেডিসিনের ডাক্তার", "doctor_availability", {"doctor_name": "জেনারেল মেডিসিনের ডাক্তার"})
    assert AVAIL not in text_of(said)
    assert "অরিন্দম সেন" in text_of(said)
    assert env.session.booking is None  # an information question opens nothing
    assert env.session.offered_doctors is not None  # but the next turn naming one of them is the answer


@pytest.mark.asyncio
async def test_a_real_doctor_named_is_still_looked_up_as_a_doctor(m, env):
    clinic(env, mentions=[SEN])
    said = await env.say("ডাক্তার সেন", "doctor_availability", {"doctor_name": "সেন"})
    assert AVAIL in text_of(said)


@pytest.mark.asyncio
async def test_a_name_that_is_no_department_is_still_looked_up_as_a_doctor(m, env):
    """The clinic's department table finds nothing: the lookup is the one it always was (and says not found)."""
    clinic(env)
    said = await env.say("ডাক্তার নোবডি", "doctor_availability", {"doctor_name": "নোবডি"})
    assert AVAIL in text_of(said)


# ===================================================== a time is not a doctor
def test_a_clock_time_in_the_doctor_slot_is_dropped_and_a_name_is_kept(m):
    drop = m._drop_a_time_filed_as_doctor
    assert "doctor_name" not in drop({"doctor_name": "পৌনে এগারোটার", "date": "2026-10-13"})
    assert drop({"doctor_name": "পৌনে এগারোটার", "date": "2026-10-13"}).get("date") == "2026-10-13"
    for name in ("অরিন্দম সেন", "সেন", "Dr. A. Sen", "ঘোষ", "দাস"):
        assert drop({"doctor_name": name}) == {"doctor_name": name}
    assert drop({}) == {}
