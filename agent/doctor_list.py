"""A caller asking WHICH doctors there are, naming none of them.

Ported from `merged_code/agent/doctor_list.py` (story: "Caller asks which doctors
are available"). Two shapes of the same question:

    "কে কে ডাক্তার আছেন"            -> every doctor (no department named)
    "which doctors are in cardiology" -> one department's doctors

WHY THIS IS NOT COVERED BY ANY EXISTING GUARD OR INTENT:

`doctor_availability` and `doctor_schedule`-style questions all require a NAMED
doctor, and a caller asking for the list cannot name one. `department_query`
takes a SYMPTOM and routes it to a department -- it cannot answer "what
departments do you have". So before this module the question reached the model
with nothing to look up, and this repository had no endpoint to look it up with
either; `/api/v1/doctors` and `/api/v1/doctors/by-department` were added for it.

WHY THIS MODULE IS ~150 LINES AND NOT THE ORIGINAL'S 423:

The module this was ported from carried its own department-name extractor --
plural detection, reduplicated question words, Bengali case endings, fuzzy
department scoring with its own floor and margin, a `_NOT_A_DEPARTMENT` stop
list. All of it existed to pull a department name out of the sentence BEFORE
asking the API, which meant a text matcher in `agent/` was guessing at a name it
had no list to check itself against.

None of that is needed here. `/api/v1/doctors/by-department` matches the query
against the real department table in BOTH directions -- the department name
inside the caller's sentence, or the caller's short form inside the department
name -- so the whole utterance can simply be handed over, and the clinic's own
data decides. Several matches come back `ambiguous` with the names offered, a
near match comes back as `did_you_mean`, and neither is ever resolved silently.
That is the same no-guessing discipline `_find_doctor` and `_find_test` already
enforce, and it is enforced against the catalogue rather than re-implemented
against a hand-written list.

So this module answers exactly one question -- "is this a request for the list?"
-- and `main.py` asks the API the rest.

REASONED, not measured: the Hindi list is new in this repository (the original
covered Bengali, English and Latin transliteration only). A native speaker
should review the Bengali and Hindi.
"""

from __future__ import annotations

import re
import unicodedata

# A plural/interrogative ask for doctors, with NO specific doctor named. Each
# entry pairs a "which/who/what" sense with a doctor word, because either alone
# is far too broad: "which day" and "the doctor" are both ordinary turns.
_EN = (
    "which doctors",
    "what doctors",
    "who are the doctors",
    "who all are the doctors",
    "list of doctors",
    "list the doctors",
    "doctors list",
    "all the doctors",
    "all doctors",
    "what are the doctors",
    "which doctor do you have",
    "which doctors do you have",
    "what doctors do you have",
    "who are your doctors",
    "your doctors",
    "doctors available",
    "doctors are available",
    "which specialists",
    "what specialists",
    "list of departments",
    "which departments",
    "what departments",
)

_LATIN = (
    "ke ke doctor",
    "kon kon doctor",
    "kara kara doctor",
    "doctor der list",
    "doctorder list",
    "ki ki doctor",
    "kon doctor ache",
    "kaun kaun doctor",
    "kaun kaun se doctor",
    "konse doctor hain",
    "kaun se doctor hain",
    "doctor ki list",
    "doctoron ki list",
    "sare doctor",
    "sab doctor",
    "kon kon bibhag",
    "kaun kaun vibhag",
)

_BN = (
    "কে কে ডাক্তার",
    "কারা কারা ডাক্তার",
    "কোন কোন ডাক্তার",
    "কি কি ডাক্তার",
    "কী কী ডাক্তার",
    "ডাক্তারদের তালিকা",
    "ডাক্তারের তালিকা",
    "সব ডাক্তারের নাম",
    "কোন ডাক্তাররা আছেন",
    "কোন ডাক্তাররা বসেন",
    "কে কে বসেন",
    "কোন কোন বিভাগ",
    "কি কি বিভাগ",
    "বিভাগের তালিকা",
)

# NEW in this repository: the original had no Devanagari list.
_HI = (
    "कौन कौन डॉक्टर",
    "कौन कौन से डॉक्टर",
    "कौन से डॉक्टर हैं",
    "कौन डॉक्टर हैं",
    "क्या क्या डॉक्टर",
    "डॉक्टरों की सूची",
    "डॉक्टर की लिस्ट",
    "डॉक्टरों की लिस्ट",
    "सभी डॉक्टरों के नाम",
    "सब डॉक्टर",
    "आपके डॉक्टर कौन",
    "कौन कौन विभाग",
    "कौन से विभाग",
    "विभागों की सूची",
)

