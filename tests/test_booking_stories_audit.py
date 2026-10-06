"""Audit: are the nine "Booking, Rescheduling and Cancellation" stories in THIS tree?

Those stories were delivered elsewhere (a tree called `ivr_stagging`) and none of that code is
here -- different files, different function names, different story IDs. The capabilities were
built here independently, under Epic E26 / KCD-36x-48x, with different designs. This file checks
each acceptance criterion against the code that actually exists, so "present" is a test result
rather than someone's reading of the source.

HOW TO READ A RUN
-----------------
  passed   the acceptance criterion is met by this tree, and this test now guards it
  xfailed  a GAP: the criterion is not met today. The reason says what is missing.
  skipped  cannot be decided off-pod (real ASR, a real Qwen, TTS pace, a live SMS provider)

Every gap is `strict=True`, so whoever closes one gets a FAILURE telling them to turn the xfail
into a plain assertion rather than a silent XPASS nobody notices. That is the point: this file is
an inventory that keeps itself honest, not a list of complaints.

Nothing here needs a GPU, a model, audio or a network. clinic-api runs in-process against a
throwaway SQLite file, exactly as tests/test_booking_endpoints.py does.

    python -m pytest tests/test_booking_stories_audit.py -v -rxs
"""

from __future__ import annotations

import datetime
import os
import re
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLINIC_API_DIR = os.path.join(REPO_ROOT, "clinic-api")
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Needs the pod, in every case because a model or a real signal is the thing under test.
POD = "needs the pod: a real model, real audio or a real provider is the subject of this check"


def _source(*parts: str) -> str:
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as f:
        return f.read()


_CLINIC_MODULES = ("main", "db", "models", "seed", "booking_service", "booking_migrate",
                   "enquiry_migrate", "i18n_content")


def _evict_clinic_modules() -> None:
    """Drop every module that resolved out of clinic-api/, by file, not by a hand-kept name list.

    `main` is ambiguous in this repo -- the orchestrator at the root and clinic-api/main.py -- and
    `db`/`models` bind their engine AT IMPORT. Leaving either loaded makes the NEXT test file's
    clinic-api import silently reuse this test's deleted temporary database. Measured, not
    theorised: without the teardown half of this, 13 tests in later files error out on
    `import main`. tests/_pod_stubs.py guards the same seam the same way."""
    for name in list(sys.modules):
        if name in _CLINIC_MODULES:
            path = getattr(sys.modules[name], "__file__", "") or ""
            if os.path.dirname(os.path.abspath(path)) == CLINIC_API_DIR:
                del sys.modules[name]


