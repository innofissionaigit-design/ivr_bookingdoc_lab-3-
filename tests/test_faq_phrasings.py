"""Clinic FAQ questions as callers actually ask them, Bengali, Hindi and English -> the topic the caller hears.

Live 2026-10-07: "আপনাদের ওখানে কখন খোলা থাকবে" and "বলছি কখন খোলা আপনাদের ক্লিনিক" were answered "I didn't
understand" -- the keyword path knew "কখন খোলে" but not "কখন খোলা", and the model filed both `unclear`. Replaying every
topic in three languages on the pod found more: "What are your timings?" and "Is the clinic open today?" were answered
with the ADDRESS (a fuzzy hit on "clinic"), and "গাড়ি কোথায় রাখব" -- a parking keyword, word for word -- also got the
address, because "কোথায়" tied it and location comes first in the table.

What the caller hears is the keyword path's answer when it gives one (it answers without the model), else -- when
the model cannot place the sentence -- the same keyword table read after it (main.py's _faq_topic_said; Bengali
"কখন ..." is held back from the keyword path by tests/test_fast_path_languages.py). Each phrasing must reach its own
topic that way, worst case: the model filed it `unclear`. Built from a freshly seeded clinic database.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _clinic_app import clinic_app  # noqa: E402
from _conversation import conversation  # noqa: E402

from agent.fast_path import Catalogue, FastPath  # noqa: E402

ASKED = {
    "hours": {
        "bn": ["আপনাদের ওখানে কখন খোলা থাকবে", "বলছি কখন খোলা আপনাদের ক্লিনিক", "ক্লিনিক কটায় খোলে", "কটা পর্যন্ত খোলা থাকে",
               "রবিবার খোলা থাকে", "আজকে খোলা আছে", "ক্লিনিক কখন বন্ধ হয়", "আপনাদের টাইমিং কী"],
        "hi": ["आपका क्लिनिक कब खुलता है", "क्लिनिक कितने बजे तक खुला रहता है", "रविवार को खुला है क्या", "आज खुला है",
               "आपकी टाइमिंग क्या है", "क्लिनिक कब बंद होता है"],
        "en": ["When are you open?", "What time do you open?", "Are you open on Sunday?", "What are your timings?",
               "Till what time is the clinic open?", "Is the clinic open today?"],
    },
    "location": {
        "bn": ["আপনাদের ক্লিনিকটা কোথায়", "ঠিকানাটা বলবেন", "কীভাবে যাব আপনাদের ওখানে", "ক্লিনিকটা কোন জায়গায়"],
        "hi": ["आपका क्लिनिक कहाँ है", "पता बताइए", "वहाँ कैसे पहुँचें", "क्लिनिक किस जगह पर है"],
        "en": ["Where is your clinic?", "What is your address?", "How do I reach you?", "Where are you located?"],
    },
    "payment_methods": {
        "bn": ["কার্ডে পেমেন্ট করা যাবে", "ইউপিআই চলে", "টাকা কীভাবে দেব", "অনলাইনে পেমেন্ট করা যায়"],
        "hi": ["यूपीआई चलता है", "पेमेंट कैसे करना है"],
        "en": ["Can I pay by card?", "Do you take UPI?", "How can I pay?", "Do you accept online payment?"],
    },
    "insurance": {
        "bn": ["ইনসিওরেন্স চলে", "মেডিক্লেম নেন", "ক্যাশলেস সুবিধা আছে"],
        "hi": ["इंश्योरेंस चलता है", "मेडिक्लेम लेते हैं", "कैशलेस सुविधा है"],
        "en": ["Do you accept insurance?", "Is cashless available?", "Do you take mediclaim?"],
    },
    "parking": {
        "bn": ["গাড়ি রাখার জায়গা আছে", "পার্কিং আছে", "গাড়ি কোথায় রাখব"],
        "hi": ["पार्किंग है क्या", "गाड़ी कहाँ खड़ी करें", "गाड़ी रखने की जगह है"],
        "en": ["Is there parking?", "Where can I park my car?", "Do you have parking?"],
    },
    "report_collection": {
        "bn": ["রিপোর্ট কীভাবে পাব", "রিপোর্ট কি হোয়াটসঅ্যাপে পাঠান", "রিপোর্ট নিতে কোথায় যাব"],
        "hi": ["रिपोर्ट कैसे मिलेगी", "रिपोर्ट व्हाट्सएप पर भेजते हैं", "रिपोर्ट कहाँ से लें"],
        "en": ["How will I get my report?", "Do you send reports on WhatsApp?", "Where do I collect the report?"],
    },
    "contact_number": {
        "bn": ["আপনাদের ফোন নম্বরটা কী", "যোগাযোগের নম্বর দিন", "হেল্পলাইন নম্বর কত"],
        "hi": ["आपका फोन नंबर क्या है", "संपर्क नंबर दीजिए", "हेल्पलाइन नंबर क्या है"],
        "en": ["What is your phone number?", "Give me your contact number", "What is the helpline number?"],
    },
    "home_collection": {
        "bn": ["বাড়িতে এসে স্যাম্পল নেন", "হোম কালেকশন হয়", "বাড়ি থেকে রক্ত নিতে আসবেন"],
        "hi": ["घर से सैंपल लेते हैं", "होम कलेक्शन होता है", "घर आकर खून लेंगे"],
        "en": ["Do you collect samples from home?", "Is home collection available?", "Can someone come home to take blood?"],
    },
}
CASES = [(topic, lang, q) for topic, by_lang in ASKED.items() for lang, qs in by_lang.items() for q in qs]

# Questions that are NOT the clinic's FAQ and must not be answered as one by the keyword path.
NOT_FAQ = [
    ("en", "What time is my appointment?"),
    ("bn", "ডাক্তার সেন কবে বসেন"),
    ("hi", "सीबीसी टेस्ट का रेट क्या है"),
]


@pytest.fixture(scope="module")
def agent():
    """The orchestrator module with the clinic's real keyword table loaded, as a booted pod has it."""
    with conversation("bn") as call:
        saved = call.m._fast_path
        call.m._fast_path = FastPath(Catalogue(call._run(call.m._tools._client.get("/api/v1/catalogue")).json()))
        try:
            yield call.m
        finally:
            call.m._fast_path = saved


