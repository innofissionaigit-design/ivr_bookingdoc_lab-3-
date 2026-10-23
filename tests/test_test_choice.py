"""Two tests that fit the words about equally well are ASKED about, never picked; the tests already clear stay on the list;
a test added to a read-back is added, not put in its place; and a second test in a sentence is no longer lost when the
first was matched by a near spelling. All from live calls of 2026-10-16:

  * "ক্রিয়েটিনিন ইউরিক্যাসিড আর পসা"      -> uric acid was never read (the walk stopped after creatinine)
  * "ইউরিক্যাসিড টিএসএ আর পিএসইচ"         -> TSH 0.83 / PSA 0.80 and TSH 0.77 / PSA 0.73: TSH was picked, PSA lost
  * "সঙ্গে কিডনি ফাংশনও জানতে চাইছি"       -> the read-back [ECG, LFT] was REPLACED by [KFT]

    python -m pytest tests/test_test_choice.py -v
"""

from __future__ import annotations

import os
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent import entity_grounding as eg  # noqa: E402
from agent.entity_grounding import ground_tests  # noqa: E402
from agent.reply_templates_i18n import test_choice_prompt as choice_prompt  # noqa: E402

TSH, PSA = "TSH", "PSA (Prostate Specific Antigen)"
URIC, CREAT, KFT, LFT, ECG = "Uric Acid", "Serum Creatinine", "Kidney Function Test (KFT)", "Liver Function Test (LFT)", "ECG"
B12, VITD = "Vitamin B12", "Vitamin D (25-OH)"


@pytest.fixture(scope="module")
def catalogue():
    from _clinic_app import clinic_app

    from agent.fast_path import Catalogue

    with clinic_app(sample_patients=False) as (_app, client):
        return Catalogue(client.get("/api/v1/catalogue").json())


def g(catalogue, text, lang="bn"):
    return ground_tests(text, lang, catalogue, [])


# ------------------------------------------------------------------------------------------- the walk no longer stops
def test_a_second_test_is_not_lost_when_the_first_was_matched_by_a_near_spelling(catalogue):
    # "ক্রিয়েটিনিন" is said; the catalogue spells it "ক্রিয়াটিনিন". The words could not be cut out by the spelling, the walk stopped.
    got = g(catalogue, "আমি আপনাদের ওখানে ক্রিয়েটিনিন ইউরিক্যাসিড আর পসা করব মোট কত টাকা")
    assert CREAT in got.names and URIC in got.names, got


def test_the_walk_fix_can_be_switched_off(catalogue, monkeypatch):
    monkeypatch.setattr(eg, "TEST_WALK_FIX", False)
    got = g(catalogue, "আমি আপনাদের ওখানে ক্রিয়েটিনিন ইউরিক্যাসিড আর করব")
    assert URIC not in got.names  # the earlier behaviour, exactly


# ------------------------------------------------------------------------------------------------- what is asked, and when
def test_two_acronyms_that_fit_the_same_two_tests_become_one_both_question(catalogue):
    got = g(catalogue, "আমি ইউরিক্যাসিড টিএসএ আর পিএসইচ করাব মোট কত পড়বে")
    assert got.names == (URIC,)
    assert got.choices == ((TSH, PSA), (TSH, PSA)) or got.choices == ((PSA, TSH), (PSA, TSH)), got.choices


def test_one_close_pair_is_asked_and_the_clear_tests_are_kept(catalogue):
    got = g(catalogue, "আমি ক্রিয়েটিনে নিউরিক অ্যাসিড আর টিএসএ করব মোট কত টাকা")
    assert set(got.names) == {URIC, CREAT} and len(got.choices) == 1 and set(got.choices[0]) == {TSH, PSA}


def test_a_test_named_clearly_elsewhere_is_not_a_second_possibility(catalogue):
    # TSH said in full; the other near-TSH word can then only be PSA, and it is read back like any inexact match
    got = g(catalogue, "আমি টিএসএইচ আর টিএসএ করব")
    assert set(got.names) == {TSH, PSA} and got.choices == ()


def test_a_clear_pair_is_never_asked(catalogue):
    got = g(catalogue, "আমি টিএসএইচ আর পিএসএ করব")
    assert set(got.names) == {TSH, PSA} and got.choices == ()


