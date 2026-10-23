"""A phone number read out digit by digit -- "নয় আট তিন শূন্য শূন্য ...", "नौ आठ तीन ...", "nine eight three ..." -- as digits.

story: "Caller cannot give a contact number" (a number different from the calling line, "captured digit by digit"), and
every booking that needs a number for its confirmation.

Before this module the number came only from the intent model's `phone` slot, read with str.isdigit(). Live, 2026-10-07:
a Hindi caller said "नौ आठ तीन शून्य शून्य पांच छह सात आठ नौ", the recogniser heard it perfectly, the model wrote it back in
words, no digit was found -- and after the second ask the booking concluded the caller REFUSED a number and said no
confirmation would be sent. The shared number normaliser could not stand in: it drops zeros (9830012345 came out as
eight digits) and knows neither "double nine" nor the English digit names callers say in Bengali or Hindi script.

THE RULE. Every spoken digit (in any of the three scripts, native digits, English digit names, "double"/"triple") is read
in order, and the LONGEST unbroken run is the number. A run, because "একটা টেস্ট" ("a test") contains the word for one,
and a time ("দশটায়") or a name between the digits ends a run rather than joining two numbers. What comes back is only a
CANDIDATE string of digits: agent/booking_flow.normalise_phone still decides whether it is a 10-digit number at all.

Pure: no I/O.
"""

from __future__ import annotations

import re
import unicodedata

_DIGIT_WORDS = {
    # Bengali, and the English digit names as Bengali callers say them
    "শূন্য": 0,
    "শুন্য": 0,
    "জিরো": 0,
    "এক": 1,
    "ওয়ান": 1,
    "দুই": 2,
    "দু": 2,
    "টু": 2,
    "তিন": 3,
    "থ্রি": 3,
    "চার": 4,
    "ফোর": 4,
    "পাঁচ": 5,
    "পাচ": 5,
    "ফাইভ": 5,
    "ছয়": 6,
    "ছ": 6,
    "সিক্স": 6,
    "সাত": 7,
    "সেভেন": 7,
    "আট": 8,
    "এইট": 8,
    "নয়": 9,
    "ন": 9,
    "নাইন": 9,
    # Hindi, and the English digit names in Devanagari
    "शून्य": 0,
    "सुन्न": 0,
    "ज़ीरो": 0,
    "जीरो": 0,
    "एक": 1,
    "वन": 1,
    "दो": 2,
    "टू": 2,
    "तीन": 3,
    "थ्री": 3,
    "चार": 4,
    "फोर": 4,
    "फ़ोर": 4,
    "पांच": 5,
    "पाँच": 5,
    "फाइव": 5,
    "छह": 6,
    "छः": 6,
    "छ": 6,
    "छे": 6,
    "सिक्स": 6,
    "सात": 7,
    "सेवन": 7,
    "आठ": 8,
    "एट": 8,
    "नौ": 9,
    "नाइन": 9,
    # English
    "zero": 0,
    "oh": 0,
    "o": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    # How the recogniser actually spells the English digit names -- live 2026-10-07, a cancellation that never found its
    # booking: "জিরো ওন টু থ্রি ফোর ফাইভ সিক্স সেবেন ..." (ওন, সেবেন) in Bengali, "giro won two three ..." in English.
    # "নাইট" is "eight" with the "n" of a preceding "seven" carried over ("seven-n-eight"): every one of three live
    # readings of "... সেবেন নাইট নাইন" / "নাইট নাইন" was eight, and "nine" always comes out "নাইন". Not English "night",
    # which is a real word next to times ("Saturday night"). A misread digit is still caught: a cancellation found by
    # number reads the number back before anything is cancelled (reply_templates.booking_confirmation_readback).
    "জেরো": 0,
    "জিরু": 0,
    "ওন": 1,
    "ওয়ন": 1,
    "থ্রী": 3,
    "ফাইব": 5,
    "সেবেন": 7,
    "সেভন": 7,
    "সেবন": 7,
    "এট": 8,
    "এইড": 8,
    "নাইট": 8,
    "নাইন্": 9,
    "ज़ीरो": 0,
    "सेवेन": 7,
    "सेव्हन": 7,
    "एइट": 8,
    "ऐट": 8,
    "नाइट": 8,
    "नाईन": 9,
    "फाईव": 5,
    "giro": 0,
    "jiro": 0,
    "ziro": 0,
    "won": 1,
    "wan": 1,
    "sevan": 7,
}
_DIGIT_WORDS = {unicodedata.normalize("NFC", k): v for k, v in _DIGIT_WORDS.items()}
_REPEAT = {
    unicodedata.normalize("NFC", k): v
    for k, v in {"ডাবল": 2, "ট্রিপল": 3, "डबल": 2, "ट्रिपल": 3, "double": 2, "triple": 3}.items()
}
_NATIVE = str.maketrans("০১২৩৪৫৬৭৮৯०१२३४५६७८९", "01234567890123456789")
_SPLIT = re.compile(r"[\s,।॥;:?!\-–]+")  # "98300-12345" is two groups of one number
_JOINERS = frozenset({"-", "–", "."})  # "98300-12345" is still one number


