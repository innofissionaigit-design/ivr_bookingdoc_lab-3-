"""Is the caller asking WHERE the clinic is, or HOW TO GET THERE -- however indirectly?

story: "Caller asks for the clinic's address"

WHY THIS IS CODE AND NOT LEFT TO THE MODEL
------------------------------------------
The address is a fixed FAQ answer, and a caller reaches for it in a hundred ways that share no keyword:

    "আপনারা কোথায় বসেন?"                              where do you sit
    "আপনাদের সেন্টারটা কোন দিকে?"                      which way is your centre
    "আপনাদের ওখানে যাওয়ার জন্য কোন স্টপে নামব?"        which stop do I get off at
    "আপনাদের ওখানে আসার কথা ছিল।"                      I was supposed to come to you

Measured on the pod on 2026-10-13 (90 such questions, the real fast path and the real Qwen): 36 were caught by the
FAQ keywords, 19 more were classified correctly by the model, and **35 were not** -- 9 of those by the keyword
matcher picking a WRONG topic (a doctor's schedule for "আপনারা কোথায় বসেন", the contact number for "আপনাদের
সেন্টারটা কি মেইন রোডের ওপর?", the parking answer for "কোন গাড়ি পাব"), 26 by the model calling the caller
"unrelated" or answering with a sentence it composed itself. A keyword list cannot cover this: the keyword matcher
works on character similarity and the model is unreliable on short indirect sentences (CLAUDE.md section 1: the model
decides what the caller wants; a deterministic rule may do that too, and this one does).

THE RULE. Bengali script only. All of:

  1. a DESTINATION is named        -- "আপনাদের / আপনারা" (you), or the clinic by a noun (সেন্টার, ক্লিনিক, অফিস ...)
  2. a PLACE or MOTION word        -- going, coming, reaching, the road, which way, where, a landmark, a stop,
                                       an area, being found, sitting
  3. NOTHING that names another thing the caller wants -- a doctor, a test or its price, a report, a booking,
                                       a day or a time, the hours, parking, insurance, payment, the phone number,
                                       a symptom. "ডাক্তার সেন কোথায় বসেন" is a doctor's schedule; "কাল আসব" is a
                                       visit; "আমার পেটে ব্যথা, কোথায় যাব" is department routing. Each keeps its
                                       own path.

A MISS changes nothing -- the turn goes where it always went. A HIT answers the fixed address FAQ and skips the
model for that turn; it is wired in front of the fast path only while no booking is open (main._resolve_intent),
because the fast path was itself taking some of these to the wrong topic.

REASONED from the 90 sentences of the 2026-10-13 brief and the repository's own sentences; tests/test_location_question.py
holds both and the near misses it must not take.
"""

from __future__ import annotations

import re
import unicodedata

_L = "ঀ-৿"  # the Bengali block, marks included


def _words(*stems: str) -> re.Pattern[str]:
    """Any of `stems` at the START of a word (Bengali inflects: যাব, যাবেন, যাবো)."""
    return re.compile(r"(?<![" + _L + r"])(?:" + "|".join(stems) + ")")


# 1. a destination: you (plural, the clinic's people) or the clinic by its noun
_DESTINATION = _words(
    "আপনাদের", "আপনারা", "আপনাদেরকে", "সেন্টার", "ক্লিনিক", "অফিস", "প্রতিষ্ঠান", "শাখা", "সেটআপ", "চেম্বার",
)

