"""The day the caller chose survives the rest of the booking -- bn, hi and en.

Live call, 2026-10-08. The agent offered Dr Kaushik Bhattacharya's next sitting, the caller took it ("এদিনই করে
দিন"), the agent asked for a time, and the caller said "এগারোটা". The intent model attached TODAY's date to that
answer -- a day nobody had spoken -- it overwrote the day the agent itself had offered, and the caller was told:

    ডাঃ ভট্টাচার্য ওই দিন বসেন না। ওঁর পরের বসার দিন শুক্রবার, 2026-10-09 তারিখ...

about the very day offered two turns earlier. The booking could not be finished.

Two defects, both pinned here: a day or time the caller never said must not replace one they settled, and
"এদিনই" ("this very day") must point at the day just offered the way "ঐদিন" already did.
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

from agent.booking_flow import pick_offered_slot  # noqa: E402

LANGS = ("bn", "hi", "en")
B = "book_appointment"
SEE_DOCTOR = {
    "bn": ("ডক্টর কৌশিকের কাছে দেখাতে চাই", "কৌশিক"),
    "hi": ("डॉक्टर कौशिक को दिखाना है", "कौशिक"),
    "en": ("I want to see Dr Kaushik", "Kaushik"),
}
THIS_DAY = {"bn": "এদিনই করে দিন", "hi": "इसी दिन कर दीजिए", "en": "this day please"}
ELEVEN = {"bn": "এগারোটা", "hi": "ग्यारह बजे", "en": "eleven o'clock"}
NOT_SITTING = {"bn": "ওই দিন বসেন না", "hi": "उस दिन नहीं बैठते", "en": "doesn't sit that day"}


def _spoken(call):
    return [line for who, line in call._log if who.startswith("AI")]


def _offered(call, lang):
    """Doctor named -> the agent offers their next sitting. Returns the day it offered."""
    text, name = SEE_DOCTOR[lang]
    call.say(text, B, {"doctor_name": name})
    st = call.session.booking
    assert st.doctor_verified and st.offered_slots, _spoken(call)
    return st.offered_slots[0]["date"]


# ============================================================================ the live call


@pytest.mark.parametrize("lang", LANGS)
def test_a_time_only_answer_does_not_move_the_day_the_caller_took(lang):
    today = datetime.date.today().isoformat()
    with conversation(lang) as call:
        offered = _offered(call, lang)
        # "this day" -- the model names no day of its own, so the offer has to carry it
        call.say(THIS_DAY[lang], B, {})
        st = call.session.booking
        assert st.slots.get("date") == offered, (st.slots, _spoken(call))

        # ...and now only a time, with TODAY's date volunteered by the model and spoken by nobody
        call.say(ELEVEN[lang], B, {"date": today, "time_slot": "11:00"})
        assert st.slots.get("date") == offered, "the day the caller took must survive"
        assert st.slots.get("time_slot") == "11:00"
        assert not any(NOT_SITTING[lang] in line for line in _spoken(call)), _spoken(call)
        assert call.blocked == []


@pytest.mark.parametrize("lang", LANGS)
def test_the_booking_reaches_the_patient_question_instead_of_looping(lang):
    """The call could not get past the day. It now gets to the next question."""
    today = datetime.date.today().isoformat()
    with conversation(lang) as call:
        _offered(call, lang)
        call.say(THIS_DAY[lang], B, {})
        said = call.say(ELEVEN[lang], B, {"date": today, "time_slot": "11:00"})
        assert said[-1].endswith("?")
        assert not any(NOT_SITTING[lang] in line for line in said), said


# ============================================================================ "this day" / "that day"


@pytest.mark.parametrize(
    "text",
    ["এদিনই করে দিন", "এই দিনেই করুন", "এদিন", "ঐদিন", "ওই দিনে করব", "সেদিনই",
     "इसी दिन कर दीजिए", "इस दिन", "उसी दिन", "this day please", "that day", "the same day"],
)
def test_this_day_and_that_day_both_take_the_offer(text):
    assert pick_offered_slot(text, [{"date": "2026-10-09", "time_slot": ""}])


@pytest.mark.parametrize(
    "text",
    ["এই দিন না", "অন্য দিন", "ওই দিন না", "not this day", "not that day", "नहीं, इस दिन नहीं",
     "শুক্রবার", "রোগীর নাম বাণী"],
)
def test_a_refusal_or_another_day_never_takes_the_offer(text):
    assert not pick_offered_slot(text, [{"date": "2026-10-09", "time_slot": ""}])


# ============================================================================ the guard does not over-block


@pytest.mark.parametrize("lang", LANGS)
def test_a_day_the_caller_really_says_still_changes_the_booking(lang):
    """Only a day nobody spoke is dropped. A day said out loud is still the caller's to change."""
    with conversation(lang) as call:
        offered = _offered(call, lang)
        call.say(THIS_DAY[lang], B, {})
        st = call.session.booking
        other = (datetime.date.fromisoformat(offered) + datetime.timedelta(days=7)).isoformat()
        moved = {
            "bn": f"না, {datetime.date.fromisoformat(other).day} তারিখে",
            "hi": f"नहीं, {datetime.date.fromisoformat(other).day} तारीख को",
            "en": f"no, on the {datetime.date.fromisoformat(other).day}th",
        }[lang]
        call.say(moved, B, {"date": other})
        assert st.slots.get("date") == other, (st.slots, _spoken(call))


