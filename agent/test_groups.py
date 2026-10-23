"""A caller names a KIND of test, not a test: "হার্টের টেস্ট", "সুগারের পরীক্ষা", "ভিটামিন টেস্টগুলো", "কিডনির কী কী টেস্ট".

story: "Caller asks for a group of tests" (2026-10-14). Before this, such a phrase was fuzzy-matched to ONE test --
"ভিটামিন টেস্ট" priced Vitamin D, "সুগার টেস্ট" priced Blood Sugar Fasting -- and the caller never heard that there
were others. agent/lay_terms.py already does this for "রক্ত পরীক্ষা" (every Blood test), "পেটের ছবি" and "মাথার ছবি";
this is the same idea for the rest of the catalogue.

WHAT IS A GROUP. A name for a set of catalogue tests -- the catalogue's own sample types (Cardiac, Imaging, Urine,
Cervical) and the purposes a caller thinks in (sugar, thyroid, kidney, liver, dengue, vitamin, hepatitis). The table
below is DATA: the group's spoken names, and the canonical catalogue names of its members. Which test belongs to which
group is a clinical judgement made once, here, REASONED from the test names and to be confirmed by the clinic; it is
never made at run time by a model. Every name and price said to the caller comes from the live catalogue
(GET /api/v1/tests); a member the catalogue no longer has is simply left out, and a group with no member left is no
match at all -- nothing is ever named that the clinic does not offer.

WHAT IT MUST NOT DO. Take over a phrase that already means ONE test. An exact alias ("থাইরয়েড টেস্ট", "কিডনি টেস্ট",
"ডেঙ্গু টেস্ট", "ইউরিন টেস্ট", "ইসিজি"), with or without a case ending (-র, -এর, -টা), stays that test. A plural
("কিডনি টেস্টগুলো") or a different shape ("কিডনির জন্য কোন টেস্ট") is the group. A test named by its code (CBC, TSH,
ECG ...) is that test. A sentence about a doctor, a report, a time, a date, a place, the home visit, a cancellation, or
about TWO groups, is not this rule's either. A miss costs nothing: the turn goes where it always went.

Pure: no I/O. REASONED, not measured on call data; grow the phrase lists from real transcripts.
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata

# ============================================================================================ the groups


@dataclasses.dataclass(frozen=True)
class TestGroup:
    key: str
    tests: tuple[str, ...]  # canonical catalogue names (the `name` of GET /api/v1/catalogue), in the order spoken
    label: dict  # how each language says "<kind> tests": bn "হার্টের", hi "दिल के", en "heart"
    nouns: tuple[str, ...]  # what names the kind -- every script and spelling; multi-word allowed
    veto: tuple[str, ...] = ()  # a word that makes the sentence about ONE test of the group, not the group


GROUPS: tuple[TestGroup, ...] = (
    TestGroup(
        "cardiac",
        ("ECG", "2D Echocardiography", "TMT (Treadmill Test)"),
        {"bn": "হার্টের", "hi": "दिल के", "en": "heart"},
        nouns=(
            "হার্ট", "হার্টে", "হারট", "হৃদয়", "হৃদযন্ত্র", "হৃদরোগ", "হৃৎপিণ্ড", "হৃৎপিন্ড", "হার্ট অ্যাটাক",
            "হার্টের রোগ", "বুকের হার্ট", "দিল", "দিলের", "কার্ডিয়াক", "কার্ডিও", "কার্ডিয়াক টেস্ট", "হার্ট চেকআপ", "হার্ট স্ক্রিনিং",
            "दिल", "हृदय", "हार्ट", "कार्डियक", "कार्डियो", "दिल की", "दिल का", "दिल के",
            "heart", "hridoy", "hridoyer", "hridoi", "hriday", "dil", "dil ki", "dil ka", "cardiac", "cardio", "hart", "heart er",
            "hartar", "harter",
        ),
        veto=("ইসিজি", "ইকো", "টিএমটি", "ট্রেডমিল", "ecg", "ekg", "echo", "tmt", "treadmill", "ইলেক্ট্রোকার্ডিও", "ইকোকার্ডিও",
              "ईसीजी", "इको", "टीएमटी", "ट्रेडमिल", "कার্ডিওলজি", "কার্ডিওলজি", "कार्डियोलॉजी", "cardiology", "ডাক্তার"),
    ),
    TestGroup(
        "chest",
        ("Chest X-Ray (PA view)", "ECG", "2D Echocardiography", "TMT (Treadmill Test)"),
        {"bn": "বুকের", "hi": "छाती के", "en": "chest"},
        # NOT the bare "বুক": that is also "book" ("টেস্ট বুক করব"). Only the possessive / locative -- "বুকের", "বুকে" --
        # and the Latin "buk er" / "buker" mean the chest.
        nouns=(
            "বুকের", "বুকে", "বুকেরই", "বুকের জন্য", "বুকের ভেতরের", "বুকের চেকআপ", "বুকের পরীক্ষা", "ছাতির", "ছাতি",
            "छाती", "छाती के", "छाती की", "सीने", "सीने के", "सीने की",
            "buker", "buk er", "buke", "bukher", "buker jonno", "chest", "chest er", "chester", "chhatir", "chatir",
        ),
        veto=("এক্স", "এক্সরে", "xray", "x-ray", "x ray", "এক্স-রে", "ইসিজি", "ইকো", "টিএমটি", "ট্রেডমিল", "ecg", "echo", "tmt",
              "ব্যথা", "ধড়ফড়", "দম", "শ্বাস", "কাশি", "হাঁপ", "চাপ"),
    ),
    TestGroup(
        "sugar",
        ("Blood Sugar Fasting", "Blood Sugar PP", "HbA1c"),
        {"bn": "সুগারের", "hi": "शुगर के", "en": "sugar"},
        nouns=(
            "সুগার", "সুগারে", "শুগার", "সুগর", "ব্লাড সুগার", "ডায়াবেটিস", "ডায়াবেটিক", "ডায়বেটিস", "ডায়াবেটিসে", "ডায়বেটিক",
            "বহুমূত্র", "মধুমেহ", "শর্করা", "রক্তে শর্করা", "রক্তের শর্করা", "রক্তে সুগার", "রক্তের সুগার", "রক্তে চিনি", "রক্তের চিনি",
            "গ্লুকোজ", "ডায়াবেটিস চেকআপ", "সুগার চেকআপ", "ডায়াবেটিস স্ক্রিনিং", "ডায়াবেটিস পরীক্ষা",
            "शुगर", "शुगर की", "शुगर का", "डायबिटीज", "डायबिटीस", "मधुमेह", "ग्लूकोज", "ब्लड शुगर",
            "sugar", "sugarer", "sugar er", "shugar", "blood sugar", "diabetes", "diabetis", "diabetic", "daibetis", "diabetis er",
            "madhumeho", "bohumutro", "glucose",
        ),
        veto=("ফাস্টিং", "পিপি", "খাওয়ার পরে", "খাবারের পরে", "এইচবিএ", "হিমোগ্লোবিন", "fasting", "pp", "hba1c",
              "postprandial", "ফাস্টিংয়ের", "फास्टिंग", "पीपी", "एचबीए", "खाने के बाद"),
    ),
    TestGroup(
        "thyroid",
        ("Thyroid Profile (T3 T4 TSH)", "TSH"),
        {"bn": "থাইরয়েডের", "hi": "थायराइड के", "en": "thyroid"},
        nouns=(
            "থাইরয়েড", "থাইরোয়েড", "থাইরইড", "থাইরয়ড", "থাইরয়েডে", "থাইরয়েড গ্ল্যান্ড", "গলার গ্ল্যান্ড", "গলার গ্রন্থি",
            "थायराइड", "थायरॉइड", "थाइरॉइड", "थायरोइड", "थायराइड ग्लैंड",
            "thyroid", "thyroid er", "tairoid", "thairoid", "thyroyd", "thyrod",
        ),
        veto=("টিএসএইচ", "প্রোফাইল", "টি৩", "টি৪", "টি ৩", "টি ৪", "tsh", "t3", "t4", "profile", "टीएसएच", "प्रोफाइल"),
    ),
    TestGroup(
        "kidney",
        ("Kidney Function Test (KFT)", "Serum Creatinine", "Serum Electrolytes", "Uric Acid"),
        {"bn": "কিডনির", "hi": "किडनी के", "en": "kidney"},
        nouns=(
            "কিডনি", "কিডনী", "কিডনিতে", "কিডনীতে", "কিডনিতে", "বৃক্ক", "কিডনির", "কিডনীর", "কিডনি চেকআপ", "কিডনি স্ক্রিনিং",
            "কিডনির রোগ", "কিডনির অবস্থা",
            "किडनी", "गुर्दा", "गुर्दे", "किडनी चेकअप",
            "kidney", "kidney er", "kidni", "kidnee", "kidny", "kidneyr",
        ),
        veto=("ফাংশন", "কেএফটি", "ক্রিয়াটিনিন", "ক্রিয়েটিনিন", "ইলেক্ট্রোলাইট", "ইলেকট্রোলাইট", "ইউরিক", "function", "kft",
              "creatinine", "electrolyte", "uric", "फंक्शन", "केएफटी", "क्रिएटिनिन", "इलेक्ट्रोलाइट", "यूरिक"),
    ),
    TestGroup(
        "liver",
        ("Liver Function Test (LFT)", "HBsAg", "HCV"),
        {"bn": "লিভারের", "hi": "लिवर के", "en": "liver"},
        nouns=(
            "লিভার", "লিভারে", "লিবার", "লিভর", "যকৃত", "যকৃতের", "জন্ডিস", "জণ্ডিস", "জন্ডিসের", "জন্ডিস", "কামলা", "লিভারের রোগ",
            "লিভার চেকআপ", "লিভার স্ক্রিনিং",
            "लिवर", "लीवर", "जॉन्डिस", "पीलिया", "यकृत", "लिवर चेकअप",
            "liver", "liver er", "libar", "livar", "jaundice", "jondis", "jandice", "kamla",
        ),
        veto=("ফাংশন", "এলএফটি", "হেপাটাইটিস", "এইচবিএসএজি", "এইচসিভি", "function", "lft", "hepatitis", "hbsag", "hcv",
              "फंक्शन", "एलएफटी", "हेपेटाइटिस", "एचबीएसएजी", "एचसीवी"),
    ),
    TestGroup(
        "hepatitis",
        ("HBsAg", "HCV"),
        {"bn": "হেপাটাইটিসের", "hi": "हेपेटाइटिस के", "en": "hepatitis"},
        nouns=(
            "হেপাটাইটিস", "হেপাটাইটিসে", "হেপাটাইটিস", "হেপাটাইটিসের", "হেপাটাইটিস টেস্ট", "হেপ", "হেপাটাইটস", "হেপাটাইটিস চেকআপ",
            "হেপাটাইটিস স্ক্রিনিং", "হেপাটাইটিসের রোগ", "হেপাটাইটিস ভাইরাস",
            "हेपेटाइटिस", "हेपेटाइटिस टेस्ट", "हेपाटाइटिस",
            "hepatitis", "hepatitis er", "hepatites", "hepatytis", "hepatitus",
        ),
        veto=("এইচবিএসএজি", "এইচসিভি", "বি", "সি", "hbsag", "hcv", " b", " c", "एचबीएसएजी", "एचसीवी", "बी", "सी"),
    ),
    TestGroup(
        "dengue",
        ("Dengue NS1 Antigen", "Dengue IgG/IgM"),
        {"bn": "ডেঙ্গুর", "hi": "डेंगू के", "en": "dengue"},
        nouns=(
            "ডেঙ্গু", "ডেঙ্গি", "ডেঙ্গুতে", "ডেংগু", "ডেঙ্গু জ্বর", "ডেঙ্গুজ্বর", "ডেঙ্গ", "ডেঙ্গু ভাইরাস", "ডেঙ্গু চেকআপ",
            "ডেঙ্গুর জ্বর", "ডেঙ্গু রোগ",
            "डेंगू", "डेंगु", "डेंगी", "डेंगू बुखार",
            "dengue", "dengu", "dengue er", "dengi", "dengu er", "dangue",
        ),
        veto=("এনএস", "আইজি", "এন্টিজেন", "অ্যান্টিজেন", "অ্যান্টিবডি", "এন্টিবডি", "ns1", "igg", "igm", "antigen", "antibody",
              "एनएस", "आईजी", "एंटीजन", "एंटीबॉडी"),
    ),
    TestGroup(
        "vitamin",
        ("Vitamin D (25-OH)", "Vitamin B12"),
        {"bn": "ভিটামিনের", "hi": "विटामिन के", "en": "vitamin"},
        nouns=(
            "ভিটামিন", "ভিটামিনে", "ভিটামিনের", "ভিটামিন ঘাটতি", "ভিটামিনের ঘাটতি", "ভিটামিন চেকআপ", "ভিটামিন স্ক্রিনিং",
            "ভিটামিন টেস্ট", "ভিটামিন পরীক্ষা", "ভাইটামিন", "বিটামিন", "ভিটামিনস",
            "विटामिन", "विटामिन की", "विटामिन टेस्ट", "वाइटामिन",
            "vitamin", "vitamins", "vitamin er", "bitamin", "bhitamin", "vitamine",
        ),
        veto=("ডি", "বি", "বি১২", "বি ১২", "বি-১২", "d3", "b12", "b 12", "vitamin d", "vitamin b", "डी", "बी", "बी बारह", "बी12",
              "बी १२", "बी 12", "কমপ্লেক্স", "complex"),
    ),
    # ----------------------------------------------------------------- the catalogue's own sample types
    TestGroup(
        "imaging",
        ("Chest X-Ray (PA view)", "USG Whole Abdomen", "USG Pregnancy Profile"),
        {"bn": "স্ক্যানের", "hi": "स्कैन के", "en": "imaging"},
        nouns=(
            "স্ক্যান", "স্ক্যানে", "স্ক্যানিং", "ইমেজিং", "ছবি তোলা", "ছবি তুলতে", "ছবি তোলার", "ছবির টেস্ট", "ছবির পরীক্ষা",
            "রেডিওলজি", "রেডিওলজির", "ইমেজিং টেস্ট", "ছবি তুলব", "ছবি করাব",
            "स्कैन", "स्कैनिंग", "इमेजिंग", "रेडियोलॉजी",
            "scan", "scanning", "imaging", "radiology", "chhobi", "chobi", "scan er", "scaning",
        ),
        veto=("মাথা", "মস্তিষ্ক", "ব্রেন", "এমআরআই", "সিটি", "এক্স", "এক্সরে", "আলট্রা", "ইউএসজি", "পেট", "অ্যাবডোমেন", "প্রেগন্যান্সি",
              "প্রেগনেন্সি", "গর্ভ", "হাড়", "হাত", "পা", "কোমর", "মেরুদণ্ড", "ঘাড়", "ডায়াগনস্টিক", "প্রেসক্রিপশন", "পাঠ", "হোয়াটসঅ্যাপ", "prescription", "whatsapp",
              "सिर", "दिमाग", "एमआरआई", "सीटी", "एक्स", "अल्ट्रा", "पेट", "प्रेगनेंसी", "head", "brain", "mri", "ct", "xray", "x-ray",
              "ultra", "usg", "abdomen", "pregnancy", "matha", "mathar", "peter", "pet"),
    ),
    TestGroup(
        "ultrasound",
        ("USG Whole Abdomen", "USG Pregnancy Profile"),
        {"bn": "আলট্রাসাউন্ডের", "hi": "अल्ट्रासाउंड के", "en": "ultrasound"},
        nouns=(
            "আলট্রাসাউন্ড", "আল্ট্রাসাউন্ড", "আলট্রাসোনোগ্রাফি", "আল্ট্রাসনোগ্রাফি", "আলট্রাসনোগ্রাফি", "সোনোগ্রাফি", "সনোগ্রাফি",
            "ইউএসজি", "ইউ এস জি", "আলট্রা", "আলট্রা সোনো", "আলট্রাসাউন্ডে", "সোনো", "আল্ট্রা", "আলট্রাসাউণ্ড", "ইউএসজি",
            "अल्ट्रासाउंड", "अल्ट्रासोनोग्राफी", "सोनोग्राफी", "यूएसजी", "अल्ट्रा",
            "ultrasound", "ultrasonography", "sonography", "usg", "u s g", "ultra sound", "ultrasound er", "sonogram", "sono",
            "alltrasound", "altrasound",
        ),
        veto=("পেট", "পেটের", "অ্যাবডোমেন", "হোল", "প্রেগন্যান্সি", "প্রেগনেন্সি", "গর্ভ", "গর্ভাবস্থা", "বাচ্চা", "পেट",
              "पेट", "प्रेगनेंसी", "गर्भ", "abdomen", "pregnancy", "pregnant", "whole", "pet", "peter", "peter"),
    ),
    TestGroup(
        "xray",
        ("Chest X-Ray (PA view)",),
        {"bn": "এক্স-রে", "hi": "एक्स-रे", "en": "X-ray"},
        nouns=(
            "এক্স-রে", "এক্সরে", "এক্স রে", "এক্সরের", "এক্স-রের", "এক্সরে প্লেট", "এক্স রে প্লেট", "এক্সরে ছবি", "এক্স-রে ছবি",
            "एक्स-रे", "एक्सरे", "एक्स रे", "एक्सरे की", "एक्स-रे की",
            "xray", "x-ray", "x ray", "x-ray er", "xray er", "eksre", "exray", "x rey", "xre",
            # the picture of the chest IS the chest X-ray
            "বুকের ছবি", "বুকের ফটো", "বুকের ছবি তোলা", "ছাতির ছবি", "বুকের ইমেজ", "छाती की फोटो", "छाती की तस्वीर", "सीने की फोटो",
            "buker chhobi", "buker chobi", "buk er chhobi", "buk er chobi", "buke chhobi", "chest er chhobi", "chest photo", "chest picture",
            "chest er photo", "chest er picture", "buker photo", "buk er photo", "buker picture",
        ),
        veto=(),  # "বুকের এক্স-রে" is an exact alias of the same test; "বুকের ছবি" is this group's own noun
    ),
    TestGroup(
        "urine",
        ("Urine Routine Examination",),
        {"bn": "প্রস্রাবের", "hi": "पेशाब के", "en": "urine"},
        nouns=(
            "প্রস্রাব", "প্রস্রাবে", "প্রস্রাবের", "প্রস্রাব", "পেশাব", "পেশাবের", "পেচ্ছাব", "মূত্র", "মূত্রের", "ইউরিন", "ইউরিনের",
            "প্রস্রাব চেকআপ", "প্রস্রাবের নমুনা", "ইউরিন সাম্পল", "ইউরিন স্যাম্পল", "মুত্র", "ইউরিন রুটিন",
            "पेशाब", "पेशाब की", "पेशाब का", "यूरिन", "मूत्र", "पेशाब का सैंपल", "यूरिन की",
            "urine", "urin", "urine er", "peshab", "peshaber", "prosrab", "prosraber", "pechhab", "pishab", "pesab", "pesaber",
        ),
        veto=("রুটিন", "ইউরিন টেস্ট", "ইউরিন রুটিন", "routine", "रूटीन", "यूरिन टेस्ट"),
    ),
    TestGroup(
        "cervical",
        ("Pap Smear",),
        {"bn": "জরায়ুর মুখের", "hi": "बच्चेदानी के", "en": "cervical"},
        nouns=(
            "জরায়ু", "জরায়ুর", "জরায়ুমুখ", "জরায়ুমুখের", "জরায়ুর মুখ", "জরায়ুর মুখের", "জরায়ুর মুখের ক্যানসার",
            "জরায়ুমুখ ক্যানসার", "সার্ভিক্যাল", "সার্ভিক্স", "সারভিক্যাল", "সার্ভাইকাল", "প্যাপ", "প্যাপ টেস্ট",
            "बच्चेदानी", "बच्चेदानी के मुंह", "सर्वाइकल", "पैप", "पैप टेस्ट",
            "cervical", "cervix", "pap", "pap test", "jorayu", "jorayur", "jorayur mukh", "gyne", "gynae",
        ),
        veto=("স্মিয়ার", "smear", "स्मियर", "ডাক্তার"),
    ),
)

# ===================================================================================== what a sentence is made of
_TEST_WORDS = (
    # Bengali: test / examination / checkup / "get it done" / "show"
    "টেস্ট", "টেষ্ট", "টেস্ট", "টেস্টের", "পরীক্ষা", "পরিক্ষা", "পরীক্ষণ", "চেকআপ", "চেক আপ", "চেক-আপ", "চেক", "জাঁচ", "জাচ",
    "করাতে", "করাব", "করাবো", "করাই", "করানো", "করাতে চাই", "করব", "করবো", "দেখাতে", "দেখাব", "দেখাবো", "দেখানো", "ডায়াগনসিস", "ডায়াগনস্টিক",
    "স্ক্রিনিং", "নমুনা", "সাম্পল", "স্যাম্পল", "ছবি", "ছবির", "ফটো", "তুলব", "তুলতে", "তোলা", "তোলাব", "তোলাতে",
    # Hindi
    "टेस्ट", "टेस्त", "टैस्ट", "टेस्ते", "टेसट", "टेस्टिंग", "जांच", "जाँच", "परीक्षण", "चेकअप", "चेक अप", "करवाना", "करवाऊं", "करानी", "करानी है", "दिखाना", "स्क्रीनिंग", "सैंपल", "फोटो", "तस्वीर", "खिंचवाना",
    # Latin
    "test", "tests", "testgulo", "testta", "porikkha", "porikha", "porikhha", "pariksha", "parikkha", "jaanch", "janch", "jach",
    "check", "checkup", "check-up", "screening", "sample", "samples", "panel", "karabo", "korabo", "korate", "korbo", "korano",
    "dekhabo", "dekhate", "karana", "karwana", "karwani", "chhobi", "chobi", "photo", "picture", "tulbo", "tolabo", "tulte",
)
_PRICE_WORDS = (
    "দাম", "দামটা", "রেট", "রেটটা", "খরচ", "খরচা", "খরচটা", "মূল্য", "টাকা", "কত টাকা", "কত লাগে", "কত লাগবে", "কত পড়বে",
    "চার্জ", "ফি", "পয়সা", "কত", "কতো", "কতটা", "কত হবে",
    "दाम", "रेट", "कीमत", "खर्च", "खर्चा", "पैसे", "कितना", "कितने", "शुल्क", "चार्ज",
    "daam", "dam", "rate", "rates", "cost", "price", "koto", "kto", "kitna", "kitne", "kharcha", "khoroch", "taka", "rupees",
    "charges", "charge", "fee", "fees", "how much",
)
_PREP_WORDS = (
    "প্রস্তুতি", "খালি পেটে", "না খেয়ে", "না খেলে", "খেয়ে আসব", "খাওয়া যাবে", "আগে কী", "আগে কি", "কী করতে হবে", "কি করতে হবে",
    "ফাস্টিং", "উপোস", "উপবাস",
    "तैयारी", "खाली पेट", "बिना खाए", "क्या करना होगा", "पहले क्या",
    "preparation", "prep", "empty stomach", "fasting", "before the test", "what should i do before", "prepare",
    "prostuti", "khali pete", "na kheye",
)
_BOOK_WORDS = (
    "করাতে চাই", "করাতে হবে", "করাব", "করাবো", "করাই", "করতে চাই", "করব", "করবো", "করতে হবে", "বুক করব", "বুক করতে", "বুক করুন", "বুক করে", "বুক করা", "বুক করি", "বুকিং", "করানো",
    "দেখাতে চাই", "দেখাব", "নিতে চাই", "তুলব", "তুলতে চাই", "তোলাব", "তোলাতে চাই",
    "करवाना", "करानी", "कराना", "बुक", "बुकिंग", "करवाऊं",
    "book", "booking", "want to get", "want to do", "need to do", "karabo", "korabo", "korte chai", "korate chai", "korbo",
    "karwana", "karana hai",
)
# A sentence that is about something other than the tests themselves -- left to the rule or the model that owns it.
_NOT_ABOUT_TESTS = (
    "ডাক্তার", "ডক্টর", "ডাঃ", "চিকিৎসক", "চিকিৎসা", "ওষুধ", "ওষুধের", "রোগী",
    "রিপোর্ট", "রেজাল্ট", "ফলাফল", "কখন", "কবে", "কতক্ষণ", "সময়", "ঘণ্টা", "ঘন্টা", "আজ", "কাল", "পরশু", "আগামী", "সোমবার",
    "মঙ্গলবার", "বুধবার", "বৃহস্পতিবার", "শুক্রবার", "শনিবার", "রবিবার", "সকাল", "দুপুর", "বিকেল", "সন্ধ্যা", "রাত",
    "ঠিকানা", "কোথায়", "কোথা", "যাব", "পৌঁছ", "পার্কিং", "গাড়ি", "খোলা", "বন্ধ", "ছুটি",
    "বাতিল", "ক্যানসেল", "রিশিডিউল", "পরিবর্তন", "অ্যাপয়েন্টমেন্ট", "সিরিয়াল",
    "বুক আছে", "বুক করা", "বুক করেছি", "বুক করেছিলাম", "বুক হয়েছে", "বুকিং আছে", "বুকিং করেছি", "বুকিং হয়েছে", "বুক করা আছে", "বাড়ি", "বাড়িতে", "হোম", "কালেকশন", "নমুনা সংগ্রহ",
    "ব্যথা", "জ্বর", "কাশি", "বমি", "সমস্যা", "অসুখ", "উপসর্গ", "লক্ষণ", "হয়েছে", "হচ্ছে", "বুঝব", "মানে", "ভয়", "চিন্তা",
    "বেশি", "কম", "নরমাল", "স্বাভাবিক", "মান",
    "डॉक्टर", "रिपोर्ट", "रिज़ल्ट", "कब", "कितने बजे", "समय", "आज", "कल", "परसों", "पता", "कहाँ", "पार्किंग", "गाड़ी", "खुला",
    "बंद", "रद्द", "कैंसल", "अपॉइंटमेंट", "घर", "दर्द", "बुखार", "समस्या", "बीमारी",
    "doctor", "report", "result", "results", "when", "time", "today", "tomorrow", "address", "where", "parking", "open", "closed",
    "cancel", "reschedule", "appointment", "home", "pain", "fever", "problem", "symptom", "symptoms", "normal", "high", "low",
    "kokhon", "kobe", "kal", "aj", "ajke", "thikana", "kothay", "doctar", "dactar", "report er", "baari", "bari", "kab", "kahan",
)

# A case ending that leaves "the same word" (singular, possessive, definite) -- NOT the plural -গুলো / -গুলি / -সমূহ.
_SUFFIXES = (
    "", "র", "ের", "য়ের", "এর", "তে", "টা", "টি", "টার", "টির", "টাকে", "টিকে", "কে", "ও", "ই", "য়", "ে", "েও", "ের", "রও", "য়ে",
    "er", "r", "e", "ta", "ti", "ke", "o", "i", "ki", "ka", "ke", "ko", "me", "mein",
    "की", "का", "के", "को", "में", "ही", "भी",
)

_NOISE = re.compile(r"[।?!,.;:'\"()\[\]{}\-–—/|…]+")
_WS = re.compile(r"\s+")
_LATIN_RUN = re.compile(r"([a-z])\1+")


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "").lower()
    t = _WS.sub(" ", _NOISE.sub(" ", t)).strip()
    # repeated Latin letters carry no information in a typed / transliterated word ("sugarr", "tessst")
    return _LATIN_RUN.sub(r"\1", t)


def _forms(phrase: str) -> tuple[str, ...]:
    return tuple(_norm(phrase).split())


def _same_word(token: str, base: str) -> bool:
    """`token` is `base`, or `base` with one singular / possessive / definite ending."""
    if token == base:
        return True
    if not token.startswith(base) or len(token) <= len(base):
        return False
    return token[len(base):] in _SUFFIXES


def _sequence_at(tokens: list[str], i: int, seq: tuple[str, ...]) -> bool:
    if i + len(seq) > len(tokens):
        return False
    for k, base in enumerate(seq):
        tok = tokens[i + k]
        if k < len(seq) - 1:
            if tok != base and not _same_word(tok, base):  # a case ending mid-phrase ("কিডনির টেস্ট")
                return False
        elif not _same_word(tok, base):
            return False
    return True


def _has_sequence(tokens: list[str], phrase: str) -> bool:
    seq = _forms(phrase)
    return bool(seq) and any(_sequence_at(tokens, i, seq) for i in range(len(tokens)))


def _has_words_any_order(tokens: list[str], phrase: str) -> bool:
    """A 2-4 word phrase whose words are all spoken side by side, in ANY order. Hindi and Bengali callers say the modality
    before or after the body part ("यूएसजी होल एब्डोमेन" / "होल एब्डोमेन यूएसजी"); an alias is written one way, and a sequence
    match alone then calls the other order "not that test" and lets the group noun ("यूएसजी") take the sentence."""
    seq = _forms(phrase)
    n = len(seq)
    if not 2 <= n <= 4:
        return False
    for i in range(len(tokens) - n + 1):
        window, ok = list(tokens[i : i + n]), True
        for base in seq:
            # a short word ("d", "b", "c") only as itself: "do" is not "d" plus an ending
            hit = next(
                (k for k, tok in enumerate(window) if tok == base or (len(base) > 3 and _same_word(tok, base))), None
            )
            if hit is None:
                ok = False
                break
            window.pop(hit)
        if ok:
            return True
    return False


def _has_prefix_word(tokens: list[str], bases: tuple[str, ...]) -> bool:
    """Any token that STARTS with one of `bases` (a stem: টেস্ট -> টেস্টগুলো, টেস্টের)."""
    stems = {_norm(b) for b in bases if _norm(b) and " " not in _norm(b)}
    long_stems = {s for s in stems if len(s) > 3}
    short_stems = stems - long_stems
    return any(tok.startswith(stem) for tok in tokens for stem in long_stems) or any(
        _same_word(tok, stem) for tok in tokens for stem in short_stems
    )


def _has_phrase_stem(norm_text: str, tokens: list[str], bases: tuple[str, ...]) -> bool:
    for b in bases:
        nb = _norm(b)
        if not nb:
            continue
        if " " in nb:
            if _has_sequence(tokens, nb) or f" {nb}" in f" {norm_text}":
                return True
        elif len(nb) <= 3:  # "কম", "মান", "ফি", "kal", "aj": the word itself, not the start of another
            if any(_same_word(tok, nb) for tok in tokens):
                return True
        elif any(tok.startswith(nb) for tok in tokens):
            return True
    return False


def _veto_hit(norm_text: str, tokens: list[str], vetoes: tuple[str, ...]) -> bool:
    for v in vetoes:
        nv = _norm(v)
        if not nv:
            continue
        if v.startswith(" "):  # " b" / " c": a lone letter word ("হেপাটাইটিস বি") -- whole-token only
            if nv in tokens:
                return True
            continue
        if len(nv) <= 2:  # "বি", "সি", "ডি", "pp", "ct": never as a prefix of a longer word
            if nv in tokens or any(_same_word(tok, nv) for tok in tokens):
                return True
            continue
        if " " in nv:
            if _has_sequence(tokens, nv):
                return True
        elif any(tok == nv or tok.startswith(nv) for tok in tokens):
            return True
    return False


# ======================================================================================= the matcher
@dataclasses.dataclass(frozen=True)
class GroupMatch:
    key: str
    tests: tuple[str, ...]  # canonical names, only those the live catalogue has
    mode: str  # "price" | "prep" | "book"
    group: TestGroup


def _english_names_of(name: str) -> tuple[str, ...]:
    """"Kidney Function Test (KFT)" -> ("kidney function test", "kft"); "Vitamin D (25-OH)" -> ("vitamin d", "25 oh")."""
    base = re.sub(r"\(.*?\)", " ", name).strip()
    bracket = re.findall(r"\((.*?)\)", name)
    names = [base] + [b for b in bracket if len(b.replace(" ", "")) >= 3]
    return tuple(n for n in dict.fromkeys(names) if n)


def _exact_alias_tests(tokens: list[str], catalogue_tests: list[dict]) -> set[str]:
    """The tests whose whole alias is spoken (any script, any singular / possessive ending): the sentence is about
    THAT test, whatever else it says -- unless the caller is asking for the several ("কিডনির টেস্ট কী কী")."""
    out: set[str] = set()
    for t in catalogue_tests:
        for key in ("aliases_bn", "aliases_hi", "aliases_en"):
            for alias in t.get(key) or ():
                if _has_sequence(tokens, alias) or _has_words_any_order(tokens, alias):
                    out.add(t.get("name"))
        # the clinic's own English name is an alias too: "kidney function test", "thyroid profile", "vitamin d"
        for alias in _english_names_of(t.get("name") or ""):
            if _has_sequence(tokens, alias) or _has_words_any_order(tokens, alias):
                out.add(t.get("name"))
    return out


# "which ones / all of them / the several": the caller wants a LIST, so a phrase that is one test's alias is still the
# group's question ("থাইরয়েডের টেস্ট কী কী" -- but "থাইরয়েড টেস্ট কত" is the one test).
_COLLECTIVE_PHRASES = (
    "কী কী", "কি কি", "কোন কোন", "যা যা", "কত রকম", "কত রকমের", "কত ধরনের", "কী ধরনের", "কি ধরনের", "কত ধরণের",
    "কয়টা", "কয়টি", "কতগুলো", "কতগুলি", "তালিকা", "লিস্ট", "সব রকম", "সবগুলো", "সব টেস্ট", "সব পরীক্ষা", "আর কী", "আর কি",
    "कौन कौन", "क्या क्या", "कितने", "कितनी तरह", "कौन कौन से", "सारे", "सभी", "सब टेस्ट",
    "ki ki", "kon kon", "ja ja", "koyta", "koto rokom", "list", "all", "which", "what all", "sob", "shob", "kaun kaun", "kya kya",
    "kitne",
)


def _asks_for_several(norm_text: str, tokens: list[str]) -> bool:
    if any(tok.endswith(("গুলো", "গুলি", "গুলোর", "সমূহ", "gulo", "guli", "gulor")) for tok in tokens):
        return True
    return _has_phrase_stem(norm_text, tokens, _COLLECTIVE_PHRASES)


def _names_a_code(tokens: list[str], catalogue_tests: list[dict]) -> bool:
    """A test named by its code or short name in Latin letters (cbc, esr, tsh, ecg, hba1c ...). A code that two tests
    share ("USG" of both ultrasounds) names the kind, not one of them."""
    seen: dict[str, set[str]] = {}
    for t in catalogue_tests:
        for w in re.findall(r"[A-Za-z0-9]+", t.get("name") or ""):
            if len(w) >= 2 and (w.isupper() or any(c.isdigit() for c in w) or w[:1].isupper() and w[1:].lower() != w[1:]):
                seen.setdefault(w.lower(), set()).add(t.get("name"))
    return any(tok in seen and len(seen[tok]) == 1 for tok in tokens)


# A name made of a letter or a number ("ভিটামিন বি টুয়েলভ", "থাইরয়েড টি থ্রি", "এইচবিএ ওয়ান সি", "vitamin b 12") is one
# test spoken in a way the alias table does not list: never the group. Letter names and number words only as whole
# tokens; "আর", "ও", "এ", "ই", "কে" are left out -- they are everyday words ("and", "this", "who").
_LETTER_NAMES = frozenset({
    "বি", "সি", "ডি", "এফ", "জি", "এইচ", "আই", "জে", "এল", "এম", "এন", "পি", "কিউ", "ইউ", "ভি", "ডব্লিউ", "এক্স", "ওয়াই", "জেড",
    "बी", "सी", "डी", "एफ", "जी", "एच", "आई", "जे", "एल", "एम", "एन", "पी", "क्यू", "यू", "वी", "एक्स", "वाई",
    "b", "c", "d", "f", "g", "h", "j", "k", "l", "m", "n", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
})
_NUMBER_WORDS = frozenset({
    "এক", "দুই", "দুটো", "তিন", "চার", "পাঁচ", "ছয়", "সাত", "আট", "নয়", "দশ", "এগারো", "এগার", "বারো", "বার", "তেরো", "চৌদ্দ",
    "পনেরো", "ষোলো", "সতেরো", "আঠারো", "উনিশ", "কুড়ি", "বিশ", "ত্রিশ", "পঞ্চাশ", "একশো",
    "ওয়ান", "টু", "থ্রি", "ফোর", "ফাইভ", "সিক্স", "সেভেন", "এইট", "নাইন", "টেন", "ইলেভেন", "টুয়েলভ", "টোয়েলভ", "ট্যুয়েলভ", "টুএলভ",
    "থার্টিন", "ফিফটিন", "টোয়েন্টি", "থার্টি", "ফিফটি", "হান্ড্রেড", "জিরো",
    "एक", "दो", "तीन", "चार", "पाँच", "पांच", "छह", "सात", "आठ", "नौ", "दस", "ग्यारह", "बारह", "तेरह", "वन", "टू", "थ्री", "फोर",
    "फाइव", "सिक्स", "सेवन", "एट", "नाइन", "टेन", "ट्वेल्व", "ट्वेल्थ",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fifteen",
    "twenty", "thirty", "fifty", "hundred", "zero",
})
_DIGITS = re.compile(r"[0-9০-৯०-९]")


def _names_a_test_by_letter_or_number(rest: list[str]) -> bool:
    for tok in rest:
        if _DIGITS.search(tok) or tok in _LETTER_NAMES or tok in _NUMBER_WORDS:
            return True
    return False


def _noun_span(tokens: list[str], group: TestGroup) -> set[int] | None:
    """The token positions of the group's noun in the sentence (the longest phrase wins), or None."""
    best: tuple[int, int] | None = None
    for noun in _nouns_of(group):
        seq = _forms(noun)
        if not seq:
            continue
        for i in range(len(tokens)):
            if _sequence_at(tokens, i, seq) and (best is None or len(seq) > best[1]):
                best = (i, len(seq))
    return None if best is None else set(range(best[0], best[0] + best[1]))


