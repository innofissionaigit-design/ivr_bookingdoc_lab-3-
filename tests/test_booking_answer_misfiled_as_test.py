"""The live call of 2026-10-07 (21:45), replayed in Bengali, Hindi and English.

Mid-way through a DOCTOR booking the intent model filed two kinds of answer as `book_test`, and both are pinned
here with the slots it actually returned (replayed against the pod's qwen2.5:7b, every language):

  * "ঐদিনেই করুন সকাল সাড়ে দশটায়" ("that day, half past ten") after the doctor's next sitting was offered: dated
    TODAY by the model, so the offered Thursday was never taken and the caller heard "he doesn't sit that day" twice.
  * "রোগীর নাম সৌরভ" / "मरीज़ का नाम सौरभ" / "Patient name is Sourav": test_name "সৌরভ"/"Sourav" -- the PATIENT's
    name -- so the turn was taken for a lab-test request and the caller was sent to the counter, twice.

Each is checked with and without the clinic's catalogue loaded (the fast path is None when clinic-api was down
at boot), and a real test named at the same point is still answered as a test question.
"""

from __future__ import annotations

import datetime
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _conversation import conversation  # noqa: E402

from agent.fast_path import Catalogue, FastPath  # noqa: E402
from agent.phrases import phrase  # noqa: E402

LANGS = ("bn", "hi", "en")
B, T = "book_appointment", "book_test"
SEE_SEN = {"bn": ("ডক্টর সেনকে দেখাব", "সেন"), "hi": ("डॉक्टर सेन को दिखाना है", "सेन"), "en": ("I want to see Dr Sen", "Sen")}
THAT_DAY = {
    "bn": ("ঐদিনেই করুন সকাল সাড়ে দশটায়", "সকালের 10 টায়"),
    "hi": ("उसी दिन कर दीजिए साढ़े दस बजे", "सुबह 10 बजे"),
    "en": ("That day please, at 10:30", "10:30"),
}
# What the model returned for the patient's name, verbatim (test_name is the name; patient_name sometimes empty).
NAME_TURNS = {
    "bn": [("রোগীর নাম সৌরভ", {"test_name": "রোগীর নাম সৌরভ", "patient_name": "সৌরভ"})],
    "hi": [("मरीज़ का नाम सौरभ", {"test_name": "सौरभ"})],
    "en": [("Patient name is Sourav", {"test_name": "Sourav"})],
}
PATIENT = {"bn": "সৌরভ", "hi": "सौरभ", "en": "Sourav"}


def _spoken(call) -> list[str]:
    return [line for who, line in call._log if who.startswith("AI")]


def _with_catalogue(call, on: bool):
    """Load the clinic's real catalogue into the fast path, as a booted pod has it -- or leave it unloaded."""
    if on:
        payload = call._run(call.m._tools._client.get("/api/v1/catalogue")).json()
        call.m._fast_path = FastPath(Catalogue(payload))
    else:
        call.m._fast_path = None


def _offered_then_that_day(call, lang):
    """Doctor named -> next sitting offered -> "that day, half past ten", dated by the model to some OTHER day."""
    text, sen = SEE_SEN[lang]
    call.say(text, B, {"doctor_name": sen})
    st = call.session.booking
    assert st.doctor_verified and st.offered_slots, _spoken(call)
    offered = st.offered_slots[0]["date"]
    wrong = (datetime.date.fromisoformat(offered) - datetime.timedelta(days=1)).isoformat()
    said, model_time = THAT_DAY[lang]
    call.say(said, T, {"date": wrong, "time_slot": model_time})
    return st, offered


@pytest.fixture(params=[False, True], ids=["no-catalogue", "catalogue"])
def catalogue_loaded(request):
    return request.param


@pytest.mark.parametrize("lang", LANGS)
def test_that_day_takes_the_day_just_offered_not_the_models_guess(lang, catalogue_loaded):
    with conversation(lang) as call:
        saved = call.m._fast_path
        try:
            _with_catalogue(call, catalogue_loaded)
            st, offered = _offered_then_that_day(call, lang)
            assert st.slots.get("date") == offered and st.slots.get("time_slot") == "10:30", (st.slots, _spoken(call))
            assert phrase("lab_test_counter_only", lang) not in _spoken(call)
            assert call.blocked == []
        finally:
            call.m._fast_path = saved