def _digits_of(token: str) -> str | None:
    """The digits one spoken token stands for, or None if it is not a digit token."""
    t = token.translate(_NATIVE).strip(".-–")
    if t.isdigit():
        return t
    if t in _DIGIT_WORDS:
        return str(_DIGIT_WORDS[t])
    return _glued(t)


# Pieces a glued token may be split into: whole digit words of at least two letters. One-letter forms ("ন" for nine,
# "ছ", "o") are left out, or the name "নয়ন" (Nayan) would be read as নয় + ন = "99".
_GLUE_PIECES = sorted((w for w in _DIGIT_WORDS if len(w) >= 2), key=len, reverse=True)


def _glued(token: str) -> str | None:
    """Digit words the recogniser wrote without a space -- live run, 2026-10-07: "এক এক" came back as "একএক". Only a
    token that splits COMPLETELY into two or more digit words counts; anything left over and it is not a number."""
    out, rest = "", token
    while rest:
        piece = next((w for w in _GLUE_PIECES if rest.startswith(w)), None)
        if piece is None:
            return None
        out += str(_DIGIT_WORDS[piece])
        rest = rest[len(piece) :]
    return out if len(out) >= 2 else None


def spoken_digit_runs(text: str) -> list[str]:
    """Every unbroken run of spoken digits in `text`, in order ("double nine" -> "99")."""
    words = [w for w in _SPLIT.split(unicodedata.normalize("NFC", text or "").lower()) if w and w not in _JOINERS]
    runs: list[str] = []
    current = ""
    repeat = 1
    for w in words:
        if w in _REPEAT:
            repeat = _REPEAT[w]
            continue
        d = _digits_of(w)
        if d is None:
            if current:
                runs.append(current)
            current, repeat = "", 1
            continue
        current += d * repeat if len(d) == 1 else d
        repeat = 1
    if current:
        runs.append(current)
    return runs


def spoken_phone(text: str) -> str | None:
    """The longest run of spoken digits, as a candidate number -- or None when there are fewer than seven digits in any
    run (a day, a time, "one test" is never a phone number). booking_flow.normalise_phone has the final say."""
    runs = spoken_digit_runs(text)
    best = max(runs, key=len, default="")
    return best if len(best) >= 7 else None


def only_a_number(text: str) -> bool:
    """Is the whole utterance a number being read out -- nothing but digit words / digits, at least seven of them?
    Callers here read numbers in English whatever language they speak, so such a turn says nothing about which
    language the CALL is in (agent/lang_select.pick_candidate)."""
    words = [w for w in _SPLIT.split(unicodedata.normalize("NFC", text or "").lower()) if w and w not in _JOINERS]
    if not words or not all(w in _REPEAT or _digits_of(w) is not None for w in words):
        return False
    return len("".join(spoken_digit_runs(text))) >= 7
