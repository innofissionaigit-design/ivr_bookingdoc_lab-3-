# Kolkata Care Voice Agent — Peer Review Results

**Review of:** the merge of `multilingual_ivr@main` into `ivr_dev_2@dev_sourav`
**Merged tree:** `multi_merge/merged`, branch `merged`, head `031c383`
**Reviewed against:** `Kolkata-Care-Voice-Agent-Peer-Review.md` (the review skill), `CLAUDE.md`, `docs/adr/0001`, `docs/adr/0002`
**Date:** 2026-10-06
**Reviewer:** Claude Code session (automated code-level review). **Not a substitute for human peer review or CodeRabbit** — `CLAUDE.md` §6.

> **Label convention** (peer-review doc §30): **Measured** = observed in this
> session. **Unmeasured** = not observed here, by anyone. **Inherited** = true of
> one or both source branches before the merge, carried across unchanged.

---

## 0. What this review can and cannot say

This review covers **Gate 1 (code correctness)** and the parts of **Gate 2
(multilingual correctness)** that automated tests reach. It covers **none** of
Gates 3–6: no pod was available, so no live voice, VAD, ASR, TTS, AEC/barge-in,
latency, concurrency or deployment behaviour was exercised.

Per the review skill §35, that means **production readiness is not
demonstrated**, and nothing below should be read as claiming it is.

The specific question this review *does* answer:

> Did merging these two branches lose any behaviour, break any story, or
> weaken any safety boundary?

**Answer: no — after 13 silent merge losses and 2 merge-interaction defects were
found and fixed.** Evidence follows.

---

## 1. The merge, in one paragraph

The two trees were separate GitHub repositories with **no common git
ancestor** (`ivr_dev_2` and `multilingual_ivr`), so this was a content merge,
not a `git merge`. Their feature sets were nearly orthogonal:

| Side | Brought |
|---|---|
| **Sourav** (`merging`) | home sample collection end to end (KCD-387); *caller has reached the hospital by mistake*; multi-test preparation merge (KCD-382); registered/booked-patient preparation (KCD-382b) |
| **Multilingual** (`multilingual_ivr`) | per-utterance reply language (ADR 0002); early LID; language-ambiguity backstop; *caller asks what they have booked*; date check; earliest-available request; the one-switch language rollback lever |

A synthetic common base was reconstructed (`merging@037dbf1`) and a real
three-way merge performed, which produced a merge commit and five conflicts.
**The conflicts were not the risk.** The risk was that the multilingual
snapshot *predated* KCD-382/382b, so from the chosen base its delta read as
"delete that work", and `git` applied those deletions cleanly in files that
never conflicted.

---

## 2. Code-level review

**Result:** ☑ **PASS WITH CONDITIONS** ☐ PASS ☐ FAIL

**Test count:** 3,769 passed · 23 failed · 11 skipped · 19 xfailed · 81 errors
(full suite, off-pod, `python -m pytest -q`)

**Failures:** 23 — **all** in `tests/test_smoke.py`, the pod-only suite that
needs live Ollama / TTS / clinic-api. `scripts/gate.sh` skips this suite off-pod
by design and says so. Both source branches fail the identical 23 off-pod.
**Merge-caused failures: 0.**

**Safety regressions:** 0. `marker_hallucination` passes; `boundary_rules`
(the one-import rule and the vendor boundary, `CLAUDE.md` §1) passes.

**Multilingual regressions:** 0. `tests/test_booked_lookup.py` 73 passed;
`tools/lookup_intent_eval.py` 38/38 correct with **0 critical confusions** in
every bucket (bn / hi / en / mixed); `test_early_lid`, `test_language_ambiguity`,
`test_language_rollback_and_drafts`, `test_language_routing_wiring` all pass.

### 2.1 Comparison against both baselines — the only honest way to read this

| | failures | of which pod-only | passed | errors |
|---|---|---|---|---|
| `merging` baseline | 23 | 23 | 3,446 | 81 |
| `multilingual_ivr` baseline | 24 | 23 | 3,449 | 63 |
| **merged** | **23** | **23** | **3,769** | **81** |

Computed as a set difference, not by eye:

- failures in merged that are in **neither** baseline → **none**
- failures in a baseline that merged **fixes** → one
  (`test_phrase_proximity::test_matching_agrees_with_an_independent_reimplementation`,
  a Hypothesis property test that did not recur)

