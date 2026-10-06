# ADR 0002 — Reply language per utterance, early language ID, ambiguity hand-off, and the language rollback lever

**Status:** Accepted. Refines ADR 0001 §2 (language is per utterance) and **supersedes the sticky reply-language
rule added on 2026-09-28** (`main.py` `_resolve_reply_language`, KCD-lay-terms). Does not touch the truth boundary,
the governance model or the pilot's capacity/placement decisions.

**Date:** 2026-10-03
**Driver:** a review of the code against `Kolkata-Care-Voice-Agent-Trilingual-Upgrade-Prompt-No-Git-Access.md`
found five places where the running system differed from it.

Every threshold below is **reasoned, not measured** — none has been calibrated on real 8 kHz G.711 Kolkata calls.
Each is a named constant or environment variable, so recalibrating is a one-line change.

---

## 1. Reply language follows the utterance (was: locked to the first language of the call)

**Before.** Recognition was per utterance, but the language the agent *answered* in was locked to the first language
of the call and moved only on an explicit "speak in Hindi". That was a deliberate answer to a live-call complaint
(2026-09-28): a Bengali caller who said one English word was answered in English, and the disclosure notice was
re-played each flip.

**Decision.** `REPLY_LANGUAGE_MODE=per_utterance` is the default: the reply template set and the TTS voice are those
of the language this utterance was recognised in (`agent/language_policy.resolve_reply_language` — "never a
call-level constant"). `REPLY_LANGUAGE_MODE=sticky` restores the old rule and is a supported setting, not dead code.

**Why the original complaint does not come back.** The three things that made it painful are handled where they
arise, not by freezing the reply language:

* *A borrowed word flipping the call.* Recognition itself only leaves the call's language when language ID
  believes the new one (`agent/lang_select.pick_candidate`, `SWITCH_MIN_LID = 0.50`); a turn language ID cannot
  place uses the previous turn's language as its prior (`ASRLanguageRouter`); a turn that could not be read keeps
  the call where it was (`_keep_language`). A lone English *name* inside a Bengali sentence is recognised by the
  Bengali engine, in Bengali script, so the turn is a Bengali turn.
* *The disclosure replaying.* It is spoken once per language per call (`session.disclosed_langs`).
* *An explicit request.* "Speak in Hindi" sets the language for its own turn and seeds the next turn's prior
  (`_lock_reply_language`); after that what the caller actually speaks decides.

A side effect worth stating: every downstream text rule (`classify_yes_no`, the fast-path cues, the emergency and
abuse detectors) is now applied with the language the transcript is *in*. Under the sticky rule a Hindi transcript
could be interpreted with Bengali cue tables.

**Cost.** A caller who genuinely alternates languages is answered in each, so the call can hear two voices.
That is what the upgrade prompt asks for. If it proves jarring on real calls, the lever is `sticky`, or a
debounce (require two consecutive turns before the reply language moves) — not built, because there is no call
data to say it is needed.

## 2. Language ID starts on the first words of the turn (was: after end-of-turn)

`agent/early_lid.py`. While the caller is still speaking, once the turn detector has ≥ `EARLY_LID_WINDOW_S` (1.2 s)
of speech of which ≥ 60 % is voiced, language ID runs on that first stretch in a worker thread (CPU). At
end-of-turn the result is used **only if decisive**: a supported, active language, confidence ≥
`EARLY_LID_MIN_CONFIDENCE` (0.80) and no second language at or above `SECOND_LANGUAGE_FLOOR` (0.03). Anything else
— including the whole accented-English-labelled-Hindi class, which by construction leaves mass on a second
language — is discarded and the whole utterance is identified exactly as before. The shortcut can only save
time; it cannot route a case the existing verification would have questioned. `EARLY_LID=off` disables it.

The result travels with the turn (`_dispatch_turn(..., early)`), never on the session, so the next turn's dispatch
cannot pick up the wrong one. One identification per turn, keyed by where the turn began on the call timeline.

## 3. Repeated ambiguity hands the call on (was: only reachable before a first turn)

`ASRLanguageRouter`'s streak counted only while there was no prior language, so after the first turn it could
never fire. A second counter, `note_turn_outcome`, is fed per turn by the orchestrator. A turn is **ambiguous** when
language ID was unsure (unknown, below the 0.55 floor, or torn between languages) **and** the recogniser that won
is not convincing (no text, or decoder agreement < `CONVINCING_MIN_AGREEMENT`, 0.6) — `lang_select.outcome_is_ambiguous`.
Either alone is not: a short "yes" reads cleanly with low language-ID confidence, and a confident label over a
garbled transcript is a transcript problem handled elsewhere. After `max_ambiguous_streak` (3) tolerated turns, the
next is a `handoff_human` decision; one clear turn resets the count.

"Hand off" means the same thing it did for the original router: the turn returns `(None, None)` and the existing
re-ask policy (`agent/reask_policy.py`) asks the caller to repeat — kindly, in their language, without a guess —
and routes to a person after `DEFAULT_MAX_REASKS` further failures. It is not an immediate transfer, because
ADR-era policy is that a faint or noisy caller is asking to be heard, not asking to be transferred.

## 4. Unreviewed pronunciations are off by default in deployment too

`PRONUNCIATION_ALLOW_DRAFT` defaulted to off in code but `deploy/env.sh` turned it on for the pilot. It now
defaults to off there as well, and `main.py` logs a warning at startup whenever it is on. **Consequence while the
native review is outstanding:** a reply that names an English lab term with no approved spoken form is blocked
(`UnspeakableTextError`) and the caller hears the system-busy fallback — this is exactly the blood-test-list reply
of the live call of 2026-09-28. To listen to the drafts on a pod, start with `PRONUNCIATION_ALLOW_DRAFT=1`. The way
out is the native review (`agent/pronunciation.review_sheet()`, `load_reviewed()`), not a default.

## 5. One setting rolls the whole stack back to Bengali only

`VOICE_AGENT_LANGUAGES` is parsed in one place (`agent.lid.parse_active_languages`; Bengali is always active,
unknown codes are ignored) and read by all three consumers:

| Consumer | `bn` alone means |
|---|---|
| `main.py` / `main_pcm.py` | no language ID, no Hindi/English ASR loaded, replies in Bengali |
| `deploy/start_all.sh` | the English ASR process (:8003) is not started; one left over is stopped by port |
| `tts_server.py` | only the Bengali voice is loaded; another language is a 422, never a fall-back voice |

`deploy/set_languages.sh bn` (and `bn,hi,en` to restore) validates the value, writes the persistent file
`/workspace/.voice_agent_languages` that `deploy/env.sh` reads, and restarts the stack with `FORCE_RESTART=1`.
No code revert, no redeploy of old code. `deploy/status.sh` reports the build it is looking at.

---

## Not verified

Everything here is proven by off-pod tests (`tests/test_early_lid.py`, `tests/test_language_ambiguity.py`,
`tests/test_language_routing_wiring.py`, `tests/test_language_rollback_and_drafts.py`), which drive the real
orchestrator functions with the pod-only libraries stubbed. Not exercised on a pod:

* the early identification against the real SpeechBrain model and real telephone audio (does the first 1.2 s
  identify as well as the whole utterance? what fraction of turns take the shortcut?);
* the ambiguity thresholds against real call recordings (how often is a *good* call flagged?);
* `set_languages.sh`'s restart on a real pod (syntax- and validation-checked only);
* how callers experience per-utterance replies on a call that really alternates languages.
