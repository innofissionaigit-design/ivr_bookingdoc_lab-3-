"""The calendar date a caller MEANT by "আজ", "কাল", "পরশু", "अगले सोमवार", "8th October", ...

story: "Caller says tomorrow, day after, or next Monday"

WHY THIS IS CODE AND NOT THE MODEL
----------------------------------
agent/llm.py asks the model to turn relative words into an ISO date "using today's date above". That
is arithmetic on a calendar, done by a 7B language model: it gets "আগামী শনিবার" a week out, or the
month boundary wrong, and nothing checked it before the booking was written. A date is a FACT the
booking is committed on, so by CLAUDE.md section 1 it is worked out here, in code, from a real
calendar, and the model's date is only the fallback for a phrasing this does not recognise.

TODAY IS THE CLINIC'S TODAY. India Standard Time (UTC+05:30, no daylight saving), never the server
clock: a pod running on UTC is still on yesterday until 05:30 in Kolkata, and "আজ" said at 01:00 would
otherwise book the day before. clinic_today() is the one place that is decided.

WHAT IS RECOGNISED (Bengali, Hindi, English, and their common romanised spellings)

  relative days    আজ/আজকে  কাল/কালকে/আগামীকাল  পরশু  তরশু  গতকাল
                   आज  कल  परसों  नरसों
                   today  tomorrow  day after tomorrow  yesterday
                   aaj  kal  porshu/parso  torshu/narso
  weekdays         সোমবার .. রবিবার, सोमवार .. रविवार, Monday .. Sunday
     bare / "এই" / "इस" / "this"           -> the next one, today included
     "আগামী" / "অगले" / "next" / "coming"  -> the next one, today excluded
     "পরের সপ্তাহের" / "अगले हफ़्ते" / "next week"  -> that weekday in the following calendar week
  a day of month   "৮ তারিখ", "৮ই অক্টোবর", "आठ तारीख", "8th October", "October 8", "8 Oct 2026"
                   -- digits in any script or number words; without a month it is this month if
                   the day has not passed, else next month; without a year, this year if not passed,
                   else next year.

Hindi "कल" is read as TOMORROW. It also means yesterday, which is why agent/fast_path_cues.py
deliberately leaves it out of its table (merge_report.md section 4.10) -- that table answers enquiries,
where either reading is possible. This module is only applied to booking, moving a booking and asking
a doctor's availability, where the day asked about is in the future. The resolved date is always read
back with its weekday before anything is committed, so a misreading is heard and corrected.

A day-of-month needs a date word or a month name next to it: a bare number is a time or part of a
phone number, never a date. A day the calendar does not have (31 November) resolves to nothing.

Pure: no I/O; `today` can be passed in.
"""

from __future__ import annotations

import datetime
import re

from agent import security_input as _num

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST")


def clinic_today() -> datetime.date:
    """Today in Kolkata, whatever the server's own time zone is."""
    return datetime.datetime.now(IST).date()


_LETTER = r"[ঀ-৿ऀ-ॿa-z]"
_LEFT = rf"(?<!{_LETTER})"
_RIGHT = rf"(?!{_LETTER})"
_INDIC_TAIL = r"[ঀ-৿ऀ-ॿ]*"  # a case ending on an Indic word: সোমবারে, সোমবারের, অক্টোবরের


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


# ------------------------------------------------------------------ relative days (whole words only)
# Whole words, because the short forms are the start of other words: আজকাল (nowadays), কালো (black),
# কালী (a name), कलकत्ता (Kolkata).
_RELATIVE = [
    (-1, ["গতকাল", "গতকালকে", "গত কাল", "yesterday", "gotokal", "gatokal"]),
    (0, ["আজ", "আজকে", "আজই", "আজকেই", "আজকের", "आज", "आज ही", "today", "tonight", "aaj", "aj", "aajke", "ajke"]),
    (1, ["কাল", "কালকে", "কালই", "কালকেই", "কালকের", "আগামীকাল", "আগামী কাল", "আগামীকালকে",
         "कल", "आने वाले कल", "tomorrow", "tomorow", "tmrw", "kal", "kaal", "kalke", "agamikal", "agami kal"]),
    (2, ["পরশু", "পরশুদিন", "পরশু দিন", "পরশুদিনে", "পরশুর", "परसों", "परसो", "day after tomorrow",
         "the day after tomorrow", "porshu", "porsu", "porshudin", "parso", "parson", "parsoon"]),
    (3, ["তরশু", "তরশুদিন", "नरसों", "नरसो", "torshu", "torsu", "narso", "narson"]),
]
_RELATIVE_RE = [(offset, re.compile(rf"{_LEFT}(?:{_alt(words)}){_RIGHT}")) for offset, words in _RELATIVE]