@pytest.fixture()
def clinic_client(monkeypatch):
    """clinic-api over HTTP on a throwaway database, leaving no trace in sys.modules or sys.path."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("CLINIC_DB_PATH", db_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    saved_path = list(sys.path)
    sys.path.insert(0, CLINIC_API_DIR)
    _evict_clinic_modules()

    from fastapi.testclient import TestClient

    import main as clinic_main

    try:
        with TestClient(clinic_main.app) as c:
            yield c
    finally:
        _evict_clinic_modules()
        sys.path[:] = saved_path
        try:
            os.remove(db_path)
        except OSError:
            pass


def _sitting_day(c, doctor_name: str, min_days_ahead: int = 2) -> str:
    for i in range(min_days_ahead, min_days_ahead + 14):
        d = datetime.date.today() + datetime.timedelta(days=i)
        r = c.get("/api/v1/doctors/availability", params={"name": doctor_name, "date": d.isoformat()})
        if r.json().get("available"):
            return d.isoformat()
    pytest.fail("no sitting day in the next two weeks of seeded data")


def _booked(c, *, phone: str = "9123456780", min_days_ahead: int = 2) -> dict:
    """A confirmed appointment through the real hold -> confirm path. Returns the confirm body
    plus the doctor/date/slot it used."""
    doctor_name = c.get("/api/v1/catalogue").json()["doctors"][0]["name"]
    date = _sitting_day(c, doctor_name, min_days_ahead)
    probe = c.post("/api/v1/bookings/hold",
                   json={"doctor_name": doctor_name, "date": date, "time_slot": "__probe__"}).json()
    slot = probe["valid_slots"][0]
    hold = c.post("/api/v1/bookings/hold",
                  json={"doctor_name": doctor_name, "date": date, "time_slot": slot}).json()
    assert hold["success"], hold
    confirm = c.post("/api/v1/bookings/confirm", json={
        "hold_token": hold["hold_token"], "doctor_id": hold["doctor_id"], "date": date,
        "time_slot": slot, "patient_name": "Test Patient", "phone": phone, "caller_phone": phone,
    }).json()
    assert confirm["success"], confirm
    return {**confirm, "doctor_name": doctor_name, "date": date, "time_slot": slot,
            "doctor_id": hold["doctor_id"]}


# ===================================================== 22. Caller moves an existing appointment
# AC: found by contact number, name or reference; the new slot swapped atomically, holding the
# old one until the new commits, a failed swap leaving the original intact; confirmation on both
# channels.


def test_22_the_intent_exists():
    from agent.intent_schema import VALID_INTENTS

    assert "reschedule_appointment" in VALID_INTENTS


def test_22_a_booking_is_found_by_reference_and_by_phone(clinic_client):
    c = clinic_client
    b = _booked(c)
    by_ref = c.get("/api/v1/bookings/lookup", params={"confirmation_id": b["confirmation_id"]}).json()
    by_phone = c.get("/api/v1/bookings/lookup", params={"phone": "9123456780"}).json()
    assert by_ref["found"] and by_phone["found"]
    # A name alone identifies nobody: the endpoint needs a phone or a reference.
    assert c.get("/api/v1/bookings/lookup", params={"name": "Test Patient"}).json()["found"] is False


def test_22_the_slot_actually_moves(clinic_client):
    c = clinic_client
    b = _booked(c)
    new_date = _sitting_day(c, b["doctor_name"], min_days_ahead=5)
    new_slot = c.post("/api/v1/bookings/hold", json={
        "doctor_name": b["doctor_name"], "date": new_date, "time_slot": "__probe__"}).json()["valid_slots"][1]
    moved = c.post("/api/v1/bookings/reschedule", json={
        "confirmation_id": b["confirmation_id"], "new_date": new_date, "new_time_slot": new_slot}).json()
    assert moved["success"] and moved["date"] == new_date and moved["time_slot"] == new_slot


def test_22_a_failed_swap_leaves_the_original_intact(clinic_client):
    """The property the story cares about most. The new slot is held FIRST
    (booking_service.reschedule_appointment), so a slot another patient holds cannot cost this
    caller the appointment they already have."""
    c = clinic_client
    mine = _booked(c, phone="9123456780")
    theirs_date = _sitting_day(c, mine["doctor_name"], min_days_ahead=5)
    slots = c.post("/api/v1/bookings/hold", json={
        "doctor_name": mine["doctor_name"], "date": theirs_date, "time_slot": "__probe__"}).json()["valid_slots"]
    theirs = c.post("/api/v1/bookings/hold", json={
        "doctor_name": mine["doctor_name"], "date": theirs_date, "time_slot": slots[0]}).json()
    c.post("/api/v1/bookings/confirm", json={
        "hold_token": theirs["hold_token"], "doctor_id": theirs["doctor_id"], "date": theirs_date,
        "time_slot": slots[0], "patient_name": "Other Patient", "phone": "9000000001",
        "caller_phone": "9000000001"})

    refused = c.post("/api/v1/bookings/reschedule", json={
        "confirmation_id": mine["confirmation_id"], "new_date": theirs_date,
        "new_time_slot": slots[0]}).json()
    assert refused["success"] is False and refused["reason"] == "slot_taken"
    assert refused["alternative_slots"], "a refusal must still offer something"

    still_there = c.get("/api/v1/bookings/lookup",
                        params={"confirmation_id": mine["confirmation_id"]}).json()
    assert still_there["found"], "the caller's original appointment was lost on a failed swap"
    assert still_there["bookings"][0]["date"] == mine["date"]
    assert still_there["bookings"][0]["time_slot"] == mine["time_slot"]


def test_22_a_retried_move_happens_once(clinic_client):
    """Idempotency-Key on the write, so a retry cannot move an appointment twice."""
    c = clinic_client
    b = _booked(c)
    new_date = _sitting_day(c, b["doctor_name"], min_days_ahead=5)
    new_slot = c.post("/api/v1/bookings/hold", json={
        "doctor_name": b["doctor_name"], "date": new_date, "time_slot": "__probe__"}).json()["valid_slots"][1]
    body = {"confirmation_id": b["confirmation_id"], "new_date": new_date, "new_time_slot": new_slot}
    headers = {"Idempotency-Key": "audit-22-retry"}
    first = c.post("/api/v1/bookings/reschedule", json=body, headers=headers).json()
    second = c.post("/api/v1/bookings/reschedule", json=body, headers=headers).json()
    assert first == second, "a retry under the same key must replay, not move again"


def test_22_the_whole_change_is_read_back_before_it_is_written():
    from agent.reply_templates import booking_confirmation_readback

    said = booking_confirmation_readback(
        {"new_date": "2026-10-15", "new_time_slot": "10:30"}, "reschedule_appointment", "bn")
    assert "2026-10-15" in said and "10:30" in said and said.rstrip().endswith("?")


def test_22_the_written_confirmation_is_queued_not_claimed_as_sent(clinic_client):
    """"Both channels" is voice + SMS. The SMS half is an honest placeholder: queue_sms never
    returns "sent" and no reply claims a message arrived. The gap is the provider, not the wiring."""
    c = clinic_client
    b = _booked(c)
    new_date = _sitting_day(c, b["doctor_name"], min_days_ahead=5)
    new_slot = c.post("/api/v1/bookings/hold", json={
        "doctor_name": b["doctor_name"], "date": new_date, "time_slot": "__probe__"}).json()["valid_slots"][1]
    c.post("/api/v1/bookings/reschedule", json={
        "confirmation_id": b["confirmation_id"], "new_date": new_date, "new_time_slot": new_slot})

    import booking_service as bs

    assert 'status="sent"' not in _source("clinic-api", "booking_service.py")
    assert "never \"sent\"" in bs.queue_sms.__doc__


@pytest.mark.skip(reason=POD + " -- a real SMS/WhatsApp provider must deliver the message")
def test_22_the_confirmation_actually_reaches_the_phone():
    ...


# ============================================================ 23. Caller cancels an appointment
# AC: the configured window rules apply and refund eligibility is stated from policy, never
# improvised; a cancellation inside a charging window is confirmed explicitly with the charge
# stated before it is applied.


def test_23_the_intent_exists():
    from agent.intent_schema import VALID_INTENTS

    assert "cancel_appointment" in VALID_INTENTS


def test_23_the_charge_comes_from_a_versioned_policy_row_not_a_constant(clinic_client):
    """tests/test_cancellation_policy.py covers the window arithmetic. This only pins that the
    rule is configuration with an effective date and a version, as the story requires.
    (Takes clinic_client so clinic-api/ is importable: this file no longer leaks sys.path.)"""
    src = _source("clinic-api", "booking_service.py")
    assert "def active_cancellation_policy" in src
    assert "CANCELLATION_FREE_WINDOW_HOURS" not in src, "the old flat constant is back"
    from models import CancellationPolicy

    for column in ("version", "effective_from", "free_window_hours", "charge_percent"):
        assert hasattr(CancellationPolicy, column), f"the policy row has no {column}"


def test_23_a_charge_is_stated_and_a_second_yes_required_before_anything_is_written(clinic_client):
    c = clinic_client
    b = _booked(c)
    # Put the appointment inside the charging window deterministically, rather than depending on
    # which day the seeded chamber hours make bookable: widen the free window past it, and make
    # sure the doctor has a fee for the percentage to apply to.
    import booking_service as bs
    import db as db_mod
    from models import Doctor

    with db_mod.SessionLocal() as s:
        policy = bs.active_cancellation_policy(s)
        assert policy is not None, "no policy row: seed_default_cancellation_policy did not run"
        policy.free_window_hours = 24 * 365
        policy.charge_percent = policy.charge_percent or 50
        policy.refund_eligible = True
        doctor = s.get(Doctor, b["doctor_id"])
        doctor.consultation_fee_inr = doctor.consultation_fee_inr or 500
        s.commit()

    first = c.post("/api/v1/bookings/cancel", json={"confirmation_id": b["confirmation_id"]}).json()
    assert first["reason"] == "charge_confirmation_required" and first["charge_inr"] > 0
    # Nothing was written: the appointment is still live.
    assert c.get("/api/v1/bookings/lookup",
                 params={"confirmation_id": b["confirmation_id"]}).json()["found"]
    # The amount is in the caller's ear before the second question.
    from agent.reply_templates import cancel_reply

    assert str(first["charge_inr"]) in cancel_reply(first, "bn")

    agreed = c.post("/api/v1/bookings/cancel",
                    json={"confirmation_id": b["confirmation_id"], "confirm_charge": True}).json()
    assert agreed["success"] and agreed["charge_inr"] == first["charge_inr"]


def test_23_a_free_cancellation_needs_no_charge_question(clinic_client):
    c = clinic_client
    b = _booked(c, min_days_ahead=9)
    done = c.post("/api/v1/bookings/cancel", json={"confirmation_id": b["confirmation_id"]}).json()
    assert done["success"] and (done.get("charge_inr") or 0) == 0


def test_23_the_rule_that_applied_is_recorded_on_the_row(clinic_client):
    c = clinic_client
    b = _booked(c, min_days_ahead=9)
    c.post("/api/v1/bookings/cancel", json={"confirmation_id": b["confirmation_id"]})

    import db as db_mod
    from models import Appointment

    with db_mod.SessionLocal() as s:
        row = s.query(Appointment).filter_by(confirmation_id=b["confirmation_id"]).first()
        assert row.status == "cancelled" and row.cancellation_policy_version


def test_23_refund_eligibility_is_stated_from_policy():
    from agent.reply_templates import cancel_reply

    # Asserted on the WORD, not a number: an earlier version of this test looked for "50" and
    # matched the "150" of the charge, so it passed while saying nothing about refunds.
    said = cancel_reply({"success": True, "charge_inr": 150, "refund_eligibility": "partial",
                         "refund_percent": 50}, "bn")
    assert "রিফান্ড" in said and "50" in said


@pytest.mark.parametrize("lang,full,none_", [
    ("bn", "পুরো রিফান্ডের যোগ্য", "কোনো রিফান্ড প্রযোজ্য নয়"),
    ("hi", "पूरे रिफ़ंड", "कोई रिफ़ंड नहीं"),
    ("en", "full refund", "no refund applies"),
])
def test_23_refund_eligibility_is_stated_in_every_language(lang, full, none_):
    from agent.reply_templates import cancel_reply

    assert full in cancel_reply({"success": True, "charge_inr": 0, "refund_eligibility": "full"}, lang)
    assert none_ in cancel_reply({"success": True, "charge_inr": 500, "refund_eligibility": "none"}, lang)


def test_23_the_refund_is_stated_before_the_caller_agrees_to_the_charge():
    """Agreeing to a deduction without hearing what comes back is not an informed yes."""
    from agent.reply_templates import cancel_reply

    for lang in ("bn", "hi", "en"):
        said = cancel_reply({"reason": "charge_confirmation_required", "charge_inr": 150,
                             "refund_eligibility": "partial", "refund_percent": 50}, lang)
        assert "150" in said and "50" in said


def test_23_refund_eligibility_comes_from_the_policy_row(clinic_client):
    """refund_terms derives eligibility from the SAME row that produced the charge -- never a
    second rule, and never a rupee amount (there is no payment system to honour one)."""
    import booking_service as bs

    class _Policy:
        def __init__(self, refund_eligible, charge_percent):
            self.refund_eligible, self.charge_percent = refund_eligible, charge_percent

    assert bs.refund_terms(_Policy(True, 50), 0) == ("full", None)
    assert bs.refund_terms(_Policy(True, 50), 250) == ("partial", 50)
    assert bs.refund_terms(_Policy(False, 50), 500) == ("none", None)
    assert bs.refund_terms(_Policy(True, 100), 500) == ("none", None)
    assert bs.refund_terms(None, 0) == ("none", None), "no policy must promise nothing"


# ====================================================== 24. Caller gives everything in one sentence
# AC: a caller stating doctor, day, time and patient name is asked only to confirm; every slot is
# filled from that turn; no question already answered is asked again; the confirmation reads back
# all four values; median turns is two.


def test_24_every_slot_is_filled_from_one_turn():
    from agent.booking_flow import merge_slots, missing_required, new_state

    st = new_state("book_appointment")
    merge_slots(st, {"doctor_name": "Sen", "date": "2026-10-15", "time_slot": "10:15",
                     "patient_name": "Rahul Das", "phone": "9876543210"})
    assert missing_required(st) == [], f"still asking for {missing_required(st)}"


def test_24_a_question_already_answered_is_not_asked_again():
    from agent.booking_flow import merge_slots, missing_required, new_state

    st = new_state("book_appointment")
    merge_slots(st, {"doctor_name": "Sen", "date": "2026-10-15", "time_slot": "10:15"})
    still = missing_required(st)
    assert "doctor_name" not in still and "date" not in still and "time_slot" not in still
    assert "patient_name" in still


def test_24_the_readback_names_every_value_including_the_phone():
    from agent.reply_templates import booking_confirmation_readback

    slots = {"doctor_name": "Sen", "date": "2026-10-15", "time_slot": "10:15",
             "patient_name": "Rahul Das", "phone": "9876543210"}
    said = booking_confirmation_readback(slots, "book_appointment", "bn")
    for value in slots.values():
        assert value in said, f"{value!r} is not read back"


def test_24_nothing_is_written_until_the_booking_is_ready_and_confirmed():
    from agent.booking_flow import is_ready_to_confirm, merge_slots, new_state

    st = new_state("book_appointment")
    merge_slots(st, {"doctor_name": "Sen", "date": "2026-10-15"})
    assert not is_ready_to_confirm(st)


@pytest.mark.xfail(strict=True, reason="GAP: nothing counts caller turns, so the story's "
                                       "'median turns is two' cannot be computed from logs. The "
                                       "other tree logs 'confirmed after N caller turn(s)'.")
def test_24_the_turns_taken_to_confirm_are_recorded():
    assert re.search(r"caller turn\(s\)|turns_to_confirm", _source("main.py"))


@pytest.mark.skip(reason=POD + " -- whether Qwen fills all five slots from one real sentence")
def test_24_the_model_fills_every_slot_from_a_real_utterance():
    ...


# ===================================================================== 25. Caller names only a doctor
# AC: the agent confirms the doctor, offers the next available sittings, and collects date, time,
# patient and contact in grouped questions; no sitting in the window is answered with the nearest
# alternative rather than a bare refusal.


def test_25_the_remaining_fields_are_asked_in_groups():
    from agent.slot_grouping import grouped_prompt, next_fields

    fields = next_fields("book_appointment", ["date", "time_slot", "patient_name", "phone"],
                         questions_per_turn=3)
    assert len(fields) > 1, "every field is still asked on its own"
    assert grouped_prompt(fields, "bn"), "no wording exists for the grouped question"


def test_25_a_grouped_question_is_capped_by_the_caller_state_table():
    from agent.slot_grouping import next_fields

    one = next_fields("book_appointment", ["date", "time_slot"], questions_per_turn=1)
    assert one == ["date"], "a distressed or older caller must still get one question at a time"


def test_25_a_day_the_doctor_does_not_sit_offers_their_next_one():
    from agent.reply_templates import doctor_availability_reply

    said = doctor_availability_reply(
        {"doctor_name": "Sen"},
        {"found": True, "available": False, "next_available_date": "2026-10-17",
         "doctor_name_bn": "সেন"}, "bn")
    assert "2026-10-17" in said, "a refusal with no alternative is a dead end"


def test_25_the_opening_offers_the_next_available_sittings():
    src = _source("main.py")
    assert "_offer_days_or_slots" in src and "doctor_offer_prompt" in src


@pytest.mark.parametrize("lang,marker", [("bn", "পরের বসার দিন"), ("hi", "अगला दिन"), ("en", "next sitting")])
def test_25_the_doctor_is_confirmed_with_their_next_sitting(lang, marker):
    from agent.reply_templates import doctor_offer_prompt

    said = doctor_offer_prompt({
        "found": True, "doctor_name": "Dr. A. Sen", "doctor_name_bn": "সেন", "doctor_name_hi": "सेन",
        "next_available_date": "2026-10-06", "chamber_hours": "10:00-12:00"}, lang)
    assert marker in said and "2026-10-06" in said and "10:00-12:00" in said
    assert said.rstrip().endswith("?"), "the offer must end by asking, not trail off"


def test_25_a_doctor_who_never_sits_is_not_offered_a_day():
    from agent.reply_templates import doctor_offer_prompt

    said = doctor_offer_prompt({"found": True, "doctor_name": "Dr. A. Sen", "doctor_name_bn": "সেন"}, "bn")
    assert "2026" not in said and said.rstrip().endswith("?")


# ========================================== 26. Caller asks for the earliest available appointment
# AC: the earliest available slot with its day and time, plus the next two alternatives;
# availability read live; nothing within a configured horizon is said so, with a callback offered.


def test_26_the_service_can_compute_the_earliest_slot_and_two_alternatives(clinic_client):
    c = clinic_client
    doctor_name = c.get("/api/v1/catalogue").json()["doctors"][0]["name"]
    body = c.get("/api/v1/doctors/earliest", params={"name": doctor_name}).json()
    assert body.get("found"), body
    assert body["date"] and body["time_slot"]
    assert isinstance(body.get("alternatives"), list)


def test_26_the_earliest_slot_is_read_live_and_not_one_already_taken(clinic_client):
    """Booking the earliest slot must change the next answer -- the story's "read live"."""
    c = clinic_client
    doctor_name = c.get("/api/v1/catalogue").json()["doctors"][0]["name"]
    before = c.get("/api/v1/doctors/earliest", params={"name": doctor_name}).json()
    hold = c.post("/api/v1/bookings/hold", json={
        "doctor_name": doctor_name, "date": before["date"], "time_slot": before["time_slot"]}).json()
    assert hold["success"], hold
    c.post("/api/v1/bookings/confirm", json={
        "hold_token": hold["hold_token"], "doctor_id": hold["doctor_id"], "date": before["date"],
        "time_slot": before["time_slot"], "patient_name": "Early Bird", "phone": "9000000002",
        "caller_phone": "9000000002"})
    after = c.get("/api/v1/doctors/earliest", params={"name": doctor_name}).json()
    assert (after["date"], after["time_slot"]) != (before["date"], before["time_slot"])