def match_test_group(text: str, catalogue_tests: list[dict]) -> GroupMatch | None:
    """The group of tests `text` asks about, or None.

    `catalogue_tests`: the `tests` of GET /api/v1/catalogue (name, aliases_bn, aliases_hi). Only members the
    catalogue has are returned; a group the catalogue cannot back is no match."""
    norm = _norm(text)
    if not norm or not catalogue_tests:
        return None
    tokens = norm.split()
    spans = {g.key: _noun_span(tokens, g) for g in GROUPS}
    hits = [g for g in GROUPS if spans[g.key] is not None]
    if not hits:
        return None
    # the group's own noun may carry a word that is a symptom elsewhere ("ডেঙ্গু জ্বর"): leave it out of the check
    named = set().union(*(spans[g.key] for g in hits))
    rest = [tok for i, tok in enumerate(tokens) if i not in named]
    if _has_phrase_stem(" ".join(rest), rest, _NOT_ABOUT_TESTS):
        return None
    if _names_a_test_by_letter_or_number(rest):
        return None
    asks = (
        _has_prefix_word(tokens, _TEST_WORDS)
        or _has_phrase_stem(norm, tokens, _TEST_WORDS)
        or _has_phrase_stem(norm, tokens, _PRICE_WORDS)
        # the noun IS the procedure ("do you do ultrasound", "i want to get an x-ray"): nothing more is needed
        or any(g.key in _PROCEDURE_GROUPS for g in hits)
    )
    if not asks:
        return None
    # two kinds of test in one sentence ("সুগার আর থাইরয়েডের টেস্ট"): not one group's answer -- unless one is a subset
    # of the other, which is the narrower question ("আলট্রাসাউন্ড" inside "স্ক্যান")
    if len(hits) > 1:
        narrower = [g for g in hits if not any(set(o.tests) < set(g.tests) for o in hits if o is not g)]
        if len(narrower) != 1:
            return None
        hits = narrower
    group = hits[0]
    if _veto_hit(norm, tokens, group.veto):
        return None
    if _names_a_code(tokens, catalogue_tests):
        return None
    spoken_aliases = _exact_alias_tests(tokens, catalogue_tests)
    if spoken_aliases and not (_asks_for_several(norm, tokens) and spoken_aliases <= set(group.tests)):
        return None
    have = {t.get("name") for t in catalogue_tests}
    members = tuple(n for n in group.tests if n in have)
    if not members:
        return None
    if _has_phrase_stem(norm, tokens, _PREP_WORDS):
        mode = "prep"
    elif _has_phrase_stem(norm, tokens, _BOOK_WORDS):
        mode = "book"
    else:
        mode = "price"
    return GroupMatch(group.key, members, mode, group)



