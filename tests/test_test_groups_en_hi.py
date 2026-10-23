"""The test groups and the "blood test" list, in English and in Hindi -- the way tests/test_test_groups.py does them in
Bengali. Live 2026-10-14: "i want to do the blood test" on an English call was answered "Which tests do you mean? Could
you say the names?" -- the lay-term table knew no English blood-test phrase at all.

    python -m pytest tests/test_test_groups_en_hi.py -v
"""

from __future__ import annotations

import itertools
import os
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent.lay_terms import BLOOD, match_lay_term  # noqa: E402
from agent.test_groups import GROUPS, _nouns_of, match_test_group, mentions_something_else  # noqa: E402


@pytest.fixture(scope="module")
def tests_():
    from _clinic_app import clinic_app

    with clinic_app(sample_patients=False) as (_app, client):
        return client.get("/api/v1/catalogue").json()["tests"]


def _key(said, tests_):
    m = match_test_group(said, tests_)
    return m.key if m else None


# ============================================================================================ English
EN = {
    "cardiac": [
        "i want to do the heart test", "heart tests", "what are the heart tests", "how much is the heart test",
        "cardiac checkup price", "do you have heart tests", "heart test cost", "I want a heart checkup", "tell me about heart tests",
        "which heart tests do you do", "can I get a heart test done", "heart screening",
    ],
    "sugar": [
        "what are the sugar tests", "diabetes test cost", "sugar test price", "i want to do the sugar test", "how much is the sugar test",
        "do you have diabetes tests", "which sugar tests do you offer", "blood sugar tests", "I need a diabetes test", "sugar checkup",
        "glucose tests", "diabetes screening",
    ],
    "thyroid": [
        "tell me about thyroid tests", "thyroid tests", "what thyroid tests do you have", "how much are the thyroid tests",
        "do you do thyroid tests", "I want to do a thyroid test", "thyroid test price", "which thyroid tests are there",
    ],
    "kidney": [
        "how much is the kidney test", "kidney tests", "what kidney tests do you have", "I want to do the kidney test",
        "do you have kidney tests", "which kidney tests are available", "kidney checkup", "kidney screening price",
    ],
    "liver": [
        "do you have liver tests", "liver tests", "what are the liver tests", "i want to do the liver test", "how much is the liver test",
        "jaundice test", "which liver tests are there", "liver checkup", "liver screening cost",
    ],
    "hepatitis": [
        "do you do hepatitis tests", "hepatitis tests", "what are the hepatitis tests", "hepatitis test price", "i want a hepatitis test",
        "how much is the hepatitis test",
    ],
    "dengue": [
        "dengue tests", "what dengue tests do you have", "dengue test price", "I need to get a dengue test done", "which dengue tests are there",
        "how much are the dengue tests", "do you have dengue tests", "dengue fever tests",
    ],
    "vitamin": [
        "which vitamin tests do you offer", "vitamin tests", "what vitamin tests do you have", "how much is the vitamin test",
        "i want to do the vitamin test", "vitamin test price", "do you do vitamin tests", "vitamins checkup",
    ],
    "imaging": ["can I do a scan here", "scan price", "how much is a scan", "i want to get a scan", "do you do scans", "imaging tests"],
    "ultrasound": [
        "ultrasound price", "how much is an ultrasound", "do you do ultrasound", "i want to get an ultrasound", "ultrasound test cost",
        "what ultrasound tests do you have", "sonography price", "usg price",
    ],
    "xray": ["how much for an x-ray", "x-ray price", "do you do x-rays", "i want to get an xray", "xray cost"],
    "urine": ["what is the price of the urine test", "urine test price", "i want to do a urine test", "do you do urine tests", "urine tests"],
    "cervical": ["cervical test price", "i want a cervical screening", "do you do cervical tests", "pap test cost"],
}
HI = {
    "cardiac": [
        "मुझे हार्ट का टेस्ट करवाना है", "दिल की जांच कितने की है", "हार्ट टेस्ट कितने का है", "हार्ट के टेस्ट कौन कौन से हैं",
        "दिल का चेकअप कराना है", "क्या आपके यहाँ हार्ट की जांच होती है", "हार्ट टेस्ट के बारे में बताइए", "हृदय की जांच",
    ],
    "sugar": [
        "शुगर टेस्ट के बारे में बताइए", "शुगर टेस्ट कितने का है", "डायबिटीज की जांच करवानी है", "शुगर के टेस्ट कौन कौन से हैं",
        "शुगर की जांच", "मुझे शुगर टेस्ट करवाना है", "डायबिटीज टेस्ट का रेट", "क्या आपके यहाँ शुगर की जांच होती है",
    ],
    "thyroid": [
        "थायराइड के टेस्ट कौन कौन से हैं", "थायराइड के टेस्ट कितने के हैं", "थायराइड की जांचें", "थायराइड टेस्ट्स बताइए",
        "मुझे थायराइड के टेस्ट करवाने हैं",
    ],
    "kidney": [
        "किडनी की जांच कितनी", "किडनी के टेस्ट कौन कौन से हैं", "किडनी टेस्ट्स बताइए", "किडनी की जांच करवानी है", "गुर्दे की जांच",
        "किडनी के टेस्ट कितने के हैं",
    ],
    "liver": [
        "लिवर के टेस्ट कौन कौन से हैं", "लिवर टेस्ट्स बताइए", "लिवर की जांच करवानी है", "पीलिया की जांच", "लिवर के टेस्ट कितने के हैं",
        "जिगर की जांच",
    ],
    "hepatitis": ["हेपेटाइटिस टेस्ट", "हेपेटाइटिस की जांच कितनी", "हेपेटाइटिस के टेस्ट बताइए", "हेपेटाइटिस टेस्ट करवाना है"],
    "dengue": ["डेंगू के टेस्ट बताइए", "डेंगू के टेस्ट कौन कौन से हैं", "डेंगू की जांच कितनी", "डेंगू टेस्ट्स", "मुझे डेंगू की जांच करवानी है"],
    "vitamin": [
        "विटामिन टेस्ट कितना लगेगा", "विटामिन टेस्ट", "विटामिन के टेस्ट कौन कौन से हैं", "विटामिन की जांच करवानी है",
        "विटामिन टेस्ट्स बताइए",
    ],
    "imaging": ["स्कैन कराना है", "स्कैन कितने का है", "स्कैन की जांच", "इमेजिंग टेस्ट"],
    "ultrasound": ["अल्ट्रासाउंड कितने का है", "अल्ट्रासाउंड करवाना है", "सोनोग्राफी कितनी", "अल्ट्रासाउंड टेस्ट", "यूएसजी कितने का है"],
    "xray": ["एक्सरे करवाना है", "एक्स-रे कितने का है", "एक्सरे का रेट", "एक्स रे कराना है"],
    "urine": ["यूरिन की जांच कितनी", "पेशाब का टेस्ट कराना है", "यूरिन टेस्ट्स", "मूत्र की जांच"],
    "cervical": ["बच्चेदानी की जांच कितनी", "सर्वाइकल टेस्ट करवाना है", "पैप टेस्ट कितने का है"],
}


