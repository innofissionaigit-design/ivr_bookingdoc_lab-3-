"""Which department did the caller name -- in Bengali, Hindi or English?

Pure (no database, no FastAPI) so it can be tested on its own and called from
every endpoint that resolves a department. It replaces a Latin-only substring
match that could not see "কার্ডিওলজি", "হার্টের ডাক্তার" or "दिल का डॉक्टर" at
all (see i18n_content.DEPARTMENT_ALIASES for the measured misses).

THREE RULES, EACH THERE BECAUSE THE OBVIOUS VERSION IS WRONG
------------------------------------------------------------
1. Words that name no department are removed from BOTH sides first. Every
   "<X> doctor" alias shares the word "doctor"; compared as written, "doctor"
   alone would match all eight departments. So would "বিভাগ", "का", "chai".

2. Latin keys match as WHOLE WORDS. The department "ENT" is the letters e-n-t,
   which sit inside "dEpartmENT", "patiENT", "appointmENT". A plain substring
   test sends a caller who said "appointment" to the ear-nose-throat clinic.

3. Bengali and Devanagari keys must START a word and may be followed only by a
   case ending. Bengali attaches the genitive to the noun -- "হার্ট" (heart) is
   said "হার্টের ডাক্তার" -- so a whole-word test misses the commonest phrasing.
   But an open-ended prefix is wrong too: "दिल" (heart) is the start of "दिल्ली"
   (Delhi). Allowing exactly the case endings and nothing else gets both right.

4. A word is read with its case ending taken off, and with its spelling folded,
   in a short window of neighbouring words. "গাইনিতে" (in gynae) is "গাইনি" + the
   locative "তে"; rule 3's short list never had "তে" and the caller who said it was
   sent to the counter (live 2026-10-12). The list in rule 3 is for NAMES, and it is
   shared with the doctor matcher, where a long list would be wrong -- "করতে" ("to
   do") is "কর" (Dr. Kar) + "তে". Departments get their own, long list below
   (_BN_NUMBER ... _HI_EMPHASIS), and a word that matches nothing exactly is only
   ever SUGGESTED ("did you mean ...?"), never acted on.

Resolution keeps the clinic's no-guessing discipline (CLAUDE.md, "Doctor
Nobody"): an exact key wins outright; otherwise every department that matched
is returned, and the caller is asked to choose when there is more than one.
"""

from __future__ import annotations

import difflib
import functools
import re
import unicodedata
from dataclasses import dataclass, field
from itertools import product

_INDIC = "ঀ-৿ऀ-ॿ"  # Bengali and Devanagari blocks, marks included

# Rule 1. Mirrors agent/doctor_request.py's filler for the doctor words; it is a
# copy rather than an import because clinic-api never imports from agent/ (it
# stands in for a separate service -- see clinic-api/main.py).
_FILLER = frozenset(
    unicodedata.normalize("NFC", w).lower()
    for w in (
        "doctor", "doctors", "dr", "doc", "specialist", "department", "dept", "clinic",
        "the", "a", "an", "in", "of", "for", "please", "want", "need", "see", "which",
        "chai", "chahiye", "er", "ka", "ki", "ke", "kon", "wala", "wali",
        "ডাক্তার", "ডাক্তারের", "ডাক্তারবাবু", "ডাক্তাররা", "ডাক্তারদের", "ডক্টর", "ডঃ", "ডাঃ",
        "বিভাগ", "বিভাগের", "বিভাগে", "বিভাগটা", "ডিপার্টমেন্ট", "স্পেশালিস্ট", "বিশেষজ্ঞ",
        "কোন", "কোনো", "কে", "কারা", "আছেন", "আছে", "বসেন", "চাই", "দেখাব", "দেখাবো",
        "দেখাতে", "দরকার", "লাগবে", "আমার", "আমি", "একজন",
        "डॉक्टर", "डाक्टर", "डॉ", "विभाग", "स्पेशलिस्ट", "विशेषज्ञ", "का", "की", "के",
        "कौन", "कोई", "चाहिए", "दिखाना", "मुझे", "है", "एक",
    )
)

