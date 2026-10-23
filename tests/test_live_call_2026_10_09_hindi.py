"""Live Hindi call, 2026-10-09: the caller asked for a department and was twice sent to the counter.

    [User] कौन कौन सा डिपार्टमेंट है वहाँ पे
    [AI]   आप सोनोस्कैन वाणी से बात कर रहे हैं। ...        <- the automated-assistant notice
    [User] कौन कौन सा डिपार्टमेंट है वहाँ पे               <- the caller never said this twice
    [AI]   हमारे यहाँ इन विभागों में डॉक्टर बैठते हैं -- ...
    ...
    [User] गाइनेकोलॉजी में दिखाना चाहता हूँ
    [AI]   माफ़ कीजिए, ठीक से समझ नहीं पाई किस विभाग में दिखाना होगा। काउंटर पर पूछ सकते हैं।

Two unrelated defects, both reproduced here:

1. The repeated question is an ECHO, not a repeat. Ten branches of _dispatch_turn re-sent the
   caller's own sentence to the transcript after the one unconditional send had already done it,
   so every one of those turns showed the question twice -- which reads as "it ignored me".
   The duplicated `turn_count += 1` also aged every turn-count TTL twice as fast on those paths.

2. Naming a department is not describing a symptom. `department_query` only ever consulted
   route_department(), whose keywords are clinician-approved complaints and whose English row
   alone happens to carry a department's own name -- so the Hindi name matched nothing. Two
   spellings of it are in play, too: English "gy" is written गाय or गाइ and the table had only
   the first, so even the department-name matcher missed गाइनेकोलॉजी (see
   tests/test_department_match.py for that half).
"""

from __future__ import annotations

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _conversation import conversation  # noqa: E402

from agent.disclosure import disclosure_for  # noqa: E402

# Spelled with explicit escapes: typed Devanagari is not guaranteed to arrive in one normalisation
# form, and these tests turn on the exact codepoints (SESSION_HANDOVER.md section 6).
WHICH_DEPARTMENTS = (
    "कौन कौन सा "
    "डिपार्टमेंट है "
    "वहाँ पे"
)
# "गाइनेकोलॉजी में दिखाना चाहता हूँ" -- the turn that was sent to the counter.
GYNAE_GAAI = (
    "गाइनेकोलॉजी में "
    "दिखाना जाहता हूं"
)
GYNAE_SLOT = "गाइनेकोलॉजी"
# "मैं गायन डॉक्टर् के बारे में पूछ रहा हूँ" -- the bare stem, and "डॉक्टर्" with a halant.
GYNAE_STEM_SLOT = "गायन"
GYNAE_SPOKEN = "गायनी"  # "गायनी", how the Hindi reply names the department
JOINT_PAIN = "जोड़ों में दर्द"
PREGNANCY = "गर्भावस्था"
GIBBERISH = "अबरकदबरक"
CANNOT_TELL = (
    "समझ नहीं पाई "
    "किस विभाग"
)


def _user_lines(call, since: int) -> list[str]:
    """Every "User" line the transcript was sent after `since`, i.e. what the caller sees of
    their own words. The caller spoke once; more than one line means the agent echoed it."""
    out = []
    for raw in call.session.ws.texts[since:]:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if msg.get("sender") == "User":
            out.append(msg["text"])
    return out


# ============================================================ 1. the question must appear once


def test_one_caller_turn_puts_the_question_on_screen_once():
    """The live symptom: the department question was shown, then the notice, then the question
    AGAIN, then the answer -- so the call read as "it did not pick it up the first time"."""
    with conversation("hi") as call:
        call.session.disclosed_langs.discard("hi")  # a first Hindi turn still owes the notice
        before = len(call.session.ws.texts)
        spoken = call.say(WHICH_DEPARTMENTS, "doctor_availability", {})

        assert _user_lines(call, before) == [WHICH_DEPARTMENTS]
        # and the turn still answers -- the notice must not consume it
        assert disclosure_for("hi") in spoken
        assert [s for s in spoken if s != disclosure_for("hi")], spoken


def test_one_caller_turn_counts_as_one_turn():
    """The duplicate send carried a duplicate `turn_count += 1`; every turn-count TTL
    (TEST_CONFIRM_TTL_TURNS, pending_enquiry) therefore aged twice as fast on these paths."""
    with conversation("hi") as call:
        before = call.session.turn_count
        call.say(WHICH_DEPARTMENTS, "doctor_availability", {})
        assert call.session.turn_count - before == 1


# ====================================================== 2. a department named, not a symptom


@pytest.mark.parametrize(
    "said,slot",
    [
        (GYNAE_GAAI, GYNAE_SLOT),       # "गाइनेकोलॉजी" -- the गाइ spelling the recogniser wrote
        (GYNAE_GAAI, GYNAE_STEM_SLOT),  # "गायन" -- the bare stem, same call
    ],
)
def test_a_department_named_in_hindi_is_answered_not_sent_to_the_counter(said, slot):
    with conversation("hi") as call:
        spoken = " ".join(call.say(said, "department_query", {"symptom_description": slot}))
        assert CANNOT_TELL not in spoken, spoken
        assert GYNAE_SPOKEN in spoken, spoken


