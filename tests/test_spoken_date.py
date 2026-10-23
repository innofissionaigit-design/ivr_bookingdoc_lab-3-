"""agent/spoken_date.py: the calendar, not the model, decides what "আজ / কাল / পরশু / next Monday" mean.

Fixed "today" (Wednesday 7 October 2026) so every expectation is a date anyone can check on a calendar.
"""

from __future__ import annotations

import datetime
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent.spoken_date import IST, clinic_today, spoken_dates  # noqa: E402

WED = datetime.date(2026, 10, 7)


def _d(days: int) -> str:
    return (WED + datetime.timedelta(days=days)).isoformat()


CASES = [
    # Bengali
    ("আজ ডাক্তার দেখাতে চাই", [_d(0)]),
    ("আজকে বিকেলে", [_d(0)]),
    ("কাল সকাল দশটায়", [_d(1)]),
    ("কালকে আসব", [_d(1)]),
    ("আগামীকাল", [_d(1)]),
    ("পরশু দিন অ্যাপয়েন্টমেন্ট করুন সকাল সাড়ে দশটা", [_d(2)]),
    ("তরশু", [_d(3)]),
    ("গতকাল বুক করেছিলাম", [_d(-1)]),
    ("শনিবার", ["2026-10-10"]),
    ("আগামী শনিবারে", ["2026-10-10"]),
    ("এই বুধবার", [_d(0)]),
    ("আগামী বুধবার", [_d(7)]),
    ("পরের সপ্তাহের সোমবার", ["2026-10-12"]),
    ("৮ তারিখে", ["2026-10-08"]),
    ("৫ তারিখ", ["2026-11-05"]),  # the 5th has passed this month
    ("৮ই অক্টোবর", ["2026-10-08"]),
    ("১লা নভেম্বর", ["2026-11-01"]),
    ("আট তারিখে", ["2026-10-08"]),
    ("৩ জানুয়ারি", ["2027-01-03"]),  # January has passed this year
    # Hindi
    ("आज", [_d(0)]),
    ("कल सुबह", [_d(1)]),
    ("परसों", [_d(2)]),
    ("नरसों", [_d(3)]),
    ("अगले सोमवार", ["2026-10-12"]),
    ("शुक्रवार को", ["2026-10-09"]),
    ("अगले हफ़्ते गुरुवार", ["2026-10-15"]),
    ("आठ तारीख", ["2026-10-08"]),
    ("8 अक्टूबर", ["2026-10-08"]),
    ("15 नवंबर 2026", ["2026-11-15"]),
    # English and romanised
    ("today", [_d(0)]),
    ("tomorrow morning", [_d(1)]),
    ("day after tomorrow", [_d(2)]),
    ("next Monday", ["2026-10-12"]),
    ("this Friday", ["2026-10-09"]),
    ("on Wednesday", [_d(0)]),
    ("next week Tuesday", ["2026-10-13"]),
    ("on the 8th", ["2026-10-08"]),
    ("8th October", ["2026-10-08"]),
    ("October 20", ["2026-10-20"]),
    ("aaj", [_d(0)]),
    ("kal", [_d(1)]),
    ("porshu", [_d(2)]),
    ("parso", [_d(2)]),
    # two days, in the order said (moving a booking: the last is the new day)
    ("বৃহস্পতিবারের অ্যাপয়েন্টমেন্টটা শনিবারে সরান", ["2026-10-08", "2026-10-10"]),
    ("move it from tomorrow to next Monday", [_d(1), "2026-10-12"]),
]

NOT_DATES = [
    "আজকাল খুব জ্বর",  # nowadays
    "কালো দাগ",  # black
    "কলকাতা থেকে বলছি",
    "कलकत्ता से",
    "সকাল ১০টায়",  # a time, not a day
    "নম্বর ৯৮৩০০১২৩৪৫",
    "রবি দাস",  # a name, not Sunday
    "may I book",
    "31st November",  # no such day
]


@pytest.mark.parametrize("text,expected", CASES)
def test_a_spoken_day_lands_on_the_calendar_date(text, expected):
    assert spoken_dates(text, WED) == expected


@pytest.mark.parametrize("text", NOT_DATES)
def test_words_that_only_look_like_days_are_not_dates(text):
    assert spoken_dates(text, WED) == []


@pytest.mark.parametrize(
    "today,text,expected",
    [
        (datetime.date(2026, 12, 31), "কাল", ["2027-01-01"]),
        (datetime.date(2026, 12, 31), "5 তারিখ", ["2027-01-05"]),
        (datetime.date(2028, 2, 28), "কাল", ["2028-02-29"]),  # leap year
        (datetime.date(2028, 2, 28), "পরশু", ["2028-03-01"]),
        (datetime.date(2027, 1, 31), "30 তারিখ", []),  # passed in January, and February has no 30th
    ],
)
def test_month_and_year_boundaries(today, text, expected):
    assert spoken_dates(text, today) == expected


def test_today_is_kolkatas_today_whatever_the_server_clock_says():
    assert clinic_today() == datetime.datetime.now(IST).date()
    assert IST.utcoffset(None) == datetime.timedelta(hours=5, minutes=30)
