# Implementation plan — transcript-grounded test entities + an acceptance layer

**Status:** IMPLEMENTED 2026-10-08. See §8 for what shipped and where.
**Written:** 2026-10-08, after the live call below.
**Owner rule this serves:** CLAUDE.md §1 — *"The LLM may decide what the caller wants. It may never decide what is true."*

---

## 1. The failure this closes

```
[User]  হম বলছি আমি ওই কে দুটো টেস্ট করবো তোমার কত টাকা পরবে টোটাল
[AI]    সিবিসি 400 টাকা আর থাইরয়েড প্রোফাইল 700 টাকা। সব মিলিয়ে মোট 1100 টাকা।
```

The caller asked for **CBC and ECG** (400 + 300 = **₹700**). They were quoted **CBC + Thyroid Profile = ₹1100**, for two tests they never named.

The transcript contains **no test name at all** — ASR lost "সিবিসি আর ইসিজি" and left "ওই কে". What it does contain is `দুটো` (two) and `টোটাল` (total). The names came from the intent layer, not the caller.

### Why nothing stopped it

| # | Gap | Where |
|---|---|---|
| 1 | The prompt's never-invent list omits test and doctor names | [agent/llm.py:120](agent/llm.py) |
| 2 | `_named_tests()` trusts the model's `test_names` with no check against what was heard | [main.py:1997](main.py) |
| 3 | Doctors get re-checked against the clinic's table (`_doctors_mentioned`); tests get nothing equivalent | main.py |
| 4 | The read-back fires only on low/single-decoder ASR confidence, and `ENTITY_SLOTS["test_rate"]` is the **singular** `("test_name",)` — a two-test question can never be fully read back | [agent/entity_confirmation.py:27](agent/entity_confirmation.py) |
| 5 | The truth boundary guards **numbers**, not **entity selection**. 400, 700 and 1100 were all genuine catalogue figures — correctly summed, for the wrong tests | — |

A secondary possibility that cannot now be ruled out (the pod is gone): an L2 **semantic-cache** hit. Test names are L2-eligible, and L2 is fuzzy cosine matching, so a near-identical sentence frame from an earlier CBC+Thyroid question could have returned that stored extraction. §4 closes this path too, because grounding runs *after* the cache.

---

## 2. Architecture change

Today:

```
ASR transcript → LLM / fast path / cache → test_names → price lookup → spoken reply
```

Proposed:

```
ASR transcript
      ↓
LLM / fast path / cache          (intent + candidate entities)
      ↓
┌───────────────────────────────────────────────┐
│ 1. ENTITY GROUNDING   (agent/entity_grounding) │  deterministic, pure
│    candidate ∈ catalogue  AND  present in text │
└───────────────────────────────────────────────┘
      ↓ grounded names only          ↓ nothing grounded
┌──────────────────────────┐   ┌──────────────────────────┐
│ 2. ACCEPTANCE LAYER      │   │ 3. ASK WHICH TESTS        │
│    "CBC আর ECG, ঠিক তো?" │   │    (count-aware)          │
└──────────────────────────┘   └──────────────────────────┘
      ↓ yes
   price lookup → spoken reply
```

**The absolute rule:** *an entity may not be used because it exists in the catalogue; it must also be supported by the caller's transcript.*

Applied to the failure:

```
transcript : "আমি ওই কে দুটো টেস্ট করবো কত টাকা পড়বে"
LLM        : test_names = ["CBC", "Thyroid Profile"]
grounding  : CBC             → not in transcript  ✗
             Thyroid Profile → not in transcript  ✗
result     : no grounded entity, test_count = 2
reply      : "আপনি দুটো টেস্টের কথা বলছেন — কোন দুটো, একটু নাম বলবেন?"
```

No price lookup happens. The ₹1100 is impossible.

---

## 3. Work items

### 3.1 `agent/entity_grounding.py` — new, pure, no I/O

Obeys CLAUDE.md's one-import rule: imports nothing from `main.py`.

```python
@dataclass(frozen=True)
class GroundedTests:
    names:      tuple[str, ...]   # canonical catalogue names, in the order said
    dropped:    tuple[str, ...]   # candidates the transcript does not support
    count:      int | None        # how many the caller said they want
    exact:      bool              # every name matched a full alias (score 1.0)

def ground_tests(transcript, lang, catalogue, candidates=()) -> GroundedTests
```

