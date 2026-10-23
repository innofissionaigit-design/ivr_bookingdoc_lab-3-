"""agent/spoken_time.py: the clock time a caller said, in Bengali, Hindi and English, as "HH:MM".

    python -m pytest tests/test_spoken_time.py -v

The Bengali cases are the live run of 2026-10-07 (scripts/live_story_check.py) plus the forms Kolkata callers use for
quarter and half hours. The negative cases are the ones that would book a wrong slot: a phone number said digit by digit,
a weekday that ends in "বার", a day with no time in it.
"""

import pytest

from agent.spoken_time import as_clock, spoken_times


@pytest.mark.parametrize(
    "said,times",
    [
        # Bengali
        ("সকাল দশটায়", ["10:00"]),
        ("শুক্রবার, সকাল দশটায়", ["10:00"]),
        ("সকাল দশটাজোট", ["10:00"]),  # what the recogniser actually wrote on the live run
        ("সকাল সাড়ে দশটায়", ["10:30"]),
        ("সোয়া দশটা", ["10:15"]),
        ("পৌনে এগারোটা", ["10:45"]),
        ("দশটা পনেরো", ["10:15"]),
        ("১০টায়", ["10:00"]),
        ("সন্ধ্যা ছটায়", ["18:00"]),
        ("ছটায়", ["18:00"]),  # no period word: the clinic's evening chamber, not 6 a.m.
        ("দেড়টায়", ["13:30"]),
        ("আমার নাম রাহুল বসু, শুক্রবার সকাল দশটায় ডাক্তার দেবাশিস দাসের সাথে", ["10:00"]),
        # Hindi
        ("सुबह दस बजे", ["10:00"]),
        ("साढ़े दस बजे", ["10:30"]),
        ("शाम साढ़े छह बजे", ["18:30"]),
        ("सवा दस", ["10:15"]),
        ("पौने ग्यारह", ["10:45"]),
        ("दस बजकर पंद्रह मिनट", ["10:15"]),
        # English
        ("at ten in the morning", ["10:00"]),
        ("at ten thirty in the morning", ["10:30"]),
        ("at seven in the evening", ["19:00"]),
        ("half past ten", ["10:30"]),
        ("quarter to eleven", ["10:45"]),
        ("10 am", ["10:00"]),
        ("6 pm", ["18:00"]),
        ("eleven o clock", ["11:00"]),
        ("10:30", ["10:30"]),
    ],
)
def test_a_spoken_time_is_read_as_the_clock(said, times):
    assert spoken_times(said) == times


@pytest.mark.parametrize(
    "said",
    [
        "নয় আট তিন শূন্য শূন্য এক দুই তিন চার পাঁচ",  # a phone number, digit by digit
        "नौ आठ तीन शून्य शून्य पांच छह सात आठ नौ",
        "nine eight three zero zero nine eight seven six five",
        "শুক্রবার",  # ends in বার (twelve) but is a weekday
        "কাল",
        "আমি একটা সিবিসি টেস্ট বুক করতে চাই",  # একটা is "a", not one o'clock
        "",
    ],
)
def test_something_that_is_not_a_time_is_never_read_as_one(said):
    assert spoken_times(said) == []


@pytest.mark.parametrize(
    "model_wrote,clock",
    [("10:00", "10:00"), ("10 AM", "10:00"), ("সকাল ১০টা", "10:00"), ("साढ़े दस", "10:30"), ("সকাল", None), ("কাল", None)],
)
def test_what_the_model_wrote_is_kept_only_if_it_is_a_time(model_wrote, clock):
    assert as_clock(model_wrote) == clock


@pytest.mark.parametrize(
    "said,times",
    [
        ("আমি একটা সিবিসি টেস্ট বুক করতে চাই", []),  # "a CBC": counting, not one o'clock
        ("তিনটা টেস্টের দাম কত", []),  # three tests
        ("দশটা", ["10:00"]),  # the whole answer to "what time?"
        ("দুপুর একটায়", ["13:00"]),
    ],
)
def test_the_bengali_counting_ending_is_not_a_clock(said, times):
    assert spoken_times(said) == times


# ---- agent/spoken_digits.py: a number read out digit by digit -----------------------------------------------------


@pytest.mark.parametrize(
    "said,number",
    [
        ("নয় আট তিন শূন্য শূন্য এক দুই তিন চার পাঁচ", "9830012345"),
        ("नौ आठ तीन शून्य शून्य पांच छह सात आठ नौ", "9830056789"),  # live: model wrote words, number was "refused"
        ("nine eight three zero zero nine eight seven six five", "9830098765"),
        ("প্রিয়া সেন, নয় আট তিন এক এক এক দুই দুই দুই তিন", "9831112223"),  # a name before the number
        ("নয় আট তিন একএক এক দুই দুই দুই তিন", "9831112223"),  # live: "এক এক" glued by the recogniser
        ("double nine eight three oh oh one two three four", "9983001234"),
        ("नाइन एट थ्री जीरो जीरो वन टू थ्री फोर फाइव", "9830012345"),
        ("98300-12345", "9830012345"),
    ],
)
def test_a_number_read_out_is_captured(said, number):
    from agent.spoken_digits import spoken_phone

    assert spoken_phone(said) == number


@pytest.mark.parametrize(
    "said", ["আমি একটা সিবিসি টেস্ট বুক করতে চাই", "শুক্রবার সকাল দশটায়", "নয়ন দাস", "আমার কাছে কোনো নম্বর নেই"]
)
def test_words_that_are_not_a_number_are_not_one(said):
    from agent.spoken_digits import spoken_phone

    assert spoken_phone(said) is None