def test_26_the_earliest_offer_is_reachable_from_the_call_loop():
    src = _source("main.py")
    assert "doctor_earliest" in src and "earliest_slots_prompt" in src


@pytest.mark.parametrize("text,lang", [
    ("ডাক্তার সেনের কাছে সবচেয়ে তাড়াতাড়ি কবে পাব?", "bn"),
    ("যত তাড়াতাড়ি সম্ভব অ্যাপয়েন্টমেন্ট চাই", "bn"),
    ("डॉक्टर सेन का जल्द से जल्द अपॉइंटमेंट", "hi"),
    ("सबसे पहले कब मिल सकता है", "hi"),
    ("what is the earliest appointment with Dr Sen", "en"),
    ("I need the soonest slot", "en"),
    ("Dr Sen earliest slot", "bn"),
])
def test_26_asking_for_the_earliest_is_recognised(text, lang):
    from agent.earliest_request import wants_earliest

    assert wants_earliest(text, lang)


@pytest.mark.parametrize("text,lang", [
    ("কাল অ্যাপয়েন্টমেন্ট চাই", "bn"),
    ("সকালে আসতে চাই", "bn"),
    ("कल अपॉइंटमेंट चाहिए", "hi"),
    ("I want an appointment tomorrow", "en"),
])
def test_26_an_ordinary_booking_is_not_read_as_an_earliest_request(text, lang):
    from agent.earliest_request import wants_earliest

    assert not wants_earliest(text, lang)