# ============================================================== "the one I just listed": a pick among the members
# The agent has just read a group out ("হার্টের টেস্ট আমাদের এখানে ৩টা আছে: ইসিজি …"). The caller's answer is a choice
# among THOSE tests, and is said short and run together: "ইসিজিয়ার ইকো" (ইসিজি + আর, the recogniser joined them), "ইকো"
# (the alias is "ইকো টেস্ট"). Matched here against the members only -- a short form that two members share is not
# a match -- so nothing outside the list the caller was just given is ever picked.
_GENERIC_TAIL = frozenset({"টেস্ট", "টেস্টের", "পরীক্ষা", "পরীক্ষার", "টেষ্ট", "টেস্টটা", "test", "tests", "टेस्ट", "जांच", "जाँच"})
_GLUE = frozenset(_SUFFIXES) | frozenset({"আর", "য়ার", "ার", "ওর", "সহ", "টাও", "টাই", "ছাড়া", "আরও"})
_PRICE_OR_TOTAL = (
    "মোট", "সব মিলিয়ে", "মিলিয়ে", "একসাথে", "একসঙ্গে", "একত্রে", "কত হবে", "কত পড়বে", "কত লাগবে", "কত লাগে", "কত", "দাম",
    "রেট", "খরচ", "টাকা", "कुल", "टोटल", "सब मिलाकर", "कितना", "कितने", "दाम", "रेट", "total", "price", "cost", "how much",
    "joto", "koto", "mot", "dam", "daam", "rate",
)


