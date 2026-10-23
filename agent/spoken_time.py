"""The clock time(s) a caller said -- "সকাল দশটায়", "সাড়ে দশটা", "साढ़े दस बजे", "at ten thirty" -- as "HH:MM".

story: "Caller names only a doctor", "Caller gives everything in one sentence", "Caller moves an existing appointment"
(every flow that books or moves a doctor's slot needs the time the caller said).

Before this module there was no time parser at all: `time_slot` was whatever text the intent model wrote, and the clinic
only accepts "HH:MM". On the live pod (2026-10-07) a Bengali caller who answered "শুক্রবার, সকাল দশটায়" had the day
taken and was asked for the time again; answering "সকাল দশটায়" on its own was then filed as smalltalk and the model
replied with nonsense. Days have been worked out from a calendar since agent/spoken_date.py; times now are too, the
same way: deterministic, three languages, nothing guessed.

WHAT IS RECOGNISED -- always with a clock MARKER, so a phone number said digit by digit ("নয় আট তিন ...") is never a time:

  Bengali  দশটা, দশটায়, ১০টায়, সাড়ে দশটা (10:30), সোয়া দশটা (10:15), পৌনে এগারোটা (10:45), দেড়টা (1:30), আড়াইটা (2:30),
           দশটা পনেরো / তিরিশ / পঁয়তাল্লিশ
  Hindi    दस बजे, साढ़े दस (10:30), सवा दस (10:15), पौने ग्यारह (10:45), डेढ़ बजे, ढाई बजे, दस बजकर पंद्रह मिनट
  English  10:30, ten o'clock, 10 am, half past ten, quarter past ten, quarter to eleven, at ten, at ten thirty
  any      "10:30", "১০:৩০"

MORNING OR EVENING. A period word wins: সকাল / सुबह / morning / am -> before noon; দুপুর / दोपहर / noon -> 12 to 3;
বিকেল, সন্ধ্যা, রাত / शाम, रात / afternoon, evening, night, pm -> after noon. With none, the clinic's day decides: 8 to 11
is the morning, 1 to 7 the afternoon or evening (nobody books a 3 a.m. consultation), 12 is noon. REASONED from the
chamber hours seeded in clinic-api (10:00-12:00 mornings, 18:00-20:00 evenings), not measured on calls.

Pure: no I/O.
"""

from __future__ import annotations

import re
import unicodedata

from agent import security_input as _num


def _nfc_keys(table: dict) -> dict:
    """Keys in NFC, the form _normalise() puts the caller's words in -- so a word matches however it was typed here."""
    return {unicodedata.normalize("NFC", k): v for k, v in table.items()}


def _nfc_words(*words: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(unicodedata.normalize("NFC", w) for w in words))


_BN_HOURS = _nfc_keys(
    {
        "এক": 1,
        "দুই": 2,
        "দু": 2,
        "তিন": 3,
        "চার": 4,
        "পাঁচ": 5,
        "ছয়": 6,
        "ছ": 6,
        "সাত": 7,
        "আট": 8,
        "নয়": 9,
        "ন": 9,
        "দশ": 10,
        "এগারো": 11,
        "এগার": 11,
        "বারো": 12,
        "বার": 12,
    }
)
_BN_MINUTES = _nfc_keys({"পনেরো": 15, "পনের": 15, "তিরিশ": 30, "ত্রিশ": 30, "পঁয়তাল্লিশ": 45})
_BN_HALVES = _nfc_keys({"দেড়": (1, 30), "আড়াই": (2, 30)})
_HI_HALVES = _nfc_keys({"डेढ़": (1, 30), "ढाई": (2, 30)})
_MODIFIER = _nfc_keys({"সাড়ে": 30, "সোয়া": 15, "পৌনে": -15, "साढ़े": 30, "सवा": 15, "पौने": -15})
_QUARTER_TO = _nfc_words("পৌনে", "पौने")