The higher pass count is expected: the merged tree carries the union of both
test suites (146 test modules vs 139 and 135).

### 2.2 The 81 errors are not defects — **Inherited**

All 81 are collection errors in 8 files, caused by this repo's known
cross-file `sys.path` pollution (`pyproject.toml` already documents it:
*"Tests and tools put the repo on sys.path and then import from it"*).

**Measured:** every one of those 8 files passes in isolation in the merged
tree — 132 tests, 0 failures. `merging` shows the same 81, `multilingual_ivr`
63. Out of scope for this merge by agreement with the repo owner; it belongs
to Epic E24 and touching `conftest`/`pytest.ini` is restricted by
`CLAUDE.md` §4.

---

## 3. Defects found and fixed during the merge

### 3.1 Thirteen silent losses — base skew (P0 had they shipped)

Caught by three independent audits (symbol-level AST, import resolution,
line-level content), **not** by the merge tool, which reported success:

| Restored | What would have broken |
|---|---|
| `main.py` KCD-382/382b `test_prep` dispatch branch | KCD-382 + 382b entirely dead code — helpers present, never called |
| `main.py` `_answer_enquiry_intent` `test_names` fallback | a single test named in the plural slot silently unanswerable |
| `agent/llm.py` `test_names` prompt rule | the model would never fill `test_names` for `test_prep`, so the multi-test story could not trigger at all |
| `agent/phrases.py` `test_prep_conflict_handoff` (bn/hi/en) | KCD-382 AC3 hand-off had no words to say |
| `agent/api_models.py` `PrepMergeAnswer` | merge response unvalidated |
| `agent/tools_client.py` `merge_test_prep` | no client for the merge endpoint |
| `agent/reply_templates.py` + `_i18n.py` `merged_test_prep_reply`, `multiple_test_bookings_reply` | `NameError` at 3 live call sites |
| `clinic-api/enquiry_service.py` `incomplete_data` + 2nd escalation trigger | **a test needing fasting but lacking a structured hour count would silently vanish from the MAX(), understating the real constraint** — the anti-hallucination boundary (§9) |
| `clinic-api/patient_context.py` `booking_test_names` | full test set in a booking group unreadable |
| `clinic-api/main.py` `/bookings/{id}/tests` | that endpoint gone |
| 4 test files (`test_followup_context` + 3 KCD-382 suites) | the regressions above would not have been caught |

