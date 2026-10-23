"""clinic-api/department_match.py: which department did the caller name?

Before this module the clinic matched departments on the Latin name only, so
"কার্ডিওলজি", "হার্টের ডাক্তার", "दिल का डॉक्टर" and "skin doctor" all came back not
found (measured 2026-10-06). These tests pin the three rules the module's docstring
gives, in all three languages, plus the traps a naive matcher falls into.

The two clinic-api modules are loaded by FILE PATH, not by putting clinic-api/ on
sys.path: clinic-api/main.py shares its module name with the orchestrator, and a
path entry left behind here would change what a later test's `import main` finds.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

CLINIC_API = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clinic-api")


def _load(name: str):
    # Registered under a private name before it runs: @dataclass resolves its module through
    # sys.modules, and the private name keeps it clear of clinic-api's own `department_match`.
    import sys

    key = f"_dm_test_{name}"
    spec = importlib.util.spec_from_file_location(key, os.path.join(CLINIC_API, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


dm = _load("department_match")
i18n = _load("i18n_content")

DEPARTMENTS = [
    dm.DepartmentKeys(name, [*i18n.DEPARTMENT_NAMES_I18N[name], *i18n.DEPARTMENT_ALIASES[name]])
    for name in i18n.DEPARTMENT_ALIASES
]

RESOLVES = {
    # Bengali
    "হার্টের ডাক্তার": "Cardiology",
    "কার্ডিওলজি": "Cardiology",
    "মেডিসিন": "General Medicine",
    "জেনারেল মেডিসিন": "General Medicine",
    "মেয়েদের ডাক্তার": "Gynaecology & Obstetrics",
    "গাইনি": "Gynaecology & Obstetrics",
    "হাড়ের ডাক্তার": "Orthopaedics",
    "কানের ডাক্তার": "ENT",
    "নাক কান গলা": "ENT",
    "চামড়ার ডাক্তার": "Dermatology",
    "বাচ্চাদের ডাক্তার": "Paediatrics",
    "সুগারের ডাক্তার": "Diabetology & Endocrinology",
    # Hindi (Devanagari -- what the Hindi recogniser writes)
    "दिल का डॉक्टर": "Cardiology",
    "जनरल डॉक्टर": "General Medicine",
    "महिला डॉक्टर": "Gynaecology & Obstetrics",
    "हड्डी का डॉक्टर": "Orthopaedics",
    "गले का डॉक्टर": "ENT",
    "त्वचा का डॉक्टर": "Dermatology",
    "बच्चों का डॉक्टर": "Paediatrics",
    "थायराइड": "Diabetology & Endocrinology",
    # English
    "heart doctor": "Cardiology",
    "cardiology": "Cardiology",
    "cardiologists": "Cardiology",
    "lady doctor": "Gynaecology & Obstetrics",
    "skin doctor": "Dermatology",
    "child doctor": "Paediatrics",
    "ENT": "ENT",
    # Live 2026-10-09, a whole Hindi call lost to one spelling: the caller asked for gynaecology
    # twice and was sent to the counter. English "gy" is written गाय or गाइ and the table had
    # only the first; the sentence "गाइनेकोलॉजी में दिखाना जाहता हूं" is the one that failed.
    "गाइनेकोलॉजी में दिखाना जाहता हूं": "Gynaecology & Obstetrics",
    "मैं गायन डॉक्टर् के बारे में पूंछ रहा हूं": "Gynaecology & Obstetrics",
    "गाइनी": "Gynaecology & Obstetrics",
    "स्त्रीरोग": "Gynaecology & Obstetrics",
    # a recogniser cut a word short; long enough and unique, so it still resolves
    "অর্থোপেডি": "Orthopaedics",
}


@pytest.mark.parametrize("said,department", list(RESOLVES.items()))
def test_a_department_resolves_in_every_language(said, department):
    assert dm.match(said, DEPARTMENTS) == [department]


@pytest.mark.parametrize(
    "said",
    [
        # Rule 1: words that name no department match nothing on their own
        "ডাক্তার",
        "doctor",
        "डॉक्टर",
        "বিভাগ",
        # Rule 2: "ENT" is the letters e-n-t, inside all of these
        "appointment",
        "patient",
        "department",
        # Rule 3: "दिल" (heart) begins "दिल्ली" (Delhi)
        "दिल्ली",
        # a doctor's name is not a department
        "সেন",
        "Sen",
    ],
)
def test_traps_a_naive_matcher_falls_into_match_nothing(said):
    assert dm.match(said, DEPARTMENTS) == []


def test_every_alias_resolves_to_its_own_department_and_no_other():
    """No alias may collide with another department -- a collision would make a real
    caller's phrasing ambiguous, or worse, resolve to the wrong clinic."""
    wrong = [
        (department, alias, dm.match(alias, DEPARTMENTS))
        for department, aliases in i18n.DEPARTMENT_ALIASES.items()
        for alias in aliases
        if dm.match(alias, DEPARTMENTS) != [department]
    ]
    assert wrong == []


def test_every_department_has_bengali_hindi_and_english_forms():
    def has(script_range, forms):
        lo, hi = script_range
        return any(any(lo <= ch <= hi for ch in f) for f in forms)

    for department, aliases in i18n.DEPARTMENT_ALIASES.items():
        forms = [department, *i18n.DEPARTMENT_NAMES_I18N[department], *aliases]
        assert has(("ঀ", "৿"), forms), f"{department}: no Bengali form"
        assert has(("ऀ", "ॿ"), forms), f"{department}: no Devanagari form"
        assert any(all(ord(c) < 128 for c in f) for f in forms), f"{department}: no English form"


@pytest.mark.parametrize(
    "key,said,expected",
    [
        ("পাল", "পাল", True),
        ("পাল", "পালের", True),  # a case ending is the same name
        ("পাল", "পালিত", False),  # Palit is a different surname
        ("সেন", "সেনগুপ্ত", False),  # Sen is not Sengupta
        ("दत्ता", "दत्त", False),  # the short form does not contain the long one...
        ("दत्त", "दत्ता", True),  # ...but the long-a is a spelling variant of it
        ("ent", "department", False),
        ("cardio", "a cardio doctor", True),
    ],
)
def test_word_match_is_a_whole_word_with_case_endings(key, said, expected):
    assert dm.word_match(key, said) is expected
