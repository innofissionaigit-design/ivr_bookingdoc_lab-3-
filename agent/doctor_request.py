"""Does what the caller said in the "doctor" slot name a doctor at all?

The intent model is told to copy the doctor term "as the caller said it"
(agent/llm.py), and it does -- including when the caller named nobody. A real
call on 2026-10-06:

    caller: আমি একজন ডক্টর দেখাব        ("I'll see a doctor")
    model : book_appointment, doctor_name = "একজন ডক্টর"   ("a doctor")

The model followed its instructions. What went wrong was downstream: that
string was stored as if it were a name, the caller was asked for a day and a
time, and only when the slot was finally held did the clinic answer "nobody is
called that" -- so the caller heard

    দুঃখিত, 'একজন ডক্টর' নামে কোনো ডাক্তার খুঁজে পেলাম না।
    ("sorry, I couldn't find a doctor named 'a doctor'")

three questions after the one that actually needed asking: WHICH doctor.

This module answers the one question that decides the flow -- is there a
person's name in here, or only "a doctor"? -- in Bengali, Hindi and English,
before anything is looked up. It deliberately does NOT try to work out which
department "heart doctor" means: the department list lives in the clinic's
database, and clinic-api matches against it (clinic-api/department_match.py).
A word list in agent/ guessing at departments is the exact shape of guess the
truth boundary forbids (CLAUDE.md section 1).

The rule is subtractive, which is what keeps it safe. Words that cannot be part
of a name -- articles, "doctor" in every script, "see", "want", "an
appointment" -- are removed, and if nothing is left (or only bare initials,
which identify nobody on their own) the caller named no one. A word this module
does not recognise is KEPT, so an unusual name is never thrown away; the worst
case is that a garbled request is looked up and comes back not-found, which the
booking flow already handles.
"""

from __future__ import annotations

import re
import unicodedata

