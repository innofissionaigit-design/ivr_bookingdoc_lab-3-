"""A caller names a KIND of test ("হার্টের টেস্ট", "সুগারের পরীক্ষা", "ভিটামিন টেস্ট"): the members of that group, with
their prices, from the live catalogue -- and a phrase that already means ONE test is left exactly as it was.

    python -m pytest tests/test_test_groups.py -v
"""

from __future__ import annotations

import itertools
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent.test_groups import GROUPS, match_test_group  # noqa: E402


@pytest.fixture(scope="module")
def tests_():
    from _clinic_app import clinic_app

    with clinic_app(sample_patients=False) as (_app, client):
        return client.get("/api/v1/catalogue").json()["tests"]


def _key(said, tests_):
    m = match_test_group(said, tests_)
    return m.key if m else None


# ---------------------------------------------------------------------------------- what is a group, per group
GROUP_PHRASES = {
    "cardiac": [
        "হার্টের টেস্ট", "হার্টের পরীক্ষা", "হার্টের টেস্টগুলো", "হার্টের কী কী টেস্ট আছে", "হার্টের কোন কোন টেস্ট হয়",
        "হার্টের পরীক্ষা করাতে চাই", "হার্ট চেকআপের খরচ কত", "হার্ট চেকআপ করাব", "হৃদরোগের টেস্ট", "হৃদযন্ত্রের পরীক্ষা",
        "হার্টের জন্য কোন টেস্ট করব", "আমার হার্টের টেস্ট করাতে কত লাগবে", "হার্ট টেস্টের দাম", "কার্ডিয়াক টেস্ট কত",
        "দিল কি জাঁচ", "दिल की जांच", "दिल के टेस्ट", "हृदय की जांच कितनी", "heart er test", "heart tests koto", "hridoyer porikkha koto",
        "heart test price",
    ],
    "sugar": [
        "সুগার টেস্ট", "সুগারের টেস্ট", "সুগারের পরীক্ষা", "সুগারের টেস্টগুলো", "সুগারের কী কী টেস্ট", "ডায়াবেটিসের টেস্ট",
        "ডায়াবেটিসের পরীক্ষা", "ডায়াবেটিস চেকআপ", "ডায়াবেটিসের জন্য কোন টেস্ট করব", "সুগার টেস্ট করাতে কত লাগবে",
        "রক্তে শর্করার পরীক্ষা", "ব্লাড সুগারের টেস্ট", "সুগার দেখাতে চাই", "সুগার চেক করব", "সুগারের টেস্টের দাম কত",
        "शुगर टेस्ट", "शुगर की जांच", "डायबिटीज के टेस्ट", "sugar test koto", "sugar er test gulo", "diabetes test price",
        "shugar er porikkha",
    ],
    "thyroid": [
        "থাইরয়েডের টেস্টগুলো", "থাইরয়েডের কী কী টেস্ট আছে", "থাইরয়েডের টেস্ট কী কী", "থাইরয়েডের কোন কোন পরীক্ষা",
        "থাইরয়েড টেস্টগুলো কত", "থাইরয়েড চেকআপ", "থাইরয়েডের জন্য কোন টেস্ট", "থাইরোয়েডের পরীক্ষাগুলো",
        "थायराइड के टेस्ट", "थायराइड की जांचें", "thyroid er testgulo", "thyroid tests price", "tairoid er test gulo",
    ],
    "kidney": [
        "কিডনির টেস্টগুলো", "কিডনির কী কী টেস্ট আছে", "কিডনির জন্য কোন টেস্ট", "কিডনি টেস্টগুলো কত", "কিডনির কোন কোন পরীক্ষা হয়",
        "কিডনি চেকআপ", "কিডনী পরীক্ষাগুলো", "কিডনির পরীক্ষাগুলোর দাম", "किडनी की जांच", "किडनी के टेस्ट", "गुर्दे की जांच",
        "kidney er testgulo", "kidney tests price", "kidney check up",
    ],
    "liver": [
        "লিভারের টেস্টগুলো", "লিভারের কী কী টেস্ট", "লিভারের কোন কোন পরীক্ষা", "লিভার চেকআপ", "জন্ডিসের টেস্ট",
        "জন্ডিসের কী কী পরীক্ষা", "লিভারের জন্য কোন টেস্ট করব", "লিভার টেস্টগুলো কত", "लिवर के टेस्ट", "पीलिया की जांच",
        "liver er testgulo", "jaundice test koto", "liver tests price",
    ],
    "hepatitis": [
        "হেপাটাইটিস টেস্ট", "হেপাটাইটিসের টেস্ট", "হেপাটাইটিসের পরীক্ষা", "হেপাটাইটিসের কী কী টেস্ট", "হেপাটাইটিস চেকআপ",
        "হেপাটাইটিস টেস্টগুলো", "হেপাটাইটিসের পরীক্ষা কত", "हेपेटाइटिस टेस्ट", "हेपेटाइटिस की जांच", "hepatitis test koto",
        "hepatitis er test", "hepatitis tests",
    ],
    "dengue": [
        "ডেঙ্গুর টেস্টগুলো", "ডেঙ্গুর কী কী টেস্ট", "ডেঙ্গুর টেস্ট কী কী", "ডেঙ্গু জ্বরের টেস্ট কী কী", "ডেঙ্গুর পরীক্ষাগুলো",
        "ডেঙ্গুর কোন কোন টেস্ট হয়", "ডেঙ্গু টেস্টগুলো কত", "ডেঙ্গুর জন্য কোন টেস্ট", "डेंगू के टेस्ट", "डेंगू की जांचें",
        "dengue er testgulo", "dengue tests price",
    ],
    "vitamin": [
        "ভিটামিন টেস্ট", "ভিটামিনের টেস্ট", "ভিটামিনের পরীক্ষা", "ভিটামিন টেস্টগুলো", "ভিটামিনের কী কী টেস্ট", "ভিটামিনের টেস্টের দাম কত",
        "ভিটামিন চেকআপ", "ভিটামিনের ঘাটতির টেস্ট", "ভিটামিন টেস্ট করাতে কত", "ভিটামিনের কোন কোন পরীক্ষা", "विटामिन टेस्ट",
        "विटामिन की जांच कितनी", "vitamin test koto", "vitamin er test", "vitamin tests price",
    ],
    "imaging": [
        "স্ক্যান করাতে কত লাগবে", "স্ক্যানের টেস্ট", "স্ক্যানের পরীক্ষা কী কী", "ছবি তুলতে কত লাগে", "ইমেজিং টেস্ট", "রেডিওলজির টেস্ট",
        "স্ক্যান কত", "স্ক্যান করাব", "स्कैन की जांच", "scan koto", "imaging tests price", "scan test price",
    ],
    "ultrasound": [
        "আলট্রাসাউন্ড কত", "আলট্রাসাউন্ডের দাম", "আলট্রাসাউন্ড করাতে চাই", "আলট্রাসাউন্ড টেস্ট", "ইউএসজি কত", "ইউএসজির দাম",
        "সোনোগ্রাফি কত", "সোনোগ্রাফি করাতে কত লাগে", "আল্ট্রাসাউন্ডের টেস্ট", "अल्ट्रासाउंड कितना", "सोनोग्राफी की जांच",
        "ultrasound koto", "usg price", "sonography price",
    ],
    "xray": [
        "এক্স-রে কত", "এক্সরে করাতে চাই", "এক্সরের দাম", "এক্স রে করাতে কত লাগবে", "এক্সরে টেস্ট", "एक्सरे कितना", "xray koto", "x-ray price",
    ],
    "urine": [
        "প্রস্রাবের টেস্ট", "পেশাবের টেস্ট", "পেশাবের পরীক্ষা", "মূত্রের পরীক্ষা", "প্রস্রাব চেকআপ", "ইউরিনের নমুনার টেস্ট কত",
        "peshab er test", "prosrab porikkha koto", "urine er test", "urine test price",
    ],
    "cervical": [
        "জরায়ুর পরীক্ষা", "জরায়ুর মুখের পরীক্ষা", "জরায়ুমুখের টেস্ট", "সার্ভিক্যাল টেস্ট", "জরায়ুর টেস্ট কত", "बच्चेदानी की जांच",
        "jorayur porikkha", "cervical test price",
    ],
}


