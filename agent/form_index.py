"""Find which spoken form appears in an utterance, without scoring every form against every stretch of it (KCD-096).

THE PROBLEM. `agent/fast_path.py` decides "which test / which doctor / which FAQ topic did the caller name" by sliding
a window of a few words along the utterance and taking the best `difflib` similarity against every known spoken
form. That is (forms x windows) `SequenceMatcher` runs per turn. Measured on the 74-row seeded catalogue it took
17-156 ms per turn -- against a 150 ms budget for the whole fast tier -- and about 3-5 seconds at 5,000 rows.

TWO CHANGES, KEPT APART BECAUSE ONE IS EXACT AND ONE IS NOT.

1. Exact pruning (always on when a floor is given). `difflib.SequenceMatcher.ratio()` can never exceed
   2 * (letters the two strings have in common, counted with multiplicity) / (len(a) + len(b)). That is a proven
   upper bound. If the bound is already below the commit floor, the real ratio is too, so the pair cannot be a
   commit and its full comparison is skipped. Every result AT OR ABOVE the floor is bit-for-bit what the full scan
   returns. On the seeded catalogue this leaves about 7 of 2,255 comparisons.

2. A bigram shortlist (only for large tables, `index_min_forms`). Forms sharing too few character pairs with the
   utterance are not compared at all. This one is NOT exact: a form whose only match is scattered, non-adjacent
   letters can be dropped. The failure direction is safe by construction -- the caller of this module commits only
   above a floor that is computed on the very same ratio, so a dropped form can only turn a would-be fast answer into
   "abstain, ask the model". It can never make a wrong answer. Short forms (< SHORT_FORM_CHARS) are never dropped:
   one changed letter of four still scores 0.75, and they are few. The parity test in
   tests/test_form_index.py measures how often the shortlist changes a decision on generated utterances.

What this module does NOT do is fold to phonemes. A fast-path COMMIT is answered with no model behind it, and a
sound-alike is exactly the "Doctor Nobody" evidence CLAUDE.md forbids acting on; sound-alikes are for suggestions
that a caller then confirms (agent/gazetteer.py). The commit gate here stays a character similarity.
"""

from __future__ import annotations

import difflib
import math
from collections import Counter, defaultdict

# Forms with fewer characters than this are always compared (see module docstring).
SHORT_FORM_CHARS = 6
# A form is compared only if at least this share of its character pairs occur in the utterance. A form that
# scores 0.72 against a stretch of the same length differs by at most about two letters, which removes at most two
# pairs each; 0.4 keeps every such form of six or more characters.
MIN_PAIR_SHARE = 0.4
# A bare name (a form without its generic word) that is not spelled exactly must be at least this long and this similar.
BARE_FUZZY_MIN_CHARS = 8
BARE_FUZZY_FLOOR = 0.85
# Below this many spoken forms a table is scanned (with exact pruning); the shortlist starts to pay for itself
# in the hundreds.
INDEX_MIN_FORMS = 400


def _bag_bound(form_bag: Counter, form_len: int, win_bag: Counter, win_len: int) -> float:
    common = 0
    for ch, n in form_bag.items():
        m = win_bag.get(ch)
        if m:
            common += min(m, n)
    return 2.0 * common / (form_len + win_len)


