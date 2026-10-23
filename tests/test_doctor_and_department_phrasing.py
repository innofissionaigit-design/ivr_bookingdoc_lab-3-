"""How callers actually phrase doctor and department questions -- the live call of 2026-10-07 and its neighbours.

  python -m pytest tests/test_doctor_and_department_phrasing.py -v

* agent/doctor_list.py        "which doctors / departments?" by its STRUCTURE, loanwords and case endings included
* agent/doctor_request.py     words that are never a doctor's name (pronouns, "sits", "when")
* agent/enquiry_followup.py   such a word in the doctor slot means the doctor being discussed
* agent/reply_templates.py    the department list, speakable by each language's voice
* clinic-api symptom routing  "ব্যাথা", the commonest spelling of "ব্যথা", reaches the same department
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.doctor_list import detect_doctor_list_request  # noqa: E402
from agent.doctor_request import names_a_doctor  # noqa: E402
from agent.enquiry_followup import EnquiryEntity, resolve_followup_slot  # noqa: E402
from agent.lang_select import speakable  # noqa: E402
from agent.reply_templates import department_list_prompt  # noqa: E402

# ---- "which doctors / which departments?" ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "আপনাদের ওখানে কোন কোন ডক্টর বসেন",  # live call: "ডক্টর", the loanword
        "কোন কোন ডিপার্টমেন্ট আছে আপনাদের ওখানে",  # live call: "ডিপার্টমেন্ট"
        "কোন কোন ডক্টর বসেন কোন কোন ডিপার্টমেন্ট এর ডক্টর বসেন",  # live call
        "আপনাদের কী কী ডিপার্টমেন্ট আছে",
        "আপনাদের কোন ডিপার্টমেন্ট আছে",
        "কোন কোন বিভাগের ডাক্তার বসেন",
        "কতজন ডাক্তার আছেন",
        "ডিপার্টমেন্টের লিস্ট দিন",
        "আপনাদের কোন কোন স্পেশালিস্ট আছেন",
        "कौन कौन से डिपार्टमेंट हैं",
        "कितने डॉक्टर हैं",
        "kon kon department ache",
        "how many departments do you have",
        "what are the departments",
    ],
)
def test_a_request_for_the_list_is_recognised_however_it_is_phrased(text):
    assert detect_doctor_list_request(text)[0] is True


@pytest.mark.parametrize(
    "text",
    [
        "কোন ডাক্তার দেখাব",  # "which doctor should I see": help to choose, not the list
        "কোন কোন দিন বসেন ডাক্তার দাস",  # "on which days does Dr Das sit": the plural is about days
        "ডাক্তার দাস কোন কোন দিন বসেন",
        "অন্য কোন কোন সময় বসেন উনি",  # live call: about times, and about one doctor
        "কোন বিভাগে আছেন ডাক্তার দাস",  # "in which department is Dr Das": locative
        "কোন বিভাগে দেখাব",
        "সব ঠিক আছে, ডাক্তার দাসের অ্যাপয়েন্টমেন্ট চাই",  # "all fine": "all" not beside the subject
        "ডাক্তার দেখাবো",
        "ডক্টর সেন কি আজ আছেন",
        "डॉक्टर सेन कल हैं क्या",
        "which day is Dr Sen in",
    ],
)
def test_a_question_about_one_doctor_or_about_days_is_not_a_list_request(text):
    assert detect_doctor_list_request(text)[0] is False


# ---- words that never name a doctor ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["বসেন", "উনি", "বসেন উনি", "ওনার", "কখন বসেন", "उनका", "वो कब बैठते हैं", "he", "when does he sit"]
)
def test_a_pronoun_or_a_verb_names_nobody(value):
    assert names_a_doctor(value) is False


@pytest.mark.parametrize("value", ["দাস", "ডক্টর দেবাশিস দাস", "Mukherjee", "অমিত কর", "उनका डॉक्टर दास"])
def test_a_name_beside_those_words_is_still_a_name(value):
    assert names_a_doctor(value) is True


@pytest.mark.parametrize("value", ["বসেন", "উনি", "বসেন উনি", "ডাক্তার", None])
def test_such_a_word_in_the_doctor_slot_means_the_doctor_being_discussed(value):
    last = [EnquiryEntity("doctor_name", "Dr. D. Das")]
    slots, resolved = resolve_followup_slot(
        "doctor_availability", {"doctor_name": value}, "অন্য কোন কোন সময় বসেন উনি", "bn", last, 1
    )
    assert resolved and slots["doctor_name"] == "Dr. D. Das"


def test_with_two_doctors_being_discussed_he_is_asked_about_never_guessed():
    last = [EnquiryEntity("doctor_name", "Dr. D. Das"), EnquiryEntity("doctor_name", "Dr. P. Ghosh")]
    slots, resolved = resolve_followup_slot(
        "doctor_availability", {"doctor_name": "উনি"}, "উনি কখন বসেন", "bn", last, 1
    )
    assert not resolved


def test_a_real_name_is_never_replaced_by_the_one_being_discussed():
    last = [EnquiryEntity("doctor_name", "Dr. D. Das")]
    slots, resolved = resolve_followup_slot(
        "doctor_availability", {"doctor_name": "ঘোষ"}, "ঘোষ কখন বসেন", "bn", last, 1
    )
    assert not resolved and slots["doctor_name"] == "ঘোষ"


# ---- the department list, said by each voice ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "lang,departments",
    [("bn", ["কার্ডিওলজি", "অর্থোপেডিক"]), ("hi", ["कार्डियोलॉजी", "ऑर्थोपेडिक"]), ("en", ["Cardiology", "Orthopaedics"])],
)
def test_the_department_list_names_them_and_asks_which_and_is_speakable(lang, departments):
    reply = department_list_prompt(departments, lang)
    assert all(d in reply for d in departments)
    assert "?" in reply
    assert speakable(reply, lang), reply


@pytest.mark.parametrize("lang", ["bn", "hi", "en"])
def test_without_the_list_it_still_moves_the_caller_on(lang):
    reply = department_list_prompt([], lang)
    assert reply and "?" in reply and speakable(reply, lang)


# ---- symptom spelling (clinic-api) -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def clinic():
    from _clinic_app import clinic_app

    with clinic_app(sample_patients=False) as (_app, c):
        yield c


@pytest.mark.parametrize(
    "said,department",
    [
        ("পায়ে ব্যাথা করছে", "Orthopaedics"),  # "ব্যাথা": what ASR and callers write
        ("পায়ে ব্যথা করছে", "Orthopaedics"),  # unchanged
        ("বুকে ব্যাথা", "Cardiology"),
        ("কোমরে খুব ব্যাথা", "Orthopaedics"),
    ],
)
def test_the_common_spelling_of_pain_routes_like_the_dictionary_one(clinic, said, department):
    body = clinic.get("/api/v1/departments/route", params={"query": said, "lang": "bn"}).json()
    assert body["matched"] is True and body["department_name"] == department


@pytest.mark.parametrize("said", ["কোন কোন ডিপার্টমেন্ট আছে আপনাদের ওখানে", "how many departments do you have"])
def test_a_question_about_departments_is_never_routed_as_a_symptom(clinic, said):
    body = clinic.get("/api/v1/departments/route", params={"query": said, "lang": "bn"}).json()
    assert body["matched"] is False


# ---- "when can I get the earliest appointment" is not "what have I booked" (agent/lookup_request.py) -------------


@pytest.mark.parametrize(
    "said",
    [
        "ডাক্তার নির্মল রায়ের সবচেয়ে তাড়াতাড়ি কবে অ্যাপয়েন্টমেন্ট পাওয়া যাবে?",
        "डॉक्टर निर्मल रॉय का सबसे जल्दी अपॉइंटमेंट कब मिलेगा?",  # live: "tell me the number you booked with"
        "When is the earliest appointment with Doctor Nirmal Roy?",
    ],
)
def test_asking_for_the_earliest_slot_is_not_a_lookup(said):
    from agent.lookup_request import asks_what_is_booked

    assert asks_what_is_booked(said) is False


@pytest.mark.parametrize("said", ["मेरा अपॉइंटमेंट कब है?", "আমার অ্যাপয়েন্টমেন্ট কবে?", "What appointment do I have booked?"])
def test_asking_about_ones_own_booking_is_still_a_lookup(said):
    from agent.lookup_request import asks_what_is_booked

    assert asks_what_is_booked(said) is True