@pytest.mark.parametrize("key,said", [(k, s) for k, v in GROUP_PHRASES.items() for s in v])
def test_the_phrase_is_the_group(tests_, key, said):
    got = match_test_group(said, tests_)
    assert got is not None, said
    # a phrase that is an exact alias of one of the group's tests stays that test (see below); it never appears here
    assert got.key == key, (said, got.key)


def test_every_group_has_a_hundred_ways_or_so_of_being_said(tests_):
    """Generated: every noun x every test word x frames. Not every product is a sentence a caller says, and an exact
    alias is rightly left to its test; what must hold is that nearly all of them are the group, never another one."""
    frames = ["{}", "আমার {}", "{} কত", "{} করাতে চাই", "{} কী কী আছে", "{} এর দাম", "একটা {} করাতে কত লাগবে", "{} টা কী"]
    words = ["টেস্ট", "পরীক্ষা", "টেস্টগুলো", "চেকআপ"]
    report = {}
    for g in GROUPS:
        if g.key in ("xray", "urine", "cervical", "ultrasound"):
            pass
        bn_nouns = [n for n in g.nouns if any("ঀ" <= c <= "৿" for c in n) and " " not in n][:10]
        total = hit = other = 0
        for noun, word, frame in itertools.product(bn_nouns, words, frames):
            total += 1
            m = match_test_group(frame.format(f"{noun} {word}"), tests_)
            if m is not None and m.key == g.key:
                hit += 1
            elif m is not None:
                other += 1
        report[g.key] = (hit, total, other)
        assert other == 0, (g.key, "matched ANOTHER group", report[g.key])
        assert total == 0 or hit / total >= 0.5, (g.key, report[g.key])
    assert sum(h for h, _, _ in report.values()) > 1500, report