@pytest.mark.parametrize("lang,marker", [
    ("bn", "সবচেয়ে আগে"), ("hi", "सबसे पहले"), ("en", "next in on")])
def test_26_the_offer_names_the_day_the_time_and_the_alternatives(lang, marker):
    from agent.reply_templates import earliest_slots_prompt

    said = earliest_slots_prompt({
        "found": True, "available": True, "doctor_name": "Dr. A. Sen", "doctor_name_bn": "সেন",
        "doctor_name_hi": "सेन", "date": "2026-10-06", "time_slot": "10:00",
        "alternatives": ["10:15", "10:30"]}, lang)
    assert marker in said
    for value in ("2026-10-06", "10:00", "10:15", "10:30"):
        assert value in said
    # The question is mid-sentence here: the offer closes by reminding the caller they may name
    # a different day instead, so it asks without ENDING on the question mark.
    assert "?" in said


@pytest.mark.parametrize("lang,marker", [
    ("bn", "ফোন নম্বর"), ("hi", "फ़ोन नंबर"), ("en", "phone number")])
def test_26_an_empty_horizon_is_said_and_a_callback_offered(lang, marker):
    from agent.reply_templates import earliest_none_prompt

    result = {"found": True, "available": False, "doctor_name": "Dr. A. Sen",
              "doctor_name_bn": "সেন", "doctor_name_hi": "सेन"}
    said = earliest_none_prompt(result, lang)
    assert marker in said and said.rstrip().endswith("?"), "a dead end, not a next step"
    # With callbacks switched off the caller is still given somewhere to go, and is NOT asked for
    # a number nothing will be done with.
    off = earliest_none_prompt(result, lang, offer_callback=False)
    assert marker not in off and off.strip()


