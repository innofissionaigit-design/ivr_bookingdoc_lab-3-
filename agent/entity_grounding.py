"""Which tests did the caller ACTUALLY name? -- the transcript, not the model, decides.

THE RULE THIS FILE EXISTS FOR
-----------------------------
An entity may not be used because it exists in the catalogue. It must ALSO be supported by the caller's own
words. CLAUDE.md section 1 draws the line at facts; this draws it one step earlier, at which entity a fact is
retrieved FOR -- because a real price for a test nobody asked about is a wrong answer made of true numbers.

Live call, 2026-10-08. ASR lost the names and produced "আমি ওই কে দুটো টেস্ট করবো ... কত টাকা পরবে টোটাল" --
two tests, a total, no name anywhere in it. The intent model filled test_names with ["CBC", "Thyroid Profile"],
the catalogue priced both, and the caller was quoted 1100 rupees for two tests they never said. They had asked
for CBC and ECG, which is 700. Every figure in that reply was genuine; the entities were invented.

    transcript : "আমি ওই কে দুটো টেস্ট করবো কত টাকা পড়বে"
    model      : test_names = ["CBC", "Thyroid Profile"]
    here       : CBC -> not in the transcript, Thyroid Profile -> not in the transcript
    result     : names=(), count=2  ->  the agent ASKS which two, and prices nothing

WHY DETERMINISTIC, NOT THE MODEL
--------------------------------
"Which test?" is a bad question for semantic similarity: HANDOVER.md measures cosine ~0.749 between the SAME
sentence frame carrying DIFFERENT tests ("cbc price koto" / "ecg price koto"), while character-level comparison
separates them cleanly. So the catalogue's own alias table decides, through the gazetteer the fast path already
uses at the same commit floor -- and the model's list is only ever FILTERED by it, never trusted.

Pure: no I/O, no clock, nothing imported from main.py (CLAUDE.md's one-import rule). The caller passes in the
catalogue it already holds.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass

from agent.fast_path import COMMIT_FLOOR
from agent.fast_path import _normalize as normalize_text
from agent.language_policy import foreign_script_language

# A turn naming more than this is a mis-parse, not a caller: stop walking rather than price a wall of tests.
# Six, not four: a caller really does ask for a health-check set (CBC, lipid, liver, kidney, thyroid) in one breath,
# and a cap of four cut the fifth WITHOUT SAYING SO -- they were read back four tests and quoted a total that left
# one out (a whole-call matrix, 2026-10-12). More than the cap is never silently cut either: see extract_tests.
MAX_TESTS_PER_TURN = 6

# Words that join two test names. Split on these FIRST, so "সিবিসি আর ইসিজি" is scored as two separate names
# instead of one window that matches neither well. A word that is not a joiner simply never splits anything.
_CONJUNCTIONS = frozenset(
    {
        "আর", "আরও", "আরো", "এবং", "ও", "সাথে", "সঙ্গে",
        "और", "तथा", "एवं", "व", "साथ",
        "and", "plus", "with", "&",
    }
)

# The word for "test" in each language -- a count only counts when it is talking about tests.
_TEST_WORD = frozenset(
    {
        "টেস্ট", "টেস্টের", "টেস্টগুলো", "টেস্টগুলি", "টেষ্ট", "পরীক্ষা", "পরীক্ষার",
        "टेस्ट", "टेस्टों", "जांच", "जाँच", "परीक्षण",
        "test", "tests",
        "taste", "tastes", "texts",  # the English recogniser's usual mishearings of "test(s)"
    }
)

# "two tests" in the three languages, including the Bengali and Hindi counter forms (bn_normalize.py speaks the
# same words in the other direction). Only 1-5: a caller naming more than five tests in one breath is not real.
_COUNT_WORDS = {
    "এক": 1, "একটা": 1, "একটি": 1, "একটে": 1,
    "দুই": 2, "দুটো": 2, "দুটি": 2, "দুইটা": 2, "দুটা": 2, "দুটোই": 2,
    "তিন": 3, "তিনটে": 3, "তিনটি": 3, "তিনটা": 3,
    "চার": 4, "চারটে": 4, "চারটি": 4, "চারটা": 4,
    "পাঁচ": 5, "পাচ": 5, "পাঁচটা": 5, "পাঁচটি": 5,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
}
_COUNT_WORDS = {unicodedata.normalize("NFC", k): v for k, v in _COUNT_WORDS.items()}
_CONJUNCTIONS = frozenset(unicodedata.normalize("NFC", w) for w in _CONJUNCTIONS)
_TEST_WORD = frozenset(unicodedata.normalize("NFC", w) for w in _TEST_WORD)

# How far a count word may sit from the word for "test" and still be counting tests: "দুটো টেস্ট", "দুটো রক্তের
# টেস্ট", "two blood tests". Beyond that it is some other number in the sentence (a time, a price, an age).
_COUNT_WINDOW = 3

_RE_WS = re.compile(r"\s+")


# Switches back to the earlier behaviour without a code change (an environment variable on the pod).
#   TEST_WALK_FIX=off    take a hit's words out of the sentence by the form's spelling again (a second test whose first
#                        neighbour was matched by a near spelling is then lost -- live 2026-10-16, "ক্রিয়েটিনিন ইউরিক্যাসিড")
#   TEST_CHOICE_ASK=off  pick the best of two near-tied tests silently again (live 2026-10-16: TSH 0.83 over PSA 0.80)
TEST_WALK_FIX = os.environ.get("TEST_WALK_FIX", "on").strip().lower() != "off"
TEST_CHOICE_ASK = os.environ.get("TEST_CHOICE_ASK", "on").strip().lower() != "off"
# Two tests are "close" for the same words when the second scores within this of the first (and reaches the commit floor).
TEST_CHOICE_MARGIN = float(os.environ.get("TEST_CHOICE_MARGIN", "0.1"))
_MAX_CANDIDATES = 3
# Two near-tied names that begin with the same word ("ভিটামিন ডি" / "ভিটামিন বি১২") are told apart by what FOLLOWS that word: when
# the words said match one tail this much better than the other, that test is clear and nothing is asked.
TEST_TAIL_MARGIN = float(os.environ.get("TEST_TAIL_MARGIN", "0.15"))
# Two near-tied names are told apart by LETTERS when one is this many edits closer ("এইচবিএসজি": HBsAg is one letter away, HbA1c
# three) -- the character score alone calls them a tie.
TEST_EDIT_LEAD = int(os.environ.get("TEST_EDIT_LEAD", "2"))
# Acronyms said back to back come out as one word; TEST_GLUE_SPLIT=off leaves such a word whole (the earlier behaviour).
TEST_GLUE_SPLIT = os.environ.get("TEST_GLUE_SPLIT", "on").strip().lower() != "off"


@dataclass(frozen=True)
class GroundedTests:
    """What the caller's own words support.

    names   -- canonical catalogue names, in the order the caller said them. The ONLY thing safe to price.
    forms   -- the alias each name was recognised by, i.e. the caller's OWN words for it: what the read-back
               says back, so they hear their vocabulary and not the Latin catalogue key the voices cannot speak.
    dropped -- names the model offered that the transcript does not support. Logged, never used.
    count   -- how many tests the caller said they want, when they said a number ("দুটো টেস্ট") -- which is a
               different fact from WHICH tests, and is what lets the agent ask "which two?" instead of guessing.
    exact   -- every grounded name matched a full alias, not a near spelling.
    choices -- one tuple of candidate names for each stretch of words that fit two or three tests about equally well
               ("টিএসএ": TSH 0.83, PSA 0.80). Not in `names`: they are asked about, never picked (main.py `_confirm_tests`).
    """

    names: tuple[str, ...] = ()
    forms: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    count: int | None = None
    exact: bool = True
    choices: tuple[tuple[str, ...], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.names or self.choices)


def _tokens(text: str) -> list[str]:
    return [t for t in _RE_WS.split(normalize_text(text)) if t]


def _segments(text: str) -> list[str]:
    """The sentence split on joining words, in the order said."""
    out: list[str] = []
    current: list[str] = []
    for token in _tokens(text):
        if token in _CONJUNCTIONS:
            if current:
                out.append(" ".join(current))
            current = []
            continue
        current.append(token)
    if current:
        out.append(" ".join(current))
    return out


def _without(text: str, form: str | None) -> str:
    """`text` with the words a match just used taken out, so the next match has to come from DIFFERENT words.

    Without this, re-scanning the same sentence for a second test keeps scoring the words already spoken for:
    "থাইরয়েড টেস্টের দাম কত" produced Thyroid Profile and then, from the leftover "টেস্টের", Widal Test -- a
    second test the caller never said. Prefix-compared because Bengali inflects the word itself ("টেস্টের" is
    "টেস্ট" with a suffix), which an equality test would miss.

    ONLY the words that match used are taken out -- the run of them, once. Taking out every word equal to a word
    of the name (the first version) made the names of a list swallow each other: "ইউরিন টেস্ট ইকো টেস্ট পিএসএ"
    lost BOTH "টেস্ট" when the first name matched, "ইকো" alone then matched nothing, and the caller was read back
    two tests of the three; "ডেঙ্গু এনএস১ ডেঙ্গু আইজিজি" lost both "ডেঙ্গু", and the leftover "আইজিজি" was scored
    as ECG -- a price for a test the caller never named (a whole-call matrix, 2026-10-12)."""
    form_tokens = _tokens(form or "")
    if not form_tokens:
        return ""
    tokens = _tokens(text)

    def same(t: str, f: str) -> bool:
        return t.startswith(f) or f.startswith(t)

    n = len(form_tokens)
    for i in range(len(tokens) - n + 1):
        if all(same(tokens[i + k], form_tokens[k]) for k in range(n)):
            return " ".join(tokens[:i] + tokens[i + n :])
    # The name's words are not a run in the sentence (the matcher read them out of order or across a filler word):
    # take out ONE occurrence of each, the first.
    kept = list(tokens)
    for f in form_tokens:
        for idx, t in enumerate(kept):
            if same(t, f):
                del kept[idx]
                break
    return " ".join(kept)


def _without_window(text: str, window: str | None) -> str:
    """`text` with the WORDS the match covered taken out (the run of them, once) -- the words the caller actually said.

    Used ONLY when `_without` could not find the form's spelling in the sentence (it returned the text unchanged), which
    is whenever the recogniser spells the word its own way ("ক্রিয়েটিনিন" said, "ক্রিয়াটিনিন" in the catalogue). Before this
    fallback that ended the walk with the rest of the sentence unread. It is a fallback and not the rule because a matched
    window may be a word wider than the name ("ডেঙ্গু এনএস১ ডেঙ্গু"), and cutting that out of a list of names made the names
    swallow each other (see `_without`): wherever `_without` worked, it is still what is used."""
    wtoks = _tokens(window or "")
    toks = _tokens(text)
    n = len(wtoks)
    if n:
        for i in range(len(toks) - n + 1):
            if toks[i : i + n] == wtoks:
                return " ".join(toks[:i] + toks[i + n :])
    return text


def spoken_count(text: str, lang: str = "bn") -> int | None:
    """How many tests the caller said they want, or None. "দুটো টেস্ট" -> 2; "দুটো সময়" -> None (not about tests).

    `lang` is accepted for symmetry with the rest of the agent's spoken-* helpers; the table is shared, because a
    Bengali caller says "two" in English as readily as "দুটো"."""
    tokens = _tokens(text)
    for i, token in enumerate(tokens):
        if token not in _TEST_WORD:
            continue
        for back in range(1, _COUNT_WINDOW + 1):
            j = i - back
            if j < 0:
                break
            if tokens[j] in _COUNT_WORDS:
                return _COUNT_WORDS[tokens[j]]
    return None


def _tail_winner(rows: list[tuple[str, str, float, str]]) -> tuple[str, str, float, str] | None:
    """Of near-tied candidates (name, form, score, window), the one whose NAME-SPECIFIC part the words match clearly better,
    or None when the words do not tell them apart.

    Names that share their first word ("ভিটামিন ডি" / "ভিটামিন বি১২", "হেপাটাইটিস বি" / "হেপাটাইটিস সি") score close to each other
    on that word alone: "ভিটামিন বি টুয়েলভ" is 0.91 for B12 and 0.90 for D. What was said after it is what decides, so the
    shared leading words are set aside and the rest of the window is compared with the rest of each name. "ভিটামিন টেস্ট"
    has nothing left to compare: it IS two tests and the caller is asked. Names that share nothing ("টিএসএইচ" / "পিএসএ")
    are compared whole."""
    from agent.spelled_letters import edit_distance, letters_of

    # Single-word names only ("এইচবিএসএজি" / "এইচবিএ১সি"), said without the word for "test": for "ভিটামিন টেস্ট" the letters of the
    # generic word would decide, and that is a question, not a pick.
    if all(len(r[1].split()) == 1 for r in rows) and not any(t in _TEST_WORD for r in rows for t in r[3].split()):
        dists = sorted((edit_distance(letters_of(r[3]), letters_of(r[1])), i) for i, r in enumerate(rows))
        if len(dists) >= 2 and dists[1][0] - dists[0][0] >= TEST_EDIT_LEAD:
            return rows[dists[0][1]]  # one is clearly closer, letter by letter
    forms = [r[1].split() for r in rows]
    k = 0
    while all(len(f) > k for f in forms) and len({f[k] for f in forms}) == 1:
        k += 1
    from difflib import SequenceMatcher

    sims: list[tuple[float, int]] = []
    for i, (_name, _form, _score, window) in enumerate(rows):
        wtoks = window.split()
        tail_form = " ".join(forms[i][k:])
        tail_window = " ".join(wtoks[k:]) if k and wtoks[:k] == forms[i][:k] else (window if not k else "")
        # the word for "test" is not part of any name: "blood sugar test" must not lean toward "fasting" for sharing a "t" and an "s"
        tail_window = " ".join(
            t for t in tail_window.split() if t not in _TEST_WORD and not any(t.startswith(w) for w in _TEST_WORD if len(w) >= 3)
        )
        sims.append((SequenceMatcher(None, tail_window, tail_form).ratio() if tail_window and tail_form else 0.0, i))
    sims.sort(reverse=True)
    if len(sims) >= 2 and sims[0][0] - sims[1][0] >= TEST_TAIL_MARGIN:
        return rows[sims[0][1]]
    return None


def extract_tests(transcript: str, lang: str, catalogue) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """Every catalogue test the transcript itself names -> (canonical names in the order said, the alias each was
    recognised by, all-exact). The best match for each stretch of words, as it always was: see extract_tests_detailed for
    the version that also reports the stretches two tests fit about equally well."""
    names, forms, exact, _choices = extract_tests_detailed(transcript, lang, catalogue, ask=False)
    return names, forms, exact


def extract_tests_detailed(
    transcript: str, lang: str, catalogue, ask: bool | None = None
) -> tuple[tuple[str, ...], tuple[str, ...], bool, tuple[tuple[str, ...], ...]]:
    """-> (names, forms, all-exact, choices).

    Each segment is scored on its own at the fast path's own commit floor, and after every hit the words that hit
    covered are taken out of that segment before scoring again -- so "সিবিসি ইসিজি" said without a joining word still
    yields both, while one test's name can never yield a second. A sentence whose joining word cut a name in half is
    retried whole, once, only when the segments found nothing at all.

    The words taken out are the form's, as before; when the form's spelling is not in the sentence (the recogniser wrote it
    its own way: "ক্রিয়েটিনিন ইউরিক্যাসিড" matched creatinine through "ক্রিয়াটিনিন") the matched WORDS are taken out instead.
    Before that fallback nothing was removed and the walk stopped with uric acid unread (live 2026-10-16).

    When the SAME words fit a second test about as well (within TEST_CHOICE_MARGIN, both at or above the floor, and the
    best not an exact spelling), neither is picked: the stretch is returned as a choice and the caller is asked. A choice
    whose candidates include a test already named clearly elsewhere in the sentence drops that candidate; if one is left
    it is taken (and read back like any inexact match), if none is left the words were a repeat.

    Nothing below the floor is ever returned: a near-miss is "I am not sure", and this module exists to turn
    "not sure" into a question rather than a price."""
    ask = TEST_CHOICE_ASK if ask is None else ask
    found: list[str] = []
    forms: list[str] = []
    scores: list[float] = []
    choices: list[tuple[str, ...]] = []
    limit = MAX_TESTS_PER_TURN + 1  # one past the cap, to SEE that the caller named more than a turn takes

    def candidates_for(remaining: str, name: str, form: str, score: float, window: str) -> list[tuple[str, str, float, str]]:
        """The best test and every other that scores within the margin on the SAME words: (name, form, score, window)."""
        out = [(name, form or name, score, window)]
        if not ask or score >= 0.999:
            return out
        wset = set(window.split())
        skip = {name}
        while len(out) < _MAX_CANDIDATES:
            n2, f2, s2, w2 = catalogue.match(
                remaining, "test", lang, COMMIT_FLOOR, skip_name=frozenset(skip), with_window=True
            )
            if not n2 or s2 < max(COMMIT_FLOOR, score - TEST_CHOICE_MARGIN) or not (wset & set(w2.split())):
                break
            out.append((n2, f2 or n2, s2, w2))
            skip.add(n2)
        return out

    def walk(text: str) -> None:
        remaining = text
        while remaining and len(found) < limit:
            name, form, score, window = catalogue.match(remaining, "test", lang, COMMIT_FLOOR, with_window=True)
            if not name or score < COMMIT_FLOOR:
                return
            rows = candidates_for(remaining, name, form, score, window)
            if len(rows) > 1:
                pick = _tail_winner(rows)
                if pick is None:
                    choices.append(tuple(r[0] for r in rows))
                else:  # the words say which: the best SCORE need not be the best FIT ("ভিটামিন ব ল্বেল্বে" is B12, not D)
                    name, form, score, window = pick
                    rows = [pick]
            if len(rows) == 1 and name not in found:
                found.append(name)
                forms.append(form or name)
                scores.append(score)
            shorter = _without(remaining, form)
            if shorter == remaining and TEST_WALK_FIX:
                shorter = _without_window(remaining, window)
            if shorter == remaining:  # nothing consumed: stop rather than score the same words again
                return
            remaining = shorter

    for segment in _segments(transcript):
        if len(found) >= limit:
            break
        walk(segment)
    if not found and not choices:
        walk(" ".join(_tokens(transcript)))
    if len(found) > MAX_TESTS_PER_TURN:
        # More tests than a turn takes. Pricing the first few and saying nothing of the rest is a short total the
        # caller believes; nothing is grounded instead, and the caller is asked which tests, as for any name that
        # could not be vouched for.
        return (), (), False, ()

    # a candidate the caller already named clearly elsewhere is not a second possibility for these words
    open_choices: list[tuple[str, ...]] = []
    exact = all(sc >= 1.0 for sc in scores) if scores else True
    for cands in choices:
        left = tuple(c for c in cands if c not in found)
        if not left:
            continue  # the same test said twice
        if len(left) == 1:
            found.append(left[0])
            forms.append(_spoken_form(left[0], lang, catalogue))
            exact = False
        else:
            open_choices.append(left)
    if open_choices:
        exact = False
    return tuple(found), tuple(forms), exact, tuple(open_choices)


def _resolves_to(name: str, grounded: tuple[str, ...], lang: str, catalogue) -> bool:
    """Is this model-offered name just one of the grounded tests under another spelling? Tried in the caller's
    language and in English, because the model answers with the catalogue's English name ("CBC") whatever
    language the call is in."""
    if name in grounded:
        return True
    folded = name.casefold()
    if any(folded == g.casefold() for g in grounded):
        return True
    for table_lang in (lang, "en"):
        canonical, _form, score = catalogue.match(name, "test", table_lang, COMMIT_FLOOR)
        if canonical and score >= COMMIT_FLOOR and canonical in grounded:
            return True
    return False


def _spoken_form(name: str, lang: str, catalogue) -> str:
    """How the call's own language says a test whose name the caller never said in it: the clinic's first alias in that
    language, else the name without its bracket ("HIV Test (ELISA)" -> "HIV Test")."""
    for row in getattr(catalogue, "test_rows", None) or ():
        if row.get("name") == name:
            aliases = row.get(f"aliases_{lang}") or []
            if aliases:
                return aliases[0]
            break
    return re.sub(r"\s*\(.*?\)", "", name).strip() or name


_GLUE_ALIASES: dict[tuple[int, str], list[str]] = {}
_GENERIC_TAIL = frozenset({"টেস্ট", "टेस्ट", "test"})


def _glue_aliases(catalogue, lang: str) -> list[str]:
    """The spoken forms of the tests whose names ARE acronyms (HCV, HBsAg, HIV, ECG ...), in `lang`'s script, without the word
    "test" and without spaces: what a glued run of acronyms is made of."""
    key = (id(catalogue), lang)
    if key not in _GLUE_ALIASES:
        from agent.spelled_letters import acronym_table

        owners = set(acronym_table(getattr(catalogue, "test_rows", None) or []).values())
        out: list[str] = []
        for row in getattr(catalogue, "test_rows", None) or ():
            if row.get("name") not in owners:
                continue
            for alias in row.get(f"aliases_{lang}") or ():
                toks = [t for t in alias.split() if t not in _GENERIC_TAIL]
                joined = "".join(toks)
                if len(joined) >= 4 and joined not in out:
                    out.append(joined)
        _GLUE_ALIASES.clear()
        _GLUE_ALIASES[key] = out
    return _GLUE_ALIASES[key]


def _unglue(transcript: str, lang: str, catalogue) -> str:
    """Acronyms said without a pause arrive as ONE long word ("এইচআইবিএইচসিভিএইচবিএসজি"); split each such word into the clinic's own
    spellings of them, in order, so the matcher sees "এইচআইভি এইচসিভি এইচবিএসএজি". Only a word covered end to end by two or more
    acronyms, each at most one letter off, is touched."""
    if lang not in ("bn", "hi"):
        return transcript
    from agent.spelled_letters import split_glued

    aliases = _glue_aliases(catalogue, lang)
    if len(aliases) < 2:
        return transcript
    words = transcript.split()
    out: list[str] = []
    changed = False
    for w in words:
        parts = split_glued(w, aliases) if len(w) >= 8 else None
        if parts:
            out.extend(parts)
            changed = True
        else:
            out.append(w)
    return " ".join(out) if changed else transcript


def ground_tests(transcript: str, lang: str, catalogue, candidates=(), extra_names=()) -> GroundedTests:
    """The tests this turn may act on, and the ones it may not.

    `candidates` is whatever the intent layer produced (the model's test_name / test_names, or a cache hit's).
    It is never trusted: it is reported back as `dropped` when the transcript does not support it, so the caller
    can be asked instead. The grounded names come from the transcript alone.

    `extra_names` is a SECOND READING of the same audio -- acronyms spelled letter by letter in the English recogniser's
    text (agent/spelled_letters.py), which a Bengali or Hindi recogniser folds into pseudo-words ("এইচসিভি" -> "হপ").
    Those tests are added after the ones the transcript names, in the call's own language, and the result is no longer
    "exact", so the caller reads it back and waits for a yes exactly as for any other test it was not sure of.
    """
    if catalogue is None:
        # No catalogue loaded (clinic-api was down at boot): nothing can vouch for a name, so nothing is grounded
        # and the caller is asked. Failing closed is the whole point -- an ungrounded price is the bug.
        return GroundedTests(dropped=tuple(c for c in candidates if c), count=spoken_count(transcript, lang))
    # Said in another script than the call's language (docs/adr/0003: a Bengali call, an English or Latin-letter
    # Bengali sentence): that script's own table first. Matched against the call's table instead, the Banglish "er"
    # (এর, "of") is a fuzzy hit for the form "esr", and "cbc test er daam koto" priced the ESR (live 2026-10-14).
    # ONLY that table: the call's own table holds the same Latin names as forms and fuzzy-matches a stray Banglish
    # "er" (এর, "of") to the code "esr" -- "buk er test korbo" priced the ESR (live 2026-10-14).
    call_lang = lang
    other = foreign_script_language(transcript, lang)
    if other is not None:
        lang = other
    if TEST_GLUE_SPLIT:
        transcript = _unglue(transcript, lang, catalogue)
    names, forms, exact, choices = extract_tests_detailed(transcript, lang, catalogue)
    if extra_names:
        added = [n for n in extra_names if n and n not in names]
        if added and len(names) + len(added) <= MAX_TESTS_PER_TURN:
            names = tuple(names) + tuple(added)
            forms = tuple(forms) + tuple(_spoken_form(n, call_lang, catalogue) for n in added)
            exact = False
    dropped = tuple(c for c in candidates if c and not _resolves_to(c, names, lang, catalogue))
    return GroundedTests(
        names=names, forms=forms, dropped=dropped, count=spoken_count(transcript, lang), exact=exact, choices=choices
    )