# ----------------------------------------------------------------------------- what must stay exactly as it was
UNCHANGED = [
    # an exact alias (any ending) stays that one test
    "কিডনি টেস্ট কত", "কিডনির টেস্ট কত", "কিডনি টেস্টের দাম", "লিভার টেস্ট কত", "থাইরয়েড টেস্ট", "থাইরয়েড টেস্টের দাম কত",
    "ডেঙ্গু টেস্ট", "ডেঙ্গু টেস্টের দাম", "ইউরিন টেস্ট কত", "প্রস্রাব পরীক্ষা কত", "ইকো টেস্ট কত", "ট্রেডমিল টেস্ট",
    "কোলেস্টেরল টেস্ট কত", "ম্যালেরিয়া টেস্ট", "টাইফয়েড টেস্ট", "ইসিজি কত", "বুকের এক্স-রে কত", "পেটের আলট্রাসাউন্ড কত",
    "প্রেগন্যান্সি আলট্রাসাউন্ডের দাম", "ডেঙ্গু আইজিজি", "ডেঙ্গু এনএস১ টেস্ট", "হেপাটাইটিস বি টেস্ট", "হেপাটাইটিস সি কত",
    "ভিটামিন ডি টেস্ট", "ভিটামিন বি১২ কত", "সুগার পিপি টেস্ট", "ব্লাড সুগার ফাস্টিং", "এইচবিএ১সি কত", "থাইরয়েড প্রোফাইল কত",
    "টিএসএইচ টেস্ট", "লিভার ফাংশন টেস্ট কত", "কিডনি ফাংশন টেস্ট", "ক্রিয়াটিনিন কত", "ইউরিক অ্যাসিড টেস্ট",
    # a test named by its code
    "cbc test er daam", "esr test koto", "tsh price", "hba1c test", "ecg koto",
    # the blood / abdomen / head lay terms, and the whole-catalogue list
    "রক্ত পরীক্ষা", "রক্তের টেস্ট করাতে চাই", "পেটের ছবি তুলতে কত", "মাথার ছবি তুলতে চাই", "মাথার স্ক্যান", "সিটি স্ক্যান কত",
    "ব্রেন এমআরআই", "আপনাদের ওখানে কোন কোন টেস্ট হয়",
    # about something else
    "ডাক্তার সুগারের টেস্ট দেখতে বলেছেন", "সুগারের রিপোর্ট কখন পাব", "কাল সুগার টেস্ট করাব", "সুগার টেস্ট রিপোর্ট কত দিনে",
    "বাড়িতে এসে সুগার টেস্ট করবেন", "আমার সুগার বেশি কিনা", "আমার সুগার টেস্ট বুক আছে", "সুগার টেস্টের অ্যাপয়েন্টমেন্ট বাতিল করব",
    "কিডনির ডাক্তার কে", "হার্টের ডাক্তার", "হার্টে ব্যথা করছে", "কিডনির সমস্যা", "ডেঙ্গু হয়েছে কিনা", "ভিটামিনের ওষুধ",
    # two kinds in one sentence
    "সুগার আর থাইরয়েডের টেস্ট", "হার্ট আর কিডনির টেস্ট", "ভিটামিন আর সুগার টেস্ট",
    # nothing to do with tests
    "ধন্যবাদ", "", "পার্কিং আছে", "আপনাদের ঠিকানা কী", "হ্যাঁ", "আরও শুনব",
]


