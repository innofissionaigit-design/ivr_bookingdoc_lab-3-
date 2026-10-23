"""Live 2026-10-12, through the REAL orchestrator and the REAL clinic API: "আমি গাইনিতে একজন ডক্টরকে দেখাব" ("I will see
a doctor in gynae") opens the gynae doctors' list and the booking, exactly as "আমি গাইনির একজন ডক্টরকে দেখাব" did.

Nothing about the clinic is canned here (tests/_conversation.py): the seeded clinic API, its department matcher
(clinic-api/department_match.py, rule 4 and all) and the real doctor table answer, so these tests also show that every
one of the 8 departments and all 32 doctors can be reached from a caller's sentence -- in Bengali and in Hindi.

    python -m pytest tests/test_department_endings_flow.py -v
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from _conversation import conversation  # noqa: E402


def _load(name: str):
    key = f"_dm_flow_{name}"
    spec = importlib.util.spec_from_file_location(key, os.path.join(REPO_ROOT, "clinic-api", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


i18n = _load("i18n_content")
DEPARTMENTS = list(i18n.DEPARTMENT_ALIASES)

LIVE = "আমি গাইনিতে একজন ডক্টরকে দেখাব"
BEFORE = "আমি গাইনির একজন ডক্টরকে দেখাব"
GYNAE = "Gynaecology & Obstetrics"

_VOWEL_SIGNS = "ািীুূেৈোৌ"


def _everyone(call) -> dict[str, list[dict]]:
    """department -> its doctors, from the clinic API the call is talking to."""
    result = call._run(call.m._tools.list_doctors())
    out: dict[str, list[dict]] = {}
    for d in result["doctors"]:
        out.setdefault(d["department"], []).append(d)
    return out


def _locative_bn(department: str) -> str:
    """The department as a caller says it in Bengali, in the locative ("in gynae"): the first word of its name
    ("গাইনি ও প্রসূতি" -> "গাইনি") + "তে" after a vowel sign, "ে" after a consonant."""
    stem = i18n.DEPARTMENT_NAMES_I18N[department][0].split(" ও ")[0].strip()
    return stem + ("তে" if stem[-1] in _VOWEL_SIGNS else "ে")


# ================================================================== the live call
@pytest.mark.parametrize("said", [LIVE, BEFORE])
def test_the_live_sentence_lists_the_gynae_doctors_and_opens_the_booking(said):
    """The intent model called it a booking and left the slots empty -- for both spellings, live."""
    with conversation("bn") as call:
        gynae = _everyone(call)[GYNAE]
        spoken = " ".join(call.say(said, "book_appointment", {}))
        for d in gynae:
            assert d["full_name_bn"] in spoken, (d["full_name_bn"], spoken)
        st = call.session.booking
        assert st is not None and st.action == "book_appointment"
        assert sorted(c[0] for c in st.doctor_choices) == sorted(d["name"] for d in gynae)
        assert call.blocked == []  # nothing the voice could not say


def test_the_locative_and_the_genitive_get_the_same_answer():
    with conversation("bn") as a:
        first = a.say(LIVE, "book_appointment", {})
    with conversation("bn") as b:
        second = b.say(BEFORE, "book_appointment", {})
    # "গাইনিতে" and "গাইনির" are different words; the reply must not depend on which was said.
    strip = lambda lines: [ln.replace("গাইনিতে", "").replace("গাইনির", "") for ln in lines]  # noqa: E731
    assert strip(first) == strip(second)


def test_the_same_sentence_as_a_department_question_lists_them_too():
    with conversation("bn") as call:
        gynae = _everyone(call)[GYNAE]
        spoken = " ".join(call.say(LIVE, "department_query", {"symptom_description": "গাইনিতে"}))
        for d in gynae:
            assert d["full_name_bn"] in spoken


def test_a_doctor_picked_from_that_list_goes_on_in_the_same_booking():
    with conversation("bn") as call:
        gynae = _everyone(call)[GYNAE]
        call.say(LIVE, "book_appointment", {})
        pick = gynae[1]
        call.say(f"{pick['full_name_bn']}কে", "book_appointment", {"doctor_name": pick["full_name_bn"]})
        st = call.session.booking
        assert st is not None and st.action == "book_appointment"
        assert st.slots.get("doctor_name") == pick["name"]


# ================================================================== what must not change
def test_a_sentence_with_no_department_is_still_asked_which_doctor():
    """"to book" is "করতে" -- "কর" + "তে". The caller is asked which doctor, as before; no list is read out."""
    with conversation("bn") as call:
        everyone = _everyone(call)
        spoken = " ".join(call.say("আমি অ্যাপয়েন্টমেন্ট করতে চাই", "book_appointment", {}))
        listed = [d["full_name_bn"] for ds in everyone.values() for d in ds if d["full_name_bn"] in spoken]
        assert listed == []


def test_a_lab_test_that_says_sugar_is_not_sent_to_a_department():
    with conversation("bn") as call:
        everyone = _everyone(call)
        spoken = " ".join(call.say("আমার সুগার টেস্ট করাতে চাই", "book_test", {"test_name": "সুগার টেস্ট"}))
        assert not any(d["full_name_bn"] in spoken for ds in everyone.values() for d in ds)


def test_a_misspelt_department_is_asked_about_never_opened():
    """"কার্ডিয়োলজি" is one letter off the clinic's spelling: "did you mean cardiology?", and no list yet."""
    with conversation("bn") as call:
        cardiology = _everyone(call)["Cardiology"]
        spoken = " ".join(call.say("কার্ডিয়োলজি", "department_query", {"symptom_description": "কার্ডিয়োলজি"}))
        assert "কার্ডিওলজি" in spoken  # offered in the voice's own script
        assert not any(d["full_name_bn"] in spoken for d in cardiology)  # nothing opened behind the caller's back


# ================================================================== every department, every doctor
@pytest.mark.parametrize("department", DEPARTMENTS)
def test_every_department_in_the_bengali_locative_reaches_all_of_its_doctors(department):
    with conversation("bn") as call:
        doctors = _everyone(call)[department]
        said = f"আমি {_locative_bn(department)} একজন ডক্টরকে দেখাব"
        spoken = " ".join(call.say(said, "book_appointment", {}))
        missing = [d["full_name_bn"] for d in doctors if d["full_name_bn"] not in spoken]
        assert len(doctors) == 4 and missing == [], (said, missing, spoken)
        st = call.session.booking
        assert st is not None and sorted(c[0] for c in st.doctor_choices) == sorted(d["name"] for d in doctors)


@pytest.mark.parametrize("department", DEPARTMENTS)
def test_every_department_in_hindi_reaches_all_of_its_doctors(department):
    with conversation("hi") as call:
        doctors = _everyone(call)[department]
        hindi = i18n.DEPARTMENT_NAMES_I18N[department][1].split(" एवं ")[0].strip()
        said = f"मुझे {hindi} में एक डॉक्टर को दिखाना है"
        spoken = " ".join(call.say(said, "book_appointment", {}))
        missing = [d["full_name_hi"] for d in doctors if d["full_name_hi"] not in spoken]
        assert len(doctors) == 4 and missing == [], (said, missing, spoken)
        assert call.session.booking is not None
