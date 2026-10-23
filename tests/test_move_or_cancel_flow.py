"""The live call of 2026-10-07 (22:09), replayed in Bengali, Hindi and English: a caller who could neither move nor
cancel their appointment.

  * "বুকিং সরাতে চাই" -- Bengali "সরানো" is both "move" and "remove"; the model heard "move". The caller then named
    the booking's OWN day ("শনিবার", "দশ তারিখে") three times and was asked "which day shall I move it to?" three
    times: a day equal to the booking's was dropped without a word.
  * "আচ্ছা দিন" ("OK, do it") to "shall I cancel it?" was not a yes; the model called it smalltalk and the caller was
    asked how they were.
  * "অ্যাপয়েন্টমেন্টটা বাতিল করে দিন" while that same cancellation awaited its yes: nothing was said at all.

The model's own labels for each turn are the ones it returned on the pod (qwen2.5:7b), replayed here.
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

from agent.booking_flow import classify_yes_no  # noqa: E402
from agent.reply_templates import booking_confirmation_readback, move_same_day_prompt  # noqa: E402
from agent.reply_templates_i18n import WEEKDAY_WORDS  # noqa: E402

LANGS = ("bn", "hi", "en")
PHONE = "9830077777"
PATIENT = {"bn": "সৌরভ", "hi": "सौरभ", "en": "Sourav"}
MOVE = {"bn": "আমি একটি বুকিং সরাতে চাই", "hi": "मुझे अपनी बुकिंग आगे-पीछे करनी है", "en": "I want to move my booking"}
CANCEL = {"bn": "অ্যাপয়েন্টমেন্টটা বাতিল করতে চাই", "hi": "अपॉइंटमेंट कैंसल करनी है", "en": "I want to cancel it"}
GO_AHEAD = {"bn": "আচ্ছা দিন", "hi": "ठीक है कर दीजिए", "en": "okay go ahead"}
HOW_ARE_YOU = {"bn": "আপনি কেমন আছেন?", "hi": "आप कैसे हैं?", "en": "How are you?"}


def _the_bookings_day(call, lang, wd: str) -> str:
    return {"bn": f"{wd} সরাতে চাই", "hi": f"{wd} वाली", "en": f"the {wd} one"}[lang]


def _cancel_naming(lang, wd: str) -> str:
    return {
        "bn": f"{wd}-এর অ্যাপয়েন্টমেন্টটা বাতিল করে দিন",
        "hi": f"{wd} वाली अपॉइंटमेंट कैंसल कर दीजिए",
        "en": f"cancel the {wd} appointment",
    }[lang]


def _book_bose(call, lang) -> tuple[dict, datetime.date]:
    """A confirmed 10:30 with Dr. T. Bose on his next sitting at least two days out (inside a week, so a weekday
    name points at it), made through the clinic API inside this call's own database."""
    tools = call.m._tools
    for ahead in range(2, 9):
        day = datetime.date.today() + datetime.timedelta(days=ahead)
        if call._run(tools.get_doctor_availability("Dr. T. Bose", day.isoformat())).get("available"):
            body = call._run(
                tools._client.post(
                    "/api/v1/appointments",
                    json={"doctor_name": "Dr. T. Bose", "date": day.isoformat(), "time_slot": "10:30",
                          "patient_name": PATIENT[lang], "phone": PHONE},
                )
            ).json()
            assert body.get("success"), body
            return body, day
    raise AssertionError("Dr. T. Bose has no sitting in the coming week")


def _still_booked(call, booking) -> bool:
    return bool(call._run(call.m._tools.lookup_bookings(confirmation_id=booking["confirmation_id"])).get("bookings"))


def _found(call, lang):
    """Started as a MOVE, the booking found by the caller's number."""
    call.say(MOVE[lang], "reschedule_appointment", {})
    call.say(PHONE, "smalltalk", {})
    st = call.session.booking
    assert st.action == "reschedule_appointment" and st.slots.get("confirmation_id"), st.slots
    return st


@pytest.mark.parametrize("lang", LANGS)
def test_the_live_call_moves_nothing_asks_move_or_cancel_and_cancels_on_ok_do_it(lang):
    with conversation(lang) as call:
        booking, day = _book_bose(call, lang)
        _found(call, lang)
        wd = WEEKDAY_WORDS[lang][day.weekday()]
        wrong = (day + datetime.timedelta(days=3)).isoformat()  # the model's own (wrong) date for the weekday
        said = call.say(_the_bookings_day(call, lang, wd), "book_test", {"date": wrong})
        fetched = call.session.fetched_booking
        assert said[-1] == move_same_day_prompt(fetched, lang), said
        assert "10:30" in said[-1]

        call.say(CANCEL[lang], "cancel_appointment", {})
        st = call.session.booking
        assert st.action == "cancel_appointment" and st.stage == "confirming"
        call.say(GO_AHEAD[lang], "smalltalk", {}, direct_reply_bn=HOW_ARE_YOU[lang])
        if call.session.booking is not None and call.session.booking.stage == "awaiting_charge_confirm":
            call.say(GO_AHEAD[lang], "smalltalk", {}, direct_reply_bn=HOW_ARE_YOU[lang])
        assert not _still_booked(call, booking)
        spoken = [line for who, line in call._log if who.startswith("AI")]
        assert not any(HOW_ARE_YOU[lang] in line for line in spoken)
        # Nothing about the cancelled booking is offered back -- not the move the caller first asked for.
        assert call.session.suspended is None and not call.session.awaiting_resume
        assert call.blocked == []