@pytest.mark.parametrize("said", UNCHANGED)
def test_what_already_meant_something_else_is_left_alone(tests_, said):
    assert match_test_group(said, tests_) is None, said


def test_a_group_the_catalogue_cannot_back_is_not_a_match(tests_):
    without_ecg = [t for t in tests_ if t["name"] not in ("ECG", "2D Echocardiography", "TMT (Treadmill Test)")]
    assert match_test_group("হার্টের টেস্ট", without_ecg) is None
    only_ecg = [t for t in tests_ if t["name"] != "2D Echocardiography" and t["name"] != "TMT (Treadmill Test)"]
    got = match_test_group("হার্টের টেস্ট", only_ecg)
    assert got is not None and got.tests == ("ECG",)


def test_the_narrower_group_wins_when_two_overlap(tests_):
    assert _key("স্ক্যান আর আলট্রাসাউন্ডের টেস্ট", tests_) == "ultrasound"
    assert _key("লিভার হেপাটাইটিস টেস্ট", tests_) == "hepatitis"


def test_mode_follows_what_is_asked(tests_):
    assert match_test_group("হার্টের টেস্টের দাম", tests_).mode == "price"
    assert match_test_group("হার্টের পরীক্ষা করাতে চাই", tests_).mode == "book"
    assert match_test_group("সুগারের টেস্টের আগে খালি পেটে থাকতে হবে", tests_).mode == "prep"
    assert match_test_group("সুগারের টেস্টের প্রস্তুতি", tests_).mode == "prep"


def test_every_member_is_a_real_catalogue_test(tests_):
    names = {t["name"] for t in tests_}
    for g in GROUPS:
        assert set(g.tests) <= names, (g.key, set(g.tests) - names)
        assert g.label.get("bn") and g.label.get("hi") and g.label.get("en"), g.key


# ===================================================================== through the real orchestrator and clinic
@pytest.fixture
def call():
    from _conversation import conversation

    from agent.fast_path import Catalogue, FastPath

    with conversation("bn") as c:
        c.m._fast_path = FastPath(Catalogue(c._run(c.m._tools._client.get("/api/v1/catalogue")).json()))
        yield c


def _rates(call):
    return {t["test_name"]: t for t in call._run(call.m._tools.list_tests())["tests"]}


def _rupees(n):
    return str(int(float(n)))