# 2. a place or motion word
_PLACE_OR_MOTION = _words(
    # going and coming
    "যাওয়া", "যাওয়ার", "যাব", "যাই", "যাচ্ছি", "যেতে", "যাইনি", "গেলে", "গিয়ে", "আসা", "আসার", "আসব", "আসি", "আসতে",
    "আসছি", "এলে", "এসে", "এসেছি", "পৌঁছ", "পৌছ", "এগোব", "এগোতে", "নামব", "নামতে", "নামার", "ধরব", "ধরতে",
    # the way
    "রাস্তা", "পথ(?!্য)", "উপায়", "দিকে", "দিকটা", "কোনদিক",
    # where
    "কোথায়", "কোথা", "কোন জায়গা", "কোন এলাকা", "কোন রাস্তা", "কোন রোড", "কোন দিক", "ঠিকানা", "লোকেশন", "লোকেসন",
    "এড্রেস", "অ্যাড্রেস", "অ্যাডরেস",
    # around it
    "ল্যান্ডমার্ক", "ল্যাণ্ডমার্ক", "ল্যান্ড মার্ক", "কাছাকাছি", "আশেপাশে", "আশপাশে", "নিকটবর্তী", "কীসের কাছে",
    "কিসের কাছে", "পাশে", "মোড়", "স্টপ", "স্ট্যান্ড", "বাসস্ট্যান্ড", "স্টেশন", "মেট্রো", "মেইন রোড", "মেন রোড", "রোডের",
    "রোডে", "চিহ্ন", "কলকাতা",
    # a landmark named by what it is next to: "হাসপাতালের কাছে" (a bare "হাসপাতালের" is "who owns your hospital")
    "(?:হাসপাতাল|বাজার|মার্কেট|মল|স্কুল|কলেজ|মন্দির|পার্ক)(?:ের|র)? (?:কাছে|পাশে|সামনে|পেছনে)",
    # being found, being there
    "চিনব", "চিনে", "চিনতে", "চেনা", "চেনার", "খুঁজে", "খুজে", "বসেন", "বসার", "অবস্থিত",
)

# 3. what makes it about something else. Word-start matches again, except where a longer word must not be caught.
_ELSEWHERE = _words(
    # a doctor
    "ডাক্তার", "ডক্টর", "ডাঃ", "ডা\\.", "চিকিৎসক", "স্পেশালিস্ট", "বিশেষজ্ঞ",
    # a test, a price, a report, a sample
    "টেস্ট", "পরীক্ষা", "দাম", "রেট", "টাকা", "ফি(?![" + _L + "])", "খরচ", "চার্জ", "রিপোর্ট", "স্যাম্পল", "কালেকশন", "নমুনা",
    # a booking
    "অ্যাপয়েন্টমেন্ট", "বুক", "সিরিয়াল", "বাতিল", "ক্যানসেল", "রিশিডিউল", "কনফার্ম", "দেখাব", "দেখাতে", "দেখাই",
    # a time or the hours ("কখনও" -- ever, never -- is not "when")
    "কখন(?!ও)", "কটা", "কটায়", "সময়", "খোলা", "খোলে", "খুলে", "খুলবে", "বন্ধ", "সকাল", "দুপুর", "বিকেল", "সন্ধ্যা",
    "রাত", "আজ", "কাল", "পরশু", "আগামী", "সোমবার", "মঙ্গলবার", "বুধবার", "বৃহস্পতিবার", "শুক্রবার", "শনিবার",
    "রবিবার", "তারিখ", "ঘণ্টা", "ঘন্টা", "মিনিট",
    # the other FAQ topics
    "পার্কিং", "গাড়ি রাখ", "ইনসুরেন্স", "ইন্সুরেন্স", "বিমা", "ক্যাশলেস", "মেডিক্লেম", "পেমেন্ট", "কার্ড", "ইউপিআই",
    "নগদ", "ফোন নম্বর", "নম্বর", "হোয়াটসঅ্যাপ", "বাড়ি থেকে",
    # a symptom or a patient: a different question ("where do I go with this?" is department routing)
    "ব্যথা", "জ্বর", "কাশি", "সর্দি", "অসুখ", "সমস্যা", "অসুস্থ", "চোখ", "কান(?![" + _L + "])", "কানে", "নাক(?![" + _L + "])", "গলা", "দাঁত", "পেট",
    "বুকে", "মাথা", "হাড়", "চামড়া", "ত্বক", "গর্ভ", "প্রেগন্যান্সি", "বাচ্চ", "শিশু", "সুগার", "প্রেশার",
)

_RE_NOISE = re.compile(r"[।?!,.;:'\"()\-–—/]+")
_RE_WS = re.compile(r"\s+")


def _normalise(text: str) -> str:
    return _RE_WS.sub(" ", _RE_NOISE.sub(" ", unicodedata.normalize("NFC", text or ""))).strip()


def asks_where_or_how_to_reach(text: str) -> bool:
    """True when `text` (Bengali) asks where the clinic is or how to get to it, and asks nothing else."""
    t = _normalise(text)
    if not t or not re.search("[" + _L + "]", t):
        return False
    if _ELSEWHERE.search(t):
        return False
    return bool(_DESTINATION.search(t) and _PLACE_OR_MOTION.search(t))