@pytest.mark.parametrize(
    "said, expected",
    [
        ("ভিটামিন বি টুয়েলভ কত", B12),
        ("ভিটামিন বি ১২ টেস্ট", B12),
        ("ভিটামিন বি কমপ্লেক্স টেস্ট", B12),
        ("ব্লাড সুগার ফাস্টিং", "Blood Sugar Fasting"),
    ],
)
def test_names_that_differ_after_a_shared_first_word_are_told_apart_by_what_follows(catalogue, said, expected):
    got = g(catalogue, said)
    assert got.names == (expected,) and got.choices == (), got


@pytest.mark.parametrize("said", ["ভিটামিন টেস্ট", "ভিটামিন পরীক্ষা", "হেপাটাইটিস টেস্ট"])
def test_a_kind_of_test_is_asked_not_picked(catalogue, said):
    got = g(catalogue, said)
    assert got.names == () and len(got.choices) == 1, got


def test_the_question_can_be_switched_off(catalogue, monkeypatch):
    monkeypatch.setattr(eg, "TEST_CHOICE_ASK", False)
    got = g(catalogue, "আমি ইউরিক্যাসিড টিএসএ আর পিএসইচ করাব মোট কত পড়বে")
    assert got.choices == () and TSH in got.names  # the earlier behaviour: the best score, picked


# ---------------------------------------------------------------------------------------------------------- the words
@pytest.mark.parametrize("lang", ["bn", "hi", "en"])
def test_the_question_is_asked_in_the_callers_language(lang):
    out = choice_prompt(["A1", "A2"], ["B1", "B2"], False, lang)
    assert "A1" in out and "A2" in out and "B1" in out and "B2" in out and out.count("?") == 1
    both = choice_prompt([], ["B1", "B2"], True, lang)
    assert "B1" in both and "B2" in both and both.count("?") == 1
    assert choice_prompt(["A1"], ["B1", "B2"], False, lang, reasked=True).count("?") >= 1


# ------------------------------------------------------------------------------- through the orchestrator and the clinic
@pytest.fixture
def call():
    from _conversation import conversation

    from agent.fast_path import Catalogue, FastPath

    with conversation("bn") as c:
        payload = c._run(c.m._tools._client.get("/api/v1/catalogue")).json()
        c.m._fast_path = FastPath(Catalogue(payload))
        c.m._catalogue_cache["payload"] = payload
        c.m._catalogue_cache["at"] = time.monotonic()
        yield c


def _rates(call):
    return {t["test_name"]: float(t["rate_inr"]) for t in call._run(call.m._tools.list_tests())["tests"]}


def _say(call, text):
    return " ".join(call.say(text, "test_rate", {"test_names": []}))


def test_the_recording_asks_once_about_both_keeps_uric_acid_and_prices_only_after_the_yes(call):
    r = _rates(call)
    out = _say(call, "আমি ইউরিক্যাসিড টিএসএ আর পিএসইচ করাব মোট কত পড়বে")
    assert "ইউরিক অ্যাসিড" in out and "টিএসএইচ" in out and "পিএসএ" in out and "দুটোই" in out and "টাকা" not in out, out
    out = _say(call, "হ্যাঁ")  # both
    assert "ইউরিক অ্যাসিড" in out and "টিএসএইচ" in out and "পিএসএ" in out and "ঠিক তো" in out and "টাকা" not in out, out
    out = _say(call, "হ্যাঁ")
    assert str(int(r[URIC] + r[TSH] + r[PSA])) in out, out


def test_one_close_pair_the_caller_picks_and_the_others_are_kept(call):
    r = _rates(call)
    out = _say(call, "আমি ক্রিয়েটিনে নিউরিক অ্যাসিড আর টিএসএ করব মোট কত টাকা")
    assert "নাকি" in out and "টিএসএইচ" in out and "পিএসএ" in out and "টাকা" not in out, out
    out = _say(call, "পিএসএ")
    assert "পিএসএ" in out and "ঠিক তো" in out and "টিএসএইচ" not in out, out
    out = _say(call, "হ্যাঁ")
    assert str(int(r[URIC] + r[CREAT] + r[PSA])) in out, out