@pytest.mark.parametrize(
    "said,names",
    [
        ("হার্টের কী কী টেস্ট আছে", ["ECG", "2D Echocardiography", "TMT (Treadmill Test)"]),
        ("সুগারের টেস্টগুলো", ["Blood Sugar Fasting", "Blood Sugar PP", "HbA1c"]),
        ("থাইরয়েডের টেস্টগুলো", ["Thyroid Profile (T3 T4 TSH)", "TSH"]),
        ("কিডনির জন্য কোন টেস্ট করব", ["Kidney Function Test (KFT)", "Serum Creatinine", "Serum Electrolytes", "Uric Acid"]),
        ("ভিটামিন টেস্ট", ["Vitamin D (25-OH)", "Vitamin B12"]),
        ("ডেঙ্গুর টেস্টগুলো", ["Dengue NS1 Antigen", "Dengue IgG/IgM"]),
        ("হেপাটাইটিসের পরীক্ষা", ["HBsAg", "HCV"]),
        ("আলট্রাসাউন্ড কত", ["USG Whole Abdomen", "USG Pregnancy Profile"]),
        ("স্ক্যান করাতে কত লাগবে", ["Chest X-Ray (PA view)", "USG Whole Abdomen", "USG Pregnancy Profile"]),
    ],
)
def test_a_group_is_answered_with_every_member_and_its_catalogue_price(call, said, names):
    rates = _rates(call)
    spoken = " ".join(call.say(said, "unclear", {}))
    assert call.blocked == []
    for n in names:
        assert (rates[n].get("test_name_bn") or n) in spoken, (said, n, spoken)
        assert _rupees(rates[n]["rate_inr"]) in spoken, (said, n)


def test_a_group_is_not_the_whole_catalogue_list(call):
    spoken = " ".join(call.say("হার্টের কী কী টেস্ট আছে", "unclear", {}))
    assert "মোট" not in spoken and call.session.test_list_offset is None  # not "34টা টেস্ট ... আরও শুনবেন"


def test_a_booking_phrase_asks_which_one_and_the_answer_is_read_back(call):
    spoken = " ".join(call.say("হার্টের পরীক্ষা করাতে চাই", "unclear", {}))
    assert "কোনটা করাতে চান" in spoken and call.session.awaiting_tests == "book_test"
    said = " ".join(call.say("ইসিজি", "unclear", {}))
    assert "ইসিজি" in said and "ঠিক তো" in said


def test_a_preparation_phrase_lists_names_and_asks_which(call):
    spoken = " ".join(call.say("সুগারের টেস্টের আগে খালি পেটে থাকতে হবে", "unclear", {}))
    assert "কোনটার প্রস্তুতি জানতে চান" in spoken
    assert "টাকা" not in spoken


def test_a_group_of_one_names_that_test_and_its_price(call):
    rates = _rates(call)
    said = " ".join(call.say("প্রস্রাবের টেস্ট", "unclear", {}))
    assert "একটাই" in said and "ইউরিন" in said and _rupees(rates["Urine Routine Examination"]["rate_inr"]) in said
    said = " ".join(call.say("এক্স-রে কত", "unclear", {}))
    assert _rupees(rates["Chest X-Ray (PA view)"]["rate_inr"]) in said
    said = " ".join(call.say("জরায়ুর মুখের পরীক্ষা", "unclear", {}))
    assert _rupees(rates["Pap Smear"]["rate_inr"]) in said


def test_an_exact_alias_still_goes_where_it_went(call):
    said = " ".join(call.say("কিডনি টেস্ট কত", "test_rate", {"test_name": "Kidney Function Test (KFT)"}))
    assert "কিডনি টেস্ট" in said and "ঠিক তো" in said  # read back in the caller's own word, as before
    assert "আমাদের এখানে" not in said and "ক্রিয়াটিনিন" not in said  # not the group's list


def test_the_blood_lay_term_is_untouched(call):
    import time

    # the blood list reads the clinic's catalogue over the network in production; here it is handed the seeded one
    call.m._catalogue_cache["payload"] = call._run(call.m._tools._client.get("/api/v1/catalogue")).json()
    call.m._catalogue_cache["at"] = time.monotonic()
    said = " ".join(call.say("রক্ত পরীক্ষা করাতে চাই", "book_test", {}))
    assert "সিবিসি" in said and "কোনটা করাতে চান" in said