@pytest.fixture(scope="module")
def fast_path(agent):
    return agent._fast_path


@pytest.mark.parametrize(("topic", "lang", "question"), CASES)
def test_each_phrasing_reaches_its_own_topic(agent, topic, lang, question):
    hit = agent._fast_path.resolve(question, lang)
    if hit is not None:
        got = hit.as_llm_shape()["slots"].get("faq_topic") if hit.intent == "clinic_faq" else hit.intent
    else:
        got = agent._faq_topic_said(question, lang)  # after the model, which filed it `unclear`
    assert got == topic, f"{question!r} -> {got}"


@pytest.mark.parametrize("question", ["আপনাদের ওখানে কখন খোলা থাকবে", "বলছি কখন খোলা আপনাদের ক্লিনিক"])
def test_the_live_call_hears_the_opening_hours(question):
    with conversation("bn") as call:
        saved = call.m._fast_path
        try:
            call.m._fast_path = FastPath(Catalogue(call._run(call.m._tools._client.get("/api/v1/catalogue")).json()))
            said = call.say(question, "unclear", {})
            hours = call._run(call.m._tools.get_faq("hours", "bn"))["answer"]
            assert said[-1] == hours, said
            assert call.blocked == []
        finally:
            call.m._fast_path = saved


def test_an_unknown_doctors_hours_are_never_the_clinics(agent):
    assert agent._faq_topic_said("ডাক্তার রায় কখন বসেন", "bn") is None
    assert agent._faq_topic_said("When is doctor Roy available, what time?", "en") is None


@pytest.mark.parametrize(("lang", "question"), NOT_FAQ)
def test_other_questions_are_not_answered_as_a_faq(fast_path, lang, question):
    hit = fast_path.resolve(question, lang)
    assert hit is None or hit.intent != "clinic_faq", f"{question!r} -> {hit.as_llm_shape()}"


def test_an_existing_database_gets_the_new_keywords_and_keeps_its_own():
    """The pod's clinic.db was seeded before these keywords existed; seed.backfill_i18n fills a keyword column only
    while it is EMPTY. booking_migrate.backfill_faq_keywords appends what is missing at startup, keeping every
    keyword already there -- including one added by hand."""
    with clinic_app(sample_patients=False) as (_app, client):
        db_path = os.environ["CLINIC_DB_PATH"]
        con = sqlite3.connect(db_path)
        con.execute("UPDATE faqs SET keywords_bn = 'সময়|হাতে লেখা', keywords_en = 'opening hours' WHERE topic = 'hours'")
        con.commit()
        con.close()
        import booking_migrate

        assert booking_migrate.backfill_faq_keywords() >= 1
        con = sqlite3.connect(db_path)
        kw_bn, kw_en = con.execute("SELECT keywords_bn, keywords_en FROM faqs WHERE topic = 'hours'").fetchone()
        con.close()
        assert kw_bn.split("|")[:2] == ["সময়", "হাতে লেখা"], "what was there is kept, in its order"
        assert "কখন খোলা" in kw_bn.split("|")
        assert "when are you open" in kw_en.split("|")
        assert booking_migrate.backfill_faq_keywords() == 0, "idempotent"
