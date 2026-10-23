"""clinic-api/doctor_mention.py and GET /api/v1/doctors/mentioned: which of the clinic's doctors does a SENTENCE name?

    python -m pytest tests/test_doctor_mention.py -v

Every case here is either a sentence from the live call of 2026-10-07 or a rule from the module's docstring, run
against the REAL seeded doctor table (tests/_clinic_app.py), not a hand-made list -- so a change to the seed that breaks
a rule (a new doctor whose surname is an everyday word, say) shows up here.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from _clinic_app import clinic_app  # noqa: E402

from agent.doctor_request import _FILLER, names_a_doctor  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with clinic_app(sample_patients=False) as (_app, c):
        yield c


def mentioned(client, text):
    body = client.get("/api/v1/doctors/mentioned", params={"text": text}).json()
    return body, [d["name"] for d in body["doctors"]]


# ---- one doctor, named in any of the ways callers name them -------------------------------------------------------


@pytest.mark.parametrize(
    "text,doctor",
    [
        ("ডক্টর দেবাশিস দাস", "Dr. D. Das"),  # the live call's answer, in the call's language
        ("डॉ देवाशीज दास", "Dr. D. Das"),  # the same answer as the Hindi recogniser wrote it, first name garbled
        ("সৌরভ মুখার্জী দেখাব", "Dr. S. Mukherjee"),  # no word for "doctor" at all
        ("Mukherjee dekhabo", "Dr. S. Mukherjee"),
        ("দাসের কাছে যাব", "Dr. D. Das"),  # case ending
        ("ডাক্তারবাবু দাস", "Dr. D. Das"),
        ("ডাক্তার করের কাছে দেখাব", "Dr. A. Kar"),  # an everyday-word surname, beside the title
        ("অমিত কর দেখাব", "Dr. A. Kar"),  # ... or beside his own first name
        ("ডাক্তার বিশ্বাসের অ্যাপয়েন্টমেন্ট", "Dr. N. Biswas"),
        ("dr chowdhuri", "Dr. R. Chowdhury"),  # spelling variation normalised away
        ("ডক্টর মুখার্জি", "Dr. S. Mukherjee"),  # ি for ী
    ],
)
def test_a_doctor_named_in_the_sentence_is_found(client, text, doctor):
    body, names = mentioned(client, text)
    assert names == [doctor]
    assert body["found"] is True and body["doctor_name"] == doctor


# ---- several doctors: the caller is asked, never answered about one of them ---------------------------------------


@pytest.mark.parametrize(
    "text,doctors",
    [
        ("dr ghosh na kijeno dr guha", {"Dr. P. Ghosh", "Dr. S. Guha"}),  # the requirement's own example
        ("রায় ডাক্তার", {"Dr. N. Roy", "Dr. P. Ray"}),  # two doctors share the Bengali surname
        ("ডাক্তার বসু", {"Dr. A. Basu", "Dr. T. Bose"}),
    ],
)
def test_several_doctors_named_are_all_offered_by_name(client, text, doctors):
    body, names = mentioned(client, text)
    assert set(names) == doctors
    assert body["found"] is False and body["ambiguous"] is True
    assert len(body["did_you_mean_bn"]) == len(doctors)  # offered in a script the Bengali voice can say


def test_the_question_names_both_doctors_in_bengali(client):
    body, _ = mentioned(client, "dr ghosh na kijeno dr guha")
    assert body["did_you_mean_bn"] == ["প্রবীর ঘোষ", "সঞ্জয় গুহ"]


# ---- nobody: the words that broke the live call, and the everyday-word collisions ---------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "অন্য কোন কোন সময় বসেন উনি",  # "বসেন" (sits) contains "সেন"; "উনি" is "he" -- the live call
        "ডাক্তার দেখাবো",  # "I'll see a doctor"
        "একজন ডক্টর দেখাব",
        "অ্যাপয়েন্টমেন্টটা বুক কর",  # "কর" is "do", not Dr. Kar
        "book kar do kal ke liye",  # ... in Hindi too
        "নাম্বারটা দে",  # "দে" is "give", not Dr. Dey
        "আমার বিশ্বাস কাল ভালো হবে",  # "বিশ্বাস" is "belief", not Dr. Biswas
        "কোন কোন ডিপার্টমেন্ট আছে",
        "Amit se baat karwao",  # a first name alone is never a doctor
        "",
    ],
)
def test_a_sentence_that_names_no_doctor_names_none(client, text):
    body, names = mentioned(client, text)
    assert names == [] and body["count"] == 0 and body["found"] is False


# ---- found, but asked about first ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,doctor",
    [
        ("Ronit Mukherjee dekhabo", "Dr. S. Mukherjee"),  # the clinic's Mukherjee is Sourav, not Ronit
        ("ডক্টর রনিত মুখার্জী", "Dr. S. Mukherjee"),
        ("অমিত দাস", "Dr. D. Das"),  # Amit is Dr. Kar's first name: which one did they mean?
        ("mukharji", "Dr. S. Mukherjee"),  # a near spelling
    ],
)
def test_an_uncertain_match_is_offered_for_confirmation_never_resolved(client, text, doctor):
    body, names = mentioned(client, text)
    assert names == [doctor]
    assert body["found"] is False and body["needs_confirmation"] is True
    assert "doctor_name" not in body  # nothing a caller could be booked against


# ---- every doctor in the clinic is reachable by full name, in every script --------------------------------------


def test_every_seeded_doctor_is_found_by_full_name_in_every_script(client):
    doctors = client.get("/api/v1/doctors").json()["doctors"]
    assert len(doctors) == 32
    missed = []
    for d in doctors:
        for key in ("full_name", "full_name_bn", "full_name_hi"):
            said = (d.get(key) or "").replace("Dr. ", "")
            if not said:
                continue
            _, names = mentioned(client, said)
            if names != [d["name"]]:
                missed.append((d["name"], key, said, names))
    assert not missed, missed


def test_no_seeded_doctor_name_is_a_word_the_agent_throws_away():
    """agent/doctor_request._FILLER removes words that can never be part of a doctor's name. If one of this clinic's
    doctors were called one of them, that doctor could never be booked by name -- so it is checked against the seed."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "clinic-api"))
    try:
        import seed
    finally:
        sys.path.remove(os.path.join(REPO_ROOT, "clinic-api"))
    words = set()
    for _dept, docs in seed.DEPARTMENTS.items():
        for name, _q in docs:
            latin, bn, hi = seed.doctor_full_names().get(name, ("", "", ""))
            for full in (latin, bn, hi):
                words.update(w.strip(".,").lower() for w in full.split() if w.strip(".,").lower() not in ("dr",))
            surname = name.split()[-1]
            words.update(a.lower() for a in seed.SURNAME_BN.get(surname, []) + seed.SURNAME_HI.get(surname, []))
    clashes = sorted(w for w in words if w in _FILLER)
    assert not clashes, clashes
    for name, _q in (d for docs in seed.DEPARTMENTS.values() for d in docs):
        assert names_a_doctor(name), name