_AM = _nfc_words("সকাল", "সকালে", "भोर", "सुबह", "सवेरे", "morning", "am", "a.m.", "a.m")
_NOON = _nfc_words("দুপুর", "দুপুরে", "दोपहर", "noon", "afternoon")
_PM = _nfc_words(
    "বিকেল",
    "বিকেলে",
    "বিকাল",
    "বিকালে",
    "সন্ধ্যা",
    "সন্ধ্যায়",
    "সন্ধে",
    "সন্ধেবেলা",
    "রাত",
    "রাতে",
    "शाम",
    "रात",
    "evening",
    "night",
    "pm",
    "p.m.",
    "p.m",
)

_LETTER = r"[ঀ-৿ऀ-ॿa-z]"
_LEFT = rf"(?<!{_LETTER})"


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


def _script(words, lo: str, hi: str) -> list[str]:
    return [w for w in words if lo <= w[0] <= hi]


# Bengali: an hour word or digits, then the clock ending টা/টায়/টার (with any case tail). "শুক্রবার" ends in "বার" but
# has no টা after it, and the hour word must start a word, so a weekday is never read as twelve o'clock.
_BN_CLOCK = re.compile(
    rf"{_LEFT}(?:(?P<mod>{_alt(_script(_MODIFIER, 'ঀ', '৿'))})\s*)?"
    rf"(?P<hour>{_alt(_BN_HOURS)}|\d{{1,2}})\s*টা(?P<tail>[ঀ-৿]*)"
    rf"(?:\s+(?P<min>{_alt(_BN_MINUTES)}|\d{{2}})(?!\d))?"
)
_BN_HALF = re.compile(rf"{_LEFT}(?P<half>{_alt(_BN_HALVES)})\s*টা")
# Hindi: digits (the normaliser has already turned दस into 10), then बजे / बजकर, or a modifier before the hour.
_HI_CLOCK = re.compile(
    rf"(?:(?P<mod>{_alt(_script(_MODIFIER, 'ऀ', 'ॿ'))})\s*)?(?P<hour>\d{{1,2}})\s*(?P<bje>बजे|बजकर|बज)?"
    rf"(?:\s*(?P<min>\d{{1,2}})\s*मिनट)?"
)
_HI_HALF = re.compile(rf"(?P<half>{_alt(_HI_HALVES)})\s*(?:बजे|बज)?")
# English (and any script): 10:30 / 10.30 (minutes must be 00-59), 10 am, 10 o'clock, half past 10, quarter to 11, at 10 (30).
_COLON = re.compile(r"(?<!\d)(?P<hour>\d{1,2})[:.](?P<min>[0-5]\d)(?!\d)\s*(?P<ap>a\.?m\.?|p\.?m\.?)?")
_EN_MARKED = re.compile(
    r"(?<![\d:.])(?P<hour>\d{1,2})(?:\s+(?P<min>15|30|45))?\s*(?P<ap>a\.?m\.?|p\.?m\.?|o'?\s?clock)(?![a-z])"
)
_EN_PAST = re.compile(r"(?P<kind>half|quarter)\s+past\s+(?P<hour>\d{1,2})(?!\d)")
_EN_TO = re.compile(r"quarter\s+to\s+(?P<hour>\d{1,2})(?!\d)")
_EN_AT = re.compile(r"(?<![a-z])at\s+(?P<hour>\d{1,2})(?:\s+(?P<min>15|30|45|00))?(?![\d:.])")


def _normalise(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "")
    t = _num._norm(t)  # lower case, ASCII digits, Indic digits too
    t = re.sub(r"[?!,\"()\-–]", " ", t)
    return " ".join(str(w) for w in _num._to_numbers([w for w in t.split() if w]))


def _period(text: str, start: int, end: int) -> str | None:
    """The period word nearest this time, looking a few words either side: "সকাল দশটায়", "दस बजे सुबह"."""
    window = text[max(0, start - 24) : end + 24]
    for words, name in ((_PM, "pm"), (_NOON, "noon"), (_AM, "am")):
        if re.search(rf"{_LEFT}(?:{_alt(words)})(?![a-z])", window):
            return name
    return None