# ------------------------------------------------------------------ weekdays (Monday = 0)
_WEEKDAYS = [
    (["সোমবার", "सोमवार", "somvar", "sombar", "sombaar"], ["monday"]),
    (["মঙ্গলবার", "मंगलवार", "mangalvar", "mongolbar"], ["tuesday"]),
    (["বুধবার", "बुधवार", "budhvar", "budhbar"], ["wednesday"]),
    (["বৃহস্পতিবার", "বিষ্যুদবার", "বেস্পতিবার", "गुरुवार", "बृहस्पतिवार", "वीरवार", "guruvar", "brihaspatibar", "bishyudbar"],
     ["thursday"]),
    (["শুক্রবার", "शुक्रवार", "shukravar", "shukrobar"], ["friday"]),
    (["শনিবার", "शनिवार", "shanivar", "shonibar"], ["saturday"]),
    (["রবিবার", "রোববার", "रविवार", "इतवार", "ravivar", "robibar", "robbar"], ["sunday"]),
]
_NEXT_WEEK = r"(?:পরের|সামনের|আগামী)\s+সপ্তাহ" + _INDIC_TAIL + r"|अगले\s+(?:हफ़्ते|हफ्ते|सप्ताह)(?:\s+(?:के|को))?|next\s+week(?:'s)?(?:\s+on)?"
_NEXT = r"আগামী|সামনের|পরের|अगले|अगला|आने\s+वाले|next|coming|the\s+coming"
_THIS = r"এই|इस|this"


def _weekday_re(index: int) -> re.Pattern:
    indic, latin = _WEEKDAYS[index]
    name = rf"(?:(?:{_alt(indic)}){_INDIC_TAIL}|(?:{_alt(latin)})s?{_RIGHT})"
    return re.compile(rf"(?:(?P<nw>{_NEXT_WEEK})\s+|(?P<nx>{_NEXT})\s+|(?P<th>{_THIS})\s+)?{_LEFT}{name}")


_WEEKDAY_RE = [_weekday_re(i) for i in range(7)]

# ------------------------------------------------------------------ day of month
_MONTH_NAMES = _num._MONTH_LOOKUP  # every month name the date-of-birth parser already knows, in all three scripts
_INDIC_MONTHS = [m for m in _MONTH_NAMES if re.search(r"[ঀ-৿ऀ-ॿ]", m)]
_LATIN_MONTHS = [m for m in _MONTH_NAMES if m not in _INDIC_MONTHS]
_MONTH = rf"(?P<month>(?:{_alt(_INDIC_MONTHS)}){_INDIC_TAIL}|(?:{_alt(_LATIN_MONTHS)}){_RIGHT})"
_DATE_WORD = r"(?:তারিখ|তাং|तारीख़|तारीख|तारिख|tarikh|tarik|date)" + _INDIC_TAIL
_DAY = r"(?<!\d)(?P<day>\d{1,2})(?!\d)"
_YEAR = r"(?:\s*,?\s*(?P<year>\d{4})(?!\d))?"
_DAY_MONTH_RE = re.compile(rf"{_DAY}\s*(?:date\s+)?(?:of\s+)?{_MONTH}{_YEAR}")
_MONTH_DAY_RE = re.compile(rf"{_LEFT}{_MONTH}\s+{_DAY}{_YEAR}")
_DAY_ONLY_RE = re.compile(rf"{_DAY}\s*{_DATE_WORD}")
# Ordinal endings said with a date: 1st, ৮ই, ১লা, ২রা, ৪ঠা, ১৯শে, आठवीं
_ORDINAL = re.compile(r"(?<=\d)(?:st|nd|rd|th|ই|লা|রা|ঠা|শে|वीं|वां|वी)(?![ঀ-৿ऀ-ॿa-z])")