def test_26_the_empty_horizon_path_uses_the_callback_flow_that_already_exists():
    src = _source("main.py")
    none_branch = src[src.index("earliest_none_prompt(result, lang, offer_callback=False)") - 600:][:1200]
    assert "_queue_callback_request" in none_branch and "awaiting_callback_phone" in none_branch
    assert "CALLBACKS_ENABLED" in none_branch, "the existing on/off switch must still govern it"


@pytest.mark.xfail(strict=True, reason="KNOWN: the 14-day horizon is a default argument on "
                                       "booking_service.earliest_available, not clinic "
                                       "configuration. The story calls it 'a configured horizon'. "
                                       "Making it a setting is a clinic-api change nobody has "
                                       "asked for; the behaviour at the boundary is correct.")
def test_26_the_horizon_is_clinic_configuration():
    assert "EARLIEST_SLOT_HORIZON" in _source("clinic-api", "booking_service.py")


# ================================================================ 27. Requested slot is already taken
# AC: the two nearest alternatives on the same day and the same time on the nearest other day, in
# one sentence; the caller may accept one by saying which; no alternative offered that is not
# actually free at the moment of speaking.


def test_27_the_service_computes_the_shape_the_story_asks_for(clinic_client):
    """nearest_alternatives does exactly this: nearest time that day, plus the same time on the
    nearest other day -- each carrying its own date."""
    c = clinic_client
    b = _booked(c)

    import booking_service as bs
    import db as db_mod

    with db_mod.SessionLocal() as s:
        alts = bs.nearest_alternatives(s, b["doctor_id"], b["date"], b["time_slot"])
    assert alts, "a taken slot produced no alternatives at all"
    assert all({"date", "time_slot"} <= set(a) for a in alts)
    assert any(a["date"] != b["date"] for a in alts), "no other-day alternative was offered"


def test_27_an_offered_alternative_is_actually_free_right_now(clinic_client):
    c = clinic_client
    b = _booked(c)

    import booking_service as bs
    import db as db_mod

    with db_mod.SessionLocal() as s:
        for a in bs.nearest_alternatives(s, b["doctor_id"], b["date"], b["time_slot"]):
            assert a["time_slot"] in bs.available_slots(s, b["doctor_id"], a["date"])


def test_27_the_caller_hears_the_free_times_and_is_asked_which():
    from agent.reply_templates import booking_reply

    said = booking_reply({}, {"success": False, "reason": "slot_taken",
                              "alternative_slots": ["11:00", "11:30"]}, "bn")
    assert "11:00" in said and "11:30" in said and said.rstrip().endswith("?")


