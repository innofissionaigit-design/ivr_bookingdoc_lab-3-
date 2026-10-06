"""Repeated language ambiguity hands the call on (upgrade prompt section 6.4), and the one definition of the
language rollback lever (section 9), off-pod. Pure logic -- no model, no audio.

    python -m pytest tests/test_language_ambiguity.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.lang_select import CONVINCING_MIN_AGREEMENT, lid_is_unsure, outcome_is_ambiguous
from agent.lid import ASRLanguageRouter, LIDResult, parse_active_languages

ACTIVE = ("bn", "hi", "en")


class Rec:
    def __init__(self, text, agreement):
        self.text, self.decoder_agreement = text, agreement


UNSURE = LIDResult(language="hi", confidence=0.40, scores={"bn": 0.35, "hi": 0.40, "en": 0.25})
SURE = LIDResult(language="bn", confidence=0.97, scores={"bn": 0.97, "hi": 0.02, "en": 0.01})


# ------------------------------------------------------------------ the definition


def test_language_id_is_unsure_when_it_is_unknown_low_or_torn():
    assert lid_is_unsure(LIDResult("unknown", 0.0), ACTIVE)
    assert lid_is_unsure(LIDResult("bn", 0.40, {"bn": 0.40, "hi": 0.30, "en": 0.30}), ACTIVE)
    assert lid_is_unsure(LIDResult("hi", 0.90, {"bn": 0.0, "hi": 0.90, "en": 0.10}), ACTIVE)  # the accented-English trap
    assert not lid_is_unsure(SURE, ACTIVE)


def test_ambiguous_needs_both_language_id_unsure_and_a_recogniser_that_is_not_convincing():
    garbled = Rec("abc", CONVINCING_MIN_AGREEMENT - 0.1)
    clean = Rec("হ্যাঁ", 1.0)
    assert outcome_is_ambiguous(UNSURE, garbled, ACTIVE)
    # a short "yes": language ID is low but the decoders agree -> NOT ambiguous
    assert not outcome_is_ambiguous(UNSURE, clean, ACTIVE)
    # a confident label over a garbled transcript is a transcript problem, not a language one
    assert not outcome_is_ambiguous(SURE, garbled, ACTIVE)
    assert not outcome_is_ambiguous(SURE, clean, ACTIVE)


def test_no_text_at_all_is_not_convincing():
    assert outcome_is_ambiguous(UNSURE, Rec("   ", 1.0), ACTIVE)


# ------------------------------------------------------------------ the router's streak


def test_repeated_ambiguity_hands_off_even_when_the_call_has_a_prior():
    r = ASRLanguageRouter(max_ambiguous_streak=3)
    r.route(LIDResult("bn", 0.95))  # a confident first turn: the call now HAS a prior language
    assert r.previous_language == "bn"
    assert [r.note_turn_outcome(True) for _ in range(3)] == [None, None, None]  # tolerated
    d = r.note_turn_outcome(True)
    assert d is not None and d.action == "handoff_human" and d.language is None


def test_one_clear_turn_resets_the_count():
    r = ASRLanguageRouter(max_ambiguous_streak=2)
    r.note_turn_outcome(True)
    r.note_turn_outcome(True)
    assert r.note_turn_outcome(False) is None
    assert r.note_turn_outcome(True) is None  # the count started again
    assert r.note_turn_outcome(True) is None
    assert r.note_turn_outcome(True).action == "handoff_human"


def test_it_keeps_asking_for_a_hand_off_while_the_ambiguity_continues():
    r = ASRLanguageRouter(max_ambiguous_streak=1)
    r.note_turn_outcome(True)
    assert r.note_turn_outcome(True).action == "handoff_human"
    assert r.note_turn_outcome(True).action == "handoff_human"
    assert r.note_turn_outcome(False) is None


def test_the_streak_is_not_the_no_prior_streak_and_does_not_disturb_it():
    r = ASRLanguageRouter(max_ambiguous_streak=2)
    r.note_turn_outcome(True)
    # the original routing behaviour is untouched: low confidence, no prior -> dual ASR, not a hand-off
    assert r.route(LIDResult("unknown", 0.1)).action == "dual_asr"


# ------------------------------------------------------------ the rollback lever


def test_parse_active_languages_defaults_to_all_three():
    assert parse_active_languages(None) == ("bn", "hi", "en")
    assert parse_active_languages("bn,hi,en") == ("bn", "hi", "en")


def test_bengali_alone_is_the_rollback():
    assert parse_active_languages("bn") == ("bn",)


def test_bengali_is_always_active_and_garbage_is_ignored_not_guessed_at():
    assert parse_active_languages("hi") == ("bn", "hi")
    assert parse_active_languages("en, fr ,HI") == ("bn", "hi", "en")
    assert parse_active_languages("") == ("bn",)
    assert parse_active_languages("klingon") == ("bn",)


def test_order_does_not_depend_on_how_it_was_typed():
    assert parse_active_languages("en,hi,bn") == ("bn", "hi", "en")
