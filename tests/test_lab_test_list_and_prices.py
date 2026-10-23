"""story: "Caller asks which lab tests are available", and the price of several -- Bengali, Hindi and English.

Live 2026-10-07: "আপনাদের ওখানে কোন কোন ল্যাব টেস্ট হয়" came back `test_prep` with no test named, and the caller was
asked "are you a registered patient, or is this test booked?". Now: the tests are read a few at a time, each with the
clinic's price, "shall I go on?" between pages. A caller booking one or several tests hears each price and their
total (the clinic's figures and their sum), then that tests are booked and paid for at the counter.
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

from agent.phrases import phrase  # noqa: E402
from agent.reply_templates import test_list_page as list_page  # noqa: E402
from agent.reply_templates_i18n import TEST_LIST_PAGE, _rupees  # noqa: E402
from agent.test_list_request import asks_for_test_list, wants_more  # noqa: E402

LANGS = ("bn", "hi", "en")
WHICH_TESTS = {
    "bn": "আপনাদের ওখানে কোন কোন ল্যাব টেস্ট হয়",
    "hi": "आपके यहाँ कौन कौन से लैब टेस्ट होते हैं",
    "en": "Which lab tests do you do?",
}
MORE = {"bn": "আরও বলুন", "hi": "और बताइए", "en": "go on"}
NO = {"bn": "না", "hi": "नहीं", "en": "no"}
BOOK_TWO = {
    "bn": ("সিবিসি আর লিপিড প্রোফাইল বুক করতে চাই", ["CBC", "Lipid Profile"]),
    "hi": ("सीबीसी और लिपिड प्रोफाइल टेस्ट बुक करना है", ["CBC", "Lipid Profile"]),
    "en": ("I want to book CBC and lipid profile", ["CBC", "Lipid Profile"]),
}
REGISTERED_QUESTION = {lang: phrase("test_prep_ask_registered", lang) for lang in LANGS}
YES = {"bn": "হ্যাঁ", "hi": "हाँ", "en": "yes"}


def _with_catalogue(call):
    """The clinic's own catalogue in the fast path, as a booted pod has it -- what grounds a test name."""
    from agent.fast_path import Catalogue, FastPath

    call.m._fast_path = FastPath(Catalogue(call._run(call.m._tools._client.get("/api/v1/catalogue")).json()))


def _listing(call) -> dict:
    return call._run(call.m._tools.list_tests())


@pytest.mark.parametrize("lang", LANGS)
def test_which_tests_reads_the_first_few_with_the_clinics_prices(lang):
    with conversation(lang) as call:
        said = call.say(WHICH_TESTS[lang], "test_prep", {})  # the model's live label
        listing = _listing(call)
        assert said[-1] == list_page(listing, 0, lang)[0], said
        assert str(listing["count"]) in said[-1]
        for t in listing["tests"][:TEST_LIST_PAGE]:
            assert _rupees(t["rate_inr"]) in said[-1]
        assert not any(REGISTERED_QUESTION[lang] in line for line in said)
        assert call.session.test_list_offset == TEST_LIST_PAGE
        assert call.blocked == []


@pytest.mark.parametrize("lang", LANGS)
def test_more_reads_the_next_few_and_no_ends_the_list(lang):
    with conversation(lang) as call:
        call.say(WHICH_TESTS[lang], "test_prep", {})
        said = call.say(MORE[lang], "smalltalk", {})
        assert said[-1] == list_page(_listing(call), TEST_LIST_PAGE, lang)[0], said
        call.say(NO[lang], "unclear", {})
        assert call.session.test_list_offset is None
        assert call.blocked == []


@pytest.mark.parametrize("lang", LANGS)
def test_the_last_page_says_that_was_all(lang):
    with conversation(lang) as call:
        listing = _listing(call)
        last = (listing["count"] - 1) // TEST_LIST_PAGE * TEST_LIST_PAGE
        reply, following = list_page(listing, last, lang)
        assert following is None
        assert _rupees(listing["tests"][-1]["rate_inr"]) in reply


@pytest.mark.parametrize("lang", LANGS)
def test_booking_several_tests_gives_each_price_the_total_and_the_counter(lang):
    text, names = BOOK_TWO[lang]
    with conversation(lang) as call:
        _with_catalogue(call)
        # Which tests is settled first (the acceptance layer, agent/entity_grounding.py): nothing is priced until
        # the caller has heard them read back and said yes.
        read_back = call.say(text, "book_test", {"test_names": names})[-1]
        assert read_back.endswith("?"), read_back
        said = call.say(YES[lang], "unclear", {})
        rates = [call._run(call.m._tools.get_test_rate(n))["rate_inr"] for n in names]
        reply = " ".join(said)
        for rate in rates:
            assert _rupees(rate) in reply, reply
        assert _rupees(sum(rates)) in reply, "the total"
        assert phrase("lab_test_counter_only", lang) in reply
        assert call.session.booking is None, "no booking is started -- tests are booked at the counter"
        assert call.blocked == []


@pytest.mark.parametrize("lang", LANGS)
def test_asking_the_price_of_several_tests_gives_their_total(lang):
    text, names = BOOK_TWO[lang]
    with conversation(lang) as call:
        _with_catalogue(call)
        assert call.say(text, "test_rate", {"test_names": names})[-1].endswith("?"), "read back first"
        said = call.say(YES[lang], "unclear", {})
        rates = [call._run(call.m._tools.get_test_rate(n))["rate_inr"] for n in names]
        assert _rupees(sum(rates)) in said[-1], said


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("আপনাদের ওখানে কোন কোন ল্যাব টেস্ট হয়", True), ("কী কী টেস্ট করা যায় আর দাম কত", True),
        ("টেস্টের লিস্টটা বলবেন", True), ("क्या क्या जांच होती है", True), ("टेस्ट की लिस्ट बताइए", True),
        ("What tests are available and their prices?", True), ("Can you give me the list of tests", True),
        ("সিবিসি টেস্টের দাম কত", False), ("কোন টেস্টটা করতে হবে", False), ("আমার টেস্টের রিপোর্ট কবে পাব", False),
        ("सीबीसी टेस्ट का रेट क्या है", False), ("How much is the CBC test?", False), ("Which test do I need for sugar?", False),
    ],
)
def test_a_list_request_is_told_from_a_single_test_question(text, expected):
    assert asks_for_test_list(text) is expected


def test_hindi_and_is_not_more():
    assert wants_more("और बताइए") and wants_more("और")
    assert not wants_more("सीबीसी और थायराइड टेस्ट बुक करना है")


def test_the_clinic_lists_every_test_with_its_rate():
    with conversation("en") as call:
        listing = _listing(call)
        catalogue = call._run(call.m._tools._client.get("/api/v1/catalogue")).json()
        assert listing["count"] == len(listing["tests"]) == len(catalogue["tests"])
        for t in listing["tests"]:
            one = call._run(call.m._tools.get_test_rate(t["test_name"]))
            assert one["rate_inr"] == t["rate_inr"], t["test_name"]