def test_27_the_http_layer_keeps_the_day_of_an_other_day_alternative(clinic_client):
    """The bug this story was about: both alternatives were flattened to bare times, so the
    other-day slot was spoken as though it were the day the caller asked for."""
    c = clinic_client
    b = _booked(c)
    import booking_service as bs
    import db as db_mod

    with db_mod.SessionLocal() as s:
        service_said = bs.nearest_alternatives(s, b["doctor_id"], b["date"], b["time_slot"])
    if not [a for a in service_said if a["date"] != b["date"]]:
        pytest.skip("the seeded data offered no other-day alternative to compare")

    over_http = c.post("/api/v1/bookings/hold", json={
        "doctor_name": b["doctor_name"], "date": b["date"], "time_slot": b["time_slot"]}).json()
    other = over_http.get("other_day_slot")
    assert other and other.get("date") and other["date"] != b["date"], (
        "the other-day alternative lost its date")
    # ...and every bare time left in alternative_slots really is on the day asked about.
    with db_mod.SessionLocal() as s:
        same_day_free = bs.available_slots(s, b["doctor_id"], b["date"])
    assert all(t in same_day_free for t in over_http["alternative_slots"])


@pytest.mark.parametrize("lang,marker", [("bn", "বৃহস্পতিবার"), ("hi", "गुरुवार"), ("en", "Thursday")])
def test_27_the_caller_hears_which_day_the_other_slot_is_on(lang, marker):
    from agent.reply_templates import booking_reply

    said = booking_reply({}, {"success": False, "reason": "slot_taken",
                              "alternative_slots": ["11:00"],
                              "other_day_slot": {"date": "2026-09-24", "time_slot": "11:15"}}, lang)
    assert marker in said and "2026-09-24" in said and said.rstrip().endswith("?")


def test_27_a_reschedule_refusal_offers_the_same_shape(clinic_client):
    """The same situation on the reschedule path, so the caller is not offered first-free there
    and nearest here."""
    c = clinic_client
    mine = _booked(c, phone="9123456780")
    taken_date = _sitting_day(c, mine["doctor_name"], min_days_ahead=5)
    slots = c.post("/api/v1/bookings/hold", json={
        "doctor_name": mine["doctor_name"], "date": taken_date,
        "time_slot": "__probe__"}).json()["valid_slots"]
    theirs = c.post("/api/v1/bookings/hold", json={
        "doctor_name": mine["doctor_name"], "date": taken_date, "time_slot": slots[0]}).json()
    c.post("/api/v1/bookings/confirm", json={
        "hold_token": theirs["hold_token"], "doctor_id": theirs["doctor_id"], "date": taken_date,
        "time_slot": slots[0], "patient_name": "Other Patient", "phone": "9000000001",
        "caller_phone": "9000000001"})

    refused = c.post("/api/v1/bookings/reschedule", json={
        "confirmation_id": mine["confirmation_id"], "new_date": taken_date,
        "new_time_slot": slots[0]}).json()
    assert refused["reason"] == "slot_taken"
    assert "other_day_slot" in refused, "the reschedule refusal still offers first-free only"


@pytest.mark.xfail(strict=True, reason="KNOWN, on a deprecated path: POST /api/v1/appointments "
                                       "(the pre-E26 endpoint, invisible to SlotLock by its own "
                                       "docstring) still offers available_slots[:3] -- first free, "
                                       "not nearest. The live path is /bookings/hold, which was "
                                       "fixed. Left deliberately rather than improving an endpoint "
                                       "the voice agent does not use.")
def test_27_the_legacy_booking_endpoint_also_offers_the_nearest():
    src = _source("clinic-api", "main.py")
    taken = src[src.index('"reason": "slot_taken"'):][:400]
    assert "nearest_alternatives" in taken


# ================================================ 28. Caller says tomorrow, day after, next Monday
# AC: relative expressions resolve against the call date in every supported language, including
# tomorrow, day after tomorrow, this coming weekday and next week; the resolved absolute date is
# always read back before use; ambiguous expressions are clarified rather than assumed.


def _resolve(text: str, lang: str, today: datetime.date) -> str | None:
    from agent import fast_path_cues as cues
    from agent.fast_path import Catalogue, FastPath

    fp = FastPath(Catalogue({"tests": [], "doctors": [], "faq_topics": []}), today=today)
    return fp._resolve_date(text, cues.table_for(lang))[0]


def test_28_bengali_today_tomorrow_and_day_after_resolve_in_code():
    today = datetime.date(2026, 9, 21)
    assert _resolve("আজ", "bn", today) == "2026-09-21"
    assert _resolve("কাল", "bn", today) == "2026-09-22"
    assert _resolve("পরশু", "bn", today) == "2026-09-23"


def test_28_english_today_tomorrow_and_day_after_resolve_in_code():
    today = datetime.date(2026, 9, 21)
    assert _resolve("today", "en", today) == "2026-09-21"
    assert _resolve("tomorrow", "en", today) == "2026-09-22"
    assert _resolve("day after tomorrow", "en", today) == "2026-09-23"


def test_28_an_ambiguous_day_word_is_not_guessed_at():
    """Hindi "kal" means both tomorrow and yesterday. The cue table leaves it out deliberately, so
    the turn abstains to the model rather than resolving it to a date."""
    from agent import fast_path_cues as cues

    assert "कल" not in cues.table_for("hi").relative_days


def test_28_the_resolved_date_is_read_back_before_the_booking_is_written():
    from agent.reply_templates import booking_confirmation_readback

    said = booking_confirmation_readback(
        {"doctor_name": "Sen", "date": "2026-09-22", "time_slot": "10:15",
         "patient_name": "Rahul Das", "phone": "9876543210"}, "book_appointment", "bn")
    assert "2026-09-22" in said and said.rstrip().endswith("?")


@pytest.mark.xfail(strict=True, reason="DELIBERATE (merge_report.md 4.10, owner-confirmed): Hindi "
                                       "resolves only आज in code because कल and परसों genuinely mean "
                                       "both tomorrow and yesterday. The readback added for this "
                                       "story covers the risk instead -- the model's date is spoken "
                                       "back before any lookup -- without guessing which कल means.")
def test_28_hindi_tomorrow_resolves_in_code():
    assert _resolve("कल", "hi", datetime.date(2026, 9, 21)) == "2026-09-22"


