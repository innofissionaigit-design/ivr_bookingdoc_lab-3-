"""The clinic's own name is us, not something to look up -- Bengali, Hindi and English.

Live call, 2026-10-08. The agent opened with "আপনি সোনোস্ক্যান বাণীর সঙ্গে কথা বলছেন", the caller said that name
back while asking for the address, and the agent answered "দুঃখিত, 'সোনস্কান' নামে কোনো টেস্ট আমাদের তালিকায় নেই" --
it had gone looking for a lab test called Sonoscan. Two gaps behind it: nothing knew the name belonged to the
agent, and a turn filed as a test question never fell back to the clinic's own FAQ.
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

from agent.clinic_identity import names_the_clinic, strip_clinic_name  # noqa: E402
from agent.entity_grounding import extract_tests  # noqa: E402
from agent.fast_path import Catalogue, FastPath  # noqa: E402

LANGS = ("bn", "hi", "en")
CBC = "Complete Blood Count (CBC)"

# The live turn, and the model's reading of it: our own name, filed as a lab test.
ADDRESS = {
    "bn": ("সোনস্কানের অযাড্ড্রেস টা কি", "সোনস্কান"),
    "hi": ("सोनोस्कैन का पता क्या है", "सोनोस्कैन"),
    "en": ("What is Sonoscan's address", "Sonoscan"),
}
HOURS = {
    "bn": "সোনোস্ক্যান কখন খোলা থাকে",
    "hi": "सोनोस्कैन कब खुलता है",
    "en": "When is Sonoscan open",
}
REAL_TEST = {
    "bn": ("সিবিসি টেস্টের দাম কত", "CBC"),
    "hi": ("सीबीसी का रेट क्या है", "CBC"),
    "en": ("how much is the CBC", "CBC"),
}


@pytest.fixture()
def call(request):
    with conversation(request.param) as c:
        saved = c.m._fast_path
        c.m._fast_path = FastPath(Catalogue(c._run(c.m._tools._client.get("/api/v1/catalogue")).json()))
        try:
            yield c
        finally:
            c.m._fast_path = saved


def _spoken(c):
    return [line for who, line in c._log if who.startswith("AI")]


# ============================================================================ the name itself


@pytest.mark.parametrize(
    "text",
    [
        "সোনস্কানের অযাড্ড্রেস টা কি", "সোনোস্ক্যান বাণীর ঠিকানা", "সনোস্কান কোথায়",
        "सोनोस्कैन का पता", "सोनस्कान कहाँ है",
        "What is Sonoscan address", "is this sonoscan", "SONOSCAN VAANI",
    ],
)
def test_the_clinics_own_name_is_recognised(text):
    assert names_the_clinic(text)


@pytest.mark.parametrize(
    "text", ["সিবিসি টেস্টের দাম", "রোগীর নাম বাণী", "ডাক্তার সেন কবে বসেন", "sono", "scan my report"]
)
def test_other_words_are_not_the_clinics_name(text):
    assert not names_the_clinic(text)
    assert strip_clinic_name(text) == text


def test_the_patients_own_name_is_never_stripped():
    """"বাণী" is an ordinary word and a woman's name. Stripping it would delete a patient."""
    assert strip_clinic_name("রোগীর নাম বাণী") == "রোগীর নাম বাণী"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("সোনস্কানের অযাড্ড্রেস টা কি", "অযাড্ড্রেস টা কি"),
        ("সোনোস্ক্যান কখন খোলা থাকে", "কখন খোলা থাকে"),
        ("What is Sonoscan's address", "What is 's address"),
        ("सोनोस्कैन कब खुलता है", "कब खुलता है"),
    ],
)
def test_the_name_is_taken_out_and_the_question_is_left(text, expected):
    assert strip_clinic_name(text) == expected


def test_stripping_the_name_does_not_disturb_a_real_test_in_the_same_sentence():
    with conversation("bn") as c:
        cat = Catalogue(c._run(c.m._tools._client.get("/api/v1/catalogue")).json())
        assert extract_tests("সোনস্কানের সিবিসি টেস্ট", "bn", cat)[0] == (CBC,)


# ============================================================================ the call


_each_language = pytest.mark.parametrize("call", LANGS, indirect=True)


@_each_language
def test_asking_the_clinics_address_by_its_name_gets_the_address(call):
    lang = call.session.lang
    text, as_test = ADDRESS[lang]
    said = call.say(text, "test_rate", {"test_name": as_test})
    expected = call._run(call.m._tools.get_faq("location", lang))["answer"]
    assert said[-1] == expected, said
    assert not any("টেস্ট" in line and "নেই" in line for line in _spoken(call)), "never 'no such test'"
    assert call.session.awaiting_tests is None, "not the which-tests loop either -- it was never a test question"
    assert call.blocked == []


@_each_language
def test_asking_the_hours_by_the_clinics_name_gets_the_hours(call):
    lang = call.session.lang
    said = call.say(HOURS[lang], "unclear", {})
    assert said[-1] == call._run(call.m._tools.get_faq("hours", lang))["answer"], said


@_each_language
def test_a_real_test_question_is_still_a_test_question(call):
    """The FAQ fallback must not swallow the thing it sits behind."""
    lang = call.session.lang
    text, name = REAL_TEST[lang]
    said = call.say(text, "test_rate", {"test_name": name})
    assert said[-1].endswith("?"), said
    assert call.session.pending_tests is not None, "read back for confirmation, as any test question is"
