"""Did the caller ask for the SOONEST appointment, rather than a day of their own?

Story: "Caller asks for the earliest available appointment". A caller who wants to be seen soon
should hear the first free slot, not be asked "which day?" and made to guess dates until one is
free.

Decided by CODE, not the model, for the same reason agent/call_end.py and agent/human_request.py
are: it is a fixed phrase, the answer changes what the agent ASKS next (never what it states as
true), and routing it through the extractor would cost a model call and risk the intent drifting.
A phrase this misses simply falls through to the ordinary "which day?" question -- the old
behaviour -- so a miss costs a turn, never a wrong answer.

REASONED, NOT MEASURED. The Hindi and English phrase lists were written by a non-native reviewer
from the Bengali set, the same caveat agent/fast_path_cues.py carries, and no real call
transcripts exist yet to calibrate against. A native speaker should review them, and real ASR
output should be checked for how these phrases actually come back, before either is trusted.
"""

from __future__ import annotations

import re
import unicodedata

# Bengali and Devanagari have no usable \b, so an Indic cue is matched at a word START -- which
# still lets a suffixed form through ("তাড়াতাড়িই") without firing inside an unrelated word.
_INDIC_CUES = {
    "bn": ("তাড়াতাড়ি", "তারাতারি", "তাড়াতারি", "শীঘ্র", "সবচেয়ে আগে", "সব থেকে আগে", "সবার আগে", "প্রথম খালি", "যত তাড়াতাড়ি"),
    "hi": ("जल्दी", "जल्द से जल्द", "सबसे पहले", "सबसे जल्दी", "पहली खाली", "शीघ्र"),
}
_INDIC_LETTER = r"[ঀ-৿ऀ-ॿ]"

# Latin script, matched as whole words: English, plus the romanised forms a code-switching caller
# (or the recogniser) produces for the same request.
_LATIN_CUES = re.compile(
    r"\b(?:earliest|soonest|asap|as soon as possible|first available|first free|"
    r"jaldi|jaldi se jaldi|sabse pehle|taratari|tartari|shighro|"
    r"shob theke age|sobcheye age|sobar age)\b",
    re.IGNORECASE,
)


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def wants_earliest(text: str, lang: str = "bn") -> bool:
    """Is the caller asking for the soonest slot rather than naming a day?

    `lang` selects the Indic cue set; the Latin cues are checked in every language, because a
    Bengali or Hindi caller saying "earliest" is exactly the code-switching this clinic hears."""
    t = _nfc(text)
    for cue in _INDIC_CUES.get(lang, ()):
        if re.search(rf"(?<!{_INDIC_LETTER}){re.escape(_nfc(cue))}", t):
            return True
    return _LATIN_CUES.search(t) is not None