_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("en", _EN),
    ("latin", _LATIN),
    ("bn", _BN),
    ("hi", _HI),
)

# WHY THERE IS NO "a doctor was named, so this is not a list request" CHECK:
#
# One was written first, as a regex for a doctor title followed by a name across
# the three scripts, and it was WRONG in a way worth recording. In Bengali and
# Hindi the words after the title are ordinary grammar, not a surname --
# "ডাক্তার আছেন" ("are there doctors"), "डॉक्टर हैं" ("are doctors") -- so the
# check read almost every genuine list request in those languages as naming a
# doctor and silently suppressed it. It suppressed 8 of 15 real requests, all of
# them non-English: precisely the trilingual failure this merge exists to avoid.
#
# It is also unnecessary. The phrase lists above all pair a plural or
# interrogative sense with a doctor word, so an availability or booking question
# about a NAMED doctor matches none of them -- verified in
# tests/test_doctor_list.py across all three scripts, including "is Dr Sen
# available tomorrow", "ডক্টর সেনের কাছে অ্যাপয়েন্টমেন্ট" and
# "डॉक्टर सेन के साथ अपॉइंटमेंट". Specific phrases do the job a loose regex
# could not.


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").lower()


# ---- the same question by its STRUCTURE, not its exact words ----------------------------------------------------
#
# The phrase lists above missed the two questions of a live call on 2026-10-07, both plainly "which doctors / which
# departments do you have":
#
#     আপনাদের ওখানে কোন কোন ডক্টর বসেন            ("ডক্টর", the English loanword; the list had only "ডাক্তার")
#     কোন কোন ডিপার্টমেন্ট আছে আপনাদের ওখানে       ("ডিপার্টমেন্ট"; the list had only "বিভাগ")
#
# Callers here say doctor and department in three scripts plus English loanwords, with case endings, in any word order.
# Enumerating every combination is the arms race the phrase list was losing. So the question is also recognised by its
# shape: a LIST-ASKING word near a DOCTOR or DEPARTMENT word.
#
#   list-asking  a reduplicated question word (কোন কোন, কে কে, কী কী, कौन कौन, kon kon) -- the Bengali/Hindi plural ask;
#                "how many" (কয়টা, কতগুলো, कितने); list (তালিকা, লিস্ট, सूची); "all" (সব, সমস্ত, सभी) but only RIGHT
#                beside the subject, because "সব ঠিক আছে" ("all fine") is an ordinary turn.
#   subject      the doctor or department word in any script, matched by its stem so "ডাক্তারদের", "ডিপার্টমেন্টের",
#                "विभागों" count.
#
# A single question word ("কোন ডাক্তার দেখাব" -- "which doctor should I see") is NOT a list request: that caller wants
# to be helped to choose, which the booking flow does. One exception, for departments only: a single question word plus
# "is there / are there" ("আপনাদের কোন ডিপার্টমেন্ট আছে?") -- unless the department word is locative ("কোন বিভাগে আছেন",
# "in which department is he"), which asks something else.
_DOCTOR_STEMS = (
    "ডাক্তার",
    "ডক্টর",
    "ডাঃ",
    "স্পেশালিস্ট",
    "বিশেষজ্ঞ",
    "डॉक्टर",
    "डाक्टर",
    "स्पेशलिस्ट",
    "विशेषज्ञ",
    "doctor",
    "daktar",
    "specialist",
)
_DEPARTMENT_STEMS = (
    "বিভাগ",
    "ডিপার্টমেন্ট",
    "ডিপার্টমেণ্ট",
    "ডিপার্টমেন",
    "विभाग",
    "डिपार्टमेंट",
    "डिपार्टमेन्ट",
    "department",
    "dept",
    "bibhag",
    "vibhag",
)
_QUESTION_WORDS = frozenset(
    {"কোন", "কে", "কি", "কী", "কারা", "কোনটা", "कौन", "क्या", "kon", "ke", "ki", "kara", "kaun", "kya", "which", "what"}
)
_HOW_MANY = frozenset({"কয়টা", "কটা", "কতগুলো", "কতগুলি", "কতজন", "কয়জন", "कितने", "कितनी", "kitne", "koyta", "many"})
_LIST_WORDS = frozenset({"তালিকা", "লিস্ট", "list", "सूची", "लिस्ट"})
_ALL_WORDS = frozenset({"সব", "সমস্ত", "সকল", "সবাই", "all", "सभी", "सब", "सारे", "sab", "sare", "saare"})
_EXISTS = frozenset(
    {"আছে", "আছেন", "রয়েছে", "রয়েছেন", "हैं", "है", "have", "has", "are", "there", "available", "ache", "achhe"}
)
_LOCATIVE_ENDINGS = ("ে", "এ", "मे", "में")
_WORD_SPLIT = re.compile(r"[\s,.!?।॥;:'\"()\-–—/]+")
_NEAR = 2  # list-asking word to subject, in words


