"""Start language ID on the first useful stretch of voiced audio, not at end-of-turn.

Upgrade-prompt section 6.1 / ADR 0001 section 2: "by the time VAD confirms the turn is complete, the routing
decision should already be sitting there. LID should contribute close to nothing to end-of-turn latency."
Until now language ID ran on the FINISHED utterance (main.py _route_and_transcribe), overlapped with the first
recogniser but still on the turn's critical path.

How it works, in three small pure pieces so none of it needs a GPU, torch or a pod:

  early_window    given the turn detector's speech spans, the slice of the caller's first words that is long
                  enough and voiced enough to identify a language from (None until there is one);
  EarlyLID        the call's handle on the identification running in the background for that turn;
  resolve_early   at end-of-turn, the result if -- and only if -- it is good enough to skip identifying again.

The early answer is only a SHORTCUT. It is used when it is decisive: a supported language, high confidence, and
no real probability on any other language (agent/lang_select.languages_to_verify). Anything less -- including
every case of the Indian-accented-English-labelled-Hindi trap, which by construction leaves mass on a second
language -- is thrown away and the whole utterance is identified as before. So a wrong early guess about a
caller who changes language mid-sentence can only happen when the first seconds were confidently and
unambiguously another language; the cost of not taking the shortcut is the old latency, never a wrong route.

THRESHOLDS ARE REASONED, NOT MEASURED. The window and the confidence floor were chosen from the model card
(VoxLingua107 is trained on clips of a few seconds) and the pod measurements recorded in lang_select.py, not
calibrated on real G.711 Kolkata calls. EARLY_LID=off disables the shortcut without a code change.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from collections.abc import Sequence

from agent.lang_select import languages_to_verify
from agent.lid import SUPPORTED_LANGUAGES, LIDResult

logger = logging.getLogger("early_lid")

# How much of the caller's first words language ID hears. REASONED.
EARLY_LID_WINDOW_S = float(os.environ.get("EARLY_LID_WINDOW_S", "1.2"))
# Of that window, at least this fraction must be voiced -- a breath, a click and a pause before the first word
# must not stand in for the caller's language. REASONED.
MIN_VOICED_FRACTION = 0.6
# The early answer is taken only at or above this. Higher than the ordinary routing floor (0.55) because a
# shorter clip is less reliable and a shortcut should only be taken when it is clear. REASONED.
EARLY_LID_MIN_CONFIDENCE = float(os.environ.get("EARLY_LID_MIN_CONFIDENCE", "0.80"))
# At end-of-turn, how long to wait for an early identification that has not finished. It is ~0.2 s of CPU and was
# started well before the caller stopped, so this is a backstop; past it the whole utterance is identified.
EARLY_LID_WAIT_S = float(os.environ.get("EARLY_LID_WAIT_S", "0.5"))


def enabled() -> bool:
    return os.environ.get("EARLY_LID", "on").strip().lower() not in ("off", "0", "false", "no")


def early_window(spans: Sequence[dict], window_s: float = EARLY_LID_WINDOW_S) -> tuple[float, float] | None:
    """(start, end) seconds, relative to the turn detector's slice, of the first `window_s` of the caller's
    speech -- or None while there is not yet that much voiced speech.

    `spans` is [{"start": s, "end": s}] from Silero. Speech must have run for at least `window_s` past its
    first sample, and at least MIN_VOICED_FRACTION of that window must fall inside a span."""
    if not spans:
        return None
    start = float(spans[0]["start"])
    end = start + window_s
    if float(spans[-1]["end"]) < end:
        return None
    voiced = sum(max(0.0, min(float(s["end"]), end) - max(float(s["start"]), start)) for s in spans)
    return (start, end) if voiced >= MIN_VOICED_FRACTION * window_s else None


@dataclasses.dataclass
class EarlyLID:
    """The identification started for one turn. `key` is the call-timeline position the turn began at
    (session.processed_until_s when it started), so a result can never be handed to a different turn."""

    key: float
    task: asyncio.Future

    def matches(self, turn_start_s: float) -> bool:
        return abs(self.key - turn_start_s) < 1e-6


def start(key: float, identify) -> EarlyLID:
    """Run `identify()` (a blocking, CPU-bound callable returning a LIDResult) in a worker thread. A failure is
    swallowed here and reported as "no early result" when resolved -- it must never reach the call."""
    task = asyncio.ensure_future(asyncio.to_thread(identify))
    task.add_done_callback(lambda f: f.cancelled() or f.exception())  # an unread failure is not an error
    return EarlyLID(key=key, task=task)


def usable(lid: LIDResult, active: tuple[str, ...]) -> bool:
    """Is this early answer decisive enough to skip identifying the whole utterance?"""
    return (
        lid.language in SUPPORTED_LANGUAGES
        and lid.language in active
        and lid.confidence >= EARLY_LID_MIN_CONFIDENCE
        and bool(lid.scores)
        and languages_to_verify(lid.language, lid.scores, active) is None
    )


async def resolve_early(early: EarlyLID | None, active: tuple[str, ...], wait_s: float = EARLY_LID_WAIT_S):
    """The early LIDResult if it finished (within `wait_s`) and is decisive; otherwise None, meaning "identify the
    whole utterance". Never raises."""
    if early is None:
        return None
    try:
        lid = await asyncio.wait_for(asyncio.shield(early.task), wait_s)
    except Exception as e:  # noqa: BLE001 - timeout or a model fault: fall back to the full utterance
        logger.info("early language ID unavailable (%s: %s) -- identifying the whole utterance", type(e).__name__, e)
        return None
    if not usable(lid, active):
        logger.info("early language ID %s %.2f not decisive -- identifying the whole utterance", lid.language, lid.confidence)
        return None
    return lid
