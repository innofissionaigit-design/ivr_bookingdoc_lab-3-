"""Live 2026-10-10: a booking ended its patient-name turn with "ঠিক আছে, বদলে সম্পর্ক রোগী করে দিলাম।" -- "I have
changed the relationship to patient" -- which nobody said and nothing meant.

The model (temperature 0, so it repeats) filled `relationship` with "একজন" ("one person") for "আমি গাইনির একজন
ডাক্তারকে দেখাবো", and with "রোগী" ("patient") for "রোগীর নাম রাজু". The first was stored on the booking; the second
counted as a correction of it and was announced. agent/relationship_words.py keeps a value only if it is a word a
caller uses for a relation.

    python -m pytest tests/test_relationship_words.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.relationship_words import clean_relationship, is_a_relationship

# ================================================================== the pure check
NOT_RELATIONSHIPS = ["একজন", "রোগী", "রোগীর", "একটা", "উনি", "কেউ", "patient", "someone", "one", "a person", "রাজু", "",
                     "   ", "ডাক্তার", "डॉक्टर", "मरीज़"]
RELATIONSHIPS = [
    "মা", "মায়ের", "বাবা", "বাবার", "ছেলে", "ছেলের", "মেয়ে", "স্ত্রী", "বউ", "স্বামী", "ভাই", "বোন", "দিদি", "দাদু", "বন্ধু",
    "আমার মা", "mother", "my mother", "Wife", "husband", "son", "daughter", "grandfather", "uncle",
    "माँ", "पिता", "बेटा", "बेटी", "पत्नी", "पति", "भाई", "बहन", "मेरी माँ", "बेटे की",
]


@pytest.mark.parametrize("value", NOT_RELATIONSHIPS)
def test_a_word_that_is_not_a_relation_is_not_one(value):
    assert is_a_relationship(value) is False


@pytest.mark.parametrize("value", RELATIONSHIPS)
def test_the_words_callers_use_for_a_relation_are_kept(value):
    assert is_a_relationship(value) is True


def test_clean_relationship_drops_only_the_bad_value_and_leaves_every_other_slot():
    slots = {"doctor_name": "Dr. A. Basu", "patient_name": "রাজু", "relationship": "রোগী", "patient_age": 62}
    assert clean_relationship(slots) == {"doctor_name": "Dr. A. Basu", "patient_name": "রাজু", "patient_age": 62}
    kept = {"patient_name": "রাজু", "relationship": "মা"}
    assert clean_relationship(kept) is kept  # untouched, same object
    assert clean_relationship({}) == {}
    assert clean_relationship({"relationship": None}) == {"relationship": None}


# ================================================================== through the real orchestrator
from test_orchestrator_booking_flow import FakeTools, m, env, book, text_of  # noqa: E402,F401
import test_doctor_name_and_reask as named  # noqa: E402

VALID = ["10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30", "11:45"]


def _clinic(env):
    named.clinic(env)

    async def mentioned(text):
        hits = [d for d in named.EVERYONE if d["full_name_bn"] in text or ("বসু" in text and d is named.BASU)]
        one = len(hits) == 1
        return {"found": one, "count": len(hits), "doctors": hits, "doctor_name": hits[0]["name"] if one else None}

    async def hold_slot(doctor, date, slot):
        if slot in VALID:
            return {"success": True, "hold_token": "h", "doctor_id": 7}
        return {"success": False, "reason": "invalid_slot", "valid_slots": VALID}

    env.tools.doctors_mentioned = mentioned
    env.tools.hold_slot = hold_slot


@pytest.mark.asyncio
async def test_the_live_conversation_never_announces_a_relationship_nobody_gave(m, env):
    """Every turn below carries the label and slots the model actually produced for it on the pod."""
    _clinic(env)
    env.state["reply"] = None
    first = await env.say("আমি গাইনির একজন ডাক্তারকে দেখাবো", "book_appointment", {"relationship": "একজন"})
    assert "অপর্ণা বসু" in text_of(first)  # the department's doctors, as before
    st = env.session.booking
    assert st is not None and "relationship" not in st.slots  # "একজন" is not stored

    env.state["reply"] = named.AVAIL
    said = []
    for text, intent, slots in [
        ("অপূর্ণা বসুর কাছে", "book_appointment", {"doctor_name": "অপূর্ণা বসু"}),
        ("মঙ্গলবার তেরো তারিখে", "unclear", {}),
        ("দশটা কুড়ি", "unclear", {}),
        ("সাড়ে দশটা করে দিন", "unclear", {}),
        ("রোগীর নাম রাজু", "book_test",
         {"test_name": "রোগীর নাম রাজু", "patient_name": "রাজু", "relationship": "রোগী"}),
    ]:
        said += await env.say(text, intent, dict(slots))
    spoken = text_of(said)
    assert "সম্পর্ক" not in spoken and "বদলে" not in spoken  # no "I changed the relationship to patient"
    st = env.session.booking
    assert st.slots.get("patient_name") == "রাজু"
    assert "relationship" not in st.slots
    assert "ফোন নম্বর" in spoken  # on to the phone number, which is what the caller should have been asked


@pytest.mark.asyncio
async def test_a_real_relationship_is_still_kept_and_a_real_change_still_acknowledged(m, env):
    _clinic(env)
    env.state["reply"] = None
    await env.say("আমি গাইনির একজন ডাক্তারকে দেখাবো", "book_appointment", {"relationship": "মা"})
    st = env.session.booking
    assert st.slots.get("relationship") == "মা"
    env.state["reply"] = named.AVAIL
    said = await env.say("না, আমার বাবার জন্য", "book_appointment", {"relationship": "বাবা"})
    assert st.slots.get("relationship") == "বাবা"
    assert "বদলে সম্পর্ক বাবা" in text_of(said)  # a genuine correction is still said