def test_neither_drops_the_doubt_and_keeps_what_was_clear(call):
    out = _say(call, "আমি ক্রিয়েটিনে নিউরিক অ্যাসিড আর টিএসএ করব মোট কত টাকা")
    assert "নাকি" in out
    out = _say(call, "না")
    assert "ঠিক তো" in out and "ইউরিক অ্যাসিড" in out and "পিএসএ" not in out and "টিএসএইচ" not in out, out


def test_a_bare_yes_to_a_which_question_asks_once_more_and_then_falls_back(call):
    out = _say(call, "আমি ক্রিয়েটিনে নিউরিক অ্যাসিড আর টিএসএ করব মোট কত টাকা")
    assert "নাকি" in out
    out = _say(call, "হ্যাঁ")
    assert "নামটা" in out and "টাকা" not in out, out
    out = _say(call, "হ্যাঁ")
    assert "ঠিক তো" in out and "পিএসএ" not in out, out


def test_a_test_added_to_a_read_back_is_added_not_put_in_its_place(call):
    out = _say(call, "ইসিজি আর লিভার ফাংশন")
    assert "ইসিজি" in out and "লিভার ফাংশন" in out and "ঠিক তো" in out
    out = _say(call, "সঙ্গে কিডনি ফাংশনও জানতে চাইছি")
    assert "ইসিজি" in out and "লিভার ফাংশন" in out and "কিডনি ফাংশন" in out and "ঠিক তো" in out, out


def test_naming_the_same_tests_again_still_replaces_the_list(call):
    _say(call, "ইসিজি আর লিভার ফাংশন")
    out = _say(call, "ইসিজি আর কিডনি ফাংশন")  # overlaps: a correction
    assert "কিডনি ফাংশন" in out and "লিভার ফাংশন" not in out, out


def test_a_clear_no_with_a_test_still_replaces_the_list(call):
    _say(call, "ইসিজি আর লিভার ফাংশন")
    out = _say(call, "না, কিডনি ফাংশন")
    assert "কিডনি ফাংশন" in out and "ইসিজি" not in out and "লিভার ফাংশন" not in out, out


def test_adding_can_be_switched_off(call, monkeypatch):
    monkeypatch.setattr(call.m, "TEST_ADD_ON", False)
    _say(call, "ইসিজি আর লিভার ফাংশন")
    out = _say(call, "সঙ্গে কিডনি ফাংশনও জানতে চাইছি")
    assert "কিডনি ফাংশন" in out and "ইসিজি" not in out, out  # the earlier behaviour: replaced


# =============================================================================== the 2026-10-21 call (44ab3f3a), turn by turn
HIV, HCV, HBSAG, HBA1C = "HIV Test (ELISA)", "HCV", "HBsAg", "HbA1c"


def test_acronyms_said_back_to_back_are_split_into_the_three_tests(catalogue):
    # turn 2: "এইচআইবিএইচসিভিএইচবিএসজি" -- HIV, HCV, HBsAg with no pause, one word, each a letter off or exact
    got = g(catalogue, "আমি এইচআইবিএইচসিভিএইচবিএসজি করব কত পড়বে")
    assert got.names == (HIV, HBSAG, HCV) or set(got.names) == {HIV, HCV, HBSAG}, got
    assert got.choices == ()


def test_an_ordinary_long_word_is_not_split(catalogue):
    got = g(catalogue, "আপনাদের ওখানে পরীক্ষা করানোর ব্যবস্থা আছে কিনা")
    assert got.names == () and got.choices == ()


def test_glued_acronym_splitting_can_be_switched_off(catalogue, monkeypatch):
    monkeypatch.setattr(eg, "TEST_GLUE_SPLIT", False)
    got = g(catalogue, "আমি এইচআইবিএইচসিভিএইচবিএসজি করব কত পড়বে")
    assert len(got.names) < 3


def test_the_bare_acronym_hiv_is_a_test(catalogue):
    assert g(catalogue, "আমি এইচআইভি করব").names == (HIV,)


