"""Live 2026-10-12: "আমি গাইনিতে একজন ডক্টরকে দেখাব" ("I will see a doctor in gynae") was not understood, while
"আমি গাইনির একজন ডক্টরকে দেখাব" was. clinic-api/department_match.py allowed only a short list of case endings after a
department word ("র", "ের", "কে", ...) and "তে" -- the locative, what Bengali says for "in" -- was not on it. It is
the commonest way to name a department.

Rule 4 of department_match.py reads a word with its ending taken off, its spelling folded, in a window of up to three
words, against the same keys as before. This file is the proof that it

  * reaches EVERY department and EVERY doctor, in all three scripts, with a long list of endings;
  * never changes an answer the old rules already gave (differential test, below);
  * does not turn a name, a lab test or an ordinary sentence into a department;
  * only ever SUGGESTS ("did you mean ...?") where the spelling is a near miss -- never opens a department.

    python -m pytest tests/test_department_endings.py -v

Set FULL_ENDING_MATRIX=1 to run every ending against every key (about 220,000 lookups, ~10 minutes); the default
runs a fixed stride of the list plus every ending that is named in this file.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLINIC_API = os.path.join(REPO_ROOT, "clinic-api")
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _load(name: str):
    key = f"_dm_ending_{name}"
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
NAMES = list(i18n.DEPARTMENT_ALIASES)

_BN = re.compile("[ঀ-৿]")
_DV = re.compile("[ऀ-ॿ]")
_LATIN = re.compile("^[A-Za-z0-9 '&.,-]+$")
FULL_MATRIX = os.environ.get("FULL_ENDING_MATRIX") == "1"


def old_match(query: str) -> list[str]:
    """department_match.match() as it was before rule 4 -- the exact / word / cut-off-word tiers, unchanged."""
    q = dm.strip_filler(query)
    if not q:
        return []
    exact = [d.name for d in DEPARTMENTS if q in d.keys()]
    if exact:
        return exact
    found = [d.name for d in DEPARTMENTS if any(dm.word_match(k, q) for k in d.keys())]
    if found:
        return found
    if len(q) >= 5:
        prefix = [d.name for d in DEPARTMENTS if any(k.startswith(q) for k in d.keys())]
        if len(prefix) == 1:
            return prefix
    return []


def new_match(query: str) -> list[str]:
    return dm.match(query, DEPARTMENTS)


def _forms(department: str) -> list[str]:
    return [department, *i18n.DEPARTMENT_NAMES_I18N[department], *i18n.DEPARTMENT_ALIASES[department]]


BN_KEYS = [(d, f) for d in NAMES for f in _forms(d) if _BN.search(f)]
DV_KEYS = [(d, f) for d in NAMES for f in _forms(d) if _DV.search(f)]
EN_KEYS = [(d, f) for d in NAMES for f in _forms(d) if _LATIN.match(f)]


def _raw_endings(number, case, emphasis) -> list[str]:
    return sorted({a + b + c for a in number for b in case for c in emphasis if a + b + c})


BN_ENDINGS = _raw_endings(dm._BN_NUMBER, dm._BN_CASE, dm._BN_EMPHASIS)
HI_ENDINGS = _raw_endings(dm._HI_NUMBER, dm._HI_POST, dm._HI_EMPHASIS)

# Endings a caller or the recogniser really produces, named so a later edit of the list cannot silently drop one.
CORE_BN = [
    "তে", "ে", "র", "ের", "এর", "কে", "য়", "য়ের", "দের", "টা", "টি", "গুলো", "গুলি", "রা", "েরা", "তেই", "তেও", "েও", "রও",
    "কেও", "গুলোকে", "গুলোর", "গুলোতে", "টায়", "টাতে", "টিতে", "দেরকে", "েরকে", "রই", "ই", "ও", "তো", "খানা", "গণ",
    "সমূহ", "থেকে", "জন্য", "সহ", "কাছে", "নিয়ে", "তেওই",
]
CORE_HI = ["में", "मे", "का", "की", "के", "को", "से", "पर", "ों", "ों में", "ों का", "यों", "ें", "ही", "भी", "में ही"]


# ================================================================== the list itself
def test_the_ending_list_is_long_and_has_every_named_ending():
    endings = set(dm._endings())
    assert len(endings) >= 2000
    missing = [e for e in [*CORE_BN, *CORE_HI] if " " not in e and dm._fold(e) not in endings]
    assert missing == []


def test_a_bare_vowel_sign_and_a_bare_ta_are_not_endings():
    """"কানা" (blind) must not be "কান" (ear), "নাকি" ("or") must not be "নাক" (nose), "সাধারণত" ("generally") must
    not be "সাধারণ" (the General Medicine word)."""
    endings = set(dm._endings())
    for bare in ("া", "ি", "ু", "ত", "ো"):
        assert dm._fold(bare) not in endings


# ================================================================== the live sentences
LIVE = [
    # Bengali: the locative, which was the miss
    ("আমি গাইনিতে একজন ডক্টরকে দেখাব", "Gynaecology & Obstetrics"),
    ("আমি গাইনির একজন ডক্টরকে দেখাব", "Gynaecology & Obstetrics"),  # worked before, must still
    ("গাইনিতে", "Gynaecology & Obstetrics"),
    ("গাইনীতে দেখাতে চাই", "Gynaecology & Obstetrics"),
    ("গাইনোকোলজিতে", "Gynaecology & Obstetrics"),
    ("প্রসূতিতে", "Gynaecology & Obstetrics"),
    ("আমি কার্ডিওলজিতে দেখাব", "Cardiology"),
    ("কার্ডিওলজিস্টকে দেখাতে চাই", "Cardiology"),
    ("হার্টে সমস্যা", "Cardiology"),
    ("হার্টেও দেখাব", "Cardiology"),
    ("অর্থোপেডিকে একজন ডাক্তার আছে", "Orthopaedics"),
    ("অর্থোপেডিক্সে যেতে চাই", "Orthopaedics"),
    ("হাড়ের ডাক্তারকে দেখাব", "Orthopaedics"),
    ("ডার্মাটোলজিতে কে কে বসেন", "Dermatology"),
    ("স্কিনে দেখাতে চাই", "Dermatology"),
    ("পেডিয়াট্রিক্সে একজন ডাক্তার", "Paediatrics"),
    ("পেডিয়াট্রিকসে", "Paediatrics"),
    ("শিশুরোগে", "Paediatrics"),
    ("ডায়াবেটোলজিতে ডাক্তার দেখাব, আছে?", "Diabetology & Endocrinology"),
    ("ডায়াবেটিসে দেখাব", "Diabetology & Endocrinology"),
    ("এন্ডোক্রিনোলজিতে", "Diabetology & Endocrinology"),
    ("জেনারেল মেডিসিনে দেখাব", "General Medicine"),
    ("মেডিসিনে একজন ডাক্তার", "General Medicine"),
    ("ইএনটিতে দেখাব", "ENT"),
    ("ইএনটির ডাক্তার", "ENT"),
    ("কানে দেখাতে চাই", "ENT"),
    # Hindi (Devanagari)
    ("मैं गायनी में दिखाना चाहता हूँ", "Gynaecology & Obstetrics"),
    ("गायनीमें दिखाना है", "Gynaecology & Obstetrics"),
    ("कार्डियोलॉजी में", "Cardiology"),
    ("कार्डियोलॉजिस्ट को दिखाना है", "Cardiology"),
    ("हड्डियों का डॉक्टर", "Orthopaedics"),
    ("ऑर्थोपेडिक में दिखाऊँगा", "Orthopaedics"),
    ("ईएनटी में", "ENT"),
    ("शुगर में दिखाना है", "Diabetology & Endocrinology"),
    ("डायबिटीज में दिखाना है", "Diabetology & Endocrinology"),
    ("जनरल मेडिसिन में", "General Medicine"),
    ("डर्मेटोलॉजी में", "Dermatology"),
    ("पीडियाट्रिक्स में", "Paediatrics"),
]


@pytest.mark.parametrize("said,department", LIVE)
def test_the_sentence_names_its_department(said, department):
    assert new_match(said) == [department]


def test_the_two_live_sentences_are_the_same_department():
    assert new_match("আমি গাইনিতে একজন ডক্টরকে দেখাব") == new_match("আমি গাইনির একজন ডক্টরকে দেখাব")


# ================================================================== every department, every key, every ending
def _stride(endings: list[str], core: list[str]) -> list[str]:
    picked = set(core) & set(endings) | {e for e in core if " " not in e}
    picked |= set(endings[:: 1 if FULL_MATRIX else 29])
    return sorted(picked)


def _join(key: str, ending: str) -> str:
    """The ending sits on the last word of the key (after the filler words that name nothing are dropped)."""
    stem = dm.strip_filler(key)
    return (stem + ending) if stem else ""


def test_every_bengali_department_key_with_every_ending_resolves_to_its_own_department():
    wrong, tried = [], 0
    for department, key in BN_KEYS:
        for e in _stride(BN_ENDINGS, CORE_BN):
            q = _join(key, e)
            if not q:
                continue
            tried += 1
            got = new_match(q)
            if got != [department]:
                wrong.append((department, key, e, got))
    assert tried > (150_000 if FULL_MATRIX else 1_500)
    assert wrong == [], f"{len(wrong)} of {tried}; first: {wrong[:5]}"


def test_every_devanagari_department_key_with_every_ending_resolves_to_its_own_department():
    wrong, tried = [], 0
    for department, key in DV_KEYS:
        for e in _stride(HI_ENDINGS, [c for c in CORE_HI if " " not in c]):
            q = _join(key, e)
            if not q:
                continue
            tried += 1
            got = new_match(q)
            if got != [department]:
                wrong.append((department, key, e, got))
    assert tried > (20_000 if FULL_MATRIX else 800)
    assert wrong == [], f"{len(wrong)} of {tried}; first: {wrong[:5]}"


@pytest.mark.parametrize("department", NAMES)
def test_a_department_is_reachable_in_all_three_scripts_with_the_locative(department):
    bn = i18n.DEPARTMENT_NAMES_I18N[department][0]
    hi = i18n.DEPARTMENT_NAMES_I18N[department][1]
    # Bengali name + locative, in a sentence; Hindi name + "में"; the Latin name as before.
    assert new_match(f"আমি {dm.strip_filler(bn)}তে একজন ডক্টরকে দেখাব") == [department]
    assert new_match(f"मुझे {dm.strip_filler(hi)} में दिखाना है") == [department]
    assert new_match(department.lower()) == [department]


FRAMES = [
    "{x}",
    "আমি {x} একজন ডক্টরকে দেখাব",
    "{x} ডাক্তার আছেন কি",
    "{x} বিভাগে অ্যাপয়েন্টমেন্ট চাই",
    "আমার {x} একটা অ্যাপয়েন্টমেন্ট দরকার",
    "কাল সকালে {x} দেখাতে চাই",
    "ওখানে {x} কারা কারা বসেন",
    "হ্যালো, {x} ডাক্তার আছে?",
]
HI_FRAMES = [
    "{x}",
    "मुझे {x} दिखाना है",
    "{x} में कौन कौन डॉक्टर हैं",
    "मैं {x} के डॉक्टर से मिलना चाहता हूँ",
    "कल सुबह {x} में अपॉइंटमेंट चाहिए",
]


def test_every_frame_every_key_with_the_locative_and_the_genitive():
    wrong = []
    for department, key in BN_KEYS:
        stem = dm.strip_filler(key)
        if not stem:
            continue
        for e in ("তে", "র", "ে", "কে", "ের", "গুলোতে", ""):
            for frame in FRAMES:
                said = frame.format(x=stem + e)
                if new_match(said) != [department]:
                    wrong.append((department, said, new_match(said)))
    for department, key in DV_KEYS:
        stem = dm.strip_filler(key)
        if not stem:
            continue
        for e in ("", "ों", "में"):
            for frame in HI_FRAMES:
                said = frame.format(x=stem + e)
                if new_match(said) != [department]:
                    wrong.append((department, said, new_match(said)))
    assert wrong == [], f"{len(wrong)}; first: {wrong[:5]}"


# ================================================================== spelling that sounds the same
def _variants(key: str) -> list[str]:
    swaps = [("ি", "ী"), ("ু", "ূ"), ("ন", "ণ"), ("স", "শ"), ("স", "ষ"), ("ি", "ী"), ("ु", "ू"), ("न", "ण"), ("स", "श")]
    out = []
    for a, b in swaps:
        if a in key:
            out.append(key.replace(a, b))
    if "্" in key:
        out.append(key.replace("্", ""))
    if "्" in key:
        out.append(key.replace("्", ""))
    if "ॉ" in key:
        out.append(key.replace("ॉ", "ो"))
    return out


def test_every_key_still_resolves_when_a_same_sounding_spelling_is_used():
    wrong, tried = [], 0
    for department, key in [*BN_KEYS, *DV_KEYS]:
        stem = dm.strip_filler(key)
        for v in _variants(stem):
            for e in ("", "তে" if _BN.search(stem) else "में"):
                tried += 1
                got = new_match(v + e)
                if got != [department]:
                    wrong.append((department, v + e, got))
    assert tried > 300
    assert wrong == [], f"{len(wrong)} of {tried}; first: {wrong[:5]}"


def test_no_folded_form_belongs_to_two_departments():
    """Folding merges spellings. If it ever merged two DEPARTMENTS' words, a caller naming one would be offered both
    (or, worse, the first)."""
    exact, bare = dm._index(DEPARTMENTS)
    clashes = {f: n for table in (exact, bare) for f, n in table.items() if len(n) > 1}
    assert clashes == {}


# ================================================================== things that are not departments
TRAPS = [
    "করতে",  # "to do" = কর (Dr. Kar) + তে
    "আমি অ্যাপয়েন্টমেন্ট করতে চাই",
    "বুক করতে চাই",
    "ক্যানসেল করতে হবে",
    "দেখতে চাই",
    "সাধারণত কখন খোলে",  # "generally" -- সাধারণ (General) + ত
    "নাকি",  # "or"
    "কানা",  # blind
    "কানাই",  # a name
    "কানপুর",
    "আমার মেয়ের জন্য",  # my daughter's -- not মেয়েদের, women's
    "আমার মেয়েকে দেখাব",
    "ছেলের জন্য অ্যাপয়েন্টমেন্ট",
    "আমার স্ত্রীর জন্য",
    "আমার বাবার জন্য",
    "দিল্লি",
    "दिल्ली",
    "दिलीप",
    "कानपुर",
    "कानून",
    "appointment",
    "department",
    "patient",
    "ডাক্তার",
    "ডাক্তারকে",
    "ডক্টরকে",
    "বিভাগে",
    "কোন ডাক্তার আছেন",
    "একজন ডাক্তার দেখাতে চাই",
]


@pytest.mark.parametrize("said", TRAPS)
def test_a_word_that_only_looks_like_a_department_plus_an_ending_is_not_one(said):
    assert new_match(said) == []


EVERYDAY = [
    "হ্যালো", "ধন্যবাদ", "আমি বাড়ি থেকে স্যাম্পল দিতে চাই", "আমার রিপোর্ট চাই", "ক্লিনিক কখন খোলে", "আপনাদের ঠিকানা কী",
    "আমার অ্যাপয়েন্টমেন্ট বাতিল করুন", "কাল সকালে দশটায়", "পরশু বিকেলে", "মঙ্গলবার তেরো তারিখে", "সাড়ে দশটা করে দিন",
    "আমার নাম রাজু", "আমার ফোন নম্বর নয় আট শূন্য", "রোগীর নাম রাজু", "আমার মায়ের জন্য", "হ্যাঁ ঠিক আছে", "না থাক",
    "একটু ধরুন", "আবার বলবেন", "আমি বুঝতে পারিনি", "কত টাকা লাগবে", "পার্কিং আছে কি", "ইনসুরেন্স চলে কি",
    "টেস্টের দাম কত", "সিবিসি টেস্ট", "লিপিড প্রোফাইল", "থাইরয়েড টেস্ট করাতে চাই", "ফাস্টিং লাগবে কি", "রিপোর্ট কবে পাব",
    "আমি এখন বেরোচ্ছি", "ঠিকানাটা আবার বলুন", "আপনাদের ক্লিনিক কোথায়", "রবিবার খোলা থাকে", "আজ কি ডাক্তার বসেছেন",
    "मेरा नाम राजू है", "मुझे रिपोर्ट चाहिए", "कल सुबह दस बजे", "धन्यवाद", "क्लिनिक कब खुलता है", "टेस्ट की कीमत क्या है",
    "मेरी माँ के लिए", "ठीक है", "फिर से बताइए", "पता क्या है", "बुकिंग करनी है", "रद्द करना है",
    "hello", "thank you", "what time do you open", "where is the clinic", "I want to cancel my appointment",
    "how much is a blood test", "my report", "yes please", "tomorrow morning at ten",
]


@pytest.mark.parametrize("said", EVERYDAY)
def test_everyday_sentences_are_not_departments_and_nothing_changed(said):
    assert new_match(said) == old_match(said)


# ================================================================== the old answers are the new answers
def _sentences_from_the_repos_own_tests() -> list[str]:
    out: set[str] = set()
    tests_dir = os.path.join(REPO_ROOT, "tests")
    for fn in sorted(os.listdir(tests_dir)):
        # This work's own tests are about the sentences that DO resolve now (and the whole-call matrix's scripts are
        # full of them); the rest of the repo is the corpus.
        if not fn.endswith(".py") or fn.startswith(
            ("test_department_endings", "test_bengali_flow", "_bn_flow", "bn_flow", "_shared_conversation")
        ):
            continue
        try:
            tree = ast.parse(open(os.path.join(tests_dir, fn), encoding="utf-8").read())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                v = node.value.strip()
                if 0 < len(v) < 300 and (_BN.search(v) or _DV.search(v)):
                    out.add(v)
    return sorted(out)


def test_no_answer_the_old_rules_gave_has_changed_anywhere_in_the_repos_own_sentences():
    corpus = _sentences_from_the_repos_own_tests()
    assert len(corpus) > 500
    changed = [(s, old_match(s), new_match(s)) for s in corpus if old_match(s) and old_match(s) != new_match(s)]
    assert changed == []


# Strings in this repo's tests that the old rules did not resolve and the new ones do. Each is a department word
# with an ending (or a body part with one); a new entry here is a decision, not an accident.
NEWLY_RESOLVED = {
    "ডায়াবেটোলজিতে ডাক্তার দেখাব, আছে",
    "ডায়াবেটোলজিতে ডাক্তার দেখাব, আছে?",
    "কানে ব্যথা",
    "চামড়ায় সমস্যা",
}


def test_the_only_sentences_in_the_repos_tests_that_start_to_resolve_are_the_expected_ones():
    corpus = _sentences_from_the_repos_own_tests()
    newly = {s for s in corpus if not old_match(s) and new_match(s)}
    unexpected = sorted(s for s in newly if s not in NEWLY_RESOLVED and len(s) < 120)
    assert unexpected == [], "review each: is it really a department name?"


def test_every_old_alias_and_every_old_frame_gives_the_same_answer_as_before():
    frames = ["{x}", "আমি {x} একজন ডক্টরকে দেখাব", "मुझे {x} दिखाना है", "{x} ডাক্তার আছেন কি", "I want {x}"]
    changed = []
    for department in NAMES:
        for f in _forms(department):
            for frame in frames:
                s = frame.format(x=f)
                if old_match(s) != new_match(s) and old_match(s):
                    changed.append((s, old_match(s), new_match(s)))
    assert changed == []


# ================================================================== a doctor's name is never a department
@pytest.fixture(scope="module")
def clinic():
    from _clinic_app import clinic_app

    with clinic_app(sample_patients=False) as (app, client):
        yield client


@pytest.fixture(scope="module")
def doctors(clinic):
    body = clinic.get("/api/v1/doctors").json()
    assert body["count"] == 32  # 8 departments x 4
    return body["doctors"]


def _name_forms(d: dict) -> list[str]:
    out = []
    for key in ("name", "full_name", "full_name_bn", "full_name_hi", "alias_bn", "alias_hi", "surname"):
        if d.get(key):
            out.append(d[key])
    return out


def test_no_doctor_name_in_any_script_or_ending_is_a_department(doctors):
    wrong = []
    for d in doctors:
        for form in _name_forms(d):
            for said in (
                form,
                f"ডাক্তার {form}",
                f"{form}কে দেখাব",
                f"{form}র কাছে অ্যাপয়েন্টমেন্ট",
                f"{form}ের কাছে",
                f"{form} তে",
                f"डॉक्टर {form} को दिखाना है",
                f"{form} से मिलना है",
                f"Dr {form}",
                f"appointment with {form}",
            ):
                got = new_match(said)
                if got:
                    wrong.append((said, got))
    assert wrong == [], wrong[:8]


def test_lab_test_names_resolve_exactly_as_they_did(clinic):
    catalogue = clinic.get("/api/v1/catalogue").json()["tests"]
    assert len(catalogue) == 34
    for t in catalogue:
        for form in [t["name"], *t.get("aliases_bn", []), *t.get("aliases_hi", [])]:
            for said in (form, f"{form} টেস্ট", f"{form} करवाना है", f"{form}-এর দাম"):
                assert new_match(said) == old_match(said), said


# ================================================================== "did you mean": a suggestion, never a resolution
SUGGESTED = [
    ("কার্ডিয়োলজি", "Cardiology"),
    ("কার্ডিওলোজি", "Cardiology"),
    ("অর্থপেডিক", "Orthopaedics"),
    ("অর্থোপেডিস", "Orthopaedics"),
    ("ডার্মাটোলোজি", "Dermatology"),
    ("পেডিয়াট্রিশিয়ান", "Paediatrics"),
    ("এন্ডোক্রাইনোলজি", "Diabetology & Endocrinology"),
    ("ডায়াবেটোলোজি", "Diabetology & Endocrinology"),
    ("গাইনোকোলোজি", "Gynaecology & Obstetrics"),
    ("ऑर्थोपीडिक", "Orthopaedics"),
]


@pytest.mark.parametrize("said,department", SUGGESTED)
def test_a_near_spelling_is_suggested_with_its_department(said, department):
    assert dm.suggest(said, DEPARTMENTS) == [department]


NOT_SUGGESTED = [
    "মেয়ের", "মেয়েকে", "আমার মেয়ের জন্য", "কানা", "নাকি", "সাধারণত", "দিল্লি", "বাবার", "ডাক্তারবাবু", "ক্লিনিক",
    "আমি অ্যাপয়েন্টমেন্ট করতে চাই", "অ্যাপয়েন্টমেন্ট", "ক্যালেন্ডার", "মেডিক্যাল", "ম্যানেজার", "ফার্মেসি", "হাসপাতাল",
    "কার্ডিওলজি কার্ডিওলজি আর ডার্মা", "কার্ডিয়োলজি আর হাড়", "", "   ",
]


@pytest.mark.parametrize("said", NOT_SUGGESTED)
def test_ordinary_words_and_whole_sentences_are_never_suggested(said):
    assert dm.suggest(said, DEPARTMENTS) == []


def test_no_doctor_or_test_name_is_ever_suggested_as_a_department(doctors, clinic):
    wrong = []
    for d in doctors:
        for form in _name_forms(d):
            for said in (form, f"{form}কে", f"{form}র"):
                if dm.suggest(said, DEPARTMENTS):
                    wrong.append((said, dm.suggest(said, DEPARTMENTS)))
    for t in clinic.get("/api/v1/catalogue").json()["tests"]:
        for form in [t["name"], *t.get("aliases_bn", []), *t.get("aliases_hi", [])]:
            if dm.suggest(form, DEPARTMENTS):
                wrong.append((form, dm.suggest(form, DEPARTMENTS)))
    assert wrong == [], wrong[:8]


def test_a_spelling_variant_resolves_like_the_spelling_the_table_already_has():
    """Hindi "शुगर" is stored bare, so "शुगर टेस्ट" has always been the diabetes department; "सुगर टेस्ट" is the same
    word spelt the other way and now says the same -- no more, no less."""
    assert old_match("शुगर टेस्ट") == ["Diabetology & Endocrinology"]
    assert new_match("सुगर टेस्ट") == old_match("शुगर टेस्ट")


def test_a_suggestion_is_made_only_when_nothing_resolved():
    """The endpoint calls suggest() only after match() found nothing; this pins that match() never returns a
    near-miss spelling as a resolution."""
    for said, _ in SUGGESTED:
        assert new_match(said) == [], said


# ================================================================== through the real clinic API
DEPARTMENT_PHRASES = {
    "Gynaecology & Obstetrics": ["গাইনিতে", "গাইনিতে একজন ডক্টর", "গাইনির ডাক্তার", "प्रसूति में", "गायनी में"],
    "Cardiology": ["কার্ডিওলজিতে", "হার্টে", "হার্টের ডাক্তার", "कार्डियोलॉजी में", "दिल का डॉक्टर"],
    "Orthopaedics": ["অর্থোপেডিকে", "হাড়ের ডাক্তারে", "ऑर्थोपेडिक में", "हड्डियों का डॉक्टर"],
    "ENT": ["ইএনটিতে", "কানের ডাক্তার", "নাক কান গলায়", "ईएनटी में"],
    "Dermatology": ["ডার্মাটোলজিতে", "স্কিনে", "চামড়ার ডাক্তার", "डर्मेटोलॉजी में"],
    "Paediatrics": ["পেডিয়াট্রিক্সে", "শিশুরোগে", "বাচ্চাদের ডাক্তার", "पीडियाट्रिक्स में"],
    "Diabetology & Endocrinology": ["ডায়াবেটোলজিতে", "ডায়াবেটিসে", "সুগারের ডাক্তার", "शुगर का डॉक्टर"],
    "General Medicine": ["জেনারেল মেডিসিনে", "মেডিসিনে", "মেডিসিনের ডাক্তার", "जनरल मेडिसिन में"],
}


def test_every_department_answers_with_every_one_of_its_doctors(clinic, doctors):
    """The whole chain a caller's words take at the clinic: the sentence -> the department -> ITS doctors, all four."""
    by_department: dict[str, set[str]] = {}
    for d in doctors:
        by_department.setdefault(d["department"], set()).add(d["name"])
    assert set(by_department) == set(DEPARTMENT_PHRASES)
    reached: set[str] = set()
    for department, phrases in DEPARTMENT_PHRASES.items():
        for said in phrases:
            for sentence in (said, f"আমি {said} একজন ডক্টরকে দেখাব"):
                r = clinic.get("/api/v1/doctors/by-department", params={"department": sentence}).json()
                assert r.get("found") is True, (sentence, r)
                assert r["department"] == department, (sentence, r["department"])
                names = {x["name"] for x in r["doctors"]}
                assert names == by_department[department], (sentence, names)
                assert r["count"] == 4
                reached |= names
    assert reached == {d["name"] for d in doctors}  # all 32


