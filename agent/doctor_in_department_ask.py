"""Is the caller asking whether the clinic HAS a doctor in some department?

    "আপনাদের ওখানে ডায়াবেটোলজিতে ডাক্তার আছে?"   do you have a doctor in diabetology?
    "do you have a cardiologist"                   (a doctor word and an existence word)

WHY THIS EXISTS
---------------
Live 2026-10-09, in Bengali: "আমায় ডক্টর ডায়াবেটোলজিতে দেখাতে বলেছে তো আপনাদের ওখানে ডাক্তার আছে
ডায়াবেটোলজি" ("I've been told to see a doctor in diabetology -- do you have one there?") was answered with
"যে নম্বর থেকে বুক করেছিলেন সেটা বলবেন?" -- "which number did you book from?". The model filed it as
`lookup_booking`: it read "বলেছে" ("was told") and "আছে" ("there is") as a booking that already exists. Run again
on the pod it did the same, so it is not a one-off; and the wrong answer is not a harmless one -- it starts an
identity check for a caller who asked a plain question about the clinic.

The question has a clean answer that needs no model: the clinic's own doctors-by-department lookup
(`doctors_by_department`, the same one the "which doctors do you have" story already uses) reads the department
out of the sentence against the real department table. This module only decides whether the turn is SHAPED like
that question; the department itself is never guessed here -- see `main._answer_doctor_in_department`, which
speaks only when clinic-api says it found one and no doctor is named in the sentence.

WHAT IT IS DELIBERATELY NOT
---------------------------
A MISS changes nothing: the turn goes to the model exactly as before. So the rule is narrow, and every sentence
about a booking is left to the flows that own it:

  * a booking noun (appointment, বুকিং, अपॉइंटमेंट ...)    -> "my diabetology appointment -- is it there?"
    is a read-back (agent/lookup_request.py), not a roster question
  * a want or another action (চাই, "I want", home visit)    -> a booking request, not a question
  * "earliest"                                              -> its own story (agent/earliest_request.py)

Deciding what the caller WANTS is allowed in agent/; deciding what is TRUE is not (CLAUDE.md section 1). This
returns a boolean and touches no data.

REASONED, NOT MEASURED, like the lists it borrows (agent/doctor_list.py): the Bengali is the clinic's own
loanword spelling, the Hindi and romanised forms were not reviewed by a native speaker.
"""

from __future__ import annotations

from agent.doctor_list import _DOCTOR_STEMS, _EXISTS, _stem_hit, _words
from agent.lookup_request import (
    asks_about_another_action,
    asks_for_the_earliest,
    mentions_a_booking,
    wants_new_booking,
)


def asks_if_a_doctor_is_there(text: str) -> bool:
    """A doctor word AND an existence word, and nothing that makes it a booking."""
    if not text or not text.strip():
        return False
    if (
        wants_new_booking(text)
        or asks_about_another_action(text)
        or asks_for_the_earliest(text)
        or mentions_a_booking(text)
    ):
        return False
    words = _words(text)
    return any(_stem_hit(w, _DOCTOR_STEMS) for w in words) and any(w in _EXISTS for w in words)
