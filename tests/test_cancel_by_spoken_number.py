"""The live call of 2026-10-07 (22:30): a caller read the number they booked from four times and never had their
booking cancelled. Replayed in Bengali, Hindi and English with what the recogniser and the model actually produced.

  * The recogniser's spellings of English digit names -- "ওন", "সেবেন", "giro", "won" -- were not digits, so the number
    came out short or not at all, and the caller was asked the opening question again each time.
  * The model filed a bare, correctly heard "জিরো ওয়ান টু ..." as `unrelated_caller` (with phone=0123456789!); with a
    task open that becomes smalltalk, and the caller was greeted afresh: "নমস্কার, কী সাহায্য করতে পারি?"
  * "giro won two three ..." switched the whole call to English (disclosure and all), and the 8 digits heard were
    read back as if accepted: "Alright, 23456789."
"""

from __future__ import annotations

import datetime
import os
import sys
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _conversation import conversation  # noqa: E402

from agent.lang_select import pick_candidate  # noqa: E402
from agent.fast_path import Catalogue, FastPath  # noqa: E402
from agent.reply_templates import booking_confirmation_readback, no_booking_on_number, phone_continue_prompt  # noqa: E402
from agent.spoken_digits import only_a_number, spoken_phone  # noqa: E402

LANGS = ("bn", "hi", "en")
PHONE = "0123456789"
CANCEL = {
    "bn": "আমি একটা বুকিং করেছিলাম সেটা ক্যান্সেল করতে চাই",
    "hi": "मैंने एक बुकिंग की थी उसे कैंसल करना है",
    "en": "I made a booking and I want to cancel it",
}
MOVE = {"bn": "আমার অ্যাপয়েন্টমেন্টটা অন্য দিনে সরাতে চাই", "hi": "मेरी अपॉइंटमेंट दूसरे दिन करनी है", "en": "I want to move my appointment"}
# The first eight digits, in the recogniser's own spellings -- the caller paused (or the end was lost).
GARBLED = {
    "bn": "জিরো ওন টু থ্রি ফোর ফাইভ সিক্স সেবেন",
    "hi": "ज़ीरो वन टू थ्री फोर फाइव सिक्स सेवेन",
    "en": "giro won two three four five six seven",
}
# ...and the rest, on its own: "eight" comes out "নাইট"/"नाइट" after "seven" (live 2026-10-07, three times out of three).
REST = {"bn": "নাইট নাইন", "hi": "नाइट नाइन", "en": "eight nine"}
# The whole number, with "phone number" in front -- the clinic FAQ's own keywords for its helpdesk number.
SAID_AS_PHONE_NUMBER = {
    "bn": "ফোন নম্বর জিরো ওয়ান টু থ্রি ফোর ফাইভ সিক্স সেভেন এইট নাইন",
    "hi": "फ़ोन नंबर ज़ीरो वन टू थ्री फोर फाइव सिक्स सेवेन एट नाइन",
    "en": "phone number zero one two three four five six seven eight nine",
}
WHOLE = {
    "bn": "জিরো ওয়ান টু থ্রি ফোর ফাইভ সিক্স সেবেন এইট নাইন",
    "hi": "ज़ीरो वन टू थ्री फोर फाइव सिक्स सेवेन एट नाइन",
    "en": "giro won two three four five six seven eight nine",
}
YES = {"bn": "হ্যাঁ", "hi": "हाँ", "en": "yes"}
GREETING = {"bn": "নমস্কার, কী সাহায্য করতে পারি?", "hi": "नमस्ते, क्या मदद करूँ?", "en": "Hello, how can I help?"}


def _book_bose(call, phone: str = PHONE) -> dict:
    tools = call.m._tools
    for ahead in range(2, 9):
        day = (datetime.date.today() + datetime.timedelta(days=ahead)).isoformat()
        if call._run(tools.get_doctor_availability("Dr. T. Bose", day)).get("available"):
            body = call._run(
                tools._client.post(
                    "/api/v1/appointments",
                    json={"doctor_name": "Dr. T. Bose", "date": day, "time_slot": "10:30", "patient_name": "Sourav", "phone": phone},
                )
            ).json()
            assert body.get("success"), body
            return body
    raise AssertionError("Dr. T. Bose has no sitting in the coming week")


