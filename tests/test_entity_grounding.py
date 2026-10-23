"""agent/entity_grounding.py -- an entity is usable only when the caller's own words support it.

The regression this file is named for is the live call of 2026-10-08: ASR lost the test names, the intent model
filled them in anyway, and the caller was quoted 1100 rupees for two tests they never asked for (they said CBC
and ECG, which is 700). Every test here runs against the REAL seeded catalogue, not a hand-written stub, so an
alias that stops existing fails these tests rather than passing them quietly.
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _clinic_app import clinic_app  # noqa: E402

from agent.entity_grounding import (  # noqa: E402
    MAX_TESTS_PER_TURN,
    extract_tests,
    ground_tests,
    spoken_count,
)
from agent.fast_path import Catalogue  # noqa: E402

CBC = "Complete Blood Count (CBC)"
ECG = "ECG"
THYROID = "Thyroid Profile (T3 T4 TSH)"

# What the recogniser actually produced on the live call -- no test name survives in it.
LIVE_2026_10_08 = "হম বলছি আমি ওই কে দুটো টেস্ট করবো তোমার কত টাকা পরবে টোটাল"
# What the caller actually said.
CBC_AND_ECG = {
    "bn": "সিবিসি আর ইসিজি টেস্ট করবো কত টাকা পড়বে",
    "hi": "सीबीसी और ईसीजी टेस्ट कराने हैं कितना लगेगा",
    "en": "I want a CBC and an ECG, how much will it cost",
}


@pytest.fixture(scope="module")
def catalogue():
    with clinic_app(sample_patients=False) as (_app, client):
        yield Catalogue(client.get("/api/v1/catalogue").json())


# ============================================================================ the regression


def test_the_live_call_grounds_no_test_at_all(catalogue):
    """The whole point. Two tests are wanted, which two is unknown, and nothing may be priced."""
    g = ground_tests(LIVE_2026_10_08, "bn", catalogue, candidates=["CBC", "Thyroid Profile"])
    assert g.names == ()
    assert not g, "falsy when nothing is grounded -- the agent must ask"
    assert g.count == 2, "the caller DID say how many"
    assert set(g.dropped) == {"CBC", "Thyroid Profile"}, "the model's invented pair, reported and unused"


def test_a_bare_test_word_never_grounds_a_test(catalogue):
    for text in ("দুটো টেস্ট করবো", "टेस्ट कराना है", "I need a test", "টেস্টের দাম কত"):
        assert extract_tests(text, "bn", catalogue)[0] == (), text


# ============================================================================ grounding what WAS said


@pytest.mark.parametrize("lang", ("bn", "hi", "en"))
def test_the_two_tests_the_caller_really_named_are_grounded_in_order(catalogue, lang):
    g = ground_tests(CBC_AND_ECG[lang], lang, catalogue, candidates=["CBC", "Thyroid Profile"])
    assert g.names == (CBC, ECG), g
    assert g.dropped == ("Thyroid Profile",), "the one the caller never said is still dropped"
    assert g, "truthy when something is grounded"


@pytest.mark.parametrize(
    ("text", "lang", "expected"),
    [
        ("সিবিসি", "bn", (CBC,)),
        ("ইসিজি করাতে হবে", "bn", (ECG,)),
        ("থাইরয়েড টেস্টের দাম কত", "bn", (THYROID,)),
        ("सीबीसी का रेट क्या है", "hi", (CBC,)),
        ("ईसीजी", "hi", (ECG,)),
        ("how much is the CBC", "en", (CBC,)),
        ("ECG price", "en", (ECG,)),
    ],
)
def test_a_single_named_test_grounds(catalogue, text, lang, expected):
    assert extract_tests(text, lang, catalogue)[0] == expected


def test_a_name_said_twice_is_grounded_once(catalogue):
    assert extract_tests("সিবিসি আর সিবিসি", "bn", catalogue)[0] == (CBC,)


def test_no_more_than_the_cap_is_ever_returned(catalogue):
    many = "সিবিসি আর ইসিজি আর থাইরয়েড টেস্ট আর লিপিড প্রোফাইল আর ইউরিন টেস্ট আর টিএসএইচ"
    assert len(extract_tests(many, "bn", catalogue)[0]) <= MAX_TESTS_PER_TURN


def test_a_near_miss_below_the_commit_floor_does_not_ground(catalogue):
    """Nonsense that shares a few characters with a real name must stay unknown, not become a priced test."""
    assert extract_tests("আমার ডিবিডি লাগবে", "bn", catalogue)[0] == ()


def test_the_form_returned_is_the_word_the_caller_actually_used(catalogue):
    """The read-back says the caller's own alias back to them, never the Latin catalogue key (which the Bengali
    and Hindi voices drop outright)."""
    names, forms, exact = extract_tests("সিবিসি আর ইসিজি", "bn", catalogue)
    assert names == (CBC, ECG)
    assert forms == ("সিবিসি", "ইসিজি")
    assert exact is True


# ============================================================================ candidates


def test_a_candidate_naming_a_grounded_test_in_english_is_not_dropped(catalogue):
    """The model answers with the catalogue's English name whatever language the call is in."""
    g = ground_tests("সিবিসি টেস্টের দাম কত", "bn", catalogue, candidates=["CBC"])
    assert g.names == (CBC,) and g.dropped == ()


def test_with_no_catalogue_nothing_grounds_and_everything_is_dropped(catalogue):
    """clinic-api was down at boot: nothing can vouch for a name, so the agent asks. Fail closed."""
    g = ground_tests("সিবিসি আর ইসিজি", "bn", None, candidates=["CBC", "ECG"])
    assert g.names == () and set(g.dropped) == {"CBC", "ECG"}


# ============================================================================ count, separate from names


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("দুটো টেস্ট করবো", 2), ("দুটি টেস্ট", 2), ("তিনটে টেস্ট করাব", 3), ("একটা টেস্ট", 1),
        ("দো टेस्ट", None), ("दो टेस्ट कराने हैं", 2), ("तीन जांच", 3),
        ("two tests please", 2), ("I need three tests", 3), ("2 tests", 2),
        ("দুটো রক্তের টেস্ট", 2),
        # not about tests, and so not a count of tests
        ("দুটো সময় বলুন", None), ("দশটা বাজে", None), ("two o'clock", None), ("", None),
        # a number too far from the word for test is some other number
        ("দুটো ডাক্তার দেখিয়ে তারপর কালকে একদিন টেস্ট", None),
    ],
)
def test_spoken_count_counts_only_tests(text, expected):
    assert spoken_count(text, "bn") == expected


def test_count_and_names_are_independent_facts(catalogue):
    """"দুটো টেস্ট: সিবিসি আর ইসিজি" -- both are known, and neither is derived from the other."""
    g = ground_tests("দুটো টেস্ট করবো, সিবিসি আর ইসিজি", "bn", catalogue)
    assert g.count == 2 and g.names == (CBC, ECG)