def test_split_glued_and_closest_of_and_edit_distance_are_pure():
    from agent.spelled_letters import closest_of, edit_distance, letters_of, split_glued

    assert edit_distance("hbsg", "hbsag") == 1 and edit_distance("", "ab") == 2 and edit_distance("x", "x") == 0
    assert letters_of("H B  S-G!") == "hbsg" and letters_of("এইচ বি এস জি") == "এইচবিএসজি"
    assert letters_of("কিডনি") == "কিডনি"  # vowel signs are part of the letters
    assert split_glued("এইচআইবিএইচসিভিএইচবিএসজি", ["এইচআইভি", "এইচসিভি", "এইচবিএসএজি"]) == ["এইচআইভি", "এইচসিভি", "এইচবিএসএজি"]
    assert split_glued("আপনাদের", ["এইচআইভি", "এইচসিভি"]) is None
    opts = {"HBsAg": ["hbsag", "এইচবিএসএজি"], "HbA1c": ["hba1c", "এইচবিএ১সি"]}
    assert closest_of("h b s g", opts) == "HBsAg"  # no "a": one letter short
    assert closest_of("এইচবিএসজি", opts) == "HBsAg"
    assert closest_of("hello", opts) is None
    assert closest_of("h b", opts) is None  # too short to tell


def test_a_low_agreement_turn_that_names_tests_is_read_back_not_withheld(call):
    call.session.turn_acronyms = (HIV,)  # the English reading spelled h-i-v
    out = " ".join(call.say("আমি এইচআইভি এইচসিভি আর এইচবিএসসি করাব কত পড়বে", "test_rate", {"test_names": []}, agreement=0.38))
    assert "শুনতে পাইনি" not in out, out
    assert "এইচআইভি" in out and "এইচসিভি" in out and "নাকি" in out and "টাকা" not in out, out


def test_withholding_on_low_agreement_can_be_restored(call, monkeypatch):
    monkeypatch.setattr(call.m, "TEST_GATE_READBACK", False)
    call.session.turn_acronyms = (HIV,)
    out = " ".join(call.say("আমি এইচআইভি এইচসিভি আর এইচবিএসসি করাব কত পড়বে", "test_rate", {"test_names": []}, agreement=0.38))
    assert "শুনতে পাইনি" in out, out  # the earlier behaviour


def _ask_hiv_hcv_and_a_close_pair(call):
    call.session.turn_acronyms = (HIV,)
    return " ".join(call.say("আমি এইচআইভি এইচসিভি আর এইচবিএসসি করাব কত পড়বে", "test_rate", {"test_names": []}, agreement=0.38))


def test_the_answer_h_b_s_g_in_english_letters_picks_hbsag_and_keeps_hiv_and_hcv(call):
    r = _rates(call)
    out = _ask_hiv_hcv_and_a_close_pair(call)
    assert "নাকি" in out
    call.session.turn_acronyms = ()
    call.m._lock_reply_language(call.session, "bn")  # the call is fixed to its first sentence's language ...
    call._state["lang"] = "en"  # ... and the English recogniser wins this turn
    out = " ".join(call.say("h b s g", "smalltalk", {}, agreement=0.6))
    assert "হ্যালো" not in out and "কেমন আছেন" not in out, out
    assert "এইচআইভি" in out and "এইচসিভি" in out and "এইচবিএসএজি" in out and "ঠিক তো" in out, out
    call._state["lang"] = "bn"
    out = " ".join(call.say("হ্যাঁ", "unclear", {}))
    assert str(int(r[HIV] + r[HCV] + r[HBSAG])) in out, out


def test_the_answer_in_bengali_letters_also_picks(call):
    _ask_hiv_hcv_and_a_close_pair(call)
    call.session.turn_acronyms = ()
    out = " ".join(call.say("এইচবিএসজি", "unclear", {}))
    assert "এইচবিএসএজি" in out and "ঠিক তো" in out and "এইচআইভি" in out and "এইচসিভি" in out, out


def test_an_answer_that_is_not_understood_is_asked_again_once_and_the_tests_are_kept(call):
    _ask_hiv_hcv_and_a_close_pair(call)
    call.session.turn_acronyms = ()
    out = " ".join(call.say("ওই যে ওটা", "smalltalk", {}))
    assert "নামটা" in out and "হ্যালো" not in out, out
    out = " ".join(call.say("কিছু একটা", "smalltalk", {}))  # still not an answer: the doubt is dropped, the clear ones read back
    assert "এইচআইভি" in out and "এইচসিভি" in out and "ঠিক তো" in out, out
