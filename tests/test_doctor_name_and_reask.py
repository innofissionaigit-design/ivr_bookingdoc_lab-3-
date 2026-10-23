"""Live 2026-10-10, after "আমি গাইনির একজন ডাক্তার কে দেখাব" read out the four Gynaecology doctors:

  1. "অপর্ণা বসু" (a doctor just offered) got "নমস্কার, কী সাহায্য করতে পারি?" -- the model filed three of the four
     bare names as `unrelated_caller`, and no rule asked the clinic about a sentence without a "doctor"/"see"/
     "sits" word in it.
  2. The caller thinking for ~10 s heard the WHOLE doctor list again, twice ("আপনি কি লাইনে আছেন? <list>",
     "কোনো তাড়া নেই। <list>"): the question kept for a silence re-ask was the entire reply.

Both fixes are gated, so half of this file is the other side: a name that was NOT offered, a patient's surname once
the doctor is settled, and a read-back that must keep every detail in its re-ask.

    python -m pytest tests/test_doctor_name_and_reask.py -v
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

BASU = {"name": "Dr. A. Basu", "full_name": "Dr. Aparna Basu", "full_name_bn": "অপর্ণা বসু", "full_name_hi": "अपर्णा बसु"}
SENGUPTA = {"name": "Dr. P. Sengupta", "full_name": "Dr. Parmita Sengupta", "full_name_bn": "পারমিতা সেনগুপ্ত", "full_name_hi": "पारमिता सेनगुप्त"}
MITRA = {"name": "Dr. R. Mitra", "full_name": "Dr. Rina Mitra", "full_name_bn": "রিনা মিত্র", "full_name_hi": "रीना मित्र"}
CHATTERJEE = {"name": "Dr. S. Chatterjee", "full_name": "Dr. Sutapa Chatterjee", "full_name_bn": "সুতপা চ্যাটার্জী", "full_name_hi": "सुतपा चैटर्जी"}
GYNAE = [BASU, SENGUPTA, MITRA, CHATTERJEE]
NOT_OFFERED = {"name": "Dr. R. Chowdhury", "full_name": "Dr. Rajiv Chowdhury", "full_name_bn": "রাজীব চৌধুরী", "full_name_hi": "राजीव चौधरी"}
EVERYONE = GYNAE + [NOT_OFFERED]

AVAIL = "AVAILABILITY-ANSWER"
LIST_QUESTION = "কার কাছে অ্যাপয়েন্টমেন্ট নিতে চান?"


def clinic(env):
    """The clinic's department table (Gynaecology only) and its reading of which doctor a sentence names."""

    async def doctors_by_department(query):
        if "গাইনি" in query:
            return {"found": True, "department": "Gynaecology & Obstetrics",
                    "department_name": "Gynaecology & Obstetrics", "doctors": GYNAE}
        return {"found": False}

    async def route_department(query, lang):
        return {"matched": False}

    async def doctors_mentioned(text):
        named = [d for d in EVERYONE if d["full_name_bn"] in text]
        one = len(named) == 1
        return {"found": one, "count": len(named), "doctors": named, "doctor_name": named[0]["name"] if one else None}

    env.tools.doctors_by_department = doctors_by_department
    env.tools.route_department = route_department
    env.tools.doctors_mentioned = doctors_mentioned
    env.state["lang"] = "bn"
    env.session.disclosed_langs.add("bn")
    env.state["reply"] = AVAIL


async def read_out_gynae_list(env, text="আমি গাইনির একজন ডাক্তার কে দেখাব"):
    env.state["reply"] = None  # the real enquiry answerer returns None for a department question with no symptom
    said = await env.say(text, "department_query", {"doctor_name": "গাইনি"})
    env.state["reply"] = AVAIL
    assert "অপর্ণা বসু" in text_of(said)
    return said


# ================================================================== 1. the bare name is the answer
@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["unrelated_caller", "smalltalk", "unclear", "doctor_availability"])
@pytest.mark.parametrize("doctor", GYNAE, ids=[d["name"] for d in GYNAE])
async def test_every_doctor_read_out_is_taken_when_named_bare_whatever_the_model_called_it(m, env, doctor, label):
    clinic(env)
    await read_out_gynae_list(env)
    assert env.session.booking is not None and env.session.booking.action == "book_appointment"
    said = await env.say(doctor["full_name_bn"], label, {})
    st = env.session.booking
    assert st is not None and st.action == "book_appointment"
    assert st.slots.get("doctor_name") == doctor["name"]
    assert "নমস্কার" not in text_of(said)  # not greeted afresh


