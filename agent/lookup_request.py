"""Is the caller asking about a booking they ALREADY HAVE, rather than asking for a new one?

story: "Caller asks what they have booked"

WHY THIS IS CODE AND NOT LEFT TO THE MODEL
------------------------------------------
`lookup_booking` is classified by the model (agent/llm.py), and that stays the general
mechanism -- a classifier generalises over phrasing in a way no list of strings can. This module
is a narrow safety net in front of it, for the ONE distinction that is both easy to get wrong and
expensive when it is:

    "আমার একটা অ্যাপয়েন্টমেন্ট আছে?"   do I have an appointment?      -> read it back
    "আমার একটা অ্যাপয়েন্টমেন্ট চাই"    I want an appointment          -> book a new one

One word apart. Heard the wrong way round, the agent starts collecting a SECOND booking for a
caller who only wanted to be reminded of the first -- the same defect the reschedule story was
written to fix ("the caller was asked five questions and given a SECOND appointment"), arriving
by a different route.

A hit skips the model for that turn. A MISS changes nothing: the turn goes to the model exactly
as before, so a phrasing this does not cover costs a classification, never a wrong answer. That
is the same trade agent/earliest_request.py and agent/lay_terms.py already make, and the reason
the rule below is deliberately narrow rather than a long list of sentences.

Deciding what the caller WANTS is allowed here; deciding what is TRUE is not (CLAUDE.md section
1). This module returns a boolean about intent and never touches a booking.

THE RULE. All three must hold:

  1. no want-verb anywhere      -- "চাই", "चाहिए", "I want" veto outright, checked FIRST
  1b. no OTHER action asked of   -- "আমার বুক করা টেস্টটা বাড়ি থেকে নেওয়া যাবে?" names a booking the caller
      that same booking            already has, but what it ASKS FOR is a home visit, not a read-back.
                                   Same shape as rule 1: a different action vetoes.
  2. a booking noun            -- appointment / booking, in any of the four scripts
  3. an existing-booking marker -- an existence or past verb ("আছে", "ছিল", "है", "बुक है"),
                                  a "when" ("কবে", "कब"), or an English question form ("do I have",
                                  "when is"). A possessive ALONE is not one: "আমার মায়ের জন্য ডাক্তার
                                  সেনের অ্যাপয়েন্টমেন্ট", "An appointment with Dr Sen for my mother" are
                                  new bookings, and were read back as lookups while "my" counted.

Precision over recall on purpose: 2 and 3 together are what separate "my appointment" from
"an appointment", and the veto in 1 is what stops a sentence that contains both a possessive and
a request ("আমার জন্য একটা অ্যাপয়েন্টমেন্ট চাই") from being read as a lookup.

REASONED, NOT MEASURED. The Bengali forms are the ones this clinic's own templates use; the
Hindi and the romanised forms were written by a non-native reviewer, the same caveat
agent/fast_path_cues.py carries. tools/lookup_intent_eval.py scores this module against a
labelled set; the MODEL half of the decision needs the pod and is unmeasured here.
"""

from __future__ import annotations

import re
import unicodedata

from agent.earliest_request import wants_earliest

# Indic scripts have no usable \b, so a cue is matched at a word START -- a suffixed form
# ("অ্যাপয়েন্টমেন্টটা", "অ্যাপয়েন্টমেন্টের") still matches, and the cue cannot fire inside an
# unrelated word. Same idiom as agent/earliest_request.py.
_INDIC_LETTER = r"[ঀ-৿ऀ-ॿ]"

# 1. A REQUEST for something new. Checked first and vetoes everything below: a sentence that
#    asks for an appointment is a booking even when it also says "my".
_WANT_INDIC = (
    # Bengali
    "চাই", "চাইছি", "চাচ্ছি", "দরকার", "করতে চাই", "করবেন", "করে দিন", "করা যাবে", "নিতে চাই",
    "লাগবে", "করাব", "করাবো", "দেখাব", "দেখাবো", "দেখাতে চাই",
    # Hindi
    "चाहिए", "चाहता", "चाहती", "करना है", "करवाना है", "करा दीजिए", "बुक कर", "लेना है",
    "करनी है", "करानी है", "करवानी है", "दिखाना है", "बुकिंग करनी",
)
_WANT_LATIN = re.compile(
    r"\b(?:i\s+want|i\s+need|i'?d\s+like|i\s+would\s+like|can\s+i\s+(?:get|book|have)|"
    r"book\s+me|please\s+book|^\s*book|make\s+(?:me\s+)?an?\s+appointment|"
    r"chai|chaai|lagbe|dorkar|korte\s+chai|chahiye)\b",
    re.IGNORECASE,
)

# 1b. A DIFFERENT ACTION asked of a booking the caller already has. Rule 1 vetoes a request for a
#     NEW booking; this vetoes a request to do something ELSE with an existing one, which is the
#     same mistake arriving from the other side: "আমার বুক করা টেস্টটা বাড়ি থেকে নেওয়া যাবে?" satisfies
#     rules 2 and 3 ("আমার", "বুক করা") and is nonetheless a home-collection request, not a
#     read-back. Home collection is the one such action with its own whole conversation (the
#     "home_collection" intent in agent/llm.py, agent/home_collection_flow.py), so a turn carrying
#     its cues abstains here and the MODEL classifies it -- exactly what this module's docstring
#     says a miss should cost: a classification, never a wrong answer.
#     Cancel/reschedule are deliberately NOT vetoed: those flows start from a booking that was
#     read back first, so reaching the lookup before them is the right order.
_OTHER_ACTION_INDIC = (
    # Bengali -- "from home", "at home", "home collection"
    "বাড়ি থেকে", "বাড়িতে", "বাসা থেকে", "বাসায়", "হোম কালেকশন", "হোম কোলেকশন",
    # Hindi
    "घर से", "घर पर", "घर पे", "होम कलेक्शन",
)
_OTHER_ACTION_LATIN = re.compile(
    r"\b(?:home\s*collection|home\s*visit|(?:from|at)\s+home|collect(?:ed|ion)?\s+(?:from|at)\s+home|bari\s*theke|barite|basa\s*theke|ghar\s*se|ghar\s*p[ae])\b",
    re.IGNORECASE,
)