@pytest.mark.parametrize("symptom", [JOINT_PAIN, PREGNANCY])
def test_a_symptom_still_routes_by_symptom(symptom):
    """The name lookup is tried ONLY after the symptom router finds nothing, so nothing that
    already resolved may change. Chest pain is deliberately not used here: it is an emergency
    and never reaches this intent at all (agent/emergency.py)."""
    with conversation("hi") as call:
        spoken = " ".join(call.say(symptom, "department_query", {"symptom_description": symptom}))
        assert CANNOT_TELL not in spoken, spoken


def test_a_department_that_is_neither_a_symptom_nor_a_name_is_still_refused():
    """The fallback must not turn "cannot tell" into a guess -- CLAUDE.md's no-guessing rule."""
    with conversation("hi") as call:
        spoken = " ".join(call.say(GIBBERISH, "department_query", {"symptom_description": GIBBERISH}))
        assert CANNOT_TELL in spoken, spoken


# ====================================== 3. the Hindi call's other two turns (same call, later in it)
#
#   [User] मैं गायनी में दिखाना चाहता हूँ
#   [AI]   अच्छा, बताती हूँ।
#   [AI]   किस डॉक्टर को दिखाना चाहते हैं? नाम न पता हो तो तकलीफ़ या विभाग बताइए। ...      <- the department was just SAID
#   [User] गायनी में दिखाना चाहता हूं डॉक्टर् का नाम बताइए
#   [AI]   हमारा क्लिनिक कोलकाता के राजारहाट न्यू टाउन में है ...                          <- the ADDRESS, for "tell me the doctor's name"

from agent.doctor_request import names_a_doctor  # noqa: E402
from agent.fast_path import Catalogue, FastPath  # noqa: E402

WANT_GYNAE = (
    "मैं गायनी में "
    "दिखाना चाहता हूं"
)
GYNAE_DOCTOR_NAME = (
    "गायनी में दिखाना "
    "चाहता हूं डॉक्टर् "
    "का नाम बताइए"
)
DOCTOR_HALANT = "डॉक्टर्"  # डॉक्टर् -- how the Hindi recogniser writes it
ADDRESS_PLEASE = "आपका पता बताइए"  # आपका पता बताइए
WHICH_DOCTOR = "किस डॉक्टर को"  # किस डॉक्टर को -- the wrong question
CLINIC_ADDRESS = "कोलकाता"  # कोलकाता


def test_the_word_doctor_in_the_recognisers_halant_spelling_names_nobody():
    assert not names_a_doctor(DOCTOR_HALANT)
    assert not names_a_doctor(DOCTOR_HALANT[:-1])  # the plain spelling, unchanged
    assert names_a_doctor("बनर्जी")  # a real surname still names someone


@pytest.mark.parametrize(
    "intent,slots",
    [
        ("book_appointment", {}),                        # the model carried no usable doctor
        ("doctor_availability", {"doctor_name": None}),  # ... or filed it as an availability question
    ],
)
def test_a_department_in_the_sentence_is_not_asked_for_again(intent, slots):
    with conversation("hi") as call:
        spoken = " ".join(call.say(WANT_GYNAE, intent, slots))
        assert WHICH_DOCTOR not in spoken, spoken
        assert GYNAE_SPOKEN in spoken, spoken


def test_asking_for_the_doctors_name_in_a_department_lists_its_doctors():
    with conversation("hi") as call:
        spoken = " ".join(call.say(GYNAE_DOCTOR_NAME, "doctor_availability", {"doctor_name": DOCTOR_HALANT}))
        assert GYNAE_SPOKEN in spoken, spoken
        assert DOCTOR_HALANT not in spoken, "the word 'doctor' was looked up as if it were a name"


def test_a_sentence_with_a_doctor_word_is_not_answered_with_the_clinic_address():
    """'tell me the doctor's NAME' is one word from the FAQ phrase 'tell me the ADDRESS' and the same verb:
    0.78 against a 0.72 floor. A sentence that says 'doctor' is not a pure FAQ question."""
    with conversation("hi") as call:
        fast = FastPath(Catalogue(call._run(call.m._tools._client.get("/api/v1/catalogue")).json()))
        assert fast.resolve(GYNAE_DOCTOR_NAME, "hi") is None


def test_a_plain_address_question_is_still_served_by_the_fast_path():
    with conversation("hi") as call:
        fast = FastPath(Catalogue(call._run(call.m._tools._client.get("/api/v1/catalogue")).json()))
        served = fast.resolve(ADDRESS_PLEASE, "hi")
        assert served is not None and served.slots.get("faq_topic") == "location"


# ----------------------------------------------------------------------------- Bengali, the call that worked

BN_WHICH_DEPARTMENTS = (
    "আপনাদের ওখানে "
    "কোন কোন ডিপার্টমেন্ট "
    "রয়েছে"
)


def test_the_bengali_department_question_is_shown_once_and_still_answered():
    with conversation("bn") as call:
        before = len(call.session.ws.texts)
        spoken = call.say(BN_WHICH_DEPARTMENTS, "department_query", {})
        assert _user_lines(call, before) == [BN_WHICH_DEPARTMENTS]
        assert spoken, "the Bengali turn was answered"
