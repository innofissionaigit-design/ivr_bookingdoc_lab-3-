"""Whole conversations, in Bengali, Hindi and English, through the REAL orchestrator and the
REAL clinic API (tests/_conversation.py), with every line the agent speaks checked against the
REAL speech gate (agent/tts.py unspeakable_spans) on the shipped pronunciation setting.

Why this file exists -- the live call of 2026-10-06:

    caller: আমি একজন ডক্টর দেখাব                 ("I'll see a doctor")
    agent : অ্যাপয়েন্টমেন্টের জন্য কোন দিন আর কোন সময় বলবেন?
    agent : আর কিছু জানতে চান? না চাইলে আমি কল শেষ করে দেব।     (five seconds later)
    caller: পরশু দিন অ্যাপয়েন্টমেন্ট করুন সকাল সাড়ে দশটা
    agent : দুঃখিত, 'একজন ডক্টর' নামে কোনো ডাক্তার খুঁজে পেলাম না।

Every unit test passed on that tree. What failed was the conversation: the order the questions
came in, what a "name" was allowed to be, what silence meant, and lines that were composed
correctly and then blocked by the speech gate so the caller never heard them. Those are only
visible by playing the call through, so that is what these tests do.

The flow they pin is the sibling tree's (_open_booking: the doctor is settled with the clinic
before anything after it is asked), built on this tree's data. The intent model's output is
scripted per turn -- including its mistakes, such as "a doctor" in the doctor slot -- because the
flow has to survive what the model actually produces.
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _conversation import conversation  # noqa: E402

from agent.phrases import phrase  # noqa: E402
from agent.reply_templates import doctor_choice_prompt  # noqa: E402

LANGS = ("bn", "hi", "en")
B = "book_appointment"

ANY_DOCTOR = {
    "bn": ("আমি একজন ডক্টর দেখাব", "একজন ডক্টর"),
    "hi": ("मुझे एक डॉक्टर दिखाना है", "एक डॉक्टर"),
    "en": ("I want to see a doctor", "a doctor"),
}
HEART_DOCTOR = {"bn": "হার্টের ডাক্তার", "hi": "दिल का डॉक्टर", "en": "heart doctor"}
CARDIOLOGY_SPOKEN = {"bn": "কার্ডিওলজি", "hi": "कार्डियोलॉजी", "en": "Cardiology"}
THE_FIRST = {"bn": "প্রথমজন", "hi": "पहला", "en": "the first one"}
YES_AT_TEN = {"bn": "হ্যাঁ, সকাল দশটায়", "hi": "हाँ, सुबह दस बजे", "en": "yes, ten in the morning"}
DETAILS = {
    "bn": ("রোগীর নাম রাহুল দাস, নম্বর ৯৮৩০০১২৩৪৫", "রাহুল দাস"),
    "hi": ("मरीज़ का नाम राहुल दास, नंबर 9830012345", "राहुल दास"),
    "en": ("patient name Rahul Das, number 9830012345", "Rahul Das"),
}
YES = {"bn": "হ্যাঁ", "hi": "हाँ", "en": "yes"}

# The opening words of the not-found sentence, per language -- for a "name" that names nobody it
# must never be said: there is no name to not find.
NOT_FOUND_MARK = {"bn": "নামে কোনো ডাক্তার", "hi": "नाम का कोई डॉक्टर", "en": "no doctor called"}


@pytest.fixture(autouse=True)
def _shipped_pronunciation_setting(monkeypatch):
    """The speech gate as shipped (deploy/env.sh): the unreviewed lexicon OFF."""
    monkeypatch.setenv("PRONUNCIATION_ALLOW_DRAFT", "0")


def _spoken(call) -> list[str]:
    return [line for who, line in call._log if who.startswith("AI")]


def _ask_for_any_doctor(call, lang):
    text, slot = ANY_DOCTOR[lang]
    return call.say(text, B, {"doctor_name": slot})


# ======================================================== the doctor is settled before anything else


@pytest.mark.parametrize("lang", LANGS)
def test_a_request_for_any_doctor_is_asked_which_doctor_with_the_departments(lang):
    with conversation(lang) as call:
        said = _ask_for_any_doctor(call, lang)
        # The question opens the reply, and it offers the clinic's departments by their SPOKEN names.
        assert said[-1].startswith(doctor_choice_prompt([], lang))
        assert CARDIOLOGY_SPOKEN[lang] in said[-1]
        # "a doctor" was never stored as a name, and no not-found sentence was spoken about it.
        assert not call.session.booking.slots.get("doctor_name")
        assert not any(NOT_FOUND_MARK[lang] in line for line in _spoken(call))
        assert call.blocked == []


@pytest.mark.parametrize("lang", LANGS)
def test_a_kind_of_doctor_lists_that_departments_doctors_and_the_first_is_taken_and_offered(lang):
    with conversation(lang) as call:
        _ask_for_any_doctor(call, lang)
        said = call.say(HEART_DOCTOR[lang], B, {"doctor_name": HEART_DOCTOR[lang]})
        assert CARDIOLOGY_SPOKEN[lang] in said[-1]
        offered = list(call.session.booking.doctor_choices)
        assert len(offered) >= 2
        # "the first one" picks the first doctor read out, and the reply NAMES that doctor and
        # offers their next sitting -- the caller is never asked for a day without being told who.
        said = call.say(THE_FIRST[lang], "unclear", {})
        st = call.session.booking
        assert st.doctor_verified and st.slots["doctor_name"] == offered[0][0]
        surname = offered[0][0].split()[-1]
        spoken_surname = {"bn": st.doctor_spoken.get("doctor_name_bn"), "hi": st.doctor_spoken.get("doctor_name_hi")}.get(
            lang, surname
        )
        assert spoken_surname and spoken_surname in said[-1]
        assert call.blocked == []


@pytest.mark.parametrize("lang", LANGS)
def test_the_whole_booking_reaches_a_spoken_confirmation_number(lang):
    with conversation(lang) as call:
        _ask_for_any_doctor(call, lang)
        call.say(HEART_DOCTOR[lang], B, {"doctor_name": HEART_DOCTOR[lang]})
        call.say(THE_FIRST[lang], "unclear", {})
        call.say(YES_AT_TEN[lang], B, {"time_slot": "10:00"})
        text, patient = DETAILS[lang]
        readback = call.say(text, B, {"patient_name": patient, "phone": "9830012345"})
        assert readback and readback[-1].endswith("?")
        if lang != "en":
            # Read back in the caller's script -- the Latin catalogue name is unspeakable there.
            assert "Dr." not in readback[-1]
        final = call.say(YES[lang], "unclear", {})
        assert any("KCD-" in line for line in final), final
        # The whole call, including the confirmation number, passed the shipped speech gate.
        assert call.blocked == [], call.blocked


@pytest.mark.parametrize("lang", LANGS)
def test_a_doctor_picked_after_the_day_was_given_is_named_before_anything_else_is_asked(lang):
    """The caller's own order on the live call: the day and time first, the doctor last. "প্রথমজন" is a
    position in a list; with the day already known nothing else names the doctor before the read-back."""
    when = {"bn": "কাল সকাল দশটায়", "hi": "कल सुबह दस बजे", "en": "tomorrow at ten"}[lang]
    with conversation(lang) as call:
        _ask_for_any_doctor(call, lang)
        call.say(when, B, {"date": "2026-10-08", "time_slot": "10:00"})
        call.say(HEART_DOCTOR[lang], B, {"doctor_name": HEART_DOCTOR[lang]})
        said = call.say(THE_FIRST[lang], "unclear", {})
        st = call.session.booking
        assert st.doctor_verified
        name = {"bn": st.doctor_spoken.get("doctor_name_bn"), "hi": st.doctor_spoken.get("doctor_name_hi")}.get(
            lang, st.slots["doctor_name"].split()[-1]
        )
        # Named in this very reply: by the acknowledgement when the doctor sits that day, or by the
        # "doesn't sit that day, next sitting is ..." offer when they do not. Never left as "the first
        # one" until the final read-back.
        assert any(name in line for line in said), said
        assert call.blocked == []


@pytest.mark.parametrize("lang", LANGS)
def test_a_day_the_doctor_does_not_sit_is_answered_with_their_next_sitting(lang):
    """Not "the doctor doesn't sit that day, say another day?", which names nobody and leaves the caller
    guessing days: the doctor, the fact, and the day they DO sit."""
    from agent.reply_templates import doctor_day_unavailable_prompt

    with conversation(lang) as call:
        doctor = {"bn": "ভট্টাচার্য", "hi": "भट्टाचार्य", "en": "Bhattacharya"}[lang]
        call.say(doctor, B, {"doctor_name": doctor, "date": "2026-10-08", "time_slot": "10:00"})
        st = call.session.booking
        result = call._run(call.m._tools.get_doctor_availability(st.slots["doctor_name"], None))
        if result.get("next_available_date") == "2026-10-08" or result.get("date") == "2026-10-08":
            pytest.skip("the seeded schedule has this doctor sitting that day; nothing to refuse")
        assert _spoken(call)[-1] == doctor_day_unavailable_prompt(result, lang)
        assert not st.slots.get("date") and st.offered_slots
        assert call.blocked == []


def test_the_departments_are_read_out_on_the_first_ask_only():
    with conversation("bn") as call:
        first = _ask_for_any_doctor(call, "bn")[-1]
        assert CARDIOLOGY_SPOKEN["bn"] in first
        again = call.say("ডাক্তার দেখাবো", B, {"doctor_name": "ডাক্তার"})[-1]
        assert again == doctor_choice_prompt([], "bn")


def test_the_live_call_of_2026_10_06_replayed():
    with conversation("bn") as call:
        first = call.say("আমি একজন ডক্টর দেখাব", B, {"doctor_name": "একজন ডক্টর"})
        call.say(
            "পরশু দিন অ্যাপয়েন্টমেন্ট করুন সকাল সাড়ে দশটা",
            B,
            {"doctor_name": "একজন ডক্টর", "date": "2026-10-09", "time_slot": "10:30"},
        )
        call.say("ডাক্তার দেখাবো", B, {"doctor_name": "ডাক্তার"})
        spoken = _spoken(call)
        assert first[-1].startswith(doctor_choice_prompt([], "bn"))
        # What the caller heard on the live call, none of which may come back:
        assert not any("নামে কোনো ডাক্তার খুঁজে" in line for line in spoken)  # "no doctor named 'a doctor'"
        assert not any("বদলে ডাক্তার" in line for line in spoken)  # "changed the doctor to 'doctor'"
        assert not call.session.booking.slots.get("doctor_name")
        # ...and the day and time the caller DID give were kept while the doctor is still being asked.
        assert call.session.booking.slots.get("time_slot") == "10:30"
        assert call.blocked == []


# ============================================================ names: one doctor, never a near-miss


def test_a_surname_resolves_to_that_doctor_and_is_offered_at_once():
    with conversation("bn") as call:
        said = call.say("ডাক্তার সেন দেখাব", B, {"doctor_name": "সেন"})
        st = call.session.booking
        assert st.doctor_verified and st.slots["doctor_name"] == "Dr. A. Sen"
        assert "সেন" in said[-1]
        assert call.blocked == []


def test_saying_the_chosen_doctor_again_is_not_announced_as_a_change():
    with conversation("bn") as call:
        call.say("ডাক্তার সেন দেখাব", B, {"doctor_name": "সেন"})
        said = call.say("সেন", "unclear", {"doctor_name": "সেন"})
        assert not any("বদলে" in line for line in said)  # "I've changed the doctor to ..."
        st = call.session.booking
        assert st.doctor_verified and st.slots["doctor_name"] == "Dr. A. Sen"


def test_a_name_nobody_has_is_offered_as_a_suggestion_then_handed_to_a_person():
    with conversation("bn") as call:
        first = call.say("ডাক্তার পালিত দেখাব", B, {"doctor_name": "পালিত"})
        # Suggested, never resolved: Dr. Pal's schedule must not be read for "Palit".
        assert "পাল" in first[-1] and first[-1].endswith("?")
        assert not call.session.booking.slots.get("doctor_name")
        for _ in range(3):
            call.say("পালিত", "unclear", {"doctor_name": "পালিত"})
        assert call.session.outcome == "handed_off"
        assert _spoken(call)[-1] == phrase("handoff", "bn")


# ================================================== an open booking owns the caller's next answer


@pytest.mark.parametrize("lang,text", [("bn", "কালকের জন্য করে দিন"), ("hi", "कल के लिए कर दीजिए"), ("en", "do it for tomorrow")])
def test_an_answer_the_model_filed_as_a_lab_test_stays_in_the_doctor_booking(lang, text):
    """Live call 2026-10-06: "do it for tomorrow", mid-booking, came back `book_test`, and the caller was
    told lab tests are booked at the counter -- the doctor booking silently abandoned."""
    doctor = {"bn": "অরিন্দম সেন", "hi": "अरिंदम सेन", "en": "Arindam Sen"}[lang]
    with conversation(lang) as call:
        call.say(doctor, B, {"doctor_name": doctor})
        said = call.say(text, "book_test", {"date": "2026-10-08"})
        assert phrase("lab_test_counter_only", lang) not in said
        st = call.session.booking
        assert st is not None and st.action == B and st.slots.get("date") == "2026-10-08"
        assert st.slots["doctor_name"] == "Dr. A. Sen"


def test_a_real_side_question_mid_booking_is_still_answered_and_the_booking_kept():
    with conversation("bn") as call:
        call.say("অরিন্দম সেন", B, {"doctor_name": "অরিন্দম সেন"})
        said = call.say("সিবিসি টেস্টের দাম কত", "test_rate", {"test_name": "CBC"})
        assert any("সিবিসি" in line or "CBC" in line for line in said), said
        assert call.session.booking is not None and call.session.booking.slots["doctor_name"] == "Dr. A. Sen"


def test_naming_a_doctor_mid_booking_answers_which_doctor_even_if_filed_as_an_enquiry():
    with conversation("bn") as call:
        _ask_for_any_doctor(call, "bn")
        call.say("অরিন্দম সেন", "doctor_availability", {"doctor_name": "অরিন্দম সেন"})
        st = call.session.booking
        assert st.doctor_verified and st.slots["doctor_name"] == "Dr. A. Sen"


# ======================================================== a booking turn heard at low confidence
#
# The live booking of 2026-10-06 scored decoder agreement 0.33 and was withheld: the caller was
# asked to repeat everything. On phone audio that happens turn after turn. The sibling tree's three
# zones apply instead: below 0.30 ask again; between that and the floor read back the one part a
# misheard word could turn into a wrong fact (the doctor), else carry on -- nothing is booked until
# the caller says yes to the full read-back, which states the date and time in words.

LOW = {"agreement": 0.33, "decoder": "rnnt"}


def test_a_doctor_heard_at_low_confidence_is_read_back_and_taken_on_yes():
    from agent.entity_confirmation import confirm_question

    with conversation("bn") as call:
        said = call.say("অরিন্দম সেনের অ্যাপয়েন্টমেন্ট চাই", B, {"doctor_name": "অরিন্দম সেন"}, **LOW)
        assert said == [confirm_question("doctor_name", "অরিন্দম সেন", "bn")]
        assert call.session.booking is None or not call.session.booking.slots.get("doctor_name")
        said = call.say("হ্যাঁ", "unclear", {})
        st = call.session.booking
        assert st.doctor_verified and st.slots["doctor_name"] == "Dr. A. Sen"
        assert "সেন" in said[-1]  # the doctor's offer, after the yes -- not before it
        assert call.blocked == []


def test_a_day_and_time_heard_at_low_confidence_are_kept_not_thrown_away():
    from agent.reply_templates import insufficient_information_reply

    with conversation("bn") as call:
        call.say("অরিন্দম সেনের অ্যাপয়েন্টমেন্ট চাই", B, {"doctor_name": "অরিন্দম সেন"})
        said = call.say("কাল সকাল দশটায়", B, {"date": "2026-10-08", "time_slot": "10:00"}, **LOW)
        assert insufficient_information_reply("bn") not in said
        st = call.session.booking
        assert st.slots.get("date") == "2026-10-08" and st.slots.get("time_slot") == "10:00"


def test_a_turn_the_decoders_disagreed_on_entirely_is_asked_again():
    """Below BOOKING_REJECT_FLOOR nothing heard is used -- not even read back. (Which re-ask sentence the
    caller hears is the re-ask policy's choice, agent/reask_policy.py; what matters here is that nothing
    was acted on.)"""
    with conversation("bn") as call:
        said = call.say(
            "অরিন্দম সেনের অ্যাপয়েন্টমেন্ট চাই", B, {"doctor_name": "অরিন্দম সেন"}, agreement=0.20, decoder="rnnt"
        )
        assert len(said) == 1 and said[0].endswith("?")
        assert call.session.pending_entity is None
        st = call.session.booking
        assert st is None or not (st.doctor_verified or st.slots.get("doctor_name"))
        assert "সেন" not in said[0]  # no offer, no read-back of the misheard name


# =================================================================== silence on an open question


@pytest.mark.parametrize("lang", LANGS)
def test_silence_on_an_open_question_is_reasked_twice_then_closed_honestly(lang):
    with conversation(lang) as call:
        question = _ask_for_any_doctor(call, lang)[-1]
        first = call.silence(11)
        assert first == [f"{phrase('silence_still_there', lang)} {question}"]
        second = call.silence(11)
        assert second == [f"{phrase('silence_take_your_time', lang)} {question}"]
        assert call.session.outcome != "silence_timeout"
        last = call.silence(11)
        assert last[0] == phrase("silence_close_unbooked", lang)
        assert call.session.outcome == "silence_timeout"
        # Never the closing offer while a question was open -- the line that offered to hang up
        # on the live caller five seconds into "which day and time?".
        assert not any(phrase("silence_prompt", lang) in line for line in _spoken(call))
        assert call.blocked == []


def test_a_short_pause_on_an_open_question_says_nothing():
    """Thinking time. Nothing is said before SILENCE_REASK_S."""
    with conversation("bn") as call:
        _ask_for_any_doctor(call, "bn")
        assert call.silence(6) == []