@pytest.mark.parametrize("key,said", [(k, s) for k, v in EN.items() for s in v])
def test_the_english_phrase_is_the_group(tests_, key, said):
    got = match_test_group(said, tests_)
    assert got is not None, said
    assert got.key == key, (said, got.key)


@pytest.mark.parametrize("key,said", [(k, s) for k, v in HI.items() for s in v])
def test_the_hindi_phrase_is_the_group(tests_, key, said):
    got = match_test_group(said, tests_)
    assert got is not None, said
    assert got.key == key, (said, got.key)


def _sweep(tests_, nouns_of, words, frames, minimum_share):
    total = hit = other = 0
    per = {}
    for g in GROUPS:
        ns = [n for n in nouns_of(g)][:14]
        t = h = o = 0
        for noun, word, frame in itertools.product(ns, words, frames):
            t += 1
            m = match_test_group(frame.format(f"{noun} {word}"), tests_)
            if m is not None and m.key == g.key:
                h += 1
            elif m is not None:
                o += 1
        per[g.key] = (h, t, o)
        total, hit, other = total + t, hit + h, other + o
        assert o == 0, (g.key, "matched ANOTHER group", per[g.key])
    return hit, total, per


def test_every_group_is_found_in_a_few_hundred_english_sentences(tests_):
    def nouns(g):
        return [n for n in _nouns_of(g) if n.isascii() and len(n) > 3 and " er" not in n and not n.endswith((" ka", " ki"))]

    hit, total, per = _sweep(
        tests_, nouns, ["test", "tests", "checkup", "screening"],
        ["i want to do the {}", "how much is the {}", "what are the {}", "do you have {}", "{} price", "can i get a {} done",
         "tell me about the {}", "which {} do you do"], 0.5,
    )
    assert hit > 1200 and hit / total > 0.5, (hit, total, per)