@pytest.mark.parametrize(
    "text,doctor",
    [
        ("আমার নাম রাহুল বসু, শুক্রবার ডাক্তার দাসের অ্যাপয়েন্টমেন্ট চাই", "Dr. D. Das"),  # patient Basu, doctor Das
        ("patient rahul das, doctor ghosh friday", "Dr. P. Ghosh"),
        ("मेरा नाम अमित सेन है, डॉक्टर दास से मिलना है", "Dr. D. Das"),
    ],
)
def test_a_patient_surname_in_the_same_sentence_does_not_compete_with_the_doctor_said_as_doctor(client, text, doctor):
    _body, names = mentioned(client, text)
    assert names == [doctor]


@pytest.mark.parametrize(
    "text,doctor",
    [
        ("ডাক্তার পলাশ মিত্র কবে বসেন?", "Dr. R. Mitra"),  # live: answered with Dr. Rina Mitra's hours
        ("doctor palash mitra", "Dr. R. Mitra"),
    ],
)
def test_title_then_another_first_name_then_an_everyday_surname_is_asked_about(client, text, doctor):
    body, names = mentioned(client, text)
    assert names == [doctor]
    assert body["found"] is False and body["needs_confirmation"] is True


def test_title_then_the_right_first_name_then_an_everyday_surname_is_that_doctor(client):
    body, names = mentioned(client, "ডাক্তার রিনা মিত্রের কাছে দেখাব")
    assert names == ["Dr. R. Mitra"] and body["found"] is True
