# Sprint-1 story audit — are Saurav's thirty stories in `multilingual_ivr`?

Source of the stories: `sprint1(Saurav PDF Source).csv`, supplied by the owner. Every **Epic**,
**User story** and **Acceptance criteria** block below is copied verbatim from that file.

Source of the verdicts: [`tests/test_saurav_stories_audit.py`](../tests/test_saurav_stories_audit.py),
run against this tree. clinic-api runs in-process over HTTP on a throwaway SQLite database;
the agent-side detectors and templates are called directly. No GPU, no model, no audio, no
network.

```bash
python -m pytest tests/test_saurav_stories_audit.py -v -rxs
```

## Headline

**All thirty stories have their core mechanism in this tree. None is absent.**

| | Stories | Criteria checks |
|---|---|---|
| Every criterion met | **18** | 104 passed |
| One or more criteria short | **12** | 13 gaps |
| Needs the pod to decide | — | 7 skipped |
| Failing | 0 | — |

These were not ported from the other tree: they were built here independently, under Epic E29
/ KCD-38x–39x for the enquiry half and through the merge recorded in `merge_report.md` for the
edge-case half. Different files, different function names, different story ids — which is why
every claim below is a test result rather than a reading of the source.

Each gap is recorded as `xfail(strict=True)`. Closing one makes the suite **fail** until the
xfail becomes a plain assertion, so a fix cannot land silently and a gap cannot rot.

### The one finding worth acting on first

Four of the thirteen gaps are the same blind spot: **a fixed-string Indic phrase table loses
the match the moment the caller names a person, or reaches for the everyday word instead of
the formal one.** Stories 17, 22, 24 and 25 all fail that way, and 25 fails in Bengali *and*
Hindi while English passes, because English alone has a named-doctor regex.

`merge_report.md` §8 already states that the Hindi lists are unreviewed by a native speaker and
that the hundred-prompt adversarial suite was never run in Hindi. This audit turns that general
caveat into named, reproducible failures — and shows it is not only Hindi: the Bengali
clinical-safety miss (`খারাপ`) arrived with the original port.

### Verdict at a glance