# 2. The thing being asked about.
_NOUN_INDIC = (
    "অ্যাপয়েন্টমেন্ট", "অ্যাপয়েনমেন্ট", "অ্যাপোয়েন্টমেন্ট", "এপয়েন্টমেন্ট", "বুকিং", "বুক",
    "অপয়েন্টমেন্ট", "সিরিয়াল",
    "अपॉइंटमेंट", "अपॉइनमेंट", "एपॉइंटमेंट", "बुकिंग", "बुक", "सीरियल", "नंबर लगा",
)
_NOUN_LATIN = re.compile(r"\b(?:appointment|appointments|appt|booking|booked|slot|serial)\b", re.IGNORECASE)

# 3. Something that marks it as one they ALREADY have: whose it is, that it exists, that it was
#    made, or a bare "when is it".
_HAVE_INDIC = (
    # Bengali -- existence, past, and "when"
    "আছে", "ছিল", "করেছিলাম", "করেছি", "করিয়েছিলাম", "নেওয়া আছে", "কবে", "কোন দিন",
    "কখন", "কটায়", "বুক করা", "হয়েছিল", "দেওয়া আছে",
    # Hindi
    "कब", "किस दिन", "कितने बजे", "बुक है", "करवाया", "कराया", "लिया था",
)
# Short Hindi verbs matched as WHOLE words: "है" is also the start of "हैं" ("क्या बिना बुकिंग के सीधे आ
# सकते हैं?" -- can I come without booking -- was read as a lookup through it).
_HAVE_INDIC_WORDS = ("है", "थी", "था")
_HAVE_LATIN = re.compile(
    r"\b(?:have\s+i|did\s+i|do\s+i\s+have|when\s+is|when'?s|what\s+is|what'?s|"
    r"ache|achhe|chilo|chhilo|kobe|kokhon|kon\s+din|korechilam|korechi|kab)\b",
    re.IGNORECASE,
)


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def _indic_hit(cues: tuple[str, ...], text: str) -> bool:
    return any(re.search(rf"(?<!{_INDIC_LETTER}){re.escape(_nfc(cue))}", text) for cue in cues)


def _indic_word(cues: tuple[str, ...], text: str) -> bool:
    return any(
        re.search(rf"(?<!{_INDIC_LETTER}){re.escape(_nfc(cue))}(?!{_INDIC_LETTER})", text) for cue in cues
    )


def wants_new_booking(text: str) -> bool:
    """Does the turn ASK for an appointment? The veto in rule 1, exposed for the tests and for
    anything else that needs to tell a request from a question."""
    t = _nfc(text)
    return _indic_hit(_WANT_INDIC, t) or _WANT_LATIN.search(t) is not None


def asks_about_another_action(text: str) -> bool:
    """Does the turn ask for something to be DONE with the booking rather than read back? Rule 1b,
    exposed alongside wants_new_booking() for the same reason: the tests and the eval need it."""
    t = _nfc(text)
    return _indic_hit(_OTHER_ACTION_INDIC, t) or _OTHER_ACTION_LATIN.search(t) is not None


def asks_for_the_earliest(text: str) -> bool:
    """Rule 1c: "when can I GET the earliest appointment?" -- story "Caller asks for the earliest available
    appointment". It has the noun and the "when" of rules 2 and 3, so it was read as "what have I booked" in all three
    languages (live 2026-10-07: "डॉक्टर निर्मल रॉय का सबसे जल्दी अपॉइंटमेंट कब मिलेगा?" -> "tell me the number you booked
    with"). agent/earliest_request.py is the one definition of that request; the booking flow already uses it."""
    return any(wants_earliest(text, lang) for lang in ("bn", "hi", "en"))


def mentions_a_booking(text: str) -> bool:
    """Does the turn name an appointment / booking at all? Rule 2 on its own, exposed for
    agent/doctor_in_department_ask.py, which must leave every sentence about a booking alone."""
    t = _nfc(text)
    return _indic_hit(_NOUN_INDIC, t) or _NOUN_LATIN.search(t) is not None


def asks_what_is_booked(text: str) -> bool:
    """Is the caller asking what they already have booked?

    Script, not language, decides which cues can match, so a code-mixed turn ("amar appointment
    ta kobe?") is read by whichever set its words are actually in -- there is no `lang` argument
    for that reason.
    """
    t = _nfc(text)
    if wants_new_booking(t) or asks_about_another_action(t) or asks_for_the_earliest(t):
        return False
    if not (_indic_hit(_NOUN_INDIC, t) or _NOUN_LATIN.search(t)):
        return False
    return _indic_hit(_HAVE_INDIC, t) or _indic_word(_HAVE_INDIC_WORDS, t) or _HAVE_LATIN.search(t) is not None
