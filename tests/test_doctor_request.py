"""agent/doctor_request.py: does the "doctor" slot name anybody?

The live call of 2026-10-06 is the reason this exists: the model put "একজন ডক্টর" ("a
doctor") in doctor_name, it was treated as a name, and the caller was told three
questions later that nobody by that name worked here. Every phrase below was either
said on a real call or is the same request in another of the three languages.
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent.doctor_request import name_tokens, names_a_doctor  # noqa: E402

NAMES_NOBODY = [
    # Bengali -- the first two are verbatim from the live call
    "একজন ডক্টর",
    "ডাক্তার",
    "ডক্টর",
    "আমি একজন ডক্টর দেখাব",
    "ডাক্তার দেখাবো",
    "কোনো ভালো ডাক্তার",
    # Hindi
    "डॉक्टर",
    "कोई डॉक्टर",
    "मुझे डॉक्टर दिखाना है",
    "एक अच्छा डॉक्टर",
    # English
    "a doctor",
    "any doctor",
    "I want to see a doctor",
    "doctor please",
    # bare initials identify nobody: "a doctor" once offered every "Dr. A."
    "Dr. A.",
    "",
    None,
]

NAMES_SOMEONE = [
    "সেন",
    "অরিন্দম সেন",
    "ডক্টর সেন",
    "ডাক্তার ভট্টাচার্য",
    "डॉक्टर सेन",
    "Dr. A. Sen",
    "Sen",
    "Dr Mukherjee",
    # a KIND of doctor is not a name, but it is not "nobody" either: it must be looked up
    # (clinic-api resolves it to a department), never discarded.
    "হার্টের ডাক্তার",
    "दिल का डॉक्टर",
    "heart doctor",
    "cardiology",
]


@pytest.mark.parametrize("said", NAMES_NOBODY)
def test_a_request_for_any_doctor_names_nobody(said):
    assert names_a_doctor(said) is False


@pytest.mark.parametrize("said", NAMES_SOMEONE)
def test_a_name_or_a_kind_of_doctor_is_kept_for_lookup(said):
    assert names_a_doctor(said) is True


def test_an_unknown_word_is_kept_not_thrown_away():
    """Subtractive by design: a word this module does not recognise survives, so an
    unusual surname is looked up rather than silently discarded."""
    assert name_tokens("ডাক্তার জগন্নাথ") == ["জগন্নাথ"]
    assert names_a_doctor("Dr. Xylophone") is True


def test_the_surname_survives_beside_an_initial():
    """The article "a" and the initial "A." are the same token to this tokenizer, so the initial is
    dropped along with it -- harmless, because the surname is what identifies the doctor and it is
    kept. (clinic-api still matches the full "Dr. A. Sen"; this only decides whether a name exists.)"""
    assert name_tokens("Dr. A. Sen") == ["sen"]
    assert names_a_doctor("A. Sen") is True
