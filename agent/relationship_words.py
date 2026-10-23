"""Is a "relationship" slot value really a relationship?

The intent model fills `relationship` ("who the patient is to the caller, when it is not themselves") and, measured
on the pod on 2026-10-10, fills it with words that are not one:

    "আমি গাইনির একজন ডাক্তারকে দেখাবো"   relationship = "একজন"   ("one person" -- from "a doctor")
    "রোগীর নাম রাজু"                      relationship = "রোগী"    ("patient" -- from "patient's name")
    "রোগীর নাম রাজস্বী"                   relationship = "রোগীর"

The value was stored in the booking as a fact. The second one then counted as a CORRECTION of the first, and the
caller was told "ঠিক আছে, বদলে সম্পর্ক রোগী করে দিলাম" -- "I have changed the relationship to patient" -- which is
neither something they said nor something true. The same field is written onto the confirmed booking, and read to
decide whether the patient is a senior.

CLAUDE.md section 1: the model may decide what the caller wants, never what is true. A relationship is a fact about
the patient, so it is accepted only when it IS one of the words a caller uses for a relation. Anything else is not
stored (the booking then says "self", as it always did when nothing was said). A relation word this list does not
know costs exactly that default -- the same as the caller not having said it -- never a wrong value.

The list is Bengali, Hindi and English, with the case endings a caller adds ("মায়ের", "ছেলেকে", "बेटे की").
REASONED, not measured against real calls: extend it from the live transcripts the clinic sees.
"""

from __future__ import annotations

import re
import unicodedata

_BN = (
    "মা", "আম্মু", "আম্মা", "মাতা", "বাবা", "আব্বু", "আব্বা", "পিতা", "ছেলে", "পুত্র", "মেয়ে", "কন্যা", "স্ত্রী", "বউ",
    "বৌ", "স্বামী", "বর", "ভাই", "বোন", "দাদা", "দিদি", "দাদু", "ঠাকুমা", "ঠাম্মা", "ঠাকুরদা", "ঠাকুরদাদা", "দিদা",
    "দিদিমা", "দাদি", "নানা", "নানি", "কাকা", "কাকু", "কাকি", "কাকিমা", "জ্যাঠা", "জেঠু", "জ্যাঠামশাই", "জেঠিমা", "মামা",
    "মামি", "মামী", "মাসি", "মাসী", "মাসিমা", "পিসি", "পিসিমা", "পিসেমশাই", "মেসো", "শ্বশুর", "শাশুড়ি", "শাশুড়ী", "বৌমা",
    "জামাই", "ভাইপো", "ভাইঝি", "ভাগ্নে", "ভাগ্নি", "ভাগ্নী", "ভাতিজা", "ভাতিজি", "বন্ধু", "বান্ধবী", "প্রতিবেশী", "আত্মীয়",
    "নাতি", "নাতনি", "নাতনী", "নাতজামাই", "বাচ্চা", "শিশু", "সন্তান", "ননদ", "দেবর", "ভাশুর", "বেয়াই", "সতীন", "নিজে",
    "নিজের", "পরিবার", "আত্মীয়স্বজন", "মালিক", "কর্মচারী", "সহকর্মী",
)
_HI = (
    "माँ", "मां", "माता", "मम्मी", "मॉम", "पिता", "पापा", "बाप", "डैड", "बेटा", "बेटे", "बेटी", "पुत्र", "पुत्री", "पत्नी",
    "बीवी", "पति", "भाई", "बहन", "दोस्त", "सहेली", "दादा", "दादी", "नाना", "नानी", "चाचा", "चाची", "ताऊ", "ताई", "मामा",
    "मामी", "मौसी", "मौसा", "बुआ", "फूफा", "भतीजा", "भतीजी", "भांजा", "भांजी", "ससुर", "सास", "बहू", "दामाद", "जीजा",
    "साला", "साली", "देवर", "भाभी", "ननद", "बच्चा", "बच्ची", "बच्चे", "पड़ोसी", "रिश्तेदार", "खुद", "अपना", "अपनी", "पोता",
    "पोती", "नाती", "परिवार", "मालिक", "कर्मचारी",
)
_EN = (
    "mother", "mom", "mum", "mommy", "mummy", "mama", "father", "dad", "daddy", "papa", "son", "daughter", "wife", "husband",
    "spouse", "partner", "brother", "sister", "friend", "grandmother", "grandfather", "grandma", "grandpa", "granny",
    "grandson", "granddaughter", "uncle", "aunt", "aunty", "auntie", "cousin", "nephew", "niece", "relative", "relation",
    "neighbour", "neighbor", "colleague", "coworker", "employee", "boss", "child", "kid", "baby", "parent", "parents",
    "family", "self", "myself", "me", "in-law", "inlaw", "fiance", "fiancee", "boyfriend", "girlfriend", "maid", "driver",
    "servant", "helper", "student", "teacher",
)

# Case endings a caller adds to a relation word ("মায়ের" = মা + য়ের, "ছেলেকে", "बेटे की" is two tokens already).
_BN_ENDINGS = ("র", "ের", "য়ের", "কে", "ও", "টা", "টি", "ই", "রা", "দের", "েরও", "রই", "য়ের", "কেও")
_HI_ENDINGS = ("का", "की", "के", "को", "ने", "से", "में", "ही", "भी", "जी")
_EN_ENDINGS = ("s", "'s")

_WORD = re.compile(r"[\wঀ-৿ऀ-ॿ'’-]+", re.UNICODE)


def _stems(words: tuple[str, ...]) -> frozenset[str]:
    return frozenset(unicodedata.normalize("NFC", w).casefold() for w in words)


_KNOWN = _stems(_BN) | _stems(_HI) | _stems(_EN)


def _is_relation_token(token: str) -> bool:
    if token in _KNOWN:
        return True
    for ending in (*_BN_ENDINGS, *_HI_ENDINGS, *_EN_ENDINGS):
        if token.endswith(ending) and len(token) > len(ending) and token[: -len(ending)] in _KNOWN:
            return True
    return False


def is_a_relationship(value: object) -> bool:
    """True when `value` carries at least one word a caller uses for a relation. Not a name, not "one person",
    not "patient"."""
    if not isinstance(value, str) or not value.strip():
        return False
    text = unicodedata.normalize("NFC", value).casefold()
    return any(_is_relation_token(tok.strip("'’-")) for tok in _WORD.findall(text))


def clean_relationship(slots: dict) -> dict:
    """`slots` without a `relationship` value that is not one. Every other slot is returned untouched."""
    if "relationship" in slots and slots.get("relationship") and not is_a_relationship(slots["relationship"]):
        return {k: v for k, v in slots.items() if k != "relationship"}
    return slots
