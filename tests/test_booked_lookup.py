"""story: "Caller asks what they have booked"

Covers the four parts of the change: the deterministic intent net, the semantic-cache exclusion,
the gated lookup, and the resend offer a bare yes can accept.

clinic-api runs in-process over HTTP on a throwaway SQLite file. No GPU, no model, no audio, no
network -- the MODEL half of the intent decision cannot be tested here and is measured instead by
tools/lookup_intent_eval.py (see that file's report).

    python -m pytest tests/test_booked_lookup.py -v
"""

from __future__ import annotations

import datetime
import os
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLINIC_API_DIR = os.path.join(REPO_ROOT, "clinic-api")
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent.lookup_request import asks_what_is_booked, wants_new_booking  # noqa: E402
from agent.reply_templates import booked_subject, lookup_reply  # noqa: E402

LANGS = ("bn", "hi", "en")

_CLINIC_MODULES = ("main", "db", "models", "seed", "booking_service", "booking_migrate",
                   "enquiry_migrate", "enquiry_service", "i18n_content", "patient_context")


def _evict_clinic_modules() -> None:
    """Drop every module that resolved out of clinic-api/, by file. `main` is ambiguous in this
    repo and `db`/`models` bind their engine AT IMPORT, so a leftover makes the next test file
    reuse this one's deleted database."""
    for name in list(sys.modules):
        if name in _CLINIC_MODULES:
            path = getattr(sys.modules[name], "__file__", "") or ""
            if os.path.dirname(os.path.abspath(path)) == CLINIC_API_DIR:
                del sys.modules[name]