def test_every_group_is_found_in_a_few_hundred_hindi_sentences(tests_):
    def nouns(g):
        return [n for n in _nouns_of(g) if any("ऀ" <= c <= "ॿ" for c in n) and " " not in n]

    hit, total, per = _sweep(
        tests_, nouns, ["टेस्ट", "जांच", "चेकअप", "टेस्ट्स"],
        ["मुझे {} करवाना है", "{} कितने का है", "{} कौन कौन से हैं", "क्या आपके यहाँ {} होता है", "{} के बारे में बताइए", "{} का रेट", "{} कराना है"], 0.5,
    )
    assert hit > 600 and hit / total > 0.5, (hit, total, per)


# ====================================================== what must stay as it was, in English and Hindi
UNCHANGED = [
    # one test by its own name
    "kidney function test", "kidney function test price", "thyroid profile price", "vitamin d test", "vitamin b12 test",
    "lipid profile", "liver function test cost", "complete blood count", "cbc test", "tsh test", "hba1c test", "blood sugar fasting",
    "blood sugar pp test", "dengue ns1 test", "ecg price", "chest x-ray price", "usg whole abdomen", "pap smear price", "hiv test",
    "किडनी फंक्शन टेस्ट", "थायराइड प्रोफाइल कितना", "विटामिन डी टेस्ट", "लिपिड प्रोफाइल", "सीबीसी टेस्ट", "इसीजी कितने का है",
    "शुगर पीपी टेस्ट", "एचबीए वन सी", "यूरिन टेस्ट कितने का", "लिवर टेस्ट कितना",
    # about something else
    "when will my heart test report be ready", "heart specialist doctor", "my sugar is high", "i have chest pain", "kidney doctor",
    "report of the sugar test when", "book a doctor for kidney", "where do i do the heart test", "tomorrow heart test",
    "हार्ट टेस्ट की रिपोर्ट कब मिलेगी", "किडनी के डॉक्टर", "कल शुगर टेस्ट करवाना है", "मेरी शुगर बढ़ गई है", "छाती में दर्द है",
    # two kinds in one sentence
    "sugar and thyroid tests", "heart and kidney tests", "शुगर और थायराइड के टेस्ट", "हार्ट और किडनी टेस्ट",
    # nothing to do with tests
    "thank you", "hello", "do you have parking", "where is the clinic", "धन्यवाद", "क्या पार्किंग है",
]


@pytest.mark.parametrize("said", UNCHANGED)
def test_what_already_meant_something_else_is_left_alone(tests_, said):
    assert match_test_group(said, tests_) is None, said


# ===================================================================================== "blood test", every language
BLOOD_PHRASES = [
    "i want to do the blood test", "blood test", "blood tests please", "blood tests", "I need a blood examination", "get my blood checked",
    "get my blood tested", "can I do a blood test here", "blood investigation", "blood work", "what blood tests do you do",
    "do you do blood tests", "test my blood", "blood testing price",
    "मुझे ब्लड टेस्ट करवाना है", "ब्लड टेस्ट", "खून की जांच", "खून का टेस्ट कराना है", "रक्त परीक्षण", "ब्लड की जांच कराना है",
    "ब्लड जांच", "रक्त की जांच", "खून चेक करवाना है", "ब्लड टेस्ट्स कौन कौन से हैं",
    "blood ki jaanch karani hai", "khoon ka test", "khoon ki jaanch", "rakt jaanch", "blood ka test karwana hai",
    "রক্ত পরীক্ষা", "ব্লাড টেস্ট করব", "রক্তের টেস্ট", "ব্লাড পরীক্ষা", "রক্ত চেক", "blood er test korbo", "rokto porikkha",
]


