"""A doctor named alongside their department is not lost -- bn, hi and en.

Live call, 2026-10-08:

    [User] कार्डियोलॉजी डिपार्टमेंट में डॉक्टर् बेनर्जी अवैलेबल है
    [AI]   कार्डियोलॉजी विभाग में ये डॉक्टर बैठते हैं -- कौशिक भट्टाचार्य, मानस दत्ता, निर्मल रॉय, सुब्रत बनर्जी...

The caller named ONE doctor and was read all four. Reproduced: it happens only when clinic-api's own
"which doctors does this sentence name?" lookup cannot be reached. That lookup is the only thing that reads the
WHOLE sentence, so when it fails the doctor's name is lost and the turn degrades to the department listing --
even though the name is findable with no network call at all, in the gazetteer the fast path already carries.
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _conversation import conversation  # noqa: E402

from agent.fast_path import COMMIT_FLOOR, Catalogue, FastPath  # noqa: E402
from agent.tools_client import ToolCallError  # noqa: E402

LANGS = ("bn", "hi", "en")
BANERJEE = "Dr. S. Banerjee"
SPOKEN = {"bn": "সুব্রত ব্যানার্জী", "hi": "सुब्रत बनर्जी", "en": "Subrata Banerjee"}
# How the reply names him once the clinic has resolved him exactly -- by surname, not in full.
SURNAME = {"bn": "ব্যানার্জী", "hi": "बनर्जी", "en": "Banerjee"}
ASKED = {
    "bn": ("কার্ডিওলজি ডিপার্টমেন্টে ডাক্তার ব্যানার্জী আছেন", "কার্ডিওলজি"),
    # "बेनर्जी" is what the recogniser wrote; the seeded alias is "बनर्जी"
    "hi": ("कार्डियोलॉजी डिपार्टमेंट में डॉक्टर् बेनर्जी अवैलेबल है", "कार्डियोलॉजी"),
    "en": ("is doctor Banerjee available in the cardiology department", "cardiology"),
}
EVERY_CARDIOLOGIST = {"bn": "ভট্টাচার্য", "hi": "भट्टाचार्य", "en": "Bhattacharya"}


@pytest.fixture()
def call(request):
    """A call with the clinic's catalogue loaded into the fast path, as a booted pod has it."""
    with conversation(request.param) as c:
        c.m._fast_path = FastPath(Catalogue(c._run(c.m._tools._client.get("/api/v1/catalogue")).json()))
        yield c


def _break_the_mention_lookup(call):
    async def unavailable(*a, **k):
        raise ToolCallError("doctors/mentioned timed out")

    call.m._tools.doctors_mentioned = unavailable


_each_language = pytest.mark.parametrize("call", LANGS, indirect=True)


@_each_language
def test_the_named_doctor_survives_when_the_mention_lookup_is_down(call):
    lang = call.session.lang
    text, department = ASKED[lang]
    _break_the_mention_lookup(call)
    said = call.say(text, "book_appointment", {"doctor_name": department})
    assert SPOKEN[lang] in said[-1], said
    assert said[-1].endswith("?"), "asked, not asserted -- a local match is weaker than the clinic's own answer"
    assert EVERY_CARDIOLOGIST[lang] not in said[-1], "not the whole department"
    assert call.blocked == []


@_each_language
def test_the_named_doctor_is_found_with_the_lookup_working_too(call):
    """With clinic-api answering, the doctor is settled (or read back) -- never swapped for the department."""
    lang = call.session.lang
    text, department = ASKED[lang]
    said = call.say(text, "book_appointment", {"doctor_name": department})
    assert SURNAME[lang] in said[-1], said
    assert EVERY_CARDIOLOGIST[lang] not in said[-1], "not the whole department"


@_each_language
def test_a_department_with_no_doctor_in_it_still_lists_the_department(call):
    """The listing is right when the caller really did name only a department."""
    lang = call.session.lang
    # "someone" is avoided deliberately: agent/human_request.py reads "see someone" as a request for staff.
    only_dept = {"bn": "কার্ডিওলজি ডিপার্টমেন্টে দেখাতে চাই", "hi": "कार्डियोलॉजी डिपार्टमेंट में दिखाना है",
                 "en": "I want to see a doctor in the cardiology department"}[lang]
    said = call.say(only_dept, "book_appointment", {"doctor_name": ASKED[lang][1]})
    assert EVERY_CARDIOLOGIST[lang] in said[-1], said


def test_the_catalogue_finds_the_name_without_any_network_call():
    """What the fallback rests on: the gazetteer already in memory matches the name in all three languages."""
    with conversation("bn") as c:
        cat = Catalogue(c._run(c.m._tools._client.get("/api/v1/catalogue")).json())
        for lang in LANGS:
            name, _form, score = cat.match(ASKED[lang][0], "doctor", lang, COMMIT_FLOOR)
            assert name == BANERJEE and score >= COMMIT_FLOOR, (lang, name, score)


def test_the_clinics_own_answer_is_never_second_guessed():
    """When clinic-api DOES answer, its answer stands -- including when it names nobody."""
    with conversation("bn") as c:
        c.m._fast_path = FastPath(Catalogue(c._run(c.m._tools._client.get("/api/v1/catalogue")).json()))

        async def names_nobody(*a, **k):
            return {"found": False, "count": 0, "doctors": []}

        c.m._tools.doctors_mentioned = names_nobody
        said = c.say(ASKED["bn"][0], "book_appointment", {"doctor_name": ASKED["bn"][1]})
        # The department listing (every cardiologist), not the single-doctor question the fallback would ask.
        assert EVERY_CARDIOLOGIST["bn"] in said[-1], said


# ============================================================================ the disclosure that followed it

# KCD-353 requires the automated-assistant notice once in the caller's own language. Live 2026-10-08 it was read
# out in full on the strength of a two-word opener ("मेरे को") that the very next sentence admitted it could not
# understand. The control is unchanged -- it is now said on the first turn in that language that actually lands.
GARBLE = {"hi": "मेरे को", "en": "uh the"}
REAL = {
    "hi": ("कार्डियोलॉजी में डॉक्टर बनर्जी", {"doctor_name": "बनर्जी"}),
    "en": ("doctor Banerjee in cardiology", {"doctor_name": "Banerjee"}),
}


@pytest.mark.parametrize("lang", ("hi", "en"))
def test_the_notice_waits_for_a_turn_the_agent_understood(lang):
    from agent.disclosure import disclosure_for

    with conversation(lang) as call:
        call.session.disclosed_langs = {"bn"}  # the Bengali greeting carried it already
        said = call.say(GARBLE[lang], "unclear", {})
        assert disclosure_for(lang) not in said, said
        assert lang not in call.session.disclosed_langs, "still owed, not skipped"

        text, slots = REAL[lang]
        said = call.say(text, "book_appointment", slots)
        assert disclosure_for(lang) in said, said
        assert lang in call.session.disclosed_langs


@pytest.mark.parametrize("lang", ("hi", "en"))
def test_the_notice_is_still_said_exactly_once(lang):
    from agent.disclosure import disclosure_for

    with conversation(lang) as call:
        call.session.disclosed_langs = {"bn"}
        text, slots = REAL[lang]
        first = call.say(text, "book_appointment", slots)
        second = call.say(text, "book_appointment", slots)
        assert disclosure_for(lang) in first and disclosure_for(lang) not in second