class FormTable:
    """One (kind, language) table: canonical name -> spoken forms, already normalised by the caller."""

    def __init__(
        self,
        rows: list[tuple[str, list[str]]],
        *,
        exact_below_chars: int = 0,
        index_min_forms: int = INDEX_MIN_FORMS,
        generic_words: frozenset[str] = frozenset(),
        bare_core: bool = False,
        bare_block: frozenset[str] = frozenset(),
    ):
        self.rows = rows
        # A spoken form that is "name + generic word" ("ইউরিন টেস্ট") is also matched by the name ALONE ("ইউরিন"), on the
        # name's own letters. Without it a bare name passed only when it happened to be long enough next to the word "test"
        # (0.727 for "ওয়াইডাল"), and failed when it was not (0.625 for "ইউরিন"). `bare_block`: names that stand for more than one
        # test ("ডেঙ্গু", "লিভার") -- a bare one is a kind of test, not a test, and is left to the group net.
        self._bare_core = bare_core
        self._bare_block = bare_block
        self._exact_below = exact_below_chars
        self._generic = generic_words
        self._fuzzy_generic: dict[str, bool] = {}
        self._cores: list[str | None] = []
        self._names: list[str] = []
        self._forms: list[str] = []
        self._spans: list[int] = []
        self._bags: list[Counter] = []
        self._core_bags: list[Counter | None] = []
        self._postings: dict[str, list[int]] = defaultdict(list)
        self._need: dict[int, int] = {}
        self._always: list[int] = []
        for name, forms in rows:
            for form in forms:
                i = len(self._forms)
                self._names.append(name)
                self._forms.append(form)
                self._spans.append(len(form.split()))
                self._bags.append(Counter(form))
                core = self._core(form)
                self._cores.append(core)
                self._core_bags.append(Counter(core) if core else None)
        self.indexed = len(self._forms) >= index_min_forms
        if self.indexed:
            for i, form in enumerate(self._forms):
                if len(form) < SHORT_FORM_CHARS:
                    self._always.append(i)
                    continue
                grams = {form[k : k + 2] for k in range(len(form) - 1)}
                self._need[i] = max(1, math.ceil(MIN_PAIR_SHARE * len(grams)))
                for g in grams:
                    self._postings[g].append(i)
        self.stats = {"lookups": 0, "compared": 0}

    def __len__(self) -> int:
        return len(self._forms)

    def _is_generic(self, token: str, fuzzy: bool) -> bool:
        """A generic word, or -- for what the caller SAID -- a misspelling of one ("test" heard as a near-copy): 0.8
        similar or better, at least three characters, so an ordinary name is never taken for one."""
        if token in self._generic:
            return True
        if not fuzzy or len(token) < 3:
            return False
        hit = self._fuzzy_generic.get(token)
        if hit is None:
            hit = any(difflib.SequenceMatcher(None, token, g).ratio() >= 0.8 for g in self._generic)
            self._fuzzy_generic[token] = hit
        return hit

    def _core(self, text: str, fuzzy: bool = False) -> str | None:
        """`text` without the generic words, or None when there are none to remove (or nothing else would remain)."""
        if not self._generic:
            return None
        words = text.split()
        kept = [w for w in words if not self._is_generic(w, fuzzy)]
        return " ".join(kept) if kept and len(kept) < len(words) else None

    def _bare_score(self, i: int, w: str, wbag: Counter, wlen: int, floor: float) -> float:
        """How well the window `w`, which has no generic word in it, says the NAME part of form `i` ("ইউরিন" for "ইউরিন টেস্ট");
        0.0 when it does not, or when this form has no name part, or the name stands for a group of tests."""
        core = self._cores[i]
        if not self._bare_core or core is None or floor <= 0.0 or core in self._bare_block:
            return 0.0
        clen = len(core)
        if 2.0 * min(clen, wlen) < floor * (clen + wlen):
            return 0.0
        if w == core:
            return 1.0
        # A name said bare is held to a far stricter standard than a whole form: "यूरिक" (uric) is one letter from "यूरिन" (urine),
        # "मेरा" (my) scores 0.73 against "मलेरिया". Short names must be spelled exactly (the spelling variants a recogniser writes
        # are aliases); a long one may be one or two letters off.
        if clen < BARE_FUZZY_MIN_CHARS or _bag_bound(self._core_bags[i], clen, wbag, wlen) < BARE_FUZZY_FLOOR:
            return 0.0
        if self._core(w, fuzzy=True) is not None or any(self._is_generic(t, True) for t in w.split()):
            return 0.0
        score = difflib.SequenceMatcher(None, core, w).ratio()
        return score if score >= max(floor, BARE_FUZZY_FLOOR) else 0.0

    def _candidates(self, text: str) -> list[int]:
        grams = {text[k : k + 2] for k in range(len(text) - 1)}
        votes: dict[int, int] = defaultdict(int)
        for g in grams:
            for i in self._postings.get(g, ()):
                votes[i] += 1
        keep = set(self._always)
        keep.update(i for i, v in votes.items() if v >= self._need[i])
        return sorted(keep)

    def best(self, words: list[str], floor: float = 0.0, *args, **kwargs):
        """See `_scan`. A name said without its generic word is only a FALLBACK: the whole-form scan runs first and is returned
        whenever it finds anything, so no turn that matched before matches differently now ("ইউরিন রূটিন" stays the form
        "ইউরিন রুটিন", not the bare "ইউরিন" it begins with). Only a turn the whole-form scan finds nothing in is scanned again,
        by names alone."""
        found = self._scan(words, floor, *args, use_bare=False, **kwargs)
        if found[0] is not None or not (self._bare_core and floor > 0.0):
            return found
        return self._scan(words, floor, *args, use_bare=True, **kwargs)

    def _scan(
        self,
        words: list[str],
        floor: float = 0.0,
        skip_name: str | frozenset[str] | set[str] | None = None,
        prefer_longer: bool = False,
        with_window: bool = False,
        use_bare: bool = False,
    ):
        """(canonical name, spoken form, score) of the best form in `words`, first-best on a tie in table order.

        `with_window=True` returns a fourth item: the words of the utterance the best form matched ("ক্রিয়েটিনিন" for the
        form "ক্রিয়াটিনিন"), which is what a caller that wants to take them out of the sentence must remove -- the form's
        own spelling is not what was said.

        floor=0 is the exact original scan: every pair scored, the best score returned even when it is low.
        floor>0 skips pairs that provably cannot reach `floor` (and, on a large table, forms the shortlist drops):
        a best score AT OR ABOVE `floor` is identical to the full scan's; below it the result is (None, None, 0.0),
        which every caller already treats as "not found".

        `skip_name` leaves canonical names out of the scan -- one name, or a set of them: the best of the OTHERS is
        the runner-up, which is how the fast path tells "blood sugar" (two tests fit it) from "blood sugar fasting"
        (one does), and how agent/entity_grounding.py walks a sentence that names several tests.

        `prefer_longer` breaks a tie for the LONGER form instead of the earlier one -- the FAQ table's rule: "গাড়ি
        কোথায় রাখব" (parking) and "কোথায়" (location) both match "গাড়ি কোথায় রাখব" exactly, and the specific phrase
        is the question asked. Off for tests and doctors, whose tie order is their own."""
        self.stats["lookups"] += 1
        if not self._forms:
            return (None, None, 0.0, "") if with_window else (None, None, 0.0)
        text = " ".join(words)
        order = self._candidates(text) if (self.indexed and floor > 0.0) else range(len(self._forms))
        windows: dict[int, list[tuple[str, Counter, int]]] = {}

        def windows_of(width: int):
            if width not in windows:
                windows[width] = [
                    (w, Counter(w), len(w))
                    for w in (" ".join(words[i : i + width]) for i in range(max(1, len(words) - width + 1)))
                ]
            return windows[width]

        skipped = frozenset() if skip_name is None else (
            frozenset({skip_name}) if isinstance(skip_name, str) else frozenset(skip_name)
        )
        best_name = best_form = None
        best_score = 0.0
        best_win = ""
        compared = 0
        for i in order:
            if self._names[i] in skipped:
                continue
            form, fbag = self._forms[i], self._bags[i]
            flen = len(form)
            span = self._spans[i]
            form_best = 0.0
            form_win = ""
            for width in {max(1, span - 1), span, span + 1}:
                for w, wbag, wlen in windows_of(width):
                    bare = self._bare_score(i, w, wbag, wlen, floor) if use_bare else 0.0
                    if floor > 0.0:
                        if 2.0 * min(flen, wlen) < floor * (flen + wlen) or _bag_bound(fbag, flen, wbag, wlen) < floor:
                            if bare > form_best:
                                form_best, form_win = bare, w
                            continue
                    if flen < self._exact_below:
                        score = 1.0 if w == form else 0.0
                    else:
                        score = difflib.SequenceMatcher(None, form, w).ratio()
                        compared += 1
                    core = self._cores[i]
                    if core is not None and score > form_best and score >= floor:
                        # the form contains a generic word ("test"): the match must hold on the naming words alone
                        wcore = self._core(w, fuzzy=True) or " ".join(
                            t for t in w.split() if not self._is_generic(t, True)
                        )
                        if not wcore:
                            score = 0.0
                        else:
                            core_score = (
                                (1.0 if wcore == core else 0.0)
                                if len(core) < self._exact_below
                                else difflib.SequenceMatcher(None, core, wcore).ratio()
                            )
                            score = min(score, core_score)
                    if bare > score:
                        score = bare
                    if score > form_best:
                        form_best, form_win = score, w
            if form_best > best_score or (
                prefer_longer and form_best > 0.0 and form_best == best_score and len(form) > len(best_form or "")
            ):
                best_name, best_form, best_score, best_win = self._names[i], form, form_best, form_win
        self.stats["compared"] += compared
        if floor > 0.0 and best_score < floor:
            return (None, None, 0.0, "") if with_window else (None, None, 0.0)
        return (best_name, best_form, best_score, best_win) if with_window else (best_name, best_form, best_score)
