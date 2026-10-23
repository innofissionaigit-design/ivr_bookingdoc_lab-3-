"""Is the caller asking whether they can come BY CAR -- where it goes, whether there is room, whether it is a bother?

story: "Caller asks about parking"

WHY THIS IS CODE AND NOT LEFT TO THE KEYWORDS OR THE MODEL
---------------------------------------------------------
The parking answer is one fixed FAQ sentence ("ক্লিনিকের নিজস্ব পার্কিং আছে, কোনো চার্জ লাগে না।") and a caller rarely
says "parking":

    "গাড়ি নিয়ে গেলে কোনো অসুবিধা হবে?"        will there be any trouble if I take the car
    "আমি গাড়ি চালিয়ে আসব ভাবছি।"             I am thinking of driving there
    "নিজের গাড়িতে এলে কোনো অসুবিধা হবে?"      if I come in my own car, any trouble

Measured on the pod on 2026-10-13 (75 such questions, the real fast path and the real Qwen): **42 got the parking
answer, 33 did not**. The failures were not "unknown" but WRONG: the address for "গাড়িটা কোথায় রাখব বলুন তো?" (the
keyword "কোথায়"), the home-collection answer for "নিজের গাড়িতে এলে কোনো অসুবিধা হবে?" (the keyword "সুবিধা"), "a doctor
must explain this" for "আমি ড্রাইভ করে আসব, কোনো সমস্যা আছে?" (the clinical guard's phrase "সমস্যা আছে"), and the model
calling the caller unrelated or composing a sentence of its own. A keyword list cannot cover this.

THE RULE. Bengali script only. Either

  A. an explicit parking word -- পার্কিং, "গাড়ি রাখ-", "পার্ক কর-", "দাঁড় কর-"; or
  B. a car word (গাড়ি, গাড়িতে, ড্রাইভ, ফোর-হুইলার, চার চাকা, "কার নিয়ে") together with the clinic or a going / coming /
     taking word ("আপনাদের ওখানে", "নিয়ে গেলে", "এলে", "আসব"),

and NOTHING that makes it another question: a doctor, a test or a report, a booking, a symptom, an ambulance or an
emergency, a public vehicle ("কোন গাড়ি পাব" -- which bus -- is the way there, the address's), the fare, sending a car,
and for B a price or a time. A MISS changes nothing: the turn goes where it always went. A HIT answers the fixed FAQ and
skips the model, and is wired in front of the fast path and the address rule (main._dispatch_turn) while no booking is
open. REASONED from the 75 sentences of the 2026-10-13 brief; tests/test_parking_question.py holds them, the 90 address
questions that must NOT be taken for parking, and the near misses.
"""

from __future__ import annotations

import re
import unicodedata

_L = "ঀ-৿"


def _words(*stems: str) -> re.Pattern[str]:
    return re.compile(r"(?<![" + _L + r"])(?:" + "|".join(stems) + ")")


# A. the parking itself
_EXPLICIT = _words(
    "পার্কিং", "পার্ক কর", "পার্কিংয়", "গাড়ি রাখ", "গাড়িটা রাখ", "গাড়ী রাখ", "গাড়ি পার্ক", "গাড়ি দাঁড়", "দাঁড় কর",
    "গাড়ি রাখা", "কার পার্ক", "কার রাখ",
)
# B. a car, and the clinic or a going / coming / taking word
_CAR = _words("গাড়ি", "গাড়ী", "ড্রাইভ", "ফোর-হুইলার", "ফোর হুইলার", "চার চাকা", "চারচাকা", "কার নিয়ে", "কারে")
_CONTEXT = _words(
    "আপনাদের", "ক্লিনিক", "সেন্টার", "ওখানে", "ওখানকার", "এখানে", "বিল্ডিং", "যাব", "যাওয়া", "যাওয়ার", "গেলে", "যেতে",
    "আসব", "আসলে", "এলে", "আসা", "আসার", "আসতে", "এসে", "নিয়ে", "চালিয়ে", "ড্রাইভ", "রাখ", "দাঁড়", "থাকবে", "থাকব",
    "স্পেস", "জায়গা",
)

# what makes it another question (always)
_ELSEWHERE = _words(
    "ডাক্তার", "ডক্টর", "ডাঃ", "চিকিৎসক", "টেস্ট", "পরীক্ষা", "রিপোর্ট", "স্যাম্পল", "নমুনা", "রক্ত", "অ্যাপয়েন্টমেন্ট",
    "বুক(?! করতে হবে)(?! করা লাগবে)", "সিরিয়াল", "বাতিল", "ক্যানসেল", "রিশিডিউল", "কনফার্ম", "দেখাব", "দেখাতে", "ব্যথা", "জ্বর", "অসুখ", "অসুস্থ",
    "কোন গাড়ি", "অ্যাম্বুলেন্স", "এম্বুলেন্স", "জরুরি", "ইমার্জেন্সি", "দুর্ঘটনা", "অ্যাক্সিডেন্ট", "ভাড়া", "পাঠ",
    "ট্যাক্সি", "ক্যাব", "উবার", "ওলা", "অটো", "বাস", "ট্রেন", "মেট্রো", "বাড়ি থেকে",
)
# what makes the IMPLICIT form another question (the explicit form -- "পার্কিং চার্জ কত" -- keeps its answer)
_ELSEWHERE_IMPLICIT = _words(
    "দাম", "রেট", "টাকা", "ফি(?![" + _L + "])", "খরচ", "চার্জ", "সকাল", "দুপুর", "বিকেল", "সন্ধ্যা", "রাত", "আজ", "কাল",
    "পরশু", "আগামী", "সোমবার", "মঙ্গলবার", "বুধবার", "বৃহস্পতিবার", "শুক্রবার", "শনিবার", "রবিবার", "তারিখ", "কটা",
    "কখন(?!ও)", "খোলা", "খোলে", "বন্ধ",
)

_RE_NOISE = re.compile(r"[।?!,.;:'\"()\-–—/]+")
_RE_WS = re.compile(r"\s+")


def _normalise(text: str) -> str:
    return _RE_WS.sub(" ", _RE_NOISE.sub(" ", unicodedata.normalize("NFC", text or ""))).strip()


def asks_about_parking_or_a_car(text: str) -> bool:
    """True when `text` (Bengali) is about bringing a car, and about nothing else the clinic answers elsewhere."""
    t = _normalise(text)
    if not t or not re.search("[" + _L + "]", t):
        return False
    if _ELSEWHERE.search(t):
        return False
    if _EXPLICIT.search(t):
        return True
    if _ELSEWHERE_IMPLICIT.search(t):
        return False
    return bool(_CAR.search(t) and _CONTEXT.search(t))