@pytest.mark.parametrize("lang", LANGS)
def test_chit_chat_while_a_cancellation_awaits_its_yes_gets_the_question_again(lang):
    with conversation(lang) as call:
        booking, _ = _book_bose(call, lang)
        _found(call, lang)
        call.say(CANCEL[lang], "cancel_appointment", {})
        st = call.session.booking
        chit_chat = {"bn": "ভালো আছি", "hi": "मैं ठीक हूँ", "en": "I'm fine, thanks"}[lang]
        said = call.say(chit_chat, "smalltalk", {}, direct_reply_bn=HOW_ARE_YOU[lang])
        assert said[-1] == booking_confirmation_readback(st.readback_slots(), "cancel_appointment", lang), said
        assert _still_booked(call, booking)


@pytest.mark.parametrize("lang", LANGS)
def test_cancel_it_said_again_while_the_cancellation_awaits_its_yes_cancels_it(lang):
    with conversation(lang) as call:
        booking, day = _book_bose(call, lang)
        _found(call, lang)
        call.say(CANCEL[lang], "cancel_appointment", {})
        wd = WEEKDAY_WORDS[lang][day.weekday()]
        said = call.say(_cancel_naming(lang, wd), "cancel_appointment", {"date": day.isoformat()})
        assert said, "never silence"
        if call.session.booking is not None and call.session.booking.stage == "awaiting_charge_confirm":
            call.say(GO_AHEAD[lang], "smalltalk", {})
        assert not _still_booked(call, booking)


@pytest.mark.parametrize("lang", LANGS)
def test_cancel_naming_another_day_is_not_taken_as_the_yes(lang):
    with conversation(lang) as call:
        booking, day = _book_bose(call, lang)
        _found(call, lang)
        call.say(CANCEL[lang], "cancel_appointment", {})
        other = WEEKDAY_WORDS[lang][(day + datetime.timedelta(days=1)).weekday()]
        said = call.say(_cancel_naming(lang, other), "cancel_appointment", {})
        assert said, "never silence"
        assert _still_booked(call, booking), "a different day was named: that is not this booking's yes"


@pytest.mark.parametrize("lang", LANGS)
def test_a_cancel_word_is_never_the_yes_to_anything_but_a_cancellation(lang):
    yes_cancel = {"bn": "হ্যাঁ বাতিল করুন", "hi": "हाँ कैंसल कर दीजिए", "en": "yes cancel it"}[lang]
    yes_remove = {"bn": "হ্যাঁ সরিয়ে দিন", "hi": "हाँ हटा दीजिए", "en": "yes remove it"}[lang]
    with conversation(lang) as call:
        m = call.m
        for action in ("book_appointment", "reschedule_appointment"):
            st = m.new_state(action)
            assert m._cancel_request_answer(call.session, st, yes_cancel, classify_yes_no(yes_cancel, lang)) is None
        # "সরিয়ে দিন" is also "move it": a remove word never vetoes the yes to a move.
        move = m.new_state("reschedule_appointment")
        assert m._cancel_request_answer(call.session, move, yes_remove, classify_yes_no(yes_remove, lang)) == "yes"


@pytest.mark.parametrize(
    ("text", "lang", "expected"),
    [
        ("আচ্ছা দিন", "bn", "yes"), ("করে দিন", "bn", "yes"), ("আচ্ছা", "bn", "yes"),
        ("অন্য দিনে করে দিন", "bn", None), ("না দিন", "bn", "no"), ("ভালো আছি", "bn", None),
        ("ठीक है कर दीजिए", "hi", "yes"), ("अच्छा", "hi", "yes"), ("नहीं कर दीजिए", "hi", "no"),
        ("okay go ahead", "en", "yes"), ("do it", "en", "yes"), ("please do", "en", "yes"),
        ("don't do it", "en", None), ("do it on monday", "en", None), ("it", "en", None), ("please", "en", None),
    ],
)
def test_a_go_ahead_answer_is_a_yes_only_when_that_is_all_it_says(text, lang, expected):
    assert classify_yes_no(text, lang) == expected
