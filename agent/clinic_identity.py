"""The clinic's own name -- "Sonoscan Vaani" -- is never an entity to look up. It is us.

Live call, 2026-10-08:

    [AI]   আপনি সোনোস্ক্যান বাণীর সঙ্গে কথা বলছেন...
    [User] সোনস্কানের অযাড্ড্রেস টা কি          ("what is Sonoscan's address")
    [AI]   দুঃখিত, 'সোনস্কান' নামে কোনো টেস্ট আমাদের তালিকায় নেই

The agent had said its own name one turn earlier, the caller said it back, and the agent looked for a lab test
by that name. Nothing in the system knew the name belonged to it: the intent model saw an unfamiliar noun in
front of a question word and filed it the way it files most unfamiliar nouns, as a test.

So the name is stripped before a sentence is matched against the catalogue or the FAQ keywords. "সোনস্কানের
অযাড্ড্রেস টা কি" becomes "অযাড্ড্রেস টা কি", which is the address question it always was.

SPELLINGS, not a fuzzy match. Each is one the recogniser actually produces or plausibly would; a fuzzy rule
wide enough to catch them all would also catch "সোনো" inside a real word. A spelling that is missed costs one
clarifying question, which is the safe direction to be wrong in.

The agent's given name ("বাণী" / "वाणी" / "Vaani") is deliberately NOT here on its own: it is also an ordinary
Bengali word and a woman's name, and stripping it from "রোগীর নাম বাণী" would delete a patient.

Pure: no I/O.
"""

from __future__ import annotations

import re
import unicodedata

# Written as a flat list so a new ASR spelling is a one-line addition.
_CLINIC_NAMES = (
    # Bengali -- the agent's own greeting spells it সোনোস্ক্যান; callers and the recogniser vary the vowels
    "সোনোস্ক্যান",
    "সোনোস্কান",
    "সোনস্ক্যান",
    "সোনস্কান",
    "সনোস্ক্যান",
    "সনোস্কান",
    "সোনোস্কেন",
    "সোনোস্ক্যানের",
    # Hindi
    "सोनोस्कैन",
    "सोनोस्कान",
    "सोनस्कैन",
    "सोनस्कान",
    # English / romanised
    "sonoscan",
    "sonascan",
    "sono scan",
    "sonoscan vaani",
    "sonoscan bani",
)
_CLINIC_NAMES = tuple(unicodedata.normalize("NFC", n) for n in _CLINIC_NAMES)

# Longest first, so "সোনোস্ক্যানের" is removed whole rather than leaving a stray suffix behind. Bengali and
# Hindi take case endings straight onto the noun ("সোনস্কানের" = "Sonoscan's"), so a match may be followed by
# more letters: \S* absorbs them. The English forms need a word boundary instead, or "sonoscan" would match
# inside an unrelated word.
_PATTERN = re.compile(
    "|".join(
        (re.escape(n) + r"\S*") if not n.isascii() else (r"\b" + re.escape(n) + r"\b")
        for n in sorted(_CLINIC_NAMES, key=len, reverse=True)
    ),
    re.IGNORECASE,
)
_RE_WS = re.compile(r"\s+")


def names_the_clinic(text: str) -> bool:
    """Does this sentence name US? ("সোনস্কানের ঠিকানা", "is this Sonoscan?")"""
    return bool(_PATTERN.search(unicodedata.normalize("NFC", text or "")))


def strip_clinic_name(text: str) -> str:
    """The sentence with our own name taken out, for matching against the catalogue and the FAQ keywords.

    Returns the text unchanged when the name is not in it, and never returns an empty string for a sentence
    that had other words in it."""
    normalized = unicodedata.normalize("NFC", text or "")
    if not _PATTERN.search(normalized):
        return text
    return _RE_WS.sub(" ", _PATTERN.sub(" ", normalized)).strip()