Each restore was verified as a genuine superset before applying: where the
multilingual side had *also* changed a file, only the missing block was
re-added (`clinic-api/main.py`, where the multilingual `other_day_slot` fix was
deliberately kept over Sourav's older code).

### 3.2 Feature collision: `asks_what_is_booked` hijacked home collection — **P1, fixed**

`agent/lookup_request.py` (multilingual) runs **before** the model and claims
the turn. Its rule requires a booking noun + an existing-booking marker, which
*"আমার বুকিং করা টেস্টটা বাড়ি থেকে নেওয়া যাবে?"* ("can the test I have booked be
collected from home?") satisfies via "আমার" + "বুক করা". Result: the turn was
answered as a booking read-back and **`home_collection` (KCD-387) was never
entered** — a whole story unreachable through its most natural phrasing.

**Fix:** rule 1b, an *other-action veto*, in the module's own idiom — rule 1
vetoes a request for a **new** booking, 1b vetoes a request to do something
**else** with an existing one, and the turn then goes to the model, which the
module's own docstring already says a miss should cost. Cancel and reschedule
are deliberately **not** vetoed: those flows legitimately begin from a booking
that was read back first.

Chosen over a call-site guard in `main.py` because the module owns this
decision, per review skill §29 (prefer one place over scattered copies).

**Verified non-regressing:** 73 lookup tests pass; the eval still scores 38/38
with 0 critical confusions; all home-collection suites pass.

### 3.3 One stale test assertion — widened with the owner's approval

`tests/test_orchestrator_unrelated_caller.py`: for *"Sorry bhai, actually mera
appointment check karna tha."* the merged tree now answers *"Could you give me
the number you booked from?"* instead of a generic re-ask — **a better reply**,
and what the same file's sibling test already asserts for that exact sentence.
Only the literal wording check was widened; **every safety assertion
(`unrelated_count == 0`, socket not closed, `outcome == "completed"`,
`signals.unrelated_caller is False`, neither unrelated phrase spoken) is
unchanged.** Approved explicitly by the repo owner, per `CLAUDE.md` §4 item 4.

---

## 4. Completeness — "no story broken, all must be present"

**Measured, in the delivered tree:**

| Check | Result |
|---|---|
| Story strings across both sides | **26 / 26 present** |
| KCD tickets referenced across both sides | **151 / 151 present** |
| Files from either source tree | **0 missing** |
| Python symbols from either source tree | **0 lost** (one deliberate rename, `_allow_draft` → `drafts_enabled`, made public for the startup warning; no stale callers) |
| `from <local module> import X` where X does not exist | **0** |
| Story-suite tests, both feature sets (24 modules) | **736 passed, 0 failed** |
| `main_pcm.py` vs its generator | byte-identical; reasoning half verified identical to `main.py` |

---

## 5. Architecture and safety review (skill §§4–18, 22, 26–29)

| Area | Judgement | Basis |
|---|---|---|
| §4 separation of responsibilities | **PASS** | unchanged by the merge; `boundary_rules` green |
| §5.1 per-utterance language | **PASS** | ADR 0002 default `per_utterance`; `sticky` retained as a switch; language no longer latched at greeting |
| §5.2 code-mixed speech | **PASS (code level)** | romanised cue sets present in both detectors; **Unmeasured** live |
| §7 decoder agreement | **PASS** | feeds `transcript_problem` and the clarification path; merged `slot_answer` chain is the union of both sides' conditions |
| §8 resolution order (deterministic → cache → LLM) | **PASS** | abstention preserved; §3.2 fix *strengthens* abstention |
| §9 anti-hallucination | **PASS** | `marker_hallucination` green; §3.1's `incomplete_data` restore was itself an anti-hallucination fix |
| §10 conversation state | **PASS** | `CallSession` is the union of both sides' explicit fields; no shared mutable module state introduced |
| §11 confirmation / low-confidence writes | **PASS** | all deterministic yes/no gates from both sides retained in `_dispatch_turn` |
| §12 booking / reschedule / cancel | **PASS (code level)** | hold→confirm two-phase intact; refund eligibility *stated* from policy, never inferred; home-collection hold mirrors KCD-376 |
| §14–16 numbers, TTS, unspeakable text | **PASS, with a Known gap** | see §6.2 — `ALLOW_DRAFT=0` means unreviewed clinical terms are *blocked*, not spoken |
| §17 barge-in / duplex | **Unmeasured** | `AEC_BARGE_IN=0`, PCM transport only, synthetic echo only |
| §18 VAD / endpointing | **Unmeasured** | no real speech |
| §26 cache safety | **PASS** | live lookup still always follows a cached intent |
| §29 code quality | **PASS with a note** | the §3.2 fix was placed in the owning module rather than duplicated; but see §6.4 |

### 5.1 One deliberate behavioural change the merge ships

`REPLY_LANGUAGE_MODE` now defaults to **`per_utterance`** (multilingual side,
ADR 0002) where the Sourav side had sticky behaviour hard-coded. ADR 0002 is
authority order #2 per `CLAUDE.md` §2, and `sticky` remains available as an env
switch, so the live-call complaint of 2026-09-28 is still addressable without a
code change. **Flagged because it changes what a caller hears**, and it is the
one merge outcome a named owner should consciously accept.

---

## 6. Live voice review

**Result:** ☐ PASS ☐ PASS WITH CONDITIONS ☐ FAIL — ☑ **NOT PERFORMED**

**Languages tested:** none live · **Code-mix combinations tested:** none live
**Barge-in result / AEC result / observed latency / ASR issues / TTS issues:**
all **Unmeasured** — no pod was running.

Per skill §3, this layer is **not interchangeable** with the code-level layer
and must be executed by human callers before any release claim.

---

## 7. Business workflow review

Code level only (deterministic stubs and local SQLite via `TestClient`):

| Workflow | Result |
|---|---|
| Booking | ☑ PASS (code level) |
| Rescheduling | ☑ PASS (code level) |
| Cancellation | ☑ PASS (code level) |
| Confirmation safety | ☑ PASS |
| Low-confidence write protection | ☑ PASS |

**Not yet covered by an automated test** (skill §12.2's mandatory test): a
simulated failure *between* new-slot reservation and old-slot release, proving
the original appointment cannot be lost. The two-phase shape is present in
code; the injected-failure test is not. **Recommended as the next test to
write** — it is a P0 invariant with no regression guard.

---

## 8. Final decision

☐ Approved ☑ **Approved with conditions** ☐ Changes requested ☐ Not ready for release

**Scope of this approval:** the **merge** is sound and complete — both feature
sets present, no story broken, no safety boundary weakened, no merge-caused test
or gate regression. It is a reasonable candidate to land on a branch and put in
front of a human reviewer and CodeRabbit.

**This is explicitly *not* an approval for release.** Gates 3–6 are unexecuted.

### Blocking issues (for release, not for landing the merge)

1. **No live/pod validation of any kind.** No telephony or browser call, no
   AEC/barge-in with real acoustic feedback, no latency p50/p95/p99, no
   concurrency run, no per-language WER/CER. Skill §33 marks every one of these
   P0. Browser success would not be sufficient either.
2. **The native pronunciation review has never been done.** `REVIEWED` in
   `agent/pronunciation.py` is empty, so with the shipped
   `PRONUNCIATION_ALLOW_DRAFT=0` a reply naming an unapproved English clinical
   term is **blocked** and the caller hears the system-busy fallback — the live
   call of 2026-09-28. Setting it to `1` is a documented listening aid, not a
   release setting (`main.py` warns while it is on). **The way out is the
   review, not the flag.** This is the one condition that forced a deployment
   choice during the merge, decided with the repo owner as `0`.
3. **`scripts/gate.sh --full` is RED** — `format`, `lint`, `typecheck`,
   `secrets_scan_fallback`. **All Inherited**, and verified to be *exactly* the
   union of the two baselines (41 unformatted files = |28 ∪ 29|; lint and
   typecheck finding sets identical). Per `CLAUDE.md` §4 the gate must be green
   before human review, so this must be cleared — but it is pre-existing debt,
   not merge damage, and clearing it was explicitly out of scope.

### Non-blocking, recommended next

4. Write the §12.2 reschedule-failure test (§7 above). P0 invariant, no guard.
5. Fix the cross-file `sys.path` pollution behind the 81 collection errors
   (Epic E24). A suite that cannot run clean as a whole cannot gate a release.
6. `secrets_scan_fallback`'s two hits reviewed by hand: `clinic-api/setup_db.sh:13`
   `DB_PASS="${DB_PASS:-kcd_app_pw}"` is a **real** (if minor) plaintext dev
   default — skill §22 "no plaintext credentials"; `test_home_collection_concurrency.py:132`
   `hold_token="pre-confirmed"` is a **false positive** (test fixture). No
   credential-shaped literal exists anywhere in non-test `agent/` or `clinic-api/`.
7. The `mypy` error (`reply_templates_i18n.py` `fromisoformat` with `str | None`)
   is a **false positive** — the `except (TypeError, ValueError)` handles `None`
   deliberately — but it keeps `typecheck` red and is worth silencing precisely.

### Reviewer notes

The instructive finding is that **a clean three-way merge is not evidence of a
correct merge.** Every one of the 13 losses in §3.1 was in a file `git` reported
as auto-merged, because the base skew made "work the other side had not yet
seen" indistinguishable from "work the other side deleted". The symbol- and
line-level audits, not the merge tool, are what caught them — and the single
most dangerous one (`incomplete_data`) would have silently weakened the
anti-hallucination boundary this project treats as immutable.

The second lesson is §3.2: two independently-correct deterministic nets, each
precision-first and well-reasoned on its own branch, overlapped on one real
caller sentence and silently disabled a whole story. **Nothing in either
branch's tests could have caught it; only the union could.** Any future
deterministic pre-model net should be reviewed against every other one that
already runs before the model.

---

## 9. Reproducing this review

```bash
cd multi_merge/merged
python -m pytest -q -rfE          # 3769 passed, 23 failed (all tests/test_smoke.py, pod-only)
bash scripts/gate.sh --full       # RED: format/lint/typecheck/secrets, all inherited
python tools/make_pcm_variant.py  # must report "byte-identical" and leave no diff
python tools/lookup_intent_eval.py  # 38/38, 0 critical confusions
```

Provenance is in the git history: `7ae17a8` synthetic base → `a6f4a77` Sourav
side → `61e9b96` multilingual side → `4ea0e12` merge → `031c383` fixes. Diff
any side against the merge to review this work independently.