def test_the_whole_catalogue_list_is_untouched(call):
    said = " ".join(call.say("আপনাদের ওখানে কোন কোন টেস্ট হয়", "unclear", {}))
    assert "34টা" in said and call.session.test_list_offset == 5


def test_the_group_does_not_open_during_a_booking(call):
    call.say("আমি ডাক্তার দেখাব", "book_appointment", {})
    said = " ".join(call.say("হার্টের টেস্ট", "unclear", {}))
    assert "টেস্ট আমাদের এখানে" not in said


def test_hindi_and_english_calls_are_answered_in_their_language():
    from _conversation import conversation

    from agent.fast_path import Catalogue, FastPath

    with conversation("hi") as c:
        c.m._fast_path = FastPath(Catalogue(c._run(c.m._tools._client.get("/api/v1/catalogue")).json()))
        said = " ".join(c.say("शुगर टेस्ट", "unclear", {}))
        assert "टेस्ट हमारे यहाँ" in said and "रुपये" in said
    with conversation("en") as c:
        c.m._fast_path = FastPath(Catalogue(c._run(c.m._tools._client.get("/api/v1/catalogue")).json()))
        said = " ".join(c.say("heart tests price", "unclear", {}))
        assert "We have 3 heart tests" in said and "rupees" in said


# ================================================================ reported 2026-10-14
CHEST = [
    "বুকের টেস্ট", "বুকের টেস্ট করব", "বুকের পরীক্ষা করাতে চাই", "বুকের কী কী টেস্ট আছে", "বুকের জন্য কোন টেস্ট করব",
    "buk er test korbo", "buk er test korte chai", "buker test koto", "buk er porikkha", "chest tests price", "छाती की जांच",
]


@pytest.mark.parametrize("said", CHEST)
def test_a_chest_test_is_the_chest_group_not_a_booking_word(tests_, said):
    got = match_test_group(said, tests_)
    assert got is not None and got.key == "chest", said
    assert got.tests == ("Chest X-Ray (PA view)", "ECG", "2D Echocardiography", "TMT (Treadmill Test)")


@pytest.mark.parametrize("said", ["টেস্ট বুক করব", "টেস্ট বুক করতে চাই", "সুগার টেস্ট বুক করব", "buk korbo", "book test", "test buk korbo",
                                  "বুক করতে চাই টেস্ট", "বুকিং"])
def test_book_the_loanword_is_never_the_chest(tests_, said):
    got = match_test_group(said, tests_)
    assert got is None or got.key != "chest", said


@pytest.mark.parametrize(
    "said",
    [
        "আমি ভিটামিন বি টুয়েলভ টেস্ট করব কত পড়বে", "ভিটামিন বি টুয়েলভ কত", "ভিটামিন বি বারো টেস্ট", "ভিটামিন বি ১২ টেস্ট",
        "ভিটামিন বি 12 টেস্ট", "vitamin b twelve test koto", "vitamin b 12 test", "ভিটামিন ডি থ্রি টেস্ট", "ভিটামিন ডি টেস্ট",
        "थायराइड टी थ्री टेस्ट", "থাইরয়েড টি থ্রি টেস্ট কত", "থাইরয়েড টি ৩ টেস্ট", "ডেঙ্গু এন এস ওয়ান টেস্ট", "ডেঙ্গু আই জি জি টেস্ট",
        "সুগার এ ওয়ান সি টেস্ট", "এইচ বি এ ওয়ান সি টেস্ট কত", "হেপাটাইটিস বি টেস্ট", "হেপাটাইটিস সি টেস্ট কত",
        "কিডনি ফাংশন টেস্ট", "ভিটামিন বি কমপ্লেক্স টেস্ট",
    ],
)
def test_a_specific_test_spoken_in_a_spelling_the_alias_table_lacks_is_not_the_group(tests_, said):
    assert match_test_group(said, tests_) is None, said


