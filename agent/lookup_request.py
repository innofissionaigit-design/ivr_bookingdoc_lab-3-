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
  2. a booking noun            -- appointment / booking, in any of the four scripts
  3. an existing-booking marker -- a possessive ("আমার", "my"), an existence or past verb
                                  ("আছে", "ছিল", "है", "बुक है"), or a bare "when" ("কবে", "कब")

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

# Indic scripts have no usable \b, so a cue is matched at a word START -- a suffixed form
# ("অ্যাপয়েন্টমেন্টটা", "অ্যাপয়েন্টমেন্টের") still matches, and the cue cannot fire inside an
# unrelated word. Same idiom as agent/earliest_request.py.
_INDIC_LETTER = r"[ঀ-৿ऀ-ॿ]"

# 1. A REQUEST for something new. Checked first and vetoes everything below: a sentence that
#    asks for an appointment is a booking even when it also says "my".
_WANT_INDIC = (
    # Bengali
    "চাই", "চাইছি", "চাচ্ছি", "দরকার", "করতে চাই", "করবেন", "করে দিন", "করা যাবে", "নিতে চাই",
    # Hindi
    "चाहिए", "चाहता", "चाहती", "करना है", "करवाना है", "करा दीजिए", "बुक कर", "लेना है",
)
_WANT_LATIN = re.compile(
    r"\b(?:i\s+want|i\s+need|i'?d\s+like|i\s+would\s+like|can\s+i\s+(?:get|book|have)|"
    r"book\s+me|please\s+book|make\s+(?:me\s+)?an?\s+appointment|"
    r"chai|chaai|lagbe|dorkar|korte\s+chai|chahiye)\b",
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
    # Bengali -- possessive, existence, past, and "when"
    "আমার", "আমাদের", "আছে", "ছিল", "করেছিলাম", "করেছি", "করিয়েছিলাম", "নেওয়া আছে", "কবে", "কোন দিন",
    "কখন", "কটায়", "বুক করা", "হয়েছিল", "দেওয়া আছে",
    # Hindi
    "मेरी", "मेरा", "मेरे", "हमारी", "है", "थी", "था", "कब", "किस दिन", "कितने बजे", "बुक है",
    "करवाया", "कराया", "लिया था",
)
_HAVE_LATIN = re.compile(
    r"\b(?:my|our|have\s+i|did\s+i|do\s+i\s+have|when\s+is|when'?s|what\s+is|what'?s|"
    r"amar|amader|ache|achhe|chilo|chhilo|kobe|kokhon|kon\s+din|korechilam|korechi|"
    r"meri|mera|mere|kab)\b",
    re.IGNORECASE,
)


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def _indic_hit(cues: tuple[str, ...], text: str) -> bool:
    return any(re.search(rf"(?<!{_INDIC_LETTER}){re.escape(_nfc(cue))}", text) for cue in cues)


def wants_new_booking(text: str) -> bool:
    """Does the turn ASK for an appointment? The veto in rule 1, exposed for the tests and for
    anything else that needs to tell a request from a question."""
    t = _nfc(text)
    return _indic_hit(_WANT_INDIC, t) or _WANT_LATIN.search(t) is not None


def asks_what_is_booked(text: str) -> bool:
    """Is the caller asking what they already have booked?

    Script, not language, decides which cues can match, so a code-mixed turn ("amar appointment
    ta kobe?") is read by whichever set its words are actually in -- there is no `lang` argument
    for that reason.
    """
    t = _nfc(text)
    if wants_new_booking(t):
        return False
    if not (_indic_hit(_NOUN_INDIC, t) or _NOUN_LATIN.search(t)):
        return False
    return _indic_hit(_HAVE_INDIC, t) or _HAVE_LATIN.search(t) is not None