def asks_to_book(text: str) -> bool:
    """The caller says they will do / book it ("করব", "করাব", "বুক করতে চাই")."""
    norm = _norm(text)
    return bool(norm) and _has_phrase_stem(norm, norm.split(), _BOOK_WORDS)


def asks_for_price(text: str) -> bool:
    """A price or a total is asked for ("মোট কত হবে", "কত পড়বে", "total koto")."""
    norm = _norm(text)
    tokens = norm.split()
    return bool(norm) and _has_phrase_stem(norm, tokens, _PRICE_OR_TOTAL)


# "All of them" -- the caller's answer to "which one would you like?" when the list has just been read out. Said as a
# quantifier ("সবগুলো", "all of them", "सभी") or as the count of the list ("দুটোই", "both", "दोनों" -- two tests read out).
# A COUNT word counts only when it equals the number of tests read out: "দুটো" after a list of five is two of them.
_ALL_ANY_SIZE = frozenset({
    # Bengali
    "সবগুলো", "সবগুলি", "সবগুলোই", "সবগুলোকেই", "সবকটা", "সবকটাই", "সবকটি", "সবকটিই", "সবই", "সবটা", "সবটাই", "সব", "সবাই",
    "সবকিছু", "সকল", "সবগুলোর", "সবকয়টা", "সবকয়টাই",
    # Hindi
    "सभी", "सारे", "सारी", "सारा", "सब", "सबका", "सबकी", "सबके", "सभीका", "सबकुछ",
    # English
    "all", "everything", "every", "each", "everyone", "entire",
    # Latin spellings
    "sob", "sobgulo", "sobkota", "sobkichu", "sabhi", "sab", "saare", "sare", "saari", "sabka", "sabki", "sabke", "sabkuch",
})
_ALL_BY_COUNT = {
    2: frozenset({
        "দুটোই", "দুটিই", "দুইটাই", "দুটোকেই", "দুটোর", "দুটো", "দুটি", "দুইটা", "দুইটি", "দুটোতেই",
        "दोनों", "दोनो", "दोनोंही", "दोनों", "दोंनो", "both", "dono", "donon", "dutoi", "duto", "duitai", "duitaii", "duitaa",
    }),
    3: frozenset({
        "তিনটেই", "তিনটাই", "তিনটিই", "তিনটে", "তিনটা", "তিনটি", "তিনটেকেই",
        "तीनों", "तीनो", "teeno", "teenon", "tinte", "tintei", "tinti",
    }),
    4: frozenset({"চারটেই", "চারটাই", "চারটিই", "চারটে", "চারটা", "चारों", "चारो", "charo", "charon", "charte"}),
    5: frozenset({"পাঁচটাই", "পাঁচটেই", "পাঁচটিই", "পাঁচটা", "पाँचों", "पांचों", "paanchon", "panchon", "pachtai"}),
}
_ALL_PHRASES = (
    "all of them", "all of these", "all of those", "all of it", "all the above", "all the tests", "all these", "all those",
    "every one", "each one", "the whole lot", "सब कुछ", "सभी टेस्ट", "सारे टेस्ट", "सब टेस्ट", "সব টেস্ট", "সবগুলো টেস্ট",
    "সব কটা", "সব গুলো", "সব কয়টা", "সব কিছু", "দুটো ই", "দুটোই", "सब ही", "सभी ही", "दोनों ही", "दोनो ही", "dono hi",
    "both of them", "both the tests", "both tests", "both of these",
)
_NOT_ALL = ("all right", "all set", "all good", "that's all", "thats all", "बस इतना", "সব ঠিক", "सब ठीक", "all clear")