def _clock(hour: int, minute: int, period: str | None) -> str | None:
    if minute < 0:  # পৌনে / पौने: a quarter TO the hour
        hour, minute = hour - 1, 60 + minute
    if not (0 <= minute < 60) or not (0 <= hour <= 23):
        return None
    if hour <= 12:
        if period == "pm" and hour < 12:
            hour += 12
        elif period == "noon" and hour < 6:
            hour += 12
        elif period is None and 1 <= hour <= 7:
            hour += 12  # the clinic day: "ছটায়" is the evening chamber, not 6 a.m.
    return f"{hour:02d}:{minute:02d}"


def spoken_times(text: str) -> list[str]:
    """Every clock time in `text`, in the order said, as "HH:MM". [] when there is none."""
    t = _normalise(text)
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []

    def add(m: re.Match, hour: int, minute: int, explicit: str | None = None) -> None:
        if any(s < m.end() and m.start() < e for s, e in taken):
            return  # already read as part of a longer expression
        value = _clock(hour, minute, explicit or _period(t, m.start(), m.end()))
        if value:
            found.append((m.start(), value))
            taken.append((m.start(), m.end()))

    def ap_of(raw: str | None) -> str | None:
        if not raw:
            return None
        raw = raw.replace(".", "")
        return "am" if raw.startswith("a") else "pm" if raw.startswith("p") else None

    for m in _COLON.finditer(t):
        add(m, int(m["hour"]), int(m["min"]), ap_of(m["ap"]))
    for m in _EN_PAST.finditer(t):
        add(m, int(m["hour"]), 30 if m["kind"] == "half" else 15)
    for m in _EN_TO.finditer(t):
        add(m, int(m["hour"]), -15)
    for m in _EN_MARKED.finditer(t):
        add(m, int(m["hour"]), int(m["min"] or 0), ap_of(m["ap"]))
    for m in _BN_HALF.finditer(t):
        add(m, *_BN_HALVES[m["half"]])
    for m in _HI_HALF.finditer(t):
        add(m, *_HI_HALVES[m["half"]])
    for m in _BN_CLOCK.finditer(t):
        hour = int(m["hour"]) if m["hour"].isdigit() else _BN_HOURS.get(m["hour"])
        if hour is None:
            continue
        minute = _MODIFIER.get(m["mod"], 0) if m["mod"] else 0
        if m["min"]:
            minute = int(m["min"]) if m["min"].isdigit() else _BN_MINUTES.get(m["min"], 0)
        if m["mod"] in _QUARTER_TO:
            minute = -15
        # "টা" is also Bengali's COUNTING ending: "একটা সিবিসি" is "a CBC", "তিনটে টেস্ট" three tests. So the hour must
        # show it is a time -- the locative "টায়" (or any ending), a quarter/half word, minutes, a period word near it,
        # or be the whole short answer ("দশটা" to "কোন সময়?").
        if not (m["tail"] or m["mod"] or m["min"] or _period(t, m.start(), m.end()) or len(t.split()) <= 3):
            continue
        add(m, hour, minute)
    for m in _HI_CLOCK.finditer(t):
        if not (m["mod"] or m["bje"]):
            continue  # a bare number is not a time: it needs बजे or साढ़े / सवा / पौने
        minute = _MODIFIER.get(m["mod"], 0) if m["mod"] else int(m["min"] or 0)
        add(m, int(m["hour"]), minute)
    for m in _EN_AT.finditer(t):
        add(m, int(m["hour"]), int(m["min"] or 0))
    return [v for _, v in sorted(found)]


def as_clock(value: str | None) -> str | None:
    """A time slot the model wrote ("10:00", "10 AM", "সকাল ১০টা", "साढ़े दस") as "HH:MM", or None if it is not one."""
    if not value:
        return None
    times = spoken_times(value)
    if times:
        return times[0]
    bare = re.fullmatch(r"\s*(\d{1,2})\s*", _normalise(value))
    return _clock(int(bare.group(1)), 0, None) if bare else None
