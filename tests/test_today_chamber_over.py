"""A day said as a weekday that is TODAY, after its chamber is over (live 2026-10-07, 21:30).

"বুধবার" said on Wednesday evening resolved to today. Every time the caller then gave (10:30, 11:00)
was refused as `invalid_slot` with an empty list, and the agent asked for "a time within the chamber
hours" again and again -- a loop no answer could leave. The clinic now says why (`sitting_over`, or
`slot_passed` with the starts still left today), and the caller is offered the doctor's next sitting
or the times left, in Bengali, Hindi and English.

The clinic's clock is frozen here, so the case is tested at the hour it happened, not whenever CI runs.
"""

from __future__ import annotations

import datetime
import os
import sys
import tempfile
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLINIC_API_DIR = os.path.join(REPO_ROOT, "clinic-api")
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent.reply_templates import booking_reply, doctor_day_unavailable_prompt, reschedule_reply  # noqa: E402

LANGS = ("bn", "hi", "en")


# ============================================================================ clinic-api


@pytest.fixture()
def clinic():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["CLINIC_DB_PATH"] = db_path
    os.environ.pop("DATABASE_URL", None)
    if CLINIC_API_DIR not in sys.path:
        sys.path.insert(0, CLINIC_API_DIR)
    for mod in ("main", "db", "models", "seed", "booking_service", "booking_migrate", "enquiry_migrate", "i18n_content"):
        sys.modules.pop(mod, None)

    from fastapi.testclient import TestClient

    import main as clinic_main

    with TestClient(clinic_main.app) as c:
        yield c, clinic_main
    try:
        os.remove(db_path)
    except OSError:
        pass


def _freeze(monkeypatch, clinic_main, now: datetime.datetime) -> None:
    """clinic-api reads the clock as datetime.datetime.now() / datetime.date.today(); both answer `now`."""

    class _Date(datetime.date):
        @classmethod
        def today(cls):
            return now.date()

    class _DateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    fake = types.SimpleNamespace(**{k: getattr(datetime, k) for k in dir(datetime) if not k.startswith("__")})
    fake.date, fake.datetime = _Date, _DateTime
    monkeypatch.setattr(clinic_main, "datetime", fake)


def _a_sitting(c) -> tuple[str, datetime.date, list[str]]:
    """A doctor, a day they sit (a week or more out, so it is never today), and that day's chamber starts."""
    doctor = c.get("/api/v1/catalogue").json()["doctors"][0]["name"]
    for offset in range(7, 21):
        day = datetime.date.today() + datetime.timedelta(days=offset)
        probe = c.post("/api/v1/bookings/hold", json={"doctor_name": doctor, "date": day.isoformat(), "time_slot": "__probe__"})
        body = probe.json()
        if body.get("reason") == "invalid_slot" and body.get("valid_slots"):
            return doctor, day, body["valid_slots"]
    pytest.fail("no sitting in the seed data")


def _hold(c, doctor, day, slot):
    return c.post("/api/v1/bookings/hold", json={"doctor_name": doctor, "date": day.isoformat(), "time_slot": slot}).json()


def test_a_chamber_already_over_today_is_refused_as_such_not_as_an_invalid_time(clinic, monkeypatch):
    c, clinic_main = clinic
    doctor, day, starts = _a_sitting(c)
    _freeze(monkeypatch, clinic_main, datetime.datetime.combine(day, datetime.time(21, 30)))
    for slot in (starts[0], starts[-1], "10:30"):
        assert _hold(c, doctor, day, slot) == {"success": False, "reason": "sitting_over"}, slot


def test_a_start_already_gone_today_is_refused_with_the_starts_still_left(clinic, monkeypatch):
    c, clinic_main = clinic
    doctor, day, starts = _a_sitting(c)
    assert len(starts) >= 3
    h, m = (int(x) for x in starts[1].split(":"))
    _freeze(monkeypatch, clinic_main, datetime.datetime.combine(day, datetime.time(h, m)) + datetime.timedelta(minutes=1))
    refused = _hold(c, doctor, day, starts[0])
    assert refused["success"] is False and refused["reason"] == "slot_passed"
    assert refused["valid_slots"] == starts[2:]


def test_a_time_outside_the_chamber_on_another_day_is_still_invalid_slot(clinic, monkeypatch):
    c, _ = clinic
    doctor, day, starts = _a_sitting(c)
    refused = _hold(c, doctor, day, "03:45")
    assert refused["reason"] == "invalid_slot" and refused["valid_slots"] == starts


# ============================================================================ what the caller hears

GENERIC_REFUSAL = {
    "bn": "চেম্বারের সময়ের মধ্যে একটা সময় বলবেন?",
    "hi": "चेंबर के समय के भीतर कोई समय बताएँगे?",
    "en": "within the chamber hours",
}
NOT_SITTING = {"bn": "ওই দিন বসেন না", "hi": "उस दिन नहीं बैठते", "en": "doesn't sit that day"}


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("reply", [booking_reply, lambda s, r, lang: reschedule_reply(r, lang)])
def test_every_time_refusal_says_why_and_what_to_say_instead(lang, reply):
    starts = ["10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30", "11:45"]
    for result in (
        {"success": False, "reason": "invalid_slot", "valid_slots": starts},
        {"success": False, "reason": "slot_passed", "valid_slots": starts[4:]},
    ):
        said = reply({}, result, lang)
        assert result["valid_slots"][0] in said and result["valid_slots"][-1] in said, said
        assert GENERIC_REFUSAL[lang] not in said, said
    for result in ({"success": False, "reason": "slot_passed", "valid_slots": []}, {"success": False, "reason": "sitting_over"}):
        said = reply({}, result, lang)
        assert GENERIC_REFUSAL[lang] not in said and "?" in said, said


@pytest.mark.parametrize("lang", LANGS)
def test_a_chamber_over_today_is_never_said_as_the_doctor_not_sitting(lang):
    result = {"found": True, "doctor_name": "Dr. A. Sen", "next_available_date": "2026-10-09", "chamber_hours": "10:00-12:00"}
    over = doctor_day_unavailable_prompt(result, lang, sitting_over=True)
    assert NOT_SITTING[lang] not in over and "10:00-12:00" in over, over
    assert NOT_SITTING[lang] in doctor_day_unavailable_prompt(result, lang), "the ordinary refusal is unchanged"


# ============================================================================ the whole call

B = "book_appointment"
SEN = {"bn": "সেন", "hi": "सेन", "en": "Sen"}


@pytest.mark.parametrize("lang", LANGS)
def test_the_live_call_of_2026_10_07_offers_the_next_sitting_instead_of_looping(lang):
    from _conversation import conversation

    with conversation(lang) as call:
        tools = call.m._tools
        avail = call._run(tools.get_doctor_availability("Dr. A. Sen", None))
        day, start = avail["date"], avail["chamber_hours"].split("-")[0]
        holds = []

        async def chamber_over(doctor, date, slot):  # what the clinic answers on that day at 21:30
            holds.append((date, slot))
            return {"success": False, "reason": "sitting_over"}

        tools.hold_slot = chamber_over
        call.say("x", B, {"doctor_name": SEN[lang], "date": day, "time_slot": start})
        st = call.session.booking
        spoken = [line for who, line in call._log if who.startswith("AI")]
        assert holds == [(day, start)]
        assert spoken[-1] == doctor_day_unavailable_prompt(avail, lang, sitting_over=True)
        assert not st.slots.get("date") and st.offered_slots, "the day is asked again, with the next sitting offered"
        assert not any(GENERIC_REFUSAL[lang] in line for line in spoken)
        assert call.blocked == []
