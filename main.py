"""Kolkata Care Diagnostics -- Bengali voice agent, WebSocket orchestrator.

Turn loop, once a caller's utterance is judged complete (agent/vad_stream.py):

  utterance WAV -> ASR (agent/asr.py, IndicConformer)
                -> intent+slots (agent/llm.py, Ollama JSON-mode,
                   fronted by agent/semantic_cache.py)
                -> Spring Boot lookup (agent/tools_client.py) -- ALWAYS
                   live, never cached; see semantic_cache.py's docstring
                -> reply text, TEMPLATED from the API response, never
                   restated by the model (agent/reply_templates.py)
                -> TTS (agent/tts.py) -> WAV bytes back over the socket

Every stage has a named failure path (see _dispatch_turn) so a caller never
gets dead air: ASR-empty, LLM-failure, tool-failure and TTS-failure each
speak a distinct, pre-recorded apology rather than the process hanging or
the socket just going quiet. See README.md "Error handling" for the full
table and the reasoning behind each choice.

HALF-DUPLEX GATE
----------------
The mic is open for the entire call, and the agent's replies play out of
the caller's speaker. With no gate, the agent hears itself: its own
greeting lands in the same buffer the turn detector is watching, so VAD
fires a "the caller finished talking" on the agent's own voice, ASR
transcribes the agent, and processed_until_s advances past audio the
caller never produced. That is a self-sustaining loop, and it is what
made real calls cut the caller off in the first second and then run a
turn behind for the rest of the call.

Browser echoCancellation does not save this. It is built to cancel a
remote WebRTC peer's rendered stream; here the audio is synthesized
locally and played through Web Audio, which the canceller never sees as a
far-end reference.

So the pipeline is explicitly half-duplex, gated from BOTH ends:
  * client mutes the mic track while agent audio is playing (static/
    index.html) -- the track stays live and keeps emitting, so the WebM
    timeline never breaks, it just carries silence;
  * server refuses to run turn detection while `agent_speaking`, then
    resynchronizes processed_until_s past the muted region once playback
    is confirmed finished.

The cost is no ACOUSTIC barge-in on this transport: a caller cannot talk
over the agent and be heard. That is a real limitation, chosen deliberately
over the alternative, which was a system that interrupted ITSELF. A caller
CAN still take the floor by clicking (static/index.html shows the button
while the agent speaks), which lands in _interrupt_playback(reason="manual").

ACOUSTIC BARGE-IN EXISTS, OFF BY DEFAULT, AND ONLY ON THE PCM TRANSPORT
----------------------------------------------------------------------
The canceller this gate was the alternative to was since built, here, in
numpy and with no vendor SDK (KCD-051/052):

  * agent/echo_cancel.py   the AEC -- the synthesised audio IS the far-end
                           reference, which is the one thing the browser's
                           own canceller can never have (above);
  * agent/barge_in.py      a four-test detector on the CLEANED signal;
  * agent/full_duplex.py   the glue, plus the two policies neither owns:
                           where the agent's audio sits on the microphone
                           timeline, and "trust no event until the filter
                           has converged".

Two reasons it does not change what THIS file does:

  * `AEC_BARGE_IN` is off by default. With it off the gate behaves exactly
    as described above -- which is also what makes this tree's duplex
    behaviour identical to voicebot-main's, where the same flag has the
    same default. Why off: the canceller and the detector are measured on
    SYNTHETIC echo only (tools/echo_eval.py --synthetic) and want the pod's
    real handset audio before anyone relies on them.
  * It is wired in main_pcm.py and NOT here. The AEC needs the reference
    placed at an exact sample position on the call timeline; a growing WebM
    read through ffmpeg cannot give one, which is the same reason the PCM
    variant exists at all. `self.duplex` therefore stays None in this file.

When it IS on (PCM transport), the gate does not go away: a trusted
barge-in event calls _interrupt_playback(reason="acoustic"), which stops
playback and RELEASES the gate, and the caller's first words are kept by
rewinding BARGE_IN_PREROLL_S behind the detected onset.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import datetime
import io
import json
import logging
import os
import re
import secrets
import tempfile
import time
import unicodedata
import uuid
import wave

import numpy as np
import torchaudio
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import abuse, action_gate, anger, call_end, date_check, early_lid, earliest_request
from agent import slot_grouping, topic_flow
from agent import unrelated_caller  # ADDED BY SOURAV: story "Caller has reached the hospital by mistake" -- wrong/unrelated caller cues
from agent import home_collection_flow as hcflow  # ADDED BY SOURAV: KCD-387 full flow
from agent import entity_confirmation as entity_text
from agent import history_templates as history_text
from agent import messages as agent_messages
from agent import patient_context as patient_policy
from agent import security_check as sec_text
from agent.acknowledgement import select_acknowledgement, slot_echo
from agent.admission import AdmissionController, HealthMonitor, http_probe
from agent.apology import enforce_single_apology
from agent.asr import TurnASR
from agent.asr_router import ASRRouter, HTTPASREngine, UnroutableLanguageError
from agent.attention import attend
from agent.audio_quality import assess as assess_audio
from agent.audio_quality import transcript_problem
from agent.booking_flow import (
    BookingState,
    classify_yes_no,
    correction_acknowledgement,
    effective_phone,
    merge_digits,
    normalise_phone,
    phone_digits_so_far,
    pick_offered_slot,
    is_ready_to_confirm,
    mark_awaiting_charge_confirm,
    mark_confirming,
    merge_slots,
    merge_spelling,
    missing_required,
    new_state,
)
from agent.booking_time_answer import is_a_time_answer
from agent.call_record import FINISH_DEADLINE_S as CALL_RECORD_DEADLINE_S
from agent.call_record import CallRecorder
from agent.call_score import CallSignals, score_call
from agent.call_state import (
    CallState,
    apply_caller_state,
    apply_channel_quality,
    apply_confidence,
    apply_language,
    new_call_state,
)
from agent.call_summary import build_call_summary
from agent.channel_quality import CHANNEL_CLEAN_16K, classify_channel
from agent.clause_split import split_into_clauses
from agent.code_switch import mixture_bucket
from agent.conditioning import condition as condition_audio
from agent.confidence_gate import VERIFIED, confidence_state, is_low_confidence
from agent.detector_budget import run_within_budget
from agent.detector_budget import snapshot as detector_budget_snapshot
from agent.disclosure import disclosure_for
from agent.disclosure import version_label as disclosure_version_label
from agent.emergency import detect_emergency
from agent.endpointing import classify_completeness
from agent.endpointing import decide as decide_turn_end
from agent.enquiry_followup import (
    FOLLOWUP_MAX_GAP,
    EnquiryEntity,
    entities_from_turn,
    recent_unique,
    resolve_followup_slot,
)
from agent.fast_path import COMMIT_FLOOR, FAQ_COMMIT_FLOOR, Catalogue, FastPath  # ADDED BY SOURAV: FAQ_COMMIT_FLOOR for the unrelated-caller hospital veto
from agent.filler import await_with_filler
from agent.full_duplex import FullDuplexProcessor, wav_to_pcm16k
from agent.golden_buckets import channel_buckets
from agent.history_intent import detect_history_question
from agent.report_request import asks_about_report
from agent.spoken_date import spoken_dates
from agent.spoken_time import as_clock, spoken_times
from agent.spoken_digits import spoken_digit_runs, spoken_phone
from agent.clinical_safety import is_clinical_interpretation
from agent.complaint import detect_complaint, is_complaint
from agent.compare_flow import KIND_NOT_FOUND, KIND_TEST, asks_to_compare, build_comparison
from agent.doctor_in_department_ask import asks_if_a_doctor_is_there
from agent.doctor_list import asks_for_the_doctor_list
from agent.doctor_request import name_tokens, names_a_doctor
from agent.doctor_personal_request import is_doctor_personal_request
from agent.callback_config import CALLBACKS_ENABLED
from agent.callback_request import asks_for_a_callback, build_callback_reason
from agent.spoken_codes import parse_phone as _parse_spoken_phone
from agent.unverifiable_claim import CATEGORY_REPORT_RESULT, detect_unverifiable_claim
from agent.human_request import asks_for_a_person
from agent.identity import IdentityState
from agent.lang_select import (
    CONVINCING_MIN_AGREEMENT,
    engines_needed,
    english_word_share,
    languages_to_verify,
    outcome_is_ambiguous,
    pick_candidate,
)
from agent.lang_select import speakable as _speakable
from agent.lang_select import transcript_supports_switch
from agent.language_policy import (
    establishes_call_language,
    foreign_script_language,
    language_mismatch,
    resolve_reply_language,
)
from agent.language_switch import detect_language_switch_request
from agent.latency_metrics import turn_latency_by_language
from agent.lay_terms import ABDOMEN, BLOOD, match_lay_term
from agent.test_groups import (
    asks_for_all,
    asks_for_price,
    asks_to_book,
    match_test_group,
    mentions_something_else,
    resolve_among,
)
from agent.location_question import asks_where_or_how_to_reach
from agent.parking_question import asks_about_parking_or_a_car
from agent.lookup_request import asks_what_is_booked, wants_new_booking
from agent.relationship_words import clean_relationship
from agent.clinic_identity import strip_clinic_name
import dataclasses

from agent.entity_grounding import GroundedTests, _spoken_form, ground_tests
from agent.spelled_letters import acronym_table, closest_of, spelled_tests
from agent.reply_templates_i18n import TEST_LIST_PAGE
from agent.test_list_request import asks_for_test_list, wants_more, wants_repeat
from agent.lid import (
    SUPPORTED_LANGUAGES,
    ASRLanguageRouter,
    LIDResult,
    SpeechBrainVoxLingua107LID,
    parse_active_languages,
)
from agent.llm import ExtractionError, extract_intent
from agent.near_end import NearEndProfile, level_and_pitch
from agent.outcome_metrics import (
    apology_events,
    audio_issue_buckets,
    barge_in_interrupts,
    channel_quality_buckets,
    code_switch_buckets,
    golden_buckets,
    insufficient_information,
    language_mismatches,
    reask_outcomes,
    senior_detections,
    unrelated_caller_events,  # ADDED BY SOURAV: story "Caller has reached the hospital by mistake"
)
from agent.pause_profile import PauseProfile
from agent.persona import is_clean as persona_clean
from agent.phrases import HANDOFF_ALL_LANGUAGES, phrase, prewarm_lines
from agent.pronunciation import coverage as pronunciation_coverage
from agent.pronunciation import drafts_enabled
from agent.playback_gate import PlaybackGate
from agent.reask_policy import ReaskTracker
from agent.reply_templates import (
    add_test_reply,
    blood_test_list_reply,
    booking_confirmation_readback,
    booking_reply,
    cancel_reply,
    clinic_faq_reply,
    compare_options_reply,
    department_list_prompt,
    doctor_list_reply,
    doctor_chosen_ack,
    doctor_day_unavailable_prompt,
    doctor_choice_prompt,
    doctors_by_department_reply,
    conflict_reply,
    department_route_reply,
    doctor_availability_reply,
    home_collection_address_confirm_reply,
    home_collection_address_pincode_mismatch_reply,
    home_collection_booking_result_reply,
    home_collection_cancelled_reply,
    home_collection_eligibility_reply,
    home_collection_final_confirm_reply,
    home_collection_hold_failed_reply,
    home_collection_quote_reply,
    home_collection_slots_reply,
    home_collection_stored_address_offer,
    insufficient_information_reply,
    lookup_reply,
    merged_test_prep_reply,  # KCD-382: restored during the multilingual merge (3 call sites below)
    date_ask_prompt,
    date_check_prompt,
    doctor_offer_prompt,
    earliest_none_prompt,
    earliest_slots_prompt,
    missing_slot_prompt,
    phone_continue_prompt,
    no_booking_on_number,
    multi_test_reply,
    multiple_bookings_reply,
    multiple_test_bookings_reply,  # KCD-382b: restored during the multilingual merge
    reschedule_reply,
    resend_reply,
    spelling_prompt,
    spelling_readback,
    test_prep_reply,
    test_rate_reply,
    multi_test_rate_reply,
    test_choice_prompt,
    test_confirm_prompt,
    test_list_page,
    test_list_reoffer,
    test_group_reply,
    which_tests_prompt,
    move_day_prompt,
    move_same_day_prompt,
    move_time_prompt,
    spoken_test_name,
    unfound_test,
)
from agent.security_check import SecurityCheck
from agent.semantic_cache import SemanticCache
from agent.semantic_cache import embed as _embed_probe
from agent.senior_care import KindnessPlanner
from agent.senior_voice import SeniorEvidence, explicit_senior_cue, stated_age_is_senior
from agent.senior_voice import estimate as estimate_senior
from agent.speaker_change import SpeakerChangeDetector, voice_embedding
from agent.speech_norm import contains_critical_figure
from agent.speech_policy import derive_policy, effective_rate, limit_questions
from agent.tools_client import ClinicToolsClient, ToolCallError
from agent.tts import FIGURE_SPEECH_SPEED, TTSClient, UnspeakableTextError
from agent.tts_router import TTSRouter
from agent.turn_ack import AckTracker, thanks_for
from agent.vad_stream import TurnDetector

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main")

POLL_INTERVAL_S = 0.5
IDLE_TIMEOUT_S = 90.0
UTTERANCE_PAD_S = 0.15  # small trailing pad so ASR doesn't clip the last phoneme

# A live call was observed closing itself ~26s after the last exchange --
# far short of IDLE_TIMEOUT_S, which only fires at 90s. That gap points to
# an intermediate proxy (RunPod's or an nginx in front of it) closing
# WebSocket connections that go quiet for a while, independent of this
# app's own idle logic. A small periodic heartbeat keeps real traffic
# flowing on the socket so no proxy in between decides it's abandoned.
HEARTBEAT_INTERVAL_S = 15.0

# Backstop for the half-duplex gate. Normally the client reports playback
# finished and the gate lifts immediately; this only fires when that
# message never arrives (JS error, stale cached page, a client that
# predates the control channel). Generous on purpose -- lifting the gate
# early puts the agent back to hearing itself, which is the bug.
PLAYBACK_GUARD_S = 3.0

# On resync, rewind slightly before the buffer's decoded end. The region
# being skipped is muted silence, so rewinding into it costs nothing,
# while NOT rewinding risks clipping the caller's first syllable if the
# WebM decode is running a beat behind real time.
RESYNC_REWIND_S = 0.25

# KCD-049: poll granularity. The fixed 0.5 s poll costs half its period on
# average (0.25 s) on EVERY turn. Instead: an ACTIVE cadence while the caller
# has spoken this turn, and -- the part that removes the tax -- a wake-up at
# exactly the moment the detector says its answer could change
# (TurnDecision.commit_after_s). REASONED, not measured under load on the pod:
# each active poll re-runs Silero over the unprocessed tail, so a shorter
# cadence costs CPU. Set ACTIVE_POLL_INTERVAL_S=0.5 to get the old behaviour.
ACTIVE_POLL_INTERVAL_S = float(os.environ.get("ACTIVE_POLL_INTERVAL_S", "0.2"))
MIN_WAKE_S = 0.02
WAKE_SLACK_S = 0.005

# KCD-048: semantic endpointing (agent/endpointing.py). OFF by default: it
# spends one extra speculative ASR call per candidate turn end, and shortens the
# silence needed after a "finished" transcript, which changes how the agent
# feels to talk to. That needs an A/B on the pod before it is anyone's default.
SEMANTIC_ENDPOINTING = os.environ.get("SEMANTIC_ENDPOINTING", "0") == "1"
SPECULATIVE_ASR_TIMEOUT_S = 1.5
MAX_SPECULATIONS_PER_TURN = 3
SPECULATION_MATCH_TOLERANCE_S = 0.25

# Trailing audio never trusted as "confirmed silence". 0.3 s covers the decode
# lag of a growing WebM; the PCM variant reads exact sample positions and
# overrides this default (tools/make_pcm_variant.py).
TURN_TAIL_GUARD_S = float(os.environ.get("TURN_TAIL_GUARD_S", "0.3"))

# KCD-051/052: acoustic echo cancellation + barge-in on the PCM transport. OFF by
# default: the canceller and detector are validated on SYNTHETIC echo only and
# need the pod's real handset audio (tools/echo_eval.py) before anyone relies on
# them. Off, the half-duplex gate behaves exactly as before.
AEC_BARGE_IN = os.environ.get("AEC_BARGE_IN", "0") == "1"
# How far before the detected onset the caller's utterance is taken to start
# (the detector needs ~0.1 s of evidence, so speech began before it fired).
BARGE_IN_PREROLL_S = 0.35

# KCD-513: "always" opens every substantive reply with "Thank you for telling me" (the operator's
# wording, editable from the database); "varied" is the short rotating acknowledgement.
ACK_MODE = os.environ.get("ACK_MODE", "always")
# KCD-495: history is spoken only after the security questions pass. "off" restores the older behaviour
# of offering a person instead of asking (for a deployment that verifies some other way).
SECURITY_QUESTIONS = os.environ.get("SECURITY_QUESTIONS", "on")


def _lookup_verification() -> str:
    """How "what have I booked?" is verified when no confirmation number is given (story "Caller asks
    what they have booked"):

      "security" (the default) -- the history flow's security questions: date of birth or patient ID.
      "name"                   -- the number booked from AND the patient's name on the booking.

    "name" exists because a patient who booked over the phone gave exactly those two things and nothing
    else: no date of birth is ever collected by a phone booking, so on "security" that patient can never
    pass and the lookup is unusable for the very people this agent books. It is the same proof the cancel
    flow already acts on (the number alone, there). Read per call, so a test can set it."""
    return os.environ.get("LOOKUP_VERIFICATION", "security")


LOOKUP_NAME_ASKS = 2  # asked for the patient's name this many times, then the counter / confirmation number is offered
# KCD-047: learn each caller's own pause length as the call goes and wait accordingly.
ADAPTIVE_PAUSE = os.environ.get("ADAPTIVE_PAUSE", "on")
# KCD-053/055: attenuate what is far below the caller's own level and not their voice.
NEAR_END_ATTENTION = os.environ.get("NEAR_END_ATTENTION", "on")

# A test the caller named must match the catalogue at least this well before it is used to look up
# their history; below it the agent ASKS which test (KCD-500: never guess). REASONED.
HISTORY_TEST_MATCH_MIN = 0.85

# KCD-055/057: condition the audio the recogniser hears -- noise suppression (only when
# the clip is noisy, and guarded so it cannot eat the speech) and level normalisation
# with a limiter (agent/conditioning.py). "off" (the default), "level" or "full".
# OFF by default: the proxies it is validated on are synthetic, and whether it helps
# the real recognisers is exactly what tools/conditioning_eval.py measures on a pod.
# The RETRY path (a poor first result on a noisy or faint line) always uses it.
CONDITION_INPUT = os.environ.get("CONDITION_INPUT", "off")

# KCD-461: "a natural filler is spoken when a stage exceeds its
# threshold... silence never exceeds a stated maximum." REASONED, not
# measured against real telephony (no pod to time it against) --
# comfortably above a fast_path/cache hit's near-zero latency and a
# warm LLM call's own "~9s across retries" evidence agent/llm.py's own
# KCD-465 fix cites, short enough that a genuinely slow turn does not
# leave the caller wondering whether the line is still open.
# OWNER'S INSTRUCTION, 2026-09-25: a holding phrase only when the answer is taking MORE THAN 0.7 s, counted from the
# moment the caller stopped speaking (so recognition time counts); an answer that comes sooner has no filler at all.
FILLER_THRESHOLD_S = float(os.environ.get("FILLER_THRESHOLD_S", "0.7"))  # was 2.5 s of silence, then 0.9 s
# ...but the semantic cache is given this long to answer before the model is waited on at all, so a hit is never
# announced by a filler that the answer was about to make pointless.
FILLER_MIN_WAIT_S = float(os.environ.get("FILLER_MIN_WAIT_S", "0.25"))

# OWNER'S INSTRUCTION, 2026-09-25: when the caller says nothing for this long after the agent finished speaking, ask
# once whether there is anything else (the call ends if not); if there is still nothing after SILENCE_CLOSE_S, end it.
SILENCE_PROMPT_S = float(os.environ.get("SILENCE_PROMPT_S", "5"))
SILENCE_CLOSE_S = float(os.environ.get("SILENCE_CLOSE_S", "5"))
# While a QUESTION of ours is open ("which doctor?", "which day?"), silence means the caller is
# thinking, looking for a phone number, asking someone in the room -- not that they are done. Five
# seconds and "anything else? otherwise I'll end the call" is how the live call of 2026-10-06 offered to
# hang up on a caller mid-booking, and five more would have done it. So an open question gets its own,
# longer window, is re-asked gently twice (each prompt different, per the "Caller goes silent" story),
# and only then does the call close -- saying plainly that the task was not completed.
SILENCE_REASK_S = float(os.environ.get("SILENCE_REASK_S", "10"))
# After a LONG LIST (five tests with their prices, a department's doctors) the caller has to take it in and choose: the
# quiet allowance is this much longer before "আপনি কি লাইনে আছেন?" (live 2026-10-14: asked ten seconds after the list).
LIST_REASK_EXTRA_S = float(os.environ.get("LIST_REASK_EXTRA_S", "8"))
QUESTION_SILENCE_PROMPTS = 2

# KCD-076: agent/channel_quality.py's FFT-based classification is pure
# CPU arithmetic over one short clip -- REASONED, not measured against
# Appendix B's real per-call budget breakdown (no live pod to time it
# against right now); generous enough that it should never fire in
# practice on hardware this pipeline already assumes, tight enough that
# a genuinely pathological clip cannot silently eat into the turn.
CHANNEL_QUALITY_BUDGET_S = 0.08

# agent/audio_quality.assess + agent/senior_voice.estimate: FFT/autocorrelation
# over one utterance, run through detector_budget so a pathological clip
# cannot delay the turn. REASONED, not measured on the pod: generous for a
# 3-6 s clip on the hardware this pipeline already assumes.
AUDIO_ANALYSIS_BUDGET_S = 0.5

CLINIC_API_BASE = os.environ.get("CLINIC_API_BASE", "http://localhost:8080")

# Languages the live path routes between. "bn" alone reproduces the original
# Bengali-only behaviour (no LID, no Hindi or English ASR loaded, and deploy/start_all.sh
# starts no English ASR process and tts_server.py loads no Hindi/English voice) -- the
# rollback lever, defined once in agent.lid.parse_active_languages. Bengali is always
# active: it is the clinic's primary language and the greeting language.
ACTIVE_LANGUAGES = parse_active_languages(os.environ.get("VOICE_AGENT_LANGUAGES"))
# Which language a REPLY is spoken in (upgrade prompt sections 6.3 / 6.5; ADR 0002).
#   sticky (default, docs/adr/0003): the language of the caller's FIRST real sentence is the language of the whole
#       call -- reply templates and TTS voice stay in it, whatever language later turns are recognised in. Moved only
#       by an explicit request ("speak in Hindi"). A bare "hello" / "হ্যাঁ" / one word does not fix it
#       (agent.language_policy.establishes_call_language).
#   per_utterance (ADR-0002, still supported): the language THIS utterance was recognised in -- the reply template
#       set and the TTS voice follow the caller turn by turn.
REPLY_LANGUAGE_MODE = os.environ.get("REPLY_LANGUAGE_MODE", "sticky").strip().lower()
ENGLISH_ASR_URL = os.environ.get("ENGLISH_ASR_URL", "http://localhost:8003")
TTS_HEALTH_URL = os.environ.get("TTS_HEALTH_URL", "http://localhost:8002/health")
OLLAMA_HEALTH_URL = os.environ.get("OLLAMA_HEALTH_URL", "http://localhost:11434/api/tags")

# Admission control (agent/admission.py). Cap is per PROCESS: with the WebM
# and PCM services both running, each has its own counter.
ADMISSION_MAX_CALLS = int(os.environ.get("ADMISSION_MAX_CALLS", "28"))
ADMISSION_SHED_P95_S = float(os.environ["ADMISSION_SHED_P95_S"]) if os.environ.get("ADMISSION_SHED_P95_S") else None
ADMISSION_BYPASS_FILE = os.environ.get("ADMISSION_BYPASS_FILE", "/workspace/.ai_bypass")
ADMISSION_ADMIN_TOKEN = os.environ.get("ADMISSION_ADMIN_TOKEN", "")

app = FastAPI()

# KCD-467: "time from process start to ready is measured and bounded."
# Captured at import time -- as close to "process start" as this module
# can observe -- so _startup()'s own duration can be reported once every
# model has answered ready, rather than assumed. See /api/health.
_PROCESS_STARTED_AT = time.monotonic()
_READY_AT: float | None = None  # set once, at the end of _startup()

# ---- process-wide singletons: loaded once, shared by every call ----
_asr: TurnASR | None = None
_turn_detector: TurnDetector | None = None
_tools: ClinicToolsClient | None = None
_tts: TTSClient | None = None
_intent_cache: SemanticCache | None = None
_fast_path: FastPath | None = None
_answer_warm_task = None
_asr_router: ASRRouter | None = None
_lid: SpeechBrainVoxLingua107LID | None = None
_tts_router: TTSRouter | None = None
_admission: AdmissionController | None = None
_health: HealthMonitor | None = None
_health_task: asyncio.Task | None = None
_languages_active: tuple[str, ...] = ("bn",)


class _UnavailableASR:
    """Registered for a language this build does not serve. Fails loudly on
    use (ASRRouter's contract) instead of silently sending Hindi audio to
    the Bengali model."""

    def __init__(self, language: str):
        self.language = language

    async def transcribe_utterance(self, wav_path: str):
        raise UnroutableLanguageError(f"language {self.language!r} is not active in this build")


_fast_path_retry_at = 0.0


async def _warm_speech_models() -> None:
    """Push one real utterance per language through its ASR, and through LID.

    The first real inference on a CUDA model compiles kernels and allocates
    workspaces; measured on the pod, the first turn of the first call took
    ~23 s (three ASRs plus LID, all cold) against ~2 s once warm. A tone or
    silence does NOT do it -- the decode path only runs on speech -- so the
    warm-up speech is the agent's own greeting, synthesized by the TTS."""
    t0 = time.monotonic()
    for lang in _languages_active:
        path = os.path.join(tempfile.gettempdir(), f"kcd_warm_{lang}_{uuid.uuid4().hex[:6]}.wav")
        try:
            wav = await _tts_router.synthesize(lang, phrase("greeting", lang))
            with open(path, "wb") as f:
                f.write(wav)
            await _asr_router.transcribe(lang, path)
            if _lid is not None:
                await asyncio.to_thread(_lid.identify_path, path)
            if lang == "bn":
                # The turn detector and the audio writer are on every call's
                # first-turn path too, and both are lazy: Silero's first
                # inference and torchaudio's first save (torchcodec, loaded
                # from the network volume) are where the first caller's wait
                # went if they are not exercised here.
                import soundfile as sf

                data, sr = await asyncio.to_thread(sf.read, path, dtype="float32", always_2d=True)
                mono = torchaudio.functional.resample(__import__("torch").from_numpy(data.mean(axis=1)), sr, 16000)
                await asyncio.to_thread(_turn_detector.poll, mono, 16000)
                warm_out = path + ".save.wav"
                await asyncio.to_thread(torchaudio.save, warm_out, mono.unsqueeze(0), 16000)
                with contextlib.suppress(OSError):
                    os.remove(warm_out)
        except Exception as e:  # noqa: BLE001 - warmup is advisory
            logger.warning("speech warmup failed for %s: %s", lang, e)
        finally:
            with contextlib.suppress(OSError):
                os.remove(path)
    logger.info("speech models warm (%.1fs)", time.monotonic() - t0)


async def _fetch_catalogue() -> dict:
    """GET /api/v1/catalogue with the service token; raises on any failure or on a body that is not a catalogue."""
    import httpx as _hx

    token = os.environ.get("CLINIC_API_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with _hx.AsyncClient(timeout=10) as c:
        resp = await c.get(f"{CLINIC_API_BASE}/api/v1/catalogue", headers=headers)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict) or "tests" not in payload:
        raise ValueError("clinic-api /api/v1/catalogue did not return a catalogue")
    return payload


# The lay-term fast path (agent/lay_terms.py, below) is a deterministic, rare gate -- not every turn -- but still
# reads the catalogue's sample_type, which the FastPath.Catalogue index does not keep. Cached separately, refetched
# at most every CATALOGUE_CACHE_TTL_S, rather than hitting the network on every matching turn.
CATALOGUE_CACHE_TTL_S = 300.0
_catalogue_cache: dict = {"payload": None, "at": 0.0}


async def _cached_catalogue() -> dict:
    now = time.monotonic()
    if _catalogue_cache["payload"] is None or now - _catalogue_cache["at"] > CATALOGUE_CACHE_TTL_S:
        _catalogue_cache["payload"] = await _fetch_catalogue()
        _catalogue_cache["at"] = now
    return _catalogue_cache["payload"]


# ---- the answers every caller asks for, rendered ahead of time -------------------------------------------------
ANSWER_WARM_INTERVAL_S = float(os.environ.get("ANSWER_WARM_INTERVAL_S", "600"))
_answers_warm: dict = {"rounds": 0, "pinned_clips": 0, "last_round_s": None, "last_error": None}


async def _warm_answers_once() -> int:
    """Render, and pin in the TTS cache, the spoken audio of every test price, every test-preparation answer and every
    clinic FAQ answer, in every active language -- exactly the clauses a live turn would synthesise, so the next caller
    who asks gets them from the cache with no synthesis. The TEXT is still built from live clinic data every time
    (the truth boundary is unchanged: nothing here stores a price as a fact); only the rendered AUDIO is kept, keyed
    by the exact text, so a changed price is simply a different clip. Each round re-reads the catalogue, renders only
    clips it does not already hold, and lets clips for answers that no longer exist become evictable."""
    if _tools is None or _tts is None:
        return 0
    t0 = time.monotonic()
    cat = await _fetch_catalogue()
    keep: set[str] = set()
    rate = derive_policy("neutral")
    for lang in _languages_active:
        replies: list[str] = []
        for row in cat.get("tests", []):
            name = row["name"]
            try:
                replies.append(test_rate_reply({"test_name": name}, await _tools.get_test_rate(name), lang))
                replies.append(test_prep_reply({"test_name": name}, await _tools.get_test_prep(name, lang), lang))
            except ToolCallError as e:
                logger.warning("answer warm-up: lookup for %r failed (%s)", name, e)
        for f in cat.get("faq_topics", []):
            try:
                replies.append(
                    clinic_faq_reply({"faq_topic": f["topic"]}, await _tools.get_faq(f["topic"], lang), lang)
                )
            except ToolCallError as e:
                logger.warning("answer warm-up: FAQ %r failed (%s)", f["topic"], e)
        for reply in replies:
            for clause in split_into_clauses(reply) or [reply]:  # as _finish_enquiry_turn does
                speed = effective_rate(rate, FIGURE_SPEECH_SPEED if contains_critical_figure(clause) else None)
                try:
                    await _tts.synthesize(clause, lang, speed=speed, pin=keep)
                except Exception as e:  # noqa: BLE001 - an unspeakable or failed clip is skipped, never fatal
                    logger.debug("answer warm-up: skipped %r (%s)", clause[:30], e)
                await asyncio.sleep(0)  # never hog the loop while calls are live
    n = _tts.retain_pinned(keep)
    _answers_warm.update(
        rounds=_answers_warm["rounds"] + 1,
        pinned_clips=n,
        last_round_s=round(time.monotonic() - t0, 1),
        last_error=None,
    )
    logger.info("answer warm-up: %d clips pinned in %.1fs", n, time.monotonic() - t0)
    return n


async def _answer_warm_loop() -> None:
    while True:
        try:
            await _warm_answers_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - advisory: a failure only means the next caller pays a synthesis
            _answers_warm["last_error"] = str(e)
            logger.warning("answer warm-up failed: %s", e)
        await asyncio.sleep(ANSWER_WARM_INTERVAL_S)


async def _load_fast_path() -> FastPath | None:
    """Fetch the catalogue and build the fast path, or None if clinic-api is
    not reachable. Optional by design: without it every turn goes to the LLM,
    which is the behaviour that existed before this path did."""
    try:
        payload = await _fetch_catalogue()
        fp = FastPath(Catalogue(payload))
        logger.info("fast path ready over %d catalogue rows", len(fp.catalogue))
        return fp
    except Exception as e:  # noqa: BLE001 - degrade to LLM-only, never fail startup
        logger.warning("catalogue unavailable, fast path disabled: %s", e)
        return None


async def _maybe_reload_fast_path() -> None:
    """If the fast path failed to load at boot (clinic-api was not up yet --
    it starts AFTER this service in start_all.sh), retry at most every 30 s.
    Without this a boot-order race silently costs the fast path until the
    next restart: every turn pays an LLM call and nothing says why."""
    global _fast_path, _fast_path_retry_at
    if _fast_path is not None or time.monotonic() < _fast_path_retry_at:
        return
    _fast_path_retry_at = time.monotonic() + 30.0
    _fast_path = await _load_fast_path()


# How long the startup warm-up may take to get the intent model into VRAM. NOT the per-turn deadline
# (agent.llm.DEFAULT_DEADLINE_S, 12 s, KCD-465): a cold 7B load measured 47-74 s on this pod, so a warm-up under
# the turn deadline times out, the client disconnects, and Ollama abandons the load -- the model never becomes
# resident and the FIRST CALLER pays the cold start against a 12 s deadline (seen on the live pod: "intent model
# warmup failed ... 12.0s deadline" in both entrypoints' logs). REASONED headroom above the measured 74 s.
INTENT_WARMUP_DEADLINE_S = float(os.environ.get("INTENT_WARMUP_DEADLINE_S", "300"))


async def _warm_intent_model() -> bool:
    """Load the intent model now, with a deadline that fits a cold load. Advisory: a failure is logged and
    startup continues (calls then pay the cold start, which is the behaviour without a warm-up)."""
    try:
        _, diag = await asyncio.to_thread(extract_intent, "নমস্কার", 2, "bn", INTENT_WARMUP_DEADLINE_S)
        logger.info("intent model warm (%.1fs)", diag["total_time_s"])
        return True
    except Exception as e:  # noqa: BLE001 - warmup is advisory
        logger.warning("intent model warmup failed: %s", e)
        return False


@app.on_event("startup")
async def _startup():
    global _asr, _turn_detector, _tools, _tts, _intent_cache, _fast_path
    global _asr_router, _lid, _tts_router, _admission, _health, _health_task, _languages_active
    import httpx as _httpx

    active = list(ACTIVE_LANGUAGES)
    engines: dict = {}
    logger.info("loading IndicConformer bn...")
    _asr = await asyncio.to_thread(TurnASR)
    engines["bn"] = _asr
    if "hi" in active:
        logger.info("loading IndicConformer hi...")
        engines["hi"] = await asyncio.to_thread(TurnASR, None, "hi")
    if "en" in active:
        engines["en"] = HTTPASREngine(ENGLISH_ASR_URL)
    for lang in SUPPORTED_LANGUAGES:
        engines.setdefault(lang, _UnavailableASR(lang))
    _asr_router = ASRRouter(engines)

    _lid = None
    if len(active) > 1:
        logger.info("loading language ID (SpeechBrain VoxLingua107, CPU)...")
        try:
            lid = SpeechBrainVoxLingua107LID(device="cpu")
            await asyncio.to_thread(lid.load)
            _lid = lid
        except Exception as e:  # noqa: BLE001 - degrade LOUDLY to Bengali-only, never guess a language
            logger.error("language ID unavailable (%s) -- serving Bengali ONLY until fixed", e)
            active = ["bn"]
    _languages_active = tuple(active)
    logger.info("active languages: %s", ",".join(_languages_active))
    if drafts_enabled():
        logger.warning(
            "PRONUNCIATION_ALLOW_DRAFT=1: UNREVIEWED spoken forms of English clinical terms are being used "
            "(%s). A listening aid, not a release setting -- see agent/pronunciation.review_sheet().",
            pronunciation_coverage(),
        )

    logger.info("loading Silero VAD...")
    _turn_detector = await asyncio.to_thread(TurnDetector, tail_guard_s=TURN_TAIL_GUARD_S)
    _tools = ClinicToolsClient(CLINIC_API_BASE)
    _tts = TTSClient()
    _tts_router = TTSRouter({lang: _tts.for_language(lang) for lang in SUPPORTED_LANGUAGES})
    _intent_cache = SemanticCache()

    _admission = AdmissionController(
        max_calls=ADMISSION_MAX_CALLS,
        latency_shed_p95_s=ADMISSION_SHED_P95_S,
        bypass_file=ADMISSION_BYPASS_FILE or None,
    )
    probe_client = _httpx.AsyncClient()
    probes = {
        "tts": http_probe(probe_client, TTS_HEALTH_URL),
        "clinic-api": http_probe(probe_client, f"{CLINIC_API_BASE}/api/health"),
        "ollama": http_probe(probe_client, OLLAMA_HEALTH_URL),
    }
    if "en" in _languages_active:
        probes["english-asr"] = http_probe(probe_client, f"{ENGLISH_ASR_URL}/health")
    _health = HealthMonitor(_admission, probes)
    _health.probe_client = probe_client
    _health_task = asyncio.create_task(_health.run_forever())

    # Pull bge-m3 into VRAM before the first caller needs it. Cold-loading
    # it inside a live turn measured past the client's patience AND past
    # the embed timeout, which silently degraded the cache to exact-match
    # only for the opening minutes of the process -- healthy-looking logs,
    # zero semantic hits. OLLAMA_KEEP_ALIVE=-1 keeps it resident after.
    try:
        await asyncio.to_thread(_embed_probe, "warmup")
        logger.info("embedding model warm")
    except Exception as e:  # noqa: BLE001 - cache is optional, the call is not
        logger.warning("embedding warmup failed, cache starts L1-only: %s", e)

    # Load Qwen into VRAM now. Cold, it measured 74 s for its first intent --
    # that is the first caller's wait, and it would also poison the latency
    # window admission control sheds load on.
    await _warm_intent_model()

    # Load the 74-row catalogue once so the fast path can identify a test
    # or doctor locally. Optional: if the clinic API is not up yet, every
    # turn simply goes to the LLM, which is the behaviour that existed
    # before this path did.
    _fast_path = await _load_fast_path()

    logger.info("prewarming TTS...")
    lines = prewarm_lines()
    await _tts.prewarm({lang: lines[lang] + [thanks_for(lang)] for lang in _languages_active})
    await _warm_speech_models()

    global _READY_AT, _answer_warm_task
    _READY_AT = time.monotonic()
    _answer_warm_task = asyncio.create_task(_answer_warm_loop())  # background: startup does not wait for it
    logger.info("startup complete -- ready for calls (%.1fs from process start)", _READY_AT - _PROCESS_STARTED_AT)


@app.on_event("shutdown")
async def _shutdown():
    if _answer_warm_task is not None:
        _answer_warm_task.cancel()
    if _health_task:
        _health_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _health_task
    if _health is not None:
        await _health.probe_client.aclose()
    if _asr_router is not None:
        with contextlib.suppress(Exception):
            await _asr_router.engine_for("en").aclose()
    if _tools:
        await _tools.aclose()
    if _tts:
        await _tts.aclose()


@app.get("/api/health")
async def health():
    # KCD-467: "instances receive traffic only once every model answers
    # a probe" -- ASGI/uvicorn already refuses connections until
    # _startup() (which awaits every model load) returns, so `ready`
    # here is never False for a request that could reach this handler
    # at all; it exists so an orchestrator polling this URL through a
    # proxy that itself started early sees an honest, explicit signal
    # rather than inferring readiness from a 200 alone. `startup_duration_s`
    # is the "measured and bounded" half of the acceptance criterion --
    # the actual bound (a deploy pipeline gating traffic on this field
    # rather than on the process merely existing) is an infra concern
    # outside this repository.
    return {
        "status": "ok",
        "ready": _READY_AT is not None,
        "startup_duration_s": round(_READY_AT - _PROCESS_STARTED_AT, 1) if _READY_AT else None,
        "asr_loaded": _asr is not None,
        "clinic_api_base": CLINIC_API_BASE,
        "languages_active": list(_languages_active),
        "lid_loaded": _lid is not None,
        "admission_open": bool(_admission and _admission.snapshot()["open"]),
    }


@app.get("/api/admission")
async def admission_status():
    """Read-only: safe to leave unauthenticated behind the private network."""
    return _admission.snapshot() if _admission else {"open": False, "closed_reason": "starting"}


class _BypassRequest(BaseModel):
    enabled: bool


@app.post("/api/admission/bypass")
async def admission_bypass(req: _BypassRequest, x_admin_token: str = Header(default="")):
    """The audited AI kill-switch: enabled=true sends every NEW call to the
    human contact centre without a deploy. Disabled entirely (503) unless
    ADMISSION_ADMIN_TOKEN is set -- an unauthenticated switch that can take
    the whole service offline is worse than none."""
    if not ADMISSION_ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="bypass endpoint disabled: no admin token configured")
    if not secrets.compare_digest(x_admin_token, ADMISSION_ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="bad admin token")
    _admission.set_bypass(req.enabled, actor="http")
    return _admission.snapshot()


@app.get("/api/stats")
async def stats():
    """Cache effectiveness, for tuning the similarity threshold against
    real traffic rather than against my assumptions about it."""
    return {
        "fast_path": _fast_path.snapshot() if _fast_path else None,
        "answer_warmup": _answers_warm,
        "intent_cache": _intent_cache.snapshot() if _intent_cache else None,
        "tts_cache": _tts.snapshot() if _tts else None,
        "admission": _admission.snapshot() if _admission else None,
        "insufficient_information": insufficient_information.snapshot(),
        "code_switch_buckets": code_switch_buckets.snapshot(),
        "channel_quality_buckets": channel_quality_buckets.snapshot(),
        "detector_budget": detector_budget_snapshot(),
        "turn_latency_by_language": turn_latency_by_language.snapshot(),
        "barge_in_interrupts": barge_in_interrupts.snapshot(),
        "reask_outcomes": reask_outcomes.snapshot(),
        "audio_issue_buckets": audio_issue_buckets.snapshot(),
        "golden_buckets": golden_buckets.snapshot(),
        "apology_events": apology_events.snapshot(),
        "language_mismatches": language_mismatches.snapshot(),
        "senior_detections": senior_detections.snapshot(),
        "unrelated_caller": unrelated_caller_events.snapshot(),  # ADDED BY SOURAV: story "Caller has reached the hospital by mistake"
    }


def _wav_duration_s(wav_bytes: bytes) -> float:
    try:
        with contextlib.closing(wave.open(io.BytesIO(wav_bytes), "rb")) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:  # noqa: BLE001 - a fallback clip may not be canonical WAV
        return 5.0


def _classify_channel_from_wav_path(path: str) -> str:
    """KCD-075's sync (CPU-bound) half -- run through
    agent.detector_budget.run_within_budget, never called directly, so a
    pathological clip cannot delay the turn past its budget slice."""
    with contextlib.closing(wave.open(path, "rb")) as w:
        raw = w.readframes(w.getnframes())
        sample_rate = w.getframerate()
        sampwidth = w.getsampwidth()
    if sampwidth != 2:
        # This pipeline only ever produces 16-bit PCM clips (torchaudio.save's
        # default); anything else is unexpected input this classifier was not
        # built to read faithfully, so default to the least alarming label
        # rather than mis-parse it into a bogus one.
        return CHANNEL_CLEAN_16K
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    return classify_channel(samples, sample_rate)


def _read_wav_mono(path: str) -> tuple[np.ndarray, int]:
    with contextlib.closing(wave.open(path, "rb")) as w:
        raw, sr, width, channels = w.readframes(w.getnframes()), w.getframerate(), w.getsampwidth(), w.getnchannels()
    if width != 2:
        raise ValueError(f"unsupported sample width {width}")
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return (x.reshape(-1, channels).mean(axis=1) if channels > 1 else x), sr


def _analyze_utterance_from_wav_path(path: str) -> dict:
    """agent/audio_quality.assess (why this clip is hard to hear) plus
    agent/senior_voice.estimate (does the speaker sound older), on one
    read of the clip. Sync and CPU-bound: only ever called through
    detector_budget.run_within_budget."""
    samples, sr = _read_wav_mono(path)
    level_dbfs, f0_hz = level_and_pitch(samples, sr)
    return {
        "audio": assess_audio(samples, sr),
        "senior": estimate_senior(samples, sr),
        "near_end": (level_dbfs, f0_hz),
        "voice": voice_embedding(samples, sr),
    }


def _enhance_wav_to_path(path: str) -> str | None:
    """A noise-filtered, level-corrected copy of the clip for a retry, or
    None if it cannot be produced."""
    try:
        samples, sr = _read_wav_mono(path)
        out, _report = condition_audio(samples, sr, suppress="always")
        enhanced = path + ".enh.wav"
        with contextlib.closing(wave.open(enhanced, "wb")) as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((np.clip(out, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes())
        return enhanced
    except Exception as e:  # noqa: BLE001 - enhancement is an optional retry, never a failure
        logger.warning("enhance failed: %s", e)
        return None


async def _decode_to_wav(raw_path: str, wav_path: str) -> bool:
    """Re-decode the WHOLE growing webm buffer every poll -- same choice
    server.py makes for the consultation recorder, and for the same reason:
    MediaRecorder only puts the container header on the FIRST chunk, so
    later chunks are not independently decodable, and re-decoding a few
    seconds of audio is cheap next to ASR+LLM+TTS.

    NOTE the cost profile: this is O(call length) on every poll, so the
    work per poll grows for the whole duration of a call. Fine for a
    handful of concurrent bench calls; it is the first thing that has to
    change for real concurrency (see README.md "Scaling")."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        raw_path,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        wav_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode == 0 and os.path.exists(wav_path) and os.path.getsize(wav_path) > 44


def _attend_wav_to_path(path: str, level_dbfs: float, f0_hz: float) -> str | None:
    """A copy of the clip with the room attenuated (agent/attention.py), or None if nothing was
    changed or it could not be produced. Never raises."""
    try:
        samples, sr = _read_wav_mono(path)
        out, report = attend(samples, level_dbfs, f0_hz, sr)
        if not report.applied:
            return None
        cleaned = path + ".att.wav"
        with contextlib.closing(wave.open(cleaned, "wb")) as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((np.clip(out, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes())
        golden_buckets.record("attention", "applied", "any")
        return cleaned
    except Exception as e:  # noqa: BLE001 - attention is an optional cleanup, never a failure
        logger.warning("attention failed: %s", e)
        return None


def _condition_wav_to_path(path: str, mode: str) -> str | None:
    """A conditioned COPY of the clip for the recogniser (KCD-055/057), or None. The
    original is left alone: audio analysis, senior and near-end judgements are made on
    what the caller actually sounded like, not on what was done to help the ASR."""
    try:
        samples, sr = _read_wav_mono(path)
        out, _report = condition_audio(samples, sr, suppress="never" if mode == "level" else "auto")
        conditioned = path + ".cond.wav"
        with contextlib.closing(wave.open(conditioned, "wb")) as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((np.clip(out, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes())
        return conditioned
    except Exception as e:  # noqa: BLE001 - conditioning is an enhancement, never a failure
        logger.warning("input conditioning failed: %s", e)
        return None


class CallSession:
    """One raw/decoded buffer for the ENTIRE call, not one per turn.

    An earlier version deleted the buffer and started a fresh file after
    every completed turn. That broke on real testing: MediaRecorder only
    puts the WebM container's header in the very first chunk of the whole
    recording session -- every chunk after a mid-call "reset" was being
    appended to a file that could never be decoded, because its one valid
    header lived in the turn-1 file that had already been deleted. Every
    turn after the first silently failed to decode, forever, for the rest
    of the call.

    Fixed the same way voice-to-rx-repo/server.py already had to: keep
    ONE continuous file for the whole call and track `processed_until_s`
    -- a marker for how much of it a prior turn has already consumed.
    Each poll only ever looks at the UNPROCESSED TAIL past that marker.
    """

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.call_id = uuid.uuid4().hex[:8]
        self.tmpdir = tempfile.mkdtemp(prefix=f"kcd_call_{self.call_id}_")
        self.last_activity = time.time()
        self.dispatch_lock = asyncio.Lock()
        self.processed_until_s = 0.0
        self.utt_seq = 0
        self.last_heartbeat = time.time()
        self.raw_path = os.path.join(self.tmpdir, "call.webm")
        self.wav_path = self.raw_path + ".wav"
        open(self.raw_path, "wb").close()

        # Starts True: the greeting goes out before the caller has said
        # anything, so the gate must already be closed when the first poll
        # tick runs, not opened a moment later by _speak().
        self.gate = PlaybackGate(PLAYBACK_GUARD_S)

        # KCD-049/048: when the poll loop next wakes, and the speculative
        # transcript state for the candidate turn end currently in view.
        self.next_wake_s = POLL_INTERVAL_S
        self.spec_end_abs: float | None = None
        self.spec_verdict: str | None = None
        self.spec_count = 0

        # KCD-052/163: an interrupt bumps speak_epoch; the turn in flight keeps
        # the epoch it started under, so everything it has yet to say is
        # discarded rather than played over the caller.
        self.speak_epoch = 0
        self.turn_epoch = 0
        # KCD-051: set by the PCM variant when AEC_BARGE_IN is on.
        # (the PCM variant assigns it earlier in __init__ when AEC_BARGE_IN is on)
        self.duplex: FullDuplexProcessor | None = getattr(self, "duplex", None)

        # `lang` is the language the LAST reply was spoken in. By default (REPLY_LANGUAGE_MODE=per_utterance,
        # docs/adr/0002) the next reply follows the language THIS utterance was recognised in -- recognition and
        # reply both go turn by turn, so a caller who switches language is answered in it, and one who says a
        # single borrowed word is not switched, because the recogniser only leaves the call's language when
        # language ID believes the new one (agent/lang_select.SWITCH_MIN_LID). In the legacy REPLY_LANGUAGE_MODE=
        # sticky it is locked to the first language of the call (KCD-lay-terms, a live call 2026-09-28) and moves
        # only on an explicit request ("speak in Hindi" -- agent/language_switch.py). Either way
        # _resolve_reply_language below is the one place the decision is made.
        self.lang = "bn"
        self.lang_locked = False
        self.lang_locked_before_turn = False  # what lang_locked was when the turn in progress began (_keep_language)
        self.lang_router = ASRLanguageRouter()
        # Language ID started on the first words of the turn in progress (agent/early_lid.py), if any.
        self.early_lid: early_lid.EarlyLID | None = None
        self.turn_started_at: float | None = None
        self.admission = None

        # Epic E26: the in-progress booking/reschedule/cancel/add-test flow
        # for this call, if any -- see agent/booking_flow.py. One at a time;
        # a caller starting a second booking mid-flow replaces it (their
        # most recent words win, same principle as merge_slots' overwrite
        # rule). No real telephony CallerID exists yet (see HANDOVER.md /
        # CLAUDE.md's "STILL NOT BUILT" notes), so there is no independent
        # "number this call came from" distinct from whatever contact phone
        # the caller states -- booking_service's proxy-authorisation model
        # is real, but is only as strong as a phone-based system without
        # SIP CallerID can be until that lands.
        self.booking: BookingState | None = None

        # KCD-395/KCD-396: what the last answered enquiry turn(s) were
        # about (agent/enquiry_followup.EnquiryEntity), so a bare
        # elliptical follow-up ("and the sample for that?") can resolve
        # without asking the caller to repeat the test/doctor name.
        # Booking turns neither read nor write this -- it is scoped to
        # the stateless enquiry intents only.
        self.last_enquiry_entities: list = []

        # KCD-061: the single, versioned carrier of caller signals
        # (Blueprint 4.5) -- language and confirmation_required are kept
        # current every turn (see _dispatch_turn); every other field
        # stays at its neutral default until a real caller-state
        # detector exists to drive it (see agent/call_state.py's own
        # docstring).
        self.call_state: CallState = new_call_state()

        # Call intelligence (KCD-084/149/155): the empathetic re-ask tracker,
        # the older-caller evidence accumulator, and bookkeeping so an
        # acknowledgement is spoken once per state entry, not every turn.
        self.reask = ReaskTracker()
        self.senior_evidence = SeniorEvidence()
        self.acknowledged_state: str | None = None
        self.senior_lookup_done = False

        # KCD-513/514: acknowledgements are suppressed on repeat turns; KCD-155's distress
        # acknowledgement, if spoken this turn, already opened it.
        self.acks = AckTracker(mode=ACK_MODE)
        self.turn_acknowledged = False
        # The agent's last utterance asked something (it contained a question mark), and whether THIS turn is the
        # caller's answer to it: only an answer to our question is thanked ("Thank you."), never a question of theirs.
        self.awaiting_answer = False
        self.answering = False
        # Silence handling (SILENCE_PROMPT_S): when the quiet started, how many times we have asked, and whether the
        # caller's next words answer "anything else?".
        self.silence_since: float | None = None
        self.silence_prompts = 0
        self.awaiting_close_answer = False
        # The question the caller has not yet answered, as (text, lang), so silence can re-ask IT instead of
        # offering to end the call; and how many times silence has re-asked it (SILENCE_REASK_S).
        self.open_question: tuple[str, str] | None = None
        self.question_silence_prompts = 0
        self.open_question_extra_s = 0.0  # a long list's extra quiet allowance (LIST_REASK_EXTRA_S)
        self.abuse_count = 0  # abusive turns so far this call (agent/abuse.py)
        # ADDED BY SOURAV: story "Caller has reached the hospital by mistake". `unrelated_count` = CONSECUTIVE turns judged unrelated
        # (it is zeroed at the top of every processed turn and only _handle_unrelated_caller raises it again, so any other kind of
        # turn resets it); `unrelated_prev` = its value going INTO the current turn; `unrelated_flow_active` = a hospital task or
        # question was already in progress when this turn began (snapshot taken before the flow gates consume their flags). Kept
        # apart from `abuse_count` on purpose: two different counters with two different closing rules are never merged.
        self.unrelated_count = 0
        self.unrelated_prev = 0
        self.unrelated_flow_active = False
        # The implicit happiness score (agent/call_score.py): counters updated as the call goes, scored once at hang-up.
        self.signals = CallSignals()
        self.reply_noted = False  # this turn's time-to-first-audio has been counted
        self.speaking_filler = False  # the "achha bolchi" filler is being spoken: not the answer
        self.lang_history: list[str] = []  # the language of each accepted turn, to see a flip that is flipped back
        # KCD-353: the disclosure was spoken in the greeting (Bengali); once more, in the
        # caller's own language, the first time it differs.
        self.disclosed_langs: set[str] = {"bn"}
        # KCD-053: who this call's near-end speaker is (level, pitch), to tell them from a
        # bystander. KCD-054/495: what we actually know about who is on the call, and the
        # voice reference that lets a change of speaker revoke a verification.
        self.near_end = NearEndProfile()
        self.identity = IdentityState()
        self.speaker = SpeakerChangeDetector()

        # KCD-501: the call's record, written as the call goes and closed at hang-up.
        self.recorder: CallRecorder | None = None
        self.outcome = "completed"
        self.turn_count = 0
        # KCD-494/495/498: a question about the caller's own history is a small state machine --
        # find the record (an OPEN question when the number is shared), then, only if a
        # verification has passed, answer from retrieved fields.
        self.history_state: str | None = None  # None | need_phone | need_name | need_test
        self.pending_history = None  # the HistoryQuestion being answered
        self.history_attempts = 0
        self.awaiting_handoff_offer = False
        # Ported story "Caller asks whether their result is dangerous" (agent/clinical_safety.py): the
        # reply offers a clinician and the caller answers on the NEXT turn, the same shape as
        # awaiting_handoff_offer just above. Its own flag rather than reusing that one, because that one
        # is answered by _continue_history_flow() and hands off with the reason "history_requested_staff"
        # -- a clinical question is not a history request, and a shared flag would file it as one.
        self.awaiting_clinician_offer = False
        # Which story made that offer, so the hand-off is filed under the right reason. Defaults to the
        # clinical one because that is the story the flag was introduced for; the unverifiable-claim guard
        # sets its own. Kept beside the flag rather than becoming a third awaiting_* field.
        self.clinician_offer_reason = "clinical_interpretation"
        # Ported story "Caller asks to be called back": set when a callback was asked for and no number is
        # known yet, so the NEXT turn is read for digits (agent/spoken_codes.parse_phone).
        self.awaiting_callback_phone = False
        # A factual lookup held back until the caller confirms what the recogniser (with nothing to
        # check it against) heard: agent/entity_confirmation.py.
        self.pending_entity: entity_text.PendingEntity | None = None
        # (utterance, clinic-api's /doctors/mentioned answer): asked at most once per utterance (_doctors_mentioned).
        self.mention_cache: tuple[str, dict | None] | None = None
        # KCD-495/497: the security questions in progress, and the senior-care planner (KCD-512).
        self.security: SecurityCheck | None = None
        self.kindness = KindnessPlanner()
        # KCD-496: an unfinished booking from an earlier call, offered after verification.
        self.pending_draft: dict | None = None
        self.awaiting_draft_answer = False
        # KCD-104: a booking set aside while the caller did a different task, and whether we are waiting for their
        # yes or no to "shall I go back to it".
        self.suspended = None
        self.awaiting_resume = False
        # Where the read-out of the clinic's tests stopped ("shall I go on?"), or None when nothing is being read.
        self.test_list_offset: int | None = None
        # (offset, unplaced turns so far) taken off test_list_offset by _continue_test_list on a turn that was not
        # "more" / yes / no / "again"; put back ONLY if the turn then turns out to be one the model cannot place
        # (the "unclear" branch of _dispatch_turn), so a garbled "go on" does not end the list. Cleared every turn.
        self.test_list_stash: tuple[int, int] | None = None
        self.test_list_strikes = 0  # turns the model could not place while the list has been open
        # (offset, turn it was parked on): the list set aside while the caller asks about ONE test from it; an
        # unambiguous "go on" within TEST_LIST_PARK_TURNS turns picks it up again (_resume_parked_test_list)
        self.test_list_parked: tuple[int, int] | None = None
        # (canonical names, turn) of the test GROUP the agent last read out ("হার্টের টেস্ট আমাদের এখানে ৩টা আছে …"):
        # the caller's next words are a pick among those, said short ("ইকো") or run together ("ইসিজিয়ার ইকো").
        self.group_members: tuple[tuple[str, ...], int, dict] | None = None
        self.group_reasked = False
        # The acceptance layer (agent/entity_grounding.py): tests read back and waiting for the caller's yes,
        # and the "which tests?" loop they land in on a no. Nothing is priced while either is set.
        self.pending_tests: dict | None = None
        # the tests whose acronyms the ENGLISH recogniser heard spelled letter by letter in this turn's audio, where the
        # Bengali / Hindi one wrote pseudo-words (agent/spelled_letters.py); and the English reading itself, in flight
        self.turn_acronyms: tuple[str, ...] = ()
        self.turn_en_task: asyncio.Future | None = None
        self.awaiting_tests: str | None = None
        self.pending_enquiry = None  # (intent, turn) when we just asked "which test / which doctor"
        # The doctors of a department the agent just read out ("... কার কাছে অ্যাপয়েন্টমেন্ট নিতে চান?") with the turn it
        # was said on: (canonical names, turn_count, the doctors' listing rows). The next turn naming one of them -- or
        # "the second one" -- is the answer to that question.
        self.offered_doctors: tuple[list[str], int, list[dict]] | None = None
        self.last_enquiry_turn = 0  # turn_count when the last test/doctor answer was given (agent/enquiry_followup.py)
        self.no_at_confirm = False  # the caller said "no, ..." at the confirmation step and it was not a bare no
        # KCD-382b: the registered/booked-patient preparation flow. Asked at most once per call
        # (test_prep_registration_asked); the two "what are we waiting for next" flags are answered
        # deterministically, the same shape awaiting_callback_phone above already uses, never by the model.
        self.test_prep_registration_asked = False
        self.awaiting_test_prep_registration_answer = False
        self.awaiting_test_prep_booking_phone = False
        # The test name(s) last resolved for PREPARATION in this call -- named directly and merged (KCD-382),
        # or retrieved from a registered/booking lookup (KCD-382b). NOT general conversation memory: only ever
        # a resolved test-name set, read back by a same-call follow-up that names no test, within the same
        # recency window agent/enquiry_followup.py already uses for a single test name (FOLLOWUP_MAX_GAP).
        self.last_resolved_prep_tests: tuple[str, ...] | None = None
        self.last_resolved_prep_turn = 0
        # ADDED BY SOURAV: KCD-387 full flow -- the in-progress home-collection enquiry/booking for
        # this call, if any. Mirrors self.booking's "one at a time, most recent words win"
        # convention (see its own comment above), but is deliberately NOT integrated with KCD-104's
        # suspend/resume machinery (self.suspended/self.awaiting_resume) -- this story does not
        # change what happens when an appointment/test booking is interrupted, and does not need to
        # participate in that system to satisfy its own requirements. See agent/home_collection_flow.py.
        self.home_collection: hcflow.HomeCollectionState | None = None
        # The same KCD-382b "are you already booked for this?" yes/no gate and digits-only phone
        # follow-up as test_prep_registration_asked/awaiting_test_prep_* above, applied to the home-
        # collection flow's own test-identification step -- never a second, competing implementation
        # of the same idea.
        self.awaiting_home_collection_registration_answer = False
        self.awaiting_home_collection_booking_phone = False
        # "Patient name is misheard": a name captured on a turn nothing vouches for is read back
        # at once, before four more questions are answered under it.
        self.awaiting_name_confirm = False
        # "Caller says tomorrow, day after, or next Monday": the enquiry waiting on a date the
        # caller has yet to confirm, as (intent, slots, date).
        self.pending_dated_enquiry: tuple | None = None
        # story: "Caller asks what they have booked"
        #   pending_resend   the confirmation_id just offered, so a bare "হ্যাঁ" next turn
        #                    accepts the offer instead of being heard as a fresh request
        #   pending_lookup   the lookup to resume once the caller passes the security questions
        self.pending_resend: str | None = None
        self.pending_lookup: dict | None = None
        # The booking last read out to this caller ("what have I booked?", or found by number for a move or a
        # cancel): "move it to Saturday" / "cancel it" straight after acts on THAT booking, without asking for
        # its confirmation number again. Set only from a lookup that passed its own check.
        self.fetched_booking: dict | None = None
        # KCD-499: stored preferences, offered after the answer and applied only if the caller says yes.
        self.pending_pref = None
        self.awaiting_pref_answer = False
        self.confirmed_preferences: dict = {}
        # KCD-047: this caller's own pause rhythm, and the evidence for "that cut was too early".
        self.pause = PauseProfile()
        self.last_commit_end_abs: float | None = None
        self.last_confirm_s = 0.0
        self.agent_spoke_since_commit = False
        self.commit_checked = True

    @property
    def policy(self):
        """Appendix C's delivery parameters for the caller's CURRENT state
        (agent/speech_policy.py) -- derived, never stored, so it can never
        disagree with call_state."""
        return derive_policy(self.call_state.caller_state, self.call_state.senior)

    # The gate itself lives in agent/playback_gate.py (KCD-050) so its rules can be
    # tested without a pod; these keep the names the rest of this file uses.
    @property
    def agent_speaking(self) -> bool:
        return self.gate.speaking

    @property
    def speak_deadline(self) -> float:
        return self.gate.deadline

    @property
    def resync_pending(self) -> bool:
        return self.gate.resync_pending

    @resync_pending.setter
    def resync_pending(self, value: bool) -> None:
        self.gate.resync_pending = value

    def hold_gate_for(self, audio_duration_s: float):
        """Called before each reply goes out. Extends rather than replaces
        the deadline: replies queue on the client, so a second clip starts
        playing only after the first finishes."""
        self.gate.hold(audio_duration_s)

    def release_gate(self):
        """Playback is over. Don't touch processed_until_s here -- the poll
        loop owns the decoded buffer and does the resync on its next tick."""
        self.gate.release()

    def register_agent_audio(self, wav_bytes: bytes, voice: str | None = None) -> None:
        """KCD-051: hand the clip about to be sent to the echo canceller as its
        far-end reference. A no-op unless the PCM variant enabled AEC."""
        if self.duplex is None or not wav_bytes:
            return
        try:
            self.duplex.place_reference(wav_to_pcm16k(wav_bytes), voice=voice)
        except Exception as e:  # noqa: BLE001 - AEC is an enhancement, never a reason to drop a reply
            logger.warning("[%s] could not register agent audio for echo cancellation: %s", self.call_id, e)

    def take_wake_delay(self) -> float:
        """Seconds to sleep before the next poll; the schedule resets to the
        idle cadence unless the last decision said otherwise."""
        delay, self.next_wake_s = self.next_wake_s, POLL_INTERVAL_S
        return delay

    def reset_speculation(self) -> None:
        self.spec_end_abs, self.spec_verdict, self.spec_count = None, None, 0

    async def append(self, chunk: bytes):
        self.last_activity = time.time()
        with open(self.raw_path, "ab") as f:
            f.write(chunk)

    async def send_json(self, sender: str, text: str):
        await self.ws.send_text(json.dumps({"sender": sender, "text": text}, ensure_ascii=False))

    async def send_audio(self, wav_bytes: bytes):
        if wav_bytes:
            await self.ws.send_bytes(wav_bytes)

    def cleanup(self):
        import shutil

        with contextlib.suppress(OSError):
            shutil.rmtree(self.tmpdir, ignore_errors=True)


async def _synthesize_one_clause(session: CallSession, clause: str, lang: str, fallback_reason: str | None) -> bytes:
    # KCD-149/157: the caller's policy rate, and slower still for a price,
    # phone number or reference (the two multiply).
    speed = effective_rate(session.policy, FIGURE_SPEECH_SPEED if contains_critical_figure(clause) else None)
    try:
        return await _tts_router.synthesize(lang, clause, speed=speed)
    except UnspeakableTextError as e:
        # KCD-455: a production occurrence is an alert, not routine
        # noise -- distinct log level and reason from an ordinary TTS
        # infra failure below, even though both fall back to the same
        # pre-recorded apology.
        logger.error("[%s] reply blocked, unspeakable spans %s in %r", session.call_id, e.spans, clause)
        return _tts.fallback_audio(fallback_reason or "tts_failure", lang)
    except Exception as e:  # noqa: BLE001 - TTS is the last mile, must not raise past here
        logger.warning("[%s] TTS failed (%s) -- using fallback audio", session.call_id, e)
        return _tts.fallback_audio(fallback_reason or "tts_failure", lang)


async def _speak(session: CallSession, text: str, lang: str | None = None, fallback_reason: str | None = None) -> float:
    """Speak `text` in `lang` (default: the call's current language).
    Returns the total audio duration in seconds.

    KCD-456: automatically slower for a reply carrying a price, phone
    number or reference ID (agent.speech_norm.contains_critical_figure)
    -- decided here, once, rather than at every call site that happens to
    produce such a reply, so no future reply_templates.py addition can
    forget to ask for it.

    KCD-462: a long reply (agent.clause_split.split_into_clauses) is
    synthesized and sent ONE CLAUSE AT A TIME instead of as one TTS call
    for the whole text -- the first clause reaches the caller as soon as
    its own (much shorter) synthesis finishes, not after the entire
    reply renders. A short reply (the common case) comes back as a
    single-item list and behaves exactly as before -- one TTS call, one
    clip. session.hold_gate_for already extends cumulatively for
    multiple clips in a row (see its own docstring), which is what a
    correction-acknowledgement-then-readback turn already relies on
    elsewhere in this file, so streaming several clips here is the same,
    already-proven pattern, not a new one."""
    lang = lang or session.lang
    if fallback_reason in ("llm_failure", "tool_failure"):
        _note(session, "system_failures")
    session.agent_spoke_since_commit = True
    if session.speak_epoch != session.turn_epoch:
        # The caller interrupted the turn this reply belongs to: it is stale, and
        # playing it over them is exactly what barge-in exists to stop.
        logger.info("[%s] reply discarded after interrupt", session.call_id)
        return 0.0
    if "?" in text or "\uff1f" in text:
        session.awaiting_answer = True
        session.open_question = (text, lang or session.lang)
        session.open_question_extra_s = 0.0  # _speak_list adds its allowance after this
    policy = session.policy
    if not policy.emergency and policy.questions_per_turn >= 1:
        # KCD-084/149: one question at a time. Only a SURPLUS question is
        # ever dropped, never a statement, so no fact is lost.
        text = limit_questions(text, policy.questions_per_turn)
    if language_mismatch(lang, text):
        # KCD-087: a reply in a different language from the caller's is a
        # defect, made visible rather than discovered on a live call.
        language_mismatches.record("reply", "mismatch", lang)
        logger.error("[%s] reply language mismatch (expected %s): %r", session.call_id, lang, text[:60])
    # KCD-514: at most one apology per reply. Templates are written to that rule; this catches
    # the ones assembled at run time (a secondary answer appended to a primary one).
    text, stacked = enforce_single_apology(text, lang)
    if stacked:
        apology_events.record("stacked_removed", "reply", lang)
    await session.send_json("AI", text)
    clauses = split_into_clauses(text) or [text]

    total_duration = 0.0
    for i, clause in enumerate(clauses):
        if session.speak_epoch != session.turn_epoch:
            break  # interrupted mid-reply: the rest is discarded, not sent
        # A live call (2026-09-28): the filler ("please hold") is itself spoken through this same
        # function while the REAL answer is still being resolved (see _await_with_filler). Marking
        # and logging it here used to consume and clear session.marks (_mark is first-occurrence-only,
        # _log_timing clears the list) before the real answer's own "reply"/"tts"/"send" marks could
        # ever be recorded -- so on exactly the slow turns worth measuring, the turn-timing log went
        # dark right after the filler and the actual delay (the thing a caller waited through) was
        # never logged at all. The filler's own latency is not interesting (it is pre-cached, near-
        # instant); skip marking it so the marks recorded so far (lid/prep/asr) survive to be logged
        # against the real reply that follows.
        if not session.speaking_filler:
            _mark(session, "reply")
        wav = await _synthesize_one_clause(session, clause, lang, fallback_reason)
        if not session.speaking_filler:
            _mark(session, "tts")
        if session.speak_epoch != session.turn_epoch:
            break  # interrupted while this clause was being synthesised

        # Close the gate BEFORE the bytes leave, never after: the client
        # can start playing the moment they land, and a poll tick that
        # slips in between send and gate is exactly the echo this
        # prevents.
        duration = _wav_duration_s(wav)
        session.hold_gate_for(duration)
        session.register_agent_audio(wav, voice=lang)
        await session.send_audio(wav)
        total_duration += duration
        if i == 0:
            if not session.speaking_filler:
                _mark(session, "send")
                _log_timing(session)
            if not session.reply_noted and not session.speaking_filler and session.turn_started_at is not None:
                session.reply_noted = True
                session.signals.note_reply(time.monotonic() - session.turn_started_at)

        # "Time to first audio" (admission.py's own docstring) means the
        # FIRST clause, not the last -- recorded once, right after it is
        # sent, so streaming a long reply is measured (and shows up as
        # faster) rather than averaged against the clauses still to come.
        if i == 0 and session.turn_started_at is not None and _admission is not None:
            turn_latency_s = time.monotonic() - session.turn_started_at
            _admission.record_turn_latency(turn_latency_s)
            # KCD-469: the SAME measurement, also broken down per
            # language -- admission's own p50/p95 stay a single
            # aggregate signal by design (see its module docstring), so
            # a language-specific regression could hide inside a
            # healthy-looking aggregate without this.
            turn_latency_by_language.record(lang, turn_latency_s)
            session.turn_started_at = None

    return total_duration


async def _queue_callback_request(session: CallSession, phone: str, lang: str) -> None:
    """Record a caller's own request to be rung back, then say so.

    Ported story "Caller asks to be called back". The reason is built by
    agent/callback_request.build_callback_reason(), which prefers the caller's
    own words and otherwise records nothing rather than inventing a generic
    "general enquiry". The call summary rides along exactly as it does for a
    hand-off, so the colleague who makes the call has the context beside the
    number.

    A storage failure never costs the caller their answer: it is suppressed the
    same way every other durable write on a spoken path here is, and the spoken
    line only claims the number was noted, which is true either way.
    """
    reason = build_callback_reason(None) or "caller_requested_callback"
    summary = build_call_summary("callback_requested", lang=lang, phone=phone, call_id=session.call_id)
    with contextlib.suppress(ToolCallError):
        await _tools.request_callback(phone, session.call_id, "asap", reason, summary)
    _rec(session, "escalation", "callback_requested")
    await _speak(session, phrase("callback_noted", lang), lang)


async def _handoff_to_human(session: CallSession, reason: str, languages: tuple[str, ...] | None = None):
    """Tell the caller and the telephony bridge that a person is taking over.

    The `handoff_human` frame is the machine-readable half: the SBC/bridge
    that owns the SIP leg acts on it (this repo has no SIP transfer). The
    spoken notice is the human-readable half, said in every language when
    the caller's is not yet known.

    Also QUEUES a callback (clinic-api/enquiry_service.request_callback) whenever a phone is already known
    (session.identity.phone) -- this is what makes a handoff (including the reask policy's own 3rd-failure handoff,
    agent/reask_policy.DEFAULT_MAX_REASKS) an actual actionable queue entry for a human, not only a spoken notice and
    a websocket frame nothing durable is kept of. Without a phone there is nothing to call back on (no automatic
    caller-ID exists in this stack), so nothing is queued -- silently, never a spoken complaint about it.
    """
    logger.warning("[%s] handoff to human: %s", session.call_id, reason)
    _note(
        session,
        "emergency"
        if reason == "emergency"
        else "asked_for_person"
        if reason in ("caller_requested", "history_requested_staff")
        else "system_handoff",
    )
    session.outcome = "handed_off"
    _rec(session, "escalation", reason)
    phone = session.identity.phone
    if phone:
        summary = build_call_summary(f"handoff:{reason}", lang=session.lang, phone=phone, call_id=session.call_id)
        with contextlib.suppress(ToolCallError):
            await _tools.request_callback(phone, session.call_id, "asap", f"handoff:{reason}", summary)
    with contextlib.suppress(Exception):
        await session.ws.send_text(json.dumps({"type": "handoff_human", "reason": reason}))
    total = 0.0
    for lang in languages or (session.lang,):
        with contextlib.suppress(Exception):
            total += await _speak(session, phrase("handoff", lang), lang)
    # Let the notice finish playing before the socket closes under it.
    await asyncio.sleep(min(total + 0.5, 20.0))
    with contextlib.suppress(Exception):
        await session.ws.close()


async def _reask_or_handoff(
    session: CallSession,
    decision,
    lang: str,
    fallback_reason: str | None = None,
    languages: tuple[str, ...] | None = None,
) -> None:
    """Ask again, kindly, before routing to a person (agent/reask_policy.py).
    A second consecutive failure also marks the caller "confused", which
    slows the agent and lowers its escalation threshold for the rest of the
    call (Appendix C)."""
    reask_outcomes.record(decision.reason or "unclear", decision.action, lang)
    _note(session, "reasks")
    if decision.mark_confused:
        apply_caller_state(session.call_state, caller_state="confused")
    # `languages` is passed when the caller's language could not be identified:
    # the re-ask and the hand-off notice are then said in EVERY active language,
    # not in whichever one the call happened to default to.
    langs = languages or (lang,)
    if decision.action == "handoff":
        for lg in langs:
            await _speak(session, phrase("reask_final", lg), lg)
        await _handoff_to_human(session, f"unintelligible:{decision.reason}", langs)
        return
    for lg in langs:
        text = phrase(decision.phrase_key, lg)
        patience = session.kindness.patience(lg)
        await _speak(session, f"{text} {patience}" if patience else text, lg, fallback_reason=fallback_reason)


async def _retry_on_enhanced_audio(
    session: CallSession, wav_path: str, lang: str | None, result, audio_issues: list[str], duration_s: float | None
):
    """A failed or doubtful turn on a noisy or faint line gets one more try
    on a noise-filtered, level-corrected copy of the clip. Only when the
    first attempt was actually poor -- filtering can hurt a recogniser that
    coped fine -- and never for cross-talk, which filtering cannot fix."""
    if lang is None or not ({"noisy", "too_quiet"} & set(audio_issues)):
        return result
    poor = (
        not result.text.strip()
        or transcript_problem(result.text, lang, duration_s, result.decoder_agreement)
        or is_low_confidence(result.decoder_agreement)
    )
    if not poor:
        return result
    enhanced = await asyncio.to_thread(_enhance_wav_to_path, wav_path)
    if not enhanced:
        return result
    try:
        retry = await _asr_router.transcribe(lang, enhanced)
    except Exception as e:  # noqa: BLE001 - the original result stands
        logger.warning("[%s] enhanced-audio retry failed: %s", session.call_id, e)
        return result
    finally:
        with contextlib.suppress(OSError):
            os.remove(enhanced)
    if retry.text.strip() and (not result.text.strip() or retry.decoder_agreement > result.decoder_agreement):
        logger.info("[%s] enhanced-audio retry improved the transcript", session.call_id)
        return retry
    return result


def _update_caller_state(session: CallSession, text: str, lang: str, analysis: dict | None) -> None:
    """KCD-084: the older-caller decision. An explicit request or self-
    description decides at once; the acoustic estimate needs evidence over
    several clips (agent/senior_voice.SeniorEvidence). Once senior, sticky
    for the call."""
    ev = session.senior_evidence
    cue = explicit_senior_cue(text, lang)
    flipped = ev.note_explicit(cue) if cue else (ev.add(analysis["senior"]) if analysis else False)
    if flipped and not session.call_state.senior:
        apply_caller_state(session.call_state, senior=True)
        senior_detections.record(ev.reason or "unknown", "call", lang)
        logger.info("[%s] senior mode on (%s)", session.call_id, ev.reason)
    # Ported story "Caller is angry about a previous experience" (agent/anger.py).
    # Deterministic, before any model call, in every language at once -- the same
    # discipline as agent/abuse.py and agent/human_request.py, and for the same
    # reason: a policy the model is asked to apply is one it can be talked out of.
    #
    # Setting the state is the WHOLE of the wiring, by design. The caller's next
    # spoken line is already decided from here by machinery this file calls two
    # lines after _update_caller_state() returns: apply_caller_state() re-derives
    # the "angry" row of Appendix C (slower delivery, one question per turn, a
    # human offered, escalation threshold lowered), and select_acknowledgement()
    # speaks the single-apology acknowledgement in the caller's own language.
    # Before this port nothing in the repository ever set "angry", so all of that
    # was unreachable; nothing new had to be written for it here.
    #
    # Sticky for the rest of the call, like senior above: a caller who has told us
    # they are angry is not un-angered by one calm turn, and quietly speeding back
    # up would read as the agent having stopped listening. The acknowledgement
    # itself is still spoken only once (main's `acknowledged_state` guard).
    if session.call_state.caller_state != "angry":
        angry, family = anger.detect_anger(text)
        if angry:
            apply_caller_state(session.call_state, caller_state="angry")
            _note(session, "anger_turns")
            logger.info("[%s] caller expressed anger (%s phrase list)", session.call_id, family)


def _note_stated_age(session: CallSession, slots: dict, lang: str) -> None:
    if stated_age_is_senior(slots.get("patient_age"), slots.get("relationship")):
        if session.senior_evidence.note_explicit("stated_age") and not session.call_state.senior:
            apply_caller_state(session.call_state, senior=True)
            senior_detections.record("stated_age", "call", lang)


_ECHOED_FIELDS = ("doctor_name", "date", "time_slot", "patient_name", "phone", "test_name", "new_date", "new_time_slot")


async def _thank(session: CallSession, lang: str) -> None:
    """A bare "Thank you." -- once per turn, and only when this turn is the caller's answer to a question we asked."""
    if not session.answering or getattr(session, "thanked_turn", None) == session.turn_count:
        return
    session.thanked_turn = session.turn_count
    await _speak(session, thanks_for(lang), lang)


async def _thank_for_answer(session: CallSession, st, changed: list[str], prior_slots: dict, lang: str) -> None:
    """Booking flows: thank the caller when they have just given a detail we asked for. Not on the first request
    ("I want an appointment with Dr Sen" answers the greeting, and is not a reply to a question about a detail)."""
    if (prior_slots or st.asked_fields) and any(f not in prior_slots for f in changed):
        await _thank(session, lang)


async def _echo_new_slots(session: CallSession, changed: list[str], prior_slots: dict, slots: dict, lang: str) -> None:
    """Senior mode's "one question, listen, confirm, next question": say
    the value just collected back before the next question. Also the moment
    a phone number first arrives, so a returning older caller is recognised
    from the record without having to ask for slower speech again."""
    newly = [f for f in changed if f not in prior_slots]
    if "phone" in newly:
        await _remember_senior(session, slots.get("phone"), lang)
    if not session.policy.confirm_each_slot:
        return
    for field in newly:
        value = slots.get(field)
        if value and field in _ECHOED_FIELDS:
            await _speak(session, slot_echo(str(value), lang), lang)
            return


async def _remember_senior(session: CallSession, phone: str | None, lang: str) -> None:
    if session.senior_lookup_done or not phone:
        return
    session.senior_lookup_done = True
    try:
        if await _tools.get_patient_senior(phone) and session.senior_evidence.note_explicit("remembered"):
            apply_caller_state(session.call_state, senior=True)
            senior_detections.record("remembered", "call", lang)
    except ToolCallError as e:
        logger.warning("[%s] senior lookup failed: %s", session.call_id, e)


async def _persist_senior(session: CallSession, phone: str | None) -> None:
    """KCD-084: once a booking is made, remember the delivery mode against
    the patient -- a boolean only, never a score, an age or any audio."""
    if not (session.call_state.senior and phone):
        return
    try:
        await _tools.set_patient_senior(phone, True)
    except ToolCallError as e:
        logger.warning("[%s] senior persist failed: %s", session.call_id, e)


_last_messages_error_logged = False


async def _refresh_messages() -> None:
    """Pull the operator-editable wording from the database (KCD-353/500/513). Bounded and silent on
    failure: the built-in text is spoken whenever the API cannot be reached, so an outage never leaves the
    agent without words."""
    global _last_messages_error_logged
    if _tools is None or not agent_messages.stale():
        return
    try:
        agent_messages.load(await asyncio.wait_for(_tools.agent_messages(), 1.5))
        _last_messages_error_logged = False
    except Exception as e:  # noqa: BLE001
        if not _last_messages_error_logged:
            logger.warning("agent messages not refreshed (%s): built-in wording is used", e)
            _last_messages_error_logged = True


def _note(session: CallSession, name: str, n: int = 1) -> None:
    """Count one signal for the call's implicit happiness score. Never raises: a score is never a reason to fail a turn."""
    try:
        session.signals.note(name, n)
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] score signal %s failed: %s", session.call_id, name, e)


def _write_satisfaction(session: CallSession) -> None:
    """At hang-up: score the call from its signals and queue the result with the call record. Counts and flags only."""
    st = session.booking
    if st is not None and topic_flow.worth_suspending(st):
        session.signals.left_mid_task = True  # a booking was still half-done when the line dropped
    result = score_call(session.signals)
    logger.info(
        "[%s] call score: %s (%s) %s",
        session.call_id,
        result.score,
        result.band,
        ", ".join(f"{n} {p:+d}" for n, p in result.reasons) or result.reason,
    )
    _rec(session, "satisfaction", result.to_payload(session.signals))


def _rec(session: CallSession, method: str, *args) -> None:
    """Queue one call-record event and start delivering it. Never raises and never waits: a
    record that cannot be written is retried and, at hang-up, reported (agent/call_record.py) --
    it is never a reason to slow or fail a turn."""
    rec = session.recorder
    if rec is None:
        return
    try:
        getattr(rec, method)(*args)
        asyncio.ensure_future(rec.flush())
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] call record event %s failed: %s", session.call_id, method, e)


def _record_action(session: CallSession, name: str, result: dict, slots: dict) -> None:
    if result.get("success"):
        _note(session, "task_completed")
        _rec(session, "action", name, result.get("confirmation_id"))
        _rec(session, "confirmed", slots)


async def _write_call_event(call_id: str, seq: int, kind: str, payload: dict, caller_phone: str | None) -> dict:
    if _tools is None:
        raise ToolCallError("clinic tools not ready")
    return await _tools.write_call_event(call_id, seq, kind, payload, caller_phone)


async def _handle_unverified_write(session: CallSession, lang: str) -> None:
    """KCD-486: a booking/reschedule write whose post-commit verification
    (clinic-api's _verify_appointment_persisted) did not confirm the
    expected values. Speaks a hold notice -- distinct from both a
    confirmation number (which would be an unverified fact) and the
    generic tool_failure apology (which invites a retry, and the write
    likely already happened, so a retry risks a second, duplicate one) --
    then escalates to a human rather than guessing."""
    await _speak(session, phrase("booking_hold_for_verification", lang), lang)
    await _handoff_to_human(session, "unverified_booking_write")


async def _await_with_filler(session: CallSession, awaitable, lang: str, threshold_s: float = FILLER_THRESHOLD_S):
    """KCD-461: thin wrapper over agent.filler.await_with_filler (the
    tested race logic) supplying "speak the pre-warmed please_wait
    phrase" as the timeout callback -- never synthesized fresh
    (TTSClient's cache already holds it from startup prewarm), so it
    costs no synthesis time at exactly the moment the system is already
    running slow."""

    async def _speak_filler():
        session.speaking_filler = True  # the holding phrase is not the answer: do not time it as one
        try:
            await _speak(session, phrase("please_wait", lang), lang)
        finally:
            session.speaking_filler = False

    # The threshold is counted from when the caller stopped speaking, not from when this stage began: recognition
    # already used some of it. It never drops below FILLER_MIN_WAIT_S, so a semantic-cache hit is not announced.
    started = session.turn_started_at
    if started is not None:
        threshold_s = max(threshold_s - (time.monotonic() - started), FILLER_MIN_WAIT_S)
    return await await_with_filler(awaitable, threshold_s, _speak_filler)


async def _slice_utterance(session: CallSession, start_s: float, end_s: float, seq: int) -> str:
    """Cuts [start_s, end_s+pad] -- both ABSOLUTE call-time offsets -- out
    of the call's decoded WAV into its own small file for ASR."""
    wav, sr = await asyncio.to_thread(torchaudio.load, session.wav_path)
    a = max(0, int(start_s * sr))
    b = min(int((end_s + UTTERANCE_PAD_S) * sr), wav.shape[-1])
    clip_path = f"{session.wav_path}.utt{seq}.wav"
    await asyncio.to_thread(torchaudio.save, clip_path, wav[:, a:b], sr)
    return clip_path


# KCD-101: the whole wall-clock a caller may wait for "what do they want" (cache lookup + model, all attempts)
# before the apology is spoken. Equal to the model's own default deadline (agent/llm.py, 12 s, REASONED, not
# measured on real telephony). The holding phrase is spoken once FILLER_THRESHOLD_S has passed without an answer.
INTENT_BUDGET_S = float(os.environ.get("INTENT_TURN_BUDGET_S", "12"))
# The semantic-cache embedding lookup gets this head start on the model; after that both run together.
CACHE_HEAD_START_S = float(os.environ.get("INTENT_CACHE_HEAD_START_S", "0.15"))
# ...and it may never hold the turn for more than this (kept as a ceiling on the head start).
CACHE_LOOKUP_MAX_S = float(os.environ.get("INTENT_CACHE_MAX_S", "1.5"))
# Below this much budget left, asking the model is pointless: go straight to the apology.
MIN_MODEL_BUDGET_S = 0.5


def _with_spelled_hint(session: CallSession, text: str, lang: str) -> str:
    """The text the INTENT MODEL reads. When the English reading spelled acronyms the Bengali / Hindi text does not carry
    ("হপ হসিগ আর হিব টেস্ট"), the tests they stand for are appended in the call's own language -- "(এইচসিভি, এইচবিএসএজি,
    এইচআইভি টেস্ট)" -- so the model sees a sentence about tests, not three unknown words. It only classifies and extracts
    from this; the tests are grounded and read back as always, and a name it returns that is not grounded is dropped."""
    cat = _fast_path.catalogue if _fast_path is not None else None
    if not session.turn_acronyms or cat is None:
        return text
    said = set(ground_tests(text, lang, cat, []).names)
    extra = [n for n in session.turn_acronyms if n not in said]
    if not extra:
        return text
    return f"{text} ({', '.join(_spoken_form(n, lang, cat) for n in extra)})"


async def _resolve_intent_uncached(session: CallSession, text: str, lang: str, key: str) -> dict:
    """Tier 2 (semantic cache) + tier 3 (LLM), run AFTER fast_path already
    abstained. Split out from _resolve_intent so the filler race below can
    wrap the WHOLE remaining sequence -- see KCD-459 note there."""
    # KCD-101: ONE wall-clock budget covers the cache lookup AND every model attempt. The model call already had
    # its own deadline (KCD-465), but the embedding lookup in front of it did not count against it, so a slow
    # cache plus a slow model could keep a caller waiting past the budget. A slow cache is treated as a miss.
    start = time.monotonic()

    def left() -> float:
        return INTENT_BUDGET_S - (time.monotonic() - start)

    # The cache lookup (an embedding call) and the model run TOGETHER, not one after the other. The lookup gets a
    # short head start; if it has not answered by then the model starts anyway and the lookup keeps going. A hit ends
    # the wait at once and the model's answer is dropped; a miss costs the model turn nothing. Measured on the pod, the
    # serial version spent up to 1.5 s waiting for a lookup that then missed, before the 1.3 s model call even began.
    lookup = asyncio.ensure_future(asyncio.to_thread(_intent_cache.get, key))
    lookup.add_done_callback(lambda f: f.cancelled() or f.exception())  # a failed lookup is a miss, never an error
    try:
        cached, how = await asyncio.wait_for(
            asyncio.shield(lookup), timeout=max(0.02, min(CACHE_HEAD_START_S, CACHE_LOOKUP_MAX_S, left()))
        )
    except TimeoutError:
        cached, how = None, "pending"
    except Exception as e:  # noqa: BLE001 - the semantic cache is optional
        logger.warning("[%s] intent cache lookup failed (%s): treated as a miss", session.call_id, e)
        cached, how = None, "error"
    if cached is not None:
        logger.info("[%s] intent cache %s hit", session.call_id, how)
        return cached

    budget_left = left()
    if budget_left < MIN_MODEL_BUDGET_S:
        raise ExtractionError(f"intent budget of {INTENT_BUDGET_S}s used up before the model was asked")
    # the extractor bounds itself by `budget_left`; the wait_for is the backstop for a thread that does not
    model = asyncio.ensure_future(
        asyncio.wait_for(
            asyncio.to_thread(extract_intent, _with_spelled_hint(session, text, lang), 2, lang, budget_left),
            timeout=budget_left + 1.0,
        )
    )
    model.add_done_callback(lambda f: f.cancelled() or f.exception())
    if not lookup.done():
        await asyncio.wait({lookup, model}, return_when=asyncio.FIRST_COMPLETED)
    if lookup.done() and not lookup.cancelled() and lookup.exception() is None and not model.done():
        hit, how = lookup.result()
        if hit is not None:
            model.cancel()  # its thread finishes on its own, bounded by its deadline; the answer is dropped
            logger.info("[%s] intent cache %s hit (while the model was running)", session.call_id, how)
            return hit
    try:
        data, diag = await model
    except TimeoutError:
        raise ExtractionError(f"intent extraction did not complete within the {INTENT_BUDGET_S}s budget") from None
    logger.info(
        "[%s] intent extracted in %.2fs (%d attempt(s))", session.call_id, diag["total_time_s"], diag["attempts"]
    )
    await asyncio.to_thread(_intent_cache.put, key, data)
    return data


async def _resolve_intent(session: CallSession, text: str, lang: str = "bn") -> dict:
    """Semantic cache in front of the LLM. A hit skips Ollama entirely --
    the slowest hop in the turn -- but the clinic lookup that follows still
    runs live, so a cached intent can never serve a stale price."""
    # Tier 1: decide it locally if we can. For a fixed catalogue the
    # entity is a string-matching problem with a 0.32 confidence margin,
    # where the embedding route had 0.03 -- see agent/fast_path.py. This
    # returns None whenever it is not sure, which is the common case for
    # anything except a routine price or availability question. It has a
    # cue table per language (agent/fast_path_cues.py, KCD-095) and abstains
    # for a language it has none for, so the turn goes to the LLM -- safe by
    # construction, just not free.
    await _maybe_reload_fast_path()
    # A number given where one is asked for is never a keyword question: "ফোন নম্বর 98300..." is the caller's number,
    # not "what is the clinic's phone number?" (the FAQ's own keywords).
    if _fast_path is not None and not _number_expected(session, text):
        gap = session.turn_count - session.last_enquiry_turn if session.last_enquiry_entities else None
        topic = recent_unique(session.last_enquiry_entities, "test_name", gap)
        hit = await asyncio.to_thread(_fast_path.resolve, text, lang, topic)
        if hit is None:
            # The call may be fixed to another language than this turn was said in (docs/adr/0003). One table never
            # fires on another language's sentence (a pinned property of the fast path), so the turn is read once
            # more in its own language here, and ONLY the routing decision is taken: a canned reply is written in
            # that language and the call speaks `lang`.
            other = foreign_script_language(text, lang)
            if other is not None:
                second = await asyncio.to_thread(_fast_path.resolve, text, other, topic)
                if second is not None and second.direct_reply_bn is None:
                    hit = second
                elif second is not None:
                    hit = _canned_reply_in_call_language(second, lang, other)
        if hit is not None:
            logger.info("[%s] fast path resolved %s (%.2f) -- no LLM call", session.call_id, hit.intent, hit.confidence)
            return hit.as_llm_shape()

    # The cache stores the whole intent object (smalltalk carries reply
    # text in the caller's language), so the language is part of the key.
    key = text if lang == "bn" else f"[{lang}] {text}"

    # KCD-459: the filler race used to wrap ONLY the LLM call, so a slow
    # embedding lookup (agent/semantic_cache.py's L2 tier, a live call to
    # Ollama's embed endpoint) burned into FILLER_THRESHOLD_S's budget
    # silently before the caller ever got the "please hold" filler -- a
    # cold/loaded embed step and a cold LLM call could sum past the
    # threshold with the caller hearing nothing for either half. Wrapping
    # the whole cache-then-LLM sequence means "no more than
    # FILLER_THRESHOLD_S of silence" is an end-to-end guarantee for this
    # tier, not just a promise about the LLM's own share of it.
    return await _await_with_filler(session, _resolve_intent_uncached(session, text, lang, key), lang)


# CodeRabbit-flagged, real bug: a failed hold used to ALWAYS clear
# time_slot, regardless of why hold_slot actually failed ("keep
# doctor/date, re-collect just the time" -- true only for a slot-shaped
# failure). For "doctor_not_found" or a date-shaped failure, the ACTUAL
# problem field was never cleared, so missing_required() still saw
# doctor_name/date as present and the SAME bad hold was retried forever
# on every subsequent turn, having only pointlessly re-asked for a time
# that was never the issue.
_HOLD_FAILURE_FIELD = {
    "doctor_not_found": "doctor_name",
    "doctor_ambiguous": "doctor_name",
    "invalid_date": "date",
    "date_in_past": "date",
    "doctor_not_available_that_day": "date",
    "sitting_over": "date",
}


def _field_to_reclear_on_hold_failure(reason: str | None) -> str:
    return _HOLD_FAILURE_FIELD.get(reason, "time_slot")


# A not-found answer that carries exactly ONE sound-alike suggestion ("did you mean Dr. Mukherjee?") is
# remembered for the turn, so a "yes" runs the lookup with that name -- otherwise the question would lead
# nowhere. Set by the lookups below, read by _finish_enquiry_turn.
_suggested: contextvars.ContextVar[dict | None] = contextvars.ContextVar("suggested", default=None)


def _note_suggestion(intent: str, slot: str, slots: dict, result: dict) -> None:
    names = result.get("did_you_mean") or []
    if not result.get("found") and result.get("needs_confirmation") and not result.get("ambiguous") and len(names) == 1:
        _suggested.set({"intent": intent, "slot": slot, "slots": dict(slots), "name": names[0]})


async def _answer_enquiry_intent(intent: str, slots: dict, lang: str) -> str | None:
    """Tool call + reply-template for one of the stateless enquiry
    intents (agent.enquiry_followup.ENQUIRY_INTENTS). Returns None when
    the intent's required slot is missing -- the caller decides whether
    that means "ask the caller" (the turn's primary question) or
    "silently skip" (a KCD-395 secondary question), never this function.
    The single implementation per intent here is also what a secondary
    question is answered with, so there is exactly one place each
    intent's tool call and reply template are wired together."""
    if intent == "test_rate":
        if not slots.get("test_name"):
            return None
        result = await _tools.get_test_rate(slots["test_name"])
        _note_suggestion(intent, "test_name", slots, result)
        return test_rate_reply(slots, result, lang)
    if intent == "doctor_availability":
        if not slots.get("doctor_name"):
            return None
        result = await _tools.get_doctor_availability(slots["doctor_name"], slots.get("date"))
        _note_suggestion(intent, "doctor_name", slots, result)
        return doctor_availability_reply(slots, result, lang)
    if intent == "test_prep":
        # KCD-382: the model may have put a single test name in "test_names" instead of "test_name" now that
        # "test_prep" is also allowed to fill the plural slot (see agent/llm.py) -- two or more names is handled
        # entirely BEFORE this function is called (main.py's test_prep dispatch branch), so by the time a turn
        # reaches here "test_names" never has more than one entry; this is only the fallback for that one.
        test_name = slots.get("test_name") or next(iter(slots.get("test_names") or []), None)
        if not test_name:
            return None
        result = await _tools.get_test_prep(test_name, lang)
        _note_suggestion(intent, "test_name", slots, result)
        return test_prep_reply({**slots, "test_name": test_name}, result, lang)
    if intent == "clinic_faq":
        if not slots.get("faq_topic"):
            return None
        result = await _tools.get_faq(slots["faq_topic"], lang)
        return clinic_faq_reply(slots, result, lang)
    if intent == "department_query":
        symptom = slots.get("symptom_description")
        if not symptom:
            return None
        result = await _tools.route_department(symptom, lang)
        if not result.get("matched") and not result.get("ambiguous"):
            # route_department is the SYMPTOM router: its keywords are clinician-approved complaints ("सीने में
            # दर्द"), and only its English row happens to carry a department's own name. So a caller who NAMES the
            # department instead of describing a complaint matched nothing and was sent to the counter -- live
            # 2026-10-09, "गाइनेकोलॉजी में दिखाना चाहता हूँ", twice. Naming a department is not a symptom, and the
            # by-department lookup is the thing that reads department names, in all three scripts and every spoken
            # form (clinic-api/department_match.py + DEPARTMENT_ALIASES). Tried ONLY once the symptom router has
            # found nothing, so no query that already resolves can change its answer; its no-guessing rules are its
            # own (several matches come back `ambiguous` and are asked about, never picked).
            with contextlib.suppress(ToolCallError):
                named = await _tools.doctors_by_department(symptom)
                if named and (named.get("found") or named.get("ambiguous") or named.get("did_you_mean")):
                    return doctors_by_department_reply(named, lang)
        return department_route_reply(result, symptom, lang)
    return None


def _named_tests(slots: dict) -> list[str]:
    """Every test the turn named, once each, in the order said: "test_name" and "test_names" together."""
    names: list[str] = []
    for name in [slots.get("test_name"), *(slots.get("test_names") or [])]:
        name = (name or "").strip()
        if name and name.casefold() not in {n.casefold() for n in names}:
            names.append(name)
    return names


async def _price_tests(session: CallSession, names: list[str], lang: str) -> tuple[list[tuple[str, object]], list[dict]]:
    """Each named test priced from the catalogue on its own -> (found: (spoken name, rate), missing: the not-found
    answers). Two names that resolve to the same catalogue test are priced once."""
    found: list[tuple[str, object]] = []
    missing: list[dict] = []
    priced: set[str] = set()
    for name in names:
        result = await _tools.get_test_rate(name)
        if result.get("found") and result.get("rate_inr") is not None:
            canonical = (result.get("test_name") or name).casefold()
            if canonical not in priced:
                priced.add(canonical)
                found.append((spoken_test_name(name, result, lang), result["rate_inr"]))
        else:
            missing.append(unfound_test(name, result, lang))
    logger.info("[%s] test prices: %d found, %d not found", session.call_id, len(found), len(missing))
    return found, missing


async def _answer_test_booking(
    session: CallSession, slots: dict, text: str, lang: str, data: dict
) -> None:
    """A caller who wants to book one or several lab tests. Which tests is settled first -- read back and
    confirmed (the acceptance layer) -- and only then are the prices, their total and the counter rule spoken."""
    named = _named_tests(slots)
    grounded = _ground_tests_said(session, text, lang, named)
    if grounded.names or grounded.choices:
        await _confirm_tests(session, "book_test", grounded, text, lang, slots, data)
        return
    if named:
        if await _answer_faq_instead(session, text, lang):
            return
        await _ask_which_tests(session, "book_test", _how_many_tests(grounded, slots), lang)
        return
    await _speak(session, phrase("lab_test_counter_only", lang), lang)


async def _speak_test_prices(session: CallSession, names: list[str], lang: str, counter: bool) -> None:
    """The prices themselves, once the caller has confirmed WHICH tests. Every figure is the catalogue's and the
    total is their sum (multi_test_rate_reply); `counter` appends the booking-is-at-the-counter rule."""
    try:
        found, missing = await _price_tests(session, names, lang)
    except ToolCallError as e:
        logger.warning("[%s] test prices unavailable (%s)", session.call_id, e)
        await _speak(session, phrase("lab_test_counter_only" if counter else "tool_failure", lang), lang)
        return
    reply = multi_test_rate_reply(found, missing, lang)
    if missing:
        session.pending_enquiry = ("test_rate", session.turn_count)
        await _speak(session, reply, lang)
        return
    if counter:
        reply = f"{reply} {phrase('lab_test_counter_only', lang)}"
    await _speak(session, _with_resume(session, reply, lang), lang)


# How many turns a read-back may sit unanswered before it is forgotten: the caller moved on, and a yes meant for
# something else must never price a list they have stopped talking about.
TEST_CONFIRM_TTL_TURNS = 3
# Fewest words a turn needs before the notice is spoken ahead of the shortcuts (see _dispatch_turn, KCD-353). REASONED, not measured.
DISCLOSURE_MIN_WORDS = 3
# Confirm before EVERY price, including a single test named in full. Set False to confirm only when more than one
# test was named or a name was matched approximately -- fewer turns, but one misheard test can then be priced
# without the caller ever hearing it back. Live calls decide this; the safe default ships.
TEST_CONFIRM_ALWAYS = True


async def _answer_faq_instead(session: CallSession, text: str, lang: str) -> bool:
    """The turn was filed as a test question, no test in it is real, but it carries one of the clinic's own FAQ
    keywords -- so it was a clinic question all along. Live 2026-10-08: "সোনস্কানের অযাড্ড্রেস টা কি" came back
    as a test named "সোনস্কান" (our own name) and the caller was told no such test exists.

    True when the FAQ answered the turn. Answered from the clinic's stored text, never composed."""
    topic = _faq_topic_said(text, lang)
    if not topic:
        return False
    try:
        reply = await _answer_enquiry_intent("clinic_faq", {"faq_topic": topic}, lang)
    except ToolCallError as e:
        logger.warning("[%s] faq lookup unavailable (%s)", session.call_id, e)
        return False
    if not reply:
        return False
    logger.info("[%s] filed as a test, answered as the %r clinic question", session.call_id, topic)
    await _speak(session, _with_resume(session, reply, lang), lang)
    return True


def _how_many_tests(grounded, slots: dict) -> int | None:
    """How many tests the caller wants. The count heard in their own words wins over the model's, the same rule
    the day and the time already follow (_pin_spoken_dates / _pin_spoken_times); the model's is the fallback."""
    model_count = slots.get("test_count")
    return grounded.count or (model_count if isinstance(model_count, int) else None)


def _answers_the_open_booking(session: CallSession, text: str) -> bool:
    """Is this turn a plain answer to the question the open booking just asked -- a clock time, a day, or the
    slot the agent itself offered?

    None of those is a NAME. The confidence gate exists so a MISHEARD NAME never reaches a lookup and becomes a
    stated fact (CLAUDE.md section 1, the "Doctor Nobody" bug); a time and a day cannot do that. Every one of
    them is read back before anything is written (booking_confirmation_readback), and a pick of an offered slot
    does not depend on recognition accuracy at all -- it points at a day the agent chose and spoke a moment ago.

    Live 2026-10-08: the agent asked "কোন সময়ের জন্য অ্যাপয়েন্টমেন্ট চাই?", the caller answered "এগারোটা", and the
    turn was refused with "দুঃখিত, ঠিক শুনতে পাইনি" -- not because the audio was poor, but because two decoders
    spelled a two-syllable word differently. "ঐদিন" went the same way. The booking could not be finished. Short
    answers are exactly where two decoders disagree, and the answer to the agent's own question is exactly what
    a booking is made of.

    A turn that also names a doctor is NOT covered (the caller checks that above): a misheard doctor name would
    have the agent read out a different doctor's schedule, which is the harm the gate is actually for."""
    st = session.booking
    if st is None or st.stage != "collecting" or st.is_stale():
        return False
    if st.offered_slots and pick_offered_slot(text, st.offered_slots):
        return True
    return bool(spoken_times(text) or spoken_dates(text))


def _ground_tests_said(session: CallSession, text: str, lang: str, candidates: list[str]):
    """The tests THIS turn's words support, with whatever the model offered filtered against them."""
    catalogue = _fast_path.catalogue if _fast_path is not None else None
    grounded = ground_tests(text, lang, catalogue, candidates, extra_names=session.turn_acronyms)
    picked = _picked_from_the_group(session, text, grounded)
    if picked:
        # the caller is choosing among the tests just read out: their short / run-together words count, and only
        # members of that very group can be picked
        names = list(grounded.names)
        forms = list(grounded.forms)
        for name, spoken in picked:
            if name not in names:
                names.append(name)
                forms.append(spoken)
        grounded = dataclasses.replace(grounded, names=tuple(names), forms=tuple(forms))
    if grounded.dropped:
        logger.info(
            "[%s] %s named by the model, not in what was heard -- dropped (heard: %s, count=%s)",
            session.call_id, list(grounded.dropped), list(grounded.names), grounded.count,
        )
    return grounded


GROUP_CONTEXT_TURNS = 3  # turns after a group is read out in which the caller's words are a pick among its members


def _group_member_rows(session: CallSession) -> list[dict]:
    ctx = session.group_members
    if ctx is None or session.turn_count - ctx[1] > GROUP_CONTEXT_TURNS or _fast_path is None:
        return []
    wanted = set(ctx[0])
    return [t for t in _fast_path.catalogue.test_rows if t.get("name") in wanted]


def _all_of_the_group(session: CallSession, text: str, lang: str) -> list[str] | None:
    """"দুটোই করাবো" / "both" / "सभी" right after a group was read out: EVERY test of that group, in the order they
    were read. Never when the sentence names a test outside the group, or is about something else."""
    rows = _group_member_rows(session)
    if len(rows) < 2 or mentions_something_else(text):
        return None
    if not asks_for_all(text, len(rows)):
        return None
    members = [n for n in session.group_members[0] if n in {r.get("name") for r in rows}]
    # "ইসিজি আর এক্সরে করব, সব মিলিয়ে কত": tests NAMED in the sentence are a pick (the flow above this one), and its "সব
    # মিলিয়ে" is "altogether" -- not "all of them"
    if _ground_tests_said(session, text, lang, []).names or resolve_among(text, rows):
        return None
    return members or None


async def _price_all_of_the_group(session: CallSession, text: str, lang: str) -> bool:
    """The caller took every test just read out: their prices are already known to them, the new fact is the total."""
    names = _all_of_the_group(session, text, lang)
    if not names:
        return False
    logger.info("[%s] every test of the group chosen (%s) -> total", session.call_id, names)
    session.group_members = None
    session.awaiting_tests = None
    await _speak_test_prices(session, names, lang, counter=asks_to_book(text))
    return True


def _group_pick(session: CallSession, text: str, lang: str):
    """The tests of the group just read out that this turn picks -- as a GroundedTests in the order said -- or None.
    Only when EVERY test this turn names is a member of that group: a sentence that also names another test, or
    names none, is not a pick."""
    rows = _group_member_rows(session)
    if not rows:
        return None
    members = {r.get("name") for r in rows}
    grounded = _ground_tests_said(session, text, lang, [])
    names = list(grounded.names)
    if not names or not set(names) <= members:
        return None
    said_order = [n for n, _ in resolve_among(text, rows) if n in names]
    ordered = said_order + [n for n in names if n not in said_order]
    forms = dict(zip(grounded.names, grounded.forms))
    return dataclasses.replace(grounded, names=tuple(ordered), forms=tuple(forms.get(n, n) for n in ordered))


def _picked_from_the_group(session: CallSession, text: str, grounded) -> list[tuple[str, str]]:
    rows = _group_member_rows(session)
    if not rows:
        return []
    return [(n, s) for n, s in resolve_among(text, rows) if n not in grounded.names]


async def _confirm_tests(
    session: CallSession, action: str, grounded, text: str, lang: str, slots: dict, data: dict
) -> None:
    """Read the tests back in the caller's own words and wait for a yes. Nothing is looked up until then."""
    session.awaiting_tests = None
    choices = [list(c) for c in getattr(grounded, "choices", ())]
    session.pending_tests = {
        "action": action,
        "names": list(grounded.names),
        "forms": list(grounded.forms),
        "choices": choices,
        "slots": dict(slots or {}),
        "data": dict(data or {}),
        "text": text,
        "turn": session.turn_count,
    }
    if choices:
        # Two tests fit some words about equally well: asked, never picked. The tests already clear stay on the list.
        logger.info("[%s] asking which of %s; kept so far %s", session.call_id, choices[0], list(grounded.names))
        await _speak(session, _test_choice_question(session.pending_tests, lang), lang)
        return
    logger.info("[%s] reading back %s before pricing", session.call_id, list(grounded.names))
    await _speak(session, test_confirm_prompt(list(grounded.forms), action, lang), lang)


def _test_choice_question(pending: dict, lang: str, reasked: bool = False) -> str:
    """The question for the first open choice in `pending`: "<kept tests> understood. One more -- A or B?" -- or "A and B,
    both?" when two stretches of words fit the same two tests."""
    cands = pending["choices"][0]
    same = sum(1 for c in pending["choices"] if c == cands)
    cat = _fast_path.catalogue if _fast_path is not None else None
    spoken = [_spoken_form(n, lang, cat) for n in cands]
    return test_choice_prompt(list(pending.get("forms") or []), spoken, same >= 2 and same >= len(cands), lang, reasked)


_NEGATIVE_LEAD = re.compile(r"^\s*(?:না|নয়|নাহ|no|nope|nah|नहीं|नही|नहि)(?=[\s,।.!?]|$)", re.IGNORECASE)
TEST_ADD_ON = os.environ.get("TEST_ADD_ON", "on").strip().lower() != "off"
# The recognisers' two decoders disagree on how an acronym is spelled, so a sentence made of acronyms scores a low agreement
# ("এইচআইভি এইচসিভি আর এইচবিএসসি": 0.38) however clearly it was said. A turn that names tests is READ BACK before any price
# (_confirm_tests), which is a confirmation by itself; TEST_GATE_READBACK=off withholds it as before.
TEST_GATE_READBACK = os.environ.get("TEST_GATE_READBACK", "on").strip().lower() != "off"


def _choice_options(cands: list[str], lang: str) -> dict[str, list[str]]:
    """For each candidate test, the ways it may be said: its acronym letters, the clinic's spoken forms in the call's language and
    in Bengali, and its name."""
    cat = _fast_path.catalogue if _fast_path is not None else None
    table = _spelled_table()
    out: dict[str, list[str]] = {}
    for n in cands:
        spellings = [letters for letters, name in table.items() if name == n]
        for row in getattr(cat, "test_rows", None) or ():
            if row.get("name") == n:
                for key in {f"aliases_{lang}", "aliases_bn"}:
                    spellings += list(row.get(key) or [])
        spellings.append(n)
        out[n] = spellings
    return out


async def _answer_test_choice(session: CallSession, pending: dict, text: str, lang: str) -> bool:
    """The caller's answer to "TSH or PSA?": a name picks it, "both" takes all, and the tests kept so far stay on the list
    either way. True when the turn was handled; False when the answer is about something else (the question lapses as any
    read-back does, and the turn is handled afresh)."""
    choices = pending["choices"]
    cands = choices[0]
    same = sum(1 for c in choices if c == cands)
    answer = classify_yes_no(text, lang)
    grounded = _ground_tests_said(session, text, lang, [])
    picked = [n for n in cands if n in grounded.names]
    if not picked:
        # Only these two or three are in play, so the answer is matched against THEM, by letters: "h b s g" (no "a"), said in
        # English letters or in Bengali ones, from whichever recogniser won the turn.
        close = closest_of(text, _choice_options(cands, lang))
        if close:
            picked = [close]
    extra = [(n, f) for n, f in zip(grounded.names, grounded.forms) if n not in cands]
    cat = _fast_path.catalogue if _fast_path is not None else None
    chosen: list[str] = []
    if picked:
        chosen = picked
    elif asks_for_all(text, len(cands)) or (answer == "yes" and same >= 2 and same >= len(cands)):
        chosen = list(cands)
    elif answer == "yes" and not pending.get("reasked"):
        pending["reasked"] = True  # a bare "yes" to "A or B?" says nothing: ask once more
        pending["turn"] = session.turn_count
        session.pending_tests = pending
        await _speak(session, _test_choice_question(pending, lang, reasked=True), lang)
        return True
    elif not extra and answer not in ("no", "yes"):
        short = len(text.split()) <= 4 and "?" not in text
        if short and not pending.get("reasked"):
            # a few words that name neither: the caller is most likely answering, and the kept tests are not given up -- ask
            # once more, instead of handing a stray "h b s g" to the model as if it opened a new conversation
            pending["reasked"] = True
            pending["turn"] = session.turn_count
            session.pending_tests = pending
            await _speak(session, _test_choice_question(pending, lang, reasked=True), lang)
            return True
        if not short:
            return False  # about something else
        # asked twice and still no name: the doubt is dropped and what was clear is read back (below)
    names, forms = list(pending["names"]), list(pending.get("forms") or [])
    if len(forms) != len(names):
        forms = [_spoken_form(n, lang, cat) for n in names]
    for n in chosen:
        if n not in names:
            names.append(n)
            forms.append(_spoken_form(n, lang, cat))
    for n, f in extra:
        if n not in names:
            names.append(n)
            forms.append(f)
    left = [c for c in choices[1:] if not (same >= 2 and c == cands)] if chosen else list(choices[1:])
    if not chosen and not extra:
        left = []  # "neither": the doubt is dropped, what was clear is kept
    _note(session, "corrections")
    if not names and not left:
        await _ask_which_tests(session, pending["action"], None, lang)
        return True
    nxt = GroundedTests(names=tuple(names), forms=tuple(forms), choices=tuple(tuple(c) for c in left), exact=False)
    await _confirm_tests(session, pending["action"], nxt, pending["text"], lang, pending["slots"], pending["data"])
    return True


async def _ask_which_tests(session: CallSession, action: str, count: int | None, lang: str) -> None:
    """Which tests -- asked, never guessed. `count` makes it specific ("you said two -- which two?")."""
    session.pending_tests = None
    session.awaiting_tests = action
    await _speak(session, which_tests_prompt(count, lang), lang)


async def _resume_confirmed_tests(session: CallSession, pending: dict, lang: str) -> None:
    names = pending["names"]
    if pending["action"] == "test_rate":
        await _answer_multi_test_rate(session, pending["text"], lang, pending["slots"], names, pending["data"])
    else:
        await _speak_test_prices(session, names, lang, counter=True)


async def _continue_test_confirm(session: CallSession, text: str, lang: str) -> bool:
    """The caller's answer to "<tests>, ঠিক তো?".

    yes -> price them. no -> the "which tests?" loop. A turn naming tests of its own is a correction and is read
    back in turn. A request for the list is answered and the question stays open. Anything else lapses it
    (KCD-104) and is handled as a fresh turn."""
    pending, session.pending_tests = session.pending_tests, None
    if session.turn_count - pending.get("turn", 0) > TEST_CONFIRM_TTL_TURNS:
        return False
    if pending.get("choices"):
        return await _answer_test_choice(session, pending, text, lang)
    answer = classify_yes_no(text, lang)
    # Tests named in the ANSWER are a correction, whatever else the sentence carries -- "না, সিবিসি আর থাইরয়েড"
    # is not a bare no, and only a bare no reopens the question (KCD-104's rule for the booking read-back).
    corrected = _ground_tests_said(session, text, lang, [])
    if (corrected.names or corrected.choices) and list(corrected.names) != pending["names"]:
        _note(session, "corrections")
        grounded_next = corrected
        pending_forms = list(pending.get("forms") or [])
        if (
            TEST_ADD_ON
            and pending["names"]
            and len(pending_forms) == len(pending["names"])
            and not (set(corrected.names) & set(pending["names"]))
            and answer != "no"
            and not _NEGATIVE_LEAD.match(text or "")
        ):
            # tests the caller had not yet named ("সঙ্গে কিডনি ফাংশনও"): ADDED to the list read back, not put in its place. A
            # sentence that names any of the same tests again is a restatement, and replaces it as it always did.
            grounded_next = GroundedTests(
                names=tuple(pending["names"]) + tuple(corrected.names),
                forms=tuple(pending_forms) + tuple(corrected.forms),
                choices=corrected.choices,
                exact=False,
            )
        await _confirm_tests(
            session, pending["action"], grounded_next, text, lang, pending["slots"], pending["data"]
        )
        return True
    if answer == "yes" or corrected.names:
        await _resume_confirmed_tests(session, pending, lang)
        return True
    if answer == "no":
        _note(session, "corrections")
        await _ask_which_tests(session, pending["action"], None, lang)
        return True
    if asks_for_test_list(text):
        session.pending_tests = pending  # the question stays open behind the list
        await _speak_test_list(session, 0, lang)
        return True
    return False


async def _continue_test_choice(session: CallSession, text: str, lang: str) -> bool:
    """Inside the "which tests?" loop.

    A test named -> read it back. A request for the clinic's tests -> read them out, and stay. Anything else
    leaves the loop for ordinary service rather than asking a third time."""
    action = session.awaiting_tests
    if await _price_all_of_the_group(session, text, lang):
        return True
    grounded = _ground_tests_said(session, text, lang, [])
    if grounded.names or grounded.choices:
        session.awaiting_tests = None
        members = set(session.group_members[0]) if session.group_members and _group_member_rows(session) else set()
        if members and not grounded.choices and set(grounded.names) <= members and asks_for_price(text):
            # a pick from the list the agent has just read -- every price is already known to the caller -- and the
            # question is what they cost together: the answer, not another "ঠিক তো?"
            await _speak_test_prices(session, list(grounded.names), lang, counter=(action == "book_test"))
            return True
        await _confirm_tests(session, action, grounded, text, lang, {}, {})
        return True
    if asks_for_test_list(text):
        await _speak_test_list(session, 0, lang)
        return True
    if _group_member_rows(session) and not session.group_reasked:
        # the caller answered the group's "কোনটা করাতে চান?" with words that name none of its tests: say it once more,
        # with the list, rather than dropping out of the question they were answering
        session.group_reasked = True
        try:
            rows = {t.get("test_name"): t for t in (await _tools.list_tests()).get("tests", [])} if _tools else {}
        except ToolCallError:
            rows = {}
        hit_names, _turn, label = session.group_members
        members = [rows[n] for n in hit_names if n in rows]
        if members:
            await _speak(session, test_group_reply(label, members, lang, "book"), lang)
            return True
    session.awaiting_tests = None
    await _speak(session, phrase("how_else_help", lang), lang)
    return True


async def _speak_test_list(session: CallSession, offset: int, lang: str) -> None:
    """story: "Caller asks which lab tests are available" -- the next few tests with their prices (live from the
    clinic: GET /api/v1/tests), and "shall I go on?" while there are more."""
    try:
        listing = await _tools.list_tests()
    except ToolCallError as e:
        logger.warning("[%s] test list unavailable (%s)", session.call_id, e)
        await _speak(session, phrase("tool_failure", lang), lang, fallback_reason="tool_failure")
        return
    reply, session.test_list_offset = test_list_page(listing, offset, lang)
    session.test_list_parked = None
    await _speak_list(
        session,
        reply if session.test_list_offset is not None else _with_resume(session, reply, lang),
        lang,
        extra_s=LIST_REASK_EXTRA_S,  # five tests and their prices: time to take them in and choose
    )


TEST_LIST_PATIENCE = 3  # turns the model cannot place that an open list survives before it lapses
TEST_LIST_PARK_TURNS = 4  # turns a list set aside for a question about one test can be picked up again


async def _resume_parked_test_list(session: CallSession, text: str, lang: str) -> bool:
    """"পরেরগুলো বলুন" a few turns after the caller asked one test's price in the middle of the list: carry on from
    where it was. Only an unambiguous go-on does (no bare "ok" / "yes"), only while nothing else is waiting on an
    answer, and only for a few turns."""
    parked = session.test_list_parked
    if parked is None:
        return False
    offset, turn = parked
    if session.turn_count - turn > TEST_LIST_PARK_TURNS or session.booking is not None:
        session.test_list_parked = None
        return False
    if session.pending_tests or session.awaiting_tests:
        return False  # a read-back is waiting on its yes/no: that answer is the read-back's; the list stays parked
    if wants_more(text, bare=False):
        session.test_list_parked = None
        await _speak_test_list(session, offset, lang)
        return True
    return False


async def _continue_test_list(session: CallSession, text: str, lang: str) -> bool:
    """The answer to "shall I go on, or name a test?". "More" / yes reads the next few; "again" reads this page once
    more; no closes it. Anything else (a test named, another question) lapses the offer and is handled as a fresh
    turn -- EXCEPT that the offer is kept in `test_list_stash` and restored if the model then cannot place the turn
    either ("প্রডেক্ট গুলো বলুন", a garbled "the next tests", live 2026-10-13): the caller is asked again, and a clear
    "পরেরগুলো বলুন" a turn later still continues the list. See the "unclear" branch of _dispatch_turn."""
    offset, session.test_list_offset = session.test_list_offset, None
    strikes, session.test_list_strikes = session.test_list_strikes, 0
    answer = classify_yes_no(text, lang)
    if answer == "no":
        await _say_history(session, [history_text.ok_anything_else(lang)], lang)
        return True
    if wants_repeat(text) and not _ground_tests_said(session, text, lang, []).names:
        await _speak_test_list(session, max(0, offset - TEST_LIST_PAGE), lang)  # the page just read, once more
        return True
    if answer == "yes" or wants_more(text):
        await _speak_test_list(session, offset, lang)
        return True
    session.test_list_stash = (offset, strikes + 1)
    return False


async def _answer_multi_test_rate(
    session: CallSession, text: str, lang: str, slots: dict, names: list[str], data: dict
) -> None:
    """story: "Caller asks the price of a test" -- several in one question, and their total.

    Each test is looked up on its own (the same catalogue lookup a single price uses), and the reply
    is agent/reply_templates_i18n.multi_test_rate_reply: every price and the total are the catalogue's
    figures and their sum, never the model's. Two names that resolve to the same catalogue test are
    priced once, so a caller who says "CBC, complete blood count" is not charged twice. A test the
    clinic cannot pin down is answered with the not-found path, and the price question stays open so
    the caller's next bare name is priced."""
    found, missing = await _price_tests(session, names, lang)
    reply = multi_test_rate_reply(found, missing, lang)
    await _finish_enquiry_turn(session, text, lang, "test_rate", slots, reply, data)
    if missing:
        session.pending_enquiry = ("test_rate", session.turn_count)


async def _handle_multi_test_prep(session: CallSession, test_names: list[str], lang: str) -> None:
    """KCD-382, story "Caller has several tests with conflicting
    preparation": the caller named more than one test in a "test_prep"
    turn (agent/llm.py's "test_names" slot, the same structure "book_test"
    already uses -- see agent/intent_schema.py). Name resolution and the
    strictest-constraint merge both happen in clinic-api (AC1); this
    function only inspects the merged result and either speaks it (AC2,
    agent.reply_templates.merged_test_prep_reply -- one sentence, never a
    paragraph per test) or hands off to a human (AC3), exactly the same
    shape as _handle_unverified_write above: a fixed notice, then
    _handoff_to_human, then return -- never both a spoken instruction and
    a hand-off for the same request, and never a continuation into
    _finish_enquiry_turn's secondary-question/coreference bookkeeping,
    which this story's AC's have no use for.

    `result["escalate_to_human"]` is the ONLY signal this function acts
    on for AC3 -- it is not re-derived here (CLAUDE.md rule 1): it is
    real whenever enquiry_service.merge_prep_instructions() found a
    conflict it could not safely fold into one number OR a resolved test
    whose structured fasting data is still incomplete (see that
    function's own docstring) -- not a model guess at a clinical
    conflict."""
    result = await _tools.merge_test_prep(test_names, lang)
    if result.get("escalate_to_human"):
        logger.info(
            "[%s] test_prep merge escalated for %r: incomplete_data=%s",
            session.call_id,
            test_names,
            result.get("incomplete_data"),
        )
        await _speak(session, phrase("test_prep_conflict_handoff", lang), lang)
        await _handoff_to_human(session, "test_prep_conflict_unresolved", (lang,))
        return
    reply = merged_test_prep_reply(test_names, result, lang)
    await _speak(session, _with_resume(session, reply, lang), lang)
    # KCD-382b: remember what was actually resolved and spoken (never on the escalation path above,
    # where nothing was safely resolved) for a same-call follow-up that names no test.
    session.last_resolved_prep_tests = tuple(test_names)
    session.last_resolved_prep_turn = session.turn_count


def _recover_followup_prep_tests(session: CallSession) -> tuple[str, ...] | None:
    """KCD-382b: the minimum context this story's follow-up case needs -- not general conversation
    memory. Reuses, unchanged, the exact recency rule agent/enquiry_followup.resolve_followup_slot
    already applies to a single test_name follow-up (gap <= FOLLOWUP_MAX_GAP counts as "what we were
    just talking about"), so "how recent is a follow-up" has exactly one definition in this
    codebase, not a second, slightly different one invented here for the plural case."""
    tests = session.last_resolved_prep_tests
    if not tests:
        return None
    if session.turn_count - session.last_resolved_prep_turn > FOLLOWUP_MAX_GAP:
        return None
    return tests


async def _speak_single_test_prep(session: CallSession, test_name: str, lang: str, text: str, data: dict) -> None:
    """KCD-382b: speak ONE already-resolved test's preparation through KCD-382's existing
    single-test path (_answer_enquiry_intent / _finish_enquiry_turn -- the same
    agent.reply_templates.test_prep_reply a directly-named single test already uses), so a test
    resolved from a registered/booking lookup or from this story's follow-up recall is never a
    second implementation of the single-test reply, and still gets _finish_enquiry_turn's own
    secondary-question and coreference bookkeeping for free."""
    slots = {"test_name": test_name}
    reply = await _answer_enquiry_intent("test_prep", slots, lang)
    if reply is None:
        # The resolved name is not in the live catalogue -- never invented, say so plainly rather
        # than guessing at a preparation instruction.
        await _speak(session, phrase("tool_failure", lang), lang)
        return
    await _finish_enquiry_turn(session, text, lang, "test_prep", slots, reply, data)
    session.last_resolved_prep_tests = (test_name,)
    session.last_resolved_prep_turn = session.turn_count


async def _handle_booking_prep_lookup(session: CallSession, phone: str, lang: str) -> None:
    """KCD-382b, PATH B of "Caller has several tests with conflicting preparation": the caller
    confirmed they are registered / have a booking and just gave the phone number it is under.
    Reuses the existing booking-search infrastructure exactly as built (clinic-api/
    patient_context.py::search_bookings, already exposed as _tools.search_bookings) -- including
    its own several-bookings-never-bookings[0] rule (KCD-497) -- to find the caller's TEST
    booking(s) by that phone, then hands the resolved test name(s) to KCD-382's own existing
    single-test path or _handle_multi_test_prep. Never a third implementation of either."""
    try:
        found = await _tools.search_bookings(phone, phone=phone)
    except ToolCallError as e:
        logger.warning("[%s] booking-prep search failed: %s", session.call_id, e)
        await _speak(session, phrase("tool_failure", lang), lang)
        return
    # Only TEST bookings matter here -- an unrelated doctor appointment on the same number is never
    # used for (or counted toward) a test-preparation answer.
    matches = [m for m in (found.get("matches") or []) if m.get("kind") == "test_booking"]
    if not matches:
        # No real test booking on that number: never invented, never guessed -- fall back to the
        # existing generic (caller-names-the-test) path, exactly PATH A's own behaviour.
        session.pending_enquiry = ("test_prep", session.turn_count)
        await _speak(session, phrase("test_prep_no_booking_found", lang), lang)
        await _speak(session, missing_slot_prompt("test_prep", "test_name", lang), lang)
        return
    if len(matches) > 1:
        # KCD-497's own rule, reused: several bookings are never collapsed to the first -- the
        # caller is asked which one, by confirmation number, the same bearer-token identifier
        # every other lookup path in this codebase already uses.
        await _speak(session, multiple_test_bookings_reply(matches, lang), lang)
        return
    confirmation_id = matches[0]["confirmation_id"]
    try:
        tests_result = await _tools.booking_test_names(confirmation_id)
    except ToolCallError as e:
        logger.warning("[%s] booking_test_names failed for %s: %s", session.call_id, confirmation_id, e)
        await _speak(session, phrase("tool_failure", lang), lang)
        return
    test_names = [n for n in (tests_result.get("test_names") or []) if n]
    if not test_names:
        # A confirmation id with no confirmed test rows left (e.g. all cancelled) -- same honest
        # fallback as "no booking found" above, never an invented test.
        session.pending_enquiry = ("test_prep", session.turn_count)
        await _speak(session, phrase("test_prep_no_booking_found", lang), lang)
        await _speak(session, missing_slot_prompt("test_prep", "test_name", lang), lang)
        return
    if len(test_names) > 1:
        await _handle_multi_test_prep(session, test_names, lang)
    else:
        await _speak_single_test_prep(session, test_names[0], lang, "", {})


# ============================================================================= KCD-387 full flow
# "Caller asks whether a test can be collected at home" -- see agent/home_collection_flow.py's own
# module docstring for why this story keeps its own cross-turn state (HomeCollectionState) rather
# than reusing BookingState, and clinic-api/home_collection_service.py's docstring for where every
# fact spoken below actually comes from (never this file, never the model). Deliberately NOT
# integrated with KCD-104's suspend/resume machinery (self.booking/self.suspended) -- this flow runs
# independently of an appointment/test booking in progress, the same way the KCD-382b test-prep
# flow a few functions above does not touch that machinery either.


def _enter_home_collection(session: CallSession) -> hcflow.HomeCollectionState:
    # ADDED BY SOURAV: same "one at a time, a stale one is simply replaced" rule as _enter_task
    # above, applied to this independent flow.
    st = session.home_collection
    if st is None or st.is_stale():
        st = hcflow.new_state()
        session.home_collection = st
    return st


async def _home_collection_lookup_by_phone(session: CallSession, phone: str, lang: str) -> bool:
    # ADDED BY SOURAV: CASE 2 of the story ("I already have a booking") -- the exact same
    # search_bookings/booking_test_names infrastructure _handle_booking_prep_lookup above already
    # uses for KCD-382b, including its own several-bookings-never-bookings[0] rule (KCD-497), never
    # a second lookup implementation. Returns True when it spoke a reply and the turn is finished
    # (nothing found, or several bookings needing disambiguation); False when it resolved the
    # test name(s) onto `st` and the caller-facing flow should continue in THIS turn.
    st = _enter_home_collection(session)
    try:
        found = await _tools.search_bookings(phone, phone=phone)
    except ToolCallError as e:
        logger.warning("[%s] home-collection booking search failed: %s", session.call_id, e)
        await _speak(session, phrase("tool_failure", lang), lang)
        return True
    matches = [m for m in (found.get("matches") or []) if m.get("kind") == "test_booking"]
    if not matches:
        await _speak(session, phrase("test_prep_no_booking_found", lang), lang)
        await _speak(session, missing_slot_prompt("home_collection", "test_name", lang), lang)
        return True
    if len(matches) > 1:
        await _speak(session, multiple_test_bookings_reply(matches, lang), lang)
        return True
    confirmation_id = matches[0]["confirmation_id"]
    try:
        tests_result = await _tools.booking_test_names(confirmation_id)
    except ToolCallError as e:
        logger.warning("[%s] booking_test_names failed for %s: %s", session.call_id, confirmation_id, e)
        await _speak(session, phrase("tool_failure", lang), lang)
        return True
    test_names = [n for n in (tests_result.get("test_names") or []) if n]
    if not test_names:
        await _speak(session, phrase("test_prep_no_booking_found", lang), lang)
        await _speak(session, missing_slot_prompt("home_collection", "test_name", lang), lang)
        return True
    hcflow.merge_test_names(st, test_names)
    st.from_booking, st.confirmation_id = True, confirmation_id
    st.phone = st.phone or phone
    return False


async def _release_home_collection_hold_if_any(session: CallSession) -> None:
    # ADDED BY SOURAV: section 14/30 -- a caller abandoning the flow, correcting the pincode after
    # already holding a slot, or declining at either confirmation gate must not permanently consume
    # real capacity. Freeing it immediately (rather than only relying on the hold's own TTL) is the
    # same "release, don't just let it time out" discipline section 30 asks for.
    st = session.home_collection
    if st is None or not st.hold_token:
        return
    token, st.hold_token = st.hold_token, None
    try:
        await _tools.home_collection_release_hold(token)
    except ToolCallError as e:
        logger.warning("[%s] home-collection release-hold failed (token already expiring): %s", session.call_id, e)


async def _advance_home_collection(session: CallSession, lang: str) -> None:
    # ADDED BY SOURAV: the "do everything automatic this turn, then ask for exactly the one thing
    # still needed" shape the book_appointment dispatch branch already uses -- driven by what is
    # actually filled in on `st`, not a strict stage enum, so information given out of order or
    # several turns apart is never rejected. Handles sections 6-19 (test ID through the "proceed?"
    # gate); _continue_home_collection_after_proceed below picks up from an explicit yes.
    st = session.home_collection
    assert st is not None
    parts: list[str] = []

    # ---- sections 6/7: which test(s) ----
    if not st.test_names:
        if not st.registration_asked:
            st.registration_asked = True
            session.awaiting_home_collection_registration_answer = True
            await _speak(session, phrase("test_prep_ask_registered", lang), lang)
            return
        await _speak(session, missing_slot_prompt("home_collection", "test_name", lang), lang)
        return

    # ---- section 9: the pincode, never inferred from phone/SIM/language/accent ----
    if not st.postal_code:
        await _speak(session, missing_slot_prompt("home_collection", "postal_code", lang), lang)
        return

    # ---- sections 8/9/10: eligibility AND serviceability together, from the one real DB-backed
    # check (enquiry_service.home_collection_eligibility) -- never a separate coarse area guess, and
    # section 10 is satisfied simply by there being no such guess anywhere in this flow.
    if not st.eligibility:
        try:
            result = await _tools.home_collection_eligibility_multi(st.test_names, st.postal_code)
        except ToolCallError:
            await _speak(session, phrase("tool_failure", lang), lang)
            return
        st.eligibility = result.get("results") or []
        st.not_found_tests = result.get("not_found") or []
        st.eligible_tests = [r["test_name"] for r in st.eligibility if r.get("found") and r.get("eligible")]
        parts.append(home_collection_eligibility_reply(result, lang))
        if not st.eligible_tests:
            session.home_collection = None  # nothing eligible/serviceable -- nothing left to continue
            await _speak(session, " ".join(parts), lang)
            return
        st.touch()

    # ---- section 11: the collection date ----
    if not st.date:
        parts.append(missing_slot_prompt("home_collection", "date", lang))
        await _speak(session, " ".join(parts), lang)
        return

    # ---- sections 12/13: real, DB-backed slots for that date/pincode ----
    if not st.chosen_slot:
        if not st.slots_offered:
            try:
                slots_result = await _tools.home_collection_slots(st.postal_code, st.date)
            except ToolCallError:
                await _speak(session, phrase("tool_failure", lang), lang)
                return
            st.slots_offered = slots_result.get("slots") or []
            if not st.slots_offered:
                st.date = None  # asked again below, next turn, for a different day
                parts.append(home_collection_slots_reply([], lang))
                await _speak(session, " ".join(parts), lang)
                return
            if len(st.slots_offered) == 1:
                st.chosen_slot = st.slots_offered[0]  # only one option: nothing to ask, section 14 still holds it below
            else:
                parts.append(home_collection_slots_reply(st.slots_offered, lang))
                st.stage = hcflow.STAGE_CHOOSE_SLOT
                await _speak(session, " ".join(parts), lang)
                return
        else:
            # Offered earlier, still unresolved (the choose-slot interception fell through to a
            # fresh turn that said something else) -- read the options out again rather than stall.
            parts.append(home_collection_slots_reply(st.slots_offered, lang))
            st.stage = hcflow.STAGE_CHOOSE_SLOT
            await _speak(session, " ".join(parts), lang)
            return

    # ---- sections 12/14: claim real capacity before asking the caller anything further ----
    if not st.hold_token:
        try:
            hold = await _tools.home_collection_hold(st.chosen_slot["slot_id"], _caller_phone(session), session.call_id)
        except ToolCallError:
            await _speak(session, phrase("tool_failure", lang), lang)
            return
        if not hold.get("success"):
            parts.append(home_collection_hold_failed_reply(hold, lang))
            st.chosen_slot, st.slots_offered, st.date = None, [], None
            await _speak(session, " ".join(parts), lang)
            return
        st.hold_token = hold["hold_token"]

    # ---- sections 15/16/17/18/19: the deterministic quote, the real payment policy, "proceed?" ----
    if st.quote is None:
        try:
            quote = await _tools.home_collection_quote(st.eligible_tests, st.postal_code)
        except ToolCallError:
            await _speak(session, phrase("tool_failure", lang), lang)
            return
        st.quote = quote
        try:
            policy = await _tools.home_collection_payment_policy(lang)
        except ToolCallError:
            policy = {}
        st.payment_policy_text = policy.get("description") if policy.get("found") else None
        parts.append(home_collection_quote_reply(quote, st.payment_policy_text, lang))
        st.stage = hcflow.STAGE_AWAITING_PROCEED
        await _speak(session, " ".join(parts), lang)
        return

    # Everything through the quote is already known and "proceed?" is either still pending (the
    # early interception in _dispatch_turn owns that yes/no) or was just answered -- continue.
    await _continue_home_collection_after_proceed(session, lang)


async def _continue_home_collection_after_proceed(session: CallSession, lang: str) -> None:
    # ADDED BY SOURAV: sections 20-24 -- patient contact details, then the address (offered from
    # storage, or captured fresh), then its readback/confirmation. Reached only after an explicit
    # "yes" to the sections 15-19 quote/proceed question (never before -- section 19's own rule).
    st = session.home_collection
    assert st is not None

    # ---- section 20: who the booking is for / where to reach them. This codebase has no PIN/OTP
    # verification gate on a NEW appointment or test booking today (book_appointment/book_test only
    # ever require patient_name+phone) -- home collection holds to that exact, already-shipped
    # standard rather than inventing a stricter, inconsistent one for this story alone.
    if not st.patient_name:
        await _speak(session, missing_slot_prompt("home_collection", "patient_name", lang), lang)
        return
    if not st.phone:
        await _speak(session, missing_slot_prompt("home_collection", "phone", lang), lang)
        return

    # ---- sections 21/24: a stored address is OFFERED, once, never silently applied (KCD-499's own
    # confirm-before-use discipline, reused via the same get_preferences call _offer_preferences
    # already makes, not a second implementation of it) ----
    if not st.address_line and not st.stored_address_offered:
        st.stored_address_offered = True
        ident = session.identity
        stored = None
        if _tools is not None and ident.patient_ref is not None:
            try:
                prefs = await _tools.get_preferences(int(ident.patient_ref), _caller_phone(session))
                stored = (prefs.get("preferences") or {}).get("collection_address")
            except (ToolCallError, ValueError, TypeError):
                stored = None
        if stored:
            st.stored_address_text = stored
            st.stage = hcflow.STAGE_OFFER_STORED_ADDRESS
            await _speak(session, home_collection_stored_address_offer(stored, lang), lang)
            return
        # No stored address on file: fall straight through to asking for one fresh, below.

    # ---- sections 21/22: a fresh address, captured as one spoken field (never an invented
    # house/road/locality NLP parser -- section 21's own "don't build one" rule) ----
    missing = hcflow.missing_address_fields(st)
    if missing:
        st.stage = hcflow.STAGE_COLLECT_ADDRESS
        await _speak(session, missing_slot_prompt("home_collection", "address_line", lang), lang)
        return
    conflict_pin = hcflow.address_postal_code_conflict(st)
    if conflict_pin:
        st.address_line = ""  # never guess which of the two pincodes is right
        st.stage = hcflow.STAGE_COLLECT_ADDRESS
        await _speak(session, home_collection_address_pincode_mismatch_reply(conflict_pin, st.postal_code, lang), lang)
        return

    # ---- section 23: read it back, ask for explicit confirmation before anything is written ----
    st.stage = hcflow.STAGE_CONFIRM_ADDRESS
    await _speak(session, home_collection_address_confirm_reply(hcflow.address_display(st), lang), lang)


def _home_collection_final_summary(st: hcflow.HomeCollectionState) -> dict:
    # ADDED BY SOURAV: one place building the dict both the final-confirm readback and (after a
    # real booking write) the result reply format -- never two different summaries of the same facts.
    return {
        "eligible_tests": st.eligible_tests,
        "date": st.chosen_slot.get("date") if st.chosen_slot else st.date,
        "start_time": st.chosen_slot.get("start_time") if st.chosen_slot else None,
        "end_time": st.chosen_slot.get("end_time") if st.chosen_slot else None,
        "address": hcflow.address_display(st),
        "total_inr": (st.quote or {}).get("total_inr"),
    }


async def _book_home_collection(session: CallSession, lang: str) -> None:
    # ADDED BY SOURAV: section 25 -- the one place this whole flow actually writes a real,
    # persistent booking. Reached only from the final-confirm "yes" (see
    # _handle_home_collection_confirmation_turn), never earlier.
    st = session.home_collection
    assert st is not None
    patient_id = None
    ident = session.identity
    if ident.patient_ref is not None:
        try:
            patient_id = int(ident.patient_ref)
        except (TypeError, ValueError):
            patient_id = None
    try:
        result = await _tools.home_collection_book(
            hold_token=st.hold_token,
            test_names=st.eligible_tests,
            postal_code=st.postal_code,
            patient_name=st.patient_name,
            phone=st.phone,
            caller_phone=st.caller_phone,
            address_line=st.address_line,
            locality=st.locality,
            city=st.city,
            state=st.state,
            landmark=st.landmark,
            patient_id=patient_id,
        )
    except ToolCallError as e:
        logger.error("[%s] home-collection booking failed: %s", session.call_id, e)
        await _speak(session, phrase("tool_failure", lang), lang, fallback_reason="tool_failure")
        session.home_collection = None
        return
    st.hold_token = None  # consumed by the booking write either way -- never released again after this
    st.booking_result = result
    await _speak(session, home_collection_booking_result_reply(result, lang), lang)
    session.home_collection = None


async def _handle_home_collection_confirmation_turn(session: CallSession, text: str, lang: str) -> bool:
    # ADDED BY SOURAV: the ONE place every yes/no (or slot-choice) gate in this flow is decided --
    # deterministically, no LLM round trip, the exact same reasoning agent/booking_flow.classify_yes_no's
    # own docstring gives for why a booking confirmation is never left to the model. Returns False
    # when the turn did not answer the pending gate at all, so main.py's caller falls through and
    # handles it as an ordinary fresh turn (the gate stays pending, same as _handle_booking_confirmation_turn).
    st = session.home_collection
    assert st is not None

    if st.stage == hcflow.STAGE_CHOOSE_SLOT:
        chosen = hcflow.resolve_slot_choice(st.slots_offered, text, None, lang)
        if chosen is None:
            return False
        st.chosen_slot, st.slots_offered = chosen, []
        st.touch()
        await _advance_home_collection(session, lang)
        return True

    answer = classify_yes_no(text, lang)
    if answer is None:
        return False

    if st.stage == hcflow.STAGE_AWAITING_PROCEED:
        if answer == "no":
            await _release_home_collection_hold_if_any(session)
            session.home_collection = None
            await _speak(session, home_collection_cancelled_reply(lang), lang)
            return True
        st.touch()
        await _continue_home_collection_after_proceed(session, lang)
        return True

    if st.stage == hcflow.STAGE_OFFER_STORED_ADDRESS:
        if answer == "yes":
            st.address_line = st.stored_address_text or ""
            st.using_stored_address = True
        # "no": fall through to collecting a fresh address below, same turn.
        st.touch()
        await _continue_home_collection_after_proceed(session, lang)
        return True

    if st.stage == hcflow.STAGE_CONFIRM_ADDRESS:
        if answer == "no":
            st.address_line, st.using_stored_address, st.stored_address_offered = "", False, True
            st.stage = hcflow.STAGE_COLLECT_ADDRESS
            await _speak(session, missing_slot_prompt("home_collection", "address_line", lang), lang)
            return True
        st.stage = hcflow.STAGE_FINAL_CONFIRM
        await _speak(
            session, home_collection_final_confirm_reply(_home_collection_final_summary(st), lang), lang
        )
        return True

    if st.stage == hcflow.STAGE_FINAL_CONFIRM:
        if answer == "no":
            await _release_home_collection_hold_if_any(session)
            session.home_collection = None
            await _speak(session, home_collection_cancelled_reply(lang), lang)
            return True
        await _book_home_collection(session, lang)
        return True

    return False


def _mentions_abdomen(test_name: str) -> bool:
    return "abdomen" in test_name.lower()


async def _queue_prescription_callback(session: CallSession, reason: str, text: str, lang: str) -> None:
    """Every time the caller is asked to send a prescription over WhatsApp, a callback is queued with a factual
    summary (agent/call_summary.py) so the human colleague who opens the photo has the call's context next to it --
    KCD-lay-terms. Only queued when a phone is actually known (session.identity.phone): there is no automatic
    caller-ID in this stack (see _handoff_to_human's own docstring), so without one there is nothing to call back on,
    and a tool failure here must never interrupt the call -- the caller was already told what to do."""
    phone = session.identity.phone
    if not phone:
        return
    summary = build_call_summary(reason, lang=lang, transcript=text, phone=phone, call_id=session.call_id)
    try:
        await _tools.request_callback(phone, session.call_id, "asap", reason, summary)
    except ToolCallError as e:
        logger.warning("[%s] could not queue prescription callback (%s): %s", session.call_id, reason, e)


async def _handle_test_group(session: CallSession, hit, lang: str) -> None:
    """story: "Caller asks for a group of tests" (agent/test_groups.py): the members of the group the catalogue has, with
    their live prices -- "হার্টের টেস্ট আমাদের এখানে ৩টা আছে: ইসিজি …" -- instead of one test picked by fuzzy matching.
    Deterministic, no model call, nothing named that the catalogue does not hold."""
    try:
        listing = await _tools.list_tests()
    except ToolCallError as e:
        logger.warning("[%s] test group unavailable (%s)", session.call_id, e)
        await _speak(session, phrase("tool_failure", lang), lang, fallback_reason="tool_failure")
        return
    by_name = {t.get("test_name"): t for t in listing.get("tests", [])}
    members = [by_name[n] for n in hit.tests if n in by_name]
    if not members:
        await _speak(session, phrase("how_else_help", lang), lang)
        return
    session.group_members = (tuple(t.get("test_name") for t in members), session.turn_count, hit.group.label)
    session.group_reasked = False
    if len(members) == 1 and hit.mode == "book":
        # one test of that kind and the caller wants it done: read it back, as for any test said by name
        only = members[0]
        grounded = GroundedTests(names=(only["test_name"],), forms=(spoken_test_name("", only, lang),), exact=True)
        await _confirm_tests(session, "book_test", grounded, "", lang, {}, {})
        return
    await _speak_list(session, test_group_reply(hit.group.label, members, lang, hit.mode), lang, extra_s=LIST_REASK_EXTRA_S)
    if hit.mode == "book":
        session.awaiting_tests = "book_test"  # the next test named is read back and booked, as in the which-tests loop


def _asks_for_the_blood_tests(session: CallSession, text: str, lang: str) -> bool:
    """"কোন কোন রক্ত পরীক্ষা হয়" / "which blood tests are there": the caller names a KIND of test, blood -- not "every test".
    The whole-catalogue list rule skips up to three words between "which" and "tests" (agent/test_list_request.py), so the
    kind was lost and all 34 tests were read out, while the same wish said as "রক্ত পরীক্ষা করাবো" got the blood tests
    (live 2026-10-16: the same sentence, three different answers). Blood is answered by the lay-term net below; this only
    keeps the whole list from taking the sentence first, on exactly the conditions that net answers it on -- a sentence
    about a report / time / place, one that names a test, or a call with a booking open is not this."""
    if session.booking is not None or match_lay_term(text) != BLOOD or mentions_something_else(text):
        return False
    return not _ground_tests_said(session, text, lang, []).names


async def _handle_lay_term(session: CallSession, category: str, text: str, lang: str) -> None:
    """A rural lay term for a test CATEGORY (agent/lay_terms.py) -- "রক্ত পরীক্ষা", "পেটের ছবি", "মাথার ছবি" -- answered
    from the REAL catalogue, never a hardcoded test name. Deterministic, no model call. See agent/lay_terms.py's
    module docstring for why HEAD never names a specific test: the catalogue has none today, and naming one that
    does not exist would be exactly the fabricated fact CLAUDE.md's truth boundary exists to prevent."""
    reason = f"lay_term_{category}"
    if category == BLOOD:
        cat = await _cached_catalogue()
        blood = [t for t in cat.get("tests", []) if t.get("sample_type") == "Blood"]
        names = sorted({t["name"] for t in blood})
        # In the caller's own script when the clinic has a name for EVERY one of them (the catalogue's aliases_bn /
        # aliases_hi); the Latin codes are not speakable to a Bengali or Hindi voice.
        alias_key = {"bn": "aliases_bn", "hi": "aliases_hi"}.get(lang)
        spoken = None
        if alias_key:
            first = {t["name"]: next(iter(t.get(alias_key) or []), None) for t in blood}
            if names and all(first.get(n) for n in names):
                spoken = [first[n] for n in names]
        reply = blood_test_list_reply(names, lang, spoken)
    elif category == ABDOMEN:
        cat = await _cached_catalogue()
        matches = sorted(
            t["name"]
            for t in cat.get("tests", [])
            if t.get("sample_type") == "Imaging" and _mentions_abdomen(t["name"])
        )
        if matches:
            result = await _tools.get_test_rate(matches[0])
            reply = test_rate_reply({"test_name": matches[0]}, result, lang)
        else:  # the catalogue changed and no longer has an abdomen imaging test: honest, not a guess
            reply = phrase("head_imaging_not_listed", lang)
    else:  # HEAD
        reply = phrase("head_imaging_not_listed", lang)
    await _speak(session, reply, lang)
    await _queue_prescription_callback(session, reason, text, lang)


async def _finish_enquiry_turn(
    session: CallSession, text: str, lang: str, intent: str, slots: dict, base_reply: str, data: dict
) -> None:
    """Shared tail for every stateless enquiry branch (KCD-395/KCD-396):
    answers `secondary_intent` if the model extracted one this turn,
    remembers this turn's entities for the NEXT turn's coreference
    resolution, then speaks the combined reply.

    A secondary lookup that fails (ToolCallError) never discards the
    primary answer the caller already got -- it is reported as "couldn't
    check that" addendum instead, same as this call's own primary
    ToolCallError handling one level up in _dispatch_turn, just scoped
    so a second-question failure cannot blow away a first-question
    success."""
    turn_entities = [(intent, slots)]
    secondary_intent = data.get("secondary_intent")
    if secondary_intent:
        secondary_slots = data.get("secondary_slots") or {}
        secondary_slots, _ = resolve_followup_slot(
            secondary_intent, secondary_slots, text, lang, session.last_enquiry_entities
        )
        addendum = None
        try:
            reply2 = await _answer_enquiry_intent(secondary_intent, secondary_slots, lang)
        except ToolCallError as e:
            logger.warning("[%s] secondary-question lookup failed (%s) -- keeping the first answer", session.call_id, e)
            reply2, addendum = None, phrase("tool_failure", lang)
        if reply2:
            base_reply = f"{base_reply} {reply2}"
            turn_entities.append((secondary_intent, secondary_slots))
        else:
            # A second question was asked but could not be answered this
            # turn (missing slot, or the lookup itself failed) -- KCD-395
            # requires it be explicitly addressed, never silently dropped.
            base_reply = f"{base_reply} {addendum or phrase('unclear', lang)}"
    session.last_enquiry_entities = entities_from_turn(*turn_entities)
    session.last_enquiry_turn = session.turn_count
    suggestion, _ = _suggested.get(), _suggested.set(None)
    if suggestion is not None and session.pending_entity is None:
        replay = {
            **data,
            "intent": suggestion["intent"],
            "secondary_intent": None,
            "slots": {**suggestion["slots"], suggestion["slot"]: suggestion["name"]},
        }
        session.pending_entity = entity_text.PendingEntity(
            suggestion["intent"], suggestion["slot"], suggestion["name"], replay
        )
    base_reply = _with_resume(session, base_reply, lang)  # KCD-104: back to the booking, if one is open
    # An answer to the caller's own question starts with the answer (no "thank you for telling me"); KCD-512: a
    # senior's warm closing after.
    base_reply = session.kindness.decorate(base_reply, lang)
    _note(session, "answers_given")
    await _speak(session, base_reply, lang)


def _mark(session: CallSession, name: str) -> None:
    """Record when a stage of THIS turn finished (first occurrence only). Read by _log_timing at first audio."""
    marks = getattr(session, "marks", None)
    if marks is not None and all(n != name for n, _t in marks):
        marks.append((name, time.monotonic()))


def _log_timing(session: CallSession) -> None:
    """One line per turn: where the time from "the turn started" to "the first audio left" went. Latency was being
    felt and reported without a breakdown; this is the breakdown, per stage, in the log."""
    marks, session.marks = getattr(session, "marks", None), None
    if not marks or len(marks) < 2:
        return
    parts, prev = [], marks[0][1]
    for name, t in marks[1:]:
        parts.append(f"{name} {int((t - prev) * 1000)} ms")
        prev = t
    logger.info(
        "[%s] turn timing: %s | total %d ms",
        session.call_id,
        " | ".join(parts),
        int((marks[-1][1] - marks[0][1]) * 1000),
    )


async def _route_and_transcribe_fixed(session: CallSession, utterance_wav: str, early: early_lid.EarlyLID | None = None):
    """Language ID -> routing -> ASR (_route_and_transcribe_raw), then: a call already fixed to Bengali or to Hindi
    is recognised in THAT language.

    Between the two Indic languages language ID is wrong often enough to matter -- live 2026-10-14, a Bengali caller's
    "ফাস্টিং করাবো মোট কত টাকা পড়বে" was labelled Hindi at 0.99, the Hindi recogniser's Devanagari transcript won and
    its invented Hindi function words ("का", "है") passed the grammar check. The reply language was already fixed
    (docs/adr/0003); the recognition was not. A call fixed to its first sentence's language means a caller who
    keeps speaking it, so the other Indic recogniser never wins. English still can (agent/lang_select.pick_candidate's
    own rule: both decoders agree and the text is Latin), and a call fixed to English is left to the routing as it was."""
    lang, result = await _route_and_transcribe_raw(session, utterance_wav, early)
    fixed = session.lang if (REPLY_LANGUAGE_MODE == "sticky" and session.lang_locked_before_turn) else None
    if fixed in ("bn", "hi") and lang == "en" and result is not None:
        # English wins outright when its decoders agree and the text is Latin -- which a short Hindi or Bengali phrase
        # can satisfy ("और बताइए" -> "all but a year", live 2026-10-14). On a call fixed to its language that needs more:
        # a REAL English sentence (five words, nearly all English), or the call's own recogniser not convincing.
        words = (result.text or "").split()
        real_english = len(words) >= 5 and english_word_share(result.text) >= 0.8
        if not real_english:
            own = await _asr_router.transcribe(fixed, utterance_wav)
            if (own.text or "").strip() and own.decoder_agreement >= CONVINCING_MIN_AGREEMENT:
                logger.info(
                    "[%s] English text %r is not a sentence and the %s recogniser is convincing -- staying in %s",
                    session.call_id, len(words), fixed, fixed,
                )
                session.lang_router.note_response_language(fixed)
                return fixed, own
    if fixed in ("bn", "hi") and lang in ("bn", "hi") and lang != fixed and result is not None:
        logger.info(
            "[%s] recognised as %s, but the call is fixed to %s -- using the %s recogniser", session.call_id, lang, fixed, fixed
        )
        session.lang_router.note_response_language(fixed)
        return fixed, await _asr_router.transcribe(fixed, utterance_wav)
    return lang, result


_SPELLED_TABLE: dict[int, dict[str, str]] = {}


def _spelled_table() -> dict[str, str]:
    """letters -> test for the acronyms in the live catalogue's test names; built once per catalogue (agent/spelled_letters.py)."""
    cat = _fast_path.catalogue if _fast_path is not None else None
    if cat is None:
        return {}
    key = id(cat)
    if key not in _SPELLED_TABLE:
        _SPELLED_TABLE.clear()
        _SPELLED_TABLE[key] = acronym_table(cat.test_rows)
    return _SPELLED_TABLE[key]


async def _note_spelled_letters(session: CallSession, lang: str | None, result) -> None:
    """The Bengali / Hindi recogniser folds a spelled acronym ("এইচসিভি") into a pseudo-word ("হপ"); the English one, which
    already ran on this very audio, writes the letters. Read them from it and keep the tests they stand for as evidence for
    this turn's grounding (`session.turn_acronyms`). Only when the Indic reading won: if English won, its text IS the
    transcript. Never raises, never waits long, never changes what the caller is shown or what the transcript says."""
    session.turn_acronyms = ()
    task = session.turn_en_task
    if task is None or result is None or lang not in ("bn", "hi"):
        return
    try:
        en = await asyncio.wait_for(asyncio.shield(task), timeout=2.5)
        names = spelled_tests(getattr(en, "text", "") or "", _spelled_table())
    except Exception as e:  # noqa: BLE001 - an extra reading is never a reason to fail a turn
        logger.info("[%s] spelled-letter reading skipped (%s)", session.call_id, type(e).__name__)
        return
    if names:
        session.turn_acronyms = tuple(names)
        logger.info(
            "[%s] spelled letters in the English reading %r -> %s (the %s text was %r)",
            session.call_id, (en.text or "")[:90], list(names), lang, (result.text or "")[:90],
        )


async def _route_and_transcribe(session: CallSession, utterance_wav: str, early: early_lid.EarlyLID | None = None):
    """Recognition (_route_and_transcribe_fixed), then the spelled-letter evidence from the English reading."""
    session.turn_acronyms = ()
    lang, result = await _route_and_transcribe_fixed(session, utterance_wav, early)
    await _note_spelled_letters(session, lang, result)
    return lang, result


async def _route_and_transcribe_raw(session: CallSession, utterance_wav: str, early: early_lid.EarlyLID | None = None):
    """LID -> routing decision -> ASR. Returns (language, ASRResult), or
    (None, None) when the router says a human should take the call (or the call has
    been guessing at its language for too many turns in a row -- see `settle`).

    `early` is the language identification started on the first words of this turn while the caller was
    still talking (agent/early_lid.py). When it is decisive it is used and the whole utterance is not identified
    again; otherwise (or if it is absent, late or failed) the whole utterance is identified, as before."""
    session.turn_en_task = None
    if _lid is None or len(_languages_active) < 2:
        return "bn", await _asr_router.transcribe("bn", utterance_wav)

    # Language ID (CPU, ~0.19 s) and recognition used to run one after the other. They now overlap: the recognisers
    # that are almost always needed -- the language this call has been in, and English (cheap, and never ruled out by
    # language ID) -- start at the SAME moment as language ID, and any other one is started the moment it says so.
    # A recogniser nobody needs is simply not run (agent/lang_select.engines_needed): Hindi's ~0.6 s of GPU time was
    # being spent on every Bengali turn for a result that could never win.
    prior = session.lang_router.previous_language or "bn"
    # The language this call is in. On the FIRST caller turn that is the language the agent greeted in (session.lang,
    # Bengali here), not "none": live 2026-10-07, a Bengali caller's first words "সিবিসি টেস্টের দাম কত?" -- mostly
    # loanwords, and "দাম" is Hindi too -- scored hi 0.80 / bn 0.20, logged "not decisive", and with no call language
    # to stay with the whole call was answered in Hindi. Leaving it still only takes a decisive language ID
    # (agent/lang_select.SWITCH_MIN_LID), English's own gate, or an explicit request.
    call_language = session.lang_router.previous_language or session.lang
    tasks: dict[str, asyncio.Future] = {}
    took: dict[str, float] = {}

    def start(lang: str) -> None:
        if lang in tasks or lang not in _languages_active:
            return

        async def run(lang=lang):
            t0 = time.perf_counter()
            try:
                return await _asr_router.transcribe(lang, utterance_wav)
            finally:
                took[lang] = (time.perf_counter() - t0) * 1000

        tasks[lang] = asyncio.ensure_future(run())
        tasks[lang].add_done_callback(lambda f: f.cancelled() or f.exception())  # an unused failure is not an error
        if lang == "en":
            session.turn_en_task = tasks[lang]  # read again by _note_spelled_letters, whoever wins the turn

    for lang in dict.fromkeys((prior, "en")):
        start(lang)
    lid = await early_lid.resolve_early(early, _languages_active)
    if lid is not None:
        logger.info(
            "[%s] early LID used: %s %.2f (identified on the first words, before the turn ended)",
            session.call_id,
            lid.language,
            lid.confidence,
        )
    else:
        try:
            lid = await asyncio.to_thread(_lid.identify_path, utterance_wav)
        except Exception as e:  # noqa: BLE001 - a LID fault must degrade the turn, not kill it
            logger.warning("[%s] LID failed (%s) -- treating as unknown", session.call_id, e)
            lid = LIDResult(language="unknown", confidence=0.0)
    _mark(session, "lid")

    def settle(lang: str, result):
        """Every route out of here passes through this: report the turn to the router, and if the call has now
        been ambiguous (language ID unsure AND the recogniser not convincing) for more turns in a row than it
        tolerates, stop guessing -- (None, None), which the caller turns into a kind re-ask and then a person."""
        decision = session.lang_router.note_turn_outcome(outcome_is_ambiguous(lid, result, _languages_active))
        if decision is not None:
            logger.warning("[%s] %s -> %s", session.call_id, decision.action, decision.reason)
            return None, None
        return lang, result

    async def outcomes_for(langs: list[str]) -> list[tuple[str, object]]:
        for lang in langs:
            start(lang)
        done = await asyncio.gather(*[tasks[lang] for lang in langs if lang in tasks], return_exceptions=True)
        good, order = [], [lang for lang in langs if lang in tasks]
        for lang, out in zip(order, done):
            if isinstance(out, BaseException):
                logger.warning("[%s] ASR %s failed: %s", session.call_id, lang, out)
            else:
                good.append((lang, out))
        if not good:
            raise next(o for o in done if isinstance(o, BaseException))
        logger.info(
            "[%s] ASR timings: %s", session.call_id, " | ".join(f"{lg} {int(took.get(lg, 0))} ms" for lg, _ in good)
        )
        return good

    async def english_if_heard(lang: str, result):
        """A DECISIVE language ID can still be wrong about English. Live 2026-10-09: an English caller was identified as
        Bengali at 0.97 on the first words, only the Bengali recogniser's transcript was looked at ("উইচ টেস্ট আর
        অ্যাভেলেবল লেয়ার"), and the turn was answered as gibberish. The English recogniser is already running for every
        turn (started above), so its transcript costs no more time; it overrules ONLY by pick_candidate's own English
        rule -- decoders agree, Latin text, and real English words or an English language-ID score -- so Bengali and
        Hindi speech that merely produces Latin-looking text still stays where it is."""
        if lang == "en" or "en" not in _languages_active:
            return lang, result
        try:
            heard = dict(await outcomes_for(["en"]))
        except Exception as e:  # noqa: BLE001 - the English check is an extra; its failure leaves the turn as it was
            logger.warning("[%s] English check after a decisive %s identification failed: %s", session.call_id, lang, e)
            return lang, result
        winner = pick_candidate([(lang, result), ("en", heard["en"])], lid.scores or None, call_language)
        if winner[0] != "en":
            return lang, result
        logger.info(
            "[%s] LID was decisive for %s, but the English recogniser heard English (agreement %.2f) -- using English",
            session.call_id, lang, heard["en"].decoder_agreement,
        )
        session.lang_router.note_response_language("en")
        return winner

    # LID's top label alone is not trusted: Indian-accented English is labelled Hindi at ~0.9, or not found at all
    # (measured; see agent/lang_select.py). Unless LID is decisive, run the recognisers that can still matter and keep
    # the one whose decoders agree AND whom language ID believes.
    verify = languages_to_verify(lid.language, lid.scores, _languages_active) if lid.scores else None
    if verify:
        # The call's own language is always among them (engines_needed's docstring): its recogniser is already running.
        need = engines_needed(lid.language, lid.scores, _languages_active, prior=call_language)
        outcomes = await outcomes_for(need)
        lang, result = pick_candidate(outcomes, lid.scores, call_language)
        logger.info(
            "[%s] LID %s %s not decisive -> ran %s, chose %s (%s)",
            session.call_id,
            lid.language,
            {k: round(v, 2) for k, v in lid.scores.items()},
            "+".join(lg for lg, _ in outcomes),
            lang,
            # agreement / words each recogniser wrote: an EMPTY transcript reads "1.00/0w" (both decoders returned nothing)
            ", ".join(f"{lg}:{r.decoder_agreement:.2f}/{len((r.text or '').split())}w" for lg, r in outcomes),
        )
        session.lang_router.note_response_language(lang)
        return settle(lang, result)

    decision = session.lang_router.route(lid)
    logger.info(
        "[%s] LID %s %.2f -> %s %s (%s)",
        session.call_id,
        lid.language,
        lid.confidence,
        decision.action,
        decision.language or "",
        decision.reason,
    )

    if decision.action == "handoff_human":
        return None, None

    if decision.action == "dual_asr" and decision.secondary_language:
        primary, secondary = decision.language, decision.secondary_language
        # Plus the call's own language, so pick_candidate has it to stay with (agent/lang_select.engines_needed).
        both = await outcomes_for(list(dict.fromkeys([primary, secondary, call_language])))
        if len(both) >= 2:
            return settle(*pick_candidate(both, lid.scores, call_language))
        return settle(*both[0])

    chosen = decision.language
    if chosen != call_language and call_language in _languages_active:
        # Language ID was decisive for a language the call is not in. Between Bengali and Hindi that is not enough on
        # its own (agent/lang_select.transcript_supports_switch): the call language's recogniser is already running,
        # so its transcript decides whether the caller really changed language or said loanwords.
        heard = dict(await outcomes_for([chosen, call_language]))
        if chosen in heard and call_language in heard:
            if not transcript_supports_switch(chosen, heard[chosen].text, call_language, heard[call_language].text):
                logger.info(
                    "[%s] LID said %s, but its transcript carries no more %s grammar than the %s one -- staying in %s",
                    session.call_id, chosen, chosen, call_language, call_language,
                )
                session.lang_router.note_response_language(call_language)
                return settle(*await english_if_heard(call_language, heard[call_language]))
            return settle(*await english_if_heard(chosen, heard[chosen]))
        return settle(*next(iter(heard.items())))
    return settle(*await english_if_heard(chosen, (await outcomes_for([chosen]))[0][1]))


# ADDED BY SOURAV: story "Caller has reached the hospital by mistake" -- the three small helpers the unrelated-caller path needs.
def _unrelated_flow_active(session: CallSession) -> bool:
    """True when a hospital task or an open question is already in progress, so a casual sentence in the middle of it ("bhai waise
    kaise ho?") is NOT read as a wrong caller and can never close the call or disturb the task. Deliberately wide: every state
    machine _dispatch_turn already honours (booking, home collection, history, security, offers, yes/no gates, pending entity)."""
    booking = session.booking
    return bool(
        (booking is not None and booking.stage != "done" and not booking.is_stale())
        or (
            session.home_collection is not None
            and session.home_collection.stage != hcflow.STAGE_DONE
            and not session.home_collection.is_stale()
        )
        or session.history_state
        or session.awaiting_handoff_offer
        or session.awaiting_clinician_offer
        or session.awaiting_callback_phone
        or session.pending_entity is not None
        or (session.security is not None and not session.security.finished)
        or session.awaiting_draft_answer
        or session.awaiting_pref_answer
        or session.awaiting_resume
        or session.suspended is not None
        or session.pending_enquiry is not None
        or session.awaiting_test_prep_registration_answer
        or session.awaiting_test_prep_booking_phone
        or session.awaiting_home_collection_registration_answer
        or session.awaiting_home_collection_booking_phone
    )


def _catalogue_entity_in(norm_text: str, lang: str) -> bool:
    """The live catalogue's half of the hospital veto (agent/unrelated_caller.has_hospital_cue): does the utterance name a test, an FAQ
    topic or a FULL doctor name? A doctor's FIRST name alone is deliberately not checked -- "Suresh hai kya" must stay a bare person, never
    a doctor lookup. Reuses the catalogue FastPath already holds; with no catalogue loaded it simply answers no (the static cue tables in
    agent/unrelated_caller.py still veto)."""
    if _fast_path is None:
        return False
    catalogue = _fast_path.catalogue
    for lg in dict.fromkeys((lang, "en")):
        for kind, floor in (("test", COMMIT_FLOOR), ("faq", FAQ_COMMIT_FLOOR), ("doctorfull", 0.9)):
            key, _form, score = catalogue.match(norm_text, kind, lg, floor)
            if key and score >= floor:
                return True
    return False


async def _handle_unrelated_caller(session: CallSession, lang: str, source: str) -> None:
    """A clearly unrelated / wrong-number caller: a short fixed polite reply the first time, a polite close on the second CONSECUTIVE time.

    Reached only after every hospital guard, an active-flow check and the hospital veto have said "this is not a hospital request"
    (see the two call sites in _dispatch_turn). The call is filed under its OWN outcome, "unrelated_caller", from the very first unrelated
    turn -- a real wrong number usually hangs up after one "sorry" -- and call_score leaves such a call unscored, so it can never be read as a
    successfully served (contained) hospital call. The outcome is put back to "completed" by the reset at the top of the next processed turn
    if the caller then asks for something real. Fixed phrases only; nothing is asked, nothing is stored, no caller text is recorded.
    """
    session.unrelated_count = session.unrelated_prev + 1
    session.outcome = "unrelated_caller"
    _note(session, "unrelated_caller")
    if session.unrelated_count >= unrelated_caller.CLOSE_AT:
        unrelated_caller_events.record("closed", source, lang)
        logger.info("[%s] unrelated caller (%s): second consecutive turn, closing politely", session.call_id, source)
        await _end_call(session, lang, "unrelated_final")
        return
    unrelated_caller_events.record("first", source, lang)
    logger.info("[%s] unrelated caller (%s): first turn, one more chance", session.call_id, source)
    await _speak(session, phrase("unrelated_first", lang), lang)


async def _dispatch_turn(session: CallSession, utterance_wav: str, early: early_lid.EarlyLID | None = None):
    """One full turn: LID -> ASR -> intent -> tool -> templated reply -> TTS.
    Serialized per-call via session.dispatch_lock so replies never
    interleave, even if the caller starts talking again immediately.
    `early`: the language ID that was started on this turn's first words (agent/early_lid.py)."""
    async with session.dispatch_lock:
        session.turn_epoch = session.speak_epoch  # this turn is current; an interrupt from now on makes it stale
        session.acks.next_turn()
        session.turn_acknowledged = False
        session.reply_noted = False
        prior_lang = session.lang  # the language the call was in BEFORE this turn's recognition (see _keep_language)
        session.lang_locked_before_turn = session.lang_locked
        # An answer to a question we asked: the last thing we said had a question mark, or a flow that asks in commands
        # ("please tell me your full name") is waiting on this very turn.
        session.answering = (
            session.awaiting_answer
            or session.history_state in ("need_phone", "need_name")
            or (session.security is not None and not session.security.finished)
        )
        session.awaiting_answer = False
        session.test_list_stash = None
        session.open_question = None
        session.question_silence_prompts = 0
        session.silence_since = None
        session.turn_started_at = time.monotonic()
        session.marks = [("start", session.turn_started_at)]
        channel_quality = session.call_state.channel_quality
        analysis: dict | None = None
        audio_issues: list[str] = []
        asr_wav = utterance_wav
        temp_wavs: list[str] = []
        try:
            if NEAR_END_ATTENTION != "off" and session.near_end.established:
                attended = await asyncio.to_thread(
                    _attend_wav_to_path, utterance_wav, session.near_end.level_dbfs, session.near_end.f0_hz or 0.0
                )
                if attended:
                    temp_wavs.append(attended)
                    asr_wav = attended
            if CONDITION_INPUT != "off":
                conditioned = await asyncio.to_thread(_condition_wav_to_path, asr_wav, CONDITION_INPUT)
                if conditioned:
                    temp_wavs.append(conditioned)
                    asr_wav = conditioned
            _mark(session, "prep")
            # `early` is passed only when there is one, so a stand-in for this stage need not know about it.
            lang, asr_result = await _route_and_transcribe(session, asr_wav, **({"early": early} if early else {}))
            _mark(session, "asr")
            # KCD-075/KCD-076: measured inside its own budget slice, on
            # the SAME clip ASR already read, before the finally below
            # deletes it. A slow or failed classification just keeps
            # last turn's label -- never blocks or fails the turn.
            channel_quality = await run_within_budget(
                "channel_quality",
                CHANNEL_QUALITY_BUDGET_S,
                _classify_channel_from_wav_path,
                utterance_wav,
                previous_value=channel_quality,
            )
            # Why is this clip hard to hear, and does the speaker sound older?
            # A slow or failed analysis just means no verdict this turn.
            analysis = await run_within_budget(
                "audio_quality",
                AUDIO_ANALYSIS_BUDGET_S,
                _analyze_utterance_from_wav_path,
                utterance_wav,
                previous_value=None,
            )
            audio_issues = analysis["audio"].issues if analysis else []
            for issue in audio_issues:
                audio_issue_buckets.record(issue, "seen", lang or session.lang)
            # Appendix F channel buckets, on the same vocabulary as the offline evaluation:
            # the live rate of cross_talk / noisy / narrowband turns (KCD-053/055).
            golden_buckets.record("turn", "analysed", lang or session.lang)
            for bucket in channel_buckets(audio_issues, channel_quality):
                golden_buckets.record(bucket, "turn", lang or session.lang)
            asr_result = await _retry_on_enhanced_audio(
                session,
                utterance_wav,
                lang,
                asr_result,
                audio_issues,
                analysis["audio"].duration_s if analysis else None,
            )
        except Exception as e:
            logger.exception("[%s] ASR/LID stage failed: %s", session.call_id, e)
            await _speak(session, phrase("llm_failure", session.lang), session.lang, fallback_reason="llm_failure")
            return
        finally:
            with contextlib.suppress(OSError):
                os.remove(utterance_wav)
            for extra in temp_wavs:
                with contextlib.suppress(OSError):
                    os.remove(extra)

        # KCD-053: a much quieter utterance in a DIFFERENT voice is someone else in the room,
        # not the caller. It never opens a turn; the call stays silent for it. The same voice,
        # merely quieter, is still the caller and goes on to the empathetic re-ask.
        if analysis and analysis.get("near_end"):
            level_dbfs, f0_hz = analysis["near_end"]
            verdict = session.near_end.judge(level_dbfs, f0_hz)
            if verdict.background:
                session.near_end.reject()
                audio_issue_buckets.record("background_utterance", "dropped", lang or session.lang)
                logger.info("[%s] background utterance dropped (%s)", session.call_id, verdict.reason)
                return
            session.near_end.accept(level_dbfs, f0_hz)

        if lang is None:
            # Ask again, kindly, before any hand-off -- an unidentifiable
            # utterance is usually a faint, noisy or mumbled one, not a
            # request for a person (agent/reask_policy.py).
            await _reask_or_handoff(
                session,
                session.reask.decide(language_ambiguous=True, audio_issues=audio_issues),
                session.lang,
                # a call already fixed to its language is spoken to in that language only, never "in every language"
                languages=(session.lang,) if session.lang_locked_before_turn else _languages_active,
            )
            return
        detected_lang = lang  # what the CALLER actually spoke this turn -- kept for analytics; never overridden
        _rec(session, "language", detected_lang)
        # what the AGENT replies in -- the language of the call's first real sentence; see the function. The text is
        # passed so a bare "hello" does not fix it.
        lang = _resolve_reply_language(
            session, lang, getattr(asr_result, "text", None), getattr(asr_result, "decoder_agreement", None)
        )
        confidence = confidence_state(getattr(asr_result, "decoder_used", None), asr_result.decoder_agreement)
        apply_confidence(session.call_state, confidence != VERIFIED)
        apply_channel_quality(session.call_state, channel_quality)
        channel_quality_buckets.record(channel_quality, "seen", lang)
        logger.debug("[%s] call_state %s", session.call_id, session.call_state.to_log_dict())

        text = asr_result.text.strip()
        if not text:
            logger.info("[%s] ASR returned empty text", session.call_id)
            lang = _keep_language(session, prior_lang)
            await _reask_or_handoff(
                session,
                session.reask.decide(asr_empty=True, audio_issues=audio_issues),
                lang,
                fallback_reason="asr_empty",
            )
            return
        # A possible medical emergency outranks everything: it is checked on EVERY turn, in every
        # language, at any ASR confidence, before any re-ask, model call or booking step, and it
        # abandons whatever was in progress (agent/emergency.py).
        if detect_emergency(text):
            logger.warning("[%s] possible emergency in the transcript", session.call_id)
            await session.send_json("User", text)
            session.turn_count += 1
            session.booking, session.pending_entity, session.history_state = None, None, None
            _note(session, "emergency")
            apply_caller_state(session.call_state, "emergency")
            await _speak(session, phrase("emergency_notice", lang), lang)
            await _handoff_to_human(session, "emergency", (lang,))
            session.outcome = "emergency"
            return
        # Swearing at the agent: a calm fixed boundary in the caller's own language, never the model (agent/abuse.py);
        # the third time in one call, a courteous close.
        if abuse.is_abusive(text):
            session.abuse_count += 1
            _note(session, "abuse_turns")
            session.signals.turns += 1
            # a call already fixed to its language (BEFORE this turn) is answered in it; an abusive FIRST turn fixes
            # nothing (_keep_language below takes the lock back) and is answered in the script it was written in
            reply_lang = session.lang if (REPLY_LANGUAGE_MODE == "sticky" and session.lang_locked_before_turn) else (
                _abuse_reply_language(text, lang, prior_lang)
            )
            _keep_language(session, reply_lang)
            logger.info("[%s] abusive turn %d", session.call_id, session.abuse_count)
            await session.send_json("User", text)
            session.turn_count += 1
            if abuse.closes_the_call(session.abuse_count):
                session.outcome = "abusive_caller"
                await _end_call(session, reply_lang, abuse.response_key(session.abuse_count))
            else:
                await _speak(session, phrase(abuse.response_key(session.abuse_count), reply_lang), reply_lang)
            return
        # A yes/no to a confirmation is legitimately one short word; the
        # jumbled-transcript checks would misread it as a fragment.
        # The same holds for a dictated phone number, a spelled name or a bare
        # "না" while a booking is collecting slots: all legitimately short or
        # tokenised, so only decoder disagreement is meaningful then.
        in_booking_flow = session.booking is not None and (
            session.booking.stage in ("confirming", "awaiting_charge_confirm") or not session.booking.is_stale()
        )
        problem = transcript_problem(
            text,
            detected_lang,  # the script this text was actually RECOGNISED in, not the (now sticky) reply language
            analysis["audio"].duration_s if analysis else None,
            asr_result.decoder_agreement,
            slot_answer=in_booking_flow
            or session.pending_entity is not None
            or session.awaiting_handoff_offer
            or session.awaiting_clinician_offer  # a bare "yes" to "shall I connect our clinician?"
            or session.awaiting_callback_phone  # a dictated phone number is legitimately just digits
            or session.history_state is not None
            or session.awaiting_draft_answer
            or session.awaiting_pref_answer
            or session.awaiting_resume
            or session.awaiting_close_answer
            or session.awaiting_test_prep_registration_answer  # KCD-382b: a bare yes/no
            or session.awaiting_test_prep_booking_phone  # KCD-382b: a dictated phone number
            or session.awaiting_home_collection_registration_answer  # ADDED BY SOURAV: KCD-387, same shape
            or session.awaiting_home_collection_booking_phone  # ADDED BY SOURAV: KCD-387, same shape
            # ADDED BY SOURAV: a bare "yes"/"no"/pincode/"the first one" while the home-collection
            # flow is active is just as legitimately short as an in-progress appointment booking.
            or (session.home_collection is not None and not session.home_collection.is_stale())
            # story: "Caller asks what they have booked" -- a bare "হ্যাঁ" to "shall I resend
            # the written confirmation?" is an answer, not a fragment.
            or session.pending_resend is not None
            # Same omission, found while adding the line above: a bare yes to the date check
            # ("আগামীকাল মানে মঙ্গলবার ... ঠিক আছে?") and to the misheard-name readback ("do you
            # mean Rahul Das?") was being discarded as jumbled, so neither question could be
            # answered the natural way. Both are closed yes/no questions the agent just asked.
            or session.pending_dated_enquiry is not None
            or session.awaiting_name_confirm,
        )  # KCD-104; "anything else?" -> a bare no: a bare yes or no to "shall I go back to it" is an answer
        if problem:
            logger.info("[%s] jumbled transcript (%s)", session.call_id, problem)
            lang = _keep_language(session, prior_lang)
            await _reask_or_handoff(
                session, session.reask.decide(transcript_issue=problem, audio_issues=audio_issues), lang
            )
            return
        session.reask.note_success()
        session.signals.turns += 1
        # The CALLER's language history (for the flip-detection signal below and call_score.py's language_flips) --
        # what was actually SAID each turn, not the (now sticky) reply language, which barely varies by design.
        session.lang_history.append(detected_lang)
        if (
            len(session.lang_history) >= 3
            and session.lang_history[-1] == session.lang_history[-3] != session.lang_history[-2]
        ):
            _note(session, "language_flips")  # A, B, A: the bot moved the call to B and the caller went back
        if _caller_thanked(text, detected_lang):  # detecting a thank-you in what was actually said, not the reply lang
            _note(session, "caller_thanked")
        session.silence_prompts = 0
        # The ONE place a readable turn is shown to the caller and counted. Unconditional, so every branch below has
        # already been through it: a branch that sends again puts the caller's own sentence on screen TWICE, which
        # reads as "it ignored me and I repeated myself". Live 2026-10-09, a Hindi caller asked for the departments
        # once; the transcript showed the question, the automated-assistant notice, THE QUESTION AGAIN, then the
        # answer, and the call was reported as not picking the question up the first time. Ten branches were doing
        # it (doctor list, report, complaint, callback, compare, clinical, claim ...). The double `turn_count += 1`
        # went with it, which also aged every turn-count TTL twice as fast on exactly those paths.
        await session.send_json("User", text)
        session.turn_count += 1
        after_prompt, session.awaiting_close_answer = session.awaiting_close_answer, False
        answer = classify_yes_no(text, lang) if after_prompt else None
        # "end the call", "শেষ করে দিন" (as the answer to "anything else?"): the call ends, decided by words, not the model
        if call_end.wants_to_end(text, lang, after_prompt=after_prompt) or answer == "no":
            _note(session, "ended_by_caller")
            await _end_call(session, lang, "silence_goodbye")
            return
        if answer == "yes":
            await _speak(session, phrase("silence_go_on", lang), lang)
            return
        # ADDED BY SOURAV: story "Caller has reached the hospital by mistake" -- the consecutive-unrelated counter. A readable turn has
        # reached this point (emergency, abuse, transcript quality and call-end have all had their say above and returned), so whatever
        # this turn turns out to be, the previous unrelated streak is over: remember it, zero it, and let _handle_unrelated_caller raise it
        # again only if THIS turn is unrelated too. That is what makes "arre bhai kaise ho" -> "CBC ka rate kya hai" never close the call
        # and never leave a stale count behind. The outcome and the score flag are put back with it, so a caller who recovers is an
        # ordinary hospital call again. A hospital task already in progress is snapshotted here, BEFORE the flow gates below consume
        # their one-shot flags.
        session.unrelated_prev = session.unrelated_count
        session.unrelated_count = 0
        if session.outcome == "unrelated_caller":
            session.outcome = "completed"
        session.signals.unrelated_caller = False
        session.unrelated_flow_active = _unrelated_flow_active(session)
        code_switch_buckets.record(mixture_bucket(text), "seen", lang)

        # KCD-353: the automated-assistant notice, in the caller's own language, on the first READABLE turn in that
        # language -- decided here, before the shortcuts below, because they answer and return without ever reaching the
        # check after the model (live 2026-10-09: two list turns went by, and the notice landed in the middle of the
        # third, between the filler and the read-back). Readable = it passed the jumbled-transcript check above AND has
        # at least DISCLOSURE_MIN_WORDS words, so the two-word opener that once got the notice and then "I did not
        # understand" ("मेरे को") still leaves it owed. A request for a person is honoured at once, notice or not; the
        # check after the model stays as the fallback for a short turn it understood.
        # (Not on the turn that IS a request for a language: its answer is in the language asked for and the notice
        # follows in that language on the next turn, instead of once in the language the request was made in.)
        if (
            lang not in session.disclosed_langs
            and len(text.split()) >= DISCLOSURE_MIN_WORDS
            and not asks_for_a_person(text, lang)
            and not (detect_language_switch_request(text, detected_lang) or detect_language_switch_request(text, lang))
        ):
            session.disclosed_langs.add(lang)
            await _speak(session, disclosure_for(lang), lang)

        # Ported story "Caller asks whether their result is dangerous"
        # (agent/clinical_safety.py). Two halves of one exchange, kept together.
        #
        # First: the answer to the offer this guard made on an earlier turn. A
        # "yes" reaches a clinician under this story's own reason code, never
        # the history flow's. Anything that is neither yes nor no lets the offer
        # lapse and the turn is handled normally -- the same courtesy
        # _continue_history_flow() shows its own offer.
        if session.awaiting_clinician_offer:
            session.awaiting_clinician_offer = False
            offer_answer = classify_yes_no(text, lang)
            if offer_answer in ("yes", "no"):
                if offer_answer == "yes":
                    await _handoff_to_human(session, session.clinician_offer_reason, (lang,))
                else:
                    await _speak(session, phrase("silence_go_on", lang), lang)
                return

        # Second: the guard itself. It runs before the fast path, the semantic
        # cache and the model, because "routed to a human BY POLICY rather than
        # by prompt wording" is the acceptance criterion -- a prompt instruction
        # is advice a 7B model can be argued out of, and a third of this
        # module's phrase list exists because an adversarial suite proved
        # exactly that. Checked BEFORE the complaint guard below, which is the
        # order the module this was ported from tested.
        #
        # Nothing here judges the result. The reply is fixed text that says who
        # WILL explain it and offers to connect them: without that offer a
        # frightened caller gets the generic greeting this turn used to fall
        # through to, which is the rebuff the story exists to prevent.
        # "আমি ড্রাইভ করে আসব, কোনো সমস্যা আছে?" ("is there any problem if I drive there?") carries the guard's phrase
        # "সমস্যা আছে" and was answered "a doctor must explain this" (pod, 2026-10-13). A sentence about bringing a car, with
        # nothing medical in it (agent/parking_question.py), is a parking question.
        if is_clinical_interpretation(text) and not asks_about_parking_or_a_car(text):
            logger.info("[%s] clinical interpretation asked for; offering a clinician", session.call_id)
            _note(session, "clinical_questions")
            session.awaiting_clinician_offer = True
            session.clinician_offer_reason = "clinical_interpretation"
            await _speak(session, phrase("clinical_interpretation", lang), lang)
            return

        # Ported story "Caller wants to make a complaint" (agent/complaint.py).
        #
        # Checked BEFORE asks_for_a_person() just below, and before agent/anger.py's
        # check inside _update_caller_state(): "I want to speak to someone about a
        # complaint" matches all three guards, and this is the most specific and the
        # most complete of them -- it keeps the caller's words verbatim AND routes to
        # a person, where the other two only route. It returns, so neither of the
        # other two runs for this turn, which is how the story's "anger enhances the
        # complaint flow, it does not compete with it" is honoured without either
        # module knowing about the other.
        #
        # The reply is fixed text (agent/phrases.py: complaint_acknowledged), never
        # the model: acceptance criterion 5 forbids defending the clinic or
        # explaining the complaint away, and a sentence the model writes is a
        # sentence that can do both.
        #
        # The verbatim text goes to clinic-api's `complaints` table and nowhere else
        # -- the escalation ledger and the call record stay text-free by design
        # (agent/call_record.py). A failure to store it must never cost the caller
        # their acknowledgement or their hand-off, so the tool call is suppressed on
        # error exactly as every other durable write on a spoken path here is.
        if is_complaint(text):
            _, family = detect_complaint(text)
            logger.info("[%s] complaint filed (%s phrase list)", session.call_id, family)
            _note(session, "complaint_filed")
            with contextlib.suppress(ToolCallError):
                await _tools.submit_complaint(session.call_id, text, family or "", lang, session.identity.phone)
            await _speak(session, phrase("complaint_acknowledged", lang), lang)
            await _handoff_to_human(session, "complaint", (lang,))
            return

        # Ported story "Caller wants to speak to a doctor personally"
        # (agent/doctor_personal_request.py). Checked next to asks_for_a_person()
        # below because it is the same KIND of request, and immediately BEFORE it
        # because the two want different answers: a caller asking for the front
        # desk can be handed straight there, and a caller asking for a doctor
        # cannot be -- there is no clinician on this line. The noun lists do not
        # overlap ("doctor" and "clinician" are deliberately absent from
        # agent/human_request.py's front-desk list), so this ordering decides
        # nothing today; it is fixed so that widening either list later cannot
        # silently turn a doctor request into a front-desk hand-off.
        #
        # The reply is fixed text and never names a doctor, including one the
        # caller just named: there is no way to reach a clinician from this call,
        # so anything warmer would be a promise this stack cannot keep.
        if is_doctor_personal_request(text):
            logger.info("[%s] caller asked to speak to a doctor personally", session.call_id)
            await _speak(session, phrase("doctor_personal_request", lang), lang)
            return

        # KCD-353: a request for a person is honoured IMMEDIATELY -- deterministic, before any
        # model call, before intent extraction, with no "did you really mean it".
        if asks_for_a_person(text, lang):
            logger.info("[%s] caller asked for a person", session.call_id)
            await _handoff_to_human(session, "caller_requested", (lang,))
            return

        # Ported story "Caller asks to be called back" (agent/callback_request.py).
        #
        # This stack has NO outbound calling, so the whole feature is: write the
        # request down for a colleague, and say exactly that. The durable half
        # already existed before this merge -- clinic-api's callback_requests
        # table and _tools.request_callback(), used today when a hand-off happens
        # and a number is known -- so what was missing was only the caller-facing
        # half: noticing that the caller ASKED.
        #
        # No promise is made about WHEN. The original module decided that from the
        # clinic's opening hours; this repository stores hours only as localised
        # free text (clinic-api/models.py DepartmentHours), and reading a time
        # window out of prose to promise a callback would be inventing a fact.
        #
        # Checked after agent/doctor_personal_request.py, which is the right
        # precedence: "have the doctor call me" is answered there, because no
        # specific doctor can be promised. The phrases here are the generic ones.
        if session.awaiting_callback_phone:
            session.awaiting_callback_phone = False
            dictated = _parse_spoken_phone(text)
            if dictated:
                session.identity.phone = session.identity.phone or dictated
                await _queue_callback_request(session, dictated, lang)
                return
            # No number heard: the request lapses rather than trapping the caller
            # in a loop, and this turn is handled normally. Same courtesy the
            # clinician offer above shows an answer that is neither yes nor no.

        if asks_for_a_callback(text):
            logger.info("[%s] caller asked to be called back", session.call_id)
            if not CALLBACKS_ENABLED:
                await _speak(session, phrase("callback_unavailable", lang), lang)
                return
            known = session.identity.phone
            if known:
                await _queue_callback_request(session, known, lang)
            else:
                session.awaiting_callback_phone = True
                await _speak(session, phrase("callback_need_number", lang), lang)
            return

        # Ported story "Caller asks to compare two options" (agent/compare_flow.py).
        #
        # Runs BEFORE the fast path, and that placement is the point. The fast
        # path's own `_ambiguous_test()` sees two tests fitting the words almost
        # equally well and abstains by design, so a comparison goes to the model
        # -- which has no compare intent, because this merge leaves the intent set
        # untouched. The caller asked about two things and was told the price of
        # one. This guard answers it from two real lookups instead.
        #
        # Both names are resolved against the live catalogue with the SAME matcher
        # and the SAME floor the fast path uses, and the second is found with
        # `skip_name` so it cannot be the first one again. If only one resolves,
        # the caller is ASKED for both: comparing against a guessed second item
        # would state a difference that was never computed from real data.
        if asks_to_compare(text) and _fast_path is not None:
            first, _form_a, _score_a = _fast_path.catalogue.match(text, "test", lang, COMMIT_FLOOR)
            second, _form_b, _score_b = _fast_path.catalogue.match(
                text, "test", lang, COMMIT_FLOOR, skip_name=first
            )
            if first and second:
                logger.info("[%s] comparing %r with %r", session.call_id, first, second)
                sides = []
                for name in (first, second):
                    answer = None
                    with contextlib.suppress(ToolCallError):
                        answer = await _tools.get_test_rate(name)
                    sides.append({"kind": KIND_TEST, **answer} if answer else {"kind": KIND_NOT_FOUND})
                _note(session, "answers_given")
                await _speak(
                    session, compare_options_reply(build_comparison(sides[0], sides[1]), first, second, lang), lang
                )
                return
            if first or second:
                await _speak(session, phrase("compare_need_two", lang), lang)
                return
            # Neither side resolved: not a comparison this system can make from
            # its own catalogue, so the turn carries on as it did before.

        # Ported story "Caller asks which doctors are available"
        # (agent/doctor_list.py). A plural, unnamed ask -- "which doctors do you
        # have", "কে কে ডাক্তার আছেন", "कौन कौन से डॉक्टर हैं". Served here
        # because it has a real lookup behind it and this repository had neither
        # the intent nor the endpoint: /api/v1/doctors and
        # /api/v1/doctors/by-department were added for it.
        #
        # The WHOLE utterance is handed to the by-department endpoint first. That
        # is deliberate and is why no department-name extractor was ported: the
        # endpoint matches against the real department table in both directions,
        # so "which doctors are in cardiology" resolves on the department name
        # inside the sentence, and "cardio" resolves on being inside the name.
        # Several matches come back `ambiguous` and a near match as
        # `did_you_mean`, both of which the reply template ASKS about -- never a
        # silent guess. A query that names no department at all resolves to
        # nothing, and the caller gets the full list instead, which is what they
        # asked for.
        if asks_for_the_doctor_list(text):
            logger.info("[%s] caller asked which doctors there are", session.call_id)
            by_department = None
            with contextlib.suppress(ToolCallError):
                by_department = await _tools.doctors_by_department(text)
            if by_department and (
                by_department.get("found") or by_department.get("ambiguous") or by_department.get("did_you_mean")
            ):
                _note(session, "answers_given")
                await _speak_department_doctors(session, by_department, lang)
                return
            everyone = None
            with contextlib.suppress(ToolCallError):
                everyone = await _tools.list_doctors()
            if everyone is None:
                _note(session, "system_failures")
                await _speak(session, phrase("tool_failure", lang), lang)
                return
            _note(session, "answers_given")
            await _speak_list(session, doctor_list_reply(everyone, lang), lang)
            return

        # Ported story "Caller states something the agent cannot verify"
        # (agent/unverifiable_claim.py). Runs AFTER every guard above it --
        # emergency, abuse, clinical interpretation, complaint,
        # doctor-personal-request, and the request for a person just above --
        # because each of those is a more specific reading of an utterance that
        # may also contain an assertion. It only ever sees text they have all
        # let through.
        #
        # Only TWO of the original's four claim categories are intercepted here.
        # "My appointment is tomorrow" and "I already paid" are both CHECKABLE in
        # this repository (GET /api/v1/bookings/lookup, GET
        # /api/v1/billing/outstanding), so they are deliberately left to fall
        # through to the real verified lookup -- which is this story's own second
        # acceptance criterion, "the agent checks the system of record where
        # verification is possible". Declining them would lose a capability this
        # system has.
        #
        # A claim about a RESULT is answered with the clinical reply: what a
        # result means is not something this system may state at all
        # (agent/clinical_safety.py), so "a doctor will explain it, shall I
        # connect one" is both the honest answer and the already-tested one.
        claim_category, _claim_phrase = detect_unverifiable_claim(text)
        if claim_category != "none":
            logger.info("[%s] unverifiable claim (%s)", session.call_id, claim_category)
            session.awaiting_clinician_offer = True
            if claim_category == CATEGORY_REPORT_RESULT:
                session.clinician_offer_reason = "clinical_interpretation"
                await _speak(session, phrase("clinical_interpretation", lang), lang)
            else:
                session.clinician_offer_reason = "unverifiable_claim"
                await _speak(session, phrase("cannot_confirm_claim", lang), lang)
            return


        # KCD-054: after verification, a different voice revokes it. The agent cannot know who
        # is speaking -- only that the voice differs from the one that was verified -- so it
        # says so plainly and stops disclosing personal details until verification is repeated.
        await _check_speaker_change(session, analysis, lang)

        # KCD-494/495/498/500: a question about the caller's OWN history, or an answer to a step of
        # one already under way, is handled here, deterministically, before any model call.
        if session.security is not None and not session.security.finished:
            if await _continue_security_flow(session, text, lang, analysis):
                return
        if session.awaiting_draft_answer and await _continue_draft_offer(session, text, lang):
            return
        if session.awaiting_pref_answer and await _continue_preference_offer(session, text, lang):
            return
        if session.awaiting_handoff_offer or session.history_state:
            if await _continue_history_flow(session, text, lang):
                return
        # stories: "Caller asks whether their report is ready", "... for their report to be sent". Every lab
        # report request goes to the counter (agent/report_request.py says why): checked before the history
        # detector, so "my previous reports" is sent there too rather than into record verification. A booking
        # in progress is kept and offered back (_with_resume), the same as any side question.
        if asks_about_report(text):
            logger.info("[%s] lab report request -> counter", session.call_id)
            _rec(session, "intent", "report_request")
            await _speak(session, _with_resume(session, phrase("report_counter_only", lang), lang), lang)
            return
        history_question = detect_history_question(text, lang)
        if history_question is not None:
            session.pending_history, session.history_attempts = history_question, 0
            await _history_step(session, lang)
            return

        # KCD-084/149/155: caller state -> delivery policy, and the
        # acknowledgement that comes before anything else when the policy
        # calls for one (once per state entry, not every turn).
        _update_caller_state(session, text, lang, analysis)
        ack = select_acknowledgement(session.policy, session.call_state.caller_state, lang)
        if ack and session.acknowledged_state != session.call_state.caller_state:
            session.acknowledged_state = session.call_state.caller_state
            session.turn_acknowledged = True
            await _speak(session, ack, lang)

        # KCD-438: an explicit "speak in Hindi/Bengali/English" request,
        # detected deterministically (no LLM call, same zero-extra-latency
        # reasoning as the yes/no check below). Acknowledged immediately in
        # the requested language. It sets the reply language for this turn
        # and seeds the next turn's prior (_lock_reply_language); in
        # REPLY_LANGUAGE_MODE=sticky it also locks it for the rest of the call.
        # Read in the language the request was SAID in, then in the call's (the two differ once the call is fixed
        # to its first language): "हिंदी में बोलिए" said on a Bengali call is a request, not a Hindi sentence.
        switch_target = detect_language_switch_request(text, detected_lang) or detect_language_switch_request(text, lang)
        if switch_target:
            if REPLY_LANGUAGE_MODE == "sticky" and session.lang_locked_before_turn and switch_target != session.lang:
                # The call is in the language of its first sentence and STAYS there: no request, in any language,
                # moves it mid-call (docs/adr/0003). Said so, in the call's own language, and the call goes on.
                logger.info("[%s] request for %s on a %s call -- the call stays", session.call_id, switch_target, session.lang)
                await _speak(session, phrase("language_stays", session.lang), session.lang)
                return
            _lock_reply_language(session, switch_target)
            session.lang_router.note_response_language(switch_target)
            await _speak(session, phrase("language_switched", switch_target), switch_target)
            return

        # Epic E26: a booking/reschedule/cancel/add-test confirmation
        # already in progress is a closed yes/no question -- answered
        # deterministically (agent/booking_flow.classify_yes_no), with NO
        # LLM round trip, both for the zero-extra-latency requirement and
        # because CLAUDE.md's truth boundary keeps exactly this kind of
        # high-stakes binary decision out of the model's hands.
        if session.pending_resend and await _continue_resend_offer(session, text, lang):
            return
        if session.pending_dated_enquiry and await _continue_dated_enquiry(session, text, lang):
            return
        if session.awaiting_name_confirm and await _continue_name_confirm(session, text, lang):
            return
        if session.awaiting_resume and await _continue_resume_offer(session, text, lang):
            return
        if session.test_list_offset is not None and await _continue_test_list(session, text, lang):
            return
        if session.test_list_offset is None and session.test_list_parked is not None and await _resume_parked_test_list(
            session, text, lang
        ):
            return
        if session.pending_tests and await _continue_test_confirm(session, text, lang):
            return
        if session.awaiting_tests and await _continue_test_choice(session, text, lang):
            return
        if session.booking is not None and session.booking.stage in ("confirming", "awaiting_charge_confirm"):
            # KCD-104: only a yes or a no is decided here. Anything else -- a question, a correction, another
            # task -- used to be answered by reading the confirmation out again; it is now a fresh turn, and the
            # pending confirmation stays pending (see _with_resume and the "unclear" branch below).
            session.no_at_confirm = False
            if await _handle_booking_confirmation_turn(session, text, lang) is not False:
                await _after_task_finished(session, lang)
                return

        # KCD-382b: the two questions the registered/booked-patient preparation flow asks are
        # themselves closed yes/no (or digits-only) questions, answered the same deterministic way
        # as the booking-confirmation and awaiting_callback_phone checks above -- no LLM round trip,
        # and a caller who answers something else entirely just falls through to the normal turn.
        if session.awaiting_test_prep_booking_phone:
            session.awaiting_test_prep_booking_phone = False
            dictated = _parse_spoken_phone(text)
            if dictated:
                session.identity.phone = session.identity.phone or dictated
                await _handle_booking_prep_lookup(session, dictated, lang)
                return
            # No number heard: never guess one -- the request lapses and this turn is handled normally.

        if session.awaiting_test_prep_registration_answer:
            session.awaiting_test_prep_registration_answer = False
            reg_answer = classify_yes_no(text, lang)
            if reg_answer == "yes":
                session.awaiting_test_prep_booking_phone = True
                await _speak(session, phrase("test_prep_ask_booking_phone", lang), lang)
                return
            if reg_answer == "no":
                session.pending_enquiry = ("test_prep", session.turn_count)
                await _speak(session, missing_slot_prompt("test_prep", "test_name", lang), lang)
                return
            # Neither yes nor no heard: the question lapses and this turn is handled normally.

        # ADDED BY SOURAV: KCD-387 full flow -- the home-collection flow's own copy of the KCD-382b
        # pattern just above (a bare yes/no, or a dictated phone number, answered deterministically,
        # no LLM round trip), plus its OWN yes/no/slot-choice gates (proceed?, use the stored
        # address?, is the address right?, final confirm?) -- see
        # _handle_home_collection_confirmation_turn's own docstring for why these are decided here
        # and not left to intent classification.
        if session.awaiting_home_collection_booking_phone:
            session.awaiting_home_collection_booking_phone = False
            dictated = _parse_spoken_phone(text)
            if dictated:
                session.identity.phone = session.identity.phone or dictated
                if not await _home_collection_lookup_by_phone(session, dictated, lang):
                    await _advance_home_collection(session, lang)
                return
            # No number heard: never guess one -- the request lapses and this turn is handled normally.

        if session.awaiting_home_collection_registration_answer:
            session.awaiting_home_collection_registration_answer = False
            reg_answer = classify_yes_no(text, lang)
            if reg_answer == "yes":
                session.awaiting_home_collection_booking_phone = True
                await _speak(session, phrase("test_prep_ask_booking_phone", lang), lang)
                return
            if reg_answer == "no":
                await _speak(session, missing_slot_prompt("home_collection", "test_name", lang), lang)
                return
            # Neither yes nor no heard: the question lapses and this turn is handled normally.

        if session.home_collection is not None and session.home_collection.stage in (
            hcflow.STAGE_AWAITING_PROCEED,
            hcflow.STAGE_OFFER_STORED_ADDRESS,
            hcflow.STAGE_CONFIRM_ADDRESS,
            hcflow.STAGE_FINAL_CONFIRM,
            hcflow.STAGE_CHOOSE_SLOT,
        ):
            if await _handle_home_collection_confirmation_turn(session, text, lang):
                return

        # ADDED BY SOURAV: story "Caller has reached the hospital by mistake" -- the unmistakable cases (a wrong number, a named person being
        # asked for) are decided here, deterministically, with no model call. It sits AFTER every hospital guard and every active-flow
        # gate above, and BEFORE FastPath / the semantic cache / the model, so a bare name never reaches doctor lookup. Skipped while any
        # hospital task is in progress; agent/unrelated_caller.classify() returns None (nothing decided) whenever the sentence carries any
        # hospital cue, so "sorry wrong number, actually I need my report" falls through to the normal flow untouched.
        if not session.unrelated_flow_active:
            unrelated_kind = unrelated_caller.classify(text, lang, _catalogue_entity_in)
            if unrelated_kind is not None:
                logger.info("[%s] unrelated caller cue: %s", session.call_id, unrelated_kind)
                _mark(session, "intent")
                _rec(session, "intent", "unrelated_caller")
                await _handle_unrelated_caller(session, lang, "rule")
                return
        elif unrelated_caller.classify(text, lang, _catalogue_entity_in) is not None:
            unrelated_caller_events.record("skipped_active_flow", "rule", lang)  # observed only; the active task is left untouched

        confirmed_entity = False
        pending, session.pending_entity = session.pending_entity, None
        if pending is not None:
            answer = classify_yes_no(text, lang)
            if answer == "yes":
                data, confirmed_entity = pending.data, True
            elif answer == "no":
                await _speak(session, entity_text.reask(lang), lang)
                return
            # anything else: the question lapses and this turn is handled as a fresh one

        if not confirmed_entity:
            # KCD-lay-terms: a rural lay term for a test category ("রক্ত পরীক্ষা", "পেটের ছবি", "মাথার ছবি"), matched
            # deterministically before any model call -- but never while a booking is already actively in progress
            # (session.booking is not None), so a lay-term-shaped word inside an unrelated answer never hijacks it.
            # story: "Caller asks which lab tests are available" -- decided before the lay-term table and the model, both
            # of which take "ল্যাব টেস্ট" for a test question (the model: `test_prep` with no test, which asked the caller
            # whether they were a registered patient). agent/test_list_request.py has the rule.
            # story: "Caller asks for a group of tests" -- "হার্টের কী কী টেস্ট", "সুগারের পরীক্ষা", "ভিটামিন টেস্ট":
            # the members of that group with their prices (agent/test_groups.py has the rule and what it leaves alone:
            # an exact alias, a code, a doctor / report / time / place sentence). Decided BEFORE the whole-catalogue
            # list below, which takes "কী কী টেস্ট" for "every test"; never while a booking is open, never for "do I
            # have it booked". A group with ONE member (urine, X-ray, Pap smear) is answered the same way: that test,
            # named and priced from the catalogue.
            group_hit = None
            if session.booking is None and _fast_path is not None and not asks_what_is_booked(text):
                # first: the caller picks from the group just read out and asks what they cost -- "ইসিজি আর এক্সরে,
                # সব মিলিয়ে কত": every price is already known to them, the new fact is the total
                if await _price_all_of_the_group(session, text, lang):
                    return
                picked = _group_pick(session, text, lang)
                if picked is not None and asks_for_price(text):
                    session.group_members = None
                    await _speak_test_prices(session, list(picked.names), lang, counter=asks_to_book(text))
                    return
                if picked is not None and asks_to_book(text):
                    # "ইকো করাব" right after the list: that test, read back as any test the caller books
                    session.group_members = None
                    await _confirm_tests(session, "book_test", picked, text, lang, {}, {})
                    return
                group_hit = match_test_group(text, _fast_path.catalogue.test_rows)
            if group_hit is not None:
                logger.info("[%s] test group %s asked for (%s), skipping the model", session.call_id, group_hit.key, group_hit.mode)
                await _handle_test_group(session, group_hit, lang)
                return
            if asks_for_test_list(text) and not _asks_for_the_blood_tests(session, text, lang):
                logger.info("[%s] test list asked for, skipping the model", session.call_id)
                await _speak_test_list(session, 0, lang)
                return
            lay_term = None if session.booking is not None else match_lay_term(text)
            if lay_term == BLOOD and (
                mentions_something_else(text) or len(_ground_tests_said(session, text, lang, []).names) >= 1
            ):
                # "blood test report kab milegi" is about the report, and "CBC blood test" names its test: the generic
                # blood-test list answers neither
                lay_term = None
            if lay_term is not None and len(_ground_tests_said(session, text, lang, []).names) >= 2:
                # Two or more tests NAMED in the sentence ("সুগার পিপি আর পেটের আলট্রাসাউন্ডের দাম"): the lay-term answer
                # prices the one it knows and says nothing of the others -- a whole-call matrix, 2026-10-12, found 64
                # such questions answered with the ultrasound's price alone. Several tests are the multi-test flow's.
                lay_term = None
            if lay_term is not None:
                await _handle_lay_term(session, lay_term, text, lang)
                return
            # story: "Caller asks what they have booked"
            #
            # A narrow deterministic net for the one distinction the model is most likely to get
            # the wrong way round: "আমার অ্যাপয়েন্টমেন্ট আছে?" (read it back) against "আমার
            # অ্যাপয়েন্টমেন্ট চাই" (book a new one). Heard wrongly, the caller who wanted a
            # reminder is given a SECOND appointment -- the reschedule story's own defect,
            # arriving by another route. agent/lookup_request.py has the rule and why it is
            # this narrow.
            #
            # Same two protections as the lay-term guard above: never while a booking is already
            # in progress (a possessive inside an answer must not hijack it), and a MISS simply
            # falls through to the model, so an uncovered phrasing costs nothing.
            if session.booking is None and asks_what_is_booked(text):
                logger.info("[%s] lookup_booking matched deterministically, skipping the model", session.call_id)
                data = {"intent": "lookup_booking", "slots": {}}
            elif session.booking is None and lang == "bn" and asks_about_parking_or_a_car(text):
                # "গাড়ি নিয়ে গেলে কোনো অসুবিধা হবে?", "আমি গাড়ি চালিয়ে আসব ভাবছি।", "গাড়িটা কোথায় রাখব বলুন তো?": the parking
                # answer, however indirectly asked (agent/parking_question.py). In front of the address rule below and of
                # the fast path, which took these to the address ("কোথায়"), home collection ("সুবিধা") or nothing.
                logger.info("[%s] a car / parking question -> the parking answer, skipping the model", session.call_id)
                data = {"intent": "clinic_faq", "slots": {"faq_topic": "parking"}}
            elif session.booking is None and lang == "bn" and asks_where_or_how_to_reach(text):
                # "আপনারা কোথায় বসেন?", "আপনাদের ওখানে যাওয়ার জন্য কোন স্টপে নামব?", "আপনাদের ওখানে আসার কথা ছিল।": the
                # address, however indirectly asked (agent/location_question.py has the rule and the measurement). Taken
                # BEFORE the fast path and the model because both were answering these wrongly -- a doctor's schedule,
                # the contact number, the parking answer, or "I did not understand" (2026-10-13, 35 of 90 on the pod).
                # Never while a booking is open; a miss goes where it always went.
                logger.info("[%s] a where / how-do-I-get-there question -> the address, skipping the model", session.call_id)
                data = {"intent": "clinic_faq", "slots": {"faq_topic": "location"}}
            elif _number_expected(session, text) and session.booking.action in ("cancel_appointment", "reschedule_appointment"):
                # The number a cancellation or a move is waiting for. Neither the keyword path nor the model gets it:
                # live 2026-10-07, "ফোন নম্বর জিরো ওয়ান ..." was answered with the clinic's helpdesk number (the FAQ
                # matched "ফোন নম্বর"), and a bare number was filed by the model as a wrong-number caller.
                logger.info("[%s] the number the open %s waits for -- skipping the model", session.call_id, session.booking.action)
                data = {"intent": session.booking.action, "slots": {}}
            else:
                # "Do you have a doctor in diabetology?" -- live 2026-10-09 the model filed it as lookup_booking and the
                # caller was asked which number they had booked from. Only after every deterministic branch above has
                # declined, and only while no booking is in progress; a MISS (the helper returns False) falls through
                # to the model exactly as before.
                if session.booking is None and asks_if_a_doctor_is_there(text) and await _answer_doctor_in_department(
                    session, text, lang
                ):
                    return
                try:
                    data = await _resolve_intent(session, text, lang)
                except ExtractionError as e:
                    logger.error("[%s] intent extraction failed: %s", session.call_id, e)
                    # A bare doctor's name the model cannot label at all (it answered "doctor_name" four times for
                    # "সঞ্জয় গুহ" and "রূপা মজুমদার" on the pod -- audit of all 32 doctors, 2026-10-10) is still an
                    # ANSWER when the booking is asking which doctor, or the doctors were read out a turn ago. The
                    # clinic's own reading of the sentence decides; the turn then goes through the same doctor rules
                    # as any unclassified one. Anything else keeps the apology exactly as before.
                    mention = None
                    if _expecting_a_doctor_name(session) and not confirmed_entity:
                        mention = await _doctors_mentioned(session, text)
                    if mention and mention.get("count"):
                        logger.info("[%s] no intent from the model, but the clinic reads the sentence as naming a doctor",
                                    session.call_id)
                        data = {"intent": "unclear", "slots": {}}
                    else:
                        await _speak(session, phrase("llm_failure", lang), lang, fallback_reason="llm_failure")
                        return

        intent = data["intent"]
        # A "relationship" the model filled with a word that is not one ("একজন", "রোগী") is not stored: it was being
        # saved onto the booking and then announced as a correction (agent/relationship_words.py).
        slots = clean_relationship(data["slots"])
        pending, session.pending_enquiry = session.pending_enquiry, None
        if intent == "unclear" and pending is not None and session.turn_count - pending[1] <= 1:
            intent = pending[0]  # "the same test", a bare name: the answer to the question we just asked
        # KCD-353: the disclosure is in the greeting (Bengali); a caller who answers in another language hears it
        # once, in theirs. Said on the first turn in that language the agent actually UNDERSTOOD: live 2026-10-08 a
        # two-word opener ("मेरे को") switched the call to Hindi, the whole disclosure was read out, and the very
        # next sentence was "माफ़ कीजिए। मैं समझ नहीं पाई" -- the notice delivered on the strength of a turn nobody
        # could read. Nothing is lost by waiting: `disclosed_langs` is only marked once it has been spoken, so an
        # unreadable turn simply leaves it owed until the caller says something in that language that lands.
        # (not on the turn that IS a request for a language: its answer is in the language asked for, and the notice
        # follows in that language on the next turn, rather than once in the language it was asked in)
        asked_for_a_language = bool(
            detect_language_switch_request(text, detected_lang) or detect_language_switch_request(text, lang)
        )
        if lang not in session.disclosed_langs and intent != "unclear" and not asked_for_a_language:
            session.disclosed_langs.add(lang)
            await _speak(session, disclosure_for(lang), lang)
        _mark(session, "intent")
        _rec(session, "intent", intent)
        if session.test_list_stash is not None and intent in ("test_rate", "test_prep"):
            # a question about one test taken in the middle of the list: the list is set aside, not lost
            session.test_list_parked = (session.test_list_stash[0], session.turn_count)
            session.test_list_stash = None
        elif intent != "unclear" and session.test_list_parked is not None and intent not in ("test_rate", "test_prep"):
            session.test_list_parked = None  # the caller went on to something else
        _note_stated_age(session, slots, lang)

        # ADDED BY SOURAV: story "Caller has reached the hospital by mistake" -- the model's own `unrelated_caller` answer. It is only a
        # PROPOSAL: it is acted on only when no hospital task is in progress AND the same hospital veto the deterministic path uses finds
        # nothing (no filled slot, no cue word, no catalogue test/FAQ/full doctor name). A bare greeting is sent back to `smalltalk`, a
        # turn with any hospital sign becomes `unclear` (ask again, never close), and during a task it is handled as ordinary `smalltalk`,
        # whose fixed reply and resume tail leave the task exactly as it was.
        # The model's "wrong number" answer is overruled when the sentence names one of THIS clinic's doctors
        # ("Mukherjee dekhabo"): a caller asking for our doctor reached the right place. First names alone never
        # count (clinic-api/doctor_mention.py), so "Amit se baat karwao" still reaches the check below.
        if (
            intent == "unrelated_caller"
            and not confirmed_entity
            and _doctor_answer_expected(session)
            and (_might_name_a_doctor(text) or _expecting_a_doctor_name(session))
        ):
            mention = await _doctors_mentioned(session, text)
            if mention and mention.get("count"):
                intent = _intent_for_a_named_doctor(session, text, mention)
                logger.info("[%s] unrelated_caller overruled: the sentence names our doctor", session.call_id)

        if intent == "unrelated_caller":
            if session.unrelated_flow_active:
                unrelated_caller_events.record("skipped_active_flow", "llm", lang)
                intent, data["direct_reply_bn"] = "smalltalk", None
            else:
                verdict = unrelated_caller.llm_verdict(text, lang, slots, _catalogue_entity_in)
                if verdict == unrelated_caller.VERDICT_UNRELATED:
                    await _handle_unrelated_caller(session, lang, "llm")
                    return
                unrelated_caller_events.record("vetoed", "llm", lang)
                intent = "smalltalk" if verdict == unrelated_caller.VERDICT_GREETING else "unclear"
                data["direct_reply_bn"] = None

        # The model could not say what the turn wants, but the clinic says it names one of its doctors ("Ronit
        # Mukherjee dekhabo", a bare "ডাক্তার দাস"): that is a doctor turn. Seeing the doctor -> booking; anything else
        # -> availability. The slot itself is settled from the clinic's own reading in either branch below.
        if (
            intent in ("unclear", "smalltalk")
            and not confirmed_entity
            and _doctor_answer_expected(session)
            and (_might_name_a_doctor(text) or _expecting_a_doctor_name(session))
        ):
            mention = await _doctors_mentioned(session, text)
            if mention and mention.get("count"):
                intent = _intent_for_a_named_doctor(session, text, mention)
                logger.info("[%s] unclassified turn names a doctor -> %s", session.call_id, intent)

        # A bare day or time said while a booking or a move is waiting for one ("সকাল দশটায়", "कल") is that flow's
        # answer, whatever the model called it. Live 2026-10-07: "সকাল দশটায়" mid-booking came back `smalltalk`, and the
        # caller heard a model-written sentence that meant nothing instead of the next booking question.
        open_task = session.booking
        if (
            intent in ("smalltalk", "unclear")
            and open_task is not None
            and open_task.action in ("book_appointment", "reschedule_appointment")
            and open_task.stage == "collecting"
            and (spoken_times(text) or spoken_dates(text) or spoken_phone(text) or _rest_of_number(session, text))
        ):
            logger.info("[%s] a bare day/time filed as %s -> the open %s's answer", session.call_id, intent, open_task.action)
            intent = open_task.action
        # A time GIVEN as an answer is not the clinic's hours. Live 2026-10-10: "ছটার সময়" ("at six"), said to
        # "কোন সময়ের জন্য?", carries "সময়" -- the bare keyword of the `hours` FAQ -- and the keyword path answered
        # the opening hours with no model call, three times in a row. Taken back for the open booking (or move) ONLY
        # when it is waiting for a day or a time, the turn carries a clock time, and nothing in it asks anything
        # (agent/booking_time_answer.py: no question word, open/close word, doctor word, "available" or "?"). So
        # "ক্লিনিক কটায় খোলে" and "ডাক্তার কখন বসেন" in the middle of a booking are still answered as questions.
        if (
            intent == "clinic_faq"
            and slots.get("faq_topic") == "hours"
            and open_task is not None
            and open_task.action in ("book_appointment", "reschedule_appointment")
            and open_task.stage == "collecting"
            and not open_task.is_stale()
            and _waiting_for(open_task) in ("date", "time_slot", "new_date", "new_time_slot")
            and is_a_time_answer(text)
        ):
            logger.info("[%s] a time given as an answer was filed as the hours FAQ -> the open %s's answer",
                        session.call_id, open_task.action)
            intent = open_task.action
            slots = {k: v for k, v in slots.items() if k != "faq_topic"}
            data["direct_reply_bn"] = None
        # The patient's phone number said inside the answer to "name and number?" is not a question about the clinic's
        # contact number, though it carries "ফোন নম্বর" (the `contact_number` FAQ's keyword). The fast path is already
        # kept off such a turn (_number_expected); this is the same turn when the MODEL filed it as that FAQ. Only
        # with a whole number actually in the sentence: "আপনাদের ফোন নম্বর কত" has none and is still the FAQ.
        if (
            intent == "clinic_faq"
            and slots.get("faq_topic") == "contact_number"
            and open_task is not None
            and open_task.action == "book_appointment"
            and _number_expected(session, text)
        ):
            logger.info("[%s] a number given as an answer was filed as the contact-number FAQ -> the open %s's answer",
                        session.call_id, open_task.action)
            intent = open_task.action
            slots = {k: v for k, v in slots.items() if k != "faq_topic"}
            data["direct_reply_bn"] = None
        # "I want to see Dr Sen" said outside a booking, with no day or time asked about, is a booking -- and so is the
        # doctor's name given as the answer to the department list just read out ("... কার কাছে অ্যাপয়েন্টমেন্ট নিতে
        # চান?"). Live 2026-10-10 both were filed `doctor_availability`: the caller who said "অরিন্দম সেনকে দেখাতে চাই"
        # was told when Dr Sen sits and asked "anything else?", and the booking never opened. Taken ONLY when the
        # clinic's own table names exactly ONE doctor in the sentence, no word asks WHEN he sits (_SITTING_WORD), and
        # either the caller said they want to see the doctor, or the doctor is one of those just read out. Anything
        # else -- an ambiguous surname, "কবে বসেন", a doctor never offered -- stays the availability answer it was.
        offered, session.offered_doctors = session.offered_doctors, None
        if (
            offered is not None
            and len(offered) > 2
            and session.booking is None
            and session.turn_count - offered[1] <= 1
            and _only_a_position(text)
        ):
            # "দ্বিতীয়জন", said to "কার কাছে অ্যাপয়েন্টমেন্ট নিতে চান?" when the list was read as an ANSWER (no booking
            # open): nothing remembered the list, so "the second one" was "I did not understand".
            logger.info("[%s] a position said after a department's doctors -> the booking, with that list", session.call_id)
            _remember_doctor_choices(_enter_task(session, "book_appointment"), offered[2], lang)
            intent = "book_appointment"
        if (
            intent == "doctor_availability"
            and session.booking is None
            and not confirmed_entity
            and not _SITTING_WORD.search(text)
            and names_a_doctor(slots.get("doctor_name"))
        ):
            wants = _wants_to_see_a_doctor(text) and wants_new_booking(text)
            answers_the_list = offered is not None and session.turn_count - offered[1] <= 1
            if wants or answers_the_list:
                mention = await _doctors_mentioned(session, text)
                named = (mention or {}).get("doctors") or []
                if mention and mention.get("found") and len(named) == 1 and (
                    wants or named[0].get("name") in offered[0]
                ):
                    logger.info(
                        "[%s] doctor_availability -> book_appointment: %s (%s)", session.call_id,
                        named[0].get("name"), "wants to see them" if wants else "answers the department list",
                    )
                    intent = "book_appointment"
        # A time said with "I'll see ..." and nothing else, outside any booking ("পৌনে এগারোটার সময় দেখাবো"), is the
        # start of a booking -- not the opening hours it shares the word "সময়" with. Live 2026-10-10: answered with the
        # clinic's hours. Same strict reading as the time answer above (a clock time, nothing that asks anything, no
        # doctor word) AND a want-to-see verb.
        if (
            intent == "clinic_faq"
            and slots.get("faq_topic") == "hours"
            and session.booking is None
            and is_a_time_answer(text)
            and _wants_to_see_a_doctor(text)
            and wants_new_booking(text)
        ):
            logger.info("[%s] a time with 'I will see' was filed as the hours FAQ -> a booking", session.call_id)
            intent = "book_appointment"
            slots = {k: v for k, v in slots.items() if k != "faq_topic"}
            data["direct_reply_bn"] = None
        # The same for the number a cancellation is waiting for. Live 2026-10-07: the model filed a bare "জিরো ওয়ান টু
        # ..." as unrelated_caller (smalltalk once a task is open), and the caller was greeted afresh instead of having
        # their booking found -- three times, until they hung up.
        if (
            intent in ("smalltalk", "unclear")
            and open_task is not None
            and open_task.action == "cancel_appointment"
            and open_task.stage == "collecting"
            and spoken_phone(text)
        ):
            logger.info("[%s] a bare number filed as %s -> the open cancellation's answer", session.call_id, intent)
            intent = "cancel_appointment"

        # story: "Caller asks a clinic FAQ" -- a question the model could not place that carries one of the clinic's own
        # FAQ keyword phrases is that FAQ. Live 2026-10-07: "আপনাদের ওখানে কখন খোলা থাকবে" and "বলছি কখন খোলা আপনাদের
        # ক্লিনিক" were filed `unclear` and the caller heard "I didn't understand" three times. The keyword path does not
        # take them before the model ("কখন" is a doctor-availability cue, and Bengali gives up there -- frozen by
        # tests/test_fast_path_languages.py), so they are taken here, by the same table at the same commit floor.
        if intent in ("unclear", "smalltalk") and not confirmed_entity:
            faq_topic = _faq_topic_said(text, lang)
            if faq_topic:
                logger.info("[%s] %s turn carries the %r FAQ's keywords -> clinic_faq", session.call_id, intent, faq_topic)
                intent, slots = "clinic_faq", {**slots, "faq_topic": faq_topic}
                data["direct_reply_bn"] = None

        pending_confirm = session.booking
        if intent == "smalltalk" and pending_confirm is not None and pending_confirm.stage in ("confirming", "awaiting_charge_confirm"):
            # A yes/no is pending: chit-chat is not its answer, and answering it with chit-chat buried the question
            # (live 2026-10-07: "আচ্ছা দিন" to "cancel it?" was answered "how are you?"). The question again.
            await _speak(
                session, booking_confirmation_readback(pending_confirm.readback_slots(), pending_confirm.action, lang), lang
            )
            return

        # A test list is open and the turn is one nobody could place -- "unclear", or the model's catch-all "smalltalk" for
        # a Hindi "और बोलिए" (answered "नमस्कार, मैं आपकी क्या मदद कर सकती हूँ?", live 2026-10-14): ask again and keep the
        # list. A thank-you is smalltalk and is answered as one.
        if (
            intent == "unclear"
            or (intent == "smalltalk" and not _caller_thanked(text, detected_lang))
        ) and session.test_list_stash is not None and session.booking is None:
            offset, strikes = session.test_list_stash
            session.test_list_stash = None
            if strikes <= TEST_LIST_PATIENCE:
                # an open list is not closed by a turn nobody could place: ask again and keep it
                session.test_list_offset, session.test_list_strikes = offset, strikes
                await _speak(session, test_list_reoffer(lang), lang)
                return

        if intent == "smalltalk":
            reply = data.get("direct_reply_bn")
            # The ONLY model-composed text a caller ever hears, so it passes the persona
            # (register, no hedging, no reassurance or advice, sentence length) or is replaced.
            # KCD-500: and it may not claim anything about THIS caller's past -- history is rendered
            # from retrieved fields by agent/history_templates.py, never composed.
            chosen = (
                reply
                if (
                    _speakable(reply or "", lang)
                    and persona_clean(reply or "", lang)
                    and not history_text.mentions_personal_history(reply or "", lang)
                )
                else phrase("smalltalk_default", lang)
            )
            await _speak(session, _with_resume(session, chosen, lang), lang)
            return

        if (
            intent == "unclear"
            and session.booking is not None
            and session.booking.stage in ("confirming", "awaiting_charge_confirm")
        ):
            if session.no_at_confirm:
                await _reopen_after_no(session, session.booking, lang)  # a no with nothing to change in it
            else:
                await _speak(
                    session, booking_confirmation_readback(session.booking.readback_slots(), session.booking.action, lang), lang
                )
            return

        if intent == "unclear":
            # CodeRabbit-flagged, real gap: the "confirming"/
            # "awaiting_charge_confirm" short-circuit above only covers
            # the yes/no step. During ordinary "collecting"-stage slot
            # filling, a bare answer with no sentence structure to key
            # off ("Ravi Das", a bare 10-digit number) is exactly the
            # kind of turn the LLM classifies "unclear" even when it DID
            # correctly extract the slot into `slots` -- and nothing
            # routed it back to the active booking, silently stalling
            # the guided one-slot-per-turn flow on the minimal answers
            # it is meant to collect. Re-attempt this turn as the active
            # action instead of bailing, as long as the state is not
            # stale (is_stale() -- previously never called anywhere,
            # per CodeRabbit -- guards against resurrecting a booking
            # the caller has plainly moved on from).
            if session.booking is not None and session.booking.stage == "collecting" and not session.booking.is_stale():
                intent = session.booking.action
            else:
                await _speak(session, phrase("unclear", lang), lang)
                return

        open_booking = session.booking
        if (
            open_booking is not None
            and open_booking.action == "book_appointment"
            and open_booking.stage == "collecting"
            and not open_booking.is_stale()
            and intent != "book_appointment"
            and _booking_answer_misfiled(intent, slots, open_booking, text, lang)
        ):
            logger.info("[%s] %s turn taken as the open booking's answer", session.call_id, intent)
            if intent in _TEST_INTENTS:
                # Whatever the model filed as a test here was not one (_booking_answer_misfiled checked it against the
                # clinic's catalogue) -- while the patient is being asked for, it is the patient's name.
                misfiled_name = slots.get("test_name")
                slots = {k: v for k, v in slots.items() if k not in ("test_name", "test_names")}
                if misfiled_name and not slots.get("patient_name") and _waiting_for(open_booking) == "patient_name":
                    slots["patient_name"] = misfiled_name
            if intent == "department_query" and slots.get("symptom_description") and not slots.get("doctor_name"):
                # The problem said in answer to "which doctor, or the problem?": looked up as a
                # department by _settle_doctor, exactly as a department named there would be.
                slots = {**slots, "doctor_name": slots["symptom_description"]}
            intent = "book_appointment"

        # The same rule for an open MOVE: "Saturday at eleven" said in answer to "which day?" names no doctor, test
        # or patient of its own, so whatever the model filed it as (a booking, an availability question), it is
        # the move's answer -- never a second booking started on top of it.
        open_move = session.booking
        if (
            open_move is not None
            and open_move.action == "reschedule_appointment"
            and open_move.stage == "collecting"
            and not open_move.is_stale()
            and intent in ("book_appointment", "doctor_availability", "book_test")
            and not any(slots.get(k) for k in ("doctor_name", "test_name", "test_names", "patient_name"))
        ):
            logger.info("[%s] %s turn taken as the open move's answer", session.call_id, intent)
            intent = "reschedule_appointment"

        # story: "Caller says tomorrow, day after, or next Monday" -- the day is worked out from a calendar
        # (agent/spoken_date.py), not left to the model's arithmetic. Before the confidence gate, so a read-back
        # that replays this turn on a yes replays the calendar's date too.
        slots = _pin_spoken_dates(session, intent, slots, text)
        slots = _pin_spoken_times(session, intent, slots, text)
        slots = _drop_a_time_filed_as_doctor(slots)
        slots = _pin_spoken_phone(session, intent, slots, text)
        data = {**data, "slots": slots}

        # KCD-442/KCD-447: a turn the two ASR decoders disagreed on is not
        # trusted to drive a factual lookup at all -- acting on a misheard
        # test/doctor name would produce a confident, wrong answer about
        # something else, which is worse than asking the caller to repeat
        # themselves. Checked before the tool call, not after: the point
        # is to never RUN the lookup on an unreliable entity, not merely
        # to hedge the reply once it comes back.
        #
        # One gate decides this (agent/action_gate.py), not two separate confidence checks -- it is proven
        # (tests/test_action_gate.py) to give exactly the same answer should_withhold_factual_answer() and
        # needs_entity_readback() gave here before; the entity itself is not yet resolved at this point in the
        # turn (the lookup has not run), so it is not passed -- exactly as those two functions never took it either.
        decoder_used = getattr(asr_result, "decoder_used", None)
        gate_verdict = (
            action_gate.decide(intent, decoder_used=decoder_used, decoder_agreement=asr_result.decoder_agreement)
            if not confirmed_entity
            else None
        )
        if (
            gate_verdict is not None
            and gate_verdict.action == action_gate.REPEAT
            and intent == "book_appointment"
            and (asr_result.decoder_agreement or 0.0) >= BOOKING_REJECT_FLOOR
        ):
            # A named doctor is the one thing in a booking turn that drives a lookup -- a misheard name
            # would have the agent read out a DIFFERENT real doctor's schedule. So it is read back, and the
            # turn replays exactly as heard on a yes (pending_entity, the same path enquiries use).
            doctor = (slots or {}).get("doctor_name")
            booking = session.booking
            # Already this booking's doctor -- or the doctor is settled and nobody said "doctor", so the "name" is
            # the patient's (_doctor_answer_expected): neither is a doctor to read back.
            known = booking is not None and (
                _same_doctor(booking, doctor) or (booking.doctor_verified and not _says_doctor(text))
            )
            if doctor and names_a_doctor(doctor) and not known:
                logger.info("[%s] booking turn at agreement %.2f -- reading the doctor back",
                            session.call_id, asr_result.decoder_agreement)
                session.pending_entity = entity_text.PendingEntity(intent, "doctor_name", doctor, data)
                if booking is not None:
                    # This question replaces any doctor offered before it. Live 2026-10-07: "তপন সেন" was offered as
                    # "Arindam Sen?", the caller said "তপন", was asked "তপন?", said yes -- and that yes was taken as yes
                    # to the stale "Arindam Sen?" offer (_pick_offered_doctor), booking Dr. Sen, not Dr. Tapan Bose.
                    booking.doctor_choices = []
                insufficient_information.record("low_decoder_agreement_readback", intent, lang)
                await _speak(session, entity_text.confirm_question("doctor_name", doctor, lang), lang)
                return
            # Nothing a misheard word could turn into a stated fact or a write: "a doctor" leads to "which
            # doctor?", a day and a time are read back in words before anything is booked, and a patient
            # name is read back by the booking branch itself ("Patient name is misheard"). Carry on.
            logger.info("[%s] booking turn at agreement %.2f -- nothing to read back, carrying on",
                        session.call_id, asr_result.decoder_agreement)
            gate_verdict = None
        if (
            gate_verdict is not None
            and gate_verdict.action == action_gate.REPEAT
            and intent in ("book_appointment", "reschedule_appointment")
            and not (slots or {}).get("doctor_name")
            and _answers_the_open_booking(session, text)
        ):
            logger.info(
                "[%s] recognisers disagreed, but the turn answers the open %s -- carrying on",
                session.call_id, intent,
            )
            gate_verdict = None
        if (
            TEST_GATE_READBACK
            and gate_verdict is not None
            and gate_verdict.action == action_gate.REPEAT
            and intent in ("test_rate", "book_test")
        ):
            probe = _ground_tests_said(session, text, lang, _named_tests(slots))
            if probe.names or probe.choices:
                logger.info(
                    "[%s] recognisers disagreed (agreement %.2f), but tests were grounded from what was said -- reading them "
                    "back instead of withholding",
                    session.call_id, asr_result.decoder_agreement,
                )
                gate_verdict = None
        if gate_verdict is not None and gate_verdict.action == action_gate.REPEAT:
            logger.info(
                "[%s] withholding %s answer: decoder_agreement=%.2f below floor",
                session.call_id,
                intent,
                asr_result.decoder_agreement,
            )
            insufficient_information.record("low_decoder_agreement", intent, lang)
            # A live call (2026-09-28): the model DID extract a name ("Roy") even though the two decoders
            # disagreed on the sentence as a whole -- the caller heard a blank "sorry, I didn't catch the name,
            # which doctor?" when what they actually needed was "do you mean Roy?", a yes/no that is faster to
            # answer and confirms EXACTLY the uncertain part instead of asking them to repeat everything. This is
            # the same read-back entity_text already does for UNAVAILABLE confidence (see the READ_BACK branch
            # below) -- extended here to LOW confidence too, but ONLY when the model actually named something;
            # nothing is invented, and the lookup still only runs after an explicit yes.
            entity = entity_text.entity_to_confirm(intent, slots)
            if entity is not None:
                slot, value = entity
                session.pending_entity = entity_text.PendingEntity(intent, slot, value, data)
                insufficient_information.record("low_decoder_agreement_readback", intent, lang)
                await _speak(session, entity_text.confirm_question(slot, value, lang), lang)
                return
            # Nothing at all was extracted: the caller said WHAT they want ("the price of ...") but not clearly
            # enough to guess a name from. Ask for the name only, instead of "say that again" for the whole sentence.
            name_slot = {"test_rate": "test_name", "test_prep": "test_name", "doctor_availability": "doctor_name"}.get(
                intent
            )
            if name_slot:
                session.pending_enquiry = (intent, session.turn_count)
                await _speak(
                    session, f"{phrase('name_not_caught', lang)} {missing_slot_prompt(intent, name_slot, lang)}", lang
                )
            else:
                await _speak(session, insufficient_information_reply(lang), lang)
            return
        # Only one decoder produced this text, so nothing vouches for it: read the entity back and
        # run the lookup only after a yes. Never let "no confidence figure" mean "trusted".
        if gate_verdict is not None and gate_verdict.action == action_gate.READ_BACK:
            entity = entity_text.entity_to_confirm(intent, slots)
            if entity is not None:
                slot, value = entity
                session.pending_entity = entity_text.PendingEntity(intent, slot, value, data)
                insufficient_information.record("single_decoder_readback", intent, lang)
                await _speak(session, entity_text.confirm_question(slot, value, lang), lang)
                return

        try:
            if intent == "test_rate":
                # Which tests the CALLER named -- the transcript decides, never the model (agent/entity_grounding).
                # Taken BEFORE the follow-up resolver, which fills an empty "test_name" from the last test talked
                # about: that test would be added to a total the caller never asked for.
                named = _named_tests(slots)
                grounded = _ground_tests_said(session, text, lang, named)
                if grounded.names or grounded.choices:
                    await _confirm_tests(session, "test_rate", grounded, text, lang, slots, data)
                    return
                if named:
                    # The model named tests this turn and the transcript supports none of them (live 2026-10-08).
                    if await _answer_faq_instead(session, text, lang):
                        return
                    await _ask_which_tests(session, "test_rate", _how_many_tests(grounded, slots), lang)
                    return
                # Nothing named this turn: a test carried over from an earlier turn ("ওটার দাম কত") is grounded by
                # the turn that DID name it, so the follow-up resolver is left exactly as it was.
                slots, _ = _followup(session, intent, slots, text, lang)
                reply = await _answer_enquiry_intent(intent, slots, lang)
                if reply is None:
                    session.pending_enquiry = (intent, session.turn_count)
                    await _speak(session, missing_slot_prompt(intent, "test_name", lang), lang)
                    return
                await _finish_enquiry_turn(session, text, lang, intent, slots, reply, data)

            elif intent == "doctor_availability":
                # The day the caller said is read back before anything is looked up (story "Caller says tomorrow, day
                # after, or next Monday"): resolve the reference locally first, then confirm the date, then ask the clinic.
                slots, _ = _followup(session, intent, slots, text, lang)
                if names_a_doctor(slots.get("doctor_name")) and await _confirm_spoken_date(
                    session, intent, slots, text, lang
                ):
                    return
                mention = None if confirmed_entity else await _doctors_mentioned(session, text)
                if mention and not _doctor_answer_expected(session) and not _titled(mention):
                    mention = None  # a surname with no "doctor" beside it, mid-task: most likely the patient's
                if mention and mention.get("found"):
                    slots = {**slots, "doctor_name": mention.get("doctor_name")}
                elif mention and mention.get("count"):
                    choices = _mention_choices(mention, lang)
                    if len(choices) == 1:
                        # One doctor, heard only approximately: confirmed before anything is read out about him.
                        replay = {**data, "intent": intent, "secondary_intent": None,
                                  "slots": {**slots, "doctor_name": choices[0][0]}}
                        session.pending_entity = entity_text.PendingEntity(intent, "doctor_name", choices[0][0], replay)
                    await _speak(session, _mention_question(mention, lang), lang)
                    return
                if not names_a_doctor(slots.get("doctor_name")):
                    slots = {**slots, "doctor_name": None}  # "ডাক্তার", "উনি", "বসেন": nobody, and nobody discussed
                    if _wants_to_see_a_doctor(text):
                        # "ডাক্তার দেখাবো" ("I'll see a doctor"): a booking with no doctor yet. Which doctor comes
                        # first -- never "no doctor called 'doctor'" (the reply the caller used to hear).
                        await _ask_which_doctor(session, _enter_task(session, "book_appointment"), lang, text)
                        return
                # The model put a DEPARTMENT in the doctor slot. Live 2026-10-10: "আমি জেনারেল মেডিসিনের একজন ডক্টরকে
                # দেখাব" came back doctor_availability with doctor_name "জেনারেল মেডিসিন", and the caller heard "there is
                # no doctor called জেনারেল মেডিসিন" and then "anything else?". The booking path already reads a
                # department out of the doctor slot (_settle_doctor); the availability path did not. Only when the
                # clinic's own reading of the sentence names NO doctor, and its department table finds that value
                # (a whole match, never a near one): then it is a department question -- a booking if the caller said
                # they will see someone, the department's doctors otherwise.
                if (
                    not (mention and mention.get("count"))
                    and not confirmed_entity
                    and names_a_doctor(slots.get("doctor_name"))
                ):
                    dept = await _department_named_in_slot(slots.get("doctor_name"))
                    if dept is not None:
                        logger.info("[%s] the doctor slot %r is a department (%s)", session.call_id,
                                    slots.get("doctor_name"), dept.get("department"))
                        if _wants_to_see_a_doctor(text) and wants_new_booking(text):
                            await _ask_which_doctor(session, _enter_task(session, "book_appointment"), lang, text)
                        else:
                            await _speak_department_doctors(session, dept, lang)
                        return
                reply = await _answer_enquiry_intent(intent, slots, lang)
                if reply is None:
                    session.pending_enquiry = (intent, session.turn_count)
                    await _speak(session, missing_slot_prompt(intent, "doctor_name", lang), lang)
                    return
                await _finish_enquiry_turn(session, text, lang, intent, slots, reply, data)

            elif intent == "test_prep":
                slots, _ = _followup(session, intent, slots, text, lang)
                # KCD-382: more than one test named in the same turn goes through the merge path entirely
                # BEFORE _answer_enquiry_intent / _finish_enquiry_turn -- see _handle_multi_test_prep's own
                # docstring for why (a hand-off closes the socket; nothing may speak after it).
                multi_names = [n for n in (slots.get("test_names") or []) if n and n.strip()]
                if len(multi_names) > 1:
                    await _handle_multi_test_prep(session, multi_names, lang)
                    return
                reply = await _answer_enquiry_intent(intent, slots, lang)
                if reply is None:
                    # KCD-382b: nothing named at all. Before falling back to the existing "which
                    # test" ask, try the same-call follow-up recall (a test set already resolved
                    # this call, named directly or via a booking lookup), then -- once, per call --
                    # ask whether the caller is registered/has a booking, so a DB-backed caller is
                    # never made to repeat test names the booking record already has.
                    recovered = _recover_followup_prep_tests(session)
                    if recovered:
                        if len(recovered) > 1:
                            await _handle_multi_test_prep(session, list(recovered), lang)
                        else:
                            await _speak_single_test_prep(session, recovered[0], lang, text, data)
                        return
                    if not session.test_prep_registration_asked:
                        session.test_prep_registration_asked = True
                        session.awaiting_test_prep_registration_answer = True
                        await _speak(session, phrase("test_prep_ask_registered", lang), lang)
                        return
                    session.pending_enquiry = (intent, session.turn_count)
                    await _speak(session, missing_slot_prompt(intent, "test_name", lang), lang)
                    return
                await _finish_enquiry_turn(session, text, lang, intent, slots, reply, data)
                resolved_name = slots.get("test_name") or next(iter(slots.get("test_names") or []), None)
                if resolved_name:
                    session.last_resolved_prep_tests = (resolved_name,)
                    session.last_resolved_prep_turn = session.turn_count

            elif intent == "clinic_faq":
                slots, _ = _followup(session, intent, slots, text, lang)
                reply = await _answer_enquiry_intent(intent, slots, lang)
                if reply is None:
                    session.pending_enquiry = (intent, session.turn_count)
                    await _speak(session, missing_slot_prompt(intent, "faq_topic", lang), lang)
                    return
                await _finish_enquiry_turn(session, text, lang, intent, slots, reply, data)

            # story: "Caller gives everything in one sentence" -- every slot this turn
            # carried is merged into what earlier turns gave (agent/booking_flow.merge_slots),
            # so a complete opening sentence falls straight through to the readback.
            elif intent == "book_appointment":
                st = _enter_task(session, "book_appointment")
                _absorb_phone_digits(st, slots)
                # The caller's choice from doctors just offered ("প্রথমজন", "সেন" for Sen-not-Sengupta)
                # is taken before anything else reads the doctor slot.
                picked = _pick_offered_doctor(st, text, slots.get("doctor_name"), lang)
                if picked:
                    slots = {**slots, "doctor_name": picked}
                    st.doctor_choices = []
                # Which doctor(s) the SENTENCE names, by the clinic's own table, before the model's copy is trusted
                # (see _doctors_mentioned). Several, or one heard approximately, is asked about below -- after this
                # turn's other answers (a day, a time) are kept, so the caller never has to repeat them.
                mention_to_ask = None
                if not picked and not confirmed_entity and (not st.doctor_verified or _says_doctor(text)):
                    mention = await _doctors_mentioned(session, text)
                    if st.doctor_verified and not _titled(mention):
                        mention = None  # most likely the PATIENT's surname (_doctor_answer_expected)
                    if mention and mention.get("found"):
                        named = mention.get("doctor_name")
                        same = st.doctor_verified and st.slots.get("doctor_name") == named
                        slots = {**slots, "doctor_name": None if same else named}
                    elif mention and mention.get("count") and not st.doctor_verified:
                        slots = {**slots, "doctor_name": None}
                        mention_to_ask = mention
                # "a doctor", "ডাক্তার", "कोई डॉक्टर": the caller asked for a doctor and named none. That is
                # never stored as a name -- not as a new one, and not over a doctor already chosen.
                asked_for_any_doctor = bool(slots.get("doctor_name")) and not names_a_doctor(slots["doctor_name"])
                if asked_for_any_doctor or _same_doctor(st, slots.get("doctor_name")):
                    slots = {**slots, "doctor_name": None}
                # A settled doctor is changed only by a sentence that says "doctor" ("না, ডাক্তার দাস"). The model also
                # files the PATIENT's name under doctor_name when it shares a doctor's surname -- "প্রিয়া সেন" on Dr.
                # Ghosh's booking would otherwise have moved it to Dr. Sen (_doctor_answer_expected says why).
                if st.doctor_verified and slots.get("doctor_name") and not picked and not _says_doctor(text):
                    logger.info("[%s] doctor slot %r ignored: the doctor is settled and nobody said 'doctor'",
                                session.call_id, slots["doctor_name"])
                    slots = {**slots, "doctor_name": None}
                # An answer to a slot offer ("প্রথমটা", "10:15", "হ্যাঁ") fills the day and time
                # before the model's own slots are merged, so a bare "yes" is not lost.
                # So does an answer whose day is only the model's guess -- no day word the calendar recognises was said.
                # "ঐদিনেই করুন" came back dated TODAY by the model, so the Thursday just offered was never taken.
                if not picked and (not slots.get("date") or not spoken_dates(text)) and _take_offered_slot(st, text):
                    slots = {k: v for k, v in slots.items() if k != "date"}
                prior_slots = dict(st.slots)
                changed = merge_slots(st, slots)
                if st.hold_token and {"doctor_name", "date", "time_slot"} & set(changed):
                    # KCD-104: the caller changed WHICH slot they want; the hold is on the old one. Drop it so
                    # the new doctor/date/time is held below (the old hold expires on its own).
                    st.hold_token, st.hold_doctor_id = None, None
                ack = correction_acknowledgement(changed, prior_slots, st.slots, lang)
                if ack:
                    await _speak(session, ack, lang)
                else:
                    await _thank_for_answer(session, st, changed, prior_slots, lang)
                    await _echo_new_slots(session, changed, prior_slots, st.slots, lang)

                if mention_to_ask is not None:
                    st.doctor_choices = _mention_choices(mention_to_ask, lang)
                    st.asked_fields.add("doctor_name")
                    await _speak(session, _mention_question(mention_to_ask, lang), lang)
                    return

                # The doctor first: nothing after it is asked until the clinic has confirmed who it is.
                if await _settle_doctor(session, st, lang, text):
                    return

                # Secure the hold as soon as doctor+date+time are known, even
                # if patient details are still missing -- KCD-376: the
                # concurrency guard must run before the caller has spoken a
                # patient name, not after.
                if (
                    st.hold_token is None
                    and st.slots.get("doctor_name")
                    and st.slots.get("date")
                    and st.slots.get("time_slot")
                ):
                    hold = await _tools.hold_slot(st.slots["doctor_name"], st.slots["date"], st.slots["time_slot"])
                    if not hold.get("success"):
                        if hold.get("reason") in ("doctor_not_available_that_day", "sitting_over"):
                            # "sitting_over": the day was today and its chamber is done -- "Wednesday" said on
                            # Wednesday evening. Offered the next sitting the same way, never re-asked for a time.
                            st.slots.pop("date", None)
                            if await _offer_next_sitting(session, st, lang, hold.get("reason") == "sitting_over"):
                                return
                        await _speak(session, booking_reply(st.slots, hold, lang), lang)
                        st.slots.pop(_field_to_reclear_on_hold_failure(hold.get("reason")), None)
                        return
                    st.hold_token, st.hold_doctor_id = hold["hold_token"], hold["doctor_id"]

                # KCD-368: a caller may spell a name unprompted, or after
                # being offered it below -- either way, letters heard this
                # turn are assembled and used before anything else is
                # asked, so an eager caller is never told to repeat
                # themselves. Confirmed by readback like every other slot.
                if not st.slots.get("patient_name") and slots.get("spelled_letters"):
                    spelled = merge_spelling(st, slots["spelled_letters"])
                    if spelled:
                        st.slots["patient_name"] = spelled.capitalize()
                        st.name_confirmed = True  # spelled out letter by letter: already certain
                        await _speak(session, spelling_readback(spelled, lang), lang)
                        return

                # "Patient name is misheard": only one decoder produced this turn, so nothing
                # vouches for the name it carried. Read it back NOW rather than at the final
                # readback -- a wrong name caught here costs one yes/no; caught at the end it
                # costs the caller every question they answered under it.
                if "patient_name" in changed and not st.name_confirmed and confidence != VERIFIED:
                    session.awaiting_name_confirm = True
                    await _speak(
                        session,
                        entity_text.confirm_question("patient_name", st.slots["patient_name"], lang),
                        lang,
                    )
                    return

                # Doctor known, day not: offer when the doctor next sits (or the soonest slot,
                # if that is what was asked for) rather than asking the caller to guess a date.
                # At the OPENING of the booking, when the doctor was only settled THIS turn (picked
                # from the doctors offered, or named after "which doctor?"), or when the caller asks
                # for the soonest slot. The middle case is the same moment as the opening one -- the
                # doctor has just become known -- and the offer is also what tells the caller WHICH
                # doctor "প্রথমজন" turned out to be; without it they were asked for a day and a time
                # with the doctor never named. Re-offering a day once the doctor was settled earlier
                # is still not done: that would talk over a caller already giving details.
                doctor_settled_now = st.doctor_verified and "doctor_name" in changed
                if (
                    st.slots.get("doctor_name")
                    and not st.slots.get("date")
                    and not st.offered_slots
                    and (not st.asked_fields or doctor_settled_now or earliest_request.wants_earliest(text, lang))
                    and await _offer_days_or_slots(session, st, text, lang)
                ):
                    return

                if doctor_settled_now:
                    # Chosen this turn but not offered (the day was already known), so nothing above
                    # said who: "প্রথমজন" is a position in a list, not a doctor. Say the name now.
                    await _speak(session, doctor_chosen_ack(st.readback_slots(), lang), lang)

                missing = missing_required(st)
                if missing:
                    field_name = missing[0]
                    if field_name == "phone" and st.note_retry("phone") >= 2:
                        # KCD-370: a caller who won't give a number still
                        # gets the booking, told plainly what that costs
                        # them, instead of being blocked here forever.
                        st.phone_declined = True
                    elif field_name == "patient_name" and st.note_retry("patient_name") >= 2:
                        # KCD-368: offered proactively after one failed
                        # capture, not only when the caller asks for it.
                        await _speak(session, spelling_prompt(lang), lang)
                        return
                    else:
                        await _speak(session, _next_question(session, st, intent, missing, lang), lang)
                        return
                    missing = missing_required(st)
                    if missing:
                        await _speak(session, _next_question(session, st, intent, missing, lang), lang)
                        return

                # Skipped when no real phone exists to check by -- the
                # "not_provided" sentinel must never be used as a lookup
                # key, or two different callers who both declined to give
                # a number would appear to conflict with EACH OTHER.
                if effective_phone(st) != "not_provided" and not st.slots.get("_conflict_checked"):
                    conflict = await _tools.booking_conflict(
                        effective_phone(st), st.slots["date"], st.slots["time_slot"]
                    )
                    st.slots["_conflict_checked"] = "1"
                    if conflict.get("conflict"):
                        await _speak(session, conflict_reply(conflict["existing"], lang), lang)
                        return

                if is_ready_to_confirm(st):
                    mark_confirming(st)
                    if st.phone_declined:
                        await _speak(session, phrase("no_confirmation_number", lang), lang)
                    await _speak(session, booking_confirmation_readback(st.readback_slots(), "book_appointment", lang), lang)

            elif intent in ("book_test", "add_test_booking"):
                # A lab test is paid and booked at the counter, never over the call (clinic-api/booking_service.
                # send_payment_link's docstring says the same from the other side: a lab test's confirmation_id is
                # never found there). No booking task is entered -- there is nothing to collect slots for. Adding a
                # test to an existing booking is still a lab test booking. What the caller CAN have now is the price:
                # each test named, and their total, before being sent to the counter.
                await _answer_test_booking(session, slots, text, lang, data)

            elif intent == "reschedule_appointment":
                st = _enter_task(session, "reschedule_appointment")
                slots = _as_new_day_and_time(slots)
                heard_number = bool(slots.get("phone"))
                _absorb_phone_digits(st, slots)  # a number in pieces is kept, never looked up (or echoed) half-heard
                # "yes" / "that day" after the doctor's next sitting was offered
                if not slots.get("new_date") and st.offered_slots and _take_offered_slot(st, text):
                    st.slots["new_date"] = st.slots.pop("date")
                    if st.slots.get("time_slot"):
                        st.slots["new_time_slot"] = st.slots.pop("time_slot")
                prior_slots = dict(st.slots)
                changed = merge_slots(st, slots)
                ack = correction_acknowledgement(changed, prior_slots, st.slots, lang)
                if ack:
                    await _speak(session, ack, lang)
                else:
                    await _thank_for_answer(session, st, changed, prior_slots, lang)
                    await _echo_new_slots(session, changed, prior_slots, st.slots, lang)
                if await _find_booking_by_number(session, st, slots, heard_number, lang):
                    return
                await _use_fetched_booking(session, st)
                missing = missing_required(st)
                if "confirmation_id" in missing:
                    await _speak(session, missing_slot_prompt(intent, "confirmation_id", lang), lang)
                    return
                if await _check_new_day(session, st, lang):
                    return
                if missing:
                    await _speak(session, missing_slot_prompt(intent, missing[0], lang), lang)
                    return
                if is_ready_to_confirm(st):
                    mark_confirming(st)
                    await _speak(session, booking_confirmation_readback(st.readback_slots(), "reschedule_appointment", lang), lang)

            elif intent == "cancel_appointment":
                st = _enter_task(session, "cancel_appointment")
                heard_number = bool(slots.get("phone"))
                _absorb_phone_digits(st, slots)  # a number in pieces is kept, never looked up (or echoed) half-heard
                prior_slots = dict(st.slots)
                changed = merge_slots(st, slots)
                ack = correction_acknowledgement(changed, prior_slots, st.slots, lang)
                if ack:
                    await _speak(session, ack, lang)
                else:
                    await _thank_for_answer(session, st, changed, prior_slots, lang)
                    await _echo_new_slots(session, changed, prior_slots, st.slots, lang)
                if await _find_booking_by_number(session, st, slots, heard_number, lang):
                    return
                await _use_fetched_booking(session, st)
                missing = missing_required(st)
                if missing:
                    await _speak(session, missing_slot_prompt(intent, missing[0], lang), lang)
                    return
                if is_ready_to_confirm(st):
                    mark_confirming(st)
                    await _speak(session, booking_confirmation_readback(st.readback_slots(), "cancel_appointment", lang), lang)
                else:
                    # Already awaiting its yes (asked again, in other words): the question again, never silence.
                    await _speak(session, booking_confirmation_readback(st.readback_slots(), "cancel_appointment", lang), lang)

            elif intent == "lookup_booking":
                # story: "Caller asks what they have booked" -- "AFTER VERIFICATION the agent
                # states ...". A spoken phone number is a CLAIM, not proof of owning it
                # (agent/identity.py's own docstring makes that distinction, and this stack has
                # no caller ID), so the same gate the history flow uses decides whether anything
                # personal may be said. Nothing is looked up before it passes.
                await _answer_booking_lookup(session, slots, lang)

            elif intent == "resend_confirmation":
                confirmation_id = slots.get("confirmation_id")
                if not confirmation_id and slots.get("phone"):
                    found = await _tools.lookup_bookings(phone=slots["phone"])
                    bookings = found.get("bookings") or []
                    if len(bookings) > 1:
                        await _speak(session, multiple_bookings_reply(bookings, lang), lang)
                        return
                    if bookings:
                        confirmation_id = bookings[0]["confirmation_id"]
                if not confirmation_id:
                    await _speak(session, missing_slot_prompt("cancel_appointment", "confirmation_id", lang), lang)
                    return
                result = await _tools.resend_confirmation(confirmation_id)
                await _speak(session, _with_resume(session, resend_reply(result, lang), lang), lang)

            elif intent == "department_query":
                if (
                    slots.get("symptom_description")
                    and _wants_to_see_a_doctor(text)
                    and wants_new_booking(text)
                    and await _department_named_in(text) is not None
                ):
                    # The same sentence as the no-symptom case below, with the department put in the symptom slot
                    # instead of the doctor slot. It was answered with the list and NO booking, so the doctor the
                    # caller then picked ("the second one", a bare name) had nothing to attach to.
                    await _ask_which_doctor(session, _enter_task(session, "book_appointment"), lang, text)
                    return
                reply = await _answer_enquiry_intent(intent, slots, lang)
                if reply is not None and slots.get("symptom_description"):
                    named = await _department_named_in(slots["symptom_description"]) or await _department_named_in(text)
                    if named is not None:
                        _remember_offered_doctors(session, named)  # the list was read; its next answer is a doctor
                if reply is None:
                    # The only way _answer_enquiry_intent returns None for this
                    # intent is a missing symptom_description, so ask for the
                    # symptom rather than speaking the generic "unclear" --
                    # which told a caller who had just said "a doctor in a
                    # department" that they had not been understood, and left
                    # them with no way forward (live call of 2026-10-06). Same
                    # one-question-per-missing-slot shape every other intent in
                    # this dispatch already uses.
                    if not slots.get("symptom_description"):
                        # No symptom: the caller asked WHICH departments (or doctors) there are -- live call
                        # 2026-10-07, "কোন কোন ডিপার্টমেন্ট আছে আপনাদের ওখানে". A department named in the sentence
                        # gets its doctors; otherwise the departments themselves, with the symptom route still open
                        # (reply_templates.department_list_prompt). Both are the clinic's own data.
                        if _wants_to_see_a_doctor(text) and wants_new_booking(text):
                            # "আমি জেনারেল মেডিসিনের একজনকে দেখাব" ("I'll see someone in General Medicine"): a booking, with
                            # the department already said. Live 2026-10-10 the department's doctors were read out and
                            # the booking never opened, so the doctor the caller then named ("অরিন্দম সেনকে দেখাতে চাই")
                            # was answered as a schedule enquiry and the call drifted to "anything else?". Here the
                            # booking opens FIRST; _ask_which_doctor reads the sentence for the department itself and
                            # lists its doctors with the choices remembered, exactly as it does for a bare "a doctor".
                            await _ask_which_doctor(session, _enter_task(session, "book_appointment"), lang, text)
                            return
                        dept = await _department_doctors(text, lang)
                        if dept is not None:
                            await _speak_department_doctors(session, dept, lang)
                            return
                        if _wants_to_see_a_doctor(text):
                            # "আমি একজন ডাক্তার দেখাব" ("I'll see a doctor") filed as a department question (live
                            # 2026-10-07): a booking with no doctor yet, so WHICH doctor -- the departments come with
                            # the question, for a caller who knows no name.
                            await _ask_which_doctor(session, _enter_task(session, "book_appointment"), lang)
                            return
                        await _speak_list(session, department_list_prompt(await _spoken_departments(lang), lang), lang)
                        return
                    await _speak(session, phrase("unclear", lang), lang)
                    return
                await _finish_enquiry_turn(session, text, lang, intent, slots, reply, data)

            elif intent == "home_collection":
                # ADDED BY SOURAV: KCD-387 full flow. Every new fact this turn gave is folded into
                # the running state first (a later value always overwrites an earlier one -- same
                # rule BookingState.merge_slots uses, so a caller correcting themselves mid-flow just
                # works); THEN the flow advances as far as that lets it. Reached only on a FRESH
                # turn classified as this intent -- the yes/no/slot-choice gates (proceed?, use the
                # stored address?, is the address right?, final confirm?) are decided earlier, before
                # intent classification even runs (see the early interception above).
                st = _enter_home_collection(session)
                hcflow.merge_test_names(st, ([slots["test_name"]] if slots.get("test_name") else []) + list(slots.get("test_names") or []))
                changed_pincode = hcflow.merge_postal_code(st, slots.get("postal_code"))
                if slots.get("date") and not st.date:
                    st.date = slots["date"]
                if slots.get("patient_name") and not st.patient_name:
                    st.patient_name = slots["patient_name"]
                if (slots.get("phone") or slots.get("contact_phone")) and not st.phone:
                    st.phone = slots.get("phone") or slots.get("contact_phone")
                if slots.get("address_line"):
                    st.address_line = slots["address_line"]
                if slots.get("landmark"):
                    st.landmark = slots["landmark"]
                st.touch()
                if changed_pincode:
                    # Section 22's own "never guess, re-validate" spirit applied to a correction:
                    # everything priced/held/offered against the OLD pincode is stale and must be
                    # recomputed against the new one, and any real capacity already held is freed
                    # immediately rather than left to its own TTL (section 14/30).
                    st.eligibility, st.eligible_tests, st.not_found_tests = [], [], []
                    st.slots_offered, st.chosen_slot, st.quote, st.payment_policy_text = [], None, None, None
                    await _release_home_collection_hold_if_any(session)
                if st.quote is not None:
                    await _continue_home_collection_after_proceed(session, lang)
                else:
                    await _advance_home_collection(session, lang)

        except ToolCallError as e:
            logger.error("[%s] clinic API call failed: %s", session.call_id, e)
            await _speak(session, phrase("tool_failure", lang), lang, fallback_reason="tool_failure")



# ---------------------------------------------------------------- the doctor, settled first
# A booking's doctor is resolved with the clinic BEFORE anything after it is asked. Modelled on the
# sibling tree's _open_booking (the tree the sprint-1 booking stories were first delivered in), built
# here on this tree's own data, tools and reply sentences.
#
# Why it has to be first, from the live call of 2026-10-06: the caller said "আমি একজন ডক্টর দেখাব"
# ("I'll see a doctor"), the model put "একজন ডক্টর" ("a doctor") in doctor_name, nothing checked it,
# and the caller was asked for a day and a time. Only when the slot was finally held did the clinic
# answer that nobody by that name worked here -- "দুঃখিত, 'একজন ডক্টর' নামে কোনো ডাক্তার খুঁজে পেলাম
# না" -- three questions after the one that actually needed asking: which doctor.
#
# The order of the checks:
#   1. a "name" that names nobody ("a doctor", agent/doctor_request.py) is never stored: ask which
#      doctor, offering the clinic's departments so a caller who knows no name has a way forward;
#   2. clinic-api finds exactly one doctor            -> keep its canonical name, carry on;
#   3. it fits several ("সেন" -> Sen or Sengupta)     -> ask which, remember the offer;
#   4. it is a department or a problem said where a name goes ("হার্টের ডাক্তার", "বুকে ব্যথা")
#                                                     -> that department's doctors, ask which;
#   5. nobody                                         -> say so and ask again.
# Asked more than DOCTOR_ASK_LIMIT times without an answer it can use, the call goes to a person
# rather than round the same question again.
DOCTOR_ASK_LIMIT = 3

# Below this the two decoders disagreed about nearly everything: acting on either reading is a guess,
# so the caller is asked again. Between it and agent/confidence_gate.LOW_CONFIDENCE_FLOOR a BOOKING turn
# is not thrown away -- the sibling tree's three-zone rule (its agent/confidence.py): read back the part
# that could be misheard into a wrong fact, else carry on, because nothing is written until the caller
# says yes to the full read-back. The live booking of 2026-10-06 scored 0.33 and the caller was simply
# asked to say everything again; on phone audio that happens on turn after turn. REASONED, not measured:
# the sibling tree's own placeholder, not calibrated on this tree's calls -- recalibrate on real audio.
BOOKING_REJECT_FLOOR = 0.30

# story: a doctor named ANYWHERE in the sentence -- with or without the word "doctor", in any script, one or several.
#
# The intent model copies "the doctor term as the caller said it" into the doctor slot, and on the live call of
# 2026-10-07 it copied "বসেন" ("sits") and "উনি" ("he"), which the caller then heard read back as names nobody has. The
# clinic now reads the WHOLE sentence against its own doctor table (clinic-api/doctor_mention.py) and says which
# doctors it names. Order of trust, the deterministic reading first and the model only when it finds nobody:
#   1. the clinic names exactly one doctor, exactly        -> that doctor (its canonical name);
#   2. it names several, or one only approximately         -> ASK which / "do you mean ...?", never pick;
#   3. it names nobody                                     -> the model's slot, if it names somebody
#                                                             (agent/doctor_request.names_a_doctor), else the doctor
#                                                             being discussed (agent/enquiry_followup), else ask.
# Words that say the caller wants to SEE a doctor rather than ask about one. Whole words or stems, never substrings of
# the sentence: "বুক" is also "chest" ("বুকে ব্যথা"), so it only counts as a whole word.
_SEE_STEMS = ("দেখা", "অ্যাপয়েন্ট", "এপয়েন্ট", "অ্যাপোয়েন্ট", "অযাপ", "dekha", "appoint", "दिखा", "अपॉइंट", "अपाइंट")
_SEE_WORDS = frozenset({"বুক", "বুকিং", "book", "booking", "consult", "बुक", "बुकिंग"})


def _wants_to_see_a_doctor(text: str) -> bool:
    words = [w.strip(".,?!।") for w in unicodedata.normalize("NFC", text or "").lower().split()]
    return any(w in _SEE_WORDS or w.startswith(_SEE_STEMS) for w in words if w)


async def _doctors_mentioned(session: CallSession, text: str) -> dict | None:
    """clinic-api's answer to "which of our doctors does this sentence name?", asked once per utterance. None when it
    cannot be asked -- the turn then carries on exactly as before, on the model's slot."""
    cached = session.mention_cache
    if cached is not None and cached[0] == text:
        return cached[1]
    if _tools is None:
        return None
    try:
        result = await _tools.doctors_mentioned(text)
    except ToolCallError as e:
        logger.warning("[%s] doctor-mention lookup unavailable (%s)", session.call_id, e)
        result = _mention_from_catalogue(session, text)
    session.mention_cache = (text, result)
    if result and result.get("count"):
        logger.info(
            "[%s] sentence names %s doctor(s): %s%s",
            session.call_id,
            result["count"],
            ", ".join(d.get("name", "?") for d in result.get("doctors") or []),
            "" if result.get("found") else " -- asking",
        )
    return result


def _mention_from_catalogue(session: CallSession, text: str) -> dict | None:
    """Which of our doctors this sentence names, decided LOCALLY, when clinic-api's own mention lookup could not
    be reached.

    Live 2026-10-08: "कार्डियोलॉजी डिपार्टमेंट में डॉक्टर् बेनर्जी अवैलेबल है" -- that one lookup failed, so nothing read
    the whole sentence, the doctor the caller had named was lost, and the turn degraded to reading out every
    doctor in Cardiology. The name was findable without any network call: the same gazetteer the fast path
    already carries matches it at 0.92 in Hindi, 1.00 in Bengali and English.

    Reported as found=False -- a local match is weaker evidence than the clinic's own answer, so the caller is
    ASKED ("do you mean Dr Subrata Banerjee?") rather than told. Never used when clinic-api did answer: its
    answer is authoritative, including when it names nobody."""
    if _fast_path is None:
        return None
    lang = session.lang or "bn"
    name, _form, score = _fast_path.catalogue.match(text, "doctor", lang, COMMIT_FLOOR)
    if not name or score < COMMIT_FLOOR:
        return None
    row = next((d for d in _fast_path.catalogue.doctor_rows if d.get("name") == name), None)
    if row is None:
        return None
    logger.info("[%s] mention lookup down -- %r found in the local catalogue (%.2f)", session.call_id, name, score)
    return {
        "found": False,
        "count": 1,
        "query": text,
        "doctors": [{**row, "strength": "titled" if _says_doctor(text) else "bare"}],
    }


def _mention_choices(mention: dict, lang: str) -> list[tuple[str, str]]:
    """(canonical name, how `lang`'s voice says it) for every doctor the sentence named -- the shape st.doctor_choices
    holds, so the caller's answer ("প্রথমজন", "ঘোষ", "হ্যাঁ") is taken by _pick_offered_doctor."""
    key = {"bn": "full_name_bn", "hi": "full_name_hi"}.get(lang)
    return [
        (d["name"], (d.get(key) if key else None) or d.get("full_name") or d["name"])
        for d in mention.get("doctors") or []
        if isinstance(d, dict) and d.get("name")
    ]


def _mention_question(mention: dict, lang: str) -> str:
    """Several doctors named: which one ("একাধিক ডাক্তার পেলাম -- কার কথা বলছেন, প্রবীর ঘোষ নাকি সঞ্জয় গুহ?").
    One named only approximately: "do you mean Dr. ...?". Never an answer about either."""
    choices = _mention_choices(mention, lang)
    if len(choices) == 1:
        label = choices[0][1]
        title = {"bn": "ডাক্তার", "hi": "डॉक्टर"}.get(lang, "" if label.lower().startswith("dr") else "Dr.")
        return entity_text.confirm_question("doctor_name", f"{title} {label}".strip(), lang)
    return doctor_availability_reply({"doctor_name": ""}, {**mention, "found": False, "ambiguous": True}, lang)


def _remember_doctor(session: CallSession, canonical: str) -> None:
    """The doctor just confirmed is now what the call is about, so "উনি কখন বসেন?" ("when does he sit?") on the next
    turns is answered about this doctor (agent/enquiry_followup.resolve_followup_slot). It replaces the remembered
    topic rather than joining it: two doctors remembered would make "he" ambiguous, and an ambiguous reference asks."""
    session.last_enquiry_entities = [EnquiryEntity(kind="doctor_name", value=canonical)]
    session.last_enquiry_turn = session.turn_count


def _titled(mention: dict | None) -> bool:
    """Did the caller say "doctor" beside the name ("ডাক্তার ঘোষ", "Dr Guha")? See _doctor_answer_expected for why."""
    return bool(mention) and any(d.get("strength") == "titled" for d in mention.get("doctors") or [])


def _doctor_answer_expected(session: CallSession) -> bool:
    """May a bare name in this turn be read as a DOCTOR? Only when nothing else is waiting for an answer, or the open
    booking is still asking which doctor.

    Bengali patients share surnames with this clinic's doctors all the time -- Das, Sen, Ghosh, Roy. Once the doctor
    is settled the booking asks for the PATIENT's name, and "আমার নাম অমিত দাস" read as a doctor would quietly move the
    booking to Dr. Das. A mention with the word "doctor" beside it (_titled) is still a doctor at any point."""
    st = session.booking
    if st is not None:
        return st.action == "book_appointment" and not st.doctor_verified
    return not _unrelated_flow_active(session)


def _expecting_a_doctor_name(session: CallSession) -> bool:
    """Is this turn, as a whole, the answer to "which doctor?" -- the open booking is still asking which doctor, or
    the doctors of a department were read out on the turn before ("... কার কাছে অ্যাপয়েন্টমেন্ট নিতে চান?")?

    Then a bare name ("অপর্ণা বসু") needs no word like "doctor" or "see" beside it to be worth the clinic's lookup:
    it IS the answer, and the clinic's own table decides whether it names exactly one doctor. Live 2026-10-10 three of
    the four names read out came back from the model as `unrelated_caller`, and because the sentence held no such
    word the clinic was never asked: the caller got "নমস্কার, কী সাহায্য করতে পারি?" for naming a doctor offered a
    moment earlier."""
    st = session.booking
    if st is not None:
        return (
            st.action == "book_appointment"
            and st.stage == "collecting"
            and not st.is_stale()
            and not st.doctor_verified
        )
    offered = session.offered_doctors
    return offered is not None and session.turn_count - offered[1] <= 1


def _intent_for_a_named_doctor(session: CallSession, text: str, mention: dict) -> str:
    """What a turn that names one of our doctors wants. Seeing them -> a booking; the answer to the booking's own
    "which doctor?" -> that booking; one of the doctors just read out -> a booking; anything else -> availability."""
    if _wants_to_see_a_doctor(text):
        return "book_appointment"
    st = session.booking
    if st is not None and st.action == "book_appointment" and not st.doctor_verified:
        return "book_appointment"
    offered = session.offered_doctors
    if offered is not None and session.turn_count - offered[1] <= 1 and mention.get("found"):
        named = [d.get("name") for d in (mention.get("doctors") or []) if isinstance(d, dict)]
        if len(named) == 1 and named[0] in offered[0]:
            return "book_appointment"
    return "doctor_availability"


# "The first one", in every language a caller here answers in.
_DOCTOR_ORDINALS = (
    ("প্রথম", "প্রথমজন", "প্রথমটা", "প্রথমটি", "প্রথমজনকে", "প্রথমজনের", "প্রথমটাকে", "first", "1st", "पहला", "पहले",
     "पहली", "pehla", "prothom"),
    ("দ্বিতীয়", "দ্বিতীয়জন", "দ্বিতীয়টা", "দ্বিতীয়টি", "দ্বিতীয়জনকে", "দ্বিতীয়জনের", "দ্বিতীয়টাকে", "second", "2nd",
     "दूसरा", "दूसरे", "दूसरी", "doosra", "ditiyo"),
    ("তৃতীয়", "তৃতীয়জন", "তৃতীয়টা", "তৃতীয়জনকে", "তৃতীয়জনের", "third", "3rd", "तीसरा", "तीसरे", "तीसरी"),
    ("চতুর্থ", "চতুর্থজন", "চতুর্থজনকে", "চতুর্থজনের", "fourth", "4th", "चौथा", "चौथे", "चौथी"),
)


def _pick_offered_doctor(st, text: str, slot_value: str | None, lang: str) -> str | None:
    """The canonical name of the doctor the caller just chose from the ones offered, or None.

    By position ("প্রথমজন", "the second one"), by a bare yes when exactly one was offered, or by a
    WHOLE word of one offered name. Whole words, because "সেন" is inside "সেনগুপ্ত": a caller who
    answers "Sen or Sengupta?" with "সেন" means Sen, and a substring test would call that a tie and
    ask the same question again -- the loop recorded on this tree's doctor flow. An answer that fits
    more than one offer returns None and is looked up as said."""
    choices = st.doctor_choices
    if not choices:
        return None
    said = set(name_tokens(" ".join(x for x in (text, slot_value) if x)))
    for index, words in enumerate(_DOCTOR_ORDINALS):
        if index < len(choices) and said & set(words):
            return choices[index][0]
    if len(choices) == 1 and classify_yes_no(text, lang) == "yes":
        return choices[0][0]
    hits = [canon for canon, label in choices if said & set(name_tokens(f"{canon} {label}"))]
    return hits[0] if len(hits) == 1 else None


def _same_doctor(st, value: str | None) -> bool:
    """Is `value` just the already-confirmed doctor said again? The model re-sends the doctor slot on
    later turns, and the stored name is the clinic's canonical one ("Dr. R. Pal"), never what the caller
    said ("পাল") -- so a plain comparison called every repeat a change. The caller then heard "OK, I've
    changed the doctor to পাল" on every turn, and the doctor was checked with the clinic all over again."""
    if not st.doctor_verified or not value:
        return False
    said = set(name_tokens(value))
    if not said:
        return False
    known = (st.doctor_said, st.slots.get("doctor_name"), *st.doctor_spoken.values())
    return any(k and said == set(name_tokens(k)) for k in known)


_TEST_INTENTS = ("book_test", "add_test_booking", "test_prep", "test_rate", "home_collection")


def _waiting_for(st) -> str | None:
    """The field the open booking asks for next."""
    return (missing_required(st) or [None])[0]


def _names_a_real_test(slots: dict, lang: str) -> bool:
    """Does any test the model filed name one the clinic actually has? Checked against the catalogue at the fast
    path's own commit floor. Without a catalogue (clinic-api was down at boot) nothing can vouch for it: asked only
    while the booking waits for the patient, so the booking keeps the turn -- a name read back wrongly costs one "no";
    a booking caller sent to the lab counter loses the booking."""
    named = [n for n in [slots.get("test_name"), *(slots.get("test_names") or [])] if n]
    if _fast_path is None:
        return False
    for name in named:
        key, _form, score = _fast_path.catalogue.match(name, "test", lang, COMMIT_FLOOR)
        if key and score >= COMMIT_FLOOR:
            return True
    return False


def _booking_answer_misfiled(intent: str, slots: dict, st, text: str = "", lang: str = "bn") -> bool:
    """Is this turn really the open doctor booking's answer, filed by the model under another intent?

    An open booking OWNS the caller's next answer -- the sibling tree's rule (its _continue_pending runs
    before intent extraction for exactly this reason). Here the model runs on every turn, and only an
    "unclear" label was handed back to the booking; any other label took the turn away. Live call
    2026-10-06: mid-booking, "do it for tomorrow" came back `book_test`, and the caller was told lab
    tests are booked at the counter -- the doctor booking silently abandoned.

    Narrow on purpose. A turn that names its OWN subject -- a test, a different doctor once one is
    settled -- is a real side question and is left alone; the topic flow brings the booking back after.
      * a test intent with no test named      -> it was the booking's answer
      * a test intent while the patient is asked for, naming no test the clinic has -> the patient's name or number
        (live 2026-10-07, all three languages: "রোগীর নাম সৌরভ", "मरीज़ का नाम सौरभ", "Patient name is Sourav" came
        back `book_test` with test_name "সৌরভ"/"Sourav", and the caller was sent to the lab counter)
      * doctor_availability before the doctor is settled -> the answer to "which doctor?"
      * department_query before the doctor is settled    -> the answer to "which doctor, or the problem?"
    """
    if intent in _TEST_INTENTS:
        if not (slots.get("test_name") or slots.get("test_names")):
            return True
        return _waiting_for(st) in ("patient_name", "phone") and not _names_a_real_test(slots, lang)
    if intent in ("doctor_availability", "department_query"):
        if not st.doctor_verified:
            return True
        # After the doctor is settled the booking is asking for the PATIENT. A name or a number given then is the
        # booking's answer even when it carries a doctor's surname -- live 2026-10-07, "प्रिया सेन, नौ आठ तीन ..." for
        # Dr. Arindam Sen's booking was filed as an availability question and the caller was asked "do you mean
        # Dr. Arindam Sen?" instead of being read the booking back. Saying "doctor" still makes it a doctor question.
        if _says_doctor(text):
            return False
        named_patient = slots.get("patient_name") or slots.get("phone") or spoken_phone(text)
        # Waiting for the patient's name or number: that is the answer, however garbled. Live run 2, 2026-10-07:
        # "প্রিয়া সেন, নয় আট তিন ..." was heard as "পৃজা সেন নজর আট তিন একএক ...", no number could be read from it,
        # and the caller was told Dr. Sen's hours instead of being asked for the rest.
        waiting_for_patient = _waiting_for(st) in ("patient_name", "phone")
        return bool(named_patient) or waiting_for_patient
    return False


# The word "doctor" in every script and spelling a caller here uses (clinic-api/doctor_mention.TITLE_WORDS has the same).
_DOCTOR_WORD = re.compile(
    r"(?<![ঀ-৿ऀ-ॿa-z])(?:ডাক্তার|ডক্টর|ডাঃ|ডা\.|डॉक्टर|डाक्टर|डॉ|(?:doctor|dr|daktar|doktor)(?![a-z]))", re.IGNORECASE
)


def _faq_topic_said(text: str, lang: str) -> str | None:
    """The clinic FAQ topic whose keyword phrase this sentence carries, by the fast path's own table and commit floor
    (a tie to the more specific phrase) -- or None. Never for a sentence that says "doctor": an unknown doctor's hours
    are not the clinic's."""
    if _fast_path is None or _says_doctor(text):
        return None
    # Our own name is not part of the question: "সোনস্কানের অযাড্ড্রেস টা কি" is an address question with the
    # clinic's name in front of it, and left in, it drags every keyword comparison down (agent/clinic_identity).
    text = strip_clinic_name(text)
    topic, _form, score = _fast_path.catalogue.match(text, "faq", lang, FAQ_COMMIT_FLOOR, prefer_longer=True)
    return topic if topic and score >= FAQ_COMMIT_FLOOR else None


def _says_doctor(text: str) -> bool:
    return bool(_DOCTOR_WORD.search(unicodedata.normalize("NFC", text or "")))


# Words that put a doctor's sitting in question -- "when", "sits", "available" -- in every script a caller uses here.
_SITTING_WORD = re.compile(
    r"(?:বসেন|বসবেন|বসছেন|কবে|কখন|বসার|बैठते|बैठेंगे|बैठती|कब|(?<![a-z])(?:sits?|sitting|available|when|bosen|boshen|kobe)(?![a-z]))",
    re.IGNORECASE,
)


def _might_name_a_doctor(text: str) -> bool:
    """Could this turn be about a doctor at all? Only then is an unclassified turn worth a clinic lookup: "hello" and
    "thank you" are not, and smalltalk needs no tools (tests/test_orchestrator_persona.py pins that)."""
    t = unicodedata.normalize("NFC", text or "")
    return _says_doctor(t) or _wants_to_see_a_doctor(t) or bool(_SITTING_WORD.search(t))


async def _offer_next_sitting(session: CallSession, st, lang: str, sitting_over: bool = False) -> bool:
    """The day given is one the doctor does not sit: name them and offer their next sitting, so the caller
    is not left guessing days. False (nothing said) if the clinic cannot be asked -- the caller then gets
    the plain "another day?" sentence, never silence."""
    try:
        result = await _tools.get_doctor_availability(st.slots["doctor_name"], None)
    except ToolCallError as e:
        logger.warning("[%s] next-sitting offer unavailable (%s)", session.call_id, e)
        return False
    if not result.get("found"):
        return False
    next_date = result.get("next_available_date") or result.get("date")
    if next_date:
        _remember_offer(st, [{"date": next_date, "time_slot": ""}])
    st.asked_fields.add("date")
    await _speak(session, doctor_day_unavailable_prompt(result, lang, sitting_over), lang)
    return True


def _remember_doctor_choices(st, doctors: list, lang: str) -> None:
    key = {"bn": "full_name_bn", "hi": "full_name_hi"}.get(lang)
    st.doctor_choices = [
        (d["name"], (d.get(key) if key else None) or d.get("full_name") or d["name"])
        for d in doctors
        if isinstance(d, dict) and d.get("name")
    ]


async def _spoken_departments(lang: str) -> list[str]:
    """The clinic's departments as `lang` can say them, live from clinic-api; [] if it is down."""
    try:
        result = await _tools.list_departments()
    except ToolCallError as e:
        logger.warning("department list unavailable (%s)", e)
        return []
    key = {"bn": "name_bn", "hi": "name_hi"}.get(lang)
    return [
        (d.get(key) if key else None) or d["name"]
        for d in result.get("departments") or []
        if isinstance(d, dict) and d.get("name")
    ]


async def _department_doctors(query: str, lang: str) -> dict | None:
    """`query` read as a department ("হার্টের ডাক্তার", "cardiology") or as a problem ("বুকে ব্যথা") ->
    clinic-api's doctors-in-that-department answer, or None. Both lookups are the clinic's own matching
    against its own table; nothing here guesses a department."""
    try:
        result = await _tools.doctors_by_department(query)
        if result.get("found"):
            return result
        route = await _tools.route_department(query, lang)
        if route.get("matched") and route.get("department_name"):
            result = await _tools.doctors_by_department(route["department_name"])
            if result.get("found"):
                return result
    except ToolCallError as e:
        logger.warning("department lookup for %r unavailable (%s)", query, e)
    return None


async def _department_named_in_slot(value: str | None) -> dict | None:
    """`value` -- what the model wrote in the DOCTOR slot -- as a department, by clinic-api's own table: the whole
    answer (found, with its doctors) or None. A near match, an ambiguous one and an outage are all None, so the turn
    carries on as the doctor lookup it always was."""
    if not value or _tools is None:
        return None
    try:
        result = await _tools.doctors_by_department(value)
    except ToolCallError as e:
        logger.warning("department lookup for the doctor slot %r unavailable (%s)", value, e)
        return None
    return result if result.get("found") and result.get("doctors") else None


def _question_only(text: str) -> str:
    """The last question of a reply: what a silence re-ask should say again when the reply was a long list ending in
    one. "… সুতপা চ্যাটার্জী। কার কাছে অ্যাপয়েন্টমেন্ট নিতে চান?" -> "কার কাছে অ্যাপয়েন্টমেন্ট নিতে চান?"."""
    t = (text or "").strip()
    if not t.endswith(("?", "\uff1f")):
        return t
    head = t[:-1]
    cut = max(head.rfind("।"), head.rfind("."), head.rfind("?"), head.rfind("\uff1f"))
    return t[cut + 1 :].strip() if cut >= 0 else t


async def _speak_list(session: CallSession, text: str, lang: str | None = None, extra_s: float = 0.0) -> float:
    """_speak for a LONG LIST that ends in a question (a department's doctors, the departments). It is read in full
    once; if the caller then goes quiet, the re-ask repeats only the question. Live 2026-10-10: the whole list of
    doctors was read three times in a row ("আপনি কি লাইনে আছেন? <list>", "কোনো তাড়া নেই। <list>"), because the open
    question kept for a re-ask was the entire reply. A read-back with details in it is NOT spoken through here: its
    re-ask keeps every detail the caller is being asked to confirm."""
    duration = await _speak(session, text, lang)
    asked = session.open_question
    if asked is not None and asked[0] == text:
        session.open_question = (_question_only(text), asked[1])
        session.open_question_extra_s = extra_s
    return duration


def _remember_offered_doctors(session: CallSession, result: dict) -> None:
    rows = [d for d in (result.get("doctors") or []) if isinstance(d, dict) and d.get("name")]
    session.offered_doctors = ([d["name"] for d in rows], session.turn_count, rows)


async def _department_named_in(text: str) -> dict | None:
    """The clinic's own answer for the department a sentence NAMES -- found, with its doctors -- or None. By name
    only (not the symptom router), so "বুকে ব্যথা" is never read as a department."""
    if not text or _tools is None:
        return None
    try:
        result = await _tools.doctors_by_department(text)
    except ToolCallError as e:
        logger.warning("department lookup unavailable (%s)", e)
        return None
    return result if result.get("found") and result.get("doctors") else None


# "The second one", said and nothing else: the answer to a list just read out. Words a caller adds around it.
_POSITION_FILLER = frozenset({
    "টা", "টি", "জন", "ওনার", "ওনাকে", "উনি", "কাছে", "ই", "আমি", "দেখাব", "দেখাবো", "দেখাতে", "চাই", "দিন", "করুন",
    "one", "the", "please", "ko", "को", "से", "दिखाना", "चाहता", "चाहती", "हूँ", "हूं",
})


def _only_a_position(text: str) -> bool:
    said = name_tokens(text)
    if not said:
        return False
    ordinals = {w for words in _DOCTOR_ORDINALS for w in words}
    return any(t in ordinals for t in said) and all(t in ordinals or t in _POSITION_FILLER for t in said)


async def _speak_department_doctors(session: CallSession, result: dict, lang: str) -> None:
    """Read out a department's doctors AND remember who was offered. The reply ends by asking whom to book with, so the
    next turn naming one of them is the answer to it (see the `offered_doctors` block in _dispatch_turn)."""
    _remember_offered_doctors(session, result)
    await _speak_list(session, doctors_by_department_reply(result, lang), lang)


async def _answer_doctor_in_department(session: CallSession, text: str, lang: str) -> bool:
    """"Do you have a doctor in <department>?" -> the department's doctors, from the clinic's own table. True when
    it answered; False for anything it is not sure of, and the turn then goes to the model unchanged.

    Speaks only when BOTH hold: no doctor is named in the sentence (a named doctor is an availability question with
    its own path, and is also what makes "Dr Sen of cardiology" safe), and clinic-api's by-department lookup says it
    found a department in the sentence. An ambiguous or near match is deliberately NOT answered here -- it falls
    through to the existing flow that asks about it."""
    mention = await _doctors_mentioned(session, text)
    if mention is None or mention.get("count"):
        return False
    try:
        result = await _tools.doctors_by_department(text)
    except ToolCallError as e:
        logger.warning("[%s] department lookup unavailable (%s)", session.call_id, e)
        return False
    if not result.get("found"):
        return False
    logger.info("[%s] caller asked whether a doctor is available in a department -- answered, skipping the model", session.call_id)
    _note(session, "answers_given")
    await _speak_list(session, doctors_by_department_reply(result, lang), lang)
    return True


async def _ask_which_doctor(session: CallSession, st, lang: str, text: str | None = None) -> bool:
    if text and not st.doctor_verified:
        # The sentence may already NAME a department ("मैं गायनी में दिखाना चाहता हूँ"). The intent model does not
        # reliably carry that into a slot -- live 2026-10-09 it came back with no usable doctor, and the caller who had
        # just said the department was asked "which doctor?" and read the whole department list back. The clinic's own
        # matcher (_department_doctors) reads the sentence instead, the same lookup _settle_doctor already uses when the
        # model DID put a department in the doctor slot. Checked before the retry is counted: this is an answer, not a
        # failed ask, and it must never walk the caller towards the hand-off limit.
        dept = await _department_doctors(text, lang)
        if dept is not None:
            logger.info("[%s] the sentence names a department (%s) -> its doctors", session.call_id, dept.get("department"))
            _remember_doctor_choices(st, dept.get("doctors") or [], lang)
            st.asked_fields.add("doctor_name")
            await _speak_list(session, doctors_by_department_reply(dept, lang), lang)
            return True
    asks = st.note_retry("doctor_name")
    if asks > DOCTOR_ASK_LIMIT:
        await _handoff_to_human(session, "doctor_unresolved")
        return True
    st.asked_fields.add("doctor_name")
    # The departments are read out on the FIRST ask only. Eight names again on every re-ask is a wall of
    # words that buries the question; a caller who missed them hears them again if they go quiet (the
    # silence re-ask repeats the question as first asked).
    departments = await _spoken_departments(lang) if asks == 1 else []
    await _speak_list(session, doctor_choice_prompt(departments, lang), lang)
    return True


async def _settle_doctor(session: CallSession, st, lang: str, text: str | None = None) -> bool:
    """Resolve the booking's doctor before anything after it is asked. True when this turn's reply has
    been spoken (the caller must answer about the doctor first); False to carry on.

    No doctor yet -> "which doctor?", ALWAYS. It used to be asked only when the caller had said "a doctor"; a booking
    opened with just a day ("কাল সকালে অ্যাপয়েন্টমেন্ট চাই") went on to ask the time and the patient first. The doctor
    decides which days and times exist at all, so it is the first thing settled; a day already given is kept."""
    name = st.slots.get("doctor_name")
    if not name:
        return await _ask_which_doctor(session, st, lang, text)
    if st.doctor_verified:
        return False
    try:
        result = await _tools.get_doctor_availability(name, st.slots.get("date"))
    except ToolCallError as e:
        # An outage must not cost the caller the booking: carry on, and the hold says so.
        logger.warning("[%s] doctor check unavailable (%s)", session.call_id, e)
        return False
    if result.get("found"):
        st.doctor_said = name
        st.slots["doctor_name"] = result.get("doctor_name") or name
        # How the Bengali and Hindi voices SAY this doctor -- the read-back uses these, never the
        # Latin catalogue name, which those voices cannot pronounce.
        st.doctor_spoken = {k: result[k] for k in ("doctor_name_bn", "doctor_name_hi") if result.get(k)}
        st.doctor_verified = True
        st.doctor_choices = []
        _remember_doctor(session, st.slots["doctor_name"])
        return False

    # Not one doctor -- so nothing may ever be booked against this name.
    st.slots.pop("doctor_name", None)
    st.doctor_spoken = {}
    if result.get("ambiguous"):
        canonical = result.get("did_you_mean") or []
        st.doctor_choices = list(zip(canonical, result.get(f"did_you_mean_{lang}") or canonical))
        st.asked_fields.add("doctor_name")
        await _speak(session, doctor_availability_reply({"doctor_name": name}, result, lang), lang)
        return True
    dept = await _department_doctors(name, lang)
    if dept is not None:
        logger.info("[%s] %r is a department, not a doctor -> %s", session.call_id, name, dept.get("department"))
        _remember_doctor_choices(st, dept.get("doctors") or [], lang)
        st.asked_fields.add("doctor_name")
        await _speak_list(session, doctors_by_department_reply(dept, lang), lang)
        return True
    if st.note_retry("doctor_name") > DOCTOR_ASK_LIMIT:
        await _handoff_to_human(session, "doctor_unresolved")
        return True
    if result.get("did_you_mean"):
        # Near spellings are OFFERED, never chosen (CLAUDE.md, "Doctor Nobody").
        canonical = result["did_you_mean"]
        st.doctor_choices = list(zip(canonical, result.get(f"did_you_mean_{lang}") or canonical))
    st.asked_fields.add("doctor_name")
    await _speak(session, doctor_availability_reply({"doctor_name": name}, result, lang), lang)
    return True


# story: "Caller names only a doctor"
# story: "Caller asks for the earliest available appointment"
# story: "Requested slot is already taken"
# A refused move, by the clinic's reason -> the fields asked again (the first is the question put).
_MOVE_RETRY_FIELDS = {
    "slot_taken": ("new_time_slot",),
    "invalid_slot": ("new_time_slot",),
    "slot_passed": ("new_time_slot",),
    "sitting_over": ("new_date", "new_time_slot"),
    "doctor_not_available_that_day": ("new_date", "new_time_slot"),
    "date_in_past": ("new_date", "new_time_slot"),
    "invalid_date": ("new_date", "new_time_slot"),
}

_DATE_INTENTS = ("book_appointment", "doctor_availability", "reschedule_appointment")


# Where a day and a time land, per flow: (the slot the model filled, the slot the caller has already settled).
# A move keeps the current appointment's day in "date" and the new one in "new_date", and the model files a bare
# answer under either, so both are guarded against the settled new day.
_DATE_SLOTS = {
    "reschedule_appointment": (("date", "new_date"), ("new_date", "new_date")),
    None: (("date", "date"),),
}
_TIME_SLOTS = {
    "reschedule_appointment": (("time_slot", "new_time_slot"), ("new_time_slot", "new_time_slot")),
    None: (("time_slot", "time_slot"),),
}


def _drop_unspoken_change(session: CallSession, intent: str, slots: dict, table: dict) -> dict:
    """Nothing the caller said this turn names a day (or a time), so the model's may not CHANGE one they settled.

    The intent model fills "date" on every booking turn whether or not a day was spoken. Live 2026-10-08: the
    caller had taken the Friday the agent itself offered, then answered "কোন সময়ের জন্য?" with just "এগারোটা" --
    a time, no day. The model attached TODAY's date, it overwrote the Friday, the caller heard "ঠিক আছে, বদলে
    ... করে দিলাম" and then "ডাঃ ভট্টাচার্য ওই দিন বসেন না" -- about the very day the agent had offered two turns
    earlier, and the booking could not be finished.

    Same rule as agent/entity_grounding.py applies to test names, on the other slots the model volunteers: what
    the caller's words do not support may not overwrite what the caller actually gave. A day they really are
    changing is said out loud, and `spoken_dates` reads it before this is ever reached."""
    st = session.booking
    if st is None or st.action != intent:
        return slots
    out = dict(slots)
    for key, settled_key in table.get(intent, table[None]):
        settled, volunteered = st.slots.get(settled_key), out.get(key)
        if settled and volunteered and volunteered != settled:
            logger.info(
                "[%s] %s=%r is not in what was heard -- keeping the %r the caller gave",
                session.call_id, key, volunteered, settled,
            )
            out.pop(key)
    return out


def _pin_spoken_dates(session: CallSession, intent: str, slots: dict, text: str) -> dict:
    """The date(s) the caller said, from the calendar, in place of the model's. A turn with no date words the
    calendar recognises keeps the model's date ONLY while it changes nothing the caller settled
    (_drop_unspoken_change). Moving a booking: the last day said is the NEW day, and with two ("Thursday's
    appointment to Saturday") the first is the current one."""
    if intent not in _DATE_INTENTS:
        return slots
    said = spoken_dates(text)
    if not said:
        return _drop_unspoken_change(session, intent, slots, _DATE_SLOTS)
    out = dict(slots)
    if intent == "reschedule_appointment":
        out["new_date"] = said[-1]
        if len(said) > 1:
            out["date"] = said[0]
        elif not slots.get("new_date"):
            out.pop("date", None)  # the one day said, filed by the model as "date", is the day to move to
    else:
        out["date"] = said[-1]
    if (out.get("date"), out.get("new_date")) != (slots.get("date"), slots.get("new_date")):
        logger.info(
            "[%s] spoken day(s) %s -> date=%s new_date=%s (the model had date=%s new_date=%s)",
            session.call_id, said, out.get("date"), out.get("new_date"), slots.get("date"), slots.get("new_date"),
        )
    return out


def _drop_a_time_filed_as_doctor(slots: dict) -> dict:
    """A clock time the model copied into the doctor slot is not a doctor. Live 2026-10-10: "পৌনে এগারোটার সময় দেখাবো
    ডক্টরকে" came back with doctor_name "পৌনে এগারোটার" and the caller heard "there is no doctor called পৌনে
    এগারোটার". The time itself is pinned from the sentence by _pin_spoken_times; the slot only loses the wrong value."""
    name = slots.get("doctor_name")
    if name and spoken_times(str(name)):
        return {k: v for k, v in slots.items() if k != "doctor_name"}
    return slots


def _pin_spoken_times(session: CallSession, intent: str, slots: dict, text: str) -> dict:
    """The clock time the caller said, from agent/spoken_time.py, in place of the model's -- the same rule as the day
    (_pin_spoken_dates). When nothing in the sentence is a time, the model's own value is kept only if it reads as one:
    "সকাল" or "এই সময়ে" is not a slot, and sent to the clinic it came back "that time is not valid" (live 2026-10-07)."""
    if intent not in _DATE_INTENTS:
        return slots
    key = "new_time_slot" if intent == "reschedule_appointment" else "time_slot"
    said = spoken_times(text)
    out = dict(slots)
    if said:
        out[key] = said[-1]
    else:
        if out.get(key):
            out[key] = as_clock(out[key])
            if out[key] is None:
                out.pop(key)
        # ...and a time the caller did not say may no more replace the one they settled than a day may.
        out = _drop_unspoken_change(session, intent, out, _TIME_SLOTS)
    if out.get(key) != slots.get(key):
        logger.info("[%s] spoken time %s -> %s=%s (the model had %r)", session.call_id, said, key, out.get(key), slots.get(key))
    return out


# The flows that take a phone number from the caller: to send a confirmation to, or to find a booking by.
_PHONE_INTENTS = (
    "book_appointment", "lookup_booking", "cancel_appointment", "reschedule_appointment", "resend_confirmation",
)


# A confirmation number read out ("KCD-2026...", "কনফার্মেশন নম্বর ...") is the model's to read, never a phone number.
_CONFIRMATION_CUE = re.compile(
    r"kcd|কে\s*সি\s*ডি|के\s*सी\s*डी|কনফার্মেশন|কনফারমেশন|कन्फ़र्मेशन|कन्फर्मेशन|confirmation", re.IGNORECASE
)


def _rest_of_number(session: CallSession, text: str) -> str | None:
    """The digits this turn offers toward a number the open task is still waiting for, when they are too few to be a
    number on their own (spoken_phone wants seven):
      * the rest of a number already begun ("নাইট নাইন" after "so far I have 0 1 2 3 4 5 6 7") -- any count;
      * a broken reading of the whole number -- its longest run, at least four digits. Live 2026-10-07: "ছয়" heard as
        "শো" split "শূন্য এক দুই তিন চার পাঁচ শো সাত আট নয়" into 6 + 3 digits, nothing counted as a number, and the
        keyword path answered "ফোন নম্বর" with the clinic's helpdesk number. Held and read back instead, so the caller
        hears what was taken and gives the rest."""
    st = session.booking
    if st is None or st.stage != "collecting" or st.is_stale():
        return None
    runs = spoken_digit_runs(text)
    if not runs:
        return None
    best = max(runs, key=len)
    if phone_digits_so_far(st):
        return best
    if _waiting_for(st) in ("phone", "contact_phone", "confirmation_id") and len(best) >= 4:
        return best
    return None


def _number_expected(session: CallSession, text: str) -> bool:
    """An open task is waiting for a number (the patient's phone, or the one a booking was made from) and this turn
    carries one -- whole, the rest of one already begun, or a broken reading of one. Not a confirmation number."""
    st = session.booking
    if st is None or st.stage != "collecting" or st.is_stale():
        return False
    if _waiting_for(st) not in ("phone", "contact_phone", "confirmation_id") and not _asks_name_and_phone_together(st):
        return False
    if _CONFIRMATION_CUE.search(unicodedata.normalize("NFC", text or "")):
        return False
    return bool(spoken_phone(text) or _rest_of_number(session, text))


def _asks_name_and_phone_together(st) -> bool:
    """The patient's name is the next field AND the phone is still owed: the booking asked both in ONE question
    ("রোগীর নাম আর একটা ফোন নম্বর বলবেন?"), so a whole number in the answer is the phone even though the name is
    what `_waiting_for` still reports first. Live 2026-10-10: "রোগীর নাম রাজস্বী ফোন নম্বার ওন ওন টু টু ..." was
    read as the clinic-contact FAQ ("ফোন নম্বর") because the number guard was off while the NAME was still missing,
    and the caller was given the helpdesk number instead of having the booking taken."""
    missing = missing_required(st)
    return bool(missing) and missing[0] == "patient_name" and "phone" in missing


def _pin_spoken_phone(session: CallSession, intent: str, slots: dict, text: str) -> dict:
    """The number the caller read out, digit by digit (agent/spoken_digits.py), when the model did not already write it
    as a whole number. A partial run is passed on too: _absorb_phone_digits keeps it until the rest is said.

    Live 2026-10-07: "नौ आठ तीन शून्य शून्य पांच छह सात आठ नौ" was heard perfectly, the model wrote it back in words, no
    digit was found, and the booking decided the caller had REFUSED a number."""
    if intent not in _PHONE_INTENTS:
        return slots
    heard = spoken_phone(text) or _rest_of_number(session, text)
    if not heard:
        return slots
    key = "contact_phone" if slots.get("contact_phone") and not slots.get("phone") else "phone"
    if normalise_phone(slots.get(key)):
        return slots  # the model already has the whole number
    logger.info("[%s] spoken number -> %s (%d digits; the model had %r)", session.call_id, key, len(heard), slots.get(key))
    return {**slots, key: heard}


def _as_new_day_and_time(slots: dict) -> dict:
    """An answer to "which day / what time?" while moving a booking: the model files a bare "Saturday" or
    "11 o'clock" as date / time_slot, the moving flow needs new_date / new_time_slot."""
    out = dict(slots)
    if out.get("date") and not out.get("new_date"):
        out["new_date"] = out.pop("date")
    if out.get("time_slot") and not out.get("new_time_slot"):
        out["new_time_slot"] = out.pop("time_slot")
    return out


async def _use_fetched_booking(session: CallSession, st) -> None:
    """Cancel or move the booking this caller was just told about, without asking for its number again.
    With a confirmation number and nothing fetched yet, the booking is read by that number -- the bearer
    token the lookup already accepts -- so the doctor is known for checking the new day."""
    fetched = session.fetched_booking
    if not st.slots.get("confirmation_id") and fetched:
        st.slots["confirmation_id"] = fetched["confirmation_id"]
        return
    cid = st.slots.get("confirmation_id")
    if cid and (not fetched or fetched.get("confirmation_id") != cid):
        with contextlib.suppress(ToolCallError):
            found = await _tools.lookup_bookings(confirmation_id=cid)
            bookings = found.get("bookings") or []
            if bookings:
                session.fetched_booking = bookings[0]


async def _find_booking_by_number(session: CallSession, st, slots: dict, heard_number: bool, lang: str) -> bool:
    """Moving or cancelling: the booking, found by the number the caller booked from. True when the turn was answered
    here -- several bookings on it (which one?), none (said so, the number read back digit by digit), or a number still
    arriving in pieces (what is held so far, and the rest). Never the opening question again as if nothing was said:
    live 2026-10-07, a caller read their number four times and was asked for it four times."""
    if st.slots.get("confirmation_id"):
        return False
    phone = slots.get("phone")
    if not phone:
        so_far = phone_digits_so_far(st) if heard_number else ""
        if so_far:
            st.asked_fields.add("phone")
            await _speak(session, phone_continue_prompt(so_far, lang), lang)
            return True
        return False
    found = await _tools.lookup_bookings(phone=phone)
    bookings = found.get("bookings") or []
    if len(bookings) > 1:
        # CodeRabbit-flagged: never silently act on bookings[0] -- a caller with several bookings names which one first.
        await _speak(session, multiple_bookings_reply(bookings, lang), lang)
        return True
    if not bookings:
        await _speak(session, no_booking_on_number(phone, lang), lang)
        return True
    st.slots["confirmation_id"] = bookings[0]["confirmation_id"]
    session.fetched_booking = bookings[0]
    return False


async def _check_new_day(session: CallSession, st, lang: str) -> bool:
    """Moving a booking: settle the new day against the doctor's real sittings BEFORE the read-back,
    instead of letting the clinic refuse it at the very end. True when the turn was answered here.

      no new day yet        -> the doctor's sitting days, and "which day?"
      the booking's own day -> not a new day: said so, and "another day, or cancel it?"
      a day they don't sit  -> their next sitting is offered ("yes" takes it)
      a time outside hours  -> the chamber hours, and "what time?"

    Any clinic failure returns False: the ordinary question is asked and the clinic still validates on
    commit, so a missed check here costs a question, never a wrong move."""
    fetched = session.fetched_booking or {}
    doctor = fetched.get("doctor_name") if fetched.get("confirmation_id") == st.slots.get("confirmation_id") else None
    if not doctor or fetched.get("kind", "appointment") != "appointment":
        return False
    new_date = st.slots.get("new_date")
    if new_date and new_date == fetched.get("date") and not st.slots.get("new_time_slot"):
        # The day it is already on. Asked again silently, the caller who was pointing at the booking ("the Saturday
        # one" -- Bengali "সরাতে" is both move and remove) heard "which day?" three times. Said, with the choice.
        st.slots.pop("new_date", None)
        st.asked_fields.add("new_date")
        await _speak(session, move_same_day_prompt(fetched, lang), lang)
        return True
    try:
        result = await _tools.get_doctor_availability(doctor, new_date)
    except ToolCallError as e:
        logger.warning("[%s] new-day check unavailable (%s)", session.call_id, e)
        return False
    if not result.get("found"):
        return False
    if not new_date:
        st.asked_fields.add("new_date")
        await _speak(session, move_day_prompt(result, lang), lang)
        return True
    if not result.get("available"):
        st.slots.pop("new_date", None)
        next_date = result.get("next_available_date")
        if next_date:
            _remember_offer(st, [{"date": next_date, "time_slot": ""}])
        # A day the doctor does sit, refused anyway: it is today and the chamber is over (clinic-api _sitting_over).
        sitting_over = _weekday_of(new_date) in (result.get("chamber_days") or [])
        await _speak(session, doctor_day_unavailable_prompt(result, lang, sitting_over), lang)
        return True
    hours = result.get("chamber_hours") or ""
    t = st.slots.get("new_time_slot")
    if t and "-" in hours:
        start, end = hours.split("-", 1)
        if not (start <= t < end):
            st.slots.pop("new_time_slot", None)
            await _speak(session, move_time_prompt(hours, lang), lang)
            return True
    return False


def _weekday_of(iso_date: str | None) -> int | None:
    """Monday = 0, as clinic-api's chamber_days; None for anything that is not an ISO date."""
    try:
        return datetime.date.fromisoformat(iso_date).weekday() if iso_date else None
    except ValueError:
        return None


def _remember_offer(st, slots: list[dict]) -> None:
    st.offered_slots = list(slots)


# story: "Caller names only a doctor"
# story: "Caller asks for the earliest available appointment"
async def _offer_days_or_slots(session: CallSession, st, text: str, lang: str) -> bool:
    """A doctor is known and no day is. Offer instead of asking.

    Asked for the soonest ("Caller asks for the earliest available appointment") -> the first free
    slot with its day and time, plus the next alternatives. Otherwise ("Caller names only a
    doctor") -> confirm the doctor and say when they next sit, so the caller is not guessing dates.

    Availability is read live on every ask, never cached: another caller can take a slot between
    two questions. Returns True when the turn has been answered.

    Any tool failure returns False and the ordinary "which day?" question is asked -- an offer is
    a convenience, and losing it must never cost the caller their booking."""
    doctor = st.slots.get("doctor_name")
    earliest = earliest_request.wants_earliest(text, lang)
    try:
        result = (
            await _tools.doctor_earliest(doctor)
            if earliest
            else await _tools.get_doctor_availability(doctor, None)
        )
    except ToolCallError as e:
        logger.warning("[%s] slot offer unavailable (%s) -- asking for the day instead", session.call_id, e)
        return False
    if not result.get("found"):
        return False

    if earliest:
        if not result.get("available"):
            # Never a bare refusal (E12-S5): a call when a slot opens, through the callback flow
            # this tree already has. A person makes that call -- nothing here watches for a
            # cancellation, so nothing promises an automatic one.
            known = session.identity.phone or st.slots.get("contact_phone") or st.slots.get("phone")
            if not CALLBACKS_ENABLED:
                await _speak(session, earliest_none_prompt(result, lang, offer_callback=False), lang)
                return True
            await _speak(session, earliest_none_prompt(result, lang, offer_callback=not known), lang)
            if known:
                await _queue_callback_request(session, known, lang)
            else:
                session.awaiting_callback_phone = True
            return True
        offered = [{"date": result["date"], "time_slot": result["time_slot"]}]
        offered += [{"date": result["date"], "time_slot": t} for t in (result.get("alternatives") or [])]
        _remember_offer(st, offered)
        await _speak(session, earliest_slots_prompt(result, lang), lang)
        return True

    next_date = result.get("next_available_date") or result.get("date")
    ask_time = not st.slots.get("time_slot")
    if not next_date:
        await _speak(session, doctor_offer_prompt(result, lang, ask_time), lang)
        return True
    _remember_offer(st, [{"date": next_date, "time_slot": ""}])
    # The offer IS the grouped day+time question (slot_grouping's pairing for this action), so
    # the caller is not asked the day twice.
    st.asked_fields.update({"date", "time_slot"} if ask_time else {"date"})
    await _speak(session, doctor_offer_prompt(result, lang, ask_time), lang)
    return True


# story: "Caller names only a doctor"
# story: "Caller asks for the earliest available appointment"
# story: "Requested slot is already taken"
def _take_offered_slot(st, text: str) -> bool:
    """The caller picked one of the slots just read out. Fills the day (and the time, when the
    offer named one) and clears the offer. False when they said something else -- a day of their
    own, a question -- which is then handled normally."""
    chosen = pick_offered_slot(text, st.offered_slots) if st.offered_slots else None
    if chosen is None:
        return False
    st.slots["date"] = chosen["date"]
    if chosen.get("time_slot"):
        st.slots["time_slot"] = chosen["time_slot"]
    st.offered_slots = []
    st.touch()
    return True


# story: "Caller says tomorrow, day after, or next Monday"
async def _confirm_spoken_date(session: CallSession, intent: str, slots: dict, text: str, lang: str) -> bool:
    """Read the date back before the lookup runs. True when the turn has been answered by a
    question instead of an answer.

    The model resolves a relative word to a calendar date on its own (agent/llm.py). This
    compares that against what THIS language's cue table makes of the same words
    (agent/date_check.py) and puts the result to the caller: one date is read back for a yes,
    two readings are offered for a choice. Nothing is looked up until they have heard the day.

    Dates the caller stated outright are read back too -- "the resolved absolute date is ALWAYS
    read back before use" is the criterion, and a misheard "15th" is as wrong as a misread
    "tomorrow"."""
    if session.booking is not None:
        return False  # a booking reads its whole slate back at the end; one question is enough
    verdict = date_check.check(text, slots.get("date"), lang)
    if verdict.action == date_check.ABSENT:
        return False
    if verdict.action == date_check.ASK:
        session.pending_dated_enquiry = (intent, dict(slots), None)
        await _speak(session, date_ask_prompt(verdict.candidates, lang), lang)
        return True
    session.pending_dated_enquiry = (intent, dict(slots), verdict.date)
    await _speak(session, date_check_prompt(verdict.date, verdict.said, lang), lang)
    return True


# story: "Caller says tomorrow, day after, or next Monday"
async def _continue_dated_enquiry(session: CallSession, text: str, lang: str) -> bool:
    """The caller's answer to "<date>. Is that right?" (or to the two-date question)."""
    intent, slots, offered = session.pending_dated_enquiry
    session.pending_dated_enquiry = None
    answer = classify_yes_no(text, lang)
    settled = offered if answer == "yes" else None
    if settled is None:
        # Either they said no, or they named a day instead of answering. Re-read whatever this
        # turn says; a day given now is confirmed in its turn, never used straight away.
        again = date_check.check(text, None, lang)
        if again.action == date_check.CONFIRM:
            session.pending_dated_enquiry = (intent, slots, again.date)
            await _speak(session, date_check_prompt(again.date, again.said, lang), lang)
            return True
        if answer == "no":
            await _speak(session, missing_slot_prompt(intent, "date", lang), lang)
            return True
        return False  # not an answer to this question: handle the turn normally
    slots = {**slots, "date": settled}
    reply = await _answer_enquiry_intent(intent, slots, lang)
    if reply is None:
        await _speak(session, missing_slot_prompt(intent, "doctor_name", lang), lang)
        return True
    await _speak(session, reply, lang)
    return True


# story: "Patient name is misheard"
async def _continue_name_confirm(session: CallSession, text: str, lang: str) -> bool:
    """The answer to "do you mean <name>?", asked because only one decoder produced that name.

    A no does NOT ask the same question again -- a name the recogniser misheard once it will
    mishear again. It drops the name and moves to spelling, which is the whole point of the
    story. Anything that is neither yes nor no is treated as the caller simply saying the name
    again, so the turn is handled normally and the new name replaces the old one."""
    session.awaiting_name_confirm = False
    st = session.booking
    if st is None:
        return False
    answer = classify_yes_no(text, lang)
    if answer == "yes":
        st.name_confirmed = True
        missing = missing_required(st)
        if missing:
            await _speak(session, _next_question(session, st, st.action, missing, lang), lang)
        else:
            mark_confirming(st)
            await _speak(session, booking_confirmation_readback(st.readback_slots(), st.action, lang), lang)
        return True
    if answer == "no":
        st.slots.pop("patient_name", None)
        st.slots.pop("_spelling_buffer", None)
        st.name_confirmed = False
        await _speak(session, spelling_prompt(lang), lang)
        return True
    return False  # not an answer: let the turn be handled as a fresh one


# story: "Caller asks what they have booked"
async def _continue_resend_offer(session: CallSession, text: str, lang: str) -> bool:
    """The answer to "shall I resend the written confirmation?".

    A yes goes through the EXISTING resend path (_tools.resend_confirmation), so there is no
    second way to resend a confirmation in this codebase. Anything that is not a yes or a no is
    not an answer to this question: the offer lapses and the turn is handled as a fresh one, so
    a caller who ignores the offer and asks something else is not made to answer it first."""
    confirmation_id, session.pending_resend = session.pending_resend, None
    answer = classify_yes_no(text, lang)
    if answer == "yes":
        try:
            result = await _tools.resend_confirmation(confirmation_id)
        except ToolCallError as e:
            logger.error("[%s] clinic API call failed: %s", session.call_id, e)
            await _speak(session, phrase("tool_failure", lang), lang, fallback_reason="tool_failure")
            return True
        await _speak(session, resend_reply(result, lang), lang)
        return True
    if answer == "no":
        await _speak(session, phrase("smalltalk_default", lang), lang)
        return True
    return False


# story: "Caller asks what they have booked"
async def _answer_booking_lookup(
    session: CallSession, slots: dict, lang: str, *, verified: bool = False
) -> None:
    """State the next upcoming booking -- but only to someone who has passed the check.

    THE GATE. A phone number the caller SAID is a claim, not proof of owning it, and this stack
    has no caller ID to check it against (agent/identity.py). So the phone/name path goes through
    the same gate and the same security questions as the history flow: nothing about a booking is
    spoken before it passes, and the question is resumed afterwards rather than re-asked.

    A stated CONFIRMATION NUMBER is treated as the bearer token it already is everywhere else in
    this codebase (booking_service.lookup_bookings says so in as many words: 8 random hex
    characters IS the existing access-control model for that one row, and resend_confirmation
    relies on it). That asymmetry is deliberate and is the ONE decision here a named owner should
    confirm -- tightening it would change the resend flow too, which this story did not ask for.
    """
    confirmation_id = slots.get("confirmation_id")
    pending = session.pending_lookup or {}
    phone = slots.get("phone") or pending.get("phone") or session.identity.phone

    if not (confirmation_id or phone):
        # The question stays open for one turn, so the bare number that answers it ("9830012345", which
        # the model files as "unclear") is taken as this lookup's answer -- the same rule a price question
        # uses for a bare test name. Without it the caller was told "I did not understand" for the very
        # number they had just been asked for.
        session.pending_enquiry = ("lookup_booking", session.turn_count)
        await _speak(session, missing_slot_prompt("lookup_booking", "phone", lang), lang)
        return

    if not confirmation_id and not verified and _lookup_verification() == "name":
        await _answer_booking_lookup_by_name(session, phone, slots.get("patient_name"), pending, lang)
        return

    if not confirmation_id and not verified:
        ident = session.identity
        decision = patient_policy.history_gate(ident, ident.patient_ref, lang)
        if not decision.allowed:
            session.pending_lookup = {"phone": phone}
            if SECURITY_QUESTIONS != "off":
                await _start_security_check(session, ident.patient_ref, lang)
                return
            # Nothing beyond the refusal is spoken, and it offers a person -- the same shape the
            # history gate uses when the questions are switched off.
            session.pending_lookup, session.awaiting_handoff_offer = None, True
            await _say_history(session, [decision.statement], lang)
            return

    try:
        result = await _tools.lookup_bookings(phone=phone, confirmation_id=confirmation_id)
    except ToolCallError as e:
        logger.error("[%s] clinic API call failed: %s", session.call_id, e)
        await _speak(session, phrase("tool_failure", lang), lang, fallback_reason="tool_failure")
        return

    bookings = result.get("bookings") or []
    logger.info("[%s] booking lookup: %d upcoming", session.call_id, len(bookings))
    # bookings arrives earliest-first and upcoming-only from clinic-api, so [0] is the NEXT one
    # and a past booking is never read out here.
    if bookings:
        session.pending_resend = bookings[0]["confirmation_id"]
        session.fetched_booking = bookings[0]
    await _speak(session, _with_resume(session, lookup_reply(bookings, lang), lang), lang)


async def _answer_booking_lookup_by_name(
    session: CallSession, phone: str, name: str | None, pending: dict, lang: str
) -> None:
    """LOOKUP_VERIFICATION=name: the number AND the patient's name must both match the booking before
    anything about it is said. The number is kept while the name is asked for; the question stays open
    for one turn so the bare name that answers it is taken as this lookup's answer. A name that does not
    match is told only that nothing was found -- never that the number has bookings under another name."""
    name = (name or "").strip()
    if not name:
        asks = int(pending.get("name_asks", 0))
        if asks >= LOOKUP_NAME_ASKS:
            session.pending_lookup = None
            await _speak(session, _with_resume(session, phrase("lookup_not_matched", lang), lang), lang)
            return
        session.pending_lookup = {"phone": phone, "name_asks": asks + 1}
        session.pending_enquiry = ("lookup_booking", session.turn_count)
        await _speak(session, missing_slot_prompt("lookup_booking", "patient_name", lang), lang)
        return
    session.pending_lookup = None
    try:
        result = await _tools.lookup_bookings(phone=phone, name=name)
    except ToolCallError as e:
        logger.error("[%s] clinic API call failed: %s", session.call_id, e)
        await _speak(session, phrase("tool_failure", lang), lang, fallback_reason="tool_failure")
        return
    bookings = result.get("bookings") or []
    logger.info("[%s] booking lookup by number and name: %d upcoming", session.call_id, len(bookings))
    if not bookings:
        await _speak(session, _with_resume(session, phrase("lookup_not_matched", lang), lang), lang)
        return
    session.pending_resend = bookings[0]["confirmation_id"]
    session.fetched_booking = bookings[0]
    await _speak(session, _with_resume(session, lookup_reply(bookings, lang), lang), lang)


async def _reopen_after_no(session: CallSession, st, lang: str) -> None:
    """The caller refused the confirmation and gave nothing to change: collection reopens, the details captured
    stay, and the first question is asked again (KCD-367)."""
    st.stage = "collecting"
    st.pending_charge_inr = None
    await _speak(
        session,
        missing_slot_prompt(st.action, "doctor_name" if st.action == "book_appointment" else "date", lang),
        lang,
    )


def _followup(session: CallSession, intent: str, slots: dict, text: str, lang: str):
    """The enquiry slots, with the topic of the last few turns filled in when the caller pointed at it or simply
    did not name a different one (agent/enquiry_followup.py). Deterministic; the model decides nothing here."""
    gap = session.turn_count - session.last_enquiry_turn if session.last_enquiry_entities else None
    return resolve_followup_slot(intent, slots, text, lang, session.last_enquiry_entities, gap)


# story: "Caller cannot give a contact number"
def _absorb_phone_digits(st, slots: dict) -> None:
    """Keep a number the caller is still reading out, instead of storing a fragment as THE number.

    The model reports whatever digits it heard this turn. A caller on someone else's phone often
    reads a number in groups, so "nine eight seven" used to be stored as the contact number and
    sail through missing_required into the readback. Now a fragment goes into the running buffer
    (agent/booking_flow.merge_digits) and the field stays unfilled, so _next_question asks for the
    rest with what they already gave kept. A whole number clears the buffer and is used as-is.

    Mutates `slots` in place, before merge_slots sees it."""
    for field in ("phone", "contact_phone"):
        value = slots.get(field)
        if not value:
            continue
        whole = normalise_phone(value)
        if whole:
            slots[field] = whole
            st.slots.pop("_phone_buffer", None)
            continue
        slots.pop(field)
        completed = merge_digits(st, value)
        if completed:
            slots[field] = completed


def _next_question(session: CallSession, st, intent: str, missing: list[str], lang: str) -> str:
    """KCD-103: the next question, one field or a group. The caller-state table (`questions_per_turn`) always wins
    over grouping, and a field already asked is asked alone (agent/slot_grouping.py)."""
    # A number arriving in pieces is continued, never restarted: asking "give me a phone number"
    # again after the caller has already read out half of it loses the half they gave.
    if missing and missing[0] == "phone":
        so_far = phone_digits_so_far(st)
        if so_far:
            st.asked_fields.add("phone")
            return phone_continue_prompt(so_far, lang)
    fields = slot_grouping.next_fields(st.action, missing, session.policy.questions_per_turn, st.asked_fields)
    grouped = slot_grouping.grouped_prompt(fields, lang) if len(fields) > 1 else None
    if grouped is None:
        fields = fields[:1]
    st.asked_fields.update(fields)
    return grouped or missing_slot_prompt(intent, fields[0], lang)


def _resume_tail(session: CallSession, reply: str, lang: str) -> str:
    """KCD-104: what brings the caller back to a booking left open while they asked something else -- the next
    question while details are still being collected, "shall I confirm it" at the confirmation step. Empty when
    there is no booking worth returning to, when the policy allows no question, or when the reply already asks one."""
    st = session.booking
    if not topic_flow.worth_suspending(st) or not topic_flow.resume_allowed(session.policy.questions_per_turn, reply):
        return ""
    if st.stage in ("confirming", "awaiting_charge_confirm"):
        return topic_flow.confirm_resume_line(lang)
    missing = missing_required(st)
    if not missing:
        return ""
    return topic_flow.resume_line(_next_question(session, st, st.action, missing, lang), lang)


def _with_resume(session: CallSession, reply: str, lang: str) -> str:
    tail = _resume_tail(session, reply, lang)
    return f"{reply} {tail}" if tail else reply


def _enter_task(session: CallSession, action: str):
    """The booking state for `action`. A DIFFERENT task started while one is half-done sets the old one aside
    instead of replacing it (KCD-104); it is offered back once the new task is finished."""
    current = session.booking
    if current is not None and current.action == action:
        return current
    if topic_flow.worth_suspending(current):
        session.suspended = current
    session.booking = new_state(action)
    return session.booking


async def _after_task_finished(session: CallSession, lang: str) -> None:
    """A task just ended (confirmed, refused or aborted). If another was set aside, offer -- never assume -- to
    go back to it."""
    if session.booking is not None or session.suspended is None:
        return
    if not topic_flow.worth_suspending(session.suspended):
        session.suspended = None
        return
    session.awaiting_resume = True
    await _speak(session, topic_flow.offer_resume(session.suspended.action, lang), lang)


async def _continue_resume_offer(session: CallSession, text: str, lang: str) -> bool:
    """The caller's answer to "shall I go back to it?". A yes restores the booking with everything it held; a
    no drops it; anything else lapses the offer and is handled as a fresh turn."""
    session.awaiting_resume = False
    st, session.suspended = session.suspended, None
    if st is None:
        return False
    answer = classify_yes_no(text, lang)
    if answer == "no":
        await _say_history(session, [history_text.ok_anything_else(lang)], lang)
        return True
    if answer != "yes":
        return False
    st.touch()
    session.booking = st
    if st.stage in ("confirming", "awaiting_charge_confirm"):
        await _speak(session, booking_confirmation_readback(st.readback_slots(), st.action, lang), lang)
        return True
    missing = missing_required(st)
    if missing:
        await _speak(session, _next_question(session, st, st.action, missing, lang), lang)
    else:
        mark_confirming(st)
        await _speak(session, booking_confirmation_readback(st.readback_slots(), st.action, lang), lang)
    return True


# "Cancel" in every script and spelling a caller here uses. "Remove" words count only toward a cancellation:
# Bengali "সরিয়ে দিন" is also "move it", so it may never veto the yes to a move.
_CANCEL_WORD = re.compile(r"বাতিল|ক্যান্?সেল|कैंसल|कैंसिल|कैन्सल|रद्द|(?<![a-z])cancel", re.IGNORECASE)
_REMOVE_WORD = re.compile(r"সরিয়ে|সরাতে|हटा|(?<![a-z])(?:remove|delete)", re.IGNORECASE)


def _cancel_request_answer(session: CallSession, st, text: str, answer: str | None) -> str | None:
    """A cancellation asked for in so many words, as the answer to "shall I ...?".

    To "shall I cancel it?" it IS the yes. Live 2026-10-07: "অ্যাপয়েন্টমেন্টটা বাতিল করে দিন দশ তারিখে" was neither
    a yes nor a no, became a fresh cancel turn on a cancellation already awaiting its answer, and nothing at all
    was said. Unless it names a day other than the booking's -- then it is a different booking, and asked about.
    To any OTHER readback (a new booking, a move) it is never a yes, whatever go-ahead words come with it."""
    said_text = unicodedata.normalize("NFC", text or "")
    if st.action != "cancel_appointment":
        return None if answer == "yes" and _CANCEL_WORD.search(said_text) else answer
    if not (_CANCEL_WORD.search(said_text) or _REMOVE_WORD.search(said_text)):
        return answer
    if answer == "no":
        return answer
    fetched = session.fetched_booking or {}
    booked = fetched.get("date") if fetched.get("confirmation_id") == st.slots.get("confirmation_id") else None
    said = spoken_dates(text)
    if said and booked and booked not in said:
        return None
    return "yes"


async def _handle_booking_confirmation_turn(session: CallSession, text: str, lang: str) -> bool | None:
    """The caller's answer to a "shall I confirm this?" readback --
    resolved deterministically (see the classify_yes_no call site in
    _dispatch_turn), never via the LLM.

    "no" means different things by action: for cancel_appointment there is
    nothing to correct, so "no" simply aborts it (KCD-372's spirit -- the
    caller was asked once with the charge stated, a "no" ends it there).
    For every other action, "no" re-opens collection instead of discarding
    the booking, because the far more common reason a caller says "no"
    here is "something's wrong", not "forget the whole thing" -- and
    KCD-367 explicitly asks for a correction to re-enter slot filling
    rather than restart. An unrecognised answer just repeats the question
    rather than guessing which the caller meant."""
    st = session.booking
    assert st is not None
    answer = _cancel_request_answer(session, st, text, classify_yes_no(text, lang))

    if answer is None:
        return False  # KCD-104: not a yes or a no -- the caller handles it as a fresh turn

    if answer == "no":
        _note(session, "corrections")
        if st.action == "cancel_appointment":
            await _speak(session, phrase("cancel_aborted", lang), lang)
            session.booking = None
            return
        if len(text.split()) > 2:
            # KCD-104: "no, make it eleven" is a correction, not just a refusal. Only a BARE no reopens the
            # booking by asking again; anything longer is handled as a fresh turn (the correction is applied and
            # read back; a question is answered; a bare no from the extractor's point of view reopens below).
            session.no_at_confirm = True
            return False
        await _reopen_after_no(session, st, lang)
        return

    # answer == "yes": commit for real.
    try:
        if st.action == "book_appointment":
            phone = effective_phone(st)
            result = await _tools.confirm_booking(
                st.hold_token,
                st.hold_doctor_id,
                st.slots["date"],
                st.slots["time_slot"],
                st.slots["patient_name"],
                phone,
                caller_phone=phone,
                patient_age=st.slots.get("patient_age"),
                relationship=st.slots.get("relationship") or "self",
            )
            if result.get("reason") == "write_unverified":
                session.booking = None
                await _handle_unverified_write(session, lang)
                return
            await _speak(session, booking_reply(st.slots, result, lang), lang)
            if result.get("success"):
                _record_action(session, "booking_confirmed", result, st.slots)
                await _persist_senior(session, phone)
                session.booking = None
                # Only a DOCTOR appointment is paid and booked over the call at all; having reached here, this one
                # just was. The agent calls the clinic API to send a payment link, then ends the call -- it never
                # takes or states a payment itself (CLAUDE.md's truth boundary keeps money out of its hands the same
                # way it keeps every other fact out of the model's). A failure sending the link is logged, never
                # spoken over the confirmation the caller already heard: the appointment IS still confirmed either
                # way, and the closing line says the link was sent regardless -- if it genuinely failed to queue,
                # that is a clinic-api-side follow-up, not something the caller can act on by being told mid-call.
                confirmation_id = result.get("confirmation_id")
                if confirmation_id:
                    with contextlib.suppress(ToolCallError):
                        await _tools.send_payment_link(confirmation_id)
                session.suspended = None  # nothing is offered back after the socket closes below
                await _end_call(session, lang, "payment_link_sent")
                return
            if result.get("reason") != "hold_expired":
                session.booking = None
            else:
                st.hold_token, st.stage = None, "collecting"  # let a retry re-hold

        elif st.action == "book_test":
            phone = effective_phone(st)
            result = await _tools.book_tests(
                st.test_names,
                st.slots["date"],
                st.slots["patient_name"],
                phone,
                caller_phone=phone,
                patient_age=st.slots.get("patient_age"),
                relationship=st.slots.get("relationship") or "self",
            )
            await _speak(session, multi_test_reply(result, lang), lang)
            _record_action(session, "tests_booked", result, st.slots)
            session.booking = None

        elif st.action == "reschedule_appointment":
            result = await _tools.reschedule_appointment(
                st.slots["confirmation_id"], st.slots["new_date"], st.slots["new_time_slot"]
            )
            if result.get("reason") == "write_unverified":
                session.booking = None
                await _handle_unverified_write(session, lang)
                return
            _record_action(session, "appointment_rescheduled", result, st.slots)
            reason = result.get("reason")
            if result.get("success") or reason not in _MOVE_RETRY_FIELDS:
                await _speak(session, reschedule_reply(result, lang), lang)
                session.booking = None
                if result.get("success"):
                    session.fetched_booking = None
            else:
                # The clinic refused the new day or time and kept the old appointment exactly as it was
                # (booking_service.reschedule_appointment holds the new slot before releasing the old one).
                # The request stays open: the refused field is asked again, never the whole move.
                st.stage = "collecting"
                for field_name in _MOVE_RETRY_FIELDS[reason]:
                    st.slots.pop(field_name, None)
                ask = missing_slot_prompt("reschedule_appointment", _MOVE_RETRY_FIELDS[reason][0], lang)
                # These replies already end in their own question (the free times, or the range to pick from).
                asks_itself = reason in ("slot_taken", "invalid_slot", "slot_passed", "sitting_over")
                await _speak(session, reschedule_reply(result, lang) if asks_itself else f"{reschedule_reply(result, lang)} {ask}", lang)

        elif st.action == "cancel_appointment":
            confirm_charge = st.stage == "awaiting_charge_confirm"
            result = await _tools.cancel_appointment(st.slots["confirmation_id"], confirm_charge)
            if not result.get("success") and result.get("reason") == "charge_confirmation_required":
                mark_awaiting_charge_confirm(st, result["charge_inr"])
                await _speak(session, cancel_reply(result, lang), lang)
                return
            await _speak(session, cancel_reply(result, lang), lang)
            _record_action(session, "appointment_cancelled", result, st.slots)
            session.booking = None
            if result.get("success"):
                # The booking is gone: nothing may be offered back about it, and it is not "the" booking any more.
                # Live 2026-10-07: a move of this same booking, set aside when the caller switched to cancelling, was
                # offered back ("shall we go back to changing the time?") right after the cancellation.
                cid = st.slots.get("confirmation_id")
                if session.suspended is not None and session.suspended.slots.get("confirmation_id") == cid:
                    session.suspended = None
                if (session.fetched_booking or {}).get("confirmation_id") == cid:
                    session.fetched_booking = None

        elif st.action == "add_test_booking":
            result = await _tools.add_test_to_booking(st.slots["confirmation_id"], st.slots["test_name"])
            await _speak(session, add_test_reply(result, lang), lang)
            _record_action(session, "test_added", result, st.slots)
            session.booking = None

    except ToolCallError as e:
        logger.error("[%s] clinic API call failed during booking confirmation: %s", session.call_id, e)
        await _speak(session, phrase("tool_failure", lang), lang, fallback_reason="tool_failure")
        session.booking = None


async def _resync_after_playback(session: CallSession) -> bool:
    """Drop everything captured while the agent was talking, by moving
    processed_until_s to the current end of the decoded buffer. That region
    is muted silence from the client's side; skipping it keeps the turn
    detector from ever analysing it, and -- more importantly -- keeps
    processed_until_s anchored to real time instead of drifting a full
    reply behind, which is what made later turns surface late."""
    if not await _decode_to_wav(session.raw_path, session.wav_path):
        return False
    wav, sr = await asyncio.to_thread(torchaudio.load, session.wav_path)
    buffer_end_s = wav.shape[-1] / sr
    session.processed_until_s = max(session.processed_until_s, buffer_end_s - RESYNC_REWIND_S)
    session.resync_pending = False
    logger.info("[%s] resynced to %.2fs after playback", session.call_id, session.processed_until_s)
    return True


def _next_wake_delay(decision) -> float:
    """KCD-049: sleep until the moment the answer could change, not until the
    next tick of a fixed timer. While the caller has spoken this turn the cadence
    is ACTIVE_POLL_INTERVAL_S; when the detector says "commit in x seconds if
    silence continues", wake exactly then (plus a hair, so the audio that
    settles it has landed)."""
    base = ACTIVE_POLL_INTERVAL_S if decision.had_any_speech else POLL_INTERVAL_S
    if decision.commit_after_s is None:
        return base
    return min(base, max(MIN_WAKE_S, decision.commit_after_s + WAKE_SLACK_S))


async def _speculative_completeness(session: CallSession, speech_end_abs: float) -> str | None:
    """KCD-048: transcribe what has been said so far, in the call's current
    language only (no LID, no router state touched), and say whether it reads as
    finished. Any failure is simply "no verdict" -- the baseline threshold then
    applies, so this can only ever shorten a wait it is confident about."""
    session.spec_count += 1
    wav = None
    try:
        session.utt_seq += 1
        wav = await _slice_utterance(session, session.processed_until_s, speech_end_abs, session.utt_seq)
        asr = await asyncio.wait_for(_asr_router.transcribe(session.lang, wav), SPECULATIVE_ASR_TIMEOUT_S)
        # an emergency phrase in what has been said so far ends the wait at once: the turn is treated as
        # finished so the notice is not delayed by a silence timer (agent/emergency.py)
        verdict = "complete" if detect_emergency(asr.text) else classify_completeness(asr.text, session.lang)
    except Exception as e:  # noqa: BLE001 - speculation is advisory
        logger.info("[%s] speculative transcription unavailable: %s", session.call_id, e)
        return None
    finally:
        if wav:
            with contextlib.suppress(OSError):
                os.remove(wav)
    session.spec_end_abs, session.spec_verdict = speech_end_abs, verdict
    return verdict


def _maybe_start_early_lid(session: CallSession, tail, sr: int, spans) -> None:
    """Start identifying the caller's language on their first words, while they are still talking
    (agent/early_lid.py; upgrade prompt section 6.1). Once per turn, keyed by where the turn began on the call
    timeline. A no-op for a Bengali-only build, with EARLY_LID=off, or before there is enough voiced speech."""
    if _lid is None or len(_languages_active) < 2 or not early_lid.enabled():
        return
    if session.early_lid is not None and session.early_lid.matches(session.processed_until_s):
        return
    window = early_lid.early_window(spans)
    if window is None:
        return
    clip = tail[int(window[0] * sr) : int(window[1] * sr)].clone()
    model = _lid

    def identify():
        wav = clip if sr == 16000 else torchaudio.functional.resample(clip, sr, 16000)
        return model.identify(wav)

    session.early_lid = early_lid.start(session.processed_until_s, identify)


async def _decide_turn(session: CallSession, tail, sr: int):
    """Where is the speech (Silero), then has the caller finished
    (agent/endpointing.decide, pure and tested off-pod). With SEMANTIC_ENDPOINTING
    a candidate turn end may trigger one speculative transcription, whose
    completeness verdict is reused for as long as the speech end has not moved."""
    spans, duration_s = await asyncio.to_thread(_turn_detector.spans, tail, sr)
    _maybe_start_early_lid(session, tail, sr, spans)
    cfg = session.pause.config(_turn_detector.config) if ADAPTIVE_PAUSE != "off" else _turn_detector.config
    if spans and not session.commit_checked and session.last_commit_end_abs is not None:
        # speech resumed after the last turn was cut: was that cut too early for THIS caller?
        resumed_after = max(0.0, float(spans[0]["start"]) - session.last_confirm_s)
        session.pause.note_turn_cut(resumed_after, session.agent_spoke_since_commit)
        session.commit_checked = True
    speech_end_abs = session.processed_until_s + float(spans[-1]["end"]) if spans else None
    completeness = None
    if (
        SEMANTIC_ENDPOINTING
        and speech_end_abs is not None
        and session.spec_end_abs is not None
        and abs(session.spec_end_abs - speech_end_abs) < SPECULATION_MATCH_TOLERANCE_S
    ):
        completeness = session.spec_verdict
    decision = decide_turn_end(spans, duration_s, cfg, completeness=completeness, semantic=SEMANTIC_ENDPOINTING)
    if decision.speculate and speech_end_abs is not None and session.spec_count < MAX_SPECULATIONS_PER_TURN:
        verdict = await _speculative_completeness(session, speech_end_abs)
        if verdict is not None:
            decision = decide_turn_end(spans, duration_s, cfg, completeness=verdict, semantic=True)
    if decision.utterance_end_s is not None:
        session.pause.observe_utterance(spans, upto_s=decision.utterance_end_s)
        session.last_confirm_s = decision.confirm_s or cfg.silence_confirm_s
        session.last_commit_end_abs = session.processed_until_s + float(decision.utterance_end_s)
        session.agent_spoke_since_commit = False
        session.commit_checked = False
    return decision


async def _interrupt_playback(session: CallSession, reason: str, resume_from_s: float | None = None) -> None:
    """The caller has taken the floor -- by clicking (manual) or by talking over
    the agent (acoustic barge-in). Stop what is playing on the client, discard
    what the interrupted turn has not yet said, open the gate.

    `resume_from_s`, when given, is where the caller's utterance begins on the
    call timeline: the marker is set there and the usual post-playback resync
    (which would skip past their first words) is cancelled."""
    was_speaking = session.agent_speaking
    if was_speaking:
        _note(session, "barge_ins")  # the caller talked over the agent
    session.speak_epoch += 1
    session.release_gate()
    if session.duplex is not None:
        session.duplex.cancel_pending()
    if reason != "manual":
        # A manual interrupt comes FROM the client, which has already stopped
        # its own playback; only a server-detected one needs to tell it to.
        with contextlib.suppress(Exception):
            await session.ws.send_text(json.dumps({"type": "stop_playback"}))
    if resume_from_s is not None:
        session.processed_until_s = max(session.processed_until_s, resume_from_s)
        session.resync_pending = False
    if was_speaking:
        logger.info("[%s] caller interrupt (%s) -- playback stopped, gate released", session.call_id, reason)
        barge_in_interrupts.record(reason, session.lang)


def _mark_verified(session: CallSession, method: str, patient_ref: str, analysis: dict | None) -> None:
    """Record that a check passed (an OTP, a confirmed date of birth -- whatever the clinic
    decides counts) and remember THIS voice as the verified one (KCD-054). The mechanism that
    verifies lives outside this file (KCD-203); it calls this when it succeeds."""
    session.identity.verify(method, patient_ref)
    session.speaker.reset()
    emb = (analysis or {}).get("voice")
    if emb is not None:
        level = ((analysis or {}).get("near_end") or (None,))[0]
        session.speaker.enroll_embedding(emb, level)


async def _check_speaker_change(session: CallSession, analysis: dict | None, lang: str) -> None:
    if not session.identity.is_verified or not session.speaker.enrolled or not analysis:
        return
    emb = analysis.get("voice")
    if emb is None:
        return  # too little voiced speech: abstain, never guess
    level = (analysis.get("near_end") or (None,))[0]
    result = session.speaker.observe_embedding(emb, level)
    if result.verdict == "changed" and session.identity.revoke("speaker_changed"):
        logger.warning("[%s] voice changed after verification -- verification revoked", session.call_id)
        audio_issue_buckets.record("speaker_change", "verification_revoked", lang)
        await _speak(session, phrase("reverify_notice", lang), lang)


async def _say_history(session: CallSession, statements: list[tuple[str, str]], lang: str, warm: bool = False) -> None:
    """Speak history statements and record their provenance ids (KCD-500/501). `warm` marks a real answer
    (not a question): it gets the thanks, and a senior's kind closing (KCD-512/513)."""
    _rec(session, "history", [sid for sid, _ in statements])
    text = " ".join(t for _, t in statements)
    if warm:
        text = session.kindness.decorate(text, lang)
    await _speak(session, text, lang)


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _caller_phone(session: CallSession) -> str:
    return session.identity.phone or "not_provided"


async def _history_step(session: CallSession, lang: str) -> None:
    """Where the history question stands: find the record, verify who is asking, answer."""
    ident = session.identity
    if ident.patient_ref is None:
        session.history_state = "need_phone"
        await _say_history(session, [history_text.ask_phone(lang)], lang)
        return
    decision = patient_policy.history_gate(ident, ident.patient_ref, lang)
    if decision.allowed:
        await _answer_history(session, lang)
        return
    if SECURITY_QUESTIONS != "off":
        await _start_security_check(session, ident.patient_ref, lang)
        return
    # NOTHING beyond the existence of a record is spoken before verification. The refusal offers a
    # person; the caller's yes/no is handled at the top of the next turn.
    session.history_state, session.pending_history, session.awaiting_handoff_offer = None, None, True
    await _say_history(session, [decision.statement], lang)


async def _start_security_check(session: CallSession, patient_ref: str | None, lang: str) -> None:
    """Begin the security questions (KCD-495). With no record found yet (`patient_ref` None) the same
    answers first LOCATE the record (KCD-497), then verify it."""
    session.security = SecurityCheck(patient_ref)
    session.history_state = "security"
    text = session.security.next_question(lang)
    if patient_ref is None:
        text = f"{history_text.no_record(lang)[1]} {text}"  # the operator's "I cannot find it" wording first
    await _say_history(session, [("security_question", text)], lang)


async def _continue_security_flow(session: CallSession, text: str, lang: str, analysis: dict | None) -> bool:
    """One caller answer to a security question. True if this turn belonged to the check."""
    chk = session.security
    if chk is None or chk.finished:
        return False
    if not chk.absorb(text):
        if chk.gave_up_reading:
            session.security, session.history_state = None, None
            await _handoff_to_human(session, "verification_unclear", (lang,))
            return True
        await _say_history(
            session,
            [("security_not_understood", f"{sec_text.did_not_understand(lang)} {chk.next_question(lang)}")],
            lang,
        )
        return True
    await _thank(session, lang)
    if not chk.ready_to_submit():
        await _say_history(session, [("security_question", chk.next_question(lang))], lang)
        return True
    try:
        if chk.finding:
            found = (
                await _tools.find_patient(session.call_id, chk.find_payload())
                if chk.find_payload()
                else {"status": "insufficient"}
            )
            if found.get("status") == "single":
                chk.patient_ref = str(found["patient_ref"])
                session.identity.claim(chk.patient_ref, session.identity.phone)
            elif found.get("status") == "insufficient":
                await _say_history(session, [("security_question", chk.next_question(lang))], lang)
                return True
            else:
                # none or ambiguous: never say which. The operator's "I cannot find that" wording, then ask again.
                stop = chk.record_find_miss()
                if stop:
                    session.security, session.history_state = None, None
                    await _say_history(session, [history_text.cannot_see(lang)], lang)
                    await _handoff_to_human(session, "history_record_not_found", (lang,))
                    return True
                await _say_history(
                    session, [history_text.cannot_see(lang), ("security_question", chk.next_question(lang))], lang
                )
                return True
        result = await _tools.verify_patient(
            session.call_id, int(chk.patient_ref), chk.payload(), session.identity.phone
        )
    except ToolCallError as e:
        logger.error("[%s] verification failed to run: %s", session.call_id, e)
        session.security, session.history_state = None, None
        await _speak(session, phrase("tool_failure", lang), lang, fallback_reason="tool_failure")
        return True
    chk.record_result(result)
    if chk.verified:
        await _on_verified(session, chk, analysis, lang)
        return True
    if chk.finished:
        session.security, session.history_state = None, None
        await _say_history(session, [("security_failed", sec_text.failed_text(lang))], lang)
        await _handoff_to_human(session, "verification_failed", (lang,))
        return True
    await _say_history(
        session, [("security_not_matched", f"{sec_text.not_matched(lang)} {chk.next_question(lang)}")], lang
    )
    return True


async def _on_verified(session: CallSession, chk: SecurityCheck, analysis: dict | None, lang: str) -> None:
    """The server said yes (KCD-495): remember this voice, switch to senior care if the registered date
    of birth says so (KCD-512), offer to continue anything unfinished (KCD-496), then answer."""
    ref = chk.patient_ref
    _mark_verified(session, "security_questions", str(ref), analysis)
    _rec(session, "patient", int(ref))
    session.security, session.history_state = None, None
    parts = [("security_verified", sec_text.verified_text(lang))]
    if session.kindness.activate(chk.age_years):
        apply_caller_state(session.call_state, senior=True)  # slower, one question at a time (Appendix C)
        opening = session.kindness.opening(lang)
        if opening:
            parts.append(("senior_opening", opening))
    await _say_history(session, parts, lang)
    await _offer_unfinished(session, lang)
    if session.pending_history is not None:
        await _answer_history(session, lang)
    # story: "Caller asks what they have booked" -- the question that sent the caller into the
    # security questions is answered now, rather than making them ask it a second time.
    if session.pending_lookup is not None:
        slots, session.pending_lookup = session.pending_lookup, None
        await _answer_booking_lookup(session, slots, lang, verified=True)
    if not session.awaiting_draft_answer:
        await _offer_preferences(session, lang)


def _draft_offer_text(draft: dict, lang: str) -> str:
    what = {
        "book_appointment": {"bn": "একটা অ্যাপয়েন্টমেন্ট", "hi": "एक अपॉइंटमेंट", "en": "an appointment"},
        "book_test": {"bn": "একটা টেস্ট", "hi": "एक टेस्ट", "en": "a test"},
        "reschedule_appointment": {
            "bn": "অ্যাপয়েন্টমেন্টের সময় বদল",
            "hi": "अपॉइंटमेंट का समय बदलना",
            "en": "changing an appointment",
        },
        "cancel_appointment": {"bn": "অ্যাপয়েন্টমেন্ট বাতিল", "hi": "अपॉइंटमेंट रद्द करना", "en": "cancelling an appointment"},
        "add_test_booking": {"bn": "একটা টেস্ট যোগ করা", "hi": "एक टेस्ट जोड़ना", "en": "adding a test"},
    }
    label = (what.get(draft.get("action")) or what["book_appointment"]).get(lang) or ""
    return {
        "bn": f"আগের বার আপনি {label} করছিলেন, কাজটা শেষ হয়নি। সেটা কি চালিয়ে যাব, নাকি নতুন করে শুরু করব?",
        "hi": f"पिछली बार आप {label} कर रहे थे, वह पूरा नहीं हुआ। क्या उसे जारी रखूँ, या नए सिरे से शुरू करूँ?",
        "en": f"Last time you were doing {label}, and it was not finished. Shall I continue it, or start fresh?",
    }.get(lang, "")


async def _offer_unfinished(session: CallSession, lang: str) -> None:
    """KCD-496: after verification, offer -- never assume -- to continue an unfinished booking."""
    ident = session.identity
    if session.booking is not None or _tools is None:
        return
    try:
        ctx = await _tools.continuity(_caller_phone(session), session.call_id, int(ident.patient_ref))
    except ToolCallError:
        return
    draft = ctx.get("draft")
    if not draft:
        return
    try:
        info = json.loads(draft.get("slots_json") or "{}")
    except ValueError:
        return
    if not info.get("action"):
        return
    session.pending_draft, session.awaiting_draft_answer = info, True
    await _say_history(session, [("continuity_offer", _draft_offer_text(info, lang))], lang)


async def _offer_preferences(session: CallSession, lang: str) -> None:
    """KCD-499: the patient's stored preferences are OFFERED for confirmation (the address is never read
    aloud), and a stored language bias never overrides what the caller actually says."""
    ident = session.identity
    if _tools is None or ident.patient_ref is None:
        return
    try:
        prefs = await _tools.get_preferences(int(ident.patient_ref), _caller_phone(session))
    except ToolCallError:
        return
    offer = patient_policy.preference_offer(prefs.get("preferences") or {}, ident, ident.patient_ref, lang)
    if offer is None:
        return
    session.pending_pref, session.awaiting_pref_answer = offer, True
    await _speak(session, offer.text, lang)


async def _continue_preference_offer(session: CallSession, text: str, lang: str) -> bool:
    if not session.awaiting_pref_answer or session.pending_pref is None:
        return False
    session.awaiting_pref_answer = False
    offer, session.pending_pref = session.pending_pref, None
    answer = classify_yes_no(text, lang)
    if answer == "yes":
        session.confirmed_preferences = dict(offer.fields)
        _rec(session, "confirmed", dict(offer.fields))
        if offer.fields.get("accessibility_mode") == "slower":
            apply_caller_state(session.call_state, senior=True)  # the slower, one-question-at-a-time delivery
        await _speak(session, history_text.ok_anything_else(lang)[1], lang)
        return True
    if answer == "no":
        await _speak(session, history_text.ok_anything_else(lang)[1], lang)
        return True
    return False


async def _continue_draft_offer(session: CallSession, text: str, lang: str) -> bool:
    """The caller's answer to "continue it, or start fresh?"."""
    if not session.awaiting_draft_answer or session.pending_draft is None:
        return False
    session.awaiting_draft_answer = False
    info, session.pending_draft = session.pending_draft, None
    answer = classify_yes_no(text, lang)
    if answer == "yes":
        st = new_state(info["action"])
        merge_slots(st, info.get("slots") or {})
        for t in info.get("test_names") or []:
            if t not in st.test_names:
                st.test_names.append(t)
        session.booking = st
        missing = missing_required(st)
        if missing:
            await _speak(session, missing_slot_prompt(info["action"], missing[0], lang), lang)
        else:
            await _speak(session, phrase("unclear", lang), lang)
        return True
    if answer == "no":
        await _say_history(session, [history_text.ok_anything_else(lang)], lang)
        return True
    return False


async def _answer_history(session: CallSession, lang: str) -> None:
    hq, ident = session.pending_history, session.identity
    now = datetime.datetime.now()
    phone = _caller_phone(session)
    try:
        timeline = await _tools.patient_timeline(int(ident.patient_ref), phone, session.call_id)
        if hq.kind == "last_test":
            catalogue = _fast_path.catalogue if _fast_path is not None else None
            name, _form, score = (
                catalogue.match(hq.test_hint or "", "test", lang, HISTORY_TEST_MATCH_MIN)
                if catalogue
                else (None, None, 0.0)
            )
            if not name or score < HISTORY_TEST_MATCH_MIN:
                session.history_state = "need_test"
                await _say_history(session, [history_text.ask_which_test(lang)], lang)
                return
            status = await _tools.patient_test_status(int(ident.patient_ref), name, phone, session.call_id)
            answer = patient_policy.answer_last_test(
                timeline, name, status if status.get("success") else None, lang, now
            )
        elif hq.kind == "appointments":
            answer = patient_policy.answer_appointments(timeline, lang, now)
        elif hq.kind == "medicines":
            answer = patient_policy.answer_medicines(timeline, lang, now)
        else:
            answer = patient_policy.answer_recent_tests(timeline, lang, now)
    except ToolCallError as e:
        logger.error("[%s] history lookup failed: %s", session.call_id, e)
        session.history_state, session.pending_history = None, None
        await _speak(session, phrase("tool_failure", lang), lang, fallback_reason="tool_failure")
        return
    session.history_state, session.pending_history = None, None
    await _say_history(session, answer.statements, lang, warm=True)


async def _continue_history_flow(session: CallSession, text: str, lang: str) -> bool:
    """True if this turn belonged to the history flow (and has been handled)."""
    if session.awaiting_handoff_offer:
        session.awaiting_handoff_offer = False
        answer = classify_yes_no(text, lang)
        if answer == "yes":
            await _handoff_to_human(session, "history_requested_staff", (lang,))
            return True
        if answer == "no":
            await _say_history(session, [history_text.ok_anything_else(lang)], lang)
            return True
        return False  # neither: the offer lapses and the turn is handled normally
    state = session.history_state
    if state == "need_test":
        session.pending_history.test_hint, session.history_state = text, None
        await _history_step(session, lang)
        return True
    if state not in ("need_phone", "need_name"):
        return False
    try:
        data = await _resolve_intent(session, text, lang)
    except ExtractionError:
        data = {"slots": {}}
    slots = data.get("slots") or {}
    session.history_attempts += 1
    try:
        if state == "need_phone":
            phone = _digits(slots.get("phone") or slots.get("contact_phone"))
            if len(phone) < 10:
                if session.history_attempts >= 2:
                    session.history_state = None
                    await _handoff_to_human(session, "history_phone_unclear", (lang,))
                else:
                    await _say_history(session, [history_text.ask_phone(lang)], lang)
                return True
            await _thank(session, lang)
            result = await _tools.identify_patient(phone)
            outcome = patient_policy.identify_outcome(result)
            session.identity.phone = session.identity.phone or phone
            if outcome == "new":
                # nobody on this number: the caller's own details can locate the record (KCD-497)
                if SECURITY_QUESTIONS != "off":
                    await _start_security_check(session, None, lang)
                else:
                    session.history_state, session.pending_history = None, None
                    await _say_history(session, [history_text.no_record(lang)], lang)
            elif outcome == "single":
                session.identity.claim(str(result["patient_ref"]), phone)
                _rec(session, "patient", result["patient_ref"])
                session.history_state = None
                await _history_step(session, lang)
            else:
                session.history_state, session.history_attempts = "need_name", 0
                await _speak(session, patient_policy.resolve_question(lang, 0), lang)
            return True
        # state == "need_name": an open question, answered with a NAME (never chosen from a list)
        spoken = slots.get("patient_name") or text
        result = await _tools.resolve_patient(session.identity.phone or "", spoken, slots.get("patient_age"))
        if result.get("status") == "single" and result.get("basis") == "exact":
            session.identity.claim(str(result["patient_ref"]), session.identity.phone)
            _rec(session, "patient", result["patient_ref"])
            session.history_state = None
            await _history_step(session, lang)
        elif session.history_attempts >= 2:
            session.history_state, session.pending_history = None, None
            await _handoff_to_human(session, "history_patient_unclear", (lang,))
        else:
            # none, ambiguous, or found only by SOUND: ask again, never assume
            await _speak(session, patient_policy.resolve_question(lang, 1), lang)
        return True
    except ToolCallError as e:
        logger.error("[%s] patient lookup failed: %s", session.call_id, e)
        session.history_state, session.pending_history = None, None
        await _speak(session, phrase("tool_failure", lang), lang, fallback_reason="tool_failure")
        return True


async def _save_unfinished_draft(session: CallSession) -> None:
    """KCD-496: a booking that was being collected when the call ended is saved, so the next call can
    offer to continue it. Bounded, never raises."""
    st = session.booking
    if st is None or st.stage != "collecting" or not (st.slots or st.test_names) or _tools is None:
        return
    phone = st.slots.get("phone") or st.slots.get("contact_phone") or session.identity.phone
    if not phone:
        return
    try:
        body = json.dumps({"action": st.action, "slots": st.slots, "test_names": st.test_names})
        await asyncio.wait_for(_tools.save_draft_booking(phone, session.call_id, body), 3.0)
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] could not save the unfinished booking: %s", session.call_id, e)


def _caller_thanked(text: str, lang: str) -> bool:
    from agent import fast_path_cues as _cues

    table = _cues.table_for(lang)
    return bool(table and table.any(_cues.normalise_cue(text), table.thanks))


def _canned_reply_in_call_language(borrowed, lang: str, other: str):
    """"thank you" said on a Bengali call: the fast path's English table has a canned reply for it, in English. The
    call speaks `lang`, so the same KIND of reply is taken from `lang`'s own table (thanks -> its thanks, greeting ->
    its greeting); anything else that carries a canned reply is not borrowed (None -> the usual path)."""
    from agent import fast_path_cues as cues
    from agent.fast_path import FastPathResult

    own, theirs = cues.table_for(lang), cues.table_for(other)
    if own is None or theirs is None:
        return None
    said = borrowed.direct_reply_bn
    reply = (
        own.thanks_reply if said == theirs.thanks_reply and own.thanks_reply
        else own.greeting_reply if said == theirs.greeting_reply and own.greeting_reply
        else None
    )
    if reply is None:
        return None
    return FastPathResult(borrowed.intent, borrowed.slots, borrowed.confidence, borrowed.matched_form, reply)


def _resolve_reply_language(
    session: CallSession, detected_lang: str, text: str | None = None, agreement: float | None = None
) -> str:
    """The language to REPLY in this turn (upgrade prompt sections 6.3 / 6.5; docs/adr/0002).

    `detected_lang` is what THIS turn's LID/ASR-verification settled on (it has already selected the ASR engine).
    By default the reply follows it: the template set and the TTS voice are the ones for the language the caller
    just spoke -- agent/language_policy.resolve_reply_language, the module that states the rule ("never a
    call-level constant"). Nothing is cached for the call except `session.lang`, the language of the last reply,
    which is where an unidentifiable turn falls back to.

    Why one borrowed word does not flip the call (the live-call complaint of 2026-09-28 that made this sticky):
    recognition itself only leaves the call's language when language ID believes the new one
    (agent/lang_select.pick_candidate, SWITCH_MIN_LID), the disclosure notice is spoken once per language per
    call (session.disclosed_langs), and a turn that could not be read keeps the call where it was
    (_keep_language). An explicit request ("speak in Hindi") sets the language for the turn it is made in and
    biases the next turn's prior (_lock_reply_language).

    REPLY_LANGUAGE_MODE=sticky (the default, docs/adr/0003): the language of the caller's first REAL sentence is the
    language of the call and only an explicit request moves it. `text` is what was recognised this turn: until a turn
    establishes the language (agent.language_policy.establishes_call_language -- not "hello", not a yes, not one
    word) the reply follows the utterance as in per_utterance mode and nothing is fixed; `text=None` (unknown)
    fixes it at once. A detected different voice after verification revokes verification
    (agent/speaker_change.py) but does not, in either mode, move the language on its own."""
    if REPLY_LANGUAGE_MODE == "sticky":
        if not session.lang_locked:
            if agreement is not None and agreement < CONVINCING_MIN_AGREEMENT:
                # the recogniser's own two decoders disagree: this turn is not trusted to FIX the call's language. It is
                # still answered in the language it was written in (a Hindi first sentence with one odd word is not
                # answered in Bengali -- live 2026-10-14), and the next convincing sentence fixes it.
                session.lang = resolve_reply_language(detected_lang, fallback=session.lang)
            elif text is None or establishes_call_language(text):
                _lock_reply_language(session, detected_lang)
            else:
                session.lang = resolve_reply_language(detected_lang, fallback=session.lang)
        lang = session.lang
    else:
        lang = resolve_reply_language(detected_lang, fallback=session.lang)
        session.lang = lang
    # The recogniser's prior is the language the caller SPEAKS, not the language the agent replies in: once the call is
    # fixed to its first sentence's language the two differ, and pinning the prior to the reply language would keep a
    # Hindi or English turn on the Bengali recogniser unless language ID were 0.97 sure (agent/lang_select.SWITCH_MIN_LID).
    session.lang_router.note_response_language(detected_lang if detected_lang in ("bn", "hi", "en") else lang)
    apply_language(session.call_state, lang)
    return lang


def _lock_reply_language(session: CallSession, lang: str) -> None:
    """An explicit request for a language. In sticky mode it holds for the rest of the call; per utterance it holds
    for this turn's reply and seeds the next turn's prior (the caller asked for it, so a next turn that language ID
    cannot place leans toward it), after which what the caller actually speaks decides."""
    session.lang, session.lang_locked = lang, True


def _keep_language(session: CallSession, prior_lang: str) -> str:
    """A turn we could not read (empty, jumbled) says nothing about which language the caller speaks: the call stays in the
    language it was in, and is answered in it. Before this, a swear word or a mumble that language ID happened to label
    Hindi moved a Bengali call to Hindi for the re-ask."""
    session.lang = prior_lang
    # a turn that could not be read must not have FIXED the call's language either (it was fixed, if at all, by
    # _resolve_reply_language earlier in this same turn)
    session.lang_locked = getattr(session, "lang_locked_before_turn", session.lang_locked)
    session.lang_router.note_response_language(prior_lang)
    apply_language(session.call_state, prior_lang)
    return prior_lang


def _abuse_reply_language(text: str, lang: str, prior_lang: str) -> str:
    """The language to answer abuse in: the script it was written in (Bengali or Devanagari), else the call's own."""
    from agent.lang_select import script_share

    for candidate in ("bn", "hi"):
        if script_share(text, candidate) >= 0.5:
            return candidate
    return prior_lang if prior_lang in _languages_active else lang


async def _end_call(session: CallSession, lang: str, key: str) -> None:
    """Say the closing line, let it play, then close the connection."""
    session.turn_epoch = session.speak_epoch
    duration = await _speak(session, phrase(key, lang), lang)
    await asyncio.sleep(min(duration, 8.0) + 0.3)
    with contextlib.suppress(Exception):
        await session.ws.close()


async def _handle_silence(session: CallSession, quiet: bool) -> bool:
    """The caller has said nothing since the agent stopped speaking. After SILENCE_PROMPT_S ask once whether there is
    anything else (one question; the call ends if not); if still nothing SILENCE_CLOSE_S later, end the call.
    True when the call was ended. `quiet` is False whenever there is speech, or the agent is mid-turn."""
    if not quiet or session.dispatch_lock.locked():
        session.silence_since = None
        return False
    now = time.monotonic()
    if session.silence_since is None:
        session.silence_since = now
        return False
    waited = now - session.silence_since
    if session.open_question is not None:
        return await _silence_during_question(session, waited)
    if session.silence_prompts == 0 and waited >= SILENCE_PROMPT_S:
        session.silence_prompts = 1
        _note(session, "silence_prompts")
        session.silence_since = None
        session.awaiting_close_answer = True
        session.turn_epoch = session.speak_epoch
        logger.info("[%s] %.1fs of silence -- asking whether there is anything else", session.call_id, waited)
        await _speak(session, phrase("silence_prompt", session.lang), session.lang)
        # "Anything else?" is a closing question, not an open task: its own silence ends the call
        # (SILENCE_CLOSE_S below) rather than being re-asked as if it were "which doctor?".
        session.open_question = None
        return False
    if session.silence_prompts >= 1 and waited >= SILENCE_CLOSE_S:
        logger.info("[%s] still silent after the prompt -- ending the call", session.call_id)
        session.outcome = "silence_timeout"
        _note(session, "silence_timeout")
        await _end_call(session, session.lang, "idle_close")
        return True
    return False


async def _silence_during_question(session: CallSession, waited: float) -> bool:
    """Silence while one of OUR questions is open. Re-ask that same question, gently and differently each
    time, then close -- saying plainly what was not done. True when the call was ended."""
    if waited < SILENCE_REASK_S + getattr(session, "open_question_extra_s", 0.0):
        return False
    text, lang = session.open_question
    session.silence_since = None
    session.turn_epoch = session.speak_epoch
    if session.question_silence_prompts < QUESTION_SILENCE_PROMPTS:
        session.question_silence_prompts += 1
        _note(session, "silence_prompts")
        lead = "silence_still_there" if session.question_silence_prompts == 1 else "silence_take_your_time"
        logger.info("[%s] %.1fs of silence on an open question -- re-asking (%d)",
                    session.call_id, waited, session.question_silence_prompts)
        await _speak(session, f"{phrase(lead, lang)} {text}", lang)
        # The re-ask is the same question, not a new one: keep it as THE open question, so the next
        # prompt does not stack "are you there?" on top of "are you there?".
        session.open_question = (text, lang)
        return False
    logger.info("[%s] still silent after %d re-asks -- ending the call", session.call_id, QUESTION_SILENCE_PROMPTS)
    session.outcome = "silence_timeout"
    _note(session, "silence_timeout")
    # Only a booking that is open and unconfirmed can be named as "not booked"; anything else gets the
    # general sentence, which is true whatever was open.
    booking = session.booking
    unbooked = booking is not None and booking.stage != "done" and booking.action == "book_appointment"
    await _speak(session, phrase("silence_close_unbooked" if unbooked else "silence_close_unfinished", lang), lang)
    await _end_call(session, lang, "idle_close")
    return True


async def _handle_barge_in(session: CallSession, event) -> None:
    sr = session.audio.sample_rate if hasattr(session, "audio") else 16000
    await _interrupt_playback(session, "acoustic", resume_from_s=max(0.0, event.at_sample / sr - BARGE_IN_PREROLL_S))


async def _turn_poll_loop(session: CallSession):
    """Runs for the lifetime of the call. Every POLL_INTERVAL_S, re-decodes
    the growing buffer -- ALWAYS from byte 0, since that's the only way
    the WebM container stays valid -- then asks the turn detector "is the
    caller done talking yet?" using only the slice of audio past
    session.processed_until_s (a prior turn's already-consumed audio).
    On yes: slice that utterance out for ASR, hand it to _dispatch_turn as
    a background task (so ingestion of the NEXT turn's audio is never
    blocked by this turn's ASR/LLM/TTS work), and advance the marker.
    """
    while True:
        await asyncio.sleep(session.take_wake_delay())

        if time.time() - session.last_activity > IDLE_TIMEOUT_S:
            logger.info("[%s] idle timeout, closing", session.call_id)
            session.turn_epoch = session.speak_epoch
            await _speak(session, phrase("idle_close", session.lang), session.lang)
            with contextlib.suppress(Exception):
                await session.ws.close()
            return

        if time.time() - session.last_heartbeat > HEARTBEAT_INTERVAL_S:
            session.last_heartbeat = time.time()
            with contextlib.suppress(Exception):
                await session.ws.send_text('{"sender":"_ping","text":""}')

        # --- half-duplex gate: never run turn detection on our own voice ---
        backstops_before = session.gate.backstop_releases
        if session.gate.blocked():
            session.silence_since = None  # the agent is talking: the quiet starts when it stops
            continue
        if session.gate.backstop_releases != backstops_before:
            logger.warning("[%s] no playback-done from client, released gate on deadline", session.call_id)

        if session.resync_pending:
            await _resync_after_playback(session)
            continue

        if not await _decode_to_wav(session.raw_path, session.wav_path):
            continue  # too little data yet to form a valid container -- not an error

        wav, sr = await asyncio.to_thread(torchaudio.load, session.wav_path)
        wav = wav.mean(dim=0) if wav.shape[0] > 1 else wav.squeeze(0)

        tail_start_sample = min(int(session.processed_until_s * sr), wav.shape[-1])
        tail = wav[tail_start_sample:]

        result = await _decide_turn(session, tail, sr)
        session.next_wake_s = _next_wake_delay(result)
        if result.utterance_end_s is None:
            if await _handle_silence(session, result.reason in ("no_speech", "too_little_audio")):
                return
            continue
        session.reset_speculation()

        absolute_end_s = session.processed_until_s + result.utterance_end_s
        session.utt_seq += 1
        utterance_wav = await _slice_utterance(
            session,
            session.processed_until_s,
            absolute_end_s,
            session.utt_seq,
        )
        # The language ID started on this turn's first words travels with the turn (never left on the session,
        # where the next turn's dispatch could pick it up).
        early = session.early_lid if session.early_lid and session.early_lid.matches(session.processed_until_s) else None
        session.early_lid = None
        session.processed_until_s = absolute_end_s
        asyncio.create_task(_dispatch_turn(session, utterance_wav, early))


async def _handle_control(session: CallSession, raw: str):
    """Client -> server control channel.

    "playback_done" is the load-bearing half of the echo gate: the server
    cannot otherwise know when the caller's speaker actually stopped.

    "interrupt" (KCD-464) is the manual stop affordance: static/index.html
    shows a button while agent_speaking, and clicking it stops local
    playback, unmutes the mic and sends this immediately, WITHOUT waiting
    for the queued clips to finish. Deliberately NOT acoustic barge-in --
    this file's own module docstring already explains why that needs an
    echo canceller this system does not have (no reference signal for
    audio synthesized locally and played through Web Audio). This is the
    honest, buildable version of "stop the AI, let the caller talk": the
    caller asks for the floor instead of the system trying to detect it
    from the muted mic, which structurally cannot see it."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[%s] unparseable control frame: %r", session.call_id, raw[:80])
        return
    if msg.get("type") == "playback_done":
        session.release_gate()
    elif msg.get("type") == "interrupt":
        await _interrupt_playback(session, "manual")


@app.websocket("/ws/audio")
async def ws_audio(ws: WebSocket):
    await ws.accept()
    session = CallSession(ws)

    # The door. A call the AI cannot serve goes to a person NOW -- never
    # queued behind the GPU. See agent/admission.py for what closes it.
    admission = _admission.try_admit()
    if not admission.admitted:
        logger.warning("[%s] call refused: %s", session.call_id, admission.reason)
        try:
            await _handoff_to_human(session, admission.reason, languages=HANDOFF_ALL_LANGUAGES)
        finally:
            session.cleanup()
        return
    session.admission = admission
    logger.info("[%s] call started (%d active)", session.call_id, _admission.active_calls)
    await _refresh_messages()
    session.recorder = CallRecorder(session.call_id, _write_call_event)
    _rec(session, "start", "voice", disclosure_version_label())
    poll_task = asyncio.create_task(_turn_poll_loop(session))

    try:
        if session.duplex is not None:
            # Tell the client to keep the microphone LIVE during playback and to
            # honour stop_playback: without this it mutes capture as before and
            # the canceller simply sees silence (safe, just no barge-in).
            await session.ws.send_text(json.dumps({"type": "config", "aec": True}))
        await _speak(session, phrase("greeting", "bn"), "bn")
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await session.append(message["bytes"])
            elif message.get("text"):
                await _handle_control(session, message["text"])
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("[%s] session crashed", session.call_id)
    finally:
        poll_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poll_task
        if session.recorder is not None:
            # KCD-501: everything completed was already written as it happened; this adds the outcome
            # and drains anything still queued, within the story's thirty seconds. A miss is logged.
            outcome = session.outcome if session.turn_count else "abandoned"
            with contextlib.suppress(Exception):
                _write_satisfaction(session)
            try:
                left = await asyncio.wait_for(session.recorder.finish(outcome), CALL_RECORD_DEADLINE_S + 3.0)
                if left:
                    logger.error("[%s] call record: %d events could not be written", session.call_id, left)
            except Exception as e:  # noqa: BLE001
                logger.error("[%s] call record could not be closed: %s", session.call_id, e)
        await _save_unfinished_draft(session)
        _admission.release(admission)
        session.cleanup()
        logger.info("[%s] call ended (%d active)", session.call_id, _admission.active_calls)


app.mount("/", StaticFiles(directory="static", html=True), name="static")
