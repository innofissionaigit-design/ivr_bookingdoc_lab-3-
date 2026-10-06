"""Language ID started on the caller's first words (agent/early_lid.py), off-pod.

Pure logic only -- no model, no audio, no pod. The orchestrator's use of it
(`_maybe_start_early_lid`, `_route_and_transcribe`) is in
tests/test_language_routing_wiring.py.

    python -m pytest tests/test_early_lid.py -v
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import early_lid
from agent.lid import LIDResult

ACTIVE = ("bn", "hi", "en")


def spans(*pairs):
    return [{"start": a, "end": b} for a, b in pairs]


# ------------------------------------------------------------------ early_window


def test_no_window_until_there_is_enough_speech():
    assert early_lid.early_window([]) is None
    assert early_lid.early_window(spans((0.2, 0.9)), window_s=1.2) is None  # 0.7 s of speech so far


def test_the_window_is_the_first_stretch_of_speech_not_the_start_of_the_buffer():
    w = early_lid.early_window(spans((0.4, 2.5)), window_s=1.2)
    assert w == pytest.approx((0.4, 1.6))


def test_a_pause_inside_the_window_is_tolerated_but_a_mostly_silent_window_is_not():
    # voiced 0.4 + 0.5 = 0.9 s of a 1.2 s window (0.75) -> enough
    assert early_lid.early_window(spans((0.0, 0.4), (0.7, 2.0)), window_s=1.2) is not None
    # voiced 0.2 + 0.2 = 0.4 s of 1.2 s (0.33) -> a breath and a click are not a language
    assert early_lid.early_window(spans((0.0, 0.2), (1.0, 1.2), (2.5, 3.5)), window_s=1.2) is None


def test_speech_that_ends_before_the_window_closes_gives_no_window():
    assert early_lid.early_window(spans((0.0, 1.0)), window_s=1.2) is None


# ------------------------------------------------------------------------ usable


def lid(lang, conf, **scores):
    return LIDResult(language=lang, confidence=conf, scores=scores)


def test_a_confident_decisive_early_answer_is_usable():
    assert early_lid.usable(lid("bn", 0.96, bn=0.96, hi=0.02, en=0.02), ACTIVE)


def test_accented_english_labelled_hindi_is_never_taken_as_a_shortcut():
    # The measured trap: Hindi at 0.9 with real mass on English. It must go through verification.
    assert not early_lid.usable(lid("hi", 0.90, bn=0.01, hi=0.90, en=0.09), ACTIVE)


def test_a_second_language_at_the_floor_is_not_decisive():
    assert not early_lid.usable(lid("bn", 0.96, bn=0.96, hi=0.03, en=0.01), ACTIVE)


def test_second_language_mass_below_the_floor_still_counts_as_decisive():
    assert early_lid.usable(lid("hi", 0.97, bn=0.01, hi=0.97, en=0.02), ACTIVE)


def test_low_confidence_unknown_inactive_or_score_less_answers_are_not_usable():
    assert not early_lid.usable(lid("bn", 0.60, bn=0.60, hi=0.01, en=0.0), ACTIVE)  # below the early floor
    assert not early_lid.usable(lid("unknown", 0.0), ACTIVE)
    assert not early_lid.usable(lid("hi", 0.99, bn=0.0, hi=0.99, en=0.0), ("bn",))  # hi not active in this build
    assert not early_lid.usable(LIDResult(language="bn", confidence=0.99), ACTIVE)  # no scores -> cannot check


# ------------------------------------------------------------------ resolve_early


@pytest.mark.asyncio
async def test_resolve_early_returns_a_decisive_finished_result():
    e = early_lid.start(10.0, lambda: lid("bn", 0.96, bn=0.96, hi=0.02, en=0.02))
    got = await early_lid.resolve_early(e, ACTIVE)
    assert got is not None and got.language == "bn"


@pytest.mark.asyncio
async def test_resolve_early_says_none_when_there_is_nothing_decisive_to_use():
    assert await early_lid.resolve_early(None, ACTIVE) is None
    e = early_lid.start(1.0, lambda: lid("hi", 0.9, bn=0.01, hi=0.9, en=0.09))
    assert await early_lid.resolve_early(e, ACTIVE) is None


@pytest.mark.asyncio
async def test_a_model_fault_is_swallowed_not_raised_into_the_call():
    def boom():
        raise RuntimeError("model fell over")

    e = early_lid.start(1.0, boom)
    assert await early_lid.resolve_early(e, ACTIVE) is None


@pytest.mark.asyncio
async def test_an_identification_that_is_too_slow_is_abandoned_not_waited_for():
    import time

    e = early_lid.start(1.0, lambda: (time.sleep(0.5), lid("bn", 0.99, bn=0.99, hi=0.0, en=0.0))[1])
    t0 = asyncio.get_running_loop().time()
    assert await early_lid.resolve_early(e, ACTIVE, wait_s=0.05) is None
    assert asyncio.get_running_loop().time() - t0 < 0.4


@pytest.mark.asyncio
async def test_start_runs_the_identification_off_the_event_loop():
    import threading

    seen = {}

    def identify():
        seen["thread"] = threading.current_thread()
        return lid("en", 0.9, bn=0.0, hi=0.0, en=1.0)

    await early_lid.start(1.0, identify).task
    assert seen["thread"] is not threading.main_thread()


def test_an_early_result_belongs_to_one_turn_only():
    class T:  # stands in for the future: matches() never touches it
        pass

    e = early_lid.EarlyLID(key=12.5, task=T())  # type: ignore[arg-type]
    assert e.matches(12.5)
    assert not e.matches(14.0)


def test_the_shortcut_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("EARLY_LID", "off")
    assert not early_lid.enabled()
    monkeypatch.setenv("EARLY_LID", "on")
    assert early_lid.enabled()
    monkeypatch.delenv("EARLY_LID")
    assert early_lid.enabled()
