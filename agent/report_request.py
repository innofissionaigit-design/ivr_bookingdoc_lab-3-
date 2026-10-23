"""Is the caller asking about THEIR lab report -- is it ready, send it, collect it?

stories: "Caller asks whether their report is ready", "Caller asks for their report to be sent"

Scope decision (2026-10-07): every such request goes to the counter. This stack has no laboratory
connection to read a report's status from, and no one-time-password channel to send one through,
so the only true answer it can give is "the counter handles reports". Before this module the
turn reached the model, which has no intent for it: "আমার রিপোর্ট কি তৈরি হয়েছে?" was answered
"sorry, I did not understand", and "is my report ready?" filed as the report-collection FAQ was
told how reports are collected -- never that this one could be checked at the counter.

WHY THIS IS CODE AND NOT LEFT TO THE MODEL: same trade as agent/lookup_request.py. A hit skips
the model for that turn; a MISS changes nothing, so a phrasing this does not cover costs a
classification, never a wrong answer.

THE RULE. Both must hold:

  1. a report noun     -- report / result, in Bengali, Hindi and Latin script
  2. it is THEIR report, or something is asked of it
                       -- a possessive ("আমার", "मेरी", "my"), or a status or delivery word
                          ("তৈরি", "पाठिये", "ready", "send", "collect", "WhatsApp")

What rule 2 leaves to the model on purpose: "সিবিসি রিপোর্ট কতক্ষণে পাব?" / "how long does the CBC
report take?" -- a question about a TEST's turnaround time, which the price reply already answers
from the catalogue. Whether a result is GOOD or BAD never reaches here: agent/clinical_safety.py
runs earlier in the turn and answers that.

Deciding what the caller WANTS is allowed here; deciding what is TRUE is not (CLAUDE.md
section 1). This returns a boolean and states nothing about any report.

REASONED, NOT MEASURED -- the same caveat agent/lookup_request.py carries for its Hindi and
romanised forms.
"""

from __future__ import annotations

import re
import unicodedata

_INDIC_LETTER = r"[ঀ-৿ऀ-ॿ]"

# 1. The report itself.
_NOUN_INDIC = (
    "রিপোর্ট", "রিপোট", "রেজাল্ট", "ফলাফল",
    "रिपोर्ट", "रिपोट", "रिज़ल्ट", "रिजल्ट", "नतीजा",
)
_NOUN_LATIN = re.compile(r"\b(?:reports?|results?|riport|ripot)\b", re.IGNORECASE)

# 2a. Whose it is.
_MINE_INDIC = (
    "আমার", "আমাদের", "আমি",
    "मेरी", "मेरा", "मेरे", "हमारी", "हमारा", "मैं", "मुझे",
)
_MINE_LATIN = re.compile(r"\b(?:my|our|mine|i|me|amar|amader|meri|mera|mere)\b", re.IGNORECASE)

# 2b. Something asked of it: is it ready, send it, collect it, where is it.
_ASK_INDIC = (
    # Bengali
    "তৈরি", "রেডি", "হয়েছে", "হয়ে গেছে", "এসেছে", "পাঠা", "পাঠি", "পাঠান", "নিতে", "নেব", "সংগ্রহ",
    "কালেক্ট", "হোয়াটসঅ্যাপ", "ইমেল", "ইমেইল", "মেইল", "কোথায়", "স্ট্যাটাস",
    # Hindi
    "तैयार", "रेडी", "आ गई", "आ गया", "आई", "भेज", "ले सकत", "लेनी", "लेना", "कलेक्ट", "व्हाट्सऐप",
    "व्हाट्सएप", "ईमेल", "मेल", "कहाँ", "स्टेटस", "बन गई",
)
_ASK_LATIN = re.compile(
    r"\b(?:ready|send|sent|collect\w*|pick\s+up|download|whats\s*app|e-?mail|mail|status|"
    r"came|arrived|where\s+is|is\s+it\s+done|pathiye|pathan|bhej\w*|taiyar)\b",
    re.IGNORECASE,
)


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def _indic_hit(cues: tuple[str, ...], text: str) -> bool:
    return any(re.search(rf"(?<!{_INDIC_LETTER}){re.escape(_nfc(cue))}", text) for cue in cues)


def asks_about_report(text: str) -> bool:
    """Is the caller asking about their own report -- its status, delivery or collection?

    Script, not language, decides which cues can match, so a code-mixed turn ("amar report ready?")
    is read by whichever set its words are in -- no `lang` argument, as in agent/lookup_request.py.
    """
    t = _nfc(text)
    if not (_indic_hit(_NOUN_INDIC, t) or _NOUN_LATIN.search(t)):
        return False
    mine = _indic_hit(_MINE_INDIC, t) or _MINE_LATIN.search(t) is not None
    asked = _indic_hit(_ASK_INDIC, t) or _ASK_LATIN.search(t) is not None
    return mine or asked