@pytest.mark.parametrize("said", BLOOD_PHRASES)
def test_a_blood_test_is_the_blood_list_in_every_language(said):
    assert match_lay_term(said) == BLOOD, said


@pytest.mark.parametrize("said", ["blood sugar test", "blood group test", "complete blood count", "ब्लड ग्रुप टेस्ट", "ब्लड शुगर टेस्ट",
                                  "ব্লাড সুগার", "blood pressure", "ब्लड प्रेशर"])
def test_a_named_blood_test_is_not_the_generic_blood_list(said):
    assert match_lay_term(said) is None, said


@pytest.mark.parametrize("said", ["blood test report when will it be ready", "when is my blood test", "blood test tomorrow",
                                  "ब्लड टेस्ट की रिपोर्ट कब मिलेगी", "খুন test কাল"])
def test_a_sentence_about_the_report_or_time_is_not_the_blood_list(said):
    assert mentions_something_else(said) is True or match_lay_term(said) is None


# ========================================================== through the real orchestrator and clinic, per language
@pytest.fixture
def en_call():
    from _conversation import conversation

    from agent.fast_path import Catalogue, FastPath

    with conversation("en") as c:
        payload = c._run(c.m._tools._client.get("/api/v1/catalogue")).json()
        c.m._fast_path = FastPath(Catalogue(payload))
        c.m._catalogue_cache["payload"] = payload
        c.m._catalogue_cache["at"] = time.monotonic()
        yield c


@pytest.fixture
def hi_call():
    from _conversation import conversation

    from agent.fast_path import Catalogue, FastPath

    with conversation("hi") as c:
        payload = c._run(c.m._tools._client.get("/api/v1/catalogue")).json()
        c.m._fast_path = FastPath(Catalogue(payload))
        c.m._catalogue_cache["payload"] = payload
        c.m._catalogue_cache["at"] = time.monotonic()
        yield c


def test_an_english_call_asking_for_a_blood_test_gets_the_blood_tests_in_english(en_call):
    said = " ".join(en_call.say("i want to do the blood test", "book_test", {}))
    assert "These are the blood tests we do" in said and "Which tests do you mean" not in said
    assert "CBC" in said or "Complete Blood Count" in said or "cbc" in said.lower()


def test_a_hindi_call_asking_for_a_blood_test_gets_the_blood_tests_in_hindi(hi_call):
    said = " ".join(hi_call.say("मुझे ब्लड टेस्ट करवाना है", "book_test", {}))
    assert "ये ब्लड टेस्ट होते हैं" in said and "कौन से टेस्ट" not in said


def test_an_english_group_is_answered_with_names_and_prices(en_call):
    rates = {t["test_name"]: t["rate_inr"] for t in en_call._run(en_call.m._tools.list_tests())["tests"]}
    said = " ".join(en_call.say("what are the heart tests", "unclear", {}))
    assert "We have 3 heart tests" in said and "rupees" in said and en_call.blocked == []
    for n in ("ECG", "2D Echocardiography", "TMT (Treadmill Test)"):
        assert str(int(float(rates[n]))) in said


def test_a_hindi_group_is_answered_with_names_and_prices(hi_call):
    rates = {t["test_name"]: t["rate_inr"] for t in hi_call._run(hi_call.m._tools.list_tests())["tests"]}
    said = " ".join(hi_call.say("शुगर टेस्ट के बारे में बताइए", "unclear", {}))
    assert "शुगर के टेस्ट हमारे यहाँ 3 हैं" in said and "रुपये" in said and hi_call.blocked == []
    for n in ("Blood Sugar Fasting", "Blood Sugar PP", "HbA1c"):
        assert str(int(float(rates[n]))) in said