@pytest.mark.parametrize("lang", LANGS)
def test_the_patients_name_filed_as_a_test_is_taken_as_the_patients_name(lang, catalogue_loaded):
    with conversation(lang) as call:
        saved = call.m._fast_path
        try:
            _with_catalogue(call, catalogue_loaded)
            st, _ = _offered_then_that_day(call, lang)
            for text, slots in NAME_TURNS[lang]:
                said = call.say(text, T, slots)
                assert phrase("lab_test_counter_only", lang) not in said, said
            assert st is call.session.booking and st.action == "book_appointment"
            assert PATIENT[lang] in (st.slots.get("patient_name") or ""), st.slots
            assert not st.slots.get("test_name")
            assert call.blocked == []
        finally:
            call.m._fast_path = saved


@pytest.mark.parametrize("lang", LANGS)
def test_a_name_and_number_filed_as_a_test_still_reaches_the_booking(lang, catalogue_loaded):
    turn = {
        "bn": ("রোগীর নাম সৌরভ আর ফোন নম্বর নাইন এইট থ্রি জিরো ওয়ান টু থ্রি ফোর ফাইভ সিক্স", {"test_name": "সৌরভ", "patient_name": "সৌরভ", "phone": "9830123456"}),
        "hi": ("मरीज़ का नाम सौरभ और फ़ोन नंबर 9830123456", {"test_name": "सौरभ", "patient_name": "सौरभ", "phone": "9830123456"}),
        "en": ("The patient is Sourav and the phone number is 9830123456", {"test_name": "Sourav", "phone": "9830123456"}),
    }[lang]
    with conversation(lang) as call:
        saved = call.m._fast_path
        try:
            _with_catalogue(call, catalogue_loaded)
            st, _ = _offered_then_that_day(call, lang)
            said = call.say(turn[0], T, turn[1])
            assert phrase("lab_test_counter_only", lang) not in said, said
            assert st.slots.get("phone") == "9830123456" or st.slots.get("contact_phone") == "9830123456", st.slots
            assert call.blocked == []
        finally:
            call.m._fast_path = saved


@pytest.mark.parametrize("lang", LANGS)
def test_a_real_test_named_while_the_patient_is_asked_is_still_a_test_question(lang):
    """The narrowing must not swallow a genuine side question: a test the clinic HAS is answered as a test."""
    with conversation(lang) as call:
        saved = call.m._fast_path
        try:
            _with_catalogue(call, True)
            st, _ = _offered_then_that_day(call, lang)
            payload = call._run(call.m._tools._client.get("/api/v1/catalogue")).json()
            test = payload["tests"][0]["name"]  # a test the clinic lists, by its own name
            # Read back first (the acceptance layer), then the counter rule on a yes.
            assert call.say(test, T, {"test_name": test})[-1].endswith("?")
            said = call.say({"bn": "হ্যাঁ", "hi": "हाँ", "en": "yes"}[lang], "unclear", {})
            assert phrase("lab_test_counter_only", lang) in " ".join(said), said
        finally:
            call.m._fast_path = saved


TAPAN = {
    "bn": [("ডক্টর তপন সেনকে দেখাব", "তপন সেন"), ("ডক্টর তপন কে দেখাব", "তপন"), "হ্যাঁ"],
    "hi": [("डॉक्टर तपन सेन को दिखाना है", "तपन सेन"), ("डॉक्टर तपन को दिखाना है", "तपन"), "हाँ"],
    "en": [("I want to see Dr Tapan Sen", "Tapan Sen"), ("I want to see Dr Tapan", "Tapan"), "yes"],
}


@pytest.mark.parametrize("lang", LANGS)
def test_a_yes_to_the_doctor_read_back_is_never_a_yes_to_an_earlier_offer(lang):
    """The opening of the same call. There is no Dr. Tapan Sen -- the clinic has Dr. Arindam Sen and Dr. Tapan Bose.
    "Tapan Sen" was offered as "Arindam Sen?"; the caller said "Tapan" (heard at agreement 0.40, so read back as
    "Tapan?"), said yes, and was booked with Dr. Sen: the yes went to the stale offer."""
    (first, first_slot), (second, second_slot), yes = TAPAN[lang]
    with conversation(lang) as call:
        call.say(first, B, {"doctor_name": first_slot})
        call.say(second, B, {"doctor_name": second_slot}, agreement=0.40, decoder="rnnt")
        call.say(yes, "unclear", {})
        st = call.session.booking
        assert st.doctor_verified and st.slots.get("doctor_name") == "Dr. T. Bose", (st.slots, _spoken(call))
        assert call.blocked == []