def _still_booked(call, booking) -> bool:
    return bool(call._run(call.m._tools.lookup_bookings(confirmation_id=booking["confirmation_id"])).get("bookings"))


@pytest.mark.parametrize("lang", LANGS)
def test_the_recognisers_spellings_of_digit_names_are_digits(lang):
    assert spoken_phone(WHOLE[lang]) == PHONE
    assert spoken_phone(GARBLED[lang]) == "01234567"
    assert spoken_phone(GARBLED[lang] + " " + REST[lang]) == PHONE
    assert spoken_phone("Saturday night") is None


@pytest.mark.parametrize("lang", LANGS)
def test_the_live_call_finds_and_cancels_the_booking(lang):
    with conversation(lang) as call:
        booking = _book_bose(call)
        call.say(CANCEL[lang], "cancel_appointment", {})
        # Half heard: what is held so far is read back, and the rest asked for -- never "give me your number" again.
        said = call.say(GARBLED[lang], "unclear", {})
        assert said[-1] == phone_continue_prompt("01234567", lang), said
        # Heard whole, and filed by the model as a wrong-number caller: it is still the cancellation's answer.
        said = call.say(WHOLE[lang], "unrelated_caller", {"phone": PHONE})
        st = call.session.booking
        assert st is not None and st.action == "cancel_appointment" and st.stage == "confirming", said
        assert not any(GREETING[lang] in line for line in said)
        call.say(YES[lang], "unclear", {})
        if call.session.booking is not None and call.session.booking.stage == "awaiting_charge_confirm":
            call.say(YES[lang], "unclear", {})
        assert not _still_booked(call, booking)
        assert call.blocked == []


@pytest.mark.parametrize("lang", LANGS)
def test_a_number_with_no_booking_on_it_is_said_so_with_the_number_read_back(lang):
    with conversation(lang) as call:
        call.say(CANCEL[lang], "cancel_appointment", {})
        said = call.say(WHOLE[lang], "smalltalk", {})
        assert said[-1] == no_booking_on_number(PHONE, lang), said
        assert call.session.booking is not None and call.session.booking.action == "cancel_appointment"
        assert call.blocked == []


@pytest.mark.parametrize("lang", LANGS)
def test_a_move_finds_its_booking_from_a_number_given_in_two_goes(lang):
    with conversation(lang) as call:
        _book_bose(call)
        call.say(MOVE[lang], "reschedule_appointment", {})
        said = call.say(GARBLED[lang], "unclear", {})
        assert said[-1] == phone_continue_prompt("01234567", lang), said
        call.say(WHOLE[lang], "smalltalk", {})
        st = call.session.booking
        assert st.action == "reschedule_appointment" and st.slots.get("confirmation_id"), st.slots
        assert call.blocked == []


def _heard(text: str, agreement: float):
    return types.SimpleNamespace(text=text, decoder_agreement=agreement)


def test_a_number_read_in_english_never_moves_a_bengali_or_hindi_call_to_english():
    english = ("en", _heard(WHOLE["en"], 0.82))
    assert only_a_number(WHOLE["en"]) and only_a_number(WHOLE["bn"]) and not only_a_number(CANCEL["en"])
    for lang in ("bn", "hi"):
        own = (lang, _heard(WHOLE[lang], 1.0))
        chosen, _ = pick_candidate([own, english], {lang: 0.71, "en": 0.28}, prior=lang)
        assert chosen == lang
    # A caller already speaking English keeps English.
    chosen, _ = pick_candidate([("bn", _heard(WHOLE["bn"], 1.0)), english], {"bn": 0.3, "en": 0.7}, prior="en")
    assert chosen == "en"


