"""Live 2026-10-13: "আপনাদের ওখানে যাব কীভাবে?" -- "how do I get to you?" -- was not answered with the address.

Three things stacked (measured on the pod):

  1. the fast path matched the location FAQ only for "কীভাবে যাব" / "কীভাবে আসব" -- one word order, one spelling. People
     say "যাব কীভাবে", and the Bengali recogniser writes "কিভাবে" or "কি ভাবে": 0.60-0.63 against a floor of 0.72, so it
     abstained and the model called the caller unrelated;
  2. "ক্লিনিক" is in the hours keyword "ক্লিনিকের সময়": "আপনাদের ক্লিনিকে কিভাবে যেতে হবে" was answered with the OPENING HOURS;
  3. the model's wrong answer was then cached for the exact text (not changed here; see the report).

This file pins the fixes -- the spelling fold and the two-word reversal (location, Bengali and Hindi only), the new
phrasings, the clinic-vs-hours guard -- and, as much, what they must NOT start doing: answering a booking statement
("কাল আসব", "আসব কি?") with the address.

    python -m pytest tests/test_howto_get_there.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent.fast_path import Catalogue, FastPath  # noqa: E402


@pytest.fixture(scope="module")
def fast_path():
    from _clinic_app import clinic_app

    with clinic_app(sample_patients=False) as (app, client):
        payload = client.get("/api/v1/catalogue").json()
    return FastPath(Catalogue(payload))


def _answer(fp, said, lang=None):
    lang = lang or ("hi" if any("ऀ" <= c <= "ॿ" for c in said) else "bn")
    r = fp.resolve(said, lang)
    return None if r is None else (r.intent, r.slots.get("faq_topic"))


LOCATION = ("clinic_faq", "location")
HOURS = ("clinic_faq", "hours")


# ========================================================= the question, said every way a caller says it
@pytest.mark.parametrize(
    "said",
    [
        "আপনাদের ওখানে যাব কীভাবে?",  # the live sentence, as typed
        "আপনাদের ওখানে যাব কিভাবে",  # as the recogniser writes it
        "আপনাদের ওখানেই যাব কি ভাবে",  # what it actually heard on the pod
        "আপনাদের ওখানে কীভাবে যাব",  # the one word order that always worked
        "আপনাদের ওখানে কিভাবে যাব",
        "ওখানে যাব কিভাবে",
        "যাব কিভাবে",
        "কিভাবে যাব",
        "আপনাদের এখানে আসব কিভাবে",
        "আসব কীভাবে",
        "কীভাবে আসব",
        "আপনাদের ক্লিনিকে কিভাবে যেতে হবে",
        "আপনাদের ক্লিনিকে যাব কিভাবে",
        "ক্লিনিকে যেতে হবে কীভাবে",
        "আপনাদের ক্লিনিকে কীভাবে আসতে হবে",
        "ঠিকানা কী",  # the original keywords are untouched
        "আপনাদের ক্লিনিক কোথায়",
    ],
)
def test_how_do_i_get_there_is_the_address(fast_path, said):
    assert _answer(fast_path, said) == LOCATION


@pytest.mark.parametrize(
    "said",
    ["आपके क्लिनिक कैसे जाएँ", "मैं कैसे आऊं", "कैसे जाऊँ", "क्लिनिक कैसे पहुँचें", "आपका पता क्या है"],
)
def test_the_same_question_in_hindi(fast_path, said):
    assert _answer(fast_path, said) == LOCATION


# ========================================================= the hours are still the hours
@pytest.mark.parametrize(
    "said",
    ["ক্লিনিকের সময় কত", "ক্লিনিকে কটার সময় যেতে পারি", "ক্লিনিক কখন খোলে", "আপনাদের ক্লিনিক কখন খোলা থাকে",
     "ক্লিনিক রবিবার খোলা থাকে কি", "आपका क्लिनिक कब खुलता है"],
)
def test_a_question_about_being_open_is_not_taken_for_the_way(fast_path, said):
    got = _answer(fast_path, said)
    assert got != LOCATION  # a miss (the model) is allowed, the address is not


def test_the_clinic_word_alone_no_longer_carries_a_way_question_to_the_hours(fast_path):
    assert _answer(fast_path, "আপনাদের ক্লিনিকে যাব কিভাবে") == LOCATION
    assert _answer(fast_path, "ক্লিনিকে যাওয়ার রাস্তা কোনটা") == LOCATION
    assert _answer(fast_path, "ক্লিনিকের সময় কত") == HOURS


# ========================================================= what must NOT become the address
@pytest.mark.parametrize(
    "said",
    [
        "কাল আসব", "কাল যাব", "পরশু আসব", "আজ আসব", "আমি কাল আসতে চাই", "কাল যেতে পারব না", "যাব কাল", "আসব কি",
        "আমি কাল আসব ডাক্তার দেখাতে", "আমার মা কাল আসবেন", "রাস্তা বন্ধ", "কোনটা সস্তা", "সস্তা কোনটা", "আসতে দেরি হবে",
        "কাল সকালে আসব", "কালকে আসব", "দাসের কাছে যাব",
        "मुझे कल आना है", "मैं कल आऊं", "कल जाना है", "मैं जाऊं", "रास्ता बंद है",
    ],
)
def test_a_statement_about_coming_is_not_answered_with_the_address(fast_path, said):
    assert _answer(fast_path, said) != LOCATION


def test_the_old_false_address_answers_are_gone(fast_path):
    """Before the how-word guard these three were answered with the clinic's address."""
    for said in ("কাল সকালে আসব", "কালকে আসব", "দাসের কাছে যাব"):
        assert _answer(fast_path, said) is None


