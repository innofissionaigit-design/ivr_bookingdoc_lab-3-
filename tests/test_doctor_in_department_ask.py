"""Two live misses of 2026-10-09 about "which departments / is there a doctor in a department", and the sentences
that must NOT change because of the fixes.

1. English: "which outdoor or open department do you have" put the question word four words before "department",
   one past the 3-word reach of agent/doctor_list._asks_by_structure, so it reached the model, which filed the
   whole sentence as a symptom and the caller was sent to the counter.
2. Bengali: "... আপনাদের ওখানে ডাক্তার আছে ডায়াবেটোলজি" was filed by the model as lookup_booking and the caller
   was asked which number they had booked from (agent/doctor_in_department_ask.py).

Both fixes are narrow on purpose, so most of this file is the negative side: what each rule must still leave alone.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.doctor_in_department_ask import asks_if_a_doctor_is_there  # noqa: E402
from agent.doctor_list import asks_for_the_doctor_list  # noqa: E402
from agent.lookup_request import asks_what_is_booked, mentions_a_booking  # noqa: E402


# ================================================ 1. the English "which ... department do you have"
@pytest.mark.parametrize(
    "text",
    [
        "which outdoor or open department do you have",  # the live sentence
        "which outdoor or opd department do you have",
        "which special outpatient department do you have",
        "what kind of department do you have",  # already caught at 3 words; must stay caught
        "which department do you have",
        "which departments do you have there",
    ],
)
def test_a_which_department_question_is_a_list_request_however_it_is_qualified(text):
    assert asks_for_the_doctor_list(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # the same distance as the live sentence, a different question: no "... department do you have" tail
        "what time is the orthopaedics department available",
        "which day does the ortho department have doctors",
        "which floor is the cardiology department on",
        "what is the phone number of the cardiology department",
        # far past the reach
        "which day is the doctor available in the cardiology department",
        # unchanged neighbours from tests/test_doctor_list.py
        "is Dr Sen available tomorrow",
        "which test is cheaper",
        "I want to speak to a doctor",
    ],
)
def test_the_wider_reach_does_not_turn_other_questions_into_list_requests(text):
    assert asks_for_the_doctor_list(text) is False


# ================================================ 2. "do you have a doctor in <department>?"
@pytest.mark.parametrize(
    "text",
    [
        # the live Bengali sentence, and a clean version of it
        "আমায় ডক্টর ডায়াবেটোলজিতে দেখাতে বলেছে তো আপনাদের ওখানে ডাক্তার আছে ডায়াবেটোলজি",
        "আপনাদের ওখানে ডায়াবেটোলজির ডাক্তার আছে?",
        "do you have a doctor in diabetology",
        "is there a doctor for diabetes",
        "आपके यहाँ डायबिटीज़ के डॉक्टर हैं क्या",
    ],
)
def test_a_plain_is_there_a_doctor_question_is_recognised(text):
    assert asks_if_a_doctor_is_there(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        # a booking noun: this is a read-back (agent/lookup_request.py), never a roster question
        "আমার ডায়াবেটোলজি ডাক্তারের অ্যাপয়েন্টমেন্ট আছে?",
        "do I have an appointment with the diabetology doctor",
        "when is my booking with the doctor",
        # a want, or another action
        "I want to see a doctor in diabetology, do you have one",
        "ডায়াবেটোলজিতে ডাক্তার দেখাব, আছে?",
        "ডায়াবেটোলজির ডাক্তার আছে? আমি বাড়ি থেকে নমুনা দিতে চাই",
        # the earliest-appointment story has its own path
        "when is the earliest doctor available in diabetology",
        # no doctor word, or no existence word
        "what is the price of a CBC test",
        "ডায়াবেটোলজি বিভাগ কোথায়",
        "I need a doctor",
    ],
)
def test_anything_about_a_booking_a_want_or_no_question_is_left_to_its_own_flow(text):
    assert asks_if_a_doctor_is_there(text) is False


def test_the_new_cue_never_fires_where_the_booking_lookup_already_does():
    """The booking read-back is decided first in main.py's dispatch; the two cues must not overlap."""
    for text in ("আমার অ্যাপয়েন্টমেন্ট আছে?", "do I have a booking with the doctor", "आपके यहाँ मेरी बुकिंग है"):
        assert asks_what_is_booked(text), text
        assert not asks_if_a_doctor_is_there(text), text


@pytest.mark.parametrize(
    "text,expected",
    [
        ("my appointment is on friday", True),
        ("আমার বুকিং আছে", True),
        ("do you have a doctor in diabetology", False),
        ("আপনাদের ওখানে ডাক্তার আছে", False),
    ],
)
def test_mentions_a_booking_is_only_the_noun_rule(text, expected):
    assert mentions_a_booking(text) is expected