@pytest.fixture()
def clinic(monkeypatch):
    import types

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("CLINIC_DB_PATH", db_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    saved_path = list(sys.path)
    sys.path.insert(0, CLINIC_API_DIR)
    _evict_clinic_modules()

    from fastapi.testclient import TestClient

    import booking_service as bs
    import db as cdb
    import main as clinic_main
    import models as cmo

    api = types.SimpleNamespace(db=cdb, models=cmo, bs=bs)
    try:
        with TestClient(clinic_main.app) as c:
            yield c, api
    finally:
        _evict_clinic_modules()
        sys.path[:] = saved_path
        try:
            os.remove(db_path)
        except OSError:
            pass


# ===================================================== 1. the deterministic intent net

EXISTING = [
    # Bengali
    "আমার অ্যাপয়েন্টমেন্ট কী আছে?",
    "আমার কী বুক করা আছে?",
    "আমার অ্যাপয়েন্টমেন্টটা কবে?",
    "আমার একটা অ্যাপয়েন্টমেন্ট আছে?",
    "আমি কবে অ্যাপয়েন্টমেন্ট করেছিলাম?",
    "আমার অ্যাপয়েন্টমেন্ট কখন ছিল?",
    # Hindi
    "मेरी अपॉइंटमेंट क्या है?",
    "मेरी अपॉइंटमेंट कब है?",
    "मेरी अपॉइंटमेंट कब थी?",
    "मेरी बुकिंग बुक है क्या?",
    # English
    "what have I got booked?",
    "do I have anything booked?",
    "when is my appointment?",
    "what's my appointment?",
    "did I book an appointment?",
    # code-mixed / romanised
    "amar appointment ta kobe?",
    "amar appointment ache?",
    "amar booking kobe?",
    "meri appointment kab hai?",
]

NEW_BOOKING = [
    "আমার একটা appointment চাই",
    "আমি একটা অ্যাপয়েন্টমেন্ট করতে চাই",
    "অ্যাপয়েন্টমেন্ট চাই",
    "আমার জন্য একটা অ্যাপয়েন্টমেন্ট চাই",
    "मुझे अपॉइंटमेंट चाहिए",
    "मेरी अपॉइंटमेंट बुक करना है",
    "अपॉइंटमेंट चाहिए",
    "I want an appointment",
    "I need an appointment",
    "can I book an appointment?",
    "book me an appointment",
    "I'd like an appointment",
    "amar ekta appointment chai",
]

# Neither: the net must stay out of the way of every other turn.
OTHER = [
    "হ্যাঁ",
    "ডাক্তার সেন কি আছেন?",
    "CBC টেস্টের রেট কত?",
    "নমস্কার",
    "আমার নাম রাহুল দাস",
    "9876543210",
    "मुझे शिकायत करनी है",
]


@pytest.mark.parametrize("text", EXISTING)
def test_an_existing_booking_question_is_caught(text):
    assert asks_what_is_booked(text), text


@pytest.mark.parametrize("text", NEW_BOOKING)
def test_a_new_booking_request_is_never_caught(text):
    """The critical direction: read as a lookup, a caller asking to BOOK would be told what they
    already have instead; read the other way, they get a second appointment."""
    assert not asks_what_is_booked(text), text
    assert wants_new_booking(text), text


@pytest.mark.parametrize("text", OTHER)
def test_an_unrelated_turn_falls_through_to_the_model(text):
    assert not asks_what_is_booked(text), text


def test_a_possessive_with_a_request_in_it_is_still_a_request():
    """"আমার জন্য একটা অ্যাপয়েন্টমেন্ট চাই" has BOTH a possessive and a booking noun. The
    want-verb has to win, which is why it is checked first."""
    assert not asks_what_is_booked("আমার জন্য একটা অ্যাপয়েন্টমেন্ট চাই")


def test_an_ambiguous_turn_is_left_to_the_model():
    """No booking noun, or no existing-booking marker: the net abstains rather than guessing, and
    the turn takes the model path it took before this story."""
    for text in ("কবে?", "appointment", "আমার", "what about tomorrow"):
        assert not asks_what_is_booked(text), text


def test_the_net_does_not_fire_inside_an_unrelated_word():
    """Indic cues match at a word START only -- the same rule agent/earliest_request.py uses."""
    assert not asks_what_is_booked("আমারুদ্দিন নামে কেউ আছে?")


# =============================================== 2. the semantic-cache exclusion

def test_a_lookup_classification_is_never_reused_fuzzily():
    from agent.semantic_cache import SemanticCache

    cache = SemanticCache()
    assert cache._is_l2_eligible({"intent": "lookup_booking", "slots": {}}) is False


def test_a_book_appointment_classification_stays_l2_eligible():
    """The exclusion must be narrow: unrelated intents keep the fuzzy reuse they had, so this
    change cannot quietly cost latency everywhere else."""
    from agent.semantic_cache import SemanticCache

    cache = SemanticCache()
    assert cache._is_l2_eligible({"intent": "book_appointment", "slots": {}}) is True
    assert cache._is_l2_eligible({"intent": "test_rate", "slots": {"test_name": "CBC"}}) is True


def test_the_pii_rule_is_untouched():
    from agent.semantic_cache import SemanticCache

    cache = SemanticCache()
    assert cache._is_l2_eligible({"intent": "test_rate", "slots": {"phone": "9876543210"}}) is False


# ================================================= 3. what the lookup returns

def _sitting_day(c, doctor_name: str) -> str:
    for i in range(2, 16):
        d = (datetime.date.today() + datetime.timedelta(days=i)).isoformat()
        if c.get("/api/v1/doctors/availability", params={"name": doctor_name, "date": d}).json().get("available"):
            return d
    pytest.fail("no sitting day in the next two weeks of seeded data")


def _book(c, phone: str = "9876543210", name: str = "Rahul Das") -> dict:
    doctor = c.get("/api/v1/catalogue").json()["doctors"][0]["name"]
    date = _sitting_day(c, doctor)
    probe = c.post("/api/v1/bookings/hold",
                   json={"doctor_name": doctor, "date": date, "time_slot": "__probe__"}).json()
    slot = probe["valid_slots"][0]
    hold = c.post("/api/v1/bookings/hold",
                  json={"doctor_name": doctor, "date": date, "time_slot": slot}).json()
    assert hold["success"], hold
    confirm = c.post("/api/v1/bookings/confirm", json={
        "hold_token": hold["hold_token"], "doctor_id": hold["doctor_id"], "date": date,
        "time_slot": slot, "patient_name": name, "phone": phone, "caller_phone": phone,
    }).json()
    assert confirm["success"], confirm
    return {**confirm, "date": date, "time_slot": slot, "doctor_name": doctor}


def test_an_appointment_is_returned_with_its_doctor_date_and_time(clinic):
    c, _ = clinic
    booked = _book(c)
    found = c.get("/api/v1/bookings/lookup", params={"phone": "9876543210"}).json()["bookings"]
    assert len(found) == 1
    b = found[0]
    assert b["kind"] == "appointment"
    assert b["doctor_name"] == booked["doctor_name"]
    assert b["date"] == booked["date"] and b["time_slot"] == booked["time_slot"]


def test_a_test_booking_is_returned_with_its_tests_and_no_time(clinic):
    """A lab test is a TestBooking row with a DATE and no time. Before this story the lookup
    queried appointments only, so this caller was told they had nothing booked."""
    c, _ = clinic
    date = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    r = c.post("/api/v1/bookings/tests", json={
        "test_names": ["Complete Blood Count (CBC)", "Lipid Profile"], "date": date,
        "patient_name": "Rahul Das", "phone": "9876500001", "caller_phone": "9876500001",
    }).json()
    assert r.get("success"), r
    found = c.get("/api/v1/bookings/lookup", params={"phone": "9876500001"}).json()["bookings"]
    assert len(found) == 1, found
    b = found[0]
    assert b["kind"] == "test"
    assert sorted(b["test_names"]) == ["Complete Blood Count (CBC)", "Lipid Profile"]
    assert b["date"] == date
    assert b["time_slot"] is None


def test_several_tests_in_one_call_are_one_booking_not_three(clinic):
    c, _ = clinic
    date = (datetime.date.today() + datetime.timedelta(days=4)).isoformat()
    c.post("/api/v1/bookings/tests", json={
        "test_names": ["Complete Blood Count (CBC)", "Lipid Profile", "Uric Acid"], "date": date,
        "patient_name": "Rahul Das", "phone": "9876500002", "caller_phone": "9876500002",
    })
    found = c.get("/api/v1/bookings/lookup", params={"phone": "9876500002"}).json()["bookings"]
    assert len(found) == 1 and len(found[0]["test_names"]) == 3


def test_the_next_upcoming_booking_comes_first(clinic):
    """Earliest first, so the reply's bookings[0] IS the next one."""
    c, cm = clinic
    booked = _book(c, phone="9876500003")
    sooner = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    assert sooner < booked["date"]
    c.post("/api/v1/bookings/tests", json={
        "test_names": ["Uric Acid"], "date": sooner, "patient_name": "Rahul Das",
        "phone": "9876500003", "caller_phone": "9876500003",
    })
    found = c.get("/api/v1/bookings/lookup", params={"phone": "9876500003"}).json()["bookings"]
    assert [b["date"] for b in found] == sorted(b["date"] for b in found)
    assert found[0]["date"] == sooner and found[0]["kind"] == "test"


def test_a_past_booking_is_not_returned_by_default(clinic):
    c, cm = clinic
    booked = _book(c, phone="9876500004")
    db = cm.db.SessionLocal()
    try:
        appt = db.query(cm.models.Appointment).filter_by(confirmation_id=booked["confirmation_id"]).first()
        appt.date = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
        db.commit()
    finally:
        db.close()
    found = c.get("/api/v1/bookings/lookup", params={"phone": "9876500004"}).json()["bookings"]
    assert found == []


# ======================================================= security

def test_an_unverified_caller_is_refused_by_the_service(clinic):
    """A phone number is a CLAIM. Asked AS someone else, the service discloses nothing -- the
    refusal is silence about the row, not a distinguishable error that would confirm it exists.

    Called at the service layer on purpose: see the endpoint test below for why the HTTP route
    cannot express "who is asking" at all."""
    c, cm = clinic
    _book(c, phone="9876500005", name="Rahul Das")
    db = cm.db.SessionLocal()
    try:
        mine = cm.bs.lookup_bookings(db, phone="9876500005", caller_phone="9876500005")
        stranger = cm.bs.lookup_bookings(db, phone="9876500005", caller_phone="9000000999")
    finally:
        db.close()
    assert len(mine) == 1
    assert stranger == []


@pytest.mark.xfail(strict=True, reason=(
    "GAP, pre-existing and the reason the orchestrator gate is load-bearing: GET "
    "/api/v1/bookings/lookup accepts phone, confirmation_id and name -- but NOT caller_phone. "
    "lookup_bookings therefore defaults `asking_as` to the phone being searched FOR, and "
    "authorize_disclosure checks the search against itself, so over HTTP anyone who speaks a "
    "number hears that number's bookings. Only the security questions in main.py stop it; "
    "exposing caller_phone here needs a real caller ID this stack does not have (no SIP, no "
    "ANI), so the parameter alone would be theatre."))
def test_the_endpoint_can_say_who_is_asking(clinic):
    c, _ = clinic
    _book(c, phone="9876500008", name="Rahul Das")
    stranger = c.get("/api/v1/bookings/lookup",
                     params={"phone": "9876500008", "caller_phone": "9000000999"}).json()
    assert stranger["bookings"] == []


def test_the_patient_themselves_can_retrieve_it(clinic):
    c, _ = clinic
    _book(c, phone="9876500006")
    mine = c.get("/api/v1/bookings/lookup",
                 params={"phone": "9876500006", "caller_phone": "9876500006"}).json()
    assert len(mine["bookings"]) == 1


def test_a_name_alone_discloses_nothing(clinic):
    c, _ = clinic
    _book(c, phone="9876500007", name="Rahul Das")
    assert c.get("/api/v1/bookings/lookup", params={"name": "Rahul Das"}).json()["bookings"] == []


def test_the_orchestrator_gates_the_phone_path_before_any_lookup():
    """The gate is in the dispatch, ahead of the tool call -- the point is never to RUN the
    lookup for an unverified caller, not to withhold its result afterwards."""
    with open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8") as f:
        body = f.read().split("async def _answer_booking_lookup(", 1)[1].split("\nasync def ")[0]
    assert body.index("history_gate(") < body.index("_tools.lookup_bookings(")
    assert "_start_security_check(" in body
    assert "pending_lookup" in body


# ========================================================= the spoken reply

def test_the_reply_states_the_doctor_for_an_appointment():
    b = {"kind": "appointment", "confirmation_id": "KCD-1", "doctor_name": "Dr. S. Mukherjee",
         "doctor_name_bn": "মুখার্জী", "doctor_name_hi": "मुखर्जी",
         "date": "2026-10-12", "time_slot": "10:15"}
    assert "মুখার্জী" in lookup_reply([b], "bn")
    assert "मुखर्जी" in lookup_reply([b], "hi")
    assert "Mukherjee" in lookup_reply([b], "en")


def test_the_reply_states_the_tests_for_a_test_booking():
    b = {"kind": "test", "confirmation_id": "KCD-2", "test_names": ["CBC", "Lipid Profile"],
         "date": "2026-10-10", "time_slot": None}
    for lang in LANGS:
        said = lookup_reply([b], lang)
        assert "CBC" in said and "Lipid Profile" in said, (lang, said)


def test_the_time_is_spoken_only_when_the_record_has_one():
    timed = {"kind": "appointment", "confirmation_id": "KCD-1", "doctor_name": "Dr. Sen",
             "date": "2026-10-12", "time_slot": "10:15"}
    untimed = {"kind": "test", "confirmation_id": "KCD-2", "test_names": ["CBC"],
               "date": "2026-10-10", "time_slot": None}
    for lang in LANGS:
        assert "10:15" in lookup_reply([timed], lang)
        said = lookup_reply([untimed], lang)
        assert ":" not in said, (lang, said)  # no clock, and no field label either


def test_the_date_is_the_records_own_date():
    b = {"kind": "appointment", "confirmation_id": "KCD-1", "doctor_name": "Dr. Sen",
         "date": "2026-10-12", "time_slot": "10:15"}
    for lang in LANGS:
        assert "2026-10-12" in lookup_reply([b], lang)


def test_no_branch_is_ever_spoken():
    """There is no appointment-level branch in this schema (reported, not fabricated). The reply
    must not name one -- and must not reach for PatientPreference.branch as a substitute, which
    is where the patient PREFERS to go, not where this booking is."""
    b = {"kind": "appointment", "confirmation_id": "KCD-1", "doctor_name": "Dr. Sen",
         "date": "2026-10-12", "time_slot": "10:15", "branch": "Salt Lake"}
    for lang in LANGS:
        said = lookup_reply([b], lang)
        assert "Salt Lake" not in said, (lang, said)
        assert "শাখা" not in said and "शाखा" not in said and "branch" not in said.lower()


def test_the_resend_is_offered_in_every_language():
    b = {"kind": "appointment", "confirmation_id": "KCD-1", "doctor_name": "Dr. Sen",
         "date": "2026-10-12", "time_slot": "10:15"}
    for lang in LANGS:
        said = lookup_reply([b], lang)
        assert said.rstrip().endswith("?"), (lang, said)


def test_nothing_found_says_so_without_inventing_a_booking():
    for lang in LANGS:
        said = lookup_reply([], lang)
        assert said and "KCD" not in said


def test_the_subject_falls_back_without_naming_a_doctor_it_does_not_have():
    assert booked_subject({"kind": "appointment"}, "en") == "with the doctor"
    assert booked_subject({"kind": "test", "test_names": []}, "en") == "the test"


# ============================================================ the resend offer

def test_a_bare_yes_accepts_the_offer_through_the_existing_path():
    """No second resend mechanism: the yes calls _tools.resend_confirmation, the same one the
    resend_confirmation intent uses."""
    with open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8") as f:
        src = f.read()
    body = src.split("async def _continue_resend_offer(", 1)[1].split("\nasync def ")[0]
    assert "classify_yes_no(" in body
    assert "_tools.resend_confirmation(" in body
    assert src.count("_tools.resend_confirmation(") == 2  # the intent branch, and this offer


def test_the_offer_lapses_rather_than_trapping_the_caller():
    """Anything that is not a yes or a no returns False, so the turn is handled as a fresh one
    instead of the caller being asked the same question again."""
    with open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8") as f:
        body = f.read().split("async def _continue_resend_offer(", 1)[1].split("\nasync def ")[0]
    statements = [ln for ln in body.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert statements[-1].strip() == "return False"


def test_the_offer_is_remembered_so_the_yes_knows_what_to_resend():
    with open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8") as f:
        body = f.read().split("async def _answer_booking_lookup(", 1)[1].split("\nasync def ")[0]
    assert "session.pending_resend = bookings[0][\"confirmation_id\"]" in body


# ======================================= end to end, through the real dispatcher
#
# The harness below is tests/test_orchestrator_booking_flow.py's: pod-only libraries stubbed, ASR
# and the extractor faked, and EVERYTHING ELSE REAL -- including the deterministic guard, the
# verification gate and the handler. `env` replaces _resolve_intent with a fake, which is exactly
# what makes these tests meaningful: the guard runs BEFORE it, so a hit proves the model was
# never consulted.

from test_orchestrator_booking_flow import FakeTools as _BookingTools  # noqa: E402
from test_orchestrator_booking_flow import env, m  # noqa: E402, F401  (pytest fixtures)

BOOKED = {
    "kind": "appointment",
    "confirmation_id": "KCD-20261012-AB12",
    "doctor_name": "Dr. S. Mukherjee",
    "doctor_name_bn": "মুখার্জী",
    "doctor_name_hi": "मुखर्जी",
    "date": "2026-10-12",
    "time_slot": "10:15",
}


class _LookupTools(_BookingTools):
    """The booking-flow fake, plus the two calls THIS flow makes. Records them, so "was the
    lookup even run?" is a fact rather than an inference."""

    def __init__(self):
        super().__init__()
        self.lookups, self.resends = [], []

    async def lookup_bookings(self, phone=None, confirmation_id=None):
        self.lookups.append((phone, confirmation_id))
        return {"found": True, "bookings": [BOOKED]}

    async def resend_confirmation(self, confirmation_id):
        self.resends.append(confirmation_id)
        return {"success": True, "sent_to_last4": "3210"}


@pytest.fixture()
def lookup_env(m, env, monkeypatch):
    tools = _LookupTools()
    monkeypatch.setattr(m, "_tools", tools)
    env.tools = tools
    return env


@pytest.mark.asyncio
async def test_an_unverified_caller_hears_nothing_about_a_booking(m, lookup_env):
    """No lookup is RUN and nothing about a booking is spoken. With no number on file the caller
    is asked for one first -- the same order the history flow uses, since a record has to be
    located before anyone can be verified against it."""
    said = await lookup_env.say("what have I got booked?", "unclear")
    assert lookup_env.tools.lookups == []
    joined = " ".join(said)
    assert "KCD-20261012-AB12" not in joined and "Mukherjee" not in joined
    assert joined, "the caller was told nothing at all"


@pytest.mark.asyncio
async def test_a_claimed_but_unverified_caller_is_sent_to_the_security_questions(m, lookup_env):
    """A number the caller SAID is a claim. It is enough to look a record up, and not enough to
    be told what is in it: the questions are asked and the lookup still has not run."""
    session = lookup_env.session
    session.identity.claim("42", "9876543210")
    said = await lookup_env.say("what have I got booked?", "unclear")
    assert lookup_env.tools.lookups == []
    assert "KCD-20261012-AB12" not in " ".join(said)
    assert session.pending_lookup is not None


@pytest.mark.asyncio
async def test_the_question_is_answered_after_verification_without_being_asked_again(m, lookup_env):
    """The caller passes the check, and the question they already asked is answered -- the point
    of remembering it rather than making them repeat themselves."""
    session = lookup_env.session
    session.identity.claim("42", "9876543210")
    session.identity.verify("security_questions", "42")
    said = await lookup_env.say("what have I got booked?", "unclear")
    assert lookup_env.tools.lookups, "the lookup never ran for a verified caller"
    joined = " ".join(said)
    assert "KCD-20261012-AB12" in joined
    assert session.pending_resend == "KCD-20261012-AB12"


@pytest.mark.asyncio
async def test_a_bare_yes_resends_through_the_existing_path(m, lookup_env):
    session = lookup_env.session
    session.identity.claim("42", "9876543210")
    session.identity.verify("security_questions", "42")
    await lookup_env.say("what have I got booked?", "unclear")
    said = await lookup_env.say("yes", "unclear")
    assert lookup_env.tools.resends == ["KCD-20261012-AB12"]
    assert session.pending_resend is None
    assert " ".join(said)


@pytest.mark.asyncio
async def test_a_no_declines_without_resending(m, lookup_env):
    session = lookup_env.session
    session.identity.claim("42", "9876543210")
    session.identity.verify("security_questions", "42")
    await lookup_env.say("what have I got booked?", "unclear")
    await lookup_env.say("no", "unclear")
    assert lookup_env.tools.resends == []
    assert session.pending_resend is None


@pytest.mark.asyncio
async def test_an_unrelated_turn_after_the_offer_is_handled_as_itself(m, lookup_env):
    """The offer lapses rather than trapping the caller: a different question is answered, and
    nothing is resent."""
    session = lookup_env.session
    session.identity.claim("42", "9876543210")
    session.identity.verify("security_questions", "42")
    await lookup_env.say("what have I got booked?", "unclear")
    await lookup_env.say("what does a CBC cost?", "test_rate", {"test_name": "CBC"})
    assert lookup_env.tools.resends == []
    assert session.pending_resend is None


@pytest.mark.asyncio
async def test_a_new_booking_request_still_opens_the_booking_flow(m, lookup_env):
    """The guard must not steal a booking. "I want an appointment" has to reach the model path
    and start collecting, exactly as before this story."""
    said = await lookup_env.say("I want an appointment", "book_appointment", {})
    assert lookup_env.tools.lookups == []
    assert lookup_env.session.booking is not None
    assert " ".join(said)


@pytest.mark.asyncio
async def test_the_guard_does_not_fire_while_a_booking_is_in_progress(m, lookup_env):
    """Same protection the lay-term guard has: a possessive inside an answer to a booking
    question must not hijack the booking that is already running."""
    await lookup_env.say("I want an appointment", "book_appointment", {"doctor_name": "Sen"})
    assert lookup_env.session.booking is not None
    await lookup_env.say("my appointment is for my mother", "unclear")
    assert lookup_env.tools.lookups == []
