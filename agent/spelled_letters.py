"""Spelled-out clinic acronyms ("H C V", "H B S A G", "H I V") read from the ENGLISH recogniser's text of the same audio.

THE PROBLEM (measured on the pod, 2026-10-16, one caller, one clip)
-------------------------------------------------------------------
A Bengali sentence with three acronyms in it -- "আমি আপনাদের ওখানে এইচসিভি, এইচবিএসএজি আর এইচআইভি টেস্ট করাবো" -- is
transcribed by the Bengali recogniser as "…ওখানে হপ হসিগ আর হিব টেস্ট করাবো…". That recogniser's vocabulary is 256
Bengali pieces; for each acronym it emits "▁হ" ("h", p 0.90-0.97) and folds the rest of the letter names into one
pseudo-word. In its own posteriors the pieces for "এইচ", "সি" and "ভি" never exceed 1-4 %, and scoring the correct text
against the audio is 87 nats worse than its own output, so no decoder setting, boosting or re-ranking of THAT model recovers
them (agent/asr.py is left exactly as it is).

The English recogniser, run on the same audio, writes the letters as letters: "h c p h b s a g r h i v" (and, slowed down,
"h c v h b s a g r h i v"). It garbles the Bengali words around them; the Bengali recogniser does the reverse. Each is good
at one half of a code-switched sentence, and the English one already runs on every turn.

WHAT THIS MODULE DOES
---------------------
Pure text, no model: from the English reading it takes the runs of single letters ("h c v", "h cv hiv") and segments them
into the clinic's own acronyms (HCV, HBsAg, HIV, ECG, CBC, ESR, TSH, PSA, CRP, LFT, KFT, TMT ...), and returns the tests
they stand for. The caller (main.py) adds those tests as EVIDENCE to the grounding of the turn; nothing the caller said is
replaced, and every price still waits for the read-back and a yes (agent/entity_grounding.py, CLAUDE.md's truth boundary).

HOW CAREFUL IT IS, AND WHY
--------------------------
* Only runs made of letters ("h c v") or of an acronym written as a word ("hiv") are read; ordinary words end a run.
* One wrong letter is tolerated per acronym ("h c p" for HCV; the recogniser cannot tell "p" from "v" on a phone line), but
  only when ONE acronym is within one letter of the chunk. "h a v" is one letter from HCV and from HIV: dropped, not guessed.
* One stray "r" is skipped between acronyms (the Bengali "আর", "and", comes out as a letter), and at most two stray letters
  at either end of a run.
* An acronym that belongs to more than one test (USG: two ultrasounds; TSH: the test and the thyroid profile) is not guessed
  at, except where a test's whole name IS that acronym.

REASONED from one clip and the transcripts of four, NOT measured on a call corpus. Tests: tests/test_spelled_letters.py.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9']+")
_STRAY_BETWEEN = frozenset("r")  # the Bengali "আর" / "and" between two acronyms
_MAX_STRAY_AT_ENDS = 2
_MIN_ACRONYM = 3
# Two-letter English words end a run: "i want to do h cv" is the run "h cv", not "todohcv".
_SHORT_WORDS = frozenset(
    "am an as at be by do go he hi if in is it me my no of oh ok on or so to up us we".split()
)
_MIN_SINGLES_TO_CORRECT = 3  # a wrong letter is forgiven only in a run of at least three single letters


def acronym_table(test_rows: list[dict]) -> dict[str, str]:
    """letters ("hcv", "hbsag") -> the canonical test name, for every acronym in the catalogue's test names that stands for
    ONE test. Read from the names themselves ("Kidney Function Test (KFT)", "HIV Test (ELISA)", "HBsAg"), never typed in."""
    owners: dict[str, set[str]] = {}
    exact: dict[str, str] = {}
    for row in test_rows or []:
        name = row.get("name") or row.get("test_name") or ""
        for tok in re.findall(r"[A-Za-z0-9]+", name):
            if not tok.isalpha() or not (_MIN_ACRONYM <= len(tok) <= 6):
                continue
            if tok.isupper() or sum(1 for c in tok if c.isupper()) >= 2:
                owners.setdefault(tok.lower(), set()).add(name)
        if name.isalpha() and name.isupper():
            exact[name.lower()] = name  # a test whose whole name is the acronym ("TSH") owns it
    table: dict[str, str] = {}
    for letters, names in owners.items():
        if letters in exact:
            table[letters] = exact[letters]
        elif len(names) == 1:
            table[letters] = next(iter(names))
    return table


def _near(chunk: str, acronym: str, allow_subs: bool = True) -> int | None:
    """Substitutions between two strings of equal length, or None when more than the allowed one (none under 3 letters, or
    when the run is too short to forgive a letter)."""
    if len(chunk) != len(acronym):
        return None
    subs = sum(1 for x, y in zip(chunk, acronym) if x != y)
    return subs if subs <= (1 if (allow_subs and len(acronym) >= _MIN_ACRONYM) else 0) else None


def _parse(letters: str, acronyms: list[str], tail_may_be_cut: bool, allow_subs: bool = True) -> list[str] | None:
    """The cheapest way to read `letters` as a run of acronyms, or None. Dynamic programming over positions; a cost is the
    number of substituted or skipped letters."""
    n = len(letters)
    inf = float("inf")
    best: list[tuple[float, list[str], int]] = [(inf, [], 0)] * (n + 1)
    best[0] = (0.0, [], 0)

    def relax(j: int, cost: float, segs: list[str], skipped: int) -> None:
        if cost < best[j][0]:
            best[j] = (cost, segs, skipped)

    for i in range(n):
        cost, segs, skipped = best[i]
        if cost == inf:
            continue
        # a stray letter: any at the very ends (a few), "r" in the middle
        at_an_end = i < _MAX_STRAY_AT_ENDS or i >= n - _MAX_STRAY_AT_ENDS
        if skipped < _MAX_STRAY_AT_ENDS and at_an_end:
            relax(i + 1, cost + 1.0, segs, skipped + 1)
        if letters[i] in _STRAY_BETWEEN and segs:
            relax(i + 1, cost + 0.5, segs, skipped)
        for a in acronyms:
            chunk = letters[i : i + len(a)]
            subs = _near(chunk, a, allow_subs)
            if subs is not None:
                if subs and sum(1 for b in acronyms if _near(chunk, b, allow_subs) is not None) > 1:
                    # one letter from two acronyms ("h a v": HCV or HIV): not guessed -- the chunk is left unread
                    relax(i + len(a), cost + 1.5, segs, skipped)
                    continue
                relax(i + len(a), cost + subs, segs + [a], skipped)
            elif tail_may_be_cut and len(a) >= _MIN_ACRONYM and i + len(a) - 1 == n and letters[i:] == a[:-1]:
                relax(n, cost + 1.0, segs + [a], skipped)  # "ec test" for ECG: the last letter was lost
    final = best[n]
    return final[1] if final[0] != inf and final[1] else None


def spelled_tests(en_text: str, table: dict[str, str]) -> list[str]:
    """The tests whose acronyms are spelled in `en_text` (the English recogniser's reading), in the order said.

    []  when nothing in it is a run of letters that reads as clinic acronyms -- which is nearly every ordinary sentence."""
    if not en_text or not table:
        return []
    acronyms = sorted(table, key=len, reverse=True)
    tokens = _WORD.findall(en_text.lower())

    def is_part(tok: str) -> bool:
        return (len(tok) <= 2 and tok.isalpha() and tok not in _SHORT_WORDS) or tok in table

    found: list[str] = []
    i = 0
    while i < len(tokens):
        if not is_part(tokens[i]):
            i += 1
            continue
        j = i
        while j < len(tokens) and is_part(tokens[j]):
            j += 1
        span = tokens[i:j]
        # a run is worth reading when it has two short pieces ("h c") or an acronym written as a word ("hiv")
        short = sum(1 for t in span if len(t) <= 2)
        cut = j < len(tokens) and tokens[j] in ("test", "tests")
        if short >= 2 or any(t in table for t in span) or (cut and short >= 1):
            letters = "".join(span)
            singles = sum(1 for t in span if len(t) == 1)
            segs = _parse(letters, acronyms, tail_may_be_cut=cut, allow_subs=singles >= _MIN_SINGLES_TO_CORRECT)
            for a in segs or []:
                name = table[a]
                if name not in found:
                    found.append(name)
        i = j
    return found


# ------------------------------------------------------------------------------------------------------------ edit distance
def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (insertions, deletions and substitutions each cost one)."""
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def letters_of(text: str) -> str:
    """The letters of `text` and nothing else, lower-cased: "H B S G" -> "hbsg", "এইচ বি এস জি" -> "এইচবিএসজি". Vowel signs and other
    combining marks stay (they are part of a Bengali or Hindi letter); spaces and punctuation go."""
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFC", (text or "").lower()) if unicodedata.category(c)[0] in "LMN")


def closest_of(text: str, options: dict[str, list[str]]) -> str | None:
    """Which of a FEW named options the words say, by letters: the answer to "HBsAg or HbA1c?" given as "h b s g" (no "a"), as
    "এইচবিএসজি" or as "দ্বিতীয়টা" is not looked up in the whole catalogue, only against these two or three.

    `options` maps a name to the spellings it may be said in. The best option wins when its closest spelling is within a third
    of that spelling's length (at least one letter) AND it is at least one letter closer than the next option -- a tie is
    not a pick. None otherwise."""
    said = letters_of(text)
    if len(said) < 2:
        return None
    scored: list[tuple[int, int, str]] = []
    for name, spellings in options.items():
        best = None
        for sp in spellings:
            t = letters_of(sp)
            if not t:
                continue
            d = edit_distance(said, t)
            if best is None or d < best[0]:
                best = (d, len(t))
        if best is not None:
            scored.append((best[0], best[1], name))
    scored.sort()
    if not scored:
        return None
    d1, length, name = scored[0]
    if d1 > max(1, length // 3):
        return None
    if len(scored) > 1 and scored[1][0] - d1 < 1:
        return None
    return name


def split_glued(token: str, aliases: list[str]) -> list[str] | None:
    """A word that is really several acronyms said without a pause ("এইচআইবিএইচসিভিএইচবিএসজি" for HIV, HCV and HBsAg) ->
    the clinic's spellings of them in order, or None. Each piece may be one letter off (insertion, deletion or substitution)
    of a spoken acronym; the whole word must be covered, by at least two pieces. An ordinary word is almost never covered."""
    n = len(token)
    if n < 8 or len(aliases) < 2:
        return None
    inf = 10**9
    best: list[tuple[int, list[str]]] = [(inf, [])] * (n + 1)
    best[0] = (0, [])
    for i in range(n):
        cost, segs = best[i]
        if cost >= inf:
            continue
        for a in aliases:
            for m in (len(a) - 1, len(a), len(a) + 1):
                if m < 3 or i + m > n:
                    continue
                d = edit_distance(token[i : i + m], a)
                if d <= 1 and cost + d < best[i + m][0]:
                    best[i + m] = (cost + d, segs + [a])
    cost, segs = best[n]
    return segs if cost < inf and len(segs) >= 2 else None