# Rule 3. What may follow a Bengali/Devanagari key inside the same word.
# Hindi writes its postpositions as separate words (का, की, के), so it needs only
# the long-a sign below; the rest are Bengali case endings.
#
# The final long-a sign (া / ा) is a SPELLING variant, not a different word: the
# Hindi alias for Dr. Dutta is "दत्ता" and callers' ASR writes "दत्त" as often. It is
# allowed on its own and nothing longer is -- "পালিত" (Palit) after "পাল" (Pal) is
# a vowel sign PLUS a consonant, which is a different surname and must not match.
_BN_ENDINGS = ("ের", "এর", "দের", "র", "কে", "টা", "টি", "গুলো", "ে", "া", "ा")
_RIGHT = r"(?=$|[^" + _INDIC + r"]|(?:" + "|".join(_BN_ENDINGS) + r")(?:$|[^" + _INDIC + r"]))"

_SPLIT = re.compile(r"[\s,.!?।॥;:'\"()\-–—/&]+")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").lower().strip()


def strip_filler(text: str) -> str:
    """`text` without the words that name no department, single-spaced."""
    return " ".join(w for w in _SPLIT.split(_norm(text)) if w and w not in _FILLER)


def _is_latin(key: str) -> bool:
    return all(ord(c) < 128 for c in key)


def word_match(key: str, query: str) -> bool:
    """Is `key` in `query` as a WORD -- not as letters inside a longer word? Rules 2 and 3 above.

    Shared with the doctor-name matcher (clinic-api/main.py _doctor_exact_matches), which had the
    substring bug this module was written to avoid: the alias "পাল" (Pal) is inside "পালিত" (Palit,
    a different surname), so a caller asking for Dr. Palit was confidently offered Dr. Pal's real
    schedule -- the "Doctor Nobody" failure CLAUDE.md section 1 names. A case ending is still a
    match ("পালের" is Pal's), a longer word is not."""
    if _is_latin(key):
        # Rule 2: whole words, with a plural allowed ("cardiologists").
        return re.search(r"\b" + re.escape(key) + r"(?:s|es)?\b", query) is not None
    # Rule 3: starts a word, ends the word or a case ending follows.
    return re.search(r"(?<![" + _INDIC + r"])" + re.escape(key) + _RIGHT, query) is not None


@dataclass
class DepartmentKeys:
    """One department and every written form a caller might use for it."""

    name: str
    forms: list[str] = field(default_factory=list)

    def keys(self) -> list[str]:
        out = []
        for form in [self.name, *self.forms]:
            k = strip_filler(form)
            if k and k not in out:
                out.append(k)
        return out


def match(query: str, departments: list[DepartmentKeys]) -> list[str]:
    """The department names `query` refers to: [] none, [one] resolved,
    [several] ambiguous -- the caller must be asked which."""
    q = strip_filler(query)
    if not q:
        return []
    exact = [d.name for d in departments if q in d.keys()]
    if exact:
        return exact
    found = [d.name for d in departments if any(word_match(k, q) for k in d.keys())]
    if found:
        return found
    # Rule 4: a case ending the short list above does not have, a spelling slip that sounds the same.
    # Reached only when nothing above found anything, so it can turn a "nothing" into an answer and cannot
    # change one that already exists.
    found = _match_by_stem(q, departments)
    if found:
        return found
    # A cut-off word from the recogniser ("অর্থোপেডি" for "অর্থোপেডিক"): accepted
    # only when it is long enough to mean something and fits ONE department, so a
    # short fragment can never resolve on its own.
    if len(q) >= 5:
        prefix = [d.name for d in departments if any(k.startswith(q) for k in d.keys())]
        if len(prefix) == 1:
            return prefix
    return []


# ============================================================================================ rule 4: stems
# Everything below is for DEPARTMENT names only. word_match() above is shared with the doctor-name matcher, and a
# name must not be read with this long a list of endings: "করতে" ("to do") would be Dr. Kar.

_MIN_STEM = 3  # letters left after an ending is taken off (a bare "মা", "কে" is never a stem)
_MAX_PASSES = 3  # stacked endings: "গুলো" + "কে" + "ও"
_WINDOW = 3  # words read together: "জেনারেল মেডিসিনে", "নাক কান গলার"
_FUZZY_AT = 0.80  # a suggestion needs this much of the word to agree ...
_FUZZY_MARGIN = 0.10  # ... and to lead the next department by this much
_FUZZY_MIN_LEN = 6  # letters, folded. Below this one edit is already 20%: "মেয়ের" (daughter's) is "মেয়েদের" (women's)
                    # minus a syllable, and "কানা" (blind) is "কান" (ear) plus a vowel