@pytest.mark.asyncio
@pytest.mark.parametrize("doctor", GYNAE, ids=[d["name"] for d in GYNAE])
async def test_a_doctor_named_bare_after_an_information_list_is_booked(m, env, doctor):
    """The list came from a question that did not say 'I will see' (no booking opened); the next turn naming one of
    those doctors is still the answer to 'কার কাছে অ্যাপয়েন্টমেন্ট নিতে চান?'."""
    clinic(env)
    await read_out_gynae_list(env, text="গাইনি বিভাগ")
    assert env.session.booking is None and env.session.offered_doctors is not None
    await env.say(doctor["full_name_bn"], "unrelated_caller", {})
    st = env.session.booking
    assert st is not None and st.action == "book_appointment"
    assert st.slots.get("doctor_name") == doctor["name"]


@pytest.mark.asyncio
async def test_a_doctor_who_was_not_offered_is_not_booked_on_the_strength_of_the_list(m, env):
    clinic(env)
    await read_out_gynae_list(env, text="গাইনি বিভাগ")
    await env.say(NOT_OFFERED["full_name_bn"], "unrelated_caller", {})
    assert env.session.booking is None


@pytest.mark.asyncio
async def test_a_bare_name_with_no_list_and_no_booking_is_not_taken_for_a_doctor(m, env):
    """Nothing said a doctor's name was expected: a bare name stays as ambiguous as it always was."""
    clinic(env)
    await env.say(BASU["full_name_bn"], "unrelated_caller", {})
    assert env.session.booking is None


@pytest.mark.asyncio
async def test_the_list_is_only_the_answer_for_one_turn(m, env):
    clinic(env)
    await read_out_gynae_list(env, text="গাইনি বিভাগ")
    await env.say("ধন্যবাদ", "smalltalk", {})  # the next turn is something else
    await env.say(BASU["full_name_bn"], "unrelated_caller", {})
    assert env.session.booking is None


# ---- the patient's surname must never be read as a doctor once the doctor is settled
def test_a_settled_doctor_means_a_name_is_the_patients_not_a_doctor(m):
    class Sess:
        pass

    s = Sess()
    s.offered_doctors = None
    s.turn_count = 5
    s.booking = m.new_state("book_appointment")
    assert m._expecting_a_doctor_name(s) is True  # asking which doctor
    s.booking.doctor_verified = True
    assert m._expecting_a_doctor_name(s) is False  # now the PATIENT's name is what is asked
    s.booking = m.new_state("cancel_appointment")
    assert m._expecting_a_doctor_name(s) is False  # a cancellation never asks which doctor
    s.booking = None
    assert m._expecting_a_doctor_name(s) is False
    s.offered_doctors = (["Dr. A. Basu"], 4)
    assert m._expecting_a_doctor_name(s) is True  # read out on the previous turn
    s.offered_doctors = (["Dr. A. Basu"], 2)
    assert m._expecting_a_doctor_name(s) is False  # too long ago


# ================================================================== 2. the re-ask repeats the question, not the list
@pytest.mark.parametrize(
    "text,expected",
    [
        ("গাইনি ও প্রসূতি বিভাগে এই ডাক্তাররা বসেন -- অপর্ণা বসু, রিনা মিত্র। কার কাছে অ্যাপয়েন্টমেন্ট নিতে চান?",
         "কার কাছে অ্যাপয়েন্টমেন্ট নিতে চান?"),
        ("These are the doctors in Gynaecology -- Dr. A. Basu, Dr. R. Mitra. Who would you like an appointment with?",
         "Who would you like an appointment with?"),
        ("गायनी विभाग में ये डॉक्टर बैठते हैं -- अपर्णा बसु, रीना मित्र। किनके साथ अपॉइंटमेंट लेना चाहेंगे?",
         "किनके साथ अपॉइंटमेंट लेना चाहेंगे?"),
        ("কোন বিভাগের কথা বলছেন?", "কোন বিভাগের কথা বলছেন?"),  # already just the question
        ("আপনার কনফার্মেশন নম্বর KCD-1001।", "আপনার কনফার্মেশন নম্বর KCD-1001।"),  # not a question: untouched
        ("", ""),
    ],
)
def test_question_only_keeps_the_last_question_and_leaves_everything_else(m, text, expected):
    assert m._question_only(text) == expected


