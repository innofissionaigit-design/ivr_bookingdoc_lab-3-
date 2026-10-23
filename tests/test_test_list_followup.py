"""The "shall I go on?" offer of the test list survives a turn nobody could place, and understands "more" in a hundred
ways -- Bengali / Hindi script, English, and Bengali / Hindi typed in Latin letters.

Live 2026-10-13: after four pages of the list the caller's "পরের টেস্টগুলো বলুন" was heard as "প্রডেক্ট গুলো বলুন"; the offer
lapsed, the model said "I did not understand", and a clear "পরেরগুলো বলুন" two turns later found no list to continue.

    python -m pytest tests/test_test_list_followup.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent.test_list_request import asks_for_test_list, wants_more, wants_repeat  # noqa: E402

MORE = [
    # Bengali script
    "আরও শুনব", "আরো বলুন", "আরও কিছু আছে?", "আর কী কী আছে", "পরের টেস্টগুলো বলুন", "পরের টেস্ট গুলো বলুন",
    "পরেরগুলো বলুন", "পরেরটা বলুন", "এরপর কী কী আছে", "তারপর বলুন", "বাকিগুলো বলুন", "বাকি টেস্ট গুলো", "বাকিটা বলুন",
    "অন্যগুলো বলুন", "বলে যান", "চালিয়ে যান", "বলতে থাকুন", "শুনতে চাই", "শুনি", "সবগুলো বলুন", "পুরো তালিকা বলুন",
    "নেক্সট বলুন", "পরবর্তীগুলো বলুন", "আরেকটু বলুন", "বলুন",
    # Hindi script
    "और बताइए", "और बताओ", "और सुनाइए", "और भी बताओ", "आगे बताइए", "आगे बताओ", "अगले वाले बताइए", "अगले टेस्ट बताओ",
    "बाकी के टेस्ट बताओ", "बाकी बताइए", "जारी रखिए", "बताते जाइए", "और कौन कौन से हैं", "और", "सुनाइए", "बताइए",
    "नेक्स्ट बताओ", "पूरी लिस्ट बताओ",
    # English
    "more", "next", "the next ones please", "go on", "go ahead", "continue", "carry on", "keep going", "the rest",
    "what else", "anything else", "tell me more", "read more", "next five", "what's next", "show me more", "any more tests",
    # Latin letters, Bengali
    "aro bolun", "aaro bolun", "aro bolen", "aro kichu bolun", "aro ki ki ache", "porer gulo bolun", "porergulo bolun",
    "porer testgulo bolun", "porer ta bolun", "er por ki ki ache", "tarpor bolun", "baki gulo bolun", "bakigulo", "baaki gulo",
    "bakita bolun", "onno gulo bolun", "bole jan", "bolte thakun", "chalie jan", "shunbo", "ami aro shunte chai", "bolun",
    "next ta bolun", "sob gulo bolun", "arekta bolun", "arektu bolun",
    # Latin letters, Hindi
    "aur batao", "aur bataiye", "aur sunao", "aur bhi batao", "aur kya kya hai", "aage batao", "aage bataiye", "agle wale batao",
    "agle test batao", "baaki wale batao", "baki sab batao", "batate jao", "batate jaiye", "poori list batao", "next batao",
    "saare batao",
]

NOT_MORE = [
    # a question of its own
    "cbc test er daam taa jano koto bolechilen", "সিবিসি টেস্টের দাম কত", "সিবিসি টেস্টের দামটা কত বলেছিলেন", "তারপর বুক করব",
    "আরও একটা টেস্ট বুক করতে চাই", "ডাক্তার সেন কখন বসেন", "আপনাদের ঠিকানা কী", "পার্কিং আছে", "aro ekta test book korbo",
    "baki test er daam koto", "aur ek test book karna hai", "सीबीसी और थायराइड बुक करना है", "what is the price of cbc",
    "do you have parking", "book an appointment",
    # not a follow-up at all
    "ধন্যবাদ", "thank you", "", "   ", "hello", "না",
]

REPEAT = [
    "আবার বলুন", "আরেকবার বলুন", "আবার বলবেন", "আরেক বার", "কী বললেন", "কি বলেছিলেন", "কত বলেছিলেন", "শুনতে পাইনি",
    "শুনিনি", "রিপিট", "दोबारा बताइए", "फिर से बताओ", "एक बार और", "क्या कहा", "कितना बताया था", "say that again", "repeat",
    "can you repeat that", "once more", "what did you say", "how much did you say", "abar bolun", "arekbar bolun",
    "ki bollen", "koto bolechilen", "shunte pai ni", "dobara batao", "phir se batao", "kya bataya", "kitna bataya",
]


@pytest.mark.parametrize("said", MORE)
def test_a_follow_up_to_go_on_is_recognised(said):
    assert wants_more(said) is True, said


@pytest.mark.parametrize("said", NOT_MORE)
def test_a_question_or_an_unrelated_word_is_not_a_go_on(said):
    assert wants_more(said) is False, said


@pytest.mark.parametrize("said", REPEAT)
def test_say_that_again_is_recognised(said):
    assert wants_repeat(said) is True, said


@pytest.mark.parametrize("said", ["পরের টেস্টগুলো বলুন", "aro bolun", "aur batao", "next", "বাকিগুলো বলুন"])
def test_go_on_is_not_taken_for_say_that_again(said):
    assert wants_repeat(said) is False, said


def test_the_list_request_itself_is_unchanged():
    assert asks_for_test_list("আপনাদের ওখানে কোন কোন টেস্ট হয়")
    assert asks_for_test_list("which tests do you do")
    assert not asks_for_test_list("সিবিসি টেস্টের দাম কত")


def test_repeated_latin_letters_do_not_matter():
    assert wants_more("aaaro boluuun") is True and wants_more("baaaki guloo") is True


# ============================================================ the whole call, through the real orchestrator
PAGE = 5


def _pages(call):
    from agent.reply_templates import test_list_page

    listing = call._run(call.m._tools.list_tests())
    out, offset = [], 0
    while offset is not None:
        reply, nxt = test_list_page(listing, offset, "bn")
        out.append(reply)
        offset = nxt
    return out


@pytest.fixture
def call():
    from _conversation import conversation

    from agent.fast_path import Catalogue, FastPath

    with conversation("bn") as c:
        # the clinic's catalogue is what lets a test NAMED in a turn be grounded (as in production, see main startup)
        c.m._fast_path = FastPath(Catalogue(c._run(c.m._tools._client.get("/api/v1/catalogue")).json()))
        yield c


def _start(call):
    first = " ".join(call.say("আপনাদের ওখানে কোন কোন টেস্ট হয়", "test_prep", {}))
    assert call.session.test_list_offset == PAGE
    return first


def test_the_failing_call_of_2026_10_13_keeps_its_place(call):
    pages = _pages(call)
    assert pages[0] in _start(call)
    said = " ".join(call.say("প্রডেক্ট গুলো বলুন", "unclear", {}))  # "the next tests", as the recogniser heard it
    assert "বুঝতে পারিনি" in said and "আরও টেস্ট শুনবেন" in said
    assert call.session.test_list_offset == PAGE, "the offer is still open"
    said = " ".join(call.say("এ", "unclear", {}))
    assert "আরও টেস্ট শুনবেন" in said and call.session.test_list_offset == PAGE
    said = " ".join(call.say("পরেরগুলো বলুন", "unclear", {}))
    assert pages[1] in said, "a clear 'the next ones' a few turns later continues the list"


@pytest.mark.parametrize(
    "said",
    ["পরের টেস্টগুলো বলুন", "আরও শুনব", "বাকিগুলো বলুন", "aro bolun", "porer gulo bolun", "aur batao", "next", "go on please",
     "আর কী কী আছে", "বলে যান"],
)
def test_every_go_on_reads_the_next_page(call, said):
    pages = _pages(call)
    _start(call)
    # Latin letters come from the English recogniser (that is what labels them "en"); the call itself stays Bengali
    call._state["lang"] = "en" if said.isascii() else "bn"
    assert pages[1] in " ".join(call.say(said, "unclear", {}))


def test_say_that_again_reads_the_page_just_read_once_more(call):
    pages = _pages(call)
    _start(call)
    call.say("আরও শুনব", "unclear", {})
    assert pages[1] in " ".join(call.say("আবার বলুন", "unclear", {}))
    assert pages[2] in " ".join(call.say("পরের গুলো বলুন", "unclear", {}))


def test_the_list_lapses_after_the_patience_is_used_up(call):
    _start(call)
    for _ in range(3):
        assert "আরও টেস্ট শুনবেন" in " ".join(call.say("প্রডেক্ট গুলো", "unclear", {}))
    said = " ".join(call.say("প্রডেক্ট গুলো", "unclear", {}))
    assert "আরও টেস্ট শুনবেন" not in said and call.session.test_list_offset is None


def test_another_real_request_ends_the_list_and_the_old_offer_is_not_resurrected(call):
    pages = _pages(call)
    _start(call)
    call.say("আপনাদের ক্লিনিকে পার্কিং আছে?", "clinic_faq", {"faq_topic": "parking"})
    assert call.session.test_list_offset is None
    said = " ".join(call.say("পরেরগুলো বলুন", "unclear", {}))
    assert pages[1] not in said


def test_a_no_still_closes_the_list(call):
    _start(call)
    said = " ".join(call.say("না", "unclear", {}))
    assert call.session.test_list_offset is None and "আরও টেস্ট শুনবেন" not in said


def test_a_test_named_in_the_middle_of_the_list_is_priced_not_paged(call):
    pages = _pages(call)
    _start(call)
    said = " ".join(call.say("সিবিসি টেস্টের দাম কত", "test_rate", {"test_name": "CBC"}))
    assert pages[1] not in said and "সিবিসি" in said


def test_a_garbled_go_on_with_a_real_question_after_it_is_still_answered(call):
    _start(call)
    call.say("প্রডেক্ট গুলো বলুন", "unclear", {})
    said = " ".join(call.say("আপনাদের ক্লিনিকে পার্কিং আছে?", "clinic_faq", {"faq_topic": "parking"}))
    assert "পার্কিং আছে" in said


def test_a_go_on_after_one_tests_price_picks_the_list_up_again(call):
    pages = _pages(call)
    _start(call)
    said = " ".join(call.say("সিবিসি টেস্টের দাম কত", "test_rate", {"test_name": "CBC"}))
    assert "সিবিসি" in said and pages[1] not in said
    assert "400" in " ".join(call.say("হ্যাঁ", "unclear", {}))  # the read-back's yes is the read-back's, not the list's
    assert pages[1] in " ".join(call.say("পরেরগুলো বলুন", "unclear", {}))


def test_a_bare_ok_after_a_price_does_not_restart_the_list(call):
    pages = _pages(call)
    _start(call)
    call.say("সিবিসি টেস্টের দাম কত", "test_rate", {"test_name": "CBC"})
    call.say("হ্যাঁ", "unclear", {})
    assert pages[1] not in " ".join(call.say("ঠিক আছে", "unclear", {}))


def test_a_parked_list_is_dropped_when_the_caller_goes_on_to_something_else(call):
    pages = _pages(call)
    _start(call)
    call.say("সিবিসি টেস্টের দাম কত", "test_rate", {"test_name": "CBC"})
    call.say("হ্যাঁ", "unclear", {})
    call.say("আপনাদের ক্লিনিকে পার্কিং আছে?", "clinic_faq", {"faq_topic": "parking"})
    assert pages[1] not in " ".join(call.say("পরেরগুলো বলুন", "unclear", {}))


def test_banglish_price_recall_prices_the_test_named_not_a_lookalike(call):
    """"cbc test er daam taa jano koto bolechilen": the Banglish "er" (of) was a fuzzy hit for the form "esr"."""
    _start(call)
    call._state["lang"] = "en"
    said = " ".join(call.say("cbc test er daam taa jano koto bolechilen", "test_rate", {"test_name": "CBC"}))
    # read back in the caller's own word for it; what matters is that it is the CBC and not the ESR
    assert "cbc" in said.lower() and "esr" not in said.lower() and "ইএসআর" not in said


# ============================== the silence re-ask after a list says the QUESTION again, not the whole list
def test_the_silence_re_ask_after_the_test_list_is_the_question_only(call):
    pages = _pages(call)
    first = " ".join(_start_list(call))
    assert pages[0] in first
    said = " ".join(call.silence(30))
    assert "আপনি কি লাইনে আছেন" in said and "আরও শুনবেন, নাকি কোনো টেস্টের নাম বলবেন?" in said
    assert "সিবিসি" not in said and "টাকা" not in said  # not the five tests and prices all over again


def test_a_listener_is_given_longer_after_a_list_than_after_an_ordinary_question(call):
    _start_list(call)
    assert call.silence(12) == []  # past SILENCE_REASK_S (10 s), still inside the list's extra allowance
    assert call.silence(10) != []  # and then it does ask


def _start_list(call):
    return call.say("আপনাদের ওখানে কোন কোন টেস্ট হয়", "test_prep", {})


# ============================== live 2026-10-14, a Hindi call: "टेस्ते", "और बोलिए", and "और बताइए" heard as English
@pytest.mark.parametrize(
    "said",
    ["आपका वहा पे कौन कौन टेस्ते होता है", "आपका वहां पे कौन कौन टेस्ट होता है", "आपके यहाँ कौन कौन टेस्त होते हैं",
     "कौन कौन से टैस्ट हैं", "आপনাদের কোন কোন টেস্ত হয়" if False else "আপনাদের কোন কোন টেস্ত হয়"],
)
def test_the_recognisers_spellings_of_test_are_still_a_list_request(said):
    assert asks_for_test_list(said) is True


@pytest.mark.parametrize("said", ["और बोलिए", "और बोलो", "और बोलें", "और बताएं", "आगे बोलिए", "बोलते जाइए", "और कहिए", "कुछ और"])
def test_more_ways_of_saying_go_on_in_hindi(said):
    assert wants_more(said) is True, said


def test_a_hindi_first_sentence_with_an_odd_spelling_gets_the_list_not_the_address(call):
    call._state["lang"] = "hi"
    said = " ".join(call.say("आपका वहा पे कौन कौन टेस्ते होता है", "clinic_faq", {"faq_topic": "location"}))
    assert "टेस्ट होते हैं" in said and "राजारहाट" not in said and "রাজারহাট" not in said
    assert call.session.lang == "hi"


def test_a_model_smalltalk_label_on_an_open_list_re_offers_it_not_a_greeting(call):
    pages = _pages(call)
    _start(call)
    said = " ".join(call.say("प्रडेक्ट गुलो", "smalltalk", {}))
    assert "ठीक से समझ नहीं पाई" in said or "ঠিক বুঝতে পারিনি" in said or "Sorry" in said
    assert call.session.test_list_offset == PAGE
    assert pages[1] in " ".join(call.say("পরেরগুলো বলুন", "unclear", {}))


def test_a_thank_you_on_an_open_list_is_still_answered_as_one(call):
    _start(call)
    said = " ".join(call.say("ধন্যবাদ", "smalltalk", {}))
    assert "আরও টেস্ট শুনবেন" not in said