**Extraction, deterministic first** (the user's point 2 — "which test?" is a bad job for semantic similarity; the handover measures cosine ≈ 0.749 between *different* tests in the same sentence frame, while character-level comparison separates them cleanly):

1. Normalise with `fast_path._normalize` (NFC, nukta drop, punctuation → space).
2. Split on conjunctions — `আর, এবং, ও, ও-তে, और, तथा, and, &, ,` — into segments.
3. Match **each segment** with `catalogue.match(segment, "test", lang, COMMIT_FLOOR)`.
4. Also match the whole transcript, to catch a name the split broke.
5. De-duplicate by canonical name, preserve order of first appearance, cap at `MAX_TESTS_PER_TURN = 4`.

**Candidate filtering:** every name in `candidates` (from the LLM) survives only if it resolves to a canonical name in the extracted set. Everything else goes to `dropped` and is logged.

**Supporting change** — `skip_name` → `skip_names`:
`Catalogue.match` and `FormTable.best` take one `skip_name` today ([agent/form_index.py:167](agent/form_index.py)). Widen it to a `frozenset`, so the runner-up scan can exclude everything already found. One-line change at the comparison; `_ambiguous_test` ([agent/fast_path.py:447](agent/fast_path.py)) keeps working by passing a one-element set.

### 3.2 `test_count` — separate from `test_names`

> `test_count` = **what** the caller said · `test_names` = **which** tests the caller said. Different facts; never derive one from the other.

- **Prompt** ([agent/llm.py](agent/llm.py)): add `"test_count": number or null` to the schema, with the rule — *"how many tests the caller said they want, when they gave a number but no names ('দুটো টেস্ট', 'दो टेस्ट', 'two tests'). Never invent names to fill a count."*
- **Deterministic parse wins**, exactly as dates and times do (`_pin_spoken_dates` / `_pin_spoken_times`): a small `spoken_count(text, lang)` in the grounding module, over the counter-words already tabulated in [agent/bn_normalize.py:225](agent/bn_normalize.py) (`দুটো`, `তিনটে`, …) plus Hindi/English equivalents, required to sit next to the word for *test*.
- A count with no names is a **complete, useful** extraction — it drives the count-aware question in §3.4.

### 3.3 Prompt hardening

[agent/llm.py:120](agent/llm.py) currently reads:

> "Never invent a patient name, phone number, confirmation number, postal code, address, or date that was not said."

Add **test names and doctor names** to that list, and a worked negative example:

```
"আমি দুটো টেস্ট করবো, কত পড়বে"
→ {"intent": "test_rate", "test_count": 2, "test_names": null}      ✓
→ {"intent": "test_rate", "test_names": ["CBC", "Thyroid Profile"]}  ✗ never
```

This is defence in depth only. Grounding (§3.1) is what actually enforces it.

### 3.4 The acceptance layer

New session state, beside the existing `pending_*` / `awaiting_*` family ([main.py:1193–1254](main.py)):

```python
self.pending_tests  = None   # {"action": "test_rate"|"book_test", "names": [...], "turn": n}
self.awaiting_tests = False  # inside the "which tests?" loop
self.tests_asked    = 0      # re-ask counter, capped
```

Both handlers join the dispatch chain at [main.py:3518–3526](main.py), before intent extraction, in this order:

```python
if session.pending_tests  and await _continue_test_confirm(session, text, lang): return
if session.awaiting_tests and await _continue_test_choice(session, text, lang):  return
```

#### Confirm before pricing

Reached from the `test_rate` branch and from `_answer_test_booking`, once grounding returns ≥ 1 name:

| | |
|---|---|
| bn (book) | আপনি সিবিসি আর ইসিজি টেস্ট করবেন, ঠিক তো? |
| bn (price) | আপনি সিবিসি আর ইসিজি টেস্টের দাম জানতে চান, ঠিক তো? |
| hi (book) | आप सीबीसी और ईसीजी टेस्ट कराएँगे, ठीक है ना? |
| en (book) | You want the CBC and ECG tests, is that right? |

Names are spoken through `spoken_test_name()` + `spoken_list()`, so the caller hears the alias in their own script — never the Latin catalogue key, which the Bengali and Hindi voices drop.

#### `_continue_test_confirm` — the answer

Any yes or no word, both directions, via the existing deterministic `classify_yes_no()` ([agent/booking_flow.py](agent/booking_flow.py)) — it already covers হ্যাঁ/ঠিক আছে/ওকে/আচ্ছা দিন, हाँ/ठीक है/अच्छा, yes/ok/sure/go ahead, and না/নয়, नहीं, no/nope. **No LLM call**, same reasoning as every other binary gate here.

| Answer | Action |
|---|---|
| **yes** | price them: existing `_price_tests` → `multi_test_rate_reply`; `book_test` appends the counter line |
| **no** | clear the names, enter the loop: ask **"কোন কোন টেস্ট করতে চান, একটু নাম বলবেন?"**, set `awaiting_tests` |
| the turn **names tests** (grounded) | treat as a correction — re-confirm the new list |
| the turn **asks for the list** (`asks_for_test_list`) | read a page of tests, keep the confirmation pending |
| anything else | the question lapses; handled as a fresh turn (the existing KCD-104 rule) |

#### `_continue_test_choice` — the loop

| Caller does | Agent does |
|---|---|
| names tests (grounded) | confirm them → back to the acceptance step |
| asks what tests exist | `_speak_test_list()` — paged, "আরও শুনবেন?" — and **stays** in the loop |
| says no / gives nothing usable / changes subject | **leaves** the loop and says the general help line |
| 2 failed asks (`TEST_ASK_LIMIT`) | leaves the loop the same way — never loops forever |

Exit line, new phrase key `how_else_help` (the existing `phrase("greeting")` opens with নমস্কার, wrong mid-call):

| bn | ঠিক আছে। আর কীভাবে সাহায্য করতে পারি? |
|---|---|
| hi | ठीक है। और कैसे मदद कर सकती हूँ? |
| en | All right. How else can I help you? |

#### Nothing grounded at all

| Case | bn |
|---|---|
| count known | আপনি **দুটো** টেস্টের কথা বলছেন — কোন দুটো, একটু নাম বলবেন? |
| no count | কোন কোন টেস্টের কথা বলছেন, একটু নাম বলবেন? |

Then `awaiting_tests` — the same loop.

### 3.5 When to confirm — one decision to make

| Option | Behaviour | Cost |
|---|---|---|
| **A (recommended, matches the request)** | confirm **every** time, before any price or booking | one extra turn on every price question |
| B | skip when exactly one test matched a full alias (score 1.0) **and** ASR confidence was VERIFIED | fewer turns; a single misheard test can still be priced unconfirmed |

Ship **A** behind one constant, `TEST_CONFIRM_ALWAYS = True`, so B is a one-line flip after listening to real calls.

---

## 4. Order of work

1. `skip_names` widening — [agent/form_index.py](agent/form_index.py), [agent/fast_path.py](agent/fast_path.py). Pure refactor, existing tests prove it.
2. `agent/entity_grounding.py` + unit tests. No orchestrator changes yet.
3. Wire grounding into the `test_rate` and `book_test` branches, still pricing immediately. The ₹1100 class of bug dies here.
4. Acceptance layer + the loop + phrases, all three languages.
5. `test_count` in the prompt and the deterministic parser.
6. Prompt never-invent list.
7. Regenerate `main_pcm.py` with `python tools/make_pcm_variant.py` (it is generated; never hand-edit) and run `bash scripts/gate.sh --full`.

Steps 1–3 are shippable on their own and remove the harm; 4–6 improve the conversation.

---

## 5. Test plan

**New — `tests/test_entity_grounding.py` (pure, fast)**

- The live transcript → `names = ()`, `count = 2`. The regression, named as such.
- `"সিবিসি আর ইসিজি"` → `("Complete Blood Count (CBC)", "ECG")`, in that order, in bn/hi/en.
- LLM candidates `["CBC", "Thyroid Profile"]` against a transcript naming neither → both `dropped`.
- A real alias still grounds: `ইসিজি` → ECG; `থাইরয়েড টেস্ট` → Thyroid Profile.
- Near-miss below `COMMIT_FLOOR` does **not** ground.
- `spoken_count`: দুটো/দুটি/তিনটে, दो/तीन, two/three → 2/3; "দুটো সময়" (no test word) → `None`.

**New — `tests/test_test_acceptance_layer.py` (through the orchestrator, bn/hi/en)**

- The 2026-10-08 call replayed: garbled transcript + the model's invented pair → the caller hears the count-aware question, **no price is spoken**, `pending_tests` is empty.
- Happy path: "সিবিসি আর ইসিজি কত" → confirmation names both → **হ্যাঁ** → ₹400 + ₹300 = **₹700**.
- Yes-word variants (হ্যাঁ, ঠিক আছে, আচ্ছা, हाँ, ठीक है, yes, ok, go ahead) all price.
- No-word variants (না, नहीं, no, nope) all enter the loop and price nothing.
- In the loop: asking for the list reads tests and **stays**; a silent/unrelated turn **exits** with `how_else_help`.
- A correction mid-confirmation ("না, সিবিসি আর থাইরয়েড") re-confirms the corrected pair.
- `TEST_ASK_LIMIT` reached → exits, never loops.

**Existing tests that must be updated** — these assert a price in the turn that names the tests, which now needs a yes first:

- `tests/test_lab_test_list_and_prices.py` — `test_booking_several_tests_...`, `test_asking_the_price_of_several_tests_...`
- `tests/test_booking_answer_misfiled_as_test.py` — `test_a_real_test_named_while_the_patient_is_asked_...`
- any case in `tests/test_conversation_flows.py` / `tests/test_segment_flows.py` that prices a test in one turn

Update them by adding the confirmation turn — **never** by weakening an assertion (CLAUDE.md §4.4).

**Gate:** `bash scripts/gate.sh --full` must stay green on `boundary_rules`, `unit_and_local_integration`, `marker_hallucination`, `orchestrator_parses`. `secrets_scan_fallback` is already red on six pre-existing files unrelated to this work.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| ASR mangles a name so grounding rejects a test the caller really said | The caller is **asked**, never guessed at — the failure mode becomes one extra question instead of a wrong price. Feed `MAX_TESTS_PER_TURN` misses into the ASR-biasing lexicon later. |
| One more turn on every price question | Measure on real calls; flip to option B (§3.5) if it grates. |
| Conjunction splitting misses an unusual joiner | Whole-transcript match runs as well, and the confirmation catches what slips through. |
| `skip_names` refactor touches the hot matcher | Pure refactor, covered by the frozen Bengali parity test in `tests/test_fast_path_languages.py`. |
| Doctors have the same invention gap | Out of scope here; `_doctors_mentioned()` already re-checks against the clinic's table. Worth a follow-up to route doctors through the same grounding module. |

---

## 7. Out of scope (noted while investigating)

- **The greeting played twice** at the top of that call — two sockets, or the greeting sent both on connect and on gate release. Separate bug.
- **`deploy/status.sh` reports healthy services as "not ready"** — verified 2026-10-08: clinic-api, voice-agent and voice-pcm all answered 200 while the script called them down.
- **Semantic-cache L2 eligibility for test names.** Grounding makes a stale fuzzy hit harmless, but consider excluding `test_names` from L2 as well.


---

## 8. What shipped

| # | Work item | Where |
|---|---|---|
| 1 | `skip_name` widened to accept a set of names | [agent/form_index.py](agent/form_index.py), [agent/fast_path.py](agent/fast_path.py) |
| 2 | Transcript grounding, pure, no I/O | [agent/entity_grounding.py](agent/entity_grounding.py) *(new)* |
| 3 | Grounding wired into `test_rate` and `book_test` | [main.py](main.py) |
| 4 | Acceptance layer + the "which tests?" loop | [main.py](main.py), [agent/reply_templates_i18n.py](agent/reply_templates_i18n.py), [agent/phrases.py](agent/phrases.py) |
| 5 | `test_count`, separate from `test_names` | [agent/intent_schema.py](agent/intent_schema.py), [agent/llm.py](agent/llm.py) |
| 6 | Test and doctor names added to the never-invent rule | [agent/llm.py](agent/llm.py) |
| 7 | `main_pcm.py` regenerated from `main.py` | `python tools/make_pcm_variant.py` |

### Two decisions taken during the build

**A "no" that also names tests is a correction, not a refusal.** "না, সিবিসি আর লিপিড প্রোফাইল" re-reads the
corrected pair back instead of dropping to the loop — the rule KCD-104 already applies to the booking read-back.
A *bare* no still opens the loop.

**Grounding consumes the words it matched.** Re-scanning the same sentence for a second test kept scoring words
already spoken for: "থাইরয়েড টেস্টের দাম কত" produced Thyroid Profile and then, from the leftover "টেস্টের",
Widal Test. `_without()` removes a match's own words before the next scan, prefix-compared because Bengali
inflects the word itself ("টেস্টের" is "টেস্ট" plus a suffix).

### Tests

| File | Count |
|---|---|
| [tests/test_entity_grounding.py](tests/test_entity_grounding.py) *(new)* | 35 |
| [tests/test_test_acceptance_layer.py](tests/test_test_acceptance_layer.py) *(new)* | 42 |
| `tests/test_lab_test_list_and_prices.py`, `tests/test_booking_answer_misfiled_as_test.py` | updated with the confirmation turn — 54 pass |

### A catalogue gap this surfaced

Grounding refuses Hindi **"थायराइड"** and English **"thyroid"**: neither is a seeded alias, though Bengali
"থাইরয়েড" is. That is the layer behaving correctly on incomplete data — a Hindi or English caller saying
"thyroid" is asked which test they mean instead of being quoted one. Worth seeding those aliases
(`TEST_ALIASES_HI` in `clinic-api/i18n_content.py`, and the English bracket-less form) with a native reviewer.


---

## 9. Follow-up, same day: the clinic's own name

```
[AI]   আপনি সোনোস্ক্যান বাণীর সঙ্গে কথা বলছেন...
[User] সোনস্কানের অযাড্ড্রেস টা কি
[AI]   দুঃখিত, 'সোনস্কান' নামে কোনো টেস্ট আমাদের তালিকায় নেই
```

The agent said its own name, the caller said it back, and the agent went looking for a lab test called
Sonoscan. Three gaps, all fixed:

| # | Gap | Fix |
|---|---|---|
| 1 | Nothing knew "Sonoscan Vaani" was the agent's own name | [agent/clinic_identity.py](agent/clinic_identity.py) *(new)* -- the name is stripped before a sentence is matched against the catalogue or the FAQ keywords |
| 2 | "অযাড্ড্রেস" was not a location keyword (only "এড্রেস" was) | every spelling the recogniser produces, bn/hi/en, in the seed and backfilled into existing databases |
| 3 | A turn filed as a test question never fell back to the clinic's FAQ | `_answer_faq_instead()` -- when no named test is grounded but the sentence carries FAQ keywords, it is the clinic question it always was |

The intent prompt now also states that Sonoscan Vaani is this clinic and this assistant, never a test, doctor or
patient name.

**Why "everything became a test":** an unfamiliar noun in front of a price or question word is what the intent
model files as a test name, and before grounding there was nothing to contradict it. Grounding (§3.1) stops the
invented ones from being priced; this adds the second half -- when the thing is not a test at all, the turn is
answered as what it is instead of being refused as a test that does not exist.

The agent's given name alone -- "বাণী" / "वाणी" / "Vaani" -- is deliberately NOT treated as the clinic's name:
it is an ordinary Bengali word and a woman's name, and stripping it would delete a patient called বাণী.

**Tests:** [tests/test_clinic_identity.py](tests/test_clinic_identity.py), 30 across the three languages --
the live turn, the hours asked the same way, the patient-name safety case, and a real test question proving the
FAQ fallback does not swallow the thing it sits behind.


---

## 10. Follow-up: the day the caller chose was overwritten

```
[AI]   হ্যাঁ, ডাঃ ভট্টাচার্য। ওঁর পরের বসার দিন শুক্রবার, 2026-10-09...
[User] এদিনই করে দিন                                  ← takes the offered day
[AI]   কোন সময়ের জন্য অ্যাপয়েন্টমেন্ট চাই?
[User] এগারোটা                                         ← a TIME. no day in it.
[AI]   ডাঃ ভট্টাচার্য ওই দিন বসেন না...                 ← about the day it had just offered
```

Reproduced exactly, then fixed:

| # | Defect | Fix |
|---|---|---|
| 1 | The model fills `date` on every booking turn. With no day in the transcript, `_pin_spoken_dates` kept the model's date and `merge_slots` wrote it over the day the caller had taken -- so a day nobody spoke replaced the one the agent itself offered | `_drop_unspoken_change()` in [main.py](main.py): a day (or time) the caller's words do not support may not CHANGE one they settled. The same rule §3.1 applies to test names, on the slots the model volunteers |
| 2 | `pick_offered_slot` knew ওই / ঐ / সেই / সে ("that day") but not এ / এই ("this day"), so "এদিনই করে দিন" named no day at all | এ/এই, इसी/इस and "this day" added to the table in [agent/booking_flow.py](agent/booking_flow.py), with the refusal forms added alongside |

The guard is deliberately narrow: it drops a volunteered value only when it would **change** something already
settled. A day the caller really is changing is said out loud, and `spoken_dates` reads it before the guard is
reached -- proved by `test_a_day_the_caller_really_says_still_changes_the_booking`.

The same protection is applied to `time_slot`, which the model volunteers just as freely.

**Tests:** [tests/test_booking_day_kept.py](tests/test_booking_day_kept.py), 31 across the three languages.

### 3 -- the answer to the agent's own question was thrown away

"এগারোটা" and "ঐদিন" were both refused with "দুঃখিত, ঠিক শুনতে পাইনি". The audio was fine: that apology comes
from the ACTION GATE, and what it reacts to is two ASR decoders spelling the same word differently --
`confidence_gate.LOW`, not a bad line.

There turned out to be a narrow **dead zone** between two other rules:

| decoder agreement | what handles the turn |
|---|---|
| < 0.25 | the transcript-quality ladder (`audio_quality.decoders_disagree`) -- asks again, kindly |
| **0.25 - 0.30** | **nothing. `insufficient_information_reply`, and the answer is discarded** |
| >= 0.30 (`BOOKING_REJECT_FLOOR`) | a booking turn already carries on |

The live call landed in that band twice. `_answers_the_open_booking()` closes it: when an open booking is
collecting and the turn is a clock time, a day, or the slot the agent itself just offered, it carries on to the
read-back instead of being refused.

Deliberately narrow, because the gate's real job is unchanged: a turn that names a DOCTOR is excluded, since a
misheard name would have the agent read out a different doctor's schedule -- the "Doctor Nobody" harm the gate
exists for. A time and a day cannot become a stated fact about a person, and both are read back before anything
is written. `test_a_doctor_name_heard_badly_is_still_held_back` pins that.


---

## 11. Follow-up: a doctor named with their department, and the repeated notice

### 1 -- "कार्डियोलॉजी डिपार्टमेंट में डॉक्टर् बेनर्जी अवैलेबल है" -> all four cardiologists

The caller named ONE doctor and was read the whole department. Reproduced: it happens **only when clinic-api's
`doctors/mentioned` lookup cannot be reached.** That lookup is the one thing that reads the WHOLE sentence, so
when it fails the name is lost and `_settle_doctor` degrades to the department listing.

The name needed no network call at all -- the gazetteer already in memory matches it:

| language | matched | score |
|---|---|---|
| hi, "बेनर्जी" as the recogniser wrote it (alias is "बनर्जी") | Dr. S. Banerjee | 0.92 |
| bn | Dr. S. Banerjee | 1.00 |
| en | Dr. S. Banerjee | 1.00 |

`_mention_from_catalogue()` ([main.py](main.py)) falls back to it, and `Catalogue.doctor_rows`
([agent/fast_path.py](agent/fast_path.py)) keeps the rows so the name can be SAID the way each language speaks
it. Reported as `found=False`, so the caller is ASKED ("do you mean Dr Subrata Banerjee?") rather than told --
a local match is weaker evidence than the clinic's own answer. Never used when clinic-api does reply, including
when it names nobody.

### 2 -- the disclosure repeated in Hindi

NOT a defect: KCD-353 requires the automated-assistant notice once in the caller's own language, and the Hindi
line carries what the Bengali greeting does not ("I am automated, not a person"; "you can ask for staff"). The
wording is `pending_clinical_and_legal_review` and the release gate reads `REVIEW_STATUS`, so removing it is a
governance decision, not an engineering one. **Left in place.**

What WAS wrong is when it fired. A two-word opener ("मेरे को") switched the call to Hindi, the notice was read
out in full, and the next sentence was "माफ़ कीजिए। मैं समझ नहीं पाई" -- delivered on the strength of a turn
nobody could read. It now waits for the first turn in that language the agent actually understood; until then
`disclosed_langs` is not marked, so it stays owed rather than being skipped. No wording change, no version bump.

**Tests:** [tests/test_doctor_named_with_department.py](tests/test_doctor_named_with_department.py), 15.

### Noticed in passing, not changed

"I want to see **someone** in the cardiology department" is handed to staff -- `agent/human_request.py` reads
"see someone" as a request for a person. In a clinic IVR "see someone" much more often means "see a doctor".
