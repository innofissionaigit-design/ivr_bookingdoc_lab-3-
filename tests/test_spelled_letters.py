"""Acronyms spelled letter by letter ("H C V", "H B S A G", "H I V"), read from the English recogniser's text of the same audio.

Live 2026-10-16: "আমি আপনাদের ওখানে এইচসিভি, এইচবিএসএজি আর এইচআইভি টেস্ট করাবো, মোট কত পড়বে" was written by the Bengali recogniser as
"…হপ হসিগ আর হিব টেস্ট…" (it folds letter names into pseudo-words; no decoder setting recovers them), while the English recogniser,
on the same audio, wrote "h c p h b s a g r h i v". The texts below are what the pods's recognisers actually returned for four
recordings of one caller; the sentences that must give NOTHING are ordinary English.

    python -m pytest tests/test_spelled_letters.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent.spelled_letters import acronym_table, spelled_tests  # noqa: E402

HCV, HBSAG, HIV, ECG, CBC = "HCV", "HBsAg", "HIV Test (ELISA)", "ECG", "Complete Blood Count (CBC)"


@pytest.fixture(scope="module")
def tests_():
    from _clinic_app import clinic_app

    with clinic_app(sample_patients=False) as (_app, client):
        return client.get("/api/v1/catalogue").json()["tests"]


@pytest.fixture(scope="module")
def table(tests_):
    return acronym_table(tests_)


# ----------------------------------------------------------------------------------------------- the table
def test_the_table_is_read_from_the_test_names(table):
    for letters, name in (("hcv", HCV), ("hbsag", HBSAG), ("hiv", HIV), ("ecg", ECG), ("cbc", CBC),
                          ("esr", "ESR"), ("tsh", "TSH"), ("kft", "Kidney Function Test (KFT)")):
        assert table.get(letters) == name, letters


def test_an_acronym_two_tests_share_is_not_guessed(table):
    assert "usg" not in table  # USG Whole Abdomen / USG Pregnancy Profile


# ------------------------------------------------------------- what the English recogniser actually wrote for the real clip
@pytest.mark.parametrize(
    "english, expected",
    [
        # the caller's clip, as read at normal speed, normalised, slowed to 0.8x and to 0.65x
        ("ah mayapna lukanay h c p h b s a g r h i v test portable mot potapa pow", [HCV, HBSAG, HIV]),
        ("ah may updo khana h c v h b s a g r h i v test portable motpot", [HCV, HBSAG, HIV]),
        ("amiapnaukani h cv hiv test for above motor powder", [HCV, HIV]),
        ("um mayapadokane a c v h v s a g r h a v test horrible motoporov", [HCV, HBSAG]),  # "h a v": HCV or HIV -> left unread
        # other live English attempts of the same caller
        ("i want to do h cv and ec test what will be the price", [HCV, ECG]),
        ("i want to know h c v and ec test", [HCV, ECG]),
        ("i'd like to know the price of ec test", [ECG]),
        ("i want to do hbsgh hcv and hiv test", [HCV, HIV]),
        ("i mean up another organic c b c areas are called a boat", [CBC]),
        ("e c g test price", [ECG]),
        ("price of e c g and c b c", [ECG, CBC]),
    ],
)
def test_the_letters_the_english_recogniser_wrote_give_their_tests(table, english, expected):
    assert spelled_tests(english, table) == expected


@pytest.mark.parametrize(
    "english",
    [
        "which tests are there", "what is the area of the clinic", "are you free to do the test", "i want to do the blood test",
        "is it at the end", "to be or not to be", "can i do the x ray there", "what are the heart tests",
        "i am at the door of the lab", "i i i am a a g", "i mean limit profile vitamin b twelve are tired",
        "amid kidney functional liver function test for a bocott to put away", "please go on", "yes", "thank you",
        "i want to know about the price of the cbc", "i have a fever and a cough", "what time do you open", "",
    ],
)
def test_ordinary_english_gives_nothing(table, english):
    got = spelled_tests(english, table)
    # "cbc" written as a word IS an acronym; everything else is nothing at all
    assert got == ([CBC] if "cbc" in english else []), (english, got)


def test_one_wrong_letter_is_forgiven_only_in_a_run_of_single_letters(table):
    assert spelled_tests("h c p", table) == [HCV]  # p for v, three single letters
    assert spelled_tests("hcp", table) == []  # one word: not a run of letters
    assert spelled_tests("h cp", table) == []  # two pieces only: nothing to correct a letter against


# ----------------------------------------------------------------------------- through the orchestrator and the clinic
@pytest.fixture
def bn_call():
    from _conversation import conversation

    from agent.fast_path import Catalogue, FastPath

    with conversation("bn") as c:
        payload = c._run(c.m._tools._client.get("/api/v1/catalogue")).json()
        c.m._fast_path = FastPath(Catalogue(payload))
        c.m._catalogue_cache["payload"] = payload
        c.m._catalogue_cache["at"] = time.monotonic()
        yield c


PSEUDO = "আমি আপনাদের ওখানে হপ হসিগ আর হিব টেস্ট করাবো মোট কত পড়বে"


def test_the_live_clip_is_read_back_with_its_three_tests_and_priced_only_after_a_yes(bn_call):
    rates = {t["test_name"]: t["rate_inr"] for t in bn_call._run(bn_call.m._tools.list_tests())["tests"]}
    bn_call.session.turn_acronyms = (HCV, HBSAG, HIV)  # what _note_spelled_letters sets from the English reading
    said = " ".join(bn_call.say(PSEUDO, "test_rate", {}))
    assert "এইচসিভি" in said and "এইচবিএসএজি" in said and "এইচআইভি" in said and "ঠিক তো" in said, said
    assert "টাকা" not in said  # nothing is priced before the yes
    bn_call.session.turn_acronyms = ()  # the next turn has its own reading
    said = " ".join(bn_call.say("হ্যাঁ", "unclear", {}))
    total = int(float(rates[HCV]) + float(rates[HBSAG]) + float(rates[HIV]))
    assert str(total) in said, said


def test_without_the_english_reading_the_pseudo_words_name_no_test(bn_call):
    said = " ".join(bn_call.say(PSEUDO, "test_rate", {}))
    assert "এইচসিভি" not in said and "টাকা" not in said


def test_the_letters_never_replace_a_test_the_caller_said_in_full(bn_call):
    bn_call.session.turn_acronyms = (HCV, ECG)
    said = " ".join(bn_call.say("আমি এইচসিভি আর ইসিজি টেস্ট করাব মোট কত", "test_rate", {}))
    assert said.count("এইচসিভি") == 1 and "ইসিজি" in said and "টাকা" not in said, said


# -------------------------------------------------- the turn's evidence: read only when the Indic reading won, never fatal
def _run(call, coro):
    return call._run(coro)


def _en_result(text):
    async def make():
        return types.SimpleNamespace(text=text, decoder_agreement=0.5, decoder_used="rnnt")

    return make()


def test_the_orchestrator_reads_the_english_text_when_bengali_won(bn_call):
    s, m = bn_call.session, bn_call.m
    s.turn_en_task = asyncio.ensure_future(_en_result("ah h c v h b s a g r h i v test"), loop=bn_call._loop)
    _run(bn_call, m._note_spelled_letters(s, "bn", types.SimpleNamespace(text=PSEUDO)))
    assert s.turn_acronyms == (HCV, HBSAG, HIV)


def test_the_english_text_is_not_read_when_english_won_or_nothing_ran(bn_call):
    s, m = bn_call.session, bn_call.m
    s.turn_en_task = asyncio.ensure_future(_en_result("h c v"), loop=bn_call._loop)
    _run(bn_call, m._note_spelled_letters(s, "en", types.SimpleNamespace(text="h c v")))
    assert s.turn_acronyms == ()
    s.turn_en_task = None
    _run(bn_call, m._note_spelled_letters(s, "bn", types.SimpleNamespace(text=PSEUDO)))
    assert s.turn_acronyms == ()


def test_a_failed_english_reading_is_not_a_failed_turn(bn_call):
    s, m = bn_call.session, bn_call.m

    async def boom():
        raise RuntimeError("english recogniser down")

    fut = asyncio.ensure_future(boom(), loop=bn_call._loop)
    s.turn_en_task = fut
    _run(bn_call, m._note_spelled_letters(s, "bn", types.SimpleNamespace(text=PSEUDO)))
    assert s.turn_acronyms == ()


def test_the_intent_model_is_given_the_tests_in_the_callers_language(bn_call):
    s, m = bn_call.session, bn_call.m
    s.turn_acronyms = (HCV, HBSAG, HIV)
    hint = m._with_spelled_hint(s, PSEUDO, "bn")
    assert hint.startswith(PSEUDO) and "এইচসিভি" in hint and "এইচবিএসএজি" in hint and "এইচআইভি" in hint
    s.turn_acronyms = ()
    assert m._with_spelled_hint(s, PSEUDO, "bn") == PSEUDO
    # a test the transcript already names is not repeated
    s.turn_acronyms = (HCV,)
    assert m._with_spelled_hint(s, "আমি এইচসিভি টেস্ট করাব", "bn") == "আমি এইচসিভি টেস্ট করাব"
