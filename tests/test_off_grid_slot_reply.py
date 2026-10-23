"""Live 2026-10-10: "দশটা কুড়ি" (10:20) was refused with "say a time between 10:00 and 11:45" -- a range 10:20 is
already inside -- because the clinic books on a 15-minute grid (clinic-api SLOT_STEP_MIN) and 10:20 is not a start
time. The caller said it again, and again.

When the refused time is INSIDE the chamber's range but not on its grid, the reply now names the two start times
either side of it, taken from the clinic's own `valid_slots`. Every other refusal keeps its wording.

    python -m pytest tests/test_off_grid_slot_reply.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.reply_templates import booking_reply, off_grid_neighbours, reschedule_reply
from agent.speech_norm import unspeakable_spans

VALID = ["10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30", "11:45"]
REFUSED = {"success": False, "reason": "invalid_slot", "valid_slots": VALID}

# What the caller heard before this change, kept for the cases that must not change.
RANGE_BN = "দুঃখিত, ওই সময়ে অ্যাপয়েন্টমেন্ট হয় না। 10:00 থেকে 11:45-এর মধ্যে একটা সময় বলবেন?"
RANGE_EN = "Sorry, appointments aren't given at that time. Could you give a time between 10:00 and 11:45?"


# ================================================================== the neighbours
@pytest.mark.parametrize(
    "asked,expected",
    [
        ("10:20", ("10:15", "10:30", 15)),
        ("10:01", ("10:00", "10:15", 15)),
        ("10:44", ("10:30", "10:45", 15)),  # one minute before a start still sits between 10:30 and 10:45
        ("11:50", None),  # after the last start: outside the range
        ("09:50", None),  # before the first
        ("10:30", None),  # itself a start: nothing to explain
        ("", None),
        (None, None),
        ("ten twenty", None),  # unreadable
    ],
)
def test_the_neighbours_are_the_clinics_own_start_times_either_side(asked, expected):
    got = off_grid_neighbours(asked, VALID)
    assert got == expected


def test_an_unevenly_spaced_list_gives_neighbours_but_no_step():
    assert off_grid_neighbours("10:20", ["10:00", "10:15", "10:45"]) == ("10:15", "10:45", None)


def test_a_single_start_has_no_neighbours():
    assert off_grid_neighbours("10:20", ["10:00"]) is None


# ================================================================== the replies
def test_the_bengali_reply_is_exactly_what_was_agreed():
    said = booking_reply({"time_slot": "10:20"}, REFUSED, "bn")
    assert said == "দুঃখিত, 10:20-এ অ্যাপয়েন্টমেন্ট হয় না — সময়গুলো ১৫ মিনিট পরপর। 10:15 বা 10:30, কোনটা হলে হবে?"
    assert unspeakable_spans(said, "bn") == []  # the speech gate would not block it


@pytest.mark.parametrize("lang", ["hi", "en"])
def test_hindi_and_english_name_the_same_two_times(lang):
    said = booking_reply({"time_slot": "10:20"}, REFUSED, lang)
    assert "10:15" in said and "10:30" in said and "15" in said and "10:20" in said


def test_a_time_outside_the_range_keeps_the_range_wording():
    assert booking_reply({"time_slot": "09:00"}, REFUSED, "bn") == RANGE_BN
    assert booking_reply({"time_slot": "12:30"}, REFUSED, "en") == RANGE_EN


def test_no_requested_time_keeps_the_range_wording():
    assert booking_reply({}, REFUSED, "bn") == RANGE_BN
    assert booking_reply({"time_slot": None}, REFUSED, "en") == RANGE_EN


def test_a_move_to_a_new_time_is_unchanged():
    """reschedule_reply is not handed the time the caller gave, so it keeps the range wording as before."""
    assert reschedule_reply(REFUSED, "bn") == RANGE_BN
    assert reschedule_reply(REFUSED, "en") == RANGE_EN


def test_the_other_refusals_are_untouched():
    for reason, bn in (
        ("slot_passed", "ওই সময়টা আজ পেরিয়ে গেছে।"),
        ("sitting_over", "আজকের চেম্বারের সময় পেরিয়ে গেছে।"),
    ):
        said = booking_reply({"time_slot": "10:20"}, {"success": False, "reason": reason, "valid_slots": VALID}, "bn")
        assert said.startswith(bn)


# ================================================================== through the real orchestrator
from test_orchestrator_booking_flow import ASK_DETAILS, FakeTools, m, env, book, text_of  # noqa: E402,F401


@pytest.mark.asyncio
async def test_a_time_off_the_grid_is_answered_with_its_neighbours_and_a_valid_one_is_then_taken(m, env):
    async def hold_slot(doctor, date, slot):
        env.tools.holds.append((doctor, date, slot))
        if slot in VALID:
            return {"success": True, "hold_token": f"hold-{len(env.tools.holds)}", "doctor_id": 7}
        return {"success": False, "reason": "invalid_slot", "valid_slots": VALID}

    env.tools.hold_slot = hold_slot
    await book(env, doctor_name="Sen")
    said = await env.say("tomorrow at 10:20", "book_appointment", {"date": "2026-10-01", "time_slot": "10:20"})
    assert env.tools.holds[-1][0] == "Sen" and env.tools.holds[-1][2] == "10:20"  # the clinic was asked for 10:20
    assert "10:15" in text_of(said) and "10:30" in text_of(said) and "10:20" in text_of(said)
    assert env.session.booking.slots.get("time_slot") is None  # the refused time is not kept

    said = await env.say("at 10:15", "book_appointment", {"time_slot": "10:15"})
    assert env.tools.holds[-1][0] == "Sen" and env.tools.holds[-1][2] == "10:15"
    assert ASK_DETAILS in text_of(said)  # on to the patient's name and number