def test_every_doctor_is_reached_by_name_as_well(clinic, doctors):
    """And through their own names, which this change does not touch: each of the 32 by full Bengali name."""
    for d in doctors:
        r = clinic.get("/api/v1/doctors/mentioned", params={"text": f"{d['full_name_bn']}কে দেখাব"}).json()
        assert r["found"] is True and r["doctor_name"] == d["name"], (d["name"], r)


def test_the_endpoint_offers_a_near_spelling_in_the_callers_language_and_opens_nothing(clinic):
    r = clinic.get("/api/v1/doctors/by-department", params={"department": "কার্ডিয়োলজি"}).json()
    assert r["found"] is False
    assert r["did_you_mean"] == ["Cardiology"]
    assert r["needs_confirmation"] is True
    assert r["did_you_mean_bn"] == ["কার্ডিওলজি"]  # the Bengali voice cannot say the Latin name
    assert r["did_you_mean_hi"] == ["कार्डियोलॉजी"]
    assert "doctors" not in r  # nothing was opened


def test_the_endpoint_still_answers_a_name_it_cannot_read_with_nothing(clinic):
    for said in ("অমুক বিভাগ", "আমার মেয়ের জন্য", "করতে", "xyzzy"):
        r = clinic.get("/api/v1/doctors/by-department", params={"department": said}).json()
        assert r["found"] is False and not r.get("did_you_mean"), (said, r)


def test_the_endpoint_keeps_asking_when_two_departments_are_named(clinic):
    r = clinic.get("/api/v1/doctors/by-department", params={"department": "হার্টে আর হাড়ের ডাক্তারে"}).json()
    assert r["found"] is False and r.get("ambiguous") is True
    assert set(r["did_you_mean"]) == {"Cardiology", "Orthopaedics"}
