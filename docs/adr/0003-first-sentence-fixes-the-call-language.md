# ADR 0003 — The first real sentence fixes the language of the whole call

**Status:** Accepted. **Supersedes ADR 0002 §1** (reply language per utterance) as the *default*; ADR 0002 §2–§5
(early language ID, ambiguity hand-off, the `VOICE_AGENT_LANGUAGES` rollback) are untouched. Does not touch the truth
boundary, the governance model or the pilot's capacity/placement decisions.

**Date:** 2026-10-13
**Driver:** the product owner's instruction: *if the caller's first sentence is Bengali, the whole conversation is in
Bengali; the same for English and Hindi.*

---

## Decision

`REPLY_LANGUAGE_MODE` now defaults to **`sticky`**: the language of the caller's first **real sentence** is the language
of the call. Reply templates, the clinic FAQ answer and the TTS voice stay in it for every later turn, whatever
language the later turns are *recognised* in. **Nothing moves it afterwards -- not even an explicit request** ("speak in
Hindi", `agent/language_switch.py`): such a request is answered, in the call's own language, with "this call stays in
<language>" (`phrases["language_stays"]`) and the call goes on. The one exception is a request that IS the first sentence,
which sets the language. An unidentifiable turn is re-asked in the call's language only (not "in every language"), and
an abusive turn is answered in it. `REPLY_LANGUAGE_MODE=per_utterance` is unchanged and still supported (set it to roll
back).

"Real sentence" is `agent/language_policy.establishes_call_language`: two or more words that are not a greeting,
a yes or a no. A bare "hello", "হ্যাঁ", "नमस्ते" or a single word does **not** fix the language — they are the same
sound in every language and the recogniser's label for them is a guess. Until a turn establishes the language the
reply follows the utterance as before and nothing is fixed.

A turn that could not be read (empty ASR, a jumbled transcript, an abusive turn handled by `_keep_language`) does
not fix it either: `_keep_language` takes back a lock made earlier in that same turn.

## What was done so the old complaints do not return

ADR 0002 §1 replaced the first-language lock because of two things; both are handled here and not by freezing less:

* *The transcript is in one language and the cue tables are another's.* Under a lock, `lang` is the language the
  agent **speaks**, not the language of the words. `classify_yes_no` therefore reads an answer in the call's language
  first and, only when that finds nothing and the answer is written in another script, in that script's language
  ("yes" on a Bengali call is a yes). It never changes an answer the call's own language gave
  (checked against 39,636 string × language pairs: 0 differ).
  `detect_language_switch_request` is read with the language the request was *said* in as well as the call's, so
  "हिंदी में बोलिए" on a Bengali call is a request. `wants_to_end` and the emergency / abuse detectors already
  read every language.
* *A borrowed word flipping the call.* Unchanged: recognition leaves the call's language only on a decisive language ID.
  The first turn is read with the greeting language as the prior (`_route_and_transcribe`, live 2026-10-07).

## Consequences

* A caller who genuinely switches language mid-call keeps hearing the first language until they ask for another. This
  is the requested behaviour, and the reason ADR 0002 had chosen otherwise (two voices on one call).
* Deterministic keyword rules keyed by language (fast-path FAQ cues, the Bengali parking and address nets) see text
  in the other language only when the caller changed language; those turns reach the model, which reads any language,
  and the reply is still built in the call's language from templates.
* A first sentence recognised in the *wrong* language (a Bengali sentence labelled Hindi by language ID) now fixes
  the call wrongly. The guard is the existing one: leaving the greeting language needs a decisive language ID.
  If it proves to happen on real calls, require two consecutive turns before fixing — not built, there is no call
  data to say it is needed.

## Verification

`tests/test_first_sentence_language.py` (40): the rule, each first language, the greeting case, the unreadable first
turn, the explicit request, `per_utterance` still working, and whole calls through the real orchestrator and clinic
API. The three tests in `tests/test_language_routing_wiring.py` that pinned the old default now set
`per_utterance` explicitly; the default test pins `sticky`.