# the same folding the sentence gets (repeated Latin letters collapse: "all" -> "al"), so a Latin entry matches itself
_ALL_ANY_SIZE = frozenset(_norm(w) for w in _ALL_ANY_SIZE)
_ALL_BY_COUNT = {n: frozenset(_norm(w) for w in ws) for n, ws in _ALL_BY_COUNT.items()}
_ALL_PHRASES = tuple(_norm(p) for p in _ALL_PHRASES)
_NOT_ALL = tuple(_norm(p) for p in _NOT_ALL)


def asks_for_all(text: str, member_count: int) -> bool:
    """The caller wants every test of the list that was just read out ("দুটোই করাবো", "both", "सभी", "all of them").
    Only a short answer, or one that says it will do / book it or asks what it costs: a long sentence that merely
    contains "all" is something else."""
    norm = _norm(text)
    tokens = norm.split()
    if not tokens or member_count < 2:
        return False
    if any(p in norm for p in _NOT_ALL):
        return False
    short = len(tokens) <= 5
    if not (short or asks_to_book(text) or asks_for_price(text)):
        return False
    counted = _ALL_BY_COUNT.get(member_count, frozenset())
    if any(t in _ALL_ANY_SIZE or t in counted for t in tokens):
        return True
    return any(p in norm for p in _ALL_PHRASES if _count_ok(p, member_count))


