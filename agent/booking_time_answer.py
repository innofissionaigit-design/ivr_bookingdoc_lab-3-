"""Is this turn a clock time GIVEN as an answer -- rather than a question about the clinic's or a doctor's hours?

    "ছটার সময়"            at six                      -> an answer
    "সাড়ে ছটার সময়"       at half past six            -> an answer
    "ক্লিনিক কটায় খোলে"    what time does it open      -> a question (no clock time in it anyway)
    "সকাল আটটায় খোলে?"     does it open at 8           -> a question (has a clock time, and says "খোলে")

WHY THIS EXISTS
---------------
Live 2026-10-10: a booking asked "আর কোন সময়ে চান?" and the caller answered "ছটার সময়" ("at six"). "সময়" is also
the bare keyword of the clinic's `hours` FAQ, so the keyword path answered the opening hours (no model call at
all), the booking added its "back to your booking" line, asked the same question again, and the caller said the
same thing again -- three times. The time was never wrong: agent/spoken_time reads it as 18:00 and the booking
flow would have taken it. It never got that far.

The keyword path cannot be taught that "সময়" next to a clock time is an answer: the same word, in "ক্লিনিকের
সময় কত", is the question. So the decision is made where the context is -- the orchestrator, while a booking is
waiting for a day or a time -- and this module only supplies the pure half: is the sentence a time and nothing
that asks about hours?

CONSERVATIVE BY CONSTRUCTION. It says yes only when a clock time was said AND no word in the sentence asks
something: a question word, an open/close word, a "timing" word, a doctor word, a "is there / available" word,
or a question mark. Everything it declines is left exactly as it was, so a caller who asks the clinic's timing --
or when a doctor sits -- in the middle of a booking is still answered, and the booking resumes after.

Deciding what the caller WANTS is allowed in agent/; deciding what is TRUE is not (CLAUDE.md section 1). This
returns a boolean about intent and touches no data.

REASONED, NOT MEASURED. The veto lists are written from this clinic's own FAQ keywords (clinic-api/seed.py) and
the questions callers ask about timing; the Hindi and romanised lists were not reviewed by a native speaker.
"""

from __future__ import annotations

import re
import unicodedata

from agent.spoken_time import spoken_times

# Indic scripts have no usable \b, so a cue is matched at a word START (a suffixed form still matches) and cannot
# fire inside an unrelated word -- the same idiom as agent/lookup_request.py.
_LETTER = r"[ঀ-৿ऀ-ॿA-Za-z]"

# Matched at a word start: the stem is enough ("খোলে", "খোলা", "খোলার" all start with "খোল").
_QUESTION_STEMS = (
    # Bengali: when / what time / how many / how long / until when
    "কখন", "কটা", "কয়টা", "কয়টায়", "কতক্ষণ", "কতটা", "কবে", "কত ", "কোন সময়", "কোনো সময়",
    # open / close / timing / hours / schedule / sits / is there / available / can I
    "খোল", "বন্ধ", "টাইমিং", "সময়সূচি", "সময়সূচী", "ভিজিটিং", "বসেন", "বসে", "বসবেন", "বসার", "আছেন", "আছে",
    "পাওয়া যাবে", "পাওয়া যায়", "উপলব্ধ", "অ্যাভেলেবল", "এভেইলেবল", "অ্যাভেইলেবল",
    # Hindi
    "कब", "कितने", "कितनी", "कितना", "कौन", "क्या", "खुल", "बंद", "टाइमिंग", "बैठते", "बैठेंगे", "उपलब्ध",
    # A sentence that talks ABOUT the clinic, or says "your" to it, is addressed to the clinic -- a question about
    # its hours however it is phrased -- not an answer to the booking's own question.
    "ক্লিনিক", "হাসপাতাল", "আপনাদের", "আপনার", "क्लिनिक", "अस्पताल", "आपके", "आपका", "आपकी",
)
# Latin words, matched whole.
_QUESTION_LATIN = re.compile(
    r"\b(?:when|what|which|how|who|where|timing|timings|hours|open|opens|opening|close|closes|closed|closing|"
    r"available|availability|visiting|schedule|sits?|sitting|"
    r"your|clinic|hospital|kobe|kokhon|kotay|kata|khole|bondho|kab|kitne|baje\s+tak)\b",
    re.IGNORECASE,
)
# The doctor word in every script (clinic-api/doctor_mention.TITLE_WORDS has the same set): a sentence that says
# "doctor" is a doctor question or a doctor's name, never just the booking's time.
_DOCTOR = re.compile(
    r"(?<![ঀ-৿ऀ-ॿa-z])(?:ডাক্তার|ডক্টর|ডাঃ|ডা\.|डॉक्टर|डाक्टर|डॉ|(?:doctor|dr|daktar|doktor)(?![a-z]))",
    re.IGNORECASE,
)
# Bengali particle "কি"/"কী" is a question marker, but only as a whole word ("কিছু", "কিন্তু" are not).
_PARTICLE = re.compile(rf"(?<!{_LETTER})(?:কি|কী|ki|kya)(?!{_LETTER})", re.IGNORECASE)


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def _asks_something(text: str) -> bool:
    t = _nfc(text)
    if "?" in t or "؟" in t:
        return True
    if _QUESTION_LATIN.search(t) or _DOCTOR.search(t) or _PARTICLE.search(t):
        return True
    return any(re.search(rf"(?<!{_LETTER}){re.escape(stem)}", t) for stem in _QUESTION_STEMS)


def is_a_time_answer(text: str) -> bool:
    """A clock time was said, and nothing in the sentence asks about hours, a doctor or availability."""
    if not text or not text.strip():
        return False
    if not spoken_times(text):
        return False
    return not _asks_something(text)
