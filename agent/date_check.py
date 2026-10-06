"""Cross-check a spoken day against the date the model resolved, and read it back before use.

Story: "Caller says tomorrow, day after, or next Monday". Its third acceptance criterion -- "the
resolved absolute date is always read back before use" -- was the one genuinely missing: a
booking's final readback names the date, but an availability question was answered on a date the
caller never heard confirmed.

WHY A CROSS-CHECK AND NOT A REWRITE
-----------------------------------
`agent/llm.py` asks the model to resolve relative words to an ISO date itself. Two documented
decisions constrain what can be done about that:

  * the owner excluded changes to the extraction prompt (merge_report.md section 2), so the
    model cannot be asked for a `date_expr` instead;
  * `fast_path_cues.py` keeps Hindi's relative-day table deliberately minimal -- `{"आज": 0}` --
    because कल and परसों genuinely mean both tomorrow and yesterday, and merge_report.md section
    4.10 records that restraint as correct and not to be "fixed".

So this module neither replaces the model's date nor widens any language's day words. It resolves
what the cue tables ALREADY know, compares, and lets the caller settle it:

    agree                     -> confirm   "আগামীকাল মানে মঙ্গলবার, ...। ঠিক আছে?"
    differ                    -> ask       "আপনি কি <A>, নাকি <B>?"
    only one side has a date  -> confirm that one
    neither                   -> nothing was said about a day

A date is never silently used because two independent readings happened to agree -- agreement
buys confidence, and the caller still hears the date. The one thing that cannot happen is a
lookup on a date nobody said out loud.

Pure: no model, no clock of its own (`today` is passed in), no I/O.
"""

from __future__ import annotations

import dataclasses
import datetime

from agent import fast_path_cues as cues

# What the agent does with the day it heard.
CONFIRM = "confirm"  # one date to read back
ASK = "ask"  # two readings disagree; the caller picks
ABSENT = "absent"  # no day was mentioned


@dataclasses.dataclass(frozen=True)
class DateVerdict:
    action: str
    date: str | None = None  # CONFIRM: the date to read back
    candidates: tuple[str, ...] = ()  # ASK: the readings to offer
    said: str | None = None  # the caller's own day word, when they used one


def resolve_spoken_day(text: str, lang: str, today: datetime.date | None = None) -> tuple[str | None, str | None]:
    """-> (ISO date, the day word the caller used), from THIS language's cue table only.

    Deliberately narrow. It resolves exactly the words `fast_path_cues` already lists for the
    language -- so Hindi resolves आज and nothing else, and a word that language treats as
    ambiguous stays unresolved here too, by design."""
    table = cues.table_for(lang)
    if table is None or not text:
        return None, None
    base = today or datetime.date.today()
    for word, offset in table.relative_days.items():
        if table.contains(text, word):
            return (base + datetime.timedelta(days=offset)).isoformat(), word
    return None, None


def _valid(date_iso: str | None) -> str | None:
    try:
        return datetime.date.fromisoformat(str(date_iso)).isoformat()
    except (TypeError, ValueError):
        return None


def check(text: str, model_date: str | None, lang: str, today: datetime.date | None = None) -> DateVerdict:
    """Compare the day this language's cue table reads against the date the model resolved."""
    code_date, said = resolve_spoken_day(text, lang, today)
    model_date = _valid(model_date)
    if code_date and model_date and code_date != model_date:
        # Two defensible readings of the same words. Neither is trusted over the other; the
        # caller decides, which is the only reading that cannot be wrong.
        return DateVerdict(ASK, candidates=(code_date, model_date), said=said)
    settled = code_date or model_date
    if not settled:
        return DateVerdict(ABSENT)
    return DateVerdict(CONFIRM, date=settled, said=said)
