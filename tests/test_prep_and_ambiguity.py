"""Fourth live-call findings (2026-09-25):

* "blood sugar preparation" (no "fasting", no "PP") was answered for the PP test: the fast path took the closest form
  and never asked whether another test fit as well. Now it abstains, and the model's turn reaches the clinic API,
  which answers "which one -- Blood Sugar Fasting or Blood Sugar PP?" (KCD-446).
* the preparation answer comes from the lab-test table: its text, else its fasting_required column -- a test the table
  marks as needing fasting is never told "no special preparation" because the text column was left empty.

  python -m pytest tests/test_prep_and_ambiguity.py -v
"""

import datetime
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.fast_path import AMBIGUITY_MARGIN, Catalogue, FastPath
from agent.reply_templates import test_prep_reply as prep_reply  # (not test_*: pytest would collect it)

TODAY = datetime.date(2026, 9, 25)


@pytest.fixture(scope="module")
def fp():
    from _clinic_app import clinic_app

    with clinic_app(sample_patients=False) as (_app, client):
        return FastPath(Catalogue(client.get("/api/v1/catalogue").json()), today=TODAY)


@pytest.mark.parametrize(
    "text", ["blood sugar preparation", "blood sugar test rate", "what is the blood sugar test price"]
)
def test_a_name_that_two_tests_fit_is_not_picked_between(fp, text):
    assert fp.resolve(text, "en") is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("how much is a cbc", "cbc"),
        ("kidney function test price", "kidney function test"),
        ("lipid profile rate", "lipid profile"),
    ],
)
def test_a_name_that_is_one_tests_is_still_served_with_no_model(fp, text, expected):
    hit = fp.resolve(text, "en")
    assert hit is not None and hit.slots["test_name"] == expected


def test_the_margin_is_a_stated_number():
    assert 0.0 < AMBIGUITY_MARGIN < 0.5


def test_the_clinic_api_asks_which_one_when_two_tests_fit(fp):
    from _clinic_app import clinic_app

    with clinic_app(sample_patients=False) as (_app, client):
        body = client.get("/api/v1/tests/prep", params={"name": "blood sugar", "lang": "en"}).json()
    assert body["found"] is False and body["ambiguous"] is True
    assert set(body["did_you_mean"]) == {"Blood Sugar Fasting", "Blood Sugar PP"}


@pytest.mark.parametrize("lang,needle", [("bn", "উপবাস"), ("hi", "खाली पेट"), ("en", "Fasting is required")])
def test_a_test_the_table_marks_as_fasting_is_never_told_no_preparation_needed(lang, needle):
    reply = prep_reply(
        {"test_name": "Uric Acid"},
        {"found": True, "test_name": "Uric Acid", "fasting_required": True, "prep_instructions": ""},
        lang,
    )
    assert needle in reply and "No special" not in reply and "বিশেষ কোনো" not in reply and "ख़ास" not in reply


@pytest.mark.parametrize(
    "lang,needle", [("bn", "বিশেষ কোনো প্রস্তুতির প্রয়োজন নেই"), ("hi", "ख़ास तैयारी"), ("en", "No special preparation")]
)
def test_a_test_the_table_marks_as_no_fasting_with_no_text_says_so(lang, needle):
    reply = prep_reply(
        {"test_name": "X"}, {"found": True, "test_name": "X", "fasting_required": False, "prep_instructions": ""}, lang
    )
    assert needle in reply


def test_the_tables_own_text_is_spoken_when_it_has_one():
    reply = prep_reply(
        {"test_name": "X"},
        {"found": True, "test_name": "X", "fasting_required": True, "prep_instructions": "Do not eat for eight hours."},
        "en",
    )
    assert "Do not eat for eight hours." in reply