def test_an_english_booking_phrase_asks_which_one_and_the_pick_is_read_back(en_call):
    said = " ".join(en_call.say("i want to do the heart test", "book_test", {}))
    assert "Which one would you like to book" in said and en_call.session.awaiting_tests == "book_test"
    said = " ".join(en_call.say("the ECG", "unclear", {}))
    assert "ecg" in said.lower() and "right" in said.lower()


def test_a_hindi_booking_phrase_asks_which_one(hi_call):
    said = " ".join(hi_call.say("मुझे हार्ट का टेस्ट करवाना है", "book_test", {}))
    assert "कौन सा करवाना चाहेंगे" in said and hi_call.session.awaiting_tests == "book_test"


def test_an_english_exact_name_is_still_that_one_test(en_call):
    said = " ".join(en_call.say("kidney function test price", "test_rate", {"test_name": "Kidney Function Test (KFT)"}))
    assert "We have" not in said and "kidney" in said.lower()


def test_a_blood_test_with_a_named_test_is_that_test_not_the_blood_list(en_call):
    said = " ".join(en_call.say("cbc blood test price", "test_rate", {"test_name": "CBC"}))
    assert "These are the blood tests we do" not in said


def test_a_pick_from_the_english_list_just_read_is_answered_with_prices_and_the_total(en_call):
    rates = {t["test_name"]: t["rate_inr"] for t in en_call._run(en_call.m._tools.list_tests())["tests"]}
    en_call.say("what are the heart tests", "unclear", {})
    said = " ".join(en_call.say("the ECG and the echo, how much in total", "unclear", {}))
    ecg, echo = float(rates["ECG"]), float(rates["2D Echocardiography"])
    assert str(int(ecg)) in said and str(int(echo)) in said and str(int(ecg + echo)) in said, said


@pytest.mark.parametrize(
    "said,expected",
    [
        ("the echo please", ["2D Echocardiography"]), ("echo and treadmill", ["2D Echocardiography", "TMT (Treadmill Test)"]),
        ("ECG", ["ECG"]), ("just the tmt", ["TMT (Treadmill Test)"]), ("the ecg and tmt, total?", ["ECG", "TMT (Treadmill Test)"]),
    ],
)
def test_english_short_names_pick_the_listed_heart_tests(tests_, said, expected):
    from agent.test_groups import resolve_among

    members = [t for t in tests_ if t["name"] in ("ECG", "2D Echocardiography", "TMT (Treadmill Test)")]
    assert [n for n, _ in resolve_among(said, members)] == expected


# ---- an alias spoken in the other word order is still that ONE test, not the group ("यूएसजी होल एब्डोमेन")
def test_alias_words_in_other_order_stay_the_single_test():
    from agent.test_groups import match_test_group

    tests = [
        {"name": "USG Whole Abdomen", "aliases_bn": ["হোল অ্যাবডোমেন ইউএসজি", "পেটের ইউএসজি"],
         "aliases_hi": ["होल एब्डोमेन यूएसजी", "पेट का यूएसजी"]},
        {"name": "USG Pregnancy Profile", "aliases_bn": ["প্রেগনেন্সি ইউএসজি"], "aliases_hi": ["प्रेगनेंसी यूएसजी"]},
    ]
    for said in ("यूएसजी होल एब्डोमेन का रेट क्या है", "यूएसजी होल एब्डोमेन टेस्ट करवाना है",
                 "ইউএসজি হোল অ্যাবডোমেন কত টাকা", "usg whole abdomen price", "whole abdomen usg ka rate"):
        assert match_test_group(said, tests) is None, said
    # a real group question is still a group question
    assert match_test_group("अल्ट्रासाउंड के कौन से टेस्ट हैं", tests) is not None