def _count_ok(phrase: str, member_count: int) -> bool:
    """A phrase that says "both" / "two" is for a list of two; the rest are for any list."""
    both = tuple(_norm(w) for w in ("both", "দুটো", "दोनों", "दोनो", "dono"))
    return member_count == 2 or not any(w in phrase for w in both)


# How a caller says each test in English when it is not the catalogue's own name: "the echo", "treadmill", "fasting sugar".
_EN_SHORT = {
    "ECG": ("ecg", "ekg", "e c g"),
    "2D Echocardiography": ("echo", "eco", "2d echo", "echocardiogram", "echocardiography"),
    "TMT (Treadmill Test)": ("tmt", "treadmill", "stress test", "t m t"),
    "Chest X-Ray (PA view)": ("x ray", "xray", "chest xray", "chest x ray"),
    "USG Whole Abdomen": ("abdomen", "abdominal", "whole abdomen", "stomach", "abdomen usg", "abdominal ultrasound"),
    "USG Pregnancy Profile": ("pregnancy", "pregnancy scan", "obstetric", "pregnancy ultrasound"),
    "Kidney Function Test (KFT)": ("kft", "kidney function", "kidney function test"),
    "Serum Creatinine": ("creatinine", "serum creatinine"),
    "Serum Electrolytes": ("electrolytes", "electrolyte", "serum electrolytes"),
    "Uric Acid": ("uric acid", "uric"),
    "Blood Sugar Fasting": ("fasting sugar", "sugar fasting", "fasting", "fbs", "blood sugar fasting"),
    "Blood Sugar PP": ("pp", "sugar pp", "post prandial", "postprandial", "blood sugar pp", "pp sugar"),
    "HbA1c": ("hba1c", "a1c", "hb a1c", "glycated haemoglobin", "glycosylated hemoglobin"),
    "Thyroid Profile (T3 T4 TSH)": ("thyroid profile", "profile", "t3 t4", "t3 t4 tsh"),
    "TSH": ("tsh",),
    "Liver Function Test (LFT)": ("lft", "liver function", "liver function test"),
    "HBsAg": ("hbsag", "hepatitis b", "hep b"),
    "HCV": ("hcv", "hepatitis c", "hep c"),
    "Dengue NS1 Antigen": ("ns1", "dengue ns1", "ns 1"),
    "Dengue IgG/IgM": ("igg", "igm", "igg igm", "dengue igg", "dengue igm"),
    "Vitamin D (25-OH)": ("vitamin d", "d3", "vit d", "vitamin d3"),
    "Vitamin B12": ("vitamin b12", "b12", "vit b12", "b 12"),
    "Urine Routine Examination": ("urine routine", "urine test", "urine"),
    "Pap Smear": ("pap smear", "pap"),
}