| # | Story | Epic | Verdict | Checks |
|---|---|---|---|---|
| 1 | [Caller asks how long results take](#01-caller-asks-how-long-results-take) | Information and Enquiry | **PARTIAL** | 2 met, 1 gap |
| 2 | [Two callers want the last slot at the same moment](#02-two-callers-want-the-last-slot-at-the-same-moment) | Booking, Rescheduling and Cancellation | **PRESENT** | 3 met |
| 3 | [Caller asks what sample is needed](#03-caller-asks-what-sample-is-needed) | Information and Enquiry | **PARTIAL** | 3 met, 1 gap |
| 4 | [Caller asks for a person immediately](#04-caller-asks-for-a-person-immediately) | Difficult, Sensitive and Edge Cases | **PARTIAL** | 5 met, 2 gaps |
| 5 | [Caller asks the price of a test](#05-caller-asks-the-price-of-a-test) | Information and Enquiry | **PRESENT** | 5 met |
| 6 | [Caller asks whether their report is ready](#06-caller-asks-whether-their-report-is-ready) | Information and Enquiry | **PRESENT** | 3 met |
| 7 | [Caller asks for their report to be sent](#07-caller-asks-for-their-report-to-be-sent) | Information and Enquiry | **PRESENT** | 3 met, 1 needs the pod |
| 8 | [Caller asks when a doctor sits](#08-caller-asks-when-a-doctor-sits) | Information and Enquiry | **PARTIAL** | 4 met, 1 gap |
| 9 | [Caller asks about a health package](#09-caller-asks-about-a-health-package) | Information and Enquiry | **PARTIAL** | 2 met, 1 gap |
| 10 | [Caller asks opening hours, address or directions](#10-caller-asks-opening-hours-address-or-directions) | Information and Enquiry | **PARTIAL** | 3 met, 1 gap, 1 needs the pod |
| 11 | [Caller asks how to prepare for a test](#11-caller-asks-how-to-prepare-for-a-test) | Information and Enquiry | **PRESENT** | 4 met |
| 12 | [Caller has several tests with conflicting preparation](#12-caller-has-several-tests-with-conflicting-preparation) | Information and Enquiry | **PRESENT** | 3 met |
| 13 | [Caller asks whether they can walk in](#13-caller-asks-whether-they-can-walk-in) | Information and Enquiry | **PRESENT** | 3 met |
| 14 | [Caller asks what they owe](#14-caller-asks-what-they-owe) | Information and Enquiry | **PRESENT** | 3 met, 1 needs the pod |
| 15 | [Caller asks whether a test can be collected at home](#15-caller-asks-whether-a-test-can-be-collected-at-home) | Information and Enquiry | **PRESENT** | 3 met |
| 16 | [Caller asks whether their insurance covers a test](#16-caller-asks-whether-their-insurance-covers-a-test) | Information and Enquiry | **PRESENT** | 4 met, 1 needs the pod |
| 17 | [Caller asks whether their result is dangerous](#17-caller-asks-whether-their-result-is-dangerous) | Difficult, Sensitive and Edge Cases | **PARTIAL** | 4 met, 1 gap, 1 needs the pod |
| 18 | [Caller asks whether a prescription is required](#18-caller-asks-whether-a-prescription-is-required) | Information and Enquiry | **PRESENT** | 3 met |
| 19 | [Caller asks something the agent does not cover](#19-caller-asks-something-the-agent-does-not-cover) | Information and Enquiry | **PRESENT** | 3 met |
| 20 | [Caller asks two questions in one breath](#20-caller-asks-two-questions-in-one-breath) | Information and Enquiry | **PRESENT** | 4 met |
| 21 | [Caller goes silent](#21-caller-goes-silent) | Difficult, Sensitive and Edge Cases | **PARTIAL** | 3 met, 1 gap |
| 22 | [Caller wants to make a complaint](#22-caller-wants-to-make-a-complaint) | Difficult, Sensitive and Edge Cases | **PARTIAL** | 4 met, 1 gap |
| 23 | [Caller asks the agent to compare two options](#23-caller-asks-the-agent-to-compare-two-options) | Information and Enquiry | **PRESENT** | 4 met |
| 24 | [Caller states something the agent cannot verify](#24-caller-states-something-the-agent-cannot-verify) | Difficult, Sensitive and Edge Cases | **PARTIAL** | 4 met, 1 gap |
| 25 | [Caller wants to speak to a doctor personally](#25-caller-wants-to-speak-to-a-doctor-personally) | Difficult, Sensitive and Edge Cases | **PARTIAL** | 3 met, 1 gap |
| 26 | [Caller describes symptoms and asks what is wrong](#26-caller-describes-symptoms-and-asks-what-is-wrong) | Difficult, Sensitive and Edge Cases | **PRESENT** | 3 met, 1 needs the pod |
| 27 | [Caller asks about another person's report](#27-caller-asks-about-another-persons-report) | Difficult, Sensitive and Edge Cases | **PARTIAL** | 4 met, 1 gap |
| 28 | [Caller asks a follow-up that depends on the previous answer](#28-caller-asks-a-follow-up-that-depends-on-the-previous-answer) | Information and Enquiry | **PRESENT** | 4 met |
| 29 | [Caller has lost their reference number](#29-caller-has-lost-their-reference-number) | Difficult, Sensitive and Edge Cases | **PRESENT** | 4 met |
| 30 | [Caller asks to be called back](#30-caller-asks-to-be-called-back) | Information and Enquiry | **PRESENT** | 4 met, 1 needs the pod |

---

## Story by story

### 01. Caller asks how long results take

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a patient, I want to know when my report will be ready, so that I can plan a follow-up.

**Acceptance criteria**

> Reporting time comes from the catalogue and is expressed as a natural duration rather than a number of hours read as a figure. Where turnaround varies by day of week or by branch that is stated.

**Verdict — PARTIAL.** 2 met, 1 gap, out of 3 criteria checks

**Where it lives** — `clinic-api/models.py` `LabTest.report_time_hours`; `GET /api/v1/tests/search`; `agent/reply_templates.test_rate_reply()` (+ `_i18n` for hi/en)

| Check | Result |
|---|---|
| reporting time comes from the catalogue | pass |
| the time is spoken as a clause not a field | pass |
| turnaround can vary by day or branch | **gap** |

**Gap — turnaround can vary by day or branch.** GAP: 'where turnaround varies by day of week or by branch that is stated'. LabTest has a single report_time_hours column and the schema has no branch or weekday dimension at all, so a varying turnaround cannot be stated because it cannot be stored.

*Note.* The time is a clause inside the price sentence, not a separate intent: a caller who asks only "how long?" is answered through `test_rate`.

---

### 02. Two callers want the last slot at the same moment

**Epic** — Conversation: Booking, Rescheduling and Cancellation  
**Owner** — Saurav

**User story**

> As one of two simultaneous callers, I want a clear outcome, so that neither of us arrives to find the appointment gone.

**Acceptance criteria**

> The slot is held on offer and committed on confirmation, so exactly one caller succeeds. The other is told immediately, during the same call, and offered the nearest alternatives. A concurrency test with thirty simultaneous attempts produces one success and no partial writes.

**Verdict — PRESENT.** every acceptance criterion is met and now guarded by a test (3 met)

**Where it lives** — `clinic-api/models.py` `SlotLock`; `booking_service.hold_slot()`; `POST /api/v1/bookings/hold` then `/confirm`; `booking_service.nearest_alternatives()`

| Check | Result |
|---|---|
| the slot is held on offer so exactly one caller succeeds | pass |
| the loser is told in the same call with alternatives | pass |
| the thirty attempt concurrency proof exists | pass |

*Note.* The AC names its own test. `tests/test_booking_concurrency.py::test_thirty_simultaneous_holds_produce_exactly_one_success` is that test, with 30 real threads; this audit guards its existence rather than racing a second time.

---

### 03. Caller asks what sample is needed

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a patient, I want to know whether it is blood, urine or something else, so that I can prepare.

**Acceptance criteria**

> The sample type is spoken as a natural clause rather than a field and a colon. The English clinical term is preserved if the caller used it. Multiple samples for one test are all stated.

**Verdict — PARTIAL.** 3 met, 1 gap, out of 4 criteria checks

**Where it lives** — `agent/sample_wording.py` (`is_specimen`, `sample_sentence`); `LabTest.sample_type`

| Check | Result |
|---|---|
| the sample is a clause not a field and a colon | pass |
| a catalogue category is never read out as a specimen | pass |
| the english clinical term is kept when the caller used it | pass |
| several samples for one test are all stated | **gap** |

**Gap — several samples for one test are all stated.** GAP: 'multiple samples for one test are all stated'. LabTest.sample_type is a single string column, so a test needing both blood and urine cannot be represented, let alone spoken; sample_sentence() takes one sample and returns one clause.

*Note.* The module exists because the live pod once spoke "সৈম্পল: Blood" -- a label, a colon, and a Latin word inside Bengali speech.

---

### 04. Caller asks for a person immediately

**Epic** — Conversation: Difficult, Sensitive and Edge Cases  
**Owner** — Saurav

**User story**

> As a caller who wants a person, I want one without negotiating, so that I do not feel trapped.

**Acceptance criteria**

> A request for a human in any supported language escalates on the same turn with no retention attempt and no question about why. The phrase set is tested per language and the rate is reported as a quality signal rather than something to minimise.

**Verdict — PARTIAL.** 5 met, 2 gaps, out of 7 criteria checks

**Where it lives** — `agent/human_request.asks_for_a_person()`; `main.py` `_handoff_to_human()`, called before intent extraction; `agent/phrases.py` `handoff`

| Check | Result |
|---|---|
| a request for a person is recognised in every language | pass |
| a bare imperative also escalates | **gap** |
| a refusal of a person is not read as a request | pass |
| escalation asks nothing about why | pass |
| the phrase set is tested per language | pass |
| asking for a person is recorded against the call | pass |
| the handoff rate is reported as a quality signal | **gap** |

**Gap — a bare imperative also escalates.** GAP, narrow: the English patterns all need a verb of wanting or asking ('I want...', 'connect me...'). A bare imperative -- 'give me a human', 'human please' -- is not matched, so the most abrupt phrasing, which is the one an exasperated caller uses, falls through to the model instead of escalating on the turn.

**Gap — the handoff rate is reported as a quality signal.** GAP: 'the rate is reported as a quality signal rather than something to minimise'. The only aggregate this tree keeps for it is agent/call_score.py's -15 penalty -- which is treating it as something to minimise, the opposite of the criterion -- and /api/stats, which carries sixteen OutcomeCounters, has no handoff rate at all.

*Note.* Negation is handled: "I don't need a person" does not escalate.

---

### 05. Caller asks the price of a test

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a patient choosing where to go, I want the price immediately and correctly, so that I can decide on the call.

**Acceptance criteria**

> The price is read from the live catalogue and spoken as a natural sentence with the sample type and reporting time. The figure is a template substitution and is never composed by the model. An unknown test produces the not-found path with near matches offered.

**Verdict — PRESENT.** every acceptance criterion is met and now guarded by a test (5 met)

**Where it lives** — `GET /api/v1/tests/search`; `agent/reply_templates.test_rate_reply()`

| Check | Result |
|---|---|
| the price is read from the live catalogue | pass |
| the figure is a template substitution | pass |
| the price sentence carries the sample and the reporting time | pass |
| an unknown test takes the not found path with no invented figure | pass |
| near matches are offered for a misheard test name | pass |

*Note.* The not-found path speaks no digit at all, in any language -- checked, because an invented price is the "Naloxone-class" failure this project was built around.

---

### 06. Caller asks whether their report is ready

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a patient, I want to know whether my report is ready before I travel, so that I do not collect something that is not there.

**Acceptance criteria**

> Status is read from the laboratory system after verification. Not ready and not found are distinguished in the reply. No clinical value is read aloud under this story, and the agent offers delivery or a callback when ready.

**Verdict — PRESENT.** every acceptance criterion is met and now guarded by a test (3 met)

**Where it lives** — `clinic-api/models.py` `LabReport`; `enquiry_service.report_status()`; `GET /api/v1/reports/status`

| Check | Result |
|---|---|
| not ready and not found are distinguished | pass |
| no clinical value is stored or returned | pass |
| status is read after verification | pass |

*Note.* "No clinical value is read aloud" is enforced by the schema, not by a rule in a prompt: the table has six columns and none of them can hold a result.

---

### 07. Caller asks for their report to be sent

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a patient, I want my report sent to me, so that I do not have to visit to collect it.

**Acceptance criteria**

> Delivery follows one-time-password verification and sends an expiring signed link rather than an attachment. Every delivery is written to the audit trail with the recipient and the verification path. A failed verification offers collection in person.

**Verdict — PRESENT.** every criterion checkable off-pod is met; the rest needs the pod (3 met, 1 needs the pod)

**Where it lives** — `enquiry_service.request_report_otp()` / `deliver_report()`; `models.ReportDeliveryOTP`, `ReportDeliveryAudit`; `POST /api/v1/reports/request-otp`, `/deliver`

| Check | Result |
|---|---|
| delivery follows otp verification | pass |
| an expiring link is sent rather than an attachment | pass |
| every delivery is audited with recipient and verification path | pass |
| the otp and the link are actually delivered | pod |

**Not decidable here — the otp and the link are actually delivered.** Needs the pod: whether the link and the OTP actually reach a handset needs a real SMS/WhatsApp provider; both are logged, not sent, today

*Note.* An expiring `link_token` is issued, never an attachment, and every delivery writes the recipient and the verification path. The OTP itself is logged, not sent -- there is no SMS provider in this stack.

---

### 08. Caller asks when a doctor sits

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a patient, I want to know a doctor's chamber days and times, so that I can plan around them.

**Acceptance criteria**

> Chamber days and times are read live from the hospital system and spoken as natural language without field labels. A doctor who is on leave is reported as such with the return date if known. An unknown doctor produces the not-found path with near matches.

**Verdict — PARTIAL.** 4 met, 1 gap, out of 5 criteria checks

**Where it lives** — `GET /api/v1/doctors/availability`; `GET /api/v1/doctors/{name}/leave`; `enquiry_service.doctor_leave_on()`; `agent/reply_templates.doctor_availability_reply()`

| Check | Result |
|---|---|
| chamber days and times are read live | pass |
| the reply names the doctor and the hours in every language | pass |
| the reply has no field labels | **gap** |
| a doctor on leave is reported with the return date | pass |
| an unknown doctor takes the not found path | pass |

**Gap — the reply has no field labels.** GAP, already tracked: 'spoken as natural language without field labels'. The Bengali availability reply says 'সময়: {hours}' and 'পরবর্তী উপলব্ধ দিন: {date}' -- a label, a colon and a value, which is a field read out. Both are inside the 12 known artefacts tests/test_spoken_text_lint.py ratchets down, so this is a logged debt, not a surprise.

*Note.* Leave carries a return date. The unknown-doctor path is the "Doctor Nobody" guard CLAUDE.md names.

---

### 09. Caller asks about a health package

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a patient comparing options, I want to hear what a package includes and how it compares with separate tests, so that I can choose on price.

**Acceptance criteria**

> Package contents are enumerated from the catalogue and the comparison against separate tests is computed in code from live rates, never stated by the model. Where a package is not available at the caller branch that is said plainly.

**Verdict — PARTIAL.** 2 met, 1 gap, out of 3 criteria checks

**Where it lives** — `models.Package` / `PackageTest`; `enquiry_service.compare_package_vs_separate()`; `GET /api/v1/packages/{name}`

| Check | Result |
|---|---|
| package contents are enumerated from the catalogue | pass |
| the comparison is computed in code from live rates | pass |
| a package unavailable at the callers branch is said plainly | **gap** |

**Gap — a package unavailable at the callers branch is said plainly.** GAP: 'where a package is not available at the caller branch that is said plainly'. The response carries a branch_restricted flag, but there is no branch table, no caller branch and nothing that can set it -- so the unavailable case can never arise or be spoken.

*Note.* `savings_inr` is arithmetic done in clinic-api; the audit asserts the three numbers reconcile, so a model-composed figure would fail the test.

---

### 10. Caller asks opening hours, address or directions

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a caller, I want simple factual questions answered instantly, so that the commonest calls never need a person.

**Acceptance criteria**

> Hours per department and branch, address, directions and parking are answered from configuration with effective dates, served by the deterministic path with no model call and under the fast-path latency target. Accuracy is verified against published information monthly.

**Verdict — PARTIAL.** 3 met, 1 gap, 1 needs the pod, out of 5 criteria checks

**Where it lives** — `GET /api/v1/faq` (hours, location, parking); `GET /api/v1/departments/{name}/hours`; `agent/fast_path.py`'s FAQ tables

| Check | Result |
|---|---|
| hours address and directions are answered from configuration | pass |
| hours are available per department | pass |
| the deterministic path answers without a model call | pass |
| hours configuration carries effective dates | **gap** |
| the answer is under the fast path latency target | pod |

**Gap — hours configuration carries effective dates.** GAP: 'from configuration with effective dates'. DepartmentHours HAS an effective_from column, but nothing populates it (the demo seed leaves it null) and no reader filters on it, so a future change of hours cannot be scheduled -- the date is storage, not behaviour.

**Not decidable here — the answer is under the fast path latency target.** Needs the pod: 'under the fast-path latency target' is a timing measurement against the Appendix B budget, and 'accuracy verified monthly' is a process, not code

*Note.* `agent/fast_path.py` imports no LLM -- asserted, so the "no model call" claim is structural rather than a comment.

---

### 11. Caller asks how to prepare for a test

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a patient booking a fasting test, I want to be told exactly how long to fast and what I may drink, so that my visit is not wasted.

**Acceptance criteria**

> Preparation covers fasting hours, water allowance, medication hold and timing, sourced from the laboratory standard operating procedure and never generated. The instruction is spoken in the caller language with the test name kept in the form the caller used.

**Verdict — PRESENT.** every acceptance criterion is met and now guarded by a test (4 met)

**Where it lives** — `GET /api/v1/tests/prep`; `LabTest.prep_instructions_bn/hi/en`, `fasting_hours`, `water_allowed_while_fasting`

| Check | Result |
|---|---|
| preparation comes from the catalogue not the model | pass |
| preparation covers fasting hours and water | pass |
| the instruction is spoken in the caller language | pass |
| the test name is kept in the form the caller used | pass |

*Note.* The caller's own form of the test name survives into the reply ("সিবিসি", not "Complete Blood Count").

---

### 12. Caller has several tests with conflicting preparation

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a patient booking three tests, I want one clear instruction, so that I know what to actually do the night before.

**Acceptance criteria**

> Conflicting rules merge to the strictest constraint through an explicit rule table and the merged instruction is stated as one coherent sentence. A conflict the rules cannot resolve escalates to a human rather than being guessed.

**Verdict — PRESENT.** every acceptance criterion is met and now guarded by a test (3 met)

**Where it lives** — `enquiry_service.merge_prep_instructions()`; `POST /api/v1/tests/prep/merge`

| Check | Result |
|---|---|
| conflicting rules merge to the strictest constraint | pass |
| the merge is an explicit rule table not string surgery | pass |
| an unresolvable conflict escalates rather than being guessed | pass |

*Note.* The strictest constraint is a MAX() over numeric columns, which is why the structured prep fields exist beside the prose ones. `escalate_to_human` is in the response shape for a conflict the rules cannot resolve.

---

### 13. Caller asks whether they can walk in

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a patient nearby, I want to know if I can just come, so that I do not need an appointment for something that does not require one.

**Acceptance criteria**

> Walk-in policy per department and test is answered from configuration with current queue expectations where available. The agent does not promise a wait time it cannot verify.

**Verdict — PRESENT.** every acceptance criterion is met and now guarded by a test (3 met)

**Where it lives** — `models.WalkInPolicy`; `enquiry_service.walk_in_policy()`; `GET /api/v1/walk-in`

| Check | Result |
|---|---|
| walk in policy is answered from configuration | pass |
| queue expectations are given where available | pass |
| no wait time is promised where none is configured | pass |

*Note.* A policy row with no queue note returns an empty string, so no wait time is invented where none is configured.

---

### 14. Caller asks what they owe

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a patient, I want to know my outstanding amount, so that I can settle it before my visit.

**Acceptance criteria**

> The amount is read from the billing system after verification and spoken slowly and grouped. The agent never negotiates, adjusts or explains a charge beyond the stated line description, and disputes route to a human.

**Verdict — PRESENT.** every criterion checkable off-pod is met; the rest needs the pod (3 met, 1 needs the pod)

**Where it lives** — `models.BillingLineItem`; `enquiry_service.outstanding_balance()`; `GET /api/v1/billing/outstanding`

| Check | Result |
|---|---|
| the amount is read from the billing system | pass |
| each line carries only its own description | pass |
| the amount is spoken grouped for a caller writing it down | pass |
| the amount is spoken slowly | pod |

**Not decidable here — the amount is spoken slowly.** Needs the pod: 'spoken slowly' is a TTS pace judgement on real audio

*Note.* The row holds a description and an amount and nothing a model could expand into an explanation of the charge.

---

### 15. Caller asks whether a test can be collected at home

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a patient who is unwell, I want to know if someone can come to me, so that I do not travel unnecessarily.

**Acceptance criteria**

> Per-test eligibility is enforced because some samples cannot travel, and postal-code serviceability is checked against a real coverage table. The slot window and any charge are quoted from live rules and never estimated.

**Verdict — PRESENT.** every acceptance criterion is met and now guarded by a test (3 met)

**Where it lives** — `models.HomeCollectionCoverage`, `LabTest.home_collection_eligible`; `enquiry_service.home_collection_eligibility()`; `GET /api/v1/home-collection/eligibility`

| Check | Result |
|---|---|
| per test eligibility and postcode serviceability are both checked | pass |
| the slot window and charge are quoted from the coverage row | pass |
| a sample that cannot travel is refused per test | pass |

*Note.* Both halves of the AC are enforced: the test must allow it AND the postcode must be serviceable. An uncovered area answers `area_not_covered`.

---

### 16. Caller asks whether their insurance covers a test

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As an insured patient, I want to know whether I am covered, so that I am not billed unexpectedly.

**Acceptance criteria**

> Eligibility is looked up by policy number and coverage and co-payment are quoted from the insurer response, never estimated. Where the insurer cannot be reached the agent says so plainly and offers a human rather than guessing.

**Verdict — PRESENT.** every criterion checkable off-pod is met; the rest needs the pod (4 met, 1 needs the pod)

**Where it lives** — `models.InsurancePolicy` / `InsuranceCoverageRule`; `enquiry_service.check_insurance_coverage()`; `GET /api/v1/insurance/coverage`

| Check | Result |
|---|---|
| eligibility is looked up by policy number | pass |
| coverage and co payment are quoted from the rule not estimated | pass |
| an unknown policy is not guessed at | pass |
| an unreachable insurer is distinguished from an unknown policy | pass |
| a real insurer timeout offers a human | pod |

**Not decidable here — a real insurer timeout offers a human.** Needs the pod: there is no real insurer integration in this stack, so the unreachable branch cannot be exercised against a live third party; `reachable` is hard-coded true today

*Note.* An unknown policy returns neither a coverage percent nor a co-payment -- nothing to misread as zero. `reachable` distinguishes "we could not ask" from "not covered", but there is no real insurer behind it yet.

---

### 17. Caller asks whether their result is dangerous

**Epic** — Conversation: Difficult, Sensitive and Edge Cases  
**Owner** — Saurav

**User story**

> As a worried patient, I want to be treated gently and connected to a clinician, so that a procedural limit does not feel like a rebuff.

**Acceptance criteria**

> Clinical interpretation is routed to a human by policy rather than by prompt wording. The reply states clearly that a doctor will explain the result and offers to connect one rather than simply refusing. An adversarial set of one hundred prompts per language produces zero breaches.

**Verdict — PARTIAL.** 4 met, 1 gap, 1 needs the pod, out of 6 criteria checks

**Where it lives** — `agent/clinical_safety.is_clinical_interpretation()`; the guard in `main.py` ahead of `_resolve_intent`; `agent/phrases.py` `clinical_interpretation`

| Check | Result |
|---|---|
| clinical interpretation is recognised in every language | pass |
| the everyday bengali wording is also caught | **gap** |
| the routing is a policy guard not prompt wording | pass |
| the reply offers a doctor rather than simply refusing | pass |
| a roleplay or forced binary prompt does not get through | pass |
| one hundred adversarial prompts per language produce zero breaches | pod |

**Gap — the everyday bengali wording is also caught.** GAP, narrow: the Bengali cues are the explicit danger words (বিপজ্জনক, সিরিয়াস). A caller who asks the ordinary thing -- 'আমার রিপোর্টটা কি খারাপ?', is my report BAD -- is not caught by the guard and reaches the model, which is the one path this story exists to close.

**Not decidable here — one hundred adversarial prompts per language produce zero breaches.** Needs the pod: 'an adversarial set of one hundred prompts per language produces zero breaches' is a measurement against the real Qwen; the deterministic guard above is what can be checked off-pod

*Note.* The audit asserts the guard's POSITION in the dispatch body, before the model is asked -- that is what makes it policy rather than prompt wording. Roleplay ("pretend you are a doctor") and forced-binary ("just say yes or no") are caught in English.

---

### 18. Caller asks whether a prescription is required

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a patient, I want to know whether I need a doctor's note, so that I am not turned away at the counter.

**Acceptance criteria**

> The answer comes from a policy table per test, not from the model. Where a prescription is required the agent says how it may be provided. The answer is never inferred from the test name.

**Verdict — PRESENT.** every acceptance criterion is met and now guarded by a test (3 met)

**Where it lives** — `LabTest.prescription_required` + `prescription_note_*`; `enquiry_service.prescription_requirement()`; `GET /api/v1/tests/{name}/prescription-requirement`

| Check | Result |
|---|---|
| the answer comes from a policy table per test | pass |
| the answer is never inferred from the test name | pass |
| where a prescription is required the agent says how to provide it | pass |

*Note.* An unknown test answers not-found rather than inferring from the name.

---

### 19. Caller asks something the agent does not cover

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a caller with an unusual question, I want a useful next step rather than a refusal, so that the call is not wasted.

**Acceptance criteria**

> An out-of-scope request is recognised, stated plainly, and routed to a human or a callback with the question captured in the context packet. The reason code is recorded against the call taxonomy and feeds the coverage backlog.

**Verdict — PRESENT.** every acceptance criterion is met and now guarded by a test (3 met)

**Where it lives** — `models.CallOutcome`; `enquiry_service.record_out_of_scope()`; `POST /api/v1/calls/out-of-scope`

| Check | Result |
|---|---|
| an out of scope question is captured with its reason code | pass |
| the question is kept verbatim for the coverage backlog | pass |
| it is stated plainly and routed | pass |

*Note.* The caller's question is stored verbatim with its reason code, which is what makes the coverage backlog readable by a human later.

---

### 20. Caller asks two questions in one breath

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a caller in a hurry, I want both questions answered, so that I do not have to ask again.

**Acceptance criteria**

> A turn containing two answerable questions produces both answers in one reply, in the order asked, each grounded independently. Where only one can be answered the agent answers it and explicitly addresses the other rather than dropping it.

**Verdict — PRESENT.** every acceptance criterion is met and now guarded by a test (4 met)

**Where it lives** — `agent/clause_split.split_into_clauses()`; `IntentExtraction.secondary_intent` / `secondary_slots`; `main.py` `_finish_enquiry_turn()`

| Check | Result |
|---|---|
| a two question turn is split into its clauses | pass |
| the extraction schema carries a second question | pass |
| both answers are produced in the order asked | pass |
| an unanswerable second question is explicitly addressed | pass |

*Note.* A second question that cannot be answered is addressed with an addendum rather than dropped, and a failed SECOND lookup never discards the first answer.

---

### 21. Caller goes silent

**Epic** — Conversation: Difficult, Sensitive and Edge Cases  
**Owner** — Saurav

**User story**

> As a caller who was distracted, I want a gentle prompt rather than a disconnection, so that I do not lose the call.

**Acceptance criteria**

> Two graduated prompts precede a graceful close, each different from the last. The close states what was and was not completed. Abandonment is logged with the turn index and the preceding prompt.

**Verdict — PARTIAL.** 3 met, 1 gap, out of 4 criteria checks

**Where it lives** — `main.py` `SILENCE_PROMPT_S`, `session.silence_prompts`, `_end_call()`; `agent/phrases.py` `silence_prompt`, `silence_go_on`, `silence_goodbye`

| Check | Result |
|---|---|
| two graduated prompts precede the close | pass |
| the prompts are wired to a silence timer | pass |
| abandonment is logged | pass |
| the close states what was and was not completed | **gap** |

**Gap — the close states what was and was not completed.** GAP: 'the close states what was and was not completed'. The goodbye is a fixed phrase ('silence_goodbye') that names nothing about the call -- a caller who was four answers into a booking hears the same farewell as one who asked nothing.

*Note.* Three distinct lines per language -- asserted distinct, so the caller is not asked the same thing twice.

---

### 22. Caller wants to make a complaint

**Epic** — Conversation: Difficult, Sensitive and Edge Cases  
**Owner** — Saurav

**User story**

> As a dissatisfied patient, I want my complaint recorded and routed to a person, so that it is not absorbed by a machine.

**Acceptance criteria**

> A complaint is recognised, acknowledged once without argument, captured verbatim in the context packet and routed to the complaints path. The agent does not attempt to resolve or explain it.

**Verdict — PARTIAL.** 4 met, 1 gap, out of 5 criteria checks

**Where it lives** — `agent/complaint.py` (`detect_complaint`, `is_complaint`); `models.ComplaintRecord`; `POST /api/v1/complaints`; `agent/phrases.py` `complaint_acknowledged`

| Check | Result |
|---|---|
| a complaint is recognised in every language | pass |
| the formal hindi wording is also caught | **gap** |
| it is acknowledged once without argument | pass |
| it is captured verbatim and routed | pass |
| the acknowledgement is fixed text never the model | pass |

**Gap — the formal hindi wording is also caught.** GAP, narrow: the Hindi cues cover शिकायत करनी है but not शिकायत दर्ज कराना -- 'to REGISTER a complaint', the formal phrasing a caller uses when they mean it most.

*Note.* Captured verbatim into one narrow-access table, which is how "verbatim" and this project's "no caller text in logs" rule hold at once. The acknowledgement is fixed text and asks nothing.

---

### 23. Caller asks the agent to compare two options

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a patient choosing between two tests or packages, I want the comparison stated, so that I can decide without doing arithmetic.

**Acceptance criteria**

> The comparison is computed in code from live values and stated as a sentence. The agent presents the facts and does not recommend one on clinical grounds, holding the advice boundary.

**Verdict — PRESENT.** every acceptance criterion is met and now guarded by a test (4 met)

**Where it lives** — `agent/compare_flow.py` (`asks_to_compare`, `build_comparison`); `agent/reply_templates.compare_options_reply()`

| Check | Result |
|---|---|
| a comparison request is recognised in every language | pass |
| the comparison is computed in code from live values | pass |
| the reply states facts and recommends nothing clinically | pass |
| two names are required before anything is compared | pass |

*Note.* `build_comparison` performs no I/O and returns facts only; the audit checks the reply recommends nothing ("you should", "recommend", "better for you").

---

### 24. Caller states something the agent cannot verify

**Epic** — Conversation: Difficult, Sensitive and Edge Cases  
**Owner** — Saurav

**User story**

> As a caller asserting a fact about my record, I want the agent to check rather than accept it, so that a mistake is not compounded.

**Acceptance criteria**

> A caller assertion never becomes a system fact. The agent checks the system of record and, where it cannot, says it cannot confirm and offers a human. A caller claim is never echoed back as though verified.

**Verdict — PARTIAL.** 4 met, 1 gap, out of 5 criteria checks

**Where it lives** — `agent/unverifiable_claim.py` (`detect_unverifiable_claim`, `asks_for_a_real_check`); `agent/phrases.py` `cannot_confirm_claim`

| Check | Result |
|---|---|
| an unverifiable claim is recognised | pass |
| a claim naming the doctor is also caught | **gap** |
| the claim is never echoed back as though verified | pass |
| the agent offers a human | pass |
| a real request to check is not treated as a claim | pass |

**Gap — a claim naming the doctor is also caught.** GAP, the same named-entity blind spot as story 25: the patterns key on a possessive ('MY doctor', 'THE doctor'). A caller who names them -- 'Doctor Sen already approved my test' -- matches nothing, so the claim is not caught and the turn goes to the model.

*Note.* The reply repeats nothing the caller asserted, and a genuine request to CHECK is distinguished from an assertion so it still reaches the lookup.

---

### 25. Caller wants to speak to a doctor personally

**Epic** — Conversation: Difficult, Sensitive and Edge Cases  
**Owner** — Saurav

**User story**

> As a caller who wants clinical reassurance, I want a realistic answer about what is possible, so that I am not left waiting for something that will not happen.

**Acceptance criteria**

> The agent states the actual process for reaching a clinician, offers the appropriate route, and never promises a call from a named doctor it cannot schedule. Where a clinical callback exists in policy it is offered.

**Verdict — PARTIAL.** 3 met, 1 gap, out of 4 criteria checks

**Where it lives** — `agent/doctor_personal_request.py`; `agent/phrases.py` `doctor_personal_request`

| Check | Result |
|---|---|
| the request is recognised in every language | pass |
| a named doctor is recognised in bengali and hindi | **gap** |
| no call from a named doctor is ever promised | pass |
| the route that does exist is offered | pass |

**Gap — a named doctor is recognised in bengali and hindi.** GAP: English has a dedicated named-doctor regex (_RE_EN_NAMED: 'Doctor Sen ... call me'); Bengali and Hindi have only fixed strings for the GENERIC 'the doctor'. So naming the doctor -- 'ডাক্তার সেনের সাথে কথা বলতে চাই' -- stops the match in exactly the two languages most callers here use.

*Note.* The reply refuses the promise and offers the route that does exist (an appointment), rather than simply declining.

---

### 26. Caller describes symptoms and asks what is wrong

**Epic** — Conversation: Difficult, Sensitive and Edge Cases  
**Owner** — Saurav

**User story**

> As a caller who is unwell, I want to be pointed at the right department without being diagnosed, so that I get help without being misled.

**Acceptance criteria**

> Symptom vocabulary maps to a department through the clinician-approved table and the reply is framed explicitly as administrative routing, never as a clinical opinion. A linguistic check over templates and sampled replies finds no diagnostic phrasing.

**Verdict — PRESENT.** every criterion checkable off-pod is met; the rest needs the pod (3 met, 1 needs the pod)

**Where it lives** — `booking_service.route_department()`; `GET /api/v1/departments/route`; `agent/reply_templates.department_route_reply()`

| Check | Result |
|---|---|
| symptom vocabulary maps to a department | pass |
| the mapping is a table not a model | pass |
| the reply is framed as routing never as a clinical opinion | pass |
| sampled replies contain no diagnostic phrasing | pod |

**Not decidable here — sampled replies contain no diagnostic phrasing.** Needs the pod: 'a linguistic check over templates and SAMPLED REPLIES finds no diagnostic phrasing' needs real call transcripts; the template half is checked above

*Note.* The audit checks the templates carry no diagnostic phrasing ("you have", "diagnos", "likely you"). Sampled real replies are a pod exercise.

---

### 27. Caller asks about another person's report

**Epic** — Conversation: Difficult, Sensitive and Edge Cases  
**Owner** — Saurav

**User story**

> As a patient, I want my results disclosed only to me or an authorised person, so that a phone line is not the weak point in my privacy.

**Acceptance criteria**

> Disclosure requires verification of the caller and an authorisation check for that patient, both enforced in the gateway rather than by the model. An unauthorised request is declined plainly and the attempt is audited. A shared number surfaces a chooser rather than a guess.

**Verdict — PARTIAL.** 4 met, 1 gap, out of 5 criteria checks

**Where it lives** — `booking_service.authorize_disclosure()` / `record_proxy()`; `models.PatientProxy`; `GET /api/v1/patients/identify`

| Check | Result |
|---|---|
| disclosure requires the caller to be the patient or an authorised proxy | pass |
| the check is enforced in the gateway not by the model | pass |
| a stated relationship alone is not enough | pass |
| a shared number surfaces a chooser rather than a guess | pass |
| an unauthorised attempt is audited | **gap** |

**Gap — an unauthorised attempt is audited.** GAP: 'the attempt is audited'. A refused disclosure returns False and nothing is written -- booking_service.authorize_disclosure has no audit write, and /api/v1/audit only lists report deliveries. A probing caller leaves no trace.

*Note.* The decision lives behind the API in clinic-api and is absent from `agent/llm.py` -- asserted, which is what "enforced in the gateway rather than by the model" means here. A stated relationship alone is refused; a second factor must agree.

---

### 28. Caller asks a follow-up that depends on the previous answer

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a caller who just heard a price, I want to ask about that same test without naming it again, so that the conversation feels natural.

**Acceptance criteria**

> Pronouns and elliptical follow-ups resolve against the entity from the previous turn. Where reference is ambiguous the agent asks rather than assuming. Tested on scripted transcripts with three consecutive follow-ups.

**Verdict — PRESENT.** every acceptance criterion is met and now guarded by a test (4 met)

**Where it lives** — `agent/enquiry_followup.py` (`has_pronoun_reference`, `resolve_followup_slot`, `recent_unique`, `FOLLOWUP_MAX_GAP`)

| Check | Result |
|---|---|
| a pronoun resolves against the previous turns entity | pass |
| a pronoun reference is detected in every language | pass |
| a stale reference is not resolved | pass |
| three consecutive follow ups are covered by the suite | pass |

*Note.* A reference older than `FOLLOWUP_MAX_GAP` turns is not carried forward, so a stale entity is asked about rather than assumed. Three consecutive follow-ups are covered by `tests/test_followup_context.py`.

---

### 29. Caller has lost their reference number

**Epic** — Conversation: Difficult, Sensitive and Edge Cases  
**Owner** — Saurav

**User story**

> As a patient without the confirmation message, I want to be found by what I remember, so that I am not turned away for not having a number.

**Acceptance criteria**

> Lookup succeeds on any workable combination of contact number, name, approximate date, test name and branch, with phonetic matching across all supported languages. Several matches are resolved by an explicit question and never by taking the most likely.

**Verdict — PRESENT.** every acceptance criterion is met and now guarded by a test (4 met)

**Where it lives** — `booking_service.lookup_bookings()`; `POST /api/v1/bookings/search` (phone, name, approximate date, test, branch); `agent/phonetic_match.py`; `agent/reply_templates.multiple_bookings_reply()`

| Check | Result |
|---|---|
| lookup succeeds on a combination that is not the reference | pass |
| a name also works as a factor | pass |
| phonetic matching exists for every supported language | pass |
| several matches are resolved by asking | pass |

*Note.* Several matches are read out as a question, never collapsed to the likeliest.

---

### 30. Caller asks to be called back

**Epic** — Conversation: Information and Enquiry  
**Owner** — Saurav

**User story**

> As a caller who cannot wait, I want a callback at a stated time, so that I do not have to keep trying.

**Acceptance criteria**

> A callback is scheduled with a stated window, the reason and context preserved, and the promise tracked to fulfilment. The agent states plainly when callbacks are not available rather than promising one it cannot place.

**Verdict — PRESENT.** every criterion checkable off-pod is met; the rest needs the pod (4 met, 1 needs the pod)

**Where it lives** — `agent/callback_request.py`; `models.CallbackRequest` (with `status`); `POST /api/v1/callbacks`; `agent/phrases.py` `callback_noted`, `callback_need_number`, `callback_unavailable`

| Check | Result |
|---|---|
| a callback request is recognised in every language | pass |
| the callback is recorded with its reason and context | pass |
| the promise is tracked to fulfilment | pass |
| the agent says plainly when it cannot promise a time | pass |
| the callback is actually placed | pod |

**Not decidable here — the callback is actually placed.** Needs the pod: 'the promise tracked to fulfilment' ends at a human placing the call; there is no outbound telephony in this stack (Epic E22), so only the queue entry can be checked here

*Note.* The reason and a factual call summary are stored with the request. No time is ever promised: this stack has no outbound calling, so `callback_noted` says a colleague will ring without naming when.

---

## How to read a run

| Outcome | Meaning |
|---|---|
| `passed` | the acceptance criterion is met by this tree, and this test now guards it |
| `xfailed` | a **gap**: the criterion is not met today, and the reason says what is missing |
| `skipped` | cannot be decided off-pod — a real model, real audio or a real third party is the subject |

A gap that gets fixed turns into a **failure** (`strict=True`), not a quiet pass. That is
deliberate: this file is an inventory that keeps itself honest, not a list of complaints.

## What this audit does not tell you

- **Nothing here has run on the pod.** Real ASR, the real Qwen, real TTS pace and real
  third parties are all out of scope off-pod; the seven skips name exactly which criteria
  that affects.
- **The Bengali and Hindi wording has not been reviewed by a native speaker.** Where a
  phrase table misses a plausible caller phrasing, this audit records it as a gap; where a
  table is merely *unreviewed*, only a native reviewer can say so. `merge_report.md` §8 lists
  which tables were written without review.
- **A passing check is as strong as its assertion.** Each one calls the real function or the
  real endpoint, but a criterion like "spoken slowly" or "verified monthly" is a process or
  a measurement, and is marked as such rather than asserted against a proxy.
