"""Four defects a whole-call matrix found on 2026-10-12 (tests/bn_flow_report.py), each pinned here.

1. A price question was answered "a doctor must explain this" and handed to a clinician when it named a test with a
   digit in its name next to "সুগার": "সুগার পিপি, এইচবিএ১সি আর ভিটামিন ডি করাতে কত লাগবে". The "১" of
   এইচবিএ১সি (HbA1c), the "১২" of বি১২ (B12) and the "১" of এনএস১ (NS1) are the TEST'S NAME, not a lab value.
2. In a list of tests, the first name's words took out every equal word of the later names: "ইউরিন টেস্ট, ইকো টেস্ট আর
   পিএসএ" was read back as two tests, and "ডেঙ্গু এনএস১, ডেঙ্গু আইজিজি আর ইকো টেস্ট" as NS1, **ECG** and Echo --
   a test nobody named, priced. The caller heard a total that left a test out, or included a wrong one.
3. "আপনি ইকো টেস্ট টেস্টের দাম জানতে চান" -- "টেস্ট" said twice.
4. "কার্ডিওলজির ডাক্তার দেখাতে চাই" with the department in the symptom slot: the doctors were read out, no booking
   was opened, and "the second one" / a bare name had nothing to attach to.

    python -m pytest tests/test_bengali_flow_matrix_fixes.py -v
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

from agent.clinical_safety import is_clinical_interpretation  # noqa: E402
from agent.entity_grounding import _without, ground_tests  # noqa: E402
from agent.reply_templates_i18n import test_confirm_prompt as confirm_prompt  # noqa: E402

B = "book_appointment"


# ============================================================ 1. a digit inside a test's name is not a lab value
@pytest.mark.parametrize(
    "said",
    [
        "সুগার পিপি, এইচবিএ১সি আর ভিটামিন ডি করাতে কত লাগবে",
        "সুগার পিপি, ভিটামিন বি১২ আর প্যাপ স্মিয়ার টেস্টের মোট দাম কত হবে",
        "ব্লাড সুগার ফাস্টিং আর ডেঙ্গু এনএস১-এর দাম কত",
        "হিমোগ্লোবিন এ১সি-এর দাম কত",
        "price of HbA1c and blood sugar",
        "how much is vitamin b12 and sugar test",
    ],
)
def test_a_price_question_naming_a_test_with_a_digit_is_not_a_clinical_question(said):
    assert is_clinical_interpretation(said) is False


@pytest.mark.parametrize(
    "said",
    [
        "আমার সুগার ২৫০ এসেছে",
        "আমার সুগার 250 এসেছে",
        "সুগার ৯৯ কি বেশি",
        "হিমোগ্লোবিন ৯ এসেছে",
        "my sugar is 250",
        "my hba1c is 9.1",
        "hba1c 7.2",
        "আমার রিপোর্ট কি বিপজ্জনক",
    ],
)
def test_a_figure_read_off_a_report_still_is(said):
    assert is_clinical_interpretation(said) is True


# ============================================================ 2. a list of tests keeps every name it was given
def test_without_takes_out_the_run_of_the_name_once():
    assert _without("ইউরিন টেস্ট ইকো টেস্ট পিএসএ", "ইউরিন টেস্ট") == "ইকো টেস্ট পিএসএ"
    assert _without("ডেঙ্গু এনএস১ ডেঙ্গু আইজিজি", "ডেঙ্গু এনএস১") == "ডেঙ্গু আইজিজি"
    assert _without("থাইরয়েড টেস্টের দাম কত", "থাইরয়েড টেস্ট") == "দাম কত"  # an inflected word is still the name
    assert _without("সিবিসি ইসিজি", "সিবিসি") == "ইসিজি"
    assert _without("anything", "") == ""


@pytest.fixture(scope="module")
def catalogue():
    from _clinic_app import clinic_app
    from agent.fast_path import Catalogue

    with clinic_app(sample_patients=False) as (app, client):
        yield Catalogue(client.get("/api/v1/catalogue").json())


@pytest.mark.parametrize(
    "said,expected",
    [
        ("ইউরিন টেস্ট ইকো টেস্ট আর পিএসএ করাতে কত লাগবে",
         {"Urine Routine Examination", "2D Echocardiography", "PSA (Prostate Specific Antigen)"}),
        ("কিডনি ফাংশন টেস্ট ডেঙ্গু এনএস১ ডেঙ্গু আইজিজি আর ইকো টেস্টের মোট দাম কত",
         {"Kidney Function Test (KFT)", "Dengue NS1 Antigen", "Dengue IgG/IgM", "2D Echocardiography"}),
        ("সিবিসি ইএসআর ব্লাড সুগার ফাস্টিং সুগার পিপি আর এইচবিএ১সি-এর দাম",
         {"Complete Blood Count (CBC)", "ESR", "Blood Sugar Fasting", "Blood Sugar PP", "HbA1c"}),
        ("লিপিড প্রোফাইল লিভার ফাংশন টেস্ট থাইরয়েড প্রোফাইল ভিটামিন বি১২ আর ইকো টেস্ট",
         {"Lipid Profile", "Liver Function Test (LFT)", "Thyroid Profile (T3 T4 TSH)", "Vitamin B12",
          "2D Echocardiography"}),
    ],
)
def test_every_test_in_a_list_is_grounded_and_none_is_invented(catalogue, said, expected):
    grounded = ground_tests(said, "bn", catalogue)
    assert set(grounded.names) == expected, (grounded.names, grounded.forms)
    assert "ECG" not in grounded.names or "ECG" in expected


def test_two_names_with_nothing_in_common_are_unchanged(catalogue):
    grounded = ground_tests("সিবিসি আর থাইরয়েড প্রোফাইল-এর দাম", "bn", catalogue)
    assert set(grounded.names) == {"Complete Blood Count (CBC)", "Thyroid Profile (T3 T4 TSH)"}


# ============================================================ 3. the read-back says "টেস্ট" once
@pytest.mark.parametrize("action", ["test_rate", "book_test"])
def test_a_name_that_ends_in_test_is_not_given_another(action):
    said = confirm_prompt(["ইকো টেস্ট"], action, "bn")
    assert "টেস্ট টেস্ট" not in said
    assert "ইকো টেস্ট" in said


def test_a_name_that_does_not_end_in_test_still_gets_it():
    assert confirm_prompt(["সিবিসি"], "test_rate", "bn") == "আপনি সিবিসি টেস্টের দাম জানতে চান, ঠিক তো?"
    assert confirm_prompt(["সিবিসি"], "book_test", "bn") == "আপনি সিবিসি টেস্ট করবেন, ঠিক তো?"
    assert confirm_prompt(["ইউরিন টেস্ট", "পিএসএ"], "test_rate", "bn") == (
        "আপনি ইউরিন টেস্ট আর পিএসএ টেস্টের দাম জানতে চান, ঠিক তো?"
    )
    assert confirm_prompt(["ইকো টেস্ট"], "test_rate", "bn") == "আপনি ইকো টেস্টের দাম জানতে চান, ঠিক তো?"
    assert "टेस्ट टेस्ट" not in confirm_prompt(["इको टेस्ट"], "test_rate", "hi")


# ============================================================ 4. the department's list is remembered
def _doctors(call, department):
    result = call._run(call.m._tools.list_doctors())["doctors"]
    return [d for d in result if d["department"] == department]


def test_department_in_the_symptom_slot_with_i_will_see_opens_the_booking():
    with conversation("bn") as call:
        doctors = _doctors(call, "Cardiology")
        spoken = " ".join(call.say("কার্ডিওলজির ডাক্তার দেখাতে চাই", "department_query",
                                   {"symptom_description": "কার্ডিওলজি"}))
        for d in doctors:
            assert d["full_name_bn"] in spoken
        st = call.session.booking
        assert st is not None and st.action == B
        assert [c[0] for c in st.doctor_choices] == [d["name"] for d in doctors]


def test_the_second_one_picks_the_second_doctor_after_a_booking_was_opened():
    with conversation("bn") as call:
        doctors = _doctors(call, "Cardiology")
        call.say("কার্ডিওলজির ডাক্তার দেখাতে চাই", "department_query", {"symptom_description": "কার্ডিওলজি"})
        order = [c[0] for c in call.session.booking.doctor_choices]
        call.say("দ্বিতীয়জন", "unclear", {})
        st = call.session.booking
        assert st.doctor_verified and st.slots["doctor_name"] == order[1]


def test_a_question_only_list_has_no_booking_but_its_answer_still_lands():
    """"which cardiologists are there" is information: nothing is opened behind the caller's back. But the question
    the reply ends with is still answered by the next turn, by position and by name."""
    for said, intent, slots in (("দ্বিতীয়জন", "unclear", {}), ("প্রথমজনকে দেখাতে চাই", "unclear", {})):
        with conversation("bn") as call:
            doctors = _doctors(call, "Cardiology")
            call.say("কার্ডিওলজিতে কারা কারা বসেন", "department_query", {"symptom_description": "কার্ডিওলজি"})
            assert call.session.booking is None  # the information was only information
            assert call.session.offered_doctors is not None
            order = call.session.offered_doctors[0]
            call.say(said, intent, slots)
            st = call.session.booking
            assert st is not None and st.action == B, said
            assert st.slots.get("doctor_name") in (order[1] if "দ্বিতীয়" in said else order[0],), said


def test_a_bare_doctor_name_after_that_list_continues_into_a_booking():
    with conversation("bn") as call:
        doctors = _doctors(call, "Cardiology")
        call.say("কার্ডিওলজিতে কারা কারা বসেন", "department_query", {"symptom_description": "কার্ডিওলজি"})
        pick = doctors[2]
        call.say(pick["full_name_bn"], "doctor_availability", {"doctor_name": pick["full_name_bn"]})
        st = call.session.booking
        assert st is not None and st.action == B and st.slots.get("doctor_name") == pick["name"]


def test_a_symptom_in_the_symptom_slot_is_still_routed_by_symptom_not_asked_which_doctor():
    """The department lookup here is by NAME only: a complaint that is not a department's name must not be turned
    into "which doctor?" because the caller also said they want to see someone."""
    with conversation("bn") as call:
        spoken = " ".join(call.say("বুকে ব্যথা নিয়ে ডাক্তার দেখাতে চাই", "department_query",
                                   {"symptom_description": "বুকে ব্যথা"}))
        assert "কোন ডাক্তার" not in spoken or "কার্ডিওলজি" in spoken


# ============================================================ 5. a lay term is only for a sentence that names no other test
import contextlib  # noqa: E402
import re  # noqa: E402

from agent.fast_path import Catalogue, FastPath  # noqa: E402
from agent.reply_templates_i18n import _rupees  # noqa: E402


@contextlib.contextmanager
def _lay_term_ready(call):
    """The lay-term path reads the catalogue over HTTP from CLINIC_API_BASE (a real address on the pod); here the
    same clinic app answers, and the fast path is loaded the way a booted pod has it."""
    m = call.m
    saved = m._fetch_catalogue

    async def fetch():
        r = await m._tools._client.get("/api/v1/catalogue")
        return r.json()

    m._fetch_catalogue = fetch
    m._catalogue_cache.update({"payload": None, "at": 0.0})
    m._fast_path = FastPath(Catalogue(call._run(m._tools._client.get("/api/v1/catalogue")).json()))
    try:
        yield
    finally:
        m._fetch_catalogue = saved
        m._catalogue_cache.update({"payload": None, "at": 0.0})


def test_a_question_naming_the_ultrasound_and_other_tests_prices_all_of_them():
    with conversation("bn") as call, _lay_term_ready(call):
        rates = {t["test_name"]: t["rate_inr"] for t in call._run(call.m._tools.list_tests())["tests"]}
        said = call.say("সুগার পিপি আর পেটের আলট্রাসাউন্ড করাতে কত লাগবে", "test_rate",
                        {"test_names": ["Blood Sugar PP", "USG Whole Abdomen"]})
        assert said[-1].endswith("?")  # read back first, as every several-test question is
        reply = " ".join(call.say("হ্যাঁ", "unclear", {}))
        assert _rupees(rates["Blood Sugar PP"]) in reply and _rupees(rates["USG Whole Abdomen"]) in reply
        assert _rupees(rates["Blood Sugar PP"] + rates["USG Whole Abdomen"]) in reply
        assert "প্রেসক্রিপশন" not in reply  # the lay-term answer's prescription note was not what answered


def test_the_ultrasound_alone_is_still_the_lay_term_answer():
    with conversation("bn") as call, _lay_term_ready(call):
        rates = {t["test_name"]: t["rate_inr"] for t in call._run(call.m._tools.list_tests())["tests"]}
        reply = " ".join(call.say("পেটের ছবি তুলতে কত লাগবে", "test_rate", {"test_name": "USG Whole Abdomen"}))
        # the price is the answer; the "send a prescription photo to this WhatsApp number" sentence is gone
        assert _rupees(rates["USG Whole Abdomen"]) in reply and call.blocked == []
        assert "প্রেসক্রিপশনের একটা ছবি" not in reply and "৯৬৩৫৫৮৮৩০৬" not in reply and "হোয়াটসঅ্যাপ" not in reply


def test_a_blood_test_asked_by_its_lay_name_is_listed_in_bengali_and_can_be_spoken():
    with conversation("bn") as call, _lay_term_ready(call):
        reply = " ".join(call.say("রক্ত পরীক্ষা করাতে চাই", "book_test", {}))
        assert "সিবিসি" in reply and "কোনটা করাতে চান" in reply
        assert not re.search(r"[A-Za-z]{3,}", reply.replace("KCD", ""))  # nothing the Bengali voice cannot say
        assert call.blocked == []


def test_the_ultrasound_and_pap_smear_question_a_caller_reported_prices_both():
    """Reported from a live instance on 2026-10-12: "পেটের আলট্রাসাউন্ড আর প্যাপ স্মিয়ার টেস্টের মোট দাম কত হবে" was
    answered with the ultrasound's price and the prescription note alone."""
    said = "পেটের আলট্রাসাউন্ড আর প্যাপ স্মিয়ার টেস্টের মোট দাম কত হবে"
    for slots in ({"test_names": ["USG Whole Abdomen", "Pap Smear"]}, {"test_name": "USG Whole Abdomen"}):
        with conversation("bn") as call, _lay_term_ready(call):
            rates = {t["test_name"]: t["rate_inr"] for t in call._run(call.m._tools.list_tests())["tests"]}
            first = call.say(said, "test_rate", dict(slots))
            assert first[-1].endswith("ঠিক তো?"), first  # read back, not answered at once
            reply = " ".join(call.say("হ্যাঁ", "unclear", {}))
            assert _rupees(rates["USG Whole Abdomen"]) in reply and _rupees(rates["Pap Smear"]) in reply
            assert _rupees(rates["USG Whole Abdomen"] + rates["Pap Smear"]) in reply and "মোট" in reply
            assert "প্রেসক্রিপশন" not in reply