def _member_forms(row: dict) -> set[tuple[str, ...]]:
    forms: set[tuple[str, ...]] = set()
    for short in _EN_SHORT.get(row.get("name") or "", ()):
        seq = _forms(short)
        if seq:
            forms.add(seq)
    for key in ("aliases_bn", "aliases_hi", "aliases_en"):
        for alias in row.get(key) or ():
            seq = _forms(alias)
            if not seq:
                continue
            forms.add(seq)
            if len(seq) >= 2 and seq[-1] in _GENERIC_TAIL:
                forms.add(seq[:-1])  # "ইকো টেস্ট" -> "ইকো"
            if len(seq) >= 2:
                forms.add(seq[1:])  # "বুকের এক্স রে" -> "এক্স রে"
    for w in re.findall(r"[A-Za-z0-9]+", row.get("name") or ""):
        if len(w) >= 2 and (w.isupper() or any(c.isdigit() for c in w) or w[:1].isupper() and w[1:].lower() != w[1:]):
            forms.add((w.lower(),))
    return {f for f in forms if f and sum(len(x) for x in f) >= 3}


def _glue_sequence_at(tokens: list[str], i: int, seq: tuple[str, ...]) -> bool:
    if i + len(seq) > len(tokens):
        return False
    for k, base in enumerate(seq):
        tok = tokens[i + k]
        if tok == base:
            continue
        last = k == len(seq) - 1
        if not tok.startswith(base):
            return False
        tail = tok[len(base):]
        if not (tail in _GLUE or (last and tail in _GLUE)):
            return False
    return True