def test_a_stray_banglish_er_is_not_the_esr(tests_):
    """The call's table also holds the Latin names, and "er" (এর, "of") is a 0.8 fuzzy hit for the code "esr"."""
    from _clinic_app import clinic_app

    from agent.entity_grounding import ground_tests
    from agent.fast_path import Catalogue

    with clinic_app(sample_patients=False) as (_app, client):
        cat = Catalogue(client.get("/api/v1/catalogue").json())
    for lang in ("bn", "hi", "en"):
        assert ground_tests("buk er test korbo", lang, cat, []).names == ()
        assert ground_tests("sugar er test koto", lang, cat, []).names == ()
    assert ground_tests("cbc test er daam koto", "bn", cat, []).names == ("Complete Blood Count (CBC)",)
    assert ground_tests("esr test er daam koto", "bn", cat, []).names == ("ESR",)
    assert ground_tests("সিবিসি টেস্টের দাম", "bn", cat, []).names == ("Complete Blood Count (CBC)",)
    assert cat.match("er", "test", "bn", 0.72)[0] is None


def test_the_chest_group_is_answered_through_the_call(call):
    rates = _rates(call)
    said = " ".join(call.say("বুকের টেস্ট করব", "book_test", {}))
    assert "কাউন্টারে" not in said  # not "tests are booked at the counter"
    for n in ("Chest X-Ray (PA view)", "ECG", "2D Echocardiography", "TMT (Treadmill Test)"):
        assert _rupees(rates[n]["rate_inr"]) in said
    assert "কোনটা করাতে চান" in said


def test_the_latin_letter_chest_test_is_answered_through_the_call(call):
    rates = _rates(call)
    call.say("আপনাদের ক্লিনিকে পার্কিং আছে?", "clinic_faq", {"faq_topic": "parking"})  # the call is Bengali from here on
    call._state["lang"] = "en"  # the English recogniser wrote the Latin letters
    said = " ".join(call.say("buk er test korbo", "book_test", {}))
    assert "esr" not in said.lower() and "ইএসআর" not in said
    assert _rupees(rates["Chest X-Ray (PA view)"]["rate_inr"]) in said and "কোনটা করাতে চান" in said


def test_vitamin_b_twelve_said_in_words_is_that_test_not_the_group(call):
    said = " ".join(call.say("আমি ভিটামিন বি টুয়েলভ টেস্ট করব কত পড়বে", "test_rate", {"test_name": "Vitamin B12"}))
    assert "টেস্ট আমাদের এখানে" not in said  # not the vitamin group's list


# ================================================== the two conversations of 2026-10-14 (second report)
def test_a_chest_picture_is_the_chest_xray_not_a_booking_lookup(call):
    """"বুকের ছবি তুলব" was asked "what is your confirmation number / which number did you book from?" -- the model took
    "বুক" for "book"."""
    said = " ".join(call.say("বুকের ছবি তুলব", "lookup_booking", {}))
    assert "কনফার্মেশন" not in said and "বুক করেছিলেন" not in said
    assert "এক্স" in said and "ঠিক তো" in said  # the chest X-ray, read back before it is priced


def test_a_pick_from_the_list_just_read_is_answered_with_prices_and_the_total(call):
    rates = _rates(call)
    call.say("বুকের টেস্ট করব", "book_test", {})
    said = " ".join(call.say("ইসিজিয়ার ইকো করাবো মোট কত হবে", "unclear", {}))
    ecg, echo = rates["ECG"]["rate_inr"], rates["2D Echocardiography"]["rate_inr"]
    assert "আর কীভাবে সাহায্য" not in said  # was: "ঠিক আছে। আর কীভাবে সাহায্য করতে পারি?"
    assert _rupees(ecg) in said and _rupees(echo) in said and _rupees(float(ecg) + float(echo)) in said
    assert "কাউন্টারে" in said  # the booking-is-at-the-counter rule follows the prices, as for any test booking