# Spelling that is the same word when spoken. Applied to BOTH the caller's words and the keys, so two spellings
# of one word meet and nothing is ever rewritten for the caller to hear. The hasanta/virama is dropped as well,
# so "পেডিয়াট্রিক্স" and "পেডিয়াট্রিকস" are one word; the nukta, so "ড়" and "য়" lose their dot.
_FOLD = str.maketrans(
    {
        "\u09c0": "\u09bf",  # ী -> ি
        "\u0988": "\u0987",  # ঈ -> ই
        "\u09c2": "\u09c1",  # ূ -> ু
        "\u098a": "\u0989",  # ঊ -> উ
        "\u09a3": "\u09a8",  # ণ -> ন
        "\u09b6": "\u09b8",  # শ -> স
        "\u09b7": "\u09b8",  # ষ -> স
        "\u09ce": "\u09a4",  # ৎ -> ত
        "\u0981": None,  # chandrabindu
        "\u09bc": None,  # nukta
        "\u09cd": None,  # hasanta
        "\u0940": "\u093f",  # ी -> ि
        "\u0908": "\u0907",  # ई -> इ
        "\u0942": "\u0941",  # ू -> ु
        "\u090a": "\u0909",  # ऊ -> उ
        "\u0923": "\u0928",  # ण -> न
        "\u0936": "\u0938",  # श -> स
        "\u0937": "\u0938",  # ष -> स
        "\u0901": None,  # chandrabindu
        "\u0902": None,  # anusvara
        "\u093c": None,  # nukta
        "\u094d": None,  # virama
        "\u0949": "\u094b",  # ॉ -> ो
        "\u0945": "\u0947",  # ॅ -> े
        "\u0911": "\u0913",  # ऑ -> ओ
        "\u090d": "\u090f",  # ऍ -> ए
        "\u200c": None,  # zero-width (non-)joiners the recogniser and keyboards add
        "\u200d": None,
    }
)

# What follows a Bengali noun: [number / "the"] + [case] + [emphasis], each part optional. "গাইনিতে" = গাইনি + তে,
# "মেডিসিনগুলোকেও" = মেডিসিন + গুলো + কে + ও. Source: the case, plural and definite markers of Bengali grammar
# (Wikipedia "Bengali grammar"; MSU Basic Bangla ch. 7.4; the marker inventory of BanLemma, arXiv 2311.03078).
# REASONED, not measured on live calls: add what real transcripts show.
_BN_NUMBER = (
    "", "টা", "টি", "টো", "টুকু", "খানা", "খানি", "গুলো", "গুলি", "গুলা", "রা", "েরা", "গণ", "বৃন্দ", "সমূহ", "দের",
    "েদের", "দিগ",
)
_BN_CASE = (
    "", "র", "ের", "এর", "য়ের", "কে", "রে", "েরে", "েরকে", "ে", "এ", "য়", "তে", "য়ে", "েতে", "ায়", "থেকে", "হতে",
    "দিয়ে", "সহ", "মধ্যে", "জন্য", "নিয়ে", "কাছে", "পাশে", "ভিতরে", "ভেতরে", "সামনে",
)
_BN_EMPHASIS = ("", "ই", "ও", "তো", "বা", "ওই")
# Deliberately NOT here: a bare "ত" (the colloquial locative, "বাসাত") -- it makes "সাধারণত" ("generally") the
# department "সাধারণ" (live-corpus check, 2026-10-12) -- and a bare "া"/"ি", which turn "কানা" (blind) into "কান"
# (ear) and "নাকি" ("or") into "নাক" (nose).

# Devanagari. Hindi writes its postpositions as separate words; these are for the times the recogniser glues them
# on ("गायनीमें") and for the oblique plural ("हड्डियों").
_HI_NUMBER = ("", "ों", "ें", "ाओं", "ाएँ", "ाएं", "यों", "ियों", "यां", "याँ", "ियां", "ियाँ")
_HI_POST = ("", "में", "मे", "का", "की", "के", "को", "से", "पर", "ने", "तक", "जी", "वाला", "वाली", "वाले")
_HI_EMPHASIS = ("", "ही", "भी")

# A name may keep ONE genitive ending when it is a key ("হার্টের ডাক্তার" is stored as "হার্টের"), so the bare word
# means the same department. Not the plural genitive: "মেয়েদের" (women's) must not make "মেয়ে" (daughter) a
# gynaecology word.
_GENITIVE = ("র", "ের", "এর", "য়ের")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").lower().translate(_FOLD)