@pytest.mark.parametrize("lang", LANGS)
def test_the_rest_of_a_number_completes_it_and_the_number_is_read_back_before_cancelling(lang):
    """Live: "নাইট নাইন" after "so far I have 0 1 2 3 4 5 6 7" was two digits -- too few to be a number on their own --
    so it went to the model, and the caller was greeted afresh."""
    with conversation(lang) as call:
        booking = _book_bose(call)
        call.say(CANCEL[lang], "cancel_appointment", {})
        call.say(GARBLED[lang], "unclear", {})
        said = call.say(REST[lang], "smalltalk", {})
        st = call.session.booking
        assert st.stage == "confirming" and st.slots.get("phone") == PHONE, said
        assert said[-1] == booking_confirmation_readback(st.readback_slots(), "cancel_appointment", lang)
        assert " ".join(PHONE) in said[-1], "the number it was found by is read back"
        call.say(YES[lang], "unclear", {})
        if call.session.booking is not None and call.session.booking.stage == "awaiting_charge_confirm":
            call.say(YES[lang], "unclear", {})
        assert not _still_booked(call, booking)


@pytest.mark.parametrize("lang", LANGS)
def test_phone_number_followed_by_the_number_is_the_callers_number_not_the_clinics_faq(lang):
    """Live: "ফোন নম্বর জিরো ওয়ান ..." was answered with the clinic's helpdesk number -- the keyword path matched
    "ফোন নম্বর" before the cancellation ever saw the digits."""
    with conversation(lang) as call:
        saved = call.m._fast_path
        try:
            payload = call._run(call.m._tools._client.get("/api/v1/catalogue")).json()
            call.m._fast_path = FastPath(Catalogue(payload))
            _book_bose(call)
            call.say(CANCEL[lang], "cancel_appointment", {})
            said = call.say(SAID_AS_PHONE_NUMBER[lang], "clinic_faq", {"faq_topic": "contact_number"})
            st = call.session.booking
            assert st is not None and st.action == "cancel_appointment" and st.stage == "confirming", said
            assert st.slots.get("phone") == PHONE
            assert call.blocked == []
        finally:
            call.m._fast_path = saved


def test_a_number_given_where_one_is_asked_never_reaches_the_keyword_path():
    with conversation("bn") as call:
        call.say(CANCEL["bn"], "cancel_appointment", {})
        assert call.m._number_expected(call.session, SAID_AS_PHONE_NUMBER["bn"])
        assert not call.m._number_expected(call.session, "ক্লিনিকের ফোন নম্বর কত?")


# The whole number, read back broken: "six" heard as the word for HUNDRED (live on the pod, 2026-10-07: "পাঁচ শো সাত").
BROKEN = {
    "bn": ("ফোন নম্বর শূন্য এক দুই তিন চার পাঁচ শো সাত আট নয়", "ছয় সাত আট নয়"),
    "hi": ("फ़ोन नंबर शून्य एक दो तीन चार पांच सौ सात आठ नौ", "छह सात आठ नौ"),
    "en": ("phone number zero one two three four five hundred seven eight nine", "six seven eight nine"),
}


@pytest.mark.parametrize("lang", LANGS)
def test_a_broken_reading_of_the_number_is_held_and_completed_never_answered_as_the_faq(lang):
    broken, rest = BROKEN[lang]
    with conversation(lang) as call:
        saved = call.m._fast_path
        try:
            payload = call._run(call.m._tools._client.get("/api/v1/catalogue")).json()
            call.m._fast_path = FastPath(Catalogue(payload))
            _book_bose(call)
            call.say(CANCEL[lang], "cancel_appointment", {})
            said = call.say(broken, "clinic_faq", {"faq_topic": "contact_number"})
            assert said[-1] == phone_continue_prompt("012345", lang), said
            call.say(rest, "smalltalk", {})
            st = call.session.booking
            assert st.stage == "confirming" and st.slots.get("phone") == PHONE, st.slots
            assert call.blocked == []
        finally:
            call.m._fast_path = saved


@pytest.mark.parametrize(
    ("lang", "text"),
    [
        ("bn", "কনফার্মেশন নম্বর কে সি ডি টু জিরো টু সিক্স ওয়ান জিরো ওয়ান জিরো"),
        ("hi", "कन्फ़र्मेशन नंबर के सी डी टू ज़ीरो टू सिक्स वन ज़ीरो वन ज़ीरो"),
        ("en", "confirmation number KCD two zero two six one zero one zero"),
    ],
)
def test_a_confirmation_number_read_out_is_left_to_the_model(lang, text):
    with conversation(lang) as call:
        call.say(CANCEL[lang], "cancel_appointment", {})
        assert not call.m._number_expected(call.session, text)