@pytest.mark.asyncio
async def test_after_a_list_the_question_kept_for_a_re_ask_is_only_the_question(m, env):
    clinic(env)
    said = await read_out_gynae_list(env)
    assert "অপর্ণা বসু" in text_of(said)  # read out in full, once
    assert env.session.open_question[0] == LIST_QUESTION


@pytest.mark.asyncio
async def test_a_silence_re_ask_after_the_list_does_not_read_the_list_again(m, env):
    clinic(env)
    await read_out_gynae_list(env)
    before = len(env.session.ws.spoken())
    await m._silence_during_question(env.session, 999.0)
    reasked = text_of(env.session.ws.spoken()[before:])
    assert LIST_QUESTION in reasked
    assert "অপর্ণা বসু" not in reasked and "রিনা মিত্র" not in reasked
    # and a second re-ask, "no hurry", is the same short question
    before = len(env.session.ws.spoken())
    await m._silence_during_question(env.session, 999.0)
    assert "অপর্ণা বসু" not in text_of(env.session.ws.spoken()[before:])


@pytest.mark.asyncio
async def test_a_question_that_carries_details_is_re_asked_in_full(m, env):
    """The re-ask is shortened ONLY for lists. A read-back keeps every detail the caller is confirming."""
    clinic(env)
    full = "আপনার অ্যাপয়েন্টমেন্ট: ডাঃ বসু, 2026-10-13, 10:15, রোগী রাজস্বী। সব ঠিক আছে?"
    await m._speak(env.session, full, "bn")
    assert env.session.open_question[0] == full


# ================================================================== 3. the model cannot label the name at all
# The audit of all 32 doctors on the pod: for "সঞ্জয় গুহ" and "রূপা মজুমদার" the model answered "doctor_name" four times
# -- not an intent -- and the caller heard the "a little trouble, please hold" apology for naming a doctor.
GUHA = {"name": "Dr. S. Guha", "full_name": "Dr. Sanjay Guha", "full_name_bn": "সঞ্জয় গুহ", "full_name_hi": "संजय गुहा"}


def _model_fails(m, env, monkeypatch):
    async def resolve(session, text, lang):
        raise m.ExtractionError("intent extraction did not complete (invalid intent 'doctor_name')")

    monkeypatch.setattr(m, "_resolve_intent", resolve)


@pytest.mark.asyncio
async def test_a_doctor_named_while_the_booking_asks_which_is_taken_when_the_model_cannot_label_it(m, env, monkeypatch):
    clinic(env)
    await read_out_gynae_list(env)  # the booking is open and asking "which doctor?"
    _model_fails(m, env, monkeypatch)
    said = await env.say(MITRA["full_name_bn"], "unclear", {})
    assert m.phrase("llm_failure", "bn") not in text_of(said)
    assert env.session.booking.slots.get("doctor_name") == MITRA["name"]


@pytest.mark.asyncio
async def test_the_apology_is_unchanged_when_no_doctor_name_was_expected(m, env, monkeypatch):
    clinic(env)
    _model_fails(m, env, monkeypatch)
    said = await env.say(MITRA["full_name_bn"], "unclear", {})
    assert m.phrase("llm_failure", "bn") in text_of(said)  # nothing asked for a doctor: as before


@pytest.mark.asyncio
async def test_the_apology_is_unchanged_when_the_clinic_names_nobody(m, env, monkeypatch):
    clinic(env)
    await read_out_gynae_list(env)
    _model_fails(m, env, monkeypatch)
    said = await env.say("কিছু একটা বলছি যা কোনো ডাক্তারের নাম নয়", "unclear", {})
    assert m.phrase("llm_failure", "bn") in text_of(said)