def test_a_time_the_caller_really_says_still_changes_the_booking():
    with conversation("bn") as call:
        _offered(call, "bn")
        call.say(THIS_DAY["bn"], B, {})
        call.say(ELEVEN["bn"], B, {"time_slot": "11:00"})
        st = call.session.booking
        assert st.slots.get("time_slot") == "11:00"
        call.say("না, সাড়ে দশটা", B, {"time_slot": "10:30"})
        assert st.slots.get("time_slot") == "10:30", st.slots


def test_a_volunteered_time_does_not_replace_the_one_the_caller_gave():
    """The same rule on the other slot: the model fills time_slot unasked too."""
    with conversation("bn") as call:
        _offered(call, "bn")
        call.say(THIS_DAY["bn"], B, {})
        call.say(ELEVEN["bn"], B, {"time_slot": "11:00"})
        st = call.session.booking
        # a turn with no time in it at all, and a time volunteered by the model
        call.say("রোগীর নাম রাহুল", B, {"patient_name": "রাহুল", "time_slot": "09:00"})
        assert st.slots.get("time_slot") == "11:00", st.slots


# ============================================================================ the answer is not thrown away

# Two ASR decoders disagreeing on a two-syllable word is not a bad line. Below 0.25 the transcript-quality
# ladder handles it; from 0.30 a booking turn already carries on; between them sat a dead zone where the answer
# to the agent's own question was refused with "দুঃখিত, ঠিক শুনতে পাইনি" and discarded (live 2026-10-08, twice).
DEAD_ZONE = 0.27
CANT_HEAR = {
    "bn": "দুঃখিত, ঠিক শুনতে পাইনি",
    "hi": "माफ़ कीजिए, मैं ठीक से सुन नहीं पाई",
    "en": "Sorry, I didn't catch that",
}


@pytest.mark.parametrize("lang", LANGS)
def test_a_time_is_not_discarded_when_the_recognisers_disagree(lang):
    with conversation(lang) as call:
        offered = _offered(call, lang)
        call.say(THIS_DAY[lang], B, {})
        said = call.say(ELEVEN[lang], B, {"time_slot": "11:00"}, agreement=DEAD_ZONE, decoder="rnnt")
        st = call.session.booking
        assert st.slots.get("time_slot") == "11:00", (st.slots, said)
        assert st.slots.get("date") == offered
        assert not any(CANT_HEAR[lang] in line for line in said), said


@pytest.mark.parametrize("lang", LANGS)
def test_taking_the_offered_day_is_not_discarded_when_the_recognisers_disagree(lang):
    """"ঐদিন" does not depend on recognition accuracy at all -- it points at a day the agent itself just spoke."""
    with conversation(lang) as call:
        offered = _offered(call, lang)
        said = call.say(THIS_DAY[lang], B, {}, agreement=DEAD_ZONE, decoder="rnnt")
        assert call.session.booking.slots.get("date") == offered, (call.session.booking.slots, said)
        assert not any(CANT_HEAR[lang] in line for line in said), said


def test_a_doctor_name_heard_badly_is_still_held_back():
    """The gate's real job is unchanged: a MISHEARD NAME must never reach a lookup and be read out as fact."""
    with conversation("bn") as call:
        said = call.say("ডক্টর রায়ের কাছে দেখাব", B, {"doctor_name": "রায়"}, agreement=DEAD_ZONE, decoder="rnnt")
        st = call.session.booking
        assert st is None or not st.doctor_verified, "the doctor is not settled on a turn heard this badly"
        assert said[-1].endswith("?"), said
