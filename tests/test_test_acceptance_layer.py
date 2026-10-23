"""Nothing is priced until the caller has heard which tests the agent thinks they said -- bn, hi and en.

The call this exists for (2026-10-08): ASR lost the names, the intent model supplied ["CBC", "Thyroid Profile"]
of its own, and the caller was quoted 1100 rupees for two tests they never asked for. They had said CBC and ECG,
which is 700. Both halves are pinned here: the invented pair must never reach a price, and the real pair must.

Every test runs through the REAL orchestrator against the REAL seeded clinic, with the clinic's own catalogue
loaded into the fast path exactly as a booted pod has it.
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

from agent.fast_path import Catalogue, FastPath  # noqa: E402
from agent.phrases import phrase  # noqa: E402
from agent.reply_templates import test_confirm_prompt as confirm_prompt  # noqa: E402
from agent.reply_templates import which_tests_prompt  # noqa: E402

LANGS = ("bn", "hi", "en")

# The transcript the recogniser actually produced -- two tests, a total, no name anywhere in it.
LIVE = {
    "bn": "হম বলছি আমি ওই কে দুটো টেস্ট করবো তোমার কত টাকা পরবে টোটাল",
    "hi": "वो दो टेस्ट कराने हैं कुल कितना लगेगा",
    "en": "um I'll do those two tests how much will it be in total",
}
# ...and what the intent model returned for it.
INVENTED = {"test_name": "CBC", "test_names": ["CBC", "Thyroid Profile"]}

# What the caller actually said.
CBC_AND_ECG = {
    "bn": "সিবিসি আর ইসিজি",
    "hi": "सीबीसी और ईसीजी",
    "en": "CBC and ECG",
}
SPOKEN = {"bn": ["সিবিসি", "ইসিজি"], "hi": ["सीबीसी", "ईसीजी"], "en": ["cbc", "ecg"]}
YES = {"bn": "হ্যাঁ", "hi": "हाँ", "en": "yes"}
NO = {"bn": "না", "hi": "नहीं", "en": "no"}
WHICH_TESTS = {
    "bn": "আপনাদের ওখানে কোন কোন ল্যাব টেস্ট হয়",
    "hi": "आपके यहाँ कौन कौन से लैब टेस्ट होते हैं",
    "en": "Which lab tests do you do?",
}
CBC_RATE, ECG_RATE, TOTAL = "400", "300", "700"
# Thyroid Profile is 700 too -- the same string as the right total -- so a wrong answer is told from a right one
# by the TOTAL the caller hears (1100 vs 700), never by that figure alone.
THYROID_RATE, WRONG_TOTAL, LIPID_RATE = "700", "1100", "650"
YES_WORDS = {
    "bn": ["হ্যাঁ", "ঠিক আছে", "আচ্ছা", "হ্যা", "ওকে"],
    "hi": ["हाँ", "ठीक है", "अच्छा", "ओके"],
    "en": ["yes", "ok", "sure", "go ahead", "yeah"],
}
NO_WORDS = {"bn": ["না", "নয়"], "hi": ["नहीं", "नही"], "en": ["no", "nope"]}


@pytest.fixture()
def call(request):
    lang = request.param
    with conversation(lang) as c:
        saved = c.m._fast_path
        c.m._fast_path = FastPath(Catalogue(c._run(c.m._tools._client.get("/api/v1/catalogue")).json()))
        try:
            yield c
        finally:
            c.m._fast_path = saved


def _lang(c):
    return {"bn": "bn", "hi": "hi", "en": "en"}[c.session.lang]


def _spoken(c):
    return [line for who, line in c._log if who.startswith("AI")]


def _price_said(c):
    return any(CBC_RATE in line or ECG_RATE in line or THYROID_RATE in line for line in _spoken(c))


pytestmark = pytest.mark.parametrize("call", LANGS, indirect=True)


# ============================================================================ the regression


def test_invented_test_names_never_reach_a_price(call):
    lang = _lang(call)
    said = call.say(LIVE[lang], "test_rate", INVENTED)
    assert said[-1] == which_tests_prompt(2, lang), said
    assert not _price_said(call), "no price may be spoken for tests the caller never named"
    assert not any(WRONG_TOTAL in line for line in _spoken(call))
    assert call.session.pending_tests is None and call.session.awaiting_tests == "test_rate"
    assert call.blocked == []


def test_the_caller_then_names_them_and_hears_the_right_total(call):
    """The whole call, end to end: the wrong 1100 becomes the right 700."""
    lang = _lang(call)
    call.say(LIVE[lang], "test_rate", INVENTED)
    said = call.say(CBC_AND_ECG[lang], "test_rate", {"test_names": ["CBC", "ECG"]})
    assert said[-1] == confirm_prompt(SPOKEN[lang], "test_rate", lang), said
    assert not _price_said(call), "still nothing priced -- the caller has not said yes yet"
    final = call.say(YES[lang], "unclear", {})[-1]
    assert CBC_RATE in final and ECG_RATE in final and TOTAL in final, final
    assert WRONG_TOTAL not in final, "the invented pair's total must never be spoken"
    assert call.session.pending_tests is None
    assert call.blocked == []


# ============================================================================ the acceptance layer


def test_a_grounded_test_is_read_back_before_any_price(call):
    lang = _lang(call)
    said = call.say(CBC_AND_ECG[lang], "test_rate", {"test_names": ["CBC", "ECG"]})
    assert said[-1].endswith("?") and said[-1] == confirm_prompt(SPOKEN[lang], "test_rate", lang)
    assert not _price_said(call)


def test_any_yes_word_prices_them(call):
    """Any of the language's own yes words, not one blessed spelling."""
    lang = _lang(call)
    for yes_word in YES_WORDS[lang]:
        call.say(CBC_AND_ECG[lang], "test_rate", {"test_names": ["CBC", "ECG"]})
        final = call.say(yes_word, "unclear", {})[-1]
        assert TOTAL in final and CBC_RATE in final, (yes_word, final)