def _words(text: str) -> list[str]:
    return [w for w in _WORD_SPLIT.split(_normalise(text)) if w]


def _stem_hit(word: str, stems: tuple[str, ...]) -> bool:
    return any(word.startswith(s) for s in stems)


def _asks_by_structure(words: list[str]) -> bool:
    subjects = [i for i, w in enumerate(words) if _stem_hit(w, _DOCTOR_STEMS) or _stem_hit(w, _DEPARTMENT_STEMS)]
    if not subjects:
        return False

    def near(i: int, reach: int = _NEAR) -> bool:
        return any(abs(i - s) <= reach for s in subjects)

    for i, w in enumerate(words):
        # The reduplicated question must GOVERN the doctor/department word: it comes first, and close.
        # "কোন কোন দিন বসেন ডাক্তার দাস" is "on which days does Dr Das sit" -- the plural "which" is about days.
        if w in _QUESTION_WORDS and i + 1 < len(words) and words[i + 1] == w:
            if any(i + 1 < s <= i + 1 + _NEAR for s in subjects):
                return True  # কোন কোন ডক্টর, কোন কোন ডিপার্টমেন্ট এর ডক্টর, कौन कौन से डॉक्टर, kon kon department
        if (w in _HOW_MANY or w in _LIST_WORDS) and near(i):
            return True
        if w in _ALL_WORDS and near(i, 1):
            return True
    departments = [i for i, w in enumerate(words) if _stem_hit(w, _DEPARTMENT_STEMS)]
    for d in departments:
        if words[d].endswith(_LOCATIVE_ENDINGS):
            continue
        asked = any(words[j] in _QUESTION_WORDS for j in range(max(0, d - 3), d))
        if asked and any(w in _EXISTS for w in words):
            return True  # আপনাদের কোন ডিপার্টমেন্ট আছে
        if _asks_far_in_english(words, d):
            return True  # which outdoor or open department do you have
    return False


# English only, and only in one exact shape. Live 2026-10-09: "which outdoor or open department do you have" has the
# question word FOUR words before "department" (qualifiers pile up in English: "which special outpatient department"),
# one past the 3-word reach above, so the ask was not recognised, went to the model, and the model filed the whole
# sentence as a symptom -- "Sorry, I couldn't quite tell which department this needs". The reach is NOT widened for
# every sentence: "what time is the orthopaedics department available" has the same distance and the word "available"
# and is a different question. Beyond 3 words only the tail "... department do you have" counts.
_FAR_QUESTION_WORDS = frozenset({"which", "what"})
_FAR_REACH = 5


def _asks_far_in_english(words: list[str], d: int) -> bool:
    if not any(words[j] in _FAR_QUESTION_WORDS for j in range(max(0, d - _FAR_REACH), d - 3)):
        return False
    return "you" in words[d + 1 : d + 3] and any(w in ("have", "has") for w in words[d + 2 : d + 4])


def detect_doctor_list_request(text: str) -> tuple[bool, str | None]:
    """True, plus how it was recognised, when the caller is asking which
    doctors (or departments) the clinic has, without naming a doctor.

    The tag is for logging only, never the reply language: a phrase family
    ("en", "latin", "bn", "hi") or "structure" for the shape-based rule above.
    """
    if not text or not text.strip():
        return False, None
    raw = unicodedata.normalize("NFC", text)
    lowered = raw.lower()
    for tag, phrases in _FAMILIES:
        if any((p in lowered) if p.isascii() else (p in raw) for p in phrases):
            return True, tag
    if _asks_by_structure(_words(text)):
        return True, "structure"
    return False, None


def asks_for_the_doctor_list(text: str) -> bool:
    """The one question this module answers for the orchestrator."""
    matched, _ = detect_doctor_list_request(text)
    return matched
