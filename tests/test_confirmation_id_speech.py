"""A caller must be able to HEAR their confirmation number, in every language.

clinic-api issues references shaped KCD-YYYYMMDD-XXXXXXXX (eight hex digits). The
verbalisers' pattern stopped at the date group, so the hex suffix fell through to the
generic number sweep: "48B06CDE" was read "forty-eight, B, six, CDE" with the letters left
in Latin -- and Latin is unspeakable on the Bengali voice, so agent/tts.py blocked the whole
confirmation sentence. Measured 2026-10-07: 231 of 402 Bengali references blocked. The
caller who had just finished a booking heard "system busy" instead of their number.

PRONUNCIATION_ALLOW_DRAFT is forced off: this must hold on the shipped setting, not only
on a test box where the unreviewed lexicon happens to be enabled.
"""

from __future__ import annotations

import os
import random
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent.tts import unspeakable_spans, verbalize  # noqa: E402

SENTENCE = {
    "bn": "কনফার্মেশন নম্বর: {}।",
    "hi": "कन्फ़र्मेशन नंबर: {}।",
    "en": "Confirmation number: {}.",
}

# The two references seen on replayed calls, one per hex letter run, and a fixed
# pseudo-random sample -- seeded, so a failure always reproduces.
_RNG = random.Random(20261007)
REFERENCES = [
    "KCD-20261008-48B06CDE",
    "KCD-20261008-1B75BFA9",
    "KCD-20261008-8BECDDEE",
    "KCD-20261008-ABCDEF01",
    "KCD-20261008-FFFFFFFF",
    "KCD-20261008-00000000",
] + [f"KCD-20261008-{_RNG.getrandbits(32):08X}" for _ in range(300)]


@pytest.fixture(autouse=True)
def _shipped_pronunciation_setting(monkeypatch):
    monkeypatch.setenv("PRONUNCIATION_ALLOW_DRAFT", "0")


@pytest.mark.parametrize("lang", ["bn", "hi", "en"])
def test_every_confirmation_number_is_speakable(lang):
    blocked = [
        ref
        for ref in REFERENCES
        if unspeakable_spans(verbalize(SENTENCE[lang].format(ref), lang), lang)
    ]
    assert blocked == [], f"{len(blocked)} of {len(REFERENCES)} blocked in {lang}, e.g. {blocked[:3]}"


@pytest.mark.parametrize(
    "lang,expected",
    [
        ("bn", "কে সি ডি , দুই শূন্য দুই ছয় , এক শূন্য শূন্য আট , চার আট বি শূন্য , ছয় সি ডি ই"),
        ("hi", "के सी डी , दो शून्य दो छह , एक शून्य शून्य आठ , चार आठ बी शून्य , छह सी डी ई"),
    ],
)
def test_the_suffix_is_read_character_by_character_not_as_a_number(lang, expected):
    """"48B06CDE" is four-eight-B-zero..., never "forty-eight": it is written down, not counted."""
    spoken = verbalize(SENTENCE[lang].format("KCD-20261008-48B06CDE"), lang)
    assert expected in spoken