# Words that carry no part of a doctor's name, in every script a caller here
# uses. Grouped by role so a reviewer can see why each is safe to discard.
_FILLER = frozenset(
    unicodedata.normalize("NFC", w).lower()
    for w in (
        # --- "doctor" itself, every spelling ASR has produced or a caller says
        "doctor", "doctors", "dr", "doc", "daktar", "dakter", "doktor",
        "ডাক্তার", "ডাক্তারের", "ডাক্তারকে", "ডাক্তারবাবু", "ডাক্তাররা", "ডাক্তারদের",
        "ডক্টর", "ডক্টরের", "ডক্টরকে", "ডাঃ", "ডঃ",
        "डॉक्टर", "डाक्टर", "डॉक्टरों", "डॉ", "डॉक्टर्स",
        # The Hindi recogniser also writes the English "r" with a trailing halant (डॉक्टर्). Live 2026-10-09 it reached
        # the clinic as a doctor called "डॉक्टर्" -- "no doctor by that name" -- because only the plain spelling was here.
        "डॉक्टर्", "डाक्टर्",
        # --- articles and quantifiers: "a doctor", "any doctor", "one doctor"
        "a", "an", "the", "any", "some", "one", "good", "available", "best",
        "একজন", "কোনো", "কোন", "একটা", "যেকোনো", "যে", "কেউ", "ভালো", "ভাল",
        "ekjon", "kono", "ekta",
        "एक", "कोई", "किसी", "अच्छा", "अच्छे", "ek", "koi", "kisi",
        # --- the request around it: "I want to see", "book with", "appointment"
        "i", "me", "my", "want", "wanna", "need", "to", "see", "meet", "consult",
        "visit", "show", "book", "with", "for", "please", "appointment", "an",
        "আমি", "আমার", "আমাকে", "দেখাব", "দেখাবো", "দেখাতে", "দেখানো", "দেখান",
        "চাই", "চাইছি", "দরকার", "লাগবে", "অ্যাপয়েন্টমেন্ট", "এপয়েন্টমেন্ট",
        "করুন", "করব", "করবো", "নিতে", "নেব", "নেবো", "সাথে", "সঙ্গে", "একটু",
        "dekhabo", "dekhate", "chai", "lagbe", "dorkar",
        "मुझे", "मैं", "मेरा", "दिखाना", "दिखाने", "दिखाऊं", "दिखाना", "है", "हैं",
        "चाहिए", "चाहता", "चाहती", "अपॉइंटमेंट", "से", "को", "मिलना", "का", "की",
        "के", "करना", "करवाना", "बुक", "dikhana", "chahiye", "milna",
        # --- a person POINTED AT, not named: "উনি" ("he"). Live call 2026-10-07: "অন্য কোন কোন সময় বসেন উনি"
        # ("what other times does he sit?") reached the clinic as a doctor called "উনি" -- and as one called
        # "বসেন". A pronoun is resolved to the doctor being discussed (agent/enquiry_followup.py), never looked up.
        "উনি", "ওনি", "উনার", "ওনার", "ওঁর", "ওঁকে", "ওনাকে", "উনাকে", "তিনি", "তাঁর", "তাঁকে", "তার", "ওর", "সে",
        "ইনি", "এনার", "ওই", "সেই", "এই", "একই", "অন্য", "আরেকজন",
        "वो", "वे", "वह", "उनका", "उनकी", "उनके", "उन्हें", "उनको", "उस", "इन", "इनका", "दूसरे",
        "he", "him", "his", "she", "her", "they", "them", "same", "other", "another", "that", "this",
        # --- what the doctor DOES and WHEN, said around the name: "sits", "comes", "is there", "what time"
        "বসেন", "বসবেন", "বসছেন", "বসে", "বসেন?", "আসেন", "আসবেন", "থাকেন", "থাকবেন", "আছেন", "আছে",
        "কখন", "কবে", "কোথায়", "কটায়", "সময়", "সময়ে", "টাইম", "টাইমে", "দিন", "দিনে", "বার", "আজ", "কাল",
        "পরশু", "সকালে", "বিকেলে", "সন্ধ্যায়", "রাতে", "কি", "কী", "আর", "এবং", "বা", "নাকি",
        "बैठते", "बैठेंगे", "बैठती", "आते", "आएंगे", "रहते", "कब", "कहाँ", "कहां", "समय", "टाइम", "दिन", "आज",
        "कल", "सुबह", "शाम", "क्या", "और", "या",
        "sit", "sits", "come", "comes", "when", "where", "time", "timing", "timings", "day", "days", "today",
        "tomorrow", "available", "there", "is", "are", "does", "do", "what", "which", "and", "or",
        "bosen", "boshen", "kokhon", "kobe", "kakhon", "somoy", "aaj", "kal", "ache", "achen",
    )
)
# Checked against the clinic's 32 seeded doctors (clinic-api/seed.py): no first name or surname, in any script, is in
# this list -- tests/test_doctor_request.py keeps that true. A name it removed would be a doctor nobody could book.

_PUNCT = re.compile(r"[\s,.!?।॥;:'\"()\-–—/]+")


def _tokens(text: str) -> list[str]:
    t = unicodedata.normalize("NFC", text or "").lower()
    return [w for w in _PUNCT.split(t) if w]


def name_tokens(text: str) -> list[str]:
    """What is left of `text` once every word that cannot be part of a name is
    removed. Order kept, so the remainder can be looked up as said."""
    return [w for w in _tokens(text) if w not in _FILLER]


def names_a_doctor(text: str | None) -> bool:
    """True when `text` carries something that could be a doctor's name.

    False for "ডাক্তার", "একজন ডক্টর", "a doctor", "any good doctor",
    "डॉक्टर", "कोई डॉक्टर" -- and for bare initials ("Dr. A."), which match
    every doctor who shares the letter and so identify none of them.
    """
    rest = name_tokens(text or "")
    if not rest:
        return False
    # One-character tokens are initials (or ASR noise). Alone they name nobody:
    # this is precisely how "a doctor" offered four doctors whose name starts
    # with "A." on the live system.
    return any(len(w) > 1 for w in rest)