@pytest.mark.xfail(strict=True, reason="DELIBERATE: resolving weekdays in code needs the model to "
                                       "name an expression instead of a date -- a prompt change the "
                                       "owner excluded (merge_report.md 2). The cross-check and "
                                       "readback added for this story make the model's reading "
                                       "audible to the caller, which is the part that was missing.")
def test_28_a_coming_weekday_resolves_in_code():
    # 2026-09-21 is a Monday; the coming Monday is the 28th.
    assert _resolve("আগামী সোমবার", "bn", datetime.date(2026, 9, 21)) == "2026-09-28"


def test_28_the_model_is_currently_the_one_resolving_dates():
    """Not a gap on its own -- it is the design. Pinned so the truth-boundary question stays
    visible: if this stops being true, the xfails above should be revisited together."""
    assert "resolve relative time words" in _source("agent", "llm.py")
    assert "yyyy-mm-dd" in _source("agent", "llm.py")


def test_28_a_resolved_date_is_read_back_before_an_availability_lookup():
    src = _source("main.py")
    assert "_confirm_spoken_date" in src and "date_check_prompt" in src
    branch = src[src.index('elif intent == "doctor_availability":'):][:600]
    assert "_confirm_spoken_date" in branch, "the check must run BEFORE the lookup"


@pytest.mark.parametrize("lang,marker", [("bn", "ঠিক আছে?"), ("hi", "सही है?"), ("en", "Is that right?")])
def test_28_the_readback_names_the_weekday_and_the_date(lang, marker):
    from agent.reply_templates import date_check_prompt

    said = date_check_prompt("2026-10-06", None, lang)
    assert marker in said and "2026-10-06" in said
    from agent.reply_templates_i18n import weekday_word

    assert weekday_word("2026-10-06", lang) in said


def test_28_the_callers_own_day_word_is_repeated_back_to_them():
    from agent.reply_templates import date_check_prompt

    assert "কাল" in date_check_prompt("2026-10-06", "কাল", "bn")
    assert "tomorrow" in date_check_prompt("2026-10-06", "tomorrow", "en")


def test_28_two_readings_of_the_same_words_are_both_offered():
    """Never the likelier one: the caller settles it."""
    from agent.reply_templates import date_ask_prompt

    for lang in ("bn", "hi", "en"):
        said = date_ask_prompt(("2026-10-06", "2026-10-13"), lang)
        assert "2026-10-06" in said and "2026-10-13" in said and said.rstrip().endswith("?")


def test_28_the_code_reading_and_the_models_reading_are_cross_checked():
    """The owner excluded prompt changes, so the model still resolves the date itself. This
    compares it against what the language's own cue table makes of the same words."""
    import datetime

    from agent import date_check

    monday = datetime.date(2026, 10, 5)
    agree = date_check.check("কাল আসব", "2026-10-06", "bn", monday)
    assert agree.action == date_check.CONFIRM and agree.date == "2026-10-06" and agree.said == "কাল"

    differ = date_check.check("কাল আসব", "2026-10-09", "bn", monday)
    assert differ.action == date_check.ASK and set(differ.candidates) == {"2026-10-06", "2026-10-09"}

    assert date_check.check("ডাক্তার সেন আছেন?", None, "bn", monday).action == date_check.ABSENT

    # A date only the model resolved is still READ BACK -- that is the whole criterion.
    model_only = date_check.check("কল आऊंगा", "2026-10-06", "hi", monday)
    assert model_only.action == date_check.CONFIRM and model_only.date == "2026-10-06"


def test_28_hindis_deliberately_minimal_day_table_is_left_alone():
    """merge_report.md section 4.10 records this restraint as correct: both कल and परसों are
    genuinely ambiguous in Hindi. The cross-check above gets the readback without widening it."""
    from agent import fast_path_cues as cues

    assert set(cues.table_for("hi").relative_days) == {"आज"}


@pytest.mark.skip(reason=POD + " -- how often the real Qwen's date agrees with the caller's words")
def test_28_the_models_resolved_dates_are_correct_on_real_utterances():
    ...


# ======================================================================== 29. Patient name is misheard
# AC: every captured name is read back before commit; a rejection opens spelling or keypad entry
# rather than repeating the same prompt; a name captured below the confidence floor always
# triggers readback even if the caller did not ask.


def test_29_the_captured_name_is_always_read_back_before_commit():
    from agent.reply_templates import booking_confirmation_readback

    said = booking_confirmation_readback(
        {"doctor_name": "Sen", "date": "2026-10-15", "time_slot": "10:15",
         "patient_name": "Rahul Das", "phone": "9876543210"}, "book_appointment", "bn")
    assert "Rahul Das" in said


def test_29_spelling_entry_exists_and_assembles_across_turns():
    from agent.booking_flow import merge_spelling, new_state, try_assemble_spelling

    assert try_assemble_spelling(["r", "a", "v", "i"]) == "ravi"
    st = new_state("book_appointment")
    merge_spelling(st, ["r", "a"])
    assert merge_spelling(st, ["v", "i"]) == "ravi", "a long name spelled over two turns is lost"


def test_29_a_spelled_name_is_read_back():
    from agent.reply_templates import spelling_readback

    assert "RAVI" in spelling_readback("ravi", "bn").upper() or "ravi" in spelling_readback("ravi", "bn")


def test_29_an_uncertain_turn_never_becomes_a_fact():
    """The floor exists and withholds the lookup -- the half of AC3 this tree does have.
    Two decoders that disagree (rnnt, 0.3) refuse the lookup; one decoder that cannot be checked
    at all (ctc_fallback) asks the entity back first."""
    from agent.confidence_gate import needs_entity_readback, should_withhold_factual_answer

    assert should_withhold_factual_answer("test_rate", 0.3, "rnnt")
    assert needs_entity_readback("test_rate", "ctc_fallback", 0.0)
    # ...and a clean turn is not second-guessed.
    assert not should_withhold_factual_answer("test_rate", 0.95, "rnnt")