def resolve_among(text: str, member_rows: list[dict]) -> list[tuple[str, str]]:
    """-> [(canonical name, the words the caller used)] for every member of the listed group the sentence picks, in the
    order said. A form two members share ("আলট্রাসাউন্ড") picks neither."""
    tokens = _norm(text).split()
    if not tokens or not member_rows:
        return []
    owners: dict[tuple[str, ...], set[str]] = {}
    for row in member_rows:
        for f in _member_forms(row):
            owners.setdefault(f, set()).add(row.get("name"))
    found: dict[str, tuple[int, str]] = {}
    for form, names in owners.items():
        if len(names) != 1:
            continue
        name = next(iter(names))
        for i in range(len(tokens)):
            if _glue_sequence_at(tokens, i, form):
                spoken = " ".join(tokens[i : i + len(form)])
                if name not in found or i < found[name][0]:
                    found[name] = (i, spoken)
                break
    return [(n, s) for n, (_i, s) in sorted(found.items(), key=lambda kv: kv[1][0])]


# ======================================================================================= English and Hindi, wider
# Added with the English / Hindi test of 2026-10-14 ("i want to do the blood test" asked "which tests do you mean?"):
# the same coverage the Bengali words have, in the other two languages and in Latin letters.
_TEST_WORDS = _TEST_WORDS + (
    "testing", "tested", "checks", "checked", "checking", "examination", "examinations", "exam", "investigation",
    "investigations", "analysis", "diagnostic", "diagnostics", "work-up", "workup", "scan", "scans", "scanning", "imaging",
    "टेस्ट्स", "परीक्षा", "जांचें", "जाँचें", "चेक", "इन्वेस्टिगेशन", "स्कैन", "स्क्रीनिंग",
    "janchein", "jaanchein", "checkup", "check-up",
)
_PRICE_WORDS = _PRICE_WORDS + (
    "pricing", "how much is", "how much does", "how much are", "cost of", "what does it cost", "what do they cost",
    "expensive", "cheap", "charges for", "कितने का", "कितनी", "कितने रुपये", "कितने पैसे", "कितना लगेगा", "कितना लगता", "भाव",
    "फीस", "कीमत क्या", "रेट क्या", "दाम क्या", "kitne ka", "kitna lagega", "kitna lagta", "kitne rupaye", "kitne paise",
)
_BOOK_WORDS = _BOOK_WORDS + (
    "want to do", "want to get", "want to take", "want to have", "want to book", "need to do", "need to get",
    "need to take", "need to book", "would like to do", "would like to get", "would like to book", "like to do", "like to get",
    "i will do", "i'll do", "let me do", "going to do", "planning to do", "to do the", "to get the", "get done", "do the",
    "book a", "book the", "schedule a", "can i do", "can i get", "can i have", "can i take", "how do i get", "where can i do",
    "करना है", "कराना है", "करवाना है", "करवानी है", "करानी है", "करवाने", "कराने", "करना चाहता", "करना चाहती",
    "करवाना चाहता", "करवाना चाहती", "कराना चाहता", "कराना चाहती", "चाहिए", "लेना है", "करवाऊं", "करवाऊँ", "कराऊं", "कराऊँ",
    "करूँगा", "करूंगा", "करूँगी", "करूंगी", "बुक करना", "बुक करवाना", "karna hai", "karwana hai", "karana hai", "karni hai",
    "karwani hai", "karwa do", "karwaana", "karaana", "karwana chahta", "karwana chahti", "chahiye", "lena hai",
)
_EXTRA_NOUNS = {
    "cardiac": (
        "heart's", "hearts", "heart health", "cardiac health", "heart condition", "cardiac screening", "heart screening",
        "हार्ट", "दिल", "हृदय", "ह्रदय", "हृदय रोग", "कार्डियक", "हार्ट चेकअप", "दिल की जांच", "दिल का चेकअप",
        "dil", "dil ki", "dil ka", "hriday", "heart checkup", "heart check up",
    ),
    "sugar": (
        "sugar level", "sugar levels", "blood glucose", "glucose level", "blood sugar level", "diabetes screening",
        "diabetes checkup", "sugar checkup", "sugars", "शुगर", "ब्लड शुगर", "डायबिटीज", "डायबिटिक", "मधुमेह", "शुगर लेवल",
        "शुगर चेकअप", "sugar ka", "sugar ki", "shugar", "madhumeh",
    ),
    "thyroid": ("thyroid gland", "thyroid levels", "थायराइड", "थायरॉयड", "थायरॉइड", "थाइरॉइड", "थायरोइड", "thyroid ka", "thyroid ki"),
    "kidney": (
        "kidneys", "renal", "renal health", "kidney health", "kidney screening", "किडनी", "गुर्दा", "गुर्दे", "वृक्क", "रीनल",
        "किडनी चेकअप", "kidney ka", "kidney ki", "gurda", "gurde",
    ),
    "liver": (
        "hepatic", "liver health", "liver screening", "jaundice", "लिवर", "लीवर", "यकृत", "जिगर", "पीलिया", "जॉन्डिस", "जौंडिस",
        "लिवर चेकअप", "liver ka", "liver ki", "piliya", "jigar",
    ),
    "hepatitis": ("hepatitis screening", "hepatitis virus", "हेपेटाइटिस", "हैपेटाइटिस", "हेपाटाइटिस", "hepatitis ka", "hepatitis ki"),
    "dengue": ("dengue fever", "dengue virus", "dengue screening", "डेंगू", "डेंगी", "डेंगू बुखार", "dengu ka", "dengue ka", "dengue ki"),
    "vitamin": (
        "vitamins", "vitamin deficiency", "vitamin levels", "vitamin screening", "विटामिन", "विटामिन्स", "वाइटामिन", "विटामिन की कमी",
        "vitamin ka", "vitamin ki", "vitamins ka",
    ),
    "imaging": ("scan", "scans", "scanning", "imaging", "radiology", "स्कैन", "स्कैनिंग", "इमेजिंग", "रेडियोलॉजी", "scan ka", "scan ki"),
    "ultrasound": (
        "ultrasound", "ultrasounds", "ultrasonography", "sonography", "sonogram", "usg", "sono", "अल्ट्रासाउंड", "अल्ट्रासोनोग्राफी",
        "सोनोग्राफी", "यूएसजी", "ultrasound ka", "ultrasound ki", "sonography ka",
    ),
    "xray": ("x-ray", "x ray", "xray", "x-rays", "xrays", "एक्स-रे", "एक्सरे", "एक्स रे", "एक्स-रे की", "xray ka", "xray ki", "x ray ka"),
    "chest": ("chest", "chest area", "छाती", "छाती के", "सीने", "सीने के", "chest ka", "chest ki", "chhati", "seena"),
    "urine": (
        "urine", "urinalysis", "urine routine", "urine sample", "urine analysis", "पेशाब", "यूरिन", "मूत्र", "पेशाब का", "पेशाब की",
        "यूरिन की", "urine ka", "urine ki", "peshab ka", "peshab ki",
    ),
    "cervical": ("cervical", "cervix", "pap", "pap smear", "बच्चेदानी", "सर्वाइकल", "पैप", "बच्चेदानी के मुंह", "cervix ka", "pap ka"),
}


_PROCEDURE_GROUPS = frozenset({"imaging", "ultrasound", "xray"})


def _nouns_of(group: TestGroup) -> tuple[str, ...]:
    return group.nouns + _EXTRA_NOUNS.get(group.key, ())


def mentions_something_else(text: str) -> bool:
    """The sentence is about a doctor, a report, a time or date, a place, a cancellation, a symptom ... -- not simply
    "which / how much" of a kind of test. Used to keep a generic phrase ("blood test") from answering "when will my
    blood test report be ready"."""
    norm = _norm(text)
    tokens = norm.split()
    return bool(norm) and _has_phrase_stem(norm, tokens, _NOT_ABOUT_TESTS)