# The words that join two names inside one department's name.
_JOINERS = frozenset(_fold(w) for w in ("ও", "এবং", "আর", "एवं", "और", "व", "and", "&"))


def _has_indic(text: str) -> bool:
    return re.search("[" + _INDIC + "]", text) is not None


# Words a department is never spelt with: the particles that join a department to the rest of the sentence.
# ("ও" is NOT here -- "গাইনি ও প্রসূতি" is one department's name.)
_PARTICLES = (
    "में", "मे", "से", "पर", "को", "ने", "तक", "भी", "ही",
    "জন্য", "জন্যে", "থেকে", "সাথে", "নিয়ে", "কাছে", "মধ্যে", "সামনে",
)
_FILLER_FOLDED = frozenset(_fold(w) for w in (*_FILLER, *_PARTICLES))


def _content_words(q: str) -> list[str]:
    """The folded words of `q` that name no department, including the ones a case ending hides: "ডক্টরকে" is
    "ডক্টর" (a filler word) and the same ending, which strip_filler() above does not see."""
    out: list[str] = []
    for w in q.split():
        f = _fold(w)
        if f and not any(s in _FILLER_FOLDED for s in _stems(f)):
            out.append(f)
    return out


@functools.lru_cache(maxsize=1)
def _endings() -> tuple[str, ...]:
    out: set[str] = set()
    for parts in (
        product(_BN_NUMBER, _BN_CASE, _BN_EMPHASIS),
        product(_HI_NUMBER, _HI_POST, _HI_EMPHASIS),
    ):
        for combo in parts:
            e = _fold("".join(combo))
            if e:
                out.add(e)
    return tuple(sorted(out, key=lambda e: (-len(e), e)))


@functools.lru_cache(maxsize=8192)
def _stems(word: str) -> frozenset[str]:
    """`word` and every shorter form reached by taking endings off it, up to _MAX_PASSES at a time. The word
    itself is always in the set -- it is tried first, as spoken -- and nothing shorter than _MIN_STEM is."""
    seen = {word}
    frontier = [word]
    for _ in range(_MAX_PASSES):
        step: list[str] = []
        for w in frontier:
            for e in _endings():
                if len(w) - len(e) >= _MIN_STEM and w.endswith(e):
                    s = w[: -len(e)]
                    if s not in seen:
                        seen.add(s)
                        step.append(s)
        frontier = step
        if not frontier:
            break
    return frozenset(seen)


def _roots(word: str) -> list[str]:
    """The forms of `word` with every ending that can come off taken off -- not the word as spoken. What a near
    miss is judged on: "মেয়েকে" is "মেয়ে" + "কে", which is four letters and no department, and not a six-letter
    word one edit from "মেয়েদের"."""
    return [f for f in _stems(word) if not any(len(f) - len(e) >= _MIN_STEM and f.endswith(e) for e in _endings())]


def _key_forms(key: str) -> tuple[set[str], set[str]]:
    """The written forms one department key is recognised by, with and without the spaces ("স্ত্রী রোগ" is also
    said "স্ত্রীরোগ"): (as stored, the same without its genitive). The second set is the bare noun -- "কান" from
    "কানের ডাক্তার" -- and is matched only by a word that CAME WITH an ending ("কানে", "কানকে"): the bare word on
    its own is not the department. "সুগার টেস্ট" is a blood test, not the sugar clinic."""
    tokens = [t for t in (_fold(w) for w in _SPLIT.split(key)) if t]
    if not tokens:
        return set(), set()
    # A department whose NAME is two names ("ডায়াবেটোলজি ও এন্ডোক্রিনোলজি", "डायबेटोलॉजी एवं एंडोक्रिनोलॉजी") is also
    # each of them: the Hindi voice says the long name, and the caller repeats the half they caught.
    parts = [tokens]
    if any(t in _JOINERS for t in tokens):
        parts = [[]]
        for t in tokens:
            if t in _JOINERS:
                parts.append([])
            else:
                parts[-1].append(t)
        parts = [tokens] + [pt for pt in parts if pt and sum(len(w) for w in pt) >= _MIN_STEM]
    exact: set[str] = set()
    bare: set[str] = set()
    for pt in parts:
        exact |= {" ".join(pt), "".join(pt)}
        for g in _GENITIVE:
            g = _fold(g)
            if pt[-1].endswith(g) and len(pt[-1]) - len(g) >= _MIN_STEM:
                last = pt[-1][: -len(g)]
                bare |= {" ".join([*pt[:-1], last]), "".join([*pt[:-1], last])}
    return exact, bare - exact