# ========================================================= nothing else about the FAQ table changed
@pytest.mark.parametrize(
    "said,expected",
    [
        ("পার্কিং আছে কি", ("clinic_faq", "parking")),
        ("ইনসুরেন্স চলে কি", ("clinic_faq", "insurance")),
        ("রিপোর্ট কীভাবে পাব", ("clinic_faq", "report_collection")),
        ("রিপোর্ট কোথায় পাব", ("clinic_faq", "report_collection")),
        ("টাকা কীভাবে দেব", ("clinic_faq", "payment_methods")),
        ("ফোন নম্বর কত", ("clinic_faq", "contact_number")),
    ],
)
def test_other_faq_topics_keep_their_answers(fast_path, said, expected):
    assert _answer(fast_path, said) == expected


# ========================================================= the keywords reach a database that already exists
def test_the_new_keywords_are_appended_to_an_existing_database():
    """clinic-api's backfill_faq_keywords adds seed keywords to rows that are already there (the pod's clinic.db), so
    the phrasings reach the pod with a clinic-api restart, no re-seed."""
    from _clinic_app import clinic_app

    with clinic_app(sample_patients=False) as (app, client):
        import booking_migrate as bm
        import db as clinic_db
        import models

        session = clinic_db.SessionLocal()
        try:
            row = session.query(models.FAQ).filter_by(topic="location").one()
            row.keywords_bn = "কোথায়|ঠিকানা|কীভাবে আসব|কীভাবে যাব"  # a database from before this change
            session.commit()
        finally:
            session.close()
        assert bm.backfill_faq_keywords() >= 1
        topics = {t["topic"]: t for t in client.get("/api/v1/catalogue").json()["faq_topics"]}
        kws = topics["location"]["keywords_bn"]
        assert "যাব কীভাবে" in kws and "কীভাবে যেতে হবে" in kws
        assert kws[:4] == ["কোথায়", "ঠিকানা", "কীভাবে আসব", "কীভাবে যাব"]  # appended, never reordered


# ========================================================= 2026-10-13: "is there a landmark near the clinic?"
@pytest.mark.parametrize(
    "said",
    [
        "ক্লিনিকের কাছাকাছি কোন ল্যান্ডমার্ক আছে",  # the live sentence: was answered with the opening HOURS
        "ক্লিনিকের কাছাকাছি কোন ল্যান্ডমার্ক আছে?",
        "আপনাদের ক্লিনিকের আশেপাশে কোন ল্যান্ডমার্ক আছে",
        "কাছাকাছি কোনো ল্যান্ডমার্ক আছে কি",
        "ল্যান্ডমার্ক কী",
        "ক্লিনিকের কাছে কোনো ল্যান্ড মার্ক আছে",
        "ক্লিনিকের ল্যান্ডমার্কটা কী",
        "কাছাকাছি কী আছে",
        "আশেপাশে কী আছে",
        "ক্লিনিক কোন এলাকায়",
        "আপনাদের ক্লিনিক কোন রাস্তায়",
        "ক্লিনিকের কাছাকাছি মেট্রো স্টেশন কোনটা",
        "आपके क्लिनिक के पास कोई लैंडमार्क है",
        "लैंडमार्क क्या है",
        "क्लिनिक किस इलाके में है",
    ],
)
def test_a_landmark_or_neighbourhood_question_is_the_address(fast_path, said):
    assert _answer(fast_path, said) == LOCATION


@pytest.mark.parametrize(
    "said",
    [
        "দশটার কাছাকাছি সময়ে আসব", "কাছাকাছি সময়ে দেখা হবে", "কাছাকাছি কোন ডাক্তার আছেন", "এলাকায় কোন ডাক্তার আছেন",
        "কাছাকাছি হাসপাতাল কোনটা", "আমার এলাকায় ডাক্তার নেই", "সব এলাকায় কি আপনাদের ব্রাঞ্চ আছে", "রোডে জ্যাম আছে দেরি হবে",
        "মেট্রো বন্ধ ছিল দেরি হবে", "आसपास कोई डॉक्टर है", "इलाके में कोई डॉक्टर है",
    ],
)
def test_a_sentence_that_only_shares_a_place_word_is_not_the_address(fast_path, said):
    assert _answer(fast_path, said) != LOCATION


def test_the_time_around_which_someone_comes_is_not_the_address(fast_path):
    """A hours word with a case ending ("সময়ে") counts: "দশটার কাছাকাছি সময়ে আসব" was relabelled to the address by the
    clinic-vs-hours guard, which only knew the bare "সময়"."""
    assert _answer(fast_path, "দশটার কাছাকাছি সময়ে আসব") != LOCATION
    assert _answer(fast_path, "কাছাকাছি সময়ে ক্লিনিক খোলা থাকে") == HOURS