def _normalise(text: str) -> str:
    """Lower case, ASCII digits, number words as digits ("আট তারিখ" -> "8 তারিখ"), ordinals stripped."""
    t = _num._norm(text)
    t = re.sub(r"[?!.:\"'()\-–]", " ", t)
    t = _ORDINAL.sub(" date", t)  # "8th", "৮ই": an ordinal said with a number is a day of the month
    words = [str(w) for w in _num._to_numbers([w for w in t.split() if w])]
    return " ".join(words)


def _month_of(name: str) -> int | None:
    for candidate in sorted(_MONTH_NAMES, key=len, reverse=True):
        if name.startswith(candidate):
            return _MONTH_NAMES[candidate]
    return None


def _safe(year: int, month: int, day: int) -> datetime.date | None:
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def _day_of_month(day: int, month: int | None, year: int | None, today: datetime.date) -> datetime.date | None:
    if not 1 <= day <= 31:
        return None
    if month is None:
        here = _safe(today.year, today.month, day)
        if here is not None and here >= today:
            return here
        nxt_year, nxt_month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        return _safe(nxt_year, nxt_month, day)
    if year is not None:
        return _safe(year, month, day)
    here = _safe(today.year, month, day)
    if here is not None and here >= today:
        return here
    return _safe(today.year + 1, month, day)


def _weekday_date(index: int, match: re.Match, today: datetime.date) -> datetime.date:
    ahead = (index - today.weekday()) % 7
    if match.group("nw"):
        monday_next_week = today + datetime.timedelta(days=7 - today.weekday())
        return monday_next_week + datetime.timedelta(days=index)
    if match.group("nx"):
        return today + datetime.timedelta(days=ahead or 7)
    return today + datetime.timedelta(days=ahead)


def spoken_dates(text: str, today: datetime.date | None = None) -> list[str]:
    """Every date the caller named, as ISO strings, in the order they were said. Empty when none."""
    today = today or clinic_today()
    t = _normalise(text)
    found: list[tuple[int, int, datetime.date]] = []  # (start, end, date)
    for offset, pattern in _RELATIVE_RE:
        for m in pattern.finditer(t):
            found.append((m.start(), m.end(), today + datetime.timedelta(days=offset)))
    for index, pattern in enumerate(_WEEKDAY_RE):
        for m in pattern.finditer(t):
            found.append((m.start(), m.end(), _weekday_date(index, m, today)))
    named_month: list[tuple[int, int]] = []  # spans where a month was named, valid date or not
    for pattern in (_DAY_MONTH_RE, _MONTH_DAY_RE):
        for m in pattern.finditer(t):
            named_month.append((m.start(), m.end()))
            year = int(m.group("year")) if m.group("year") else None
            d = _day_of_month(int(m.group("day")), _month_of(m.group("month")), year, today)
            if d is not None:
                found.append((m.start(), m.end(), d))
    for m in _DAY_ONLY_RE.finditer(t):
        if any(a <= m.start() < b for a, b in named_month):
            continue  # "31st November": the month was said, and has no 31st -- never this month's 31st instead
        d = _day_of_month(int(m.group("day")), None, None, today)
        if d is not None:
            found.append((m.start(), m.end(), d))
    # Overlapping readings of the same words ("day after tomorrow" also contains "tomorrow"; "8 October"
    # is also "8"): the longest one wins.
    found.sort(key=lambda f: (f[0], -(f[1] - f[0])))
    kept: list[tuple[int, int, datetime.date]] = []
    for start, end, d in found:
        if kept and start < kept[-1][1]:
            continue
        kept.append((start, end, d))
    return [d.isoformat() for _, _, d in kept]