def test_any_no_word_prices_nothing_and_asks_which(call):
    lang = _lang(call)
    for no_word in NO_WORDS[lang]:
        call.say(CBC_AND_ECG[lang], "test_rate", {"test_names": ["CBC", "ECG"]})
        said = call.say(no_word, "unclear", {})
        assert said[-1] == which_tests_prompt(None, lang), (no_word, said)
        assert call.session.awaiting_tests == "test_rate"
        assert not any(WRONG_TOTAL in line or TOTAL in line for line in _spoken(call))


def test_a_correction_at_the_read_back_is_read_back_in_turn(call):
    lang = _lang(call)
    call.say(CBC_AND_ECG[lang], "test_rate", {"test_names": ["CBC", "ECG"]})
    # Lipid Profile, not Thyroid: "থাইরয়েড" is a seeded Bengali alias but "थायराइड"/"thyroid" are not seeded for
    # Hindi/English, so grounding rightly refuses them -- a catalogue gap, tracked separately, not a bug here.
    corrected = {
        "bn": "না, সিবিসি আর লিপিড প্রোফাইল",
        "hi": "नहीं, सीबीसी और लिपिड प्रोफाइल",
        "en": "no, CBC and lipid profile",
    }[lang]
    said = call.say(corrected, "test_rate", {"test_names": ["CBC", "Lipid Profile"]})
    assert said[-1].endswith("?")
    assert not _price_said(call)
    final = call.say(YES[lang], "unclear", {})[-1]
    assert LIPID_RATE in final and CBC_RATE in final, final


def test_the_read_back_lapses_when_the_caller_moves_on(call):
    """KCD-104: a question that is neither answered nor corrected does not hold the call hostage."""
    lang = _lang(call)
    call.say(CBC_AND_ECG[lang], "test_rate", {"test_names": ["CBC", "ECG"]})
    other = {"bn": "আপনাদের ক্লিনিকটা কোথায়", "hi": "आपका क्लिनिक कहाँ है", "en": "Where is your clinic?"}[lang]
    call.say(other, "clinic_faq", {"faq_topic": "location"})
    assert call.session.pending_tests is None
    assert not _price_said(call)


# ============================================================================ the "which tests?" loop


def test_in_the_loop_asking_what_tests_exist_reads_them_and_stays(call):
    lang = _lang(call)
    call.say(LIVE[lang], "test_rate", INVENTED)
    assert call.session.awaiting_tests == "test_rate"
    said = call.say(WHICH_TESTS[lang], "test_prep", {})
    assert "400" in said[-1], "the list, with prices"
    assert call.session.awaiting_tests == "test_rate", "still in the loop"


def test_in_the_loop_naming_a_test_reads_it_back(call):
    lang = _lang(call)
    call.say(LIVE[lang], "test_rate", INVENTED)
    said = call.say(CBC_AND_ECG[lang], "test_rate", {"test_names": ["CBC", "ECG"]})
    assert said[-1] == confirm_prompt(SPOKEN[lang], "test_rate", lang)
    assert call.session.awaiting_tests is None


def test_anything_else_leaves_the_loop_for_ordinary_service(call):
    lang = _lang(call)
    call.say(LIVE[lang], "test_rate", INVENTED)
    said = call.say(NO[lang], "unclear", {})
    assert said[-1] == phrase("how_else_help", lang), said
    assert call.session.awaiting_tests is None, "never a third ask -- the loop cannot spin"
    assert not _price_said(call)


# ============================================================================ booking the same way


def test_booking_tests_is_confirmed_before_the_price_and_the_counter(call):
    lang = _lang(call)
    book = {"bn": "সিবিসি আর ইসিজি বুক করতে চাই", "hi": "सीबीसी और ईसीजी बुक करना है", "en": "book CBC and ECG"}[lang]
    said = call.say(book, "book_test", {"test_names": ["CBC", "ECG"]})
    assert said[-1] == confirm_prompt(SPOKEN[lang], "book_test", lang), said
    assert not _price_said(call)
    final = call.say(YES[lang], "unclear", {})[-1]
    assert TOTAL in final and phrase("lab_test_counter_only", lang) in final


def test_booking_with_invented_names_asks_which_instead_of_pricing(call):
    lang = _lang(call)
    said = call.say(LIVE[lang], "book_test", INVENTED)
    assert said[-1] == which_tests_prompt(2, lang), said
    assert not _price_said(call)
    assert call.session.awaiting_tests == "book_test"


# ============================================================================ what must NOT change


def test_a_test_named_in_full_still_works_in_one_yes(call):
    lang = _lang(call)
    one = {"bn": "সিবিসি টেস্টের দাম কত", "hi": "सीबीसी का रेट क्या है", "en": "how much is the CBC"}[lang]
    call.say(one, "test_rate", {"test_name": "CBC"})
    final = call.say(YES[lang], "unclear", {})[-1]
    assert CBC_RATE in final


def test_a_count_from_the_model_alone_still_makes_the_question_specific(call):
    """The deterministic count wins, but when ASR gives nothing to count, the model's test_count is used."""
    lang = _lang(call)
    vague = {"bn": "ওগুলোর দাম কত", "hi": "उनका दाम क्या है", "en": "how much for those"}[lang]
    said = call.say(vague, "test_rate", {"test_names": ["CBC", "ECG"], "test_count": 2})
    assert said[-1] == which_tests_prompt(2, lang), said