@functools.lru_cache(maxsize=16)
def _index_of(table: tuple) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    exact: dict[str, list[str]] = {}
    bare: dict[str, list[str]] = {}
    for name, keys in table:
        for key in keys:
            if not _has_indic(key):
                continue  # Latin keys are read by word_match (rule 2), plurals and all
            as_stored, genitive_off = _key_forms(key)
            for target, forms in ((exact, as_stored), (bare, genitive_off)):
                for form in forms:
                    names = target.setdefault(form, [])
                    if name not in names:
                        names.append(name)
    return (
        {f: tuple(n) for f, n in exact.items()},
        {f: tuple(n) for f, n in bare.items()},
    )


def _index(departments: list["DepartmentKeys"]):
    """(forms as stored, bare nouns) -> the departments each names. Built once per distinct table (the endpoint
    rebuilds `departments` on every request; the table behind it changes only with a seed)."""
    return _index_of(tuple((d.name, tuple(d.keys())) for d in departments))


def _match_by_stem(q: str, departments: list["DepartmentKeys"]) -> list[str]:
    """Rule 4. `q` is already filler-free. Every run of up to _WINDOW neighbouring words is looked up, as spoken
    first and then with endings taken off its last word, against the folded keys. Exact equality only."""
    tokens = _content_words(q)
    if not any(_has_indic(t) for t in tokens):
        return []
    exact, bare = _index(departments)
    hits: list[str] = []
    for i in range(len(tokens)):
        for j in range(i + 1, min(i + _WINDOW, len(tokens)) + 1):
            head = tokens[i : j - 1]
            spoken = tokens[j - 1]
            if not any(_has_indic(t) for t in tokens[i:j]):
                continue
            for last in _stems(spoken):
                # The bare noun is for a word that had an ending to lose; the word as spoken is not one.
                tables = (exact,) if last == spoken else (exact, bare)
                for form in (" ".join([*head, last]), "".join([*head, last])):
                    for table in tables:
                        for name in table.get(form, ()):
                            if name not in hits:
                                hits.append(name)
    return hits


def suggest(query: str, departments: list["DepartmentKeys"]) -> list[str]:
    """Departments `query` is CLOSE to but does not name: a spelling the exact rules above cannot see
    ("কার্ডিয়োলজি", "অর্থপেডিক"). [] or ONE name. This is a suggestion, to be asked about -- "did you mean
    ...?" -- and never a resolution; a department is only opened for a caller who said it, or said yes.

    Edit-similarity (difflib's ratio) of each short Indic word, endings off, to every single-word Indic key. A
    department needs _FUZZY_AT and must lead the next by _FUZZY_MARGIN."""
    words = _content_words(strip_filler(query))
    # ONE word left: the caller said a department and nothing else that is not a filler. A sentence with other
    # words in it is never read for a near miss -- "আমার মেয়ের জন্য" is about a daughter, not women's medicine.
    if len(words) != 1 or not _has_indic(words[0]):
        return []
    singles: dict[str, set[str]] = {}
    for d in departments:
        for key in d.keys():
            if not _has_indic(key) or " " in key.strip():
                continue
            for forms in _key_forms(key):
                for form in forms:
                    if " " not in form and len(form) >= _FUZZY_MIN_LEN:
                        singles.setdefault(form, set()).add(d.name)
    best: dict[str, float] = {}
    for form in _roots(words[0]):
        if len(form) < _FUZZY_MIN_LEN:
            continue
        for key, names in singles.items():
            if form.startswith(key) or key.startswith(form):
                # A longer word built on a department word is another word ("সাধারণত", "generally", on "সাধারণ"),
                # and a shorter one is the cut-off rule's business -- neither is a misspelling.
                continue
            sm = difflib.SequenceMatcher(None, form, key)
            if sm.real_quick_ratio() < _FUZZY_AT or sm.quick_ratio() < _FUZZY_AT:
                continue
            r = sm.ratio()
            for name in names:
                if r > best.get(name, 0.0):
                    best[name] = r
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    if not ranked or ranked[0][1] < _FUZZY_AT:
        return []
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < _FUZZY_MARGIN:
        return []
    return [ranked[0][0]]