# ---- "both" / "all" right after a group was read out: every member, summed
def test_asks_for_all_by_quantifier_and_by_count():
    from agent.test_groups import asks_for_all

    yes2 = ["দুটোই করাবো", "দুটোই", "दोनों करवाने हैं", "दोनों", "both", "both please", "all of them", "सभी", "সবগুলো",
            "সবগুলো করাব", "dono karwana hai", "sab karwa do", "I want both tests", "দুটোই করাব মোট কত"]
    for said in yes2:
        assert asks_for_all(said, 2), said
    assert asks_for_all("তিনটেই করাবো", 3) and asks_for_all("तीनों", 3) and asks_for_all("all three", 3)
    # a count word is for that count only
    assert not asks_for_all("দুটোই", 3)
    assert not asks_for_all("both", 3)
    # not "all": a different sentence
    for said in ("all right", "that's all", "সব ঠিক আছে", "ঠিক আছে"):
        assert not asks_for_all(said, 2), said
    # a sentence about a doctor / time is left to the caller of this check (mentions_something_else)
    from agent.test_groups import mentions_something_else

    assert mentions_something_else("আমি সব টেস্টের কথা জানতে চাই না ডাক্তার দেখাতে চাই আজ")


def test_all_of_them_is_not_a_pick_of_named_tests():
    # "both" is the quantifier; "ECG and X-ray, all together how much" names tests -> the pick flow, not "all"
    from agent.test_groups import asks_for_all

    assert asks_for_all("সব মিলিয়ে কত", 3)  # no test named: the whole list


# ===================================================== "which BLOOD tests" is the blood list, not every test (live 2026-10-16)
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


def test_the_three_live_turns_all_get_the_blood_list_in_bengali(bn_call):
    # turn 1 and turn 3 were answered with ALL 34 tests ("মোট 34টা টেস্ট"); turn 2 with the blood tests
    for said in (
        "আপনাদের ওখানে কোন কোন রক্ত পরীক্ষা হয়",
        "আপনাদের আমি রক্ত পরীক্ষা করাবো",
        "আপনাদের ওখানে কোন কোন ব্লাড টেস্ট হয়",
    ):
        out = " ".join(bn_call.say(said, "unclear", {}))
        assert "রক্তের যে পরীক্ষাগুলো হয়" in out and "মোট 34টা" not in out, (said, out)


@pytest.mark.parametrize(
    "said",
    ["which blood tests are there", "which blood tests do you have", "what blood tests do you do", "which blood tests are available"],
)
def test_which_blood_tests_in_english_is_the_blood_list(en_call, said):
    out = " ".join(en_call.say(said, "unclear", {}))
    assert "These are the blood tests we do" in out and "We do 34 tests in all" not in out, out


@pytest.mark.parametrize(
    "said", ["कौन कौन से ब्लड टेस्ट होते हैं", "आपके यहाँ कौन कौन से खून के टेस्ट होते हैं", "कौन कौन सी ब्लड जांच होती है"]
)
def test_which_blood_tests_in_hindi_is_the_blood_list(hi_call, said):
    out = " ".join(hi_call.say(said, "unclear", {}))
    assert "ये ब्लड टेस्ट होते हैं" in out and "कुल 34" not in out, out


def test_the_whole_list_is_still_the_whole_list_in_bengali(bn_call):
    assert "মোট 34টা" in " ".join(bn_call.say("আপনাদের ওখানে কোন কোন টেস্ট হয়", "unclear", {}))


def test_the_whole_list_is_still_the_whole_list_in_english(en_call):
    assert "We do 34 tests in all" in " ".join(en_call.say("which tests are there", "unclear", {}))


def test_the_whole_list_is_still_the_whole_list_in_hindi(hi_call):
    assert "कुल 34" in " ".join(hi_call.say("आपका वहाँ पे कौन कौन सा टेस्ट होता है", "unclear", {}))


def test_blood_with_a_report_or_a_named_test_is_not_taken_for_the_blood_list(en_call):
    from agent.test_groups import mentions_something_else  # noqa: F401  (the veto this fix leans on)

    for said in ("which blood tests have reports ready", "which blood tests are there, CBC or ESR"):
        out = " ".join(en_call.say(said, "unclear", {}))
        assert "These are the blood tests we do" not in out, (said, out)