def test_29_a_name_below_the_confidence_floor_triggers_its_own_readback():
    from agent.entity_confirmation import confirm_question, entity_to_confirm

    assert entity_to_confirm("book_appointment", {"patient_name": "Rahul Das"}) == (
        "patient_name", "Rahul Das")
    assert "_absorb_phone_digits" in _source("main.py")  # the booking branch was reached
    src = _source("main.py")
    assert "awaiting_name_confirm" in src and "confidence != VERIFIED" in src
    for lang in ("bn", "hi", "en"):
        assert "Rahul Das" in confirm_question("patient_name", "Rahul Das", lang)


def test_29_rejecting_the_name_opens_spelling_rather_than_repeating_the_prompt():
    src = _source("main.py")
    handler = src[src.index("async def _continue_name_confirm"):][:2000]
    assert 'answer == "no"' in handler and "spelling_prompt" in handler
    assert 'st.slots.pop("patient_name", None)' in handler, "the misheard name must be dropped"


def test_29_a_spelled_name_is_not_then_queried_again():
    """A name spelled letter by letter is already certain; asking "do you mean X?" after it would
    be the agent doubting what it just had dictated."""
    src = _source("main.py")
    spelling = src[src.index("merge_spelling(st, slots"):][:600]
    assert "st.name_confirmed = True" in spelling


@pytest.mark.skip(reason="no telephony in this stack: no SIP, no DTMF, so keypad entry cannot "
                         "exist yet (HANDOVER.md: browser WebSocket only). Spelling is the "
                         "buildable half of this AC.")
def test_29_keypad_entry_is_offered():
    ...


# ============================================================ 30. Caller cannot give a contact number
# AC: a contact number different from the calling line is accepted, captured digit by digit, read
# back and confirmed before use; a refusal to give any number still completes the booking with a
# stated consequence: no written confirmation.


def test_30_a_different_contact_number_is_accepted_and_wins():
    from agent.booking_flow import effective_phone, merge_slots, new_state

    st = new_state("book_appointment")
    merge_slots(st, {"phone": "9000000000", "contact_phone": "9876543210"})
    assert effective_phone(st) == "9876543210", "the number the caller asked for must win"


def test_30_the_number_the_confirmation_goes_to_is_the_one_read_back():
    from agent.reply_templates import booking_confirmation_readback

    said = booking_confirmation_readback(
        {"doctor_name": "Sen", "date": "2026-10-15", "time_slot": "10:15",
         "patient_name": "Rahul Das", "phone": "9000000000", "contact_phone": "9876543210"},
        "book_appointment", "bn")
    assert "9876543210" in said and "9000000000" not in said


def test_30_a_refusal_still_completes_the_booking():
    from agent.booking_flow import effective_phone, merge_slots, new_state

    st = new_state("book_appointment")
    merge_slots(st, {"doctor_name": "Sen", "date": "2026-10-15", "time_slot": "10:15",
                     "patient_name": "Rahul Das"})
    assert effective_phone(st) == "not_provided"


def test_30_the_consequence_of_refusing_is_stated_in_every_language():
    from agent.phrases import phrase

    for lang in ("bn", "hi", "en"):
        said = phrase("no_confirmation_number", lang)
        assert said and said.strip(), f"no stated consequence in {lang}"
    assert "written confirmation" in phrase("no_confirmation_number", "en")


def test_30_no_message_is_faked_for_a_caller_who_gave_no_number(clinic_client):
    import booking_service as bs
    import db as db_mod

    with db_mod.SessionLocal() as s:
        out = bs.queue_sms(s, bs.NOT_PROVIDED_PHONE, "booking_confirmed", "x", "KCD-1")
    assert out == {"queued": False, "reason": "no_phone_on_file"}


def test_30_the_number_is_captured_digit_by_digit():
    from agent.booking_flow import merge_digits, new_state, phone_digits_so_far

    st = new_state("book_appointment")
    assert merge_digits(st, "987") is None and phone_digits_so_far(st) == "987"
    assert merge_digits(st, "6543") is None and phone_digits_so_far(st) == "9876543"
    assert merge_digits(st, "210") == "9876543210", "the completed number is returned"
    assert phone_digits_so_far(st) == "", "and the buffer is cleared for the next one"


def test_30_a_fragment_never_becomes_the_number():
    """What this fixes: the model reports the digits it heard this turn, and three of them used
    to be stored as THE contact number and sail into the readback."""
    from agent.booking_flow import merge_slots, missing_required, new_state, normalise_phone

    assert normalise_phone("987") is None
    assert normalise_phone("9876543210") == "9876543210"
    assert normalise_phone("09876543210") == "9876543210"
    assert normalise_phone("919876543210") == "9876543210"
    # Over-long is refused outright rather than trimmed to its last ten digits: that is how a
    # time spoken after a number turns into a different person's phone.
    assert normalise_phone("98765432101030") is None

    st = new_state("book_appointment")
    merge_slots(st, {"doctor_name": "Sen", "date": "2026-10-06", "time_slot": "10:15",
                     "patient_name": "Rahul Das"})
    assert "phone" in missing_required(st)


@pytest.mark.parametrize("lang,marker", [
    ("bn", "বাকি সংখ্যাগুলো"), ("hi", "बाकी अंक"), ("en", "rest of the number")])
def test_30_the_caller_hears_what_was_taken_down_so_far(lang, marker):
    from agent.reply_templates import phone_continue_prompt

    said = phone_continue_prompt("9876", lang)
    assert marker in said and "9 8 7 6" in said, "the digits are read back one by one"


def test_30_a_partial_number_continues_rather_than_restarting():
    src = _source("main.py")
    assert "phone_continue_prompt" in src and "_absorb_phone_digits" in src
