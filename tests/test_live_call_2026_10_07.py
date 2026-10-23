"""The live Bengali call of 2026-10-07, turn by turn, through the REAL orchestrator (main_pcm._dispatch_turn).

    python -m pytest tests/test_live_call_2026_10_07.py -v

What the caller heard, and what each test now pins instead:

  1. "ডক্টর দেবাশিস দাস" switched the call to Hindi           -> tests/test_lang_select.py (the routing rule itself)
  2. "কোন কোন ডিপার্টমেন্ট আছে" was answered "what's the problem?" -> the departments are listed
  3. "অন্য কোন কোন সময় বসেন উনি" -> "'বসেন' নামে ডাক্তার খুঁজে পাইনি"   -> answered about the doctor being discussed
     "... উনি" -> "'উনি' নামে কোনো ডাক্তার আমাদের এখানে নেই"         -> the same
  plus the requirements that came with it: "ডাক্তার দেখাবো" asks WHICH doctor (never "no doctor called 'doctor'"), a
  booking settles the doctor before the day and time, and two doctors named ("ghosh na ki jeno guha") are both offered.

Same approach as tests/test_orchestrator_booking_flow.py, whose harness this reuses: the extractor, ASR and clinic tools
are fakes; the dispatch, the order of the checks and every reply template are real. The fake clinic below answers the
two lookups this flow adds (/doctors/mentioned, /departments) the way the real one does for these sentences --
tests/test_doctor_mention.py checks the real one.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_orchestrator_booking_flow import env, m, text_of  # noqa: E402,F401  (the harness)

DEPARTMENTS_BN = ["কার্ডিওলজি", "অর্থোপেডিক", "ইএনটি"]

# What clinic-api/doctor_mention.py answers for the sentences used below (verified in tests/test_doctor_mention.py).
GHOSH_OR_GUHA = {
    "found": False,
    "count": 2,
    "ambiguous": True,
    "doctors": [
        {"name": "Dr. P. Ghosh", "full_name": "Dr. Prabir Ghosh", "full_name_bn": "প্রবীর ঘোষ", "exact": True},
        {"name": "Dr. S. Guha", "full_name": "Dr. Sanjay Guha", "full_name_bn": "সঞ্জয় গুহ", "exact": True},
    ],
    "did_you_mean": ["Dr. Prabir Ghosh", "Dr. Sanjay Guha"],
    "did_you_mean_bn": ["প্রবীর ঘোষ", "সঞ্জয় গুহ"],
}
DAS = {
    "found": True,
    "count": 1,
    "doctor_name": "Dr. D. Das",
    "doctors": [{"name": "Dr. D. Das", "full_name_bn": "দেবাশিস দাস", "exact": True}],
}
NOBODY = {"found": False, "count": 0, "doctors": []}


@pytest.fixture
def bn(m, env):
    """The harness, in Bengali, with a clinic that also answers "which doctors does this sentence name?" and
    "which departments are there?". Every enquiry answer is recorded with the slots it was asked with."""
    env.state["lang"] = "bn"
    env.session.disclosed_langs.add("bn")
    mentions = {}

    async def doctors_mentioned(text):
        return mentions.get(text, NOBODY)

    async def list_departments():
        return {"found": True, "departments": [{"name": n, "name_bn": n} for n in DEPARTMENTS_BN]}

    async def doctors_by_department(query):
        return {"found": False, "query": query}

    async def route_department(query, lang="bn"):
        return {"matched": False}

    async def list_doctors():
        # More doctors than are ever read out one by one, so the reply offers the departments instead.
        rows = [
            {"name": f"Dr. X{i}", "full_name_bn": f"ডাক্তার {i}", "department": d, "department_bn": d}
            for i, d in enumerate(DEPARTMENTS_BN * 4)
        ]
        return {"found": True, "count": len(rows), "doctors": rows}

    env.tools.doctors_mentioned = doctors_mentioned
    env.tools.list_departments = list_departments
    env.tools.doctors_by_department = doctors_by_department
    env.tools.route_department = route_department
    env.tools.list_doctors = list_doctors
    env.mentions = mentions
    env.asked = []

    real_answer = m._answer_enquiry_intent

    required = {"doctor_availability": "doctor_name", "department_query": "symptom_description"}

    async def answer(intent, slots, lang):
        # The real one's contract: None when the intent's required slot is missing (the caller decides what to ask).
        if intent in required and not slots.get(required[intent]):
            return None
        env.asked.append((intent, dict(slots)))
        return "উত্তর।"

    env.real_answer = real_answer
    m._answer_enquiry_intent = answer
    yield env
    m._answer_enquiry_intent = real_answer


# ---- "ডাক্তার দেখাবো": which doctor, first ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("said,slot", [("ডাক্তার দেখাবো", "ডাক্তার"), ("একজন ডক্টর দেখাব", "একজন ডক্টর")])
async def test_seeing_a_doctor_with_no_name_asks_which_doctor_never_no_doctor_called_doctor(bn, said, slot):
    out = text_of(await bn.say(said, "doctor_availability", {"doctor_name": slot}))
    assert "কোন ডাক্তার দেখাতে চান" in out
    assert "নামে" not in out  # never "no doctor called '...'"
    assert "কার্ডিওলজি" in out  # the departments, for a caller who knows no name
    assert bn.asked == []  # nothing was looked up under a non-name
    assert bn.session.booking is not None and bn.session.booking.action == "book_appointment"


@pytest.mark.asyncio
async def test_a_booking_with_no_doctor_asks_the_doctor_before_the_day_and_time(bn):
    out = text_of(await bn.say("কাল সকালে অ্যাপয়েন্টমেন্ট চাই", "book_appointment", {"date": "2026-10-08"}))
    assert "কোন ডাক্তার দেখাতে চান" in out
    assert "কোন দিন" not in out and "কোন সময়" not in out
    assert bn.session.booking.slots.get("date") == "2026-10-08"  # the day already given is kept


# ---- two doctors named: both offered, the caller chooses ----------------------------------------------------------


@pytest.mark.asyncio
async def test_two_doctors_named_are_both_offered_and_the_answer_settles_it(bn):
    said = "ডক্টর ঘোষ না কি যেন ডক্টর গুহ দেখাব"  # as the Bengali recogniser writes "dr ghosh na ki jeno dr guha"
    bn.mentions[said] = GHOSH_OR_GUHA
    out = text_of(await bn.say(said, "book_appointment", {"doctor_name": "ghosh"}))
    assert "প্রবীর ঘোষ" in out and "সঞ্জয় গুহ" in out
    assert bn.tools.holds == []  # nothing held against either of them
    st = bn.session.booking
    assert [c[0] for c in st.doctor_choices] == ["Dr. P. Ghosh", "Dr. S. Guha"]
    assert not st.doctor_verified

    await bn.say("দ্বিতীয়জন", "book_appointment", {})
    assert st.doctor_verified and st.slots["doctor_name"] == "Dr. S. Guha"


@pytest.mark.asyncio
async def test_a_doctor_named_without_the_word_doctor_is_found_by_the_clinic_not_the_model(bn):
    said = "সৌরভ মুখার্জী দেখাব"
    bn.mentions[said] = {
        **DAS,
        "doctor_name": "Dr. S. Mukherjee",
        "doctors": [{"name": "Dr. S. Mukherjee", "exact": True}],
    }
    await bn.say(said, "unclear", {})  # the model could not classify it at all
    st = bn.session.booking
    assert st is not None and st.doctor_verified and st.slots["doctor_name"] == "Dr. S. Mukherjee"


# ---- "উনি" / "বসেন": the doctor being discussed, never a name ------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("slot", ["বসেন", "উনি", "বসেন উনি"])
async def test_he_and_sits_are_answered_about_the_doctor_just_confirmed(bn, slot):
    said = "ডক্টর দেবাশিস দাস"
    bn.mentions[said] = DAS
    await bn.say(said, "book_appointment", {"doctor_name": "দেবাশিস দাস"})
    assert bn.session.booking.doctor_verified

    await bn.say("অন্য কোন কোন সময় বসেন উনি", "doctor_availability", {"doctor_name": slot})
    assert bn.asked == [("doctor_availability", {"doctor_name": "Dr. D. Das"})]


# ---- "which departments are there?" -------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "said",
    ["কোন কোন ডিপার্টমেন্ট আছে আপনাদের ওখানে", "আপনাদের ওখানে কোন কোন ডক্টর বসেন"],  # the call's own words
)
async def test_which_departments_or_doctors_is_answered_with_the_departments(m, bn, monkeypatch, said):
    async def no_model(session, text, lang):
        raise AssertionError("recognised deterministically: the model is not asked")

    monkeypatch.setattr(m, "_resolve_intent", no_model)
    out = text_of(await bn.say(said, "department_query", {}))
    assert all(d in out for d in DEPARTMENTS_BN)
    assert bn.asked == []


@pytest.mark.asyncio
async def test_the_model_path_lists_departments_too_when_no_symptom_was_said(bn):
    """A phrasing the deterministic detector does not know, filed by the model as department_query with no symptom
    (exactly what the live call's model did): the departments, not "what is the problem?"."""
    out = text_of(await bn.say("আপনাদের ওখানে কী কী চিকিৎসা হয়", "department_query", {}))
    assert all(d in out for d in DEPARTMENTS_BN)
    assert "কোন বিভাগে দেখাতে চান" in out


@pytest.mark.asyncio
async def test_a_symptom_is_still_routed_as_before(bn):
    async def route(query, lang="bn"):
        return {"matched": True, "department_name": "Orthopaedics"}

    bn.tools.route_department = route
    await bn.say("পায়ে ব্যথা করছে", "department_query", {"symptom_description": "পায়ে ব্যথা"})
    assert bn.asked == [("department_query", {"symptom_description": "পায়ে ব্যথা"})]


# ---- a PATIENT who shares a doctor's surname never moves the booking ---------------------------------------------
# Das, Sen, Ghosh, Roy: the commonest Bengali surnames are this clinic's doctors' too. After the doctor is settled the
# booking asks for the patient's name, and that answer must stay the patient's.

DAS_BY_SURNAME = {
    "found": False,
    "count": 1,
    "needs_confirmation": True,
    "doctors": [{"name": "Dr. D. Das", "full_name_bn": "দেবাশিস দাস", "exact": False, "strength": "surname"}],
    "did_you_mean_bn": ["দেবাশিস দাস"],
}
DAS_TITLED = {
    "found": True,
    "count": 1,
    "doctor_name": "Dr. D. Das",
    "doctors": [{"name": "Dr. D. Das", "full_name_bn": "দেবাশিস দাস", "exact": True, "strength": "titled"}],
}


async def _settled_on_ghosh(bn):
    said = "ডক্টর প্রবীর ঘোষ"
    bn.mentions[said] = {**DAS, "doctor_name": "Dr. P. Ghosh", "doctors": [{"name": "Dr. P. Ghosh", "exact": True}]}
    await bn.say(said, "book_appointment", {"doctor_name": "প্রবীর ঘোষ"})
    st = bn.session.booking
    assert st.doctor_verified and st.slots["doctor_name"] == "Dr. P. Ghosh"
    return st


@pytest.mark.asyncio
async def test_the_patients_surname_does_not_change_the_doctor(bn):
    st = await _settled_on_ghosh(bn)
    said = "আমার নাম অমিত দাস"
    bn.mentions[said] = DAS_BY_SURNAME
    await bn.say(said, "book_appointment", {"patient_name": "অমিত দাস"})
    assert st.slots["doctor_name"] == "Dr. P. Ghosh"
    assert st.slots.get("patient_name") == "অমিত দাস"


@pytest.mark.asyncio
async def test_a_bare_patient_name_the_model_could_not_place_stays_with_the_booking(bn):
    st = await _settled_on_ghosh(bn)
    said = "অমিত দাস"
    bn.mentions[said] = DAS_BY_SURNAME
    await bn.say(said, "unclear", {"patient_name": "অমিত দাস"})
    assert bn.asked == []  # not turned into a question about Dr. Das
    assert st.slots["doctor_name"] == "Dr. P. Ghosh"


@pytest.mark.asyncio
async def test_saying_doctor_beside_the_name_still_changes_the_doctor(bn):
    st = await _settled_on_ghosh(bn)
    said = "না, ডাক্তার দাসের কাছে দেখাব"
    bn.mentions[said] = DAS_TITLED
    await bn.say(said, "book_appointment", {"doctor_name": "দাস"})
    assert st.slots["doctor_name"] == "Dr. D. Das"


@pytest.mark.asyncio
async def test_seeing_a_doctor_filed_as_a_department_question_asks_which_doctor(bn):
    """Live run 2026-10-07: "আমি একজন ডাক্তার দেখাব" came back department_query, no symptom -- and the caller was read the
    department list instead of being asked which doctor."""
    out = text_of(await bn.say("আমি একজন ডাক্তার দেখাব", "department_query", {}))
    assert "কোন ডাক্তার দেখাতে চান" in out and "কার্ডিওলজি" in out
    assert bn.session.booking is not None and bn.session.booking.action == "book_appointment"


# ---- the time the caller said (agent/spoken_time.py) ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_spoken_bengali_time_is_the_slot_whatever_the_model_wrote(bn):
    said = "ডক্টর দেবাশিস দাস"
    bn.mentions[said] = DAS
    await bn.say(said, "book_appointment", {"doctor_name": "দেবাশিস দাস"})
    await bn.say("শুক্রবার, সকাল সাড়ে দশটায়", "book_appointment", {"date": "2026-10-09", "time_slot": "সকাল"})
    st = bn.session.booking
    assert st.slots.get("time_slot") == "10:30"


@pytest.mark.asyncio
@pytest.mark.parametrize("model_says", ["smalltalk", "unclear"])
async def test_a_bare_time_mid_booking_is_the_bookings_answer_not_smalltalk(bn, model_says):
    """Live run 2026-10-07: "সকাল দশটায়" mid-booking came back smalltalk, with a model-written reply that meant nothing."""
    said = "ডক্টর দেবাশিস দাস"
    bn.mentions[said] = DAS
    await bn.say(said, "book_appointment", {"doctor_name": "দেবাশিস দাস"})
    await bn.say("শুক্রবার", "book_appointment", {"date": "2026-10-09"})
    out = text_of(await bn.say("সকাল দশটায়", model_says, {}, direct_reply_bn="সকাল দিনে আপনাকে ভালো লাগে হোক।"))
    assert "ভালো লাগে" not in out
    assert bn.session.booking.slots.get("time_slot") == "10:00"


@pytest.mark.asyncio
async def test_patient_details_filed_as_an_availability_question_go_to_the_booking(bn):
    """Live run 2026-10-07 (Hindi): "प्रिया सेन, नौ आठ तीन ..." for Dr. Arindam Sen's booking was filed as
    doctor_availability, and the caller was asked "do you mean Dr. Arindam Sen?" instead of hearing the read-back."""
    st = await _settled_on_ghosh(bn)
    said = "প্রিয়া সেন, নয় আট তিন এক এক এক দুই দুই দুই তিন"
    await bn.say(said, "doctor_availability", {"doctor_name": "প্রিয়া সেন", "patient_name": "প্রিয়া সেন"})
    assert bn.asked == []  # no availability answer about anybody
    assert st.slots["doctor_name"] == "Dr. P. Ghosh"
    assert st.slots.get("patient_name") == "প্রিয়া সেন"
    assert st.slots.get("phone") == "9831112223"  # the spoken digits, read deterministically
