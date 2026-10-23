"""The Bengali booking and test-price flows, played as whole calls for every doctor, department and test.

Three scenarios (each a function that plays ONE call and returns a Result):

  1. department_to_booking   the caller names a department, is read its doctors, picks one, gives a day and a time,
                             then a patient name and a phone number, and says yes to the read-back -> confirmed.
  2. doctor_only             the caller says only a doctor's name; the rest of the booking is asked for in turn.
  3. test_prices             the caller asks the price of one test, or of several, and hears each price and the total.

What is REAL: the orchestrator (_dispatch_turn and everything under it), the clinic API and its database, the
department / doctor / test matchers, the reply templates and the speech gate (agent/tts.py). What is GIVEN, as in
every test in this repository: the transcript (no ASR) and, per turn, the intent and slots the language model would
return (no LLM on this machine) -- written the way the model returns them on the pod, mistakes included. A result
here therefore proves the FLOW and the RULES around the model; it does not prove the ASR or the model.

Run it directly to print the report:    python tests/_bn_flow_matrix.py
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import itertools
import os
import re
import sys
import time
import unicodedata

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

B = "book_appointment"

# ============================================================================== Bengali, said aloud
_DIGITS_BN = "০১২৩৪৫৬৭৮৯"
_NUM_WORDS = {
    1: "এক", 2: "দুই", 3: "তিন", 4: "চার", 5: "পাঁচ", 6: "ছয়", 7: "সাত", 8: "আট", 9: "নয়", 10: "দশ", 11: "এগারো",
    12: "বারো", 13: "তেরো", 14: "চৌদ্দ", 15: "পনেরো", 16: "ষোলো", 17: "সতেরো", 18: "আঠারো", 19: "উনিশ", 20: "কুড়ি",
    21: "একুশ", 22: "বাইশ", 23: "তেইশ", 24: "চব্বিশ", 25: "পঁচিশ", 26: "ছাব্বিশ", 27: "সাতাশ", 28: "আঠাশ",
    29: "ঊনত্রিশ", 30: "ত্রিশ", 31: "একত্রিশ", 45: "পঁয়তাল্লিশ",
}
_HOUR_O_CLOCK = {  # "<n>টা" as people say it
    1: "একটা", 2: "দুটো", 3: "তিনটে", 4: "চারটে", 5: "পাঁচটা", 6: "ছটা", 7: "সাতটা", 8: "আটটা", 9: "নটা", 10: "দশটা",
    11: "এগারোটা", 12: "বারোটা",
}
_MONTHS = ["", "জানুয়ারি", "ফেব্রুয়ারি", "মার্চ", "এপ্রিল", "মে", "জুন", "জুলাই", "আগস্ট", "সেপ্টেম্বর", "অক্টোবর",
           "নভেম্বর", "ডিসেম্বর"]
_WEEKDAYS = ["সোমবার", "মঙ্গলবার", "বুধবার", "বৃহস্পতিবার", "শুক্রবার", "শনিবার", "রবিবার"]
_PHONE_WORDS = ["শূন্য", "এক", "দুই", "তিন", "চার", "পাঁচ", "ছয়", "সাত", "আট", "নয়"]


def bn_digits(n: int | str) -> str:
    return "".join(_DIGITS_BN[int(c)] if c.isdigit() else c for c in str(n))


def _period(h24: int) -> str:
    if h24 < 12:
        return "সকাল"
    if h24 < 15:
        return "দুপুর"
    if h24 < 18:
        return "বিকেল"
    return "সন্ধ্যা"


def time_phrase(slot: str, style: str) -> str:
    """"10:15" as a caller says it. style: 'fraction' (সোয়া / সাড়ে / পৌনে), 'minutes' (দশটা পনেরো),
    'digits' (১০:১৫)."""
    h, m = (int(x) for x in slot.split(":"))
    h12 = h % 12 or 12
    period = _period(h)
    if style == "digits":
        return f"{period} {bn_digits(h12)}টা" + (f" {bn_digits(m)}" if m else "")
    if style == "minutes" or (style == "fraction" and m not in (0, 15, 30, 45)):
        return f"{period} {_HOUR_O_CLOCK[h12]}" + (f" {_NUM_WORDS[m]}" if m else "")
    # fraction
    if m == 0:
        return f"{period} {_HOUR_O_CLOCK[h12]}"
    if m == 15:
        return f"{period} সোয়া {_HOUR_O_CLOCK[h12]}"
    if m == 30:
        return f"{period} সাড়ে {_HOUR_O_CLOCK[h12]}"
    nxt = (h12 % 12) + 1
    return f"{_period(h)} পৌনে {_HOUR_O_CLOCK[nxt]}"


def date_phrase(day: dt.date, today: dt.date, style: str) -> str:
    """The day as a caller says it. style: 'relative' (কাল / পরশু, else the weekday), 'weekday_number'
    (মঙ্গলবার তেরো তারিখে), 'number_month' (১৩ই অক্টোবর)."""
    offset = (day - today).days
    weekday = _WEEKDAYS[day.weekday()]
    if style == "relative":
        if offset == 1:
            return "কাল"
        if offset == 2:
            return "পরশু"
        if offset < 7 and day.weekday() != today.weekday():
            return weekday
        # The weekday name would mean today, or is a week away: said with its number, which is one day only.
        return f"{weekday} {_NUM_WORDS[day.day]} তারিখে"
    if style == "weekday_number":
        return f"{weekday} {_NUM_WORDS[day.day]} তারিখে"
    return f"{bn_digits(day.day)}ই {_MONTHS[day.month]}"


def phone_phrase(number: str, style: str) -> str:
    if style == "bn_digits":
        return bn_digits(number)
    if style == "words":
        return " ".join(_PHONE_WORDS[int(c)] for c in number)
    return number  # latin digits


PATIENTS = ["রাহুল দাস", "সুমন ঘোষ", "অনিন্দিতা সেন", "প্রিয়া মুখার্জী", "দেবজিৎ বন্দ্যোপাধ্যায়", "মৌসুমী রায়", "তন্ময় পাল",
            "শর্মিষ্ঠা বিশ্বাস"]

# ============================================================================== results
@dataclasses.dataclass
class Result:
    scenario: str
    combo: dict
    passed: bool = True
    failures: list[str] = dataclasses.field(default_factory=list)
    transcript: str = ""
    seconds: float = 0.0

    def fail(self, why: str) -> None:
        self.passed = False
        self.failures.append(why)


# Lines that mean the flow went wrong, whatever else it said.
_WRONG_LINES = (
    "বুঝতে পারিনি",  # "I did not understand"
    "নামে কোনো ডাক্তার",  # "no doctor called ..."
    "কাউন্টারে জিজ্ঞেস",  # "ask at the counter" (used as the fallback hand-off)
    "দুঃখিত, কোনো",
    "একজন মানুষের সঙ্গে",  # hand-off to a person
)


def _spoken(call) -> list[str]:
    return [line for who, line in call._log if who == "AI" or who == "AI BLOCKED"]


def _repeats(lines: list[str]) -> list[str]:
    """A question asked twice in a row, word for word, is the "it did not hear me" failure."""
    out = []
    for a, b in zip(lines, lines[1:]):
        if a == b and len(a) > 20:
            out.append(a)
    return out


# ============================================================================== the clinic's facts, via the API
def clinic_doctors(w, call) -> list[dict]:
    result = call._run(w.m._tools.list_doctors())
    docs = []
    for d in result["doctors"]:
        av = call._run(w.m._tools.get_doctor_availability(d["name"], None))
        hours = av.get("regular_chamber_hours") or av.get("chamber_hours") or ""
        docs.append({**d, "chamber_days": av.get("chamber_days") or [], "chamber_hours": hours,
                     "slots": slots_in(hours)})
    return docs


def slots_in(hours: str, step: int = 15) -> list[str]:
    """"17:30-19:30" -> ["17:30", ... "19:15"]: the start times a 15-minute grid allows inside the chamber."""
    m = re.match(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$", hours or "")
    if not m:
        return []
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    return [f"{t // 60:02d}:{t % 60:02d}" for t in range(a, b, step)]


def next_dates(chamber_days: list[int], count: int, today: dt.date, skip: int = 0) -> list[dt.date]:
    out, day, seen = [], today + dt.timedelta(days=1), 0
    while len(out) < count:
        if day.weekday() in chamber_days:
            if seen >= skip:
                out.append(day)
            seen += 1
        day += dt.timedelta(days=1)
    return out
