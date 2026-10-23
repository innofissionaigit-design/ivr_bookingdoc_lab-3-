"""A test said by its NAME alone ("ইউরিন", "ওয়াইডাল"), or with "পরীক্ষা" for "test", is the same test as with "টেস্ট".

Live call, 2026-10-22: "আমি ইউরিন ওয়াইডাল আর ডেঙ্গু এনএসওয়ান করাব কত পড়বে" was read back as Widal + Dengue NS1 -- the urine test was
heard and silently dropped. The seed's alias is "ইউরিন টেস্ট"; the bare name scored 0.625 against it (under the 0.72 floor) while
"ওয়াইডাল" scored 0.727 against "ওয়াইডাল টেস্ট" -- over it by accident of length. And "পরীক্ষা" was not a generic test word in the
matcher at all.

    python -m pytest tests/test_bare_test_names.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent.entity_grounding import ground_tests  # noqa: E402

URINE, WIDAL, NS1, ECHO = "Urine Routine Examination", "Widal Test", "Dengue NS1 Antigen", "2D Echocardiography"


@pytest.fixture(scope="module")
def catalogue():
    from _clinic_app import clinic_app

    from agent.fast_path import Catalogue

    with clinic_app(sample_patients=False) as (_app, client):
        return Catalogue(client.get("/api/v1/catalogue").json())


def names(catalogue, text, lang="bn"):
    return list(ground_tests(text, lang, catalogue, []).names)


def test_the_call_that_dropped_urine(catalogue):
    assert set(names(catalogue, "আমি ইউরিন ওয়াইডাল আর ডেঙ্গু এনএসওয়ান করাব কত পড়বে")) == {URINE, WIDAL, NS1}


def test_bare_names_alone(catalogue):
    assert names(catalogue, "ইউরিন করাব") == [URINE]
    assert names(catalogue, "ইকো করাব") == [ECHO]
    assert names(catalogue, "ওয়াইডাল করাব") == [WIDAL]


@pytest.mark.parametrize("word", ["পরীক্ষা", "পরীক্ষার", "টেস্ট"])
def test_porikkha_is_test(catalogue, word):
    assert names(catalogue, f"ওয়াইডাল {word} করাব") == [WIDAL]
    assert names(catalogue, f"ডেঙ্গু এনএস১ {word} করাব") == [NS1]
    assert set(names(catalogue, f"ইউরিন {word} ওয়াইডাল {word} করাব")) == {URINE, WIDAL}


def test_all_tests_is_not_urine(catalogue):
    # "সব পরীক্ষা" ("every test") used to ground to the urine test: "পরীক্ষা" scored against "প্রস্রাব পরীক্ষা"
    assert names(catalogue, "সব পরীক্ষা") == []


def test_a_sound_alike_bare_name_is_not_the_test(catalogue):
    # uric is one letter from urine, "my" scores 0.73 against malaria: a bare name must be spelled exactly (or be a long name)
    assert names(catalogue, "hello यूरिक", "hi") == []
    assert names(catalogue, "मेरा नाम राजू है", "hi") == []
    assert names(catalogue, "मेरा शुगर 250 है", "hi") == []
    assert names(catalogue, "ज़हर खा लिया", "hi") == []


def test_a_group_word_stays_a_group(catalogue):
    # said bare these name a KIND of test (several tests), which the group net answers -- never one test of the kind
    for text in ("ডেঙ্গু করাব", "লিভার করাব", "কিডনি করাব"):
        assert names(catalogue, text) == [], text


def test_switch_off_restores_whole_form_scoring(monkeypatch):
    from _clinic_app import clinic_app

    import agent.fast_path as fp

    monkeypatch.setattr(fp, "TEST_BARE_NAME", False)
    with clinic_app(sample_patients=False) as (_app, client):
        cat = fp.Catalogue(client.get("/api/v1/catalogue").json())
    assert URINE not in ground_tests("আমি ইউরিন ওয়াইডাল করাব", "bn", cat, []).names