def test_a_short_pick_without_a_price_question_is_read_back(call):
    call.say("হার্টের পরীক্ষা করাতে চাই", "book_test", {})
    said = " ".join(call.say("ইকো করাব", "unclear", {}))
    assert "ইকো" in said and "ঠিক তো" in said


def test_a_pick_is_only_among_the_tests_just_listed(call):
    rates = _rates(call)
    call.say("হার্টের পরীক্ষা করাতে চাই", "book_test", {})  # ECG, Echo, TMT
    said = " ".join(call.say("সিবিসি করাব মোট কত", "unclear", {}))
    assert "সিবিসি" in said  # a test NAMED outside the list is still read back as itself (the normal grounding)
    assert _rupees(rates["ECG"]["rate_inr"]) not in said


def test_an_answer_that_names_none_of_the_listed_tests_asks_once_more_then_lets_go(call):
    call.say("বুকের টেস্ট করব", "book_test", {})
    first = " ".join(call.say("ওটা", "unclear", {}))
    assert "কোনটা করাতে চান" in first and "ইসিজি" in first
    second = " ".join(call.say("ওটা", "unclear", {}))
    assert "আর কীভাবে সাহায্য" in second


def test_the_list_is_not_a_standing_context(call):
    """After a few unrelated turns "ইকো" is no longer a pick from a list the caller has long left."""
    call.say("বুকের টেস্ট করব", "book_test", {})
    call.say("আপনাদের ক্লিনিকে পার্কিং আছে?", "clinic_faq", {"faq_topic": "parking"})
    for _ in range(3):
        call.say("আপনাদের ক্লিনিকে পার্কিং আছে?", "clinic_faq", {"faq_topic": "parking"})
    rows = call.m._group_member_rows(call.session)
    assert rows == []


def test_a_pick_and_a_total_after_a_price_list_is_answered_without_a_read_back(call):
    rates = _rates(call)
    call.say("বুকের টেস্ট কত", "unclear", {})  # the list, price mode: no "কোনটা করাতে চান?" is open
    said = " ".join(call.say("এক্সরে আর টিএমটি আর ইসিজি করব, সব মিলিয়ে কত", "unclear", {}))
    total = sum(float(rates[n]["rate_inr"]) for n in ("Chest X-Ray (PA view)", "TMT (Treadmill Test)", "ECG"))
    assert "ঠিক তো" not in said and _rupees(total) in said
    assert said.index(_rupees(rates["Chest X-Ray (PA view)"]["rate_inr"])) < said.index(_rupees(rates["TMT (Treadmill Test)"]["rate_inr"]))
    assert call.session.group_members is None


def test_a_pick_without_a_price_question_after_a_price_list_is_still_read_back(call):
    call.say("বুকের টেস্ট কত", "unclear", {})
    said = " ".join(call.say("ইকো করাব", "unclear", {}))
    assert "ইকো" in said and "ঠিক তো" in said


def test_both_after_the_ultrasound_list_is_answered_with_the_total(call):
    """Live 2026-10-15: "দুটোই করাবো" after the two ultrasounds were read out got the same list again, twice. Every test of
    the list taken -> the answer is their total (1500 + 1600), not the list."""
    rates = _rates(call)
    call.say("\u0986\u09ae\u09bf \u0987\u0989\u098f\u09b8\u099c\u09bf \u0995\u09b0\u09be\u09ac\u09cb \u0995\u09a4 \u09aa\u09dc\u09ac\u09c7", "unclear", {})
    said = " ".join(call.say("\u09a6\u09c1\u099f\u09cb\u0987 \u0995\u09b0\u09be\u09ac\u09cb", "unclear", {}))
    total = sum(float(rates[n]["rate_inr"]) for n in ("USG Whole Abdomen", "USG Pregnancy Profile"))
    assert _rupees(total) in said, said
    assert "\u0995\u09cb\u09a8\u099f\u09be \u0995\u09b0\u09be\u09a4\u09c7 \u099a\u09be\u09a8" not in said
