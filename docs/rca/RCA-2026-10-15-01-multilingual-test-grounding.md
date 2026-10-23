# Root Cause Analysis Report

**Incident/Bug ID:** RCA-2026-10-15-01
**Title:** Wrong test priced, tests dropped or never recognised, and dead-end re-asks in Bengali / English / Hindi test-price calls (27 annotated blocks, "Loop paragraph" test run)
**Date Identified:** 2026-10-15 (test printed 20:10 IST; pod `38.29.145.27` was already down when this analysis was written)
**Reported By:** Project owner, by a three-language manual test ("Loop paragraph.pdf", 27 numbered blocks with the owner's own intent notes)
**Component/Service:** `voice-pcm` (`main_pcm.py`, generated from `main.py`): ASR → `ground_tests` → read-back confirmation → price → TTS. Parts touched: `agent/entity_grounding.py`, `agent/fast_path.py` + `agent/form_index.py`, `clinic-api/i18n_content.py` / `seed.py` aliases, `agent/speech_norm.py`, `agent/channel_quality.py`, `agent/reask_policy.py`, `static/pcm/index.html`
**Severity:** **S1 (High)** for D1 + D2 (a wrong test's price was stated as the answer, twice, in Hindi). S2 for D3–D8. S3/S4 for the rest.
**Status:** Root causes established from code and offline reproduction. **No fix is implemented by this report.** One related feature ("both / all of them → summed price") was already fixed and deployed earlier the same day (D12).

> **How this was done.** The PDF's 10 pages were read in full (text and rendered images) and every user line was replayed through the real grounding code (`ground_tests`, `match_test_group`, `FormTable.best`) on a catalogue built from the live seed data. The offline output matches the live transcripts decision for decision (Appendix B). **What could not be done:** the pod is down, so logs, per-engine ASR agreement scores, the raw WAVs, and the real TTS audio were not available. Every claim about ASR (what the recogniser *heard*) is taken from the transcript; claims about *why* the recogniser heard it are marked **[hypothesis]**.

---

# PART A — READ THIS FIRST: every problem, with its conversation above its analysis

Each problem below has the same four parts, in this order:

1. **The conversation** — copied from your PDF (your words as the ASR wrote them, and what the agent said).
2. **What went wrong** — in plain words.
3. **Why it happened** — the root cause, with the numbers from the code.
4. **How to fix it** — the top 5 suggestions, best first (★ = what I would do).

Part B (further down) is the same findings in the formal RCA template.

## How the agent picks a test (needed to read the analyses)

```
you speak ──► ASR writes text ──► every word-window of that text is scored (0 to 1)
                                   against every known spoken name of every test
          ──► the best score must be 0.72 or more (COMMIT_FLOOR)
          ──► the agent reads it back:  "You want the price of X and Y, is that right?"
          ──► you say yes ──► the price of X and Y is spoken
```

Two things to remember:

* **Only the best score is used.** The code does not look at how close the second-best test was.
* **If you say the same set again after a read-back, the code treats that as "yes".** (`main.py:2352`)

Test-name scores quoted below come from running your exact words through the real code on the seeded clinic catalogue.

---

## PROBLEM 1 — Hindi: "vitamin B12" is taken as "vitamin D", and the wrong price is spoken

**Your note (4/5):** "I told vitamin b12 and taking as vitamin d". **Severity: S1 (a wrong price was stated).**

### The conversation (block 3, Hindi column)

```
[User] मलेरिया टेस्ट यूरिक्स टेस्ट और विटामिन ब ल्वेल्वे करना चाहता हूँ कितना लगेगा
[AI]   अच्छा, बताती हूँ।
[AI]   आप मलेरिया टेस्ट और विटामिन डी टेस्ट का दाम जानना चाहते हैं, ठीक है ना?
[User] मलेरिया टेस्ट यूरिक एसिड टेस्ट और विटामिन ब ल्वेल्वे टेस्ट
[AI]   आप मलेरिया टेस्ट, यूरिक एसिड और विटामिन डी टेस्ट का दाम जानना चाहते हैं, ठीक है ना?
[User] मलेरिया टेस्ट यूरिक एसिड टेस्ट और विटामिन बी ल्वेल्वे
[AI]   मलेरिया टेस्ट 400 रुपये, यूरिक एसिड 250 रुपये और विटामिन डी 1800 रुपये। सब मिलाकर कुल 2450 रुपये।
```

Same sentence in Bengali worked: `…ভিটামিন ব টেলভে…` → read back **ভিটামিন বি১২** → yes → 1850. In English the first try read back "uric acid and vitamin d" (see Problem 3).

### What went wrong

1. First read-back: **malaria** was kept, **uric acid was lost**, and **vitamin B12 became vitamin D**.
2. Second read-back: uric acid came back, but **vitamin D stayed**.
3. Third time you said the same set. The agent took that as "yes" and **priced Vitamin D (₹1800)**. The total ₹2450 is wrong. It should have been 400 + 250 + 1200 = ₹1850.

### Why it happened

| Step         | Fact (from code)                                                                                                                                                                                                 |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ASR          | The Hindi engine wrote "B twelve" as`ब ल्वेल्वे` / `बी ल्वेल्वे` (it never writes `बारह`)                                                                                           |
| Scores       | `विटामिन ब ल्वेल्वे` → **Vitamin D 0.84**, Vitamin B12 0.75. Both pass 0.72                                                                                                             |
| Rule         | Only the best is used. The margin was 0.09; the project's own constant`AMBIGUITY_MARGIN` says "under 0.20 is not something to pick between", but this path never checks it (`agent/entity_grounding.py:186`) |
| Aliases      | Hindi B12 aliases are only`विटामिन बी बारह` and `बी बारह` (`clinic-api/i18n_content.py:34`). Nothing for `बी ट्वेल्व` / `बी 12`                                          |
| Confirmation | Third attempt set == pending set → treated as yes (`main.py:2352`)                                                                                                                                            |

**Root causes: RC-1 (repeat = yes), RC-2 (no margin), RC-3 (alias gap).**

### Top-5 fixes

1. ★ A repeated set must still get an explicit "yes" (change one line at `main.py:2352`).
2. ★ Apply the ambiguity margin inside `extract_tests`; when two tests are within 0.2, ask "विटामिन डी या बी बारह?".
3. ★ Add Hindi aliases `विटामिन बी ट्वेल्व`, `बी ट्वेल्व`, `विटामिन बी 12`, `बी 12`.
4. Never price a test that was not in the last read-back and not explicitly confirmed (a runtime guard).
5. Ask the caller to say it differently on the 2nd failure ("बी, एक, दो" / "वह टेस्ट जो विटामिन बी का है").

---

## PROBLEM 2 — Hindi: "blood sugar" is taken as "blood group", and B12 as vitamin D again

**Your note (8):** "blood sugar taking as blood group and b12 taking as vitamin d". **Severity: S1.**

### The conversation (block 7, Hindi column)

```
[User] मैं आयरॉइड प्रोफाइले ब्लड शुगर और विटामिन ब ल्वेल्वे टेस्ट कराना चाहता हूँ कितना लगेगा
[AI]   अच्छा, बताती हूँ।
[AI]   आप थायराइड प्रोफाइल, ब्लड ग्रुप और विटामिन डी टेस्ट का दाम जानना चाहते हैं, ठीक है ना?
[User] मैं थायरॉइड प्रोफाइले ब्लड शुगर और विटामिन ब ल्वेल्वे का दाम जानना चाहता हूं
[AI]   थायराइड प्रोफाइल 700 रुपये, ब्लड ग्रुप 200 रुपये और विटामिन डी 1800 रुपये। सब मिलाकर कुल 2700 रुपये।
```

For comparison, the **Bengali** and **English** columns of the same block were right (`2020`):

```
[bn] সুগার পিপি 120 + থাইরয়েড প্রোফাইল 700 + ভিটামিন বি১২ 1200 = মোট 2020
[en] Thyroid Profile 700 + Blood Sugar PP 120 + Vitamin B12 1200 = 2020 rupees
```

### What went wrong

Three tests said, three read back, **two wrong** (Blood Group for Blood Sugar, Vitamin D for B12). You repeated the sentence once. The agent priced the wrong set: **₹2700 instead of ₹2020**.

### Why it happened

| Heard                                  | Best                                              | Second                   | Margin |
| -------------------------------------- | ------------------------------------------------- | ------------------------ | ------ |
| `ब्लड शुगर`                  | Blood Group`ब्लड ग्रुप` **0.74** | Blood Sugar Fasting 0.67 | 0.07   |
| `विटामिन ब ल्वेल्वे` | Vitamin D**0.84**                           | Vitamin B12 0.75         | 0.09   |

* The Hindi table has **no bare "ब्लड शुगर" alias**. Only `ब्लड शुगर फास्टिंग`, `शुगर फास्टिंग`, `शुगर पीपी`. The shared word `ब्लड` pulls it toward `ब्लड ग्रुप`.
* The phrase `ब्लड शुगर` is genuinely ambiguous (Fasting or PP). The correct behaviour is to ask, not pick.
* The same repeat-as-yes rule (RC-1) priced the repeat.

### Top-5 fixes

1. ★ Add bare `ब्लड शुगर`, `शुगर` to a *choice* alias that always asks "फास्टिंग या पीपी?".
2. ★ Margin rule (as Problem 1).
3. ★ Repeat is not a yes (as Problem 1).
4. Strip shared prefixes (`ब्लड`, `विटामिन`, `डेंगू`) before scoring, so the distinctive tail decides.
5. Put the runner-up in the read-back: "ब्लड ग्रुप — या ब्लड शुगर?".

---

## PROBLEM 3 — English: a test is silently dropped, and Banglish gets an English reply full of Bengali letters

**Your note (4):** "Accent is not matching, so shifted back to banglish but answer comes in english". **Severity: S2.**

### The conversation (block 3, English column)

```
[User] i want to do malariot uric acid test and vitamin vitual what will be the price
[AI]   Alright, let me tell you.
[AI]   You'd like the price of uric acid and vitamin d, is that right?
[User] ম্যালেরিয়া টেস্ট ইউরিক অ্যাসিড টেস্ট এন্ড ভিটামিন বি টুয়েলভ
[AI]   You'd like the price of ম্যালেরিয়া টেস্ট, ইউরিক অ্যাসিড and ভিটামিন বি১২, is that right?
[AI]   Are you still there?
[User] আই ওয়ান্ট টু নো ম্যালেরিয়া টেস্ট ইউরিক অ্যাসিক টেস্ট এন্ড ভিটামিন বি টুয়েলভ
[AI]   Malaria Antigen is 400 rupees, Uric Acid is 250 rupees and Vitamin B12 is 1200 rupees. Altogether that is 1850 rupees.
```

### What went wrong

* **3a. Silent drop.** You named three tests. The agent read back **two** ("uric acid and vitamin d"). Malaria vanished, and the third was wrong (D, not B12). It did not say "I only caught two".
* **3b. Reply with Bengali letters in an English sentence.** After you switched to Banglish, the read-back was an *English* sentence containing Bengali names.
* **3c. "Are you still there?"** You never heard the read-back, because the English voice cannot speak Bengali letters.

### Why it happened

| Part   | Fact                                                                                                                                                                                                                                                                                                    |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3a     | `malariot` scores **0.61** against `malaria antigen` (below 0.72, dropped). `vitamin vitual` → **D 0.88**, B12 0.78. No check that "three names said" ≠ "two names found" (`spoken_count` only covers "two tests" style counts)                                                   |
| 3b     | The call is locked to English (first sentence English, ADR 0003). The read-back is built from your**own words** (`forms`) → Bengali script inside an English template (`reply_templates_i18n.py:271 test_confirm_prompt`)                                                                    |
| 3c     | `unspeakable_spans(text, "en")` flags any non-ASCII (`agent/speech_norm.py:490`). `_synthesize_one_clause` then plays a **pre-recorded fallback clip** (`main.py:1398–1410`). The screen shows the read-back; the speaker plays the fallback. No answer came → the silence prompt fired |
| Result | The set you gave the second time equals the pending set → priced (via RC-1). The price was right by luck of ASR, not by design                                                                                                                                                                         |

The rule "no language switch in the middle" is working as you asked. The bug is that the read-back is not built for it.

### 3.1 DEEP ANALYSIS — "The code is strict that a call started in English never falls into another language. So why did Bengali appear?"

**Short answer.** In this call the agent's **reply language never changed**. It stayed English from the first sentence to the last (proof below). What you saw in Bengali came in through **five other doors that the language lock does not cover**. The rule in the code is true. It is also narrower than "the whole conversation stays in one language".

#### A. What the lock covers, and what it does not

The rule is `REPLY_LANGUAGE_MODE=sticky` (`main.py:490–500`, `docs/adr/0003`): *"the language of the caller's first REAL sentence is the language of the call — reply templates and TTS voice stay in it, whatever language later turns are recognised in."*

| Thing | Locked to English after your first English sentence? | Code |
|---|---|---|
| Reply **templates** (the sentence structure) | **Yes** | `_resolve_reply_language`, `main.py:7286–7330` |
| TTS **voice** for the reply | **Yes** | same `lang` passed to `_speak` |
| **Which recogniser reads your audio** (bn / hi / en) | **No, by design** — decided again for every utterance | `_route_and_transcribe_raw`, `main.py:3154–3330`; `pick_candidate`, `agent/lang_select.py:266` |
| The recogniser's **prior** (which engine is favoured next) | **No, and it moves to what you SPEAK** | `session.lang_router.note_response_language(detected_lang)`, `main.py:7328` — the comment above it says so: *"the recogniser's prior is the language the caller SPEAKS, not the language the agent replies in"* |
| The **script of your words** that are echoed back | **No** — whatever script the winning recogniser used | `ground_tests` + `foreign_script_language`, `agent/entity_grounding.py`, `agent/language_policy.py:81` (its docstring: *"a later turn may be said in another one"*) |
| The **greeting** | **No, always Bengali** (the call has no language yet) | `main.py:7569` `_speak(session, phrase("greeting", "bn"), "bn")`; `self.lang = "bn"` at `main.py:1121` |

So "do not switch the reply" and "never produce a Bengali character" are two different promises. Only the first was built.

#### B. Proof: your English column replayed through the real orchestrator

I replayed the user turns of the English column of block 3 through the real `_dispatch_turn` (seeded clinic, the recogniser's language scripted per turn, as in `tests/_conversation.py`). Script: `scratchpad/lock_replay.py`. Output, laid out as a table:

| Turn | Recogniser reported | `session.lang` (reply) | `lang_locked` | router prior (next recogniser) | Agent |
|---|---|---|---|---|---|
| 0 (connect) | – | `bn` | False | – | greeting in Bengali (`main.py:7569`) |
| 1 `i want to know the price of the cbc test` | en | **en** | **True** | en | disclosure in English + `You'd like the price of cbc, is that right?` |
| 2 `i want to do malariot uric acid test and vitamin vitual …` | en | **en** | True | en | `You'd like the price of uric acid and vitamin d, is that right?` |
| 3 `ম্যালেরিয়া টেস্ট ইউরিক অ্যাসিড টেস্ট এন্ড ভিটামিন বি টুয়েলভ` | **bn** | **en** | True | **bn** | `You'd like the price of ম্যালেরিয়া টেস্ট, ইউরিক অ্যাসিড and ভিটামিন বি১২, is that right?` |
| (12 s silence) | – | en | True | bn | `Are you still there? You'd like the price of ম্যালেরিয়া টেস্ট, …` |
| 4 `আই ওয়ান্ট টু নো ম্যালেরিয়া টেস্ট …` | **bn** | **en** | True | bn | `Malaria Antigen is 400 rupees, Uric Acid is 250 rupees and Vitamin B12 is 1200 rupees. Altogether that is 1850 rupees.` |

This is line-for-line what your PDF shows. The log of the same run contains the system's own alarm for turn 3:

```
ERROR main: [8d5fe2ab] reply language mismatch (expected en): "You'd like the price of ম্যালেরিয়া টেস্ট, ইউরিক অ্যাসিড and"
```

So the reply language was **en in every row**. Two things did move: the **recogniser** (en → bn on turn 3) and the **router prior** (en → bn). Neither is "the reply".

#### C. The five doors

**Door 1 — The greeting is hard-coded Bengali.**
`main.py:7569`: `await _speak(session, phrase("greeting", "bn"), "bn")`. It is spoken before the caller has said anything, so there is no language to lock to yet. Every column of your PDF starts with `[AI] নমস্কার। আপনি সোনোস্ক্যান বাণীর সঙ্গে…`, including the English column. The first call state is also Bengali: `self.lang = "bn"` (`main.py:1121`) and, for the first turn, `prior = session.lang_router.previous_language or "bn"` (`main.py:3197`). The comment at `main.py:485–487` states it: Bengali is "the clinic's primary language and the greeting language". It is deliberate. It is also the one Bengali sentence an English caller always hears.

**Door 2 — Your English speech was transcribed by the Bengali recogniser, into Bengali letters.**
Under the lock the agent still has to *hear* you. For each turn it runs the engines and `pick_candidate` chooses:

1. **English wins** only if the English recogniser's two decoders agree ≥ **0.60**, its text is Latin, **and** (language ID gives English ≥ **0.05** *or* the text is ≥ **50 %** words from a fixed 161-word list).
2. Otherwise the Indic engine that language ID believes wins. A call leaves its language only at LID ≥ 0.97 (`SWITCH_MIN_LID`).

I ran `pick_candidate` with the Bengali-script text on one side and the English text on the other:

| Case | English engine agreement | LID en | → engine chosen |
|---|---|---|---|
| Accented speech, English engine unsure | 0.45 | 0.05 | **bn** |
| English engine nearly agrees | 0.55 (< 0.60) | 0.05 | **bn** |
| English engine agrees, LID says Bengali, medical words | 0.70 | 0.02 | **bn** (word share only 0.33, see below) |
| English engine agrees, LID en 0.10 | 0.70 | 0.10 | en |
| English text garbled (`malariot … vitual`), LID en 0.02 | 0.70 | 0.02 | **bn** |

Why the word-share escape does not help here: the fixed list (`agent/lang_select.py`, `_ENGLISH_WORDS`) holds everyday words (`test`, `and`, `price`, `want` are in it). **`malaria`, `uric`, `acid`, `vitamin`, `twelve` are not.** So `malaria test uric acid test and vitamin b twelve` scores **0.33**, below the 0.5 bar. The English words that matter in this clinic are exactly the ones the rule cannot see.

This is the effect the project already recorded once: `tests/test_language_routing_wiring.py:409` — *"Live 2026-10-09: early LID bn 0.97; the Bengali recogniser transliterated the English; the English one heard it correctly."* When you said "Accent is not matching, so shifted back to banglish", that made it more likely, not less: Bengali-accented English is what language ID labels Bengali, and the Bengali recogniser writes it in Bengali script (`আই ওয়ান্ট টু নো…` is "I want to know…").

**A side effect that compounds.** After one Bengali-engine turn the router prior is `bn` (table above). The next turn starts with the Bengali engine favoured, so recognition *drifts* toward Bengali even though the reply language is locked. English must then win by the rule above.

**Door 3 — The read-back repeats your words in the script they were recognised in.**
This is the door that put Bengali letters into an English sentence:

```
ground_tests(text, lang="en")                         agent/entity_grounding.py
  └─ foreign_script_language(text, "en") -> "bn"       agent/language_policy.py:81   (text has no Latin letter)
  └─ read in the BENGALI table -> forms = ["ম্যালেরিয়া টেস্ট", "ইউরিক অ্যাসিড", "ভিটামিন বি১২"]
_confirm_tests(...)                                   main.py:2306
  └─ test_confirm_prompt(list(grounded.forms), action, "en")   agent/reply_templates_i18n.py:271
       lang is not "bn" or "hi" -> the English template: "You'd like the price of {listed}, is that right?"
```

`forms` is "the alias each name was recognised by, i.e. the caller's OWN words for it". That is right for a same-language call and gives "your words, back to you". Under a lock it puts Bengali words in an English template. Nothing translates them to the English canonical name (`Malaria Antigen`, `Uric Acid`, `Vitamin B12`), although that name is known.

**Door 4 — The detector exists, but it only writes a log line.**
`main.py:1455–1459`: `language_mismatch(lang, text)` → `language_mismatches.record(...)` and `logger.error("reply language mismatch…")`. It does not change the reply. And the order inside `_speak` is: send the text to the screen first (`await session.send_json("AI", text)`, `main.py:1465`), **then** synthesise. So the chat window prints whatever the template produced.

**Door 5 — The blocked reply is not even heard (as far as the tree shows).**
The English voice drops anything outside ASCII. `unspeakable_spans(verbalize(line, "en"), "en")` on the turn-3 line returns `['ম্যালেরিয়া', 'টেস্ট', 'ইউরিক', 'অ্যাসিড', 'ভিটামিন', 'বি']`, so `TTSClient.synthesize` raises `UnspeakableTextError` (`agent/tts.py`, KCD-455) and `_synthesize_one_clause` plays `fallback_audio(...)` (`main.py:1398–1410`). `fallback_audio` looks for `static/fallback_audio/…wav`. **That folder does not exist in the project tree**, and the pod tree was byte-identical (467/467 files). The function then returns empty bytes and logs *"call will go silent on this failure path"* (`agent/tts.py:306–310`). So the caller most likely heard **nothing**, saw a Bengali-lettered English sentence, then heard `Are you still there?`. (If a recorded clip existed it would be a Bengali apology unless an `_en.wav` was also recorded, because the code falls back to the Bengali file: `agent/tts.py:289–296`.)

#### D. Answer, in one paragraph

You are right that the code locks the call to its first language, and the replay shows it held: **every reply template and every voice choice stayed English**. The Bengali you saw is the *echo of what the Bengali recogniser wrote*, placed into an English sentence by a code path that was written to be faithful to the caller's words, not to the call's language. The lock decides **which template and voice to use**; it never decided **which script the quoted words may be in**. On top of that the greeting is Bengali by design, and the one safety net that notices a mismatch only logs. What triggered it was a speaker doing the natural thing a Bengali caller does when an English call is not understood: switching to Banglish.

#### E. What I could not verify

* **Which candidate text the live engines produced.** The pod is down; there are no per-engine agreement scores or LID values. Door 2's table shows what the *rule* does for plausible inputs, not what happened on your turn.
* **Whether a recorded fallback clip existed on the pod.** The tree has none and the pod was verified identical before the test; I cannot see the pod's disk now.
* **What the speaker played.** The replay shows the text. The "heard nothing" claim rests on `agent/tts.py` and `agent/speech_norm.py` as read.

#### F. Fixes specific to this question (ranked)

1. ★ **Read-backs must be written in the reply language, with canonical names.** When `lang` is not the script of `forms`, use the catalogue's canonical name (`Malaria Antigen`) or its alias in the reply language's table. One function, used wherever `forms` is interpolated (`test_confirm_prompt`, `_confirm_tests`, the group replies). Add a test: *for every reply language, no reply contains a character from a script the voice cannot speak.*
2. ★ **Turn the mismatch detector into a repair.** In `_speak`, when `language_mismatch` or `unspeakable_spans` is non-empty, rebuild the line from canonical names *before* `send_json`, so the screen and the speaker are the same string. At minimum, do not send text that cannot be spoken.
3. ★ **Make the rule one sentence the code enforces end to end.** Add to `docs/adr/0003` the missing half: *the reply AND the words quoted in it are in the call's language; the greeting is the only exception.* Add the replay above as a regression test (`tests/test_language_lock_replay.py`).
4. **Decide the greeting.** Keep Bengali (the clinic's primary language); or speak a two-line greeting ("নমস্কার… / Hello…") so an English caller hears English at once; or pick the greeting from the entry URL or phone number. This is a product decision, not a bug.
5. **Make the recogniser less eager to leave an English call.** Under an English lock require LID ≥ 0.97 for Bengali **and** a Bengali-grammar check (`transcript_supports_switch`) before Bengali text wins, as the other direction already does; add the clinic's medical English terms (`malaria`, `uric`, `acid`, `vitamin`, `dengue`, `lipid`, `thyroid`, `hcv` …) to `_ENGLISH_WORDS` so a clean English sentence clears the 0.5 word-share bar.
6. **Record the fallback clips** (`sorry_repeat`, `system_busy`, `check_failed` in `bn`, `hi`, `en`) so a blocked reply is audible and in the right language, or change the code to synthesise a plain English apology instead of returning silence.
7. **Optional product behaviour:** after two Banglish turns under an English lock, ask once "Would you prefer Bengali? / বাংলায় বলব?" and honour the answer through the existing explicit-switch path, which is the only door meant to move the lock.

### Top-5 fixes (Problem 3 as a whole)
1. ★ Build read-backs from **canonical names in the locked language**, never from the raw words you spoke. *(3.1-F1)*
2. ★ If a "forms" span is not speakable in the reply language, replace it with the canonical name; make the screen and the speaker the same string. *(3.1-F2)*
3. ★ Say how many were caught: "You named three; I caught two — uric acid and vitamin D. What was the other?"
4. Make the greeting a decision, not an accident (3.1-F4), and record the fallback clips (3.1-F6).
5. Optional (your decision): after two Banglish turns under an English lock, offer once "Shall I continue in Bengali?" (3.1-F7).

---

## PROBLEM 4 — English: "ECG" is heard as "ec", nothing is recognised

**Your note (11):** "I have tried 3 times and it always takes ec in the place of ecg" — and (13) "next day it's working". **Severity: S2.**

### The conversation (blocks 10–13)

```
English column, block 10:
[User] i'd like to know the price of ec test
[AI]   You are speaking with Sonoscan Vaani. I am an automated assistant, not a person. …
[AI]   Which tests do you mean? Could you say the names?

English column, block 12 (next day):
[User] i would like to know the price of ecg
[AI]   You'd like the price of ecg, is that right?
[User] yes
[AI]   ECG is 300 rupees.

Hindi column, block 10:
[User] एसीजी टेस्ट का प्राइस क्या है
[AI]   अच्छा, बताती हूँ।
[AI]   आप ईसीजी टेस्ट का दाम जानना चाहते हैं, ठीक है ना?
[User] हाँ बिलकुल
[AI]   ईसीजी 300 रुपये।

Bengali column, block 10:
[User] ইসিজি টেস্টের দাম কত
[AI]   আপনি ইসিজি টেস্টের দাম জানতে চান, ঠিক তো?   [User] হ্যাঁ   [AI] ইসিজি 300 টাকা।
```

### What went wrong

In English the word "ECG" arrived as **`ec`**. The agent found no test and asked "Which tests do you mean?". Three tries, same result. The next day, the same word worked.

### Why it happened

| Step    | Fact                                                                                                                                                                                                                            |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Scoring | `ec test` → best **0.44** (`elisa`); the form `ecg` is **3 letters**, and forms shorter than the "exact below" size must match exactly (`agent/form_index.py`, `exact_below_chars`) → `ec` ≠ `ecg` |
| Letters | `e c g` scores **0.35**; `ecg` alone scores 1.00. Spaced letters are not collapsed                                                                                                                                    |
| ASR     | The English engine dropped the final**g** three times in a row, one day, then not the next. Same pattern as device/gain/noise-suppressor effects **[hypothesis, no WAV available]**                                 |
| Message | "Which tests do you mean?" gives no options, so the next try is the same                                                                                                                                                        |

### Top-5 fixes

1. ★ Collapse runs of single letters before matching (`e c g` → `ecg`, `h c v` → `hcv`).
2. ★ Allow a unique-prefix recovery for ≤ 4-letter acronyms next to "test" (`ec test` → ECG), **always with a read-back**.
3. Offer the nearest 2–3 candidates ("ECG, HCV or HIV?") instead of the generic question.
4. Test with `noiseSuppression: false` for the same words (the browser may clip the consonant).
5. Record the WAV of any "nothing grounded" turn so the garble can be heard and measured.

---

## PROBLEM 5 — Hindi: "USG" is not recognised; "whole abdomen" is not recognised; only Pap smear is taken

**Your notes (16, 18):** "Its not right. i can't get and debug it due to hindi wordings." / "doesn't take usg". **Severity: S2.**

### The conversation (blocks 15 and 17, Hindi column)

```
Block 15
[User] मैं पप्स्मियर उस प्रेगनेंसी प्रोफाइले करना चाहता हूँ कितना लगेगा
[AI]   अच्छा, बताती हूँ।
[AI]   आप पैप स्मियर टेस्ट का दाम जानना चाहते हैं, ठीक है ना?
[AI]   माफ़ कीजिए। मैं ठीक से समझ नहीं पाई। कोई जल्दी नहीं, क्या आप धीरे बोलेंगे?
[User] उस होल आप्डोमन और उस प्रेग्नेंसी प्रोफाइले कराना चाहता हूँ
[AI]   अच्छा, बताती हूँ।
[AI]   माफ़ कीजिए, ठीक से सुन नहीं पाई। क्या आप फिर से थोड़ा साफ़ बोलेंगे?
[AI]   माफ़ कीजिए। मैं ठीक से समझ नहीं पाई। कोई जल्दी नहीं, क्या आप धीरे बोलेंगे?
[User] मैं पप्स्मियर उस होल आप्डोमन और उस प्रेगनेंट से प्रोफाइले कराना चाहता हूँ कितना लगेगा
[AI]   अच्छा, बताती हूँ।
[AI]   आप पैप स्मियर टेस्ट का दाम जानना चाहते हैं, ठीक है ना?

Block 17
[User] मैं उस कढ़ना चाहता हूँ कितना लगेगा
[AI]   अच्छा, बताती हूँ।
[AI]   किस टेस्ट की कीमत जाननी है, ज़रा बताइए?
[User] मैं उस कराना चाहता हूँ
[AI]   अच्छा, बताती हूँ।
[AI]   टेस्ट की बुकिंग और पेमेंट काउंटर पर आकर करनी होती है, फ़ोन पर नहीं होती। पर्चा लेकर काउंटर पर आ जाइए।
[User] मैं उस कढ़ाना चाहता हूँ कितना लगेगा
[AI]   अच्छा, बताती हूँ।
[AI]   किस टेस्ट की कीमत जाननी है, ज़रा बताइए?
```

Same words in **English** worked: `papsmere and ussh abdomen and whole pregnancy profile` → read back all three → yes → **4000**.
Same words in **Bengali** worked: `প্যাপসফিয়ার প্রেগন্যান্সি আলট্রাসাউন্ড আর পেটের আলট্রাসাউন্ড` → 3 tests → **4000**.

### What went wrong

* **"USG" was written by the Hindi ASR as `उस`** (the Hindi word "that"). It is a real Hindi word, so nothing can treat it as a test.
* **"whole abdomen" was written `होल आप्डोमन`.** The alias is `होल एब्डोमेन`.
* Only **Pap smear** was read back. Both ultrasounds were silently dropped.
* After "I did not catch that", **the same question was repeated three times** with no options.
* Two of the "sorry" lines came with no user line in front of them (Problem 10).

### Why it happened

| Heard                                            | Best form                                | Score          | Floor 0.72     |
| ------------------------------------------------ | ---------------------------------------- | -------------- | -------------- |
| `उस होल आप्डोमन`                   | `होल एब्डोमेन यूएसजी` | **0.60** | below          |
| `उस प्रेगनेंसी प्रोफाइले` | `प्रेगनेंसी यूएसजी`    | 0.74           | passes (alone) |
| `मैं उस कढ़ना चाहता हूँ`     | nothing                                  | –             | nothing        |

* Standing alone, `उस प्रेगनेंसी प्रोफाइले` scores 0.74 (passes). **Inside the full sentence the replay grounds only Pap Smear** (`पप्स्मियर` scores 0.84). Why the pregnancy window loses in the long sentence is not proven; the competing forms are `लिपिड/थायराइड प्रोफाइल` at 0.64–0.70 for the same words.
* Hindi aliases for USG are only `पेट का अल्ट्रासाउंड`, `होल एब्डोमेन यूएसजी`, `पेट का यूएसजी`, `प्रेगनेंसी अल्ट्रासाउंड`, `प्रेगनेंसी यूएसजी` (`i18n_content.py:43–44`). There is no `यूएसजी` alone and no `अल्ट्रासाउंड` alone.
* The dead-end is `which_tests_prompt` with no options (RC-8).

### Top-5 fixes

1. ★ Add Hindi aliases: `यूएसजी`, `अल्ट्रासाउंड`, `होल एब्डोमेन`, `होल अब्डोमेन`, `होल आप्डोमन`, `प्रेगनेंसी प्रोफाइल`.
2. ★ Treat `उस` as USG **only** when it sits next to `होल` / `प्रेगनेंसी` / `अल्ट्रासाउंड`.
3. ★ After 2 failed asks, read categories: "ब्लड, अल्ट्रासाउंड / स्कैन, दिल, यूरिन?" (the group net already exists).
4. Say what was caught: "मैंने पैप स्मियर सुना; बाकी दो टेस्ट का नाम फिर से बताएँ।"
5. Hindi ASR biasing with the 34 test names + aliases (Problem 11 below).

---

## PROBLEM 6 — Bengali: "দুটোই করাবো" ("both") repeated the list twice  (FIXED)

**Your note (18):** "Need to add this feature". **Status: fixed and deployed earlier the same day.**

### The conversation (block 17, Bengali column — as in the PDF)

```
[User] আমি ইউএসজি করাবো কত পড়বে
[AI]   আলট্রাসাউন্ডের টেস্ট আমাদের এখানে 2টা আছে: পেটের আলট্রাসাউন্ড 1500 টাকা আর প্রেগন্যান্সি আলট্রাসাউন্ড 1600 টাকা। কোনটা করাতে চান?
[User] দুটোই করাবো
[AI]   আলট্রাসাউন্ডের টেস্ট আমাদের এখানে 2টা আছে: পেটের আলট্রাসাউন্ড 1500 টাকা আর প্রেগন্যান্সি আলট্রাসাউন্ড 1600 টাকা। কোনটা করাতে চান?
```

English: `i want to do usg so what will be the price` → list ✔. Hindi: see Problem 5.

### What went wrong

"দুটোই" ("both") names no test, so the "which one?" loop re-asked the same list.

### Why it happened

`_continue_test_choice` (in `main.py`) knew only "a test named" and "list request"; "both / all" is neither, so it fell to the "say the list once more" branch.

### What was done (already deployed)

`agent/test_groups.py` `asks_for_all` + `main.py` `_price_all_of_the_group`: "দুটোই", "both", "all of them", "दोनों", "सभी", "সবগুলো" after a group list → speak each price and the total. Verified on the pod:

```
[bn] দুটোই করাবো  → … পেটের আলট্রাসাউন্ড 1500 টাকা আর প্রেগন্যান্সি আলট্রাসাউন্ড 1600 টাকা। সব মিলিয়ে মোট 3100 টাকা।
[hi] दोनों करवाने हैं → … सब मिलाकर कुल 3100 रुपये।
[en] both of them please → … Altogether that is 3100 rupees.
```

And in your own run, Hindi dengue `दोनों` → **1800** (block 20, see Problem 7).

### Top-5 follow-ups

1. ★ Keep (shipped).
2. "All of them" on the 34-test paged list → total of the page.
3. A count word that does not match the list size ("দুটো" after five) → ask "which two?".
4. Add `dono` / `sabhi` Latin spellings (done).
5. Put your block-17 lines into the regression file (done in `test_test_groups.py`).

---

## PROBLEM 7 — English: dengue tests not recognised; Hindi falls back to the dengue group

**Your note (21):** "ASR garbled with my voice" / "In hindi, it only recognizes "dengu", so it directly forward me to dengu grouping". **Severity: S2 (English) / works as designed (Hindi).**

### The conversation (block 20)

```
[bn] [User] আমি ডেঙ্গু এনএসওয়ান আর ডেঙ্গু আইজিজি করাব মোট কত করবে
     [AI]   আপনি ডেঙ্গু এনএস১ আর ডেঙ্গু আইজিজি টেস্টের দাম জানতে চান, ঠিক তো?
     [User] হ্যাঁ
     [AI]   ডেঙ্গু এনএস১ 900 টাকা আর ডেঙ্গু আইজিজি 900 টাকা। সব মিলিয়ে মোট 1800 টাকা।

[en] [User] i want to do dingo enis one and dingo i gzi what will be the cost
     [AI]   Alright, let me tell you.
     [AI]   Which tests do you mean? Could you say the names?

[hi] [User] मैं डेंगू इग और डेंगू नस ओन करना चाहता हूँ कितना लगेगा
     [AI]   डेंगू के टेस्ट हमारे यहाँ 2 हैं: डेंगू एनएस वन 900 रुपये और डेंगू आईजीजी 900 रुपये। कौन सा करवाना चाहेंगे?
     [User] दोनों
     [AI]   डेंगू एनएस वन 900 रुपये और डेंगू आईजीजी 900 रुपये। सब मिलाकर कुल 1800 रुपये।
```

### What went wrong

* **English:** "dengue" came out as **"dingo"**, "NS1" as **"enis one"**, "IgG" as **"i gzi"**. Nothing matched.
* **Hindi:** `डेंगू इग` (IgG) scored 0.63 (below 0.72) and `डेंगू नस ओन` scored 0.83 (NS1). Only NS1 was grounded, so the **dengue group net** listed both tests. You said "दोनों" and got ₹1800.

### Why it happened

| Heard                    | Best                         | Score                     |
| ------------------------ | ---------------------------- | ------------------------- |
| `dingo enis one`       | `elisa` (HIV)              | 0.67 (and the wrong test) |
| `dingo i gzi`          | `dengue igg/igm`           | 0.56                      |
| `डेंगू इग`      | `डेंगू टेस्ट`    | 0.63                      |
| `डेंगू नस ओन` | `डेंगू एनएस वन` | 0.83                      |

The English engine does not know "dengue" as a word it should prefer. This is a recogniser vocabulary problem (RC-5), not a matching bug.

### Top-5 fixes

1. ★ Contextual biasing on the English and Indic engines with the test names (Problem 11).
2. ★ Add Hindi `डेंगू इग`, `डेंगू आई जी`, `डेंगू नस वन` as aliases.
3. Keep the group fallback (it behaved correctly) and say "I only caught *Dengue*" before the list.
4. Offer "both" as the first option in the group prompt.
5. Count the fallback rate as a quality metric (it means the ASR failed).

---

## PROBLEM 8 — Banglish/English: HBsAg + HCV + HIV took four tries; TTS says "hib"; Hindi escalated to a human

**Your notes (24):** "I had tell slowly… 1. after facing multiple failures it succeed 2. hiv is telling as 'হিব' by tts / It doesn't take". **Severity: S2.**

### The conversation (block 23)

```
[bn] [User] আমি এইচভিএসএইজি এইচসিভি আর এইচআইভি টেস্ট করাব মোট কত পড়বে
     [AI]   আপনি এইচসিভি, এইচবিএসএজি আর এইচআইভি টেস্টের দাম জানতে চান, ঠিক তো?
     [User] হ্যাঁ
     [AI]   এইচসিভি 600 টাকা, এইচবিএসএজি 400 টাকা আর এইচআইভি টেস্ট 500 টাকা। সব মিলিয়ে মোট 1500 টাকা।

[en] [User] i want to jvs egv and hiv tests what will be the price
     [AI]   Alright, let me tell you.
     [AI]   You'd like the price of hiv test, is that right?
     [User] আই ওয়ান্ট এইচ সি ভি এইচ বি এসএইজি এন্ড এইচ আই ভি টেস্ট
     [AI]   You'd like the price of এইচসিভি and এইচবিএসএজি, is that right?
     [User] i want to do hvsg and hiv test
     [AI]   You'd like the price of hiv test, is that right?
     [User] i want to do hbsgh hcv and hiv test
     [AI]   You'd like the price of hcv, hbsag and hiv test, is that right?
     [User] yes
     [AI]   HCV is 600 rupees, HBsAg is 400 rupees and HIV Test (ELISA) is 500 rupees. Altogether that is 1500 rupees.

[hi] [User] मैं और आइचीफी तीनों टेस्ट करना चाहता हूँ कितना लगेगा
     [AI]   अच्छा, बताती हूँ।
     [AI]   कौन कौन से टेस्ट की बात कर रहे हैं, नाम बताएँगे?
     [AI]   माफ़ कीजिए। पास से किसी और की आवाज़ आ रही है। क्या आप फिर बोलेंगे?
     [AI]   माफ़ कीजिए। पास से किसी और की आवाज़ आ रही है। क्या आप फिर बोलेंगे?
     [AI]   माफ़ कीजिए। मैं आपको समझ नहीं पाई।
     [undefined] undefined
     [AI]   मैं आपको हमारे स्टाफ़ से जोड़ रही हूँ, कृपया लाइन पर बने रहिए।
```

### What went wrong

1. **English attempt 1:** `jvs egv` (HBsAg, HCV) was not recognised; only HIV was read back.
2. **English attempt 2 (Banglish):** `এইচ আই ভি টেস্ট` (spaced letters) was not matched, so HIV was dropped; the read-back was in Bengali script inside English (Problem 3).
3. **English attempt 3:** `hvsg` was not recognised.
4. **English attempt 4:** worked. And the voice says **HIV like a word** ("হিব").
5. **Hindi:** the ASR wrote **`आइचीफी`** for "HBsAg and HIV"; nothing matched; after **four non-understandings** (two of them the false "someone else is speaking" message) the agent handed the call to staff, and the chat showed `[undefined] undefined`.

### Why it happened

| Part           | Fact                                                                                                                                                                                                                                                    |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ASR            | `jvs egv`, `hvsg`, `hbsgh`, `आइचीफी` are all recogniser noise for letter names                                                                                                                                                            |
| Scoring        | `jvs egv` 0.38; `hvsg` 0.67 (HBsAg); `hbsgh` 0.80 (HBsAg — passes); `आइचीफी` 0.50                                                                                                                                                        |
| Spaced letters | `এইচ আই ভি টেস্ট` → **0.67** (HIV) — below 0.72; `এইচ বি এসএইজি` 0.74 passes, `এইচ সি ভি` 0.77 passes                                                                                                       |
| HIV alias      | the form is`এইচআইভি টেস্ট`; bare `এইচআইভি` scores HCV **0.71** vs HIV **0.70** (below floor, near tie)                                                                                                               |
| TTS            | `verbalize_en` (`agent/speech_norm.py:433`) has **no acronym spelling**; only bn/hi use `pronunciation.apply`                                                                                                                               |
| Crosstalk      | `_is_crosstalk` fires on "near-continuous tonal energy" (`agent/channel_quality.py:114`). It documents itself as a heuristic; a slow single speaker with the browser's noise suppressor fits that **[hypothesis]**                            |
| Escalation     | `_reask_or_handoff` after repeated failures calls `_handoff_to_human`, which sends `{"type": "handoff_human"}` (`main.py:1577`). `static/pcm/index.html:207–210` prints any non-chat frame as `[sender] text` → `[undefined] undefined` |

### Top-5 fixes

1. ★ Collapse spaced letters: `এইচ আই ভি` → `এইচআইভি`; `h c v` → `hcv`; `एच आई वी` → `एचआईवी`.
2. ★ Drop the word "test" from the HIV aliases (bare `এইচআইভি`, `एचआईवी`), and add HIV/HCV to the confusable pair list (always "which one?").
3. ★ In `verbalize_en`, spell known acronyms ("H I V", "H B s A g", "H C V", "E C G").
4. Do not use the crosstalk message unless the near-end tracker really saw a second voice; replace the human handoff with the options ladder first.
5. Client: ignore frames without `sender`/`text` (`index.html:207–210`).

---

## PROBLEM 9 — HCV + ECG: "h cv and ec test" is not recognised; the same problem when you speak slowly

**Your note (27):** "same problem / I have to tell in slow pace. then it can take properly". **Severity: S2.**

### The conversation (block 26)

```
[bn] [User] আমি হর ইক টেস্ট করব মোট কত পড়বে
     [AI]   আপনি ইকো টেস্টের দাম জানতে চান, ঠিক তো?
     [User] আমি এইচসিভি আর ইসিজি টেস্টের দাম জানতে চাই
     [AI]   আপনি এইচসিভি আর ইসিজি টেস্টের দাম জানতে চান, ঠিক তো?
     [User] হ্যাঁ
     [AI]   এইচসিভি 600 টাকা আর ইসিজি 300 টাকা। সব মিলিয়ে মোট 900 টাকা।

[en] [User] i want to do h cv and ec test what will be the price
     [AI]   Alright, let me tell you.
     [AI]   Which tests do you mean? Could you say the names?
     [User] এইচসিভি অ্যান্ড ইসিজি
     [AI]   You'd like the price of এইচসিভি and ইসিজি, is that right?
     [AI]   Sorry. I did not understand. There is no rush. Could you say it slowly?
     [User] i want to know h c v and ec test
     (no reply recorded)

[hi] [User] मैं ह और टेस्ट का दाम जानना चाहता हूं
     [AI]   अच्छा, बताती हूँ।
     [AI]   किस टेस्ट की कीमत जाननी है, ज़रा बताइए?
     [User] आइट और
     [AI]   अच्छा, बताती हूँ।
     [AI]   माफ़ कीजिए, नाम ठीक से सुन नहीं पाई। किस टेस्ट की कीमत जाननी है, ज़रा बताइए?
     [User] एचसीवी और ईसीजी
     [AI]   आप एचसीवी और ईसीजी टेस्ट का दाम जानना चाहते हैं, ठीक है ना?
     [User] हाँ
     [AI]   एचसीवी 600 रुपये और ईसीजी 300 रुपये। सब मिलाकर कुल 900 रुपये।
```

### What went wrong

* **Bengali:** `হর ইক` ("heart echo"? an ASR garble of "HCV ECG") became the **Echo test**. The read-back caught it; you corrected it. Fine.
* **English:** `h cv` and `ec` produced nothing; the Banglish retry was read back in Bengali script then "Sorry. I did not understand" with no user line; your last line got no reply.
* **Hindi:** `ह और` and `आइट और` gave nothing. `एचसीवी और ईसीजी` (clearly said) worked at once.

### Why it happened

Same causes as Problems 4 and 8: spaced letters (`h cv` 0.44; `h c v` 0.50) are not collapsed and short acronyms must match exactly. Hindi `एचसीवी` and `ईसीजी` are in the table, but a garbled `ह` / `आइट` is not.

### Top-5 fixes

1. ★ Letter collapse + acronym recovery (Problems 4, 8).
2. ★ A fixed prompt that suits acronyms: "Please say the letters slowly, like H–C–V" (spelling mode), accepted by the collapse rule.
3. ★ Recogniser biasing (Problem 11).
4. When the same turn nearly matches two acronyms (HCV/HBsAg/HIV/ECG), ask "which of these?" with the list.
5. Detect "slow repetition" (your pace) and prefer the CTC head's hypothesis for acronyms.

---

## PROBLEM 10 — "Sorry, I did not understand" appears with no user line (phantom re-asks)

**Not in your notes; found while reading line by line.** **Severity: S3.**

### The conversation (the lines where a re-ask follows a read-back with no [User] in between)

```
[bn] block 15: [AI] আপনি প্রেগন্যান্সি আলট্রাসাউন্ড, প্যাপ স্মিয়ার আর পেটের আলট্রাসাউন্ড টেস্টের দাম জানতে চান, ঠিক তো?
                [AI] দুঃখিত। আমি ঠিক বুঝতে পারিনি। কোনও তাড়া নেই, একটু ধীরে বলবেন?
[hi] block 15: [AI] आप पैप स्मियर टेस्ट का दाम जानना चाहते हैं, ठीक है ना?
                [AI] माफ़ कीजिए। मैं ठीक से समझ नहीं पाई। कोई जल्दी नहीं, क्या आप धीरे बोलेंगे?
[hi] block 15: [AI] अच्छा, बताती हूँ।
                [AI] माफ़ कीजिए, ठीक से सुन नहीं पाई। क्या आप फिर से थोड़ा साफ़ बोलेंगे?
                [AI] माफ़ कीजिए। मैं ठीक से समझ नहीं पाई। कोई जल्दी नहीं, क्या आप धीरे बोलेंगे?
[en] block 26: [AI] You'd like the price of এইচসিভি and ইসিজি, is that right?
                [AI] Sorry. I did not understand. There is no rush. Could you say it slowly?
```

### What went wrong

The agent re-asked when **no user text was shown**. In the Bengali case the read-back was already correct, so the re-ask made you repeat the whole sentence for nothing.

### Why it happened

When ASR returns an **empty string**, `main.py:3557` (`if not text:`) re-asks, and the chat only prints a `[User]` line when text exists. So an empty turn looks like a "phantom".

Likely triggers right after a read-back **[hypothesis, needs WAV/logs]**:

* your short "yes / হ্যাঁ" shorter than the minimum speech length, or clipped by the agent-speaking gate tail;
* a breath/click picked up by the laptop mic;
* the agent's own voice leaking (AEC is off by default, `AEC_BARGE_IN=0`; the browser's own echo cancel is on).

### Top-5 fixes

1. ★ Log duration / RMS / VAD span and keep the WAV for every empty turn.
2. ★ After a read-back, treat an empty or very short turn as "possibly yes": ask "हाँ या ना?" once, not "sorry".
3. Lower the minimum speech length for the turn that follows a yes/no question.
4. Check the echo-gate tail against the length of the read-back; shorten it for short answers.
5. Add a "second chance" (boost gain and re-run ASR once) for empty results.

---

## PROBLEM 11 — The root of Problems 4–9: the recognisers are not told the clinic's words

**Not a single line in the PDF; it is the common cause.** **Severity: S2.**

### What the conversations show

| You said                | ASR wrote                            |
| ----------------------- | ------------------------------------ |
| ECG (en)                | `ec`                               |
| dengue / NS1 / IgG (en) | `dingo` / `enis one` / `i gzi` |
| HBsAg HCV (en)          | `jvs egv`, `hvsg`, `hbsgh`     |
| USG (hi)                | `उस`                             |
| whole abdomen (hi)      | `होल आप्डोमन`            |
| B12 (hi)                | `ब ल्वेल्वे`              |
| thyroid (hi)            | `आयरॉइड`                     |
| HBsAg + HIV (hi)        | `आइचीफी`                     |
| HCV (hi)                | `ह`                               |

### Why it happens

`agent/asr.py` runs **plain greedy CTC/RNNT** (`logprobs=False`). No hot-words, no vocabulary, no biasing. Everything after ASR is repair. When the output is far from every alias (`dingo enis one`), nothing downstream can recover.

### What the research says (Appendix D)

* NVIDIA NeMo documents three ways: a CTC-based word spotter for hybrid Transducer-CTC models (**CTC-WS**), a GPU phrase-boosting tree (**GPU-PB**), and Flashlight boosting with an n-gram LM.
* **SpellMapper** is a post-ASR neural spell-corrector with a custom vocabulary.
* Limits here: the IndicConformer checkpoints load only in AI4Bharat's NeMo 1.23 fork, which uses `logprobs=False`; GPU-PB needs newer NeMo. Feasibility on this fork is **unproven**.

### Top-5 fixes

1. ★ CTC-WS word spotting from the catalogue (34 names × aliases) on the CTC head's log-probs (`logprobs=True` exists in `transcribe`). Test on the fork first.
2. ★ Re-score the CTC head's hypothesis against aliases (a light version of Problem 4's collapse, run on the *second* decoder's text; `decoder_agreement` already runs both).
3. SpellMapper-style post-correction using the catalogue as the custom vocabulary.
4. N-best from RNNT beam search into the unused `agent/nbest_rescore.py`.
5. Last: adapt the model on 200–500 labelled clinic utterances (the plan in `REMINDERS-if-client-asks.md`).

---

## WHAT WORKED (no action)

| Block | Language                       | Result                                                                                                       |
| ----- | ------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| 1     | bn, en, hi                     | "which tests are there" → paged list of 34 tests, "go on / और बोलिए" worked, "that's all" at the end |
| 3     | bn                             | blood list; malaria + uric acid + B12 → 1850 (B12 recovered from`ব টেলভে`)                          |
| 6     | bn, en, hi                     | X-ray answered with the one test: 400                                                                        |
| 7     | bn, en                         | sugar PP + thyroid + B12 → 2020                                                                             |
| 10    | bn, hi                         | ECG → 300                                                                                                   |
| 12    | en                             | `i would like to know the price of ecg` → 300                                                             |
| 15    | bn (second attempt), en        | Pap smear + USG whole abdomen + USG pregnancy → 4000                                                        |
| 20    | bn                             | dengue NS1 + IgG → 1800                                                                                     |
| 23    | bn                             | HBsAg + HCV + HIV → 1500 (`এইচভিএসএইজি` repaired)                                              |
| 26    | bn (second attempt), hi (last) | HCV + ECG → 900                                                                                             |

**Pattern (multi-test sentences, first read-back):** Bengali correct in **5 of 6** (the miss was `হর ইক` → Echo); English correct in **2 of 6**; Hindi correct in **0 of 6** (block 20 only worked through the dengue group fallback). The Bengali aliases and ASR are the best-covered path.

---

## SUMMARY TABLE — which problem needs which fix

| Fix                    | P1 | P2 | P3 | P4 | P5 | P7 | P8 | P9 | P10 | P11 |
| ---------------------- | -- | -- | -- | -- | -- | -- | -- | -- | --- | --- |
| A1 repeat ≠ yes       | ● | ● | ● |    |    |    |    |    |     |     |
| A2 margin → "A or B?" | ● | ● |    |    |    |    | ● | ● |     |     |
| A3 aliases             | ● | ● |    |    | ● | ● | ● |    |     |     |
| A4 letter collapse     |    |    |    | ● |    |    | ● | ● |     |     |
| A5 recogniser biasing  |    |    |    | ● | ● | ● | ● | ● |     | ●  |
| A6 canonical read-back |    |    | ● |    |    |    | ● |    |     |     |
| A7 English acronym TTS |    |    |    |    |    |    | ● |    |     |     |
| A8 options ladder      |    |    |    | ● | ● |    | ● | ● |     |     |
| A9 "caught N of M"     |    |    | ● |    | ● |    |    |    |     |     |
| A10 empty-ASR handling |    |    |    |    |    |    |    |    | ●  |     |
| A11 crosstalk guard    |    |    |    |    |    |    | ● |    |     |     |
| A12 client frame       |    |    |    |    |    |    | ● |    |     |     |

**Order I would do them in:** A1, A2, A3, A6, A12 (small, safe, remove the wrong-price risk) → A4, A7, A8, A9, A10 → A5.

---

# PART B — FORMAL RCA (your template)

## 1. Problem Statement

**Expected.** A caller names one or more tests in any of the three languages, hears the tests read back correctly, says yes, and hears each test's own price and the sum. When the agent is unsure it asks a short, useful question. It never states a price for a test the caller did not name. It speaks only what the caller's TTS voice can pronounce. It does not leave the caller in a loop.

**Actual (summary of the 27 annotated blocks).**

| #     | Owner's note                                                                                              | What really happened                                                                                                                                               | Defect               |
| ----- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------- |
| 4/5   | "I told vitamin b12 and taking as vitamin d" (Hindi)                                                      | Hindi priced**Vitamin D ₹1800** and a total of ₹2450 for a request that was B12                                                                            | D1, D2, D3           |
| 4     | "Accent is not matching, shifted to Banglish but the answer comes in English"                             | Under the English lock the read-back put**Bengali-script test names inside an English sentence**                                                             | D6                   |
| 8     | "blood sugar taking as blood group and b12 taking as vitamin d" (Hindi)                                   | **Blood Group ₹200 + Vitamin D ₹1800 = ₹2700** stated as the price                                                                                        | D1, D2, D3           |
| 11    | "tried 3 times and it always takes ec in the place of ecg" (English)                                      | `Which tests do you mean?` three times; next day it worked (13)                                                                                                  | D4, D5               |
| 16    | "Its not right. I can't get and debug it due to Hindi wordings"                                           | Hindi "USG whole abdomen / USG pregnancy" never recognised; only Pap smear read back; two phantom "sorry" turns                                                    | D3, D9               |
| 17/18 | "doesn't take usg" / "Need to add this feature"                                                           | Bengali "দুটোই" re-read the list twice (**fixed, D12**); Hindi "USG" never recognised, `किस टेस्ट की कीमत जाननी है?` three times | D3, D8, D12          |
| 21    | "ASR garbled with my voice… in Hindi it only recognises 'dengu' so it forwards me to the dengu grouping" | English:`Which tests do you mean?`; Hindi: group list (correct fallback) then "दोनों" worked                                                                | D5 (ASR), D13        |
| 24    | "told slowly… after multiple failures it succeeded… hiv is told as 'হিব' by TTS"                     | English needed 4 attempts;**Hindi gave up and escalated to a human**; English TTS mispronounces HIV                                                          | D4, D5, D7, D10, D11 |
| 27    | "same problem, I have to tell in slow pace"                                                               | `h c v and ec test` not recognised in English; `ह और` in Hindi; slow repetition worked                                                                      | D4, D5               |

Blocks 1–3, 6, 7 (Bengali, English), 10 (Bengali, Hindi), 12, 15 (English), 20 (Bengali), 23 (Bengali) worked as designed.

---

## 2. Impact and Scope

* **Users:** every caller who says a test name the recogniser garbles, which in this run was **every English and Hindi multi-test sentence containing an acronym or a "USG/B12/blood sugar" name**. Bengali was largely clean (5 of 6 multi-test sentences correct on the first read-back; English 2 of 6; Hindi 0 of 6).
* **Worst effect (S1):** a **wrong price spoken as fact** (₹2450 and ₹2700 in Hindi). The user's own words never confirmed it; the agent took a *repeat* as a yes (D1).
* **Frequency in this run:** wrong price 2 of 27 blocks. Dropped tests (partial read-back) 5 blocks. Dead-end / multi-retry 7 blocks. Escalation to a human 1 block. Mispronounced term 1 block.
* **Not affected:** nothing was booked wrongly (the lab test flow is counter-only; prices are informational). No data was lost. The harm is a wrong quoted amount and a frustrating call.
* **Scope limit:** the run is one speaker, one browser (Chrome mic path with `noiseSuppression: true`), a laptop microphone, one pod. Rates here are indications, not measured population rates.

---

## 3. Incident Timeline

| When (2026-10-15) | Event                                                                                                                                                                                                                                                                                   |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ~08:17 pod clock  | New RTX A4000 pod provisioned (`provision.sh`), stack started, ngrok tunnel up                                                                                                                                                                                                        |
| earlier           | USG / Pap smear report (USG "whole body", Hindi group list) → alias-order fix pushed (`_has_words_any_order`)                                                                                                                                                                        |
| earlier           | "দুটোই করাবো" repeated the ultrasound list twice (the same transcript is block 17) →`asks_for_all` / `_price_all_of_the_group` implemented, tested (519 + 1138 passed), deployed (only `voice-agent`, `voice-pcm` restarted, 243 s), verified on the pod in bn/hi/en |
| ~16:51            | Owner opened the URL during a model reload →`ERR_NGROK_8012` (expected 3–5 min window; not a defect)                                                                                                                                                                                |
| 20:10             | Owner printed the 3-column test transcript with intent notes (this report's input)                                                                                                                                                                                                      |
| 20:10+            | Pod found down (cause not in scope; no logs available)                                                                                                                                                                                                                                  |
| this report       | Code-level RCA, offline reproduction, web research, report                                                                                                                                                                                                                              |

---

## 4. Evidence and Reproduction

### 4.1 Offline reproduction of the grounding decision

Environment: seeded catalogue (34 tests) via clinic-api `/api/v1/catalogue`, `Catalogue` + `ground_tests`, language per column. Input: the user's ASR text copied from the PDF. Full table in **Appendix B**. The decisive rows:

| Block   | Language      | Input (ASR text)                                                                                          | Grounded (what the agent read back / priced)                                           | Matches live?                        |
| ------- | ------------- | --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------ |
| 4 / 5   | hi            | `मलेरिया टेस्ट यूरिक एसिड टेस्ट और विटामिन **ब ल्वेल्वे**`  | Malaria, Uric Acid,**Vitamin D** (D 0.84 vs B12 0.75)                            | yes (read back D; 3rd time priced D) |
| 8       | hi            | `… थायरॉइड प्रोफाइले **ब्लड शुगर** और विटामिन ब ल्वेल्वे …` | Thyroid,**Blood Group** (0.74), **Vitamin D**                              | yes (₹2700)                         |
| 3       | en            | `malariot uric acid test and vitamin **vitual**`                                                        | Uric Acid,**Vitamin D** (malaria dropped)                                        | yes                                  |
| 11 / 27 | en            | `price of ec test`                                                                                      | nothing grounded (`ec` ↔ `ecg`: 2-letter token, exact-only)                       | yes                                  |
| 16      | hi            | `पप्स्मियर उस प्रेगनेंसी प्रोफाइले` / `उस होल आप्डोमन …`   | **Pap Smear only** / **nothing**                                           | yes                                  |
| 18      | hi            | `मैं उस कढ़ना चाहता हूँ`                                                              | nothing                                                                                | yes                                  |
| 21      | en            | `dingo enis one and dingo i gzi`                                                                        | nothing                                                                                | yes                                  |
| 24      | en            | `jvs egv and hiv tests` / `hvsg and hiv test`                                                         | **HIV only**                                                                     | yes                                  |
| 24      | en (Banglish) | `এইচ সি ভি এইচ বি এসএইজি এন্ড এইচ আই ভি টেস্ট`                        | HCV, HBsAg (**HIV dropped**: `এইচ আই ভি টেস্ট` scores 0.67 < 0.72) | yes                                  |
| 27      | en            | `h cv and ec test`                                                                                      | nothing (`h cv` 0.44)                                                                | yes                                  |
| 27      | hi            | `मैं ह और टेस्ट का दाम …` / `आइट और`                                            | nothing                                                                                | yes                                  |
| 20      | hi            | `डेंगू इग और डेंगू नस ओन`                                                             | NS1 only + dengue group → "दोनों" → ₹1800                                      | yes                                  |

### 4.2 The confirmation decision (the S1 defect), in code

`main.py:2333` `_continue_test_confirm`, the answer to "<tests></tests>, ঠিক তো?":

```python
corrected = _ground_tests_said(session, text, lang, [])
if corrected.names and list(corrected.names) != pending["names"]:
    ...                       # a different set: read it back again
    return True
if answer == "yes" or corrected.names:      # main.py:2352
    await _resume_confirmed_tests(session, pending, lang)   # PRICE IT
```

A turn whose grounded set equals the pending set is treated as **yes**. When the recogniser garbles the same word the same way twice, "the caller said it again" is not independent evidence, and the wrong set is priced. Replaying block 4 gives exactly this sequence: attempt 1 → [Malaria, D] read back; attempt 2 → [Malaria, Uric, D] differs → read back; attempt 3 → [Malaria, Uric, D] equals pending → priced. Block 8: attempt 1 [Thyroid, Blood Group, D] read back; attempt 2 identical → priced ₹2700.

### 4.3 Why D wins over B12 (and Blood Group over Blood Sugar)

`agent/form_index.py:132` `FormTable.best` scores every spoken form against every window of the utterance with `difflib.SequenceMatcher.ratio()`; `agent/entity_grounding.py:186` `extract_tests` keeps the **single best** at or above `COMMIT_FLOOR = 0.72`. There is **no margin check** in this path. (`fast_path._ambiguous_test` / `AMBIGUITY_MARGIN = 0.2` exists but only guards the single-test fast-path answer.)

| Heard                                    | Winner                                  | Runner-up                                                      | Margin | Result               |
| ---------------------------------------- | --------------------------------------- | -------------------------------------------------------------- | ------ | -------------------- |
| `विटामिन ब ल्वेल्वे`   | Vitamin D`विटामिन डी` 0.84   | Vitamin B12`विटामिन बी बारह` 0.75               | 0.09   | D priced             |
| `विटामिन बी ल्वेल्वे` | Vitamin D 0.90                          | Vitamin B12 0.80                                               | 0.10   | D priced             |
| `ब्लड शुगर`                    | Blood Group`ब्लड ग्रुप` 0.74 | Blood Sugar Fasting`ब्लड शुगर फास्टिंग` 0.67 | 0.07   | Blood Group priced   |
| `vitamin vitual`                       | Vitamin D 0.88                          | Vitamin B12 0.78                                               | 0.10   | D read back          |
| `उस होल आप्डोमन`           | USG Whole Abdomen 0.60                  | –                                                             | –     | below floor, nothing |

Both winners were within 0.10 of the runner-up; the design's own constant says anything under 0.20 is "not something to pick between".

### 4.4 The Hindi alias table (clinic-api/i18n_content.py:19–47)

| Test                     | Hindi aliases today                                                                                              | What callers actually said                                                                      |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Vitamin B12              | `विटामिन बी बारह`, `बी बारह`                                                              | `बी ट्वेल्व`, `बी 12`, ASR `ब ल्वेल्वे`                               |
| Blood Sugar Fasting / PP | `ब्लड शुगर फास्टिंग`, `शुगर फास्टिंग`, `शुगर पीपी` …                  | bare`ब्लड शुगर`, `शुगर`                                                         |
| USG Whole Abdomen        | `पेट का अल्ट्रासाउंड`, `होल एब्डोमेन यूएसजी`, `पेट का यूएसजी` | `उस` (ASR), `होल आप्डोमन`, `यूएसजी` alone, `अल्ट्रासाउंड` |
| USG Pregnancy            | `प्रेगनेंसी अल्ट्रासाउंड`, `प्रेगनेंसी यूएसजी`                         | `उस प्रेगनेंसी प्रोफाइल`                                                  |
| HIV                      | `एचआईवी टेस्ट`, `एड्स टेस्ट`                                                             | bare`एचआईवी`                                                                            |

### 4.5 TTS and client evidence

* `agent/speech_norm.py:433` `verbalize_en` does numbers, dates, times and IDs. It has **no acronym handling**. `pronunciation.apply` (letter-by-letter acronyms) is called only for `bn` and `hi`. An English reply is sent to the English FastPitch with "HIV" as a word.
* `agent/speech_norm.py:490` `unspeakable_spans(text, "en")` returns every non-ASCII run. `main.py:1398` `_synthesize_one_clause` turns that into a **pre-recorded fallback clip**; the chat transcript still shows the original reply.
* `main.py:1577` sends `{"type": "handoff_human", "reason": …}`. `static/pcm/index.html:207–210` handles `_ping`, `config`, `stop_playback` and sends everything else to `appendLog(data.sender, data.text)` → `[undefined] undefined`.
* `main.py:3557` `if not text:` → "ASR returned empty text" → re-ask with **no `[User]` line** (the "phantom sorry" turns).
* `agent/channel_quality.py:114` `_is_crosstalk`: "near-continuous, tonal energy … HEURISTIC, not a real overlapping-speech detector".

---

## 5. Expected vs. Actual Behaviour

| Situation                                     | Expected                                                                      | Actual                                                                                                         |
| --------------------------------------------- | ----------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Hindi "B12" garbled (`ब ल्वेल्वे`) | Ask "विटामिन डी या बी बारह?" or read back both candidates    | Priced D (₹1800) after 3 attempts                                                                             |
| Hindi bare "blood sugar"                      | "फास्टिंग या पीपी?"                                             | Read back and priced**Blood Group**                                                                      |
| Same garbled set heard twice                  | Treated as a still-unconfirmed hearing (explicit yes needed)                  | Treated as a yes                                                                                               |
| English "ECG" heard`ec`                     | Recover (`ec`+`test` ↔ ECG) or read back "ECG?"                          | `Which tests do you mean?` ×3                                                                               |
| English "H C V"                               | Collapse spaced letters →`hcv`                                             | Not recognised                                                                                                 |
| Hindi "USG" heard`उस`                     | Ask once with options ("अल्ट्रासाउंड, एक्स-रे, ब्लड…") | `किस टेस्ट की कीमत जाननी है?` ×3                                                       |
| 3 tests named, 2 grounded                     | "I caught A and B; what was the third?"                                       | Read back 2 as if complete (malaria vanished)                                                                  |
| English-locked call, Banglish speech          | Read back in the locked language with canonical English names                 | Read-back containing Bengali script → blocked by the TTS guard → fallback audio, then "Are you still there?" |
| HIV in an English reply                       | "aitch eye vee"                                                               | "hiv" (heard as "হিব")                                                                                      |
| Handoff                                       | Clean "connecting you" line                                                   | Extra`[undefined] undefined` line                                                                            |

---

## 6. Root Cause

There is not one root cause; the incident is **five independent causes that line up** so that a mildly garbled sentence ends in a wrong price or a loop.

**RC-1 (the S1 cause). Repetition is treated as confirmation.**
`_continue_test_confirm` (`main.py:2352`, `if answer == "yes" or corrected.names`) resumes pricing whenever the caller names the *same* set again. ASR errors are not random per attempt. The same speaker saying the same phrase gets the same garble, so the "second independent hearing" is the first error again. *Evidence:* §4.2, §4.1 rows 4 and 8, the 4-attempt English sequence of block 3 (E3c priced only because the set matched).

**RC-2 (the S1 enabler). The grounding step commits the single best match with no ambiguity margin.**
`extract_tests` takes `catalogue.match(...)` best ≥ 0.72 and never asks whether the runner-up is within `AMBIGUITY_MARGIN`. B12/D, Blood Group/Blood Sugar and HIV/HCV are structurally close (shared prefix `विटामिन`, `ब्लड`, `एच`; the Bengali bare `এইচআইভি` scores HCV 0.71 vs HIV 0.70). *Evidence:* §4.3.

**RC-3. The Hindi (and partly English) alias coverage reflects the *written* clinic names, not how callers *say* them.**
Callers mix English words into Hindi. The table has the Hindi number-word `बारह` but not `ट्वेल्व`/`12`; nothing for bare `ब्लड शुगर`, `यूएसजी`, `उस`, `होल आप्डोमन`; the HIV alias carries the word "test" so the bare acronym falls under the floor (0.70). *Evidence:* §4.4, §4.1.

**RC-4. English letter-names and truncated acronyms are not normalised before matching.**
ASR writes `h c v`, `e c g`, `ec`, `h cv`. Short forms are compared exact-only (`exact_below_chars`), spaced letters are not collapsed, and Bengali spaced letters (`এইচ আই ভি`) score 0.64–0.67. Acronyms are the shortest, most confusable strings in the catalogue and the ones callers say most. *Evidence:* §4.1 rows 11, 24, 27; extra score probes in Appendix B.

**RC-5. The recognisers are not told the clinic's vocabulary.**
There is no hot-word / context biasing anywhere in the ASR calls (`agent/asr.py` runs plain greedy CTC/RNNT with `logprobs=False`). Everything after ASR is repair. When the ASR output is far from any alias (`dingo enis one`, `jvs egv`, `उस`), no downstream rule can recover. This is the dominant cause of the English/Hindi failure *rate*, but only RC-1/RC-2 turn it into a wrong price. *Evidence:* §4.1; the same speaker/session gets Bengali 6/6 (the Bengali engine and the Bengali alias list are the best-covered path).

**Secondary, independent causes**

* **RC-6 (mixed-script read-back under the English lock; full analysis in Part A, section 3.1).** `test_confirm_prompt(forms, …)` echoes the caller's own words (`forms`). Banglish → Bengali-script forms → inside an English template → `unspeakable_spans` blocks it → fallback clip. The caller never hears the read-back and then hears "Are you still there?".
* **RC-7 (English TTS has no acronym path).** `verbalize_en` has no acronym spelling.
* **RC-8 (no recovery ladder).** After a failed grounding the agent repeats the same question; there is no escalation to options, a category question, or a spell-it prompt.
* **RC-9 (empty-ASR re-asks right after a read-back).** `if not text:` re-asks silently. **[hypothesis]** the caller's short "yes/হ্যাঁ" or a breath falls under the minimum speech length or is clipped by the agent-speaking gate; cannot be proven without the WAVs/logs.
* **RC-10 (crosstalk heuristic fires on a slow single speaker).** Slow, continuous speech with the browser's noise suppressor removing the floor looks "near-continuous tonal" to `_is_crosstalk`. **[hypothesis]**; the rule is documented as a heuristic.
* **RC-11 (client).** `index.html` does not special-case the `handoff_human` frame.

---

## 7. Contributing Factors

1. **Confirmation is the only safety net, and RC-1 removes it exactly when ASR is systematically wrong.** Every price is preceded by a read-back (`TEST_CONFIRM_ALWAYS = True`), which is why *first-attempt* errors never became wrong prices. The wrong prices appear only on attempt 2 or 3.
2. **Read-back uses `ঠিক তো?`-style yes/no, not a choice.** A caller who hears "Vitamin D?" has to produce a correction from scratch.
3. **The sticky language lock (ADR 0003)** correctly refuses to switch, but then needs the recogniser for the *spoken* language and the *reply* language to be handled independently. The Banglish speaker under an English lock is a combination nobody designed for (RC-6).
4. **Browser `noiseSuppression: true`** can eat quiet consonants and unstressed syllables ("g" in ECG, "t" in "twelve") and gives the same garble run-to-run. Block 11 vs 13 (same words, next day works) is consistent with a device/gain/day effect. **[hypothesis]**.
5. **Hindi test-name speech is code-mixed English.** The Hindi ASR model writes English letter names as Hindi words (`USG → उस`, `IgG → इग`, `HBsAg HIV → आइचीफी`).
6. **Catalogue closeness.** Several pairs are one letter apart (HIV/HCV, Blood Group/Blood Sugar, Dengue NS1/IgG, Vitamin B12/D).
7. **Error messages are generic.** `Which tests do you mean? Could you say the names?` carries no options and no hint.
8. **The test was run with a slow, deliberate voice for the later attempts** (the owner's notes). The agent has no signal that "slow repeat" means "I am fixing your mistake".
9. **Pod down at analysis time:** no logs, so RC-9/RC-10 stay hypotheses.

---

## 8. Detection and Prevention Gap

| Layer                               | What existed                                                                  | Why it missed this                                                                                                                                                                                               |
| ----------------------------------- | ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Unit tests                          | 1,100+ tests on grounding, groups, lay terms, flows                           | They feed**clean** Bengali text. No test replays real ASR garble, and none asserts "same garble twice must not price". The Hindi/English alias tables were never tested with spoken-English-in-Hindi forms |
| Truth-boundary rule (`CLAUDE.md`) | "The LLM may decide what the caller wants; it may never decide what is true." | The wrong price came from the*deterministic* layer, which the rule does not constrain. The grounding layer was treated as ground truth                                                                         |
| `AMBIGUITY_MARGIN`                | Exists and is documented                                                      | Wired into the fast-path single answer only;`extract_tests` (the multi-test path every price uses) bypasses it                                                                                                 |
| Confidence signals                  | `decoder_agreement` per engine, `is_low_confidence`, `confidence_state` | Not used to harden the confirm step (a low-agreement turn can still be accepted on a repeat)                                                                                                                     |
| Monitoring                          | Counters (`corrections`, buckets), per-turn logs                            | No alert on "priced after ≥2 read-backs", "pricing a test not in the first read-back", or "agent-spoke ≠ agent-displayed" (TTS fallback)                                                                       |
| Language-lock tests                 | Sticky lock verified with whole-call probes                                   | Probes are text-in; ASR script mixing was never part of them                                                                                                                                                     |
| Live testing                        | One owner, three languages                                                    | Found the issues, but there is no corpus to rerun after each change                                                                                                                                              |

---

## 9. Immediate Mitigation

Until fixes ship (all can be done in minutes, none changes a model):

1. **Config/one-line:** make a *same-set repeat* require an explicit yes. In `_continue_test_confirm`, change `if answer == "yes" or corrected.names:` to `if answer == "yes":` and re-read the set once ("ठीक सुना? हाँ या नहीं?"). Cost: one extra turn in the case that was already failing.
2. **Data-only:** add the missing Hindi/English/Bengali alias spellings in Appendix C (`i18n_content.py`; they are filled at boot only when the DB column is empty, so a reseed or a one-off update of `aliases_hi` is needed on the pod).
3. **Operational:** tell testers to say acronyms as words ("एच सी वी", "ई सी जी") until D4 ships; use English names under an English lock (no Banglish).
4. **Observability:** on the next pod, keep `/workspace/logs/pcm_app.log`, log `decoder_agreement` per engine and keep `utterance_wav` for the failing turns so RC-9/RC-10 can be closed.

---

## 10. Corrective and Preventive Actions

Owner column names the role, not a person. Priority: P0 = before next live test, P1 = this sprint, P2 = next sprint. Every action has a success criterion that can be run offline.

| ID  | Action                                                                 | Owner        | Priority | Deadline    | Success Criterion                                                                                                                                                     | Status   |
| --- | ---------------------------------------------------------------------- | ------------ | -------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| A1  | Remove "same set again = yes" (D1)                                     | Backend      | P0       | next deploy | Replay of blocks 4 and 8: no price spoken without an explicit yes; test added                                                                                         | Proposed |
| A2  | Ambiguity margin in`extract_tests` + "A या B?" choice prompt (D2)  | Backend      | P0       | next deploy | `ब्लड शुगर`, `विटामिन ब ल्वेल्वे`, bare `एचआईवी` → a question naming both candidates; 0 silent wrong picks on the Appendix B set | Proposed |
| A3  | Alias additions, Hindi/English/Bengali (D3)                            | Content + QA | P0       | next deploy | Every Appendix C form grounds to the right test; no new false positives on the 1,100-test suite                                                                       | Proposed |
| A4  | Letter-name collapse and short-acronym recovery (D4)                   | Backend      | P1       | +1 sprint   | `h c v`, `e c g`, `এইচ আই ভি`, `ec test` → HCV, ECG, HIV, ECG (with read-back)                                                                        | Proposed |
| A5  | Recogniser biasing / post-correction (D5)                              | ML           | P1       | +1 sprint   | Re-run of the 27 blocks: ≥ 85% first-attempt correct set (baseline Hindi/English ≈ 35%)                                                                             | Proposed |
| A6  | Locked-language read-back uses canonical names only (D6)               | Backend      | P0       | next deploy | No non-Latin character in any English reply; test over the whole phrase table                                                                                         | Proposed |
| A7  | English acronym spelling in`verbalize_en` (D7)                       | Backend      | P1       | +1 sprint   | "HIV", "HBsAg", "HCV", "ECG" spelled letter by letter; listening check                                                                                                | Proposed |
| A8  | Recovery ladder (D8)                                                   | Backend      | P1       | +1 sprint   | After 2 failures the agent offers options; no identical re-ask twice in a row                                                                                         | Proposed |
| A9  | Partial-grounding disclosure (D14)                                     | Backend      | P1       | +1 sprint   | "You named three, I caught two" when the sentence enumerates more names than grounded                                                                                 | Proposed |
| A10 | Empty-ASR / short-turn handling and logging (D9)                       | Backend      | P1       | +1 sprint   | A clip < 400 ms after a read-back is treated as a probable yes/no, not an error; logged                                                                               | Proposed |
| A11 | Crosstalk guard (D10)                                                  | Backend      | P2       | +2 sprints  | No crosstalk reply on a single enrolled speaker (`SpeakerChangeDetector` agrees)                                                                                    | Proposed |
| A12 | Client: ignore non-chat frames (D11)                                   | Frontend     | P0       | next deploy | No`[undefined]` lines in the transcript pane                                                                                                                        | Proposed |
| A13 | Build a replay corpus from this report (Appendix B/C) and run it in CI | QA           | P0       | next deploy | One command prints per-turn expected vs grounded; CI fails on a wrong price                                                                                           | Proposed |
| A14 | Per-engine agreement and "priced after N read-backs" metrics and alert | Platform     | P1       | +1 sprint   | Alert fires on any price whose test was not in the first read-back                                                                                                    | Proposed |

**Top-5 suggestions per problem** (ranked, ★ = recommended; "cost" is relative)

**D1 – repeat treated as yes (S1).**

1. ★ Require an explicit yes/हाँ/হ্যাঁ for the price; a repeat re-reads the set once. *(tiny)*
2. Accept a repeat as yes **only** when both attempts have `decoder_agreement` ≥ 0.9 and `exact=True`. *(small)*
3. On a repeat with the same set, switch the prompt from yes/no to a choice ("Vitamin D, ya Vitamin B12?"). *(small)*
4. Count read-backs per set; on the 2nd, hand the choice to a spelled-letter question ("B-1-2 ya D?"). *(medium)*
5. Add a runtime guard: never state a price for a test that was not the subject of the **last spoken read-back**. *(small, defence in depth)*

**D2 – no ambiguity margin in grounding.**

1. ★ Apply `AMBIGUITY_MARGIN` inside `extract_tests`; return both candidates with a "which one?" flag. *(small)*
2. Keep a "confusable pairs" table (`agent/confusables.py` already computes it) and always ask on those pairs. *(small)*
3. Score with a prefix-discount: strip shared generic prefixes (`विटामिन`, `ब्लड`, `डेंगू`) before comparing the distinctive tail. *(medium)*
4. Raise `COMMIT_FLOOR` per-pair (0.9 for HIV/HCV, B12/D). *(small)*
5. Put the runner-up in the read-back ("…विटामिन डी, या शायद बी बारह?"). *(small)*

**D3 – Hindi/English alias coverage.**

1. ★ Add the spoken forms in Appendix C (12 Hindi, 9 English, 4 Bengali). *(tiny)*
2. Generate aliases mechanically: English name → Devanagari/Bengali transliterations with number-word variants (`twelve/ट्वेल्व/बारह/१२`). *(medium)*
3. Capture and label unmatched utterances from real calls weekly and add them (the existing plan in `REMINDERS-if-client-asks.md`). *(process)*
4. Drop the word "test" from acronym aliases (`एचआईवी`, `এইচআইভি`) so bare names clear the floor. *(tiny)*
5. Add the group nouns as soft aliases (`शुगर`, `ब्लड शुगर`) that always return a "which one" choice rather than a test. *(small)*

**D4 – spaced letters and truncated acronyms.**

1. ★ Collapse runs of single letters/letter-names before matching (`h c v`→`hcv`; `এইচ আই ভি`→`এইচআইভি`; `ए च सी वी`). *(small)*
2. Allow a unique-prefix recovery for ≤ 4-letter acronyms next to "test" (`ec test`→ECG) **only with read-back**. *(small)*
3. Normalise letter-name words (`e`, `see`, `gee`, `aitch`, `bee`, `वी`, `जी`). *(small)*
4. Fold the Bengali/Hindi letter-name spelling to Latin acronyms in one table shared by all three languages. *(medium)*
5. When the sentence contains an acronym-shaped token that matches nothing, ask "spell it letter by letter?" *(small)*

**D5 – recogniser garble (ASR).** *(see research in §12)*

1. ★ Contextual biasing from the catalogue's 34 tests × aliases (CTC-WS word spotter; works on the CTC head of the hybrid models) — needs CTC log-probs (`logprobs=True` exists in the call). *(medium, feasibility on the AI4Bharat NeMo 1.23 fork unproven)*
2. SpellMapper-style post-correction with the catalogue as the custom vocabulary. *(medium)*
3. N-best from RNNT beam search into the existing `nbest_rescore.py` (unused today). *(medium)*
4. Route "test-name-expected" turns to the English engine **and** the Indic engine and merge the grounded sets. *(small–medium, latency)*
5. Fine-tune/adapt on 200–500 labelled clinic utterances later. *(large, last)*

**D6 – mixed-script read-back under the lock.**

1. ★ Build read-backs from canonical names in the locked language, never from the caller's raw `forms`. *(small)*
2. If any `forms` span is non-Latin and the lock is `en`, substitute the canonical English name. *(tiny)*
3. Make `_synthesize_one_clause` fail **loudly**: the text on screen and the audio must be the same string; send the fallback text to the UI too. *(small)*
4. Unit test: for every locked language, no reply contains a script the voice cannot speak. *(small)*
5. Let the Banglish caller choose the reply language *explicitly* ("তাহলে বাংলায় বলুন") via the existing explicit-switch path, and prompt once. *(owner decision; conflicts with ADR 0003 default)*

**D7 – English TTS "HIV".**

1. ★ In `verbalize_en`, spell known acronyms ("H I V", or "aitch eye vee"). *(tiny)*
2. Reuse `pronunciation.py` lexicon with an `en` column. *(small)*
3. Spell any ALL-CAPS token ≤ 5 letters not in a word list. *(tiny)*
4. Add a listening-test sheet for the 20 clinic acronyms (`review_sheet()` already exists). *(process)*
5. Replace `HIV Test (ELISA)` in English replies by "H I V test". *(tiny)*

**D8 – dead-end re-asks.**

1. ★ After the 2nd failed ask, read a category list ("blood, scan, heart, urine, thyroid?"). *(small)*
2. Offer the nearest 2–3 catalogue names ("Did you mean ECG, HCV or HIV?"). *(small)*
3. Offer the test list ("shall I read the tests?") — a path that already exists. *(tiny)*
4. Ask to **spell** the name, or to say it with its group ("the heart test, ECG"). *(small)*
5. After 3, offer a callback instead of the 4th identical question (this is what the Hindi block ended in, but only after the false "someone else is speaking"). *(small)*

**D9 – empty ASR after a read-back.**

1. ★ Log duration, RMS and VAD span for every empty turn; keep the WAV. *(tiny)*
2. After a read-back treat an empty/very short turn as "possibly yes": ask "हाँ या ना?" once (no "sorry"). *(small)*
3. Lower the minimum speech length for the turn that follows a question. *(small)*
4. Check the echo-gate tail (`hold_gate_for`) against the length of the read-back; shrink it for short answers. *(small)*
5. Add a "second chance" ASR with a boosted gain for empty results (the enhance retry exists for noisy lines only). *(small)*

**D10 – false crosstalk and early escalation.**

1. ★ Do not use `_is_crosstalk` unless `near_end` already saw a second voice (f0 differs). *(small)*
2. Raise the continuous-energy threshold for slow speech (≥ 1.3 s per word). *(small)*
3. Replace the human handoff after 4 failures with the D8 ladder first. *(small)*
4. Tell the caller what to do ("please say the test name only") rather than "someone else is speaking". *(tiny)*
5. Make the crosstalk reply configurable and off by default for laptop-mic clients. *(tiny)*

**D11 – `[undefined] undefined`.**

1. ★ `if (!data.sender || data.text === undefined) return;` in `index.html`. *(tiny)*
2. Give machine frames a `type` and handle `handoff_human` with a status line. *(tiny)*
3. Send a spoken-text echo for the handoff line only. *(tiny)*
4. Add a client-side frame whitelist. *(tiny)*
5. Add a UI test that fails on `undefined` text. *(tiny)*

**D12 – "both/all of them → total" (done).**

1. ★ Keep: `asks_for_all` + `_price_all_of_the_group`. *(shipped)*
2. Add "all of them" for the 34-test paging list (`all` → total of the page). *(small)*
3. Handle a count word that does not match the list ("দুটো" after five). *(small, today ignored on purpose)*
4. Add Hindi Latin spelling variants (`dono`, `sabhi`). *(done)*
5. Add the owner's phrase set from this PDF to the test file. *(tiny)*

**D13 – "group" fallback when only the group word is recognised.** This worked as intended in block 20 (Hindi). Suggestions are refinements:

1. ★ Keep, and say "I only caught *Dengue*" before the list. *(tiny)*
2. Rank members by partial evidence (`नस ओन` → NS1). *(small)*
3. Offer "both" as the first option. *(tiny)*
4. Skip the list when only one member remains plausible and read it back. *(small)*
5. Count the group-fallback rate as a quality metric (it means ASR fell back). *(small)*

**D14 – partial grounding read back as complete.**

1. ★ Count named-test separators (`और`, `আর`, `and`, `,`) and compare with grounded count; if greater, say so. *(small)*
2. Read back what was caught, then ask for the missing one by position ("and what came after uric acid?"). *(small)*
3. Use `spoken_count` (it already exists for "two tests") for this case. *(tiny)*
4. When any segment fails the floor, keep its raw text and ask "what is '<raw></raw>'?". *(medium)*
5. Never price a subset when the sentence has more segments than grounded names. *(small)*

---

## 11. Verification and Regression Testing

**Performed for this report (analysis only, no code change):**

* Replayed 31 user utterances (Appendix B) through `ground_tests` + `match_test_group`; **all 31 match the live transcript's behaviour** (names, drops, group fallbacks).
* Scored 44 phrase/form pairs with `FormTable.best` for the margin and floor evidence (§4.3, Appendix B).
* Read `_continue_test_confirm`, `extract_tests`, `FormTable.best`, `verbalize_en`, `unspeakable_spans`, `_synthesize_one_clause`, `_handoff_to_human`, `index.html`, `channel_quality._is_crosstalk`.

**Regression state before this report** (from the earlier work today): 519 passed (groups + list follow-ups), 1,138 passed (wider run); the only failures are the 4 that predate these changes (2 `test_language_routing_wiring`, 2 `test_lay_term_and_payment_flow`).

**Required before closing (see §14):**

1. Add the replay corpus (Appendix B) as `tests/test_asr_garble_replay.py`: the *input text → expected grounded set or expected question* table.
2. A property test: for any garbled set, **no price is spoken unless the set equals the set of the last read-back AND the caller said an explicit yes**.
3. A listening check for HIV/HBsAg/HCV/ECG in English.
4. A live re-run of the 27 blocks on the next pod and diff against this report.

**Remaining failures:** none are *new*. The 4 pre-existing failures are unrelated to this incident.

---

## 12. Residual Risks and Limitations

* **ASR garble is not fixed by anything above except D5.** A1–A4 turn a wrong price into a question; they do not make the recogniser hear "dengue". Expect more questions in Hindi/English until biasing ships.
* **Biasing feasibility is unproven** on this stack. NeMo documents CTC-WS for hybrid Transducer-CTC models and a GPU-PB boosting tree ([word boosting docs](https://docs.nvidia.com/nemo-framework/user-guide/26.02/nemotoolkit/asr/asr_customization/word_boosting.html), [CTC-WS paper](https://arxiv.org/html/2406.07096v1), [TurboBias](https://arxiv.org/html/2508.07014v2)), but the IndicConformer checkpoints load only in AI4Bharat's NeMo 1.23 fork (`agent/asr.py` header). GPU-PB needs a newer NeMo; CTC-WS needs CTC log-probs from the fork. SpellMapper ([paper](https://arxiv.org/pdf/2306.02317), [docs](https://docs.nvidia.com/deeplearning/nemo/user-guide/docs/en/main/nlp/spellchecking_asr_customization.html)) is English-trained.
* **More aliases raise false positives.** Each Hindi alias must be run against the whole suite and the confusable-pair report.
* **Explicit confirmation costs a turn.** The literature is consistent: explicit confirmation is more reliable and slower, and a wrong implicit confirmation is harder to repair ([Eurospeech &#39;99 problem spotting](https://foreman.virt.dacs.utwente.nl/~theune/PUBS/Problem_spotting_Eurospeech99.pdf), [CAMMIA, Interspeech 2004](https://www.isca-archive.org/interspeech_2004/sagawa04_interspeech.html)). For a price quote the cost is justified.
* **Speaker/device specific:** all 27 blocks are one speaker with Chrome noise suppression on. The accent/voice question the owner raises is **not proven** either way; the microphone path and the recogniser may both contribute.
* **Search coverage gap:** I found no paper specifically on spelled-letter acronyms in Hindi-English ASR; D4's rules are engineering judgement, not literature.
* **Hypotheses open:** RC-9, RC-10, the "ec" truncation and the block 11 vs 13 day effect need the WAVs/logs.

---

## 13. Lessons Learned

1. **A confirmation step is only as strong as its weakest "yes".** Counting a repeat as consent turned a safety net into an amplifier for systematic error. Treat every consent path as a security boundary.
2. **"Best match above a floor" is not a decision rule; "clear winner above a floor" is.** The margin constant existed; it was just not in the path that mattered.
3. **Test with the garble, not with the intent.** The suite proves the agent understands *what a person means*; it never proved it survives *what the recogniser writes*. A replay corpus of real ASR text (this PDF) must be a permanent regression asset.
4. **Alias tables are a data product.** They need an owner, a feedback loop from unmatched utterances, and per-language review; the Hindi table was written from the Hindi names, not from Hindi callers.
5. **Show-and-speak must be the same string.** The UI displayed a read-back the caller could not hear.
6. **Fail the call forward, not in a loop.** Three identical re-asks is a design smell.
7. **Instrument before the pod disappears:** keep the WAV and the per-engine score for failed turns.
8. **Language rules interact.** The "no mid-call language switch" rule is right, but it changed what the read-back must do (D6) and nobody re-checked templates against it.

---

## 14. Closure Criteria

The RCA can be closed when **all** hold:

1. **No wrong-test price is possible by repetition:** the property test in §11 passes; replay of blocks 4 and 8 yields a question, never a price.
2. **Margin rule live:** every pair in `confusables` with margin < 0.2 produces a "which one?" prompt; Appendix B shows 0 silent wrong picks.
3. **Alias coverage:** all forms in Appendix C ground correctly (Hindi 12, English 9, Bengali 4) and the existing 1,100+ tests have no new failures beyond the 4 known.
4. **Acronym handling:** `h c v`, `e c g`, `এইচ আই ভি`, `ec test` ground (with read-back) in the replay.
5. **Speak = show:** no non-Latin character in an English reply; no `[undefined]` line in the transcript pane.
6. **TTS:** HIV/HBsAg/HCV/ECG are spelled letter-by-letter in English (listening check by one native listener).
7. **Recovery ladder:** no identical question twice in a row; a call never ends in a handoff after only "not understood" turns without an options step.
8. **Live re-run** of the 27 blocks on a running pod: ≥ 85% of blocks produce the correct set within two attempts, 0 wrong prices; per-turn engine scores and WAVs captured for any failure.
9. **Owner sign-off** of the Banglish-under-English-lock policy (D6 option 5).

---

# Appendix A. Line-by-line review of every conversation block

Legend: ✅ correct · ⚠️ works with friction · ❌ defect (ID). "ASR" is the user line as it appears in the transcript.

### Block 1 – "which tests are there" (3 languages)

| Lang | User (ASR)                                                                                                                        | Agent                                                                                                                                                    | Verdict                                                             |
| ---- | --------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| bn   | `আপনাদের ওখানে কোন কোন টেস্ট হয়`                                                                     | "মোট 34টা … সিবিসি, ইএসআর, ব্লড সুগার ফাস্টিং, সুগার পিপি, এইচবিএওয়ানসি। আরও শুনবেন…" | ✅                                                                  |
| bn   | (silence)                                                                                                                         | `আপনি কি লাইনে আছেন? আরও শুনবেন…`                                                                                             | ⚠️ silence re-ask while the caller reads a long list (D13a below) |
| bn   | `বলতে থাকুন টেস্টগুলোর নাম` ×2, `আরো বলুন` ×2, `বলতে থাকুন আরও`                  | five tests per page, end with "এই ছিল সব টেস্ট"                                                                                              | ✅ paging state kept (earlier fix)                                  |
| en   | `which tests are there` / `please go on` ×6                                                                                  | 34 tests in 7 pages, "That's all of them."                                                                                                               | ✅                                                                  |
| hi   | `आपका वहाँ पे कौन कौन सा टेस्ट होता है` / `और बोलिए` ×5 / `और बोलिए प्लीस` | 34 tests, "ये थे सारे टेस्ट"                                                                                                                | ✅ (disclosure sentence before the first answer is by design)       |

*Observation (not annotated):* the silence re-ask (`SILENCE_REASK_S=10`, `LIST_REASK_EXTRA_S=8`) can still fire while a caller listens to a list. Low severity.

### Block 3 – blood tests, then price of malaria + uric acid + vitamin B12

| Lang          | User (ASR)                                                                                                                                       | Agent                                                                          | Verdict                                                               |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ | --------------------------------------------------------------------- |
| bn            | `রক্ত পরীক্ষা করাবো`                                                                                                           | group list (23 blood tests) +`এর মধ্যে কোনটা করাতে চান?` | ✅                                                                    |
| bn            | `ম্যালেরিয়া টেস্ট ইউরিক অ্যাসিড আর ভিটামিন ব টেলভে মোট কত টাকা পড়বে`                | read-back Malaria, Uric Acid, Vitamin B12                                      | ✅ (B12 recovered from`ব টেলভে`)                              |
| bn            | (no answer for 10 s)                                                                                                                             | silence re-ask repeating the whole read-back                                   | ⚠️ D13a                                                             |
| bn            | `হ্যাঁ`                                                                                                                                   | 400 + 250 + 1200 = 1850                                                        | ✅                                                                    |
| en            | `i want to do the blood test`                                                                                                                  | English group list                                                             | ✅                                                                    |
| en            | `i want to do malariot uric acid test and vitamin vitual what will be the price`                                                               | read-back**"uric acid and vitamin d"**                                   | ❌ D3, D14 (malaria dropped silently;`vitual`→D 0.88 vs B12 0.78)  |
| en (Banglish) | `ম্যালেরিয়া টেস্ট ইউরিক অ্যাসিড টেস্ট এন্ড ভিটামিন বি টুয়েলভ`                           | read-back*in English with Bengali names*                                     | ❌ D6 (blocked by TTS guard → fallback; then "Are you still there?") |
| en (Banglish) | `আই ওয়ান্ট টু নো … ভিটামিন বি টুয়েলভ`                                                                          | priced 400 + 250 + 1200 = 1850 (same set as pending)                           | ⚠️ correct set, but through D1's path                               |
| hi            | `मैं ब्लड टेस्ट कराना चाहता हूँ`                                                                                      | Hindi blood list                                                               | ✅                                                                    |
| hi            | `मलेरिया टेस्ट यूरिक्स टेस्ट और विटामिन ब ल्वेल्वे करना चाहता हूँ कितना लगेगा` | read-back Malaria,**Vitamin D** (uric dropped)                           | ❌ D2, D3, D14                                                        |
| hi            | repeat with`यूरिक एसिड टेस्ट … ब्लेल्वे टेस्ट`                                                                     | read-back Malaria, Uric,**Vitamin D**                                    | ❌ D2, D3 (the correction fixed uric, not D)                          |
| hi            | third repeat                                                                                                                                     | **priced Malaria 400 + Uric 250 + Vitamin D 1800 = ₹2450**              | ❌**D1** (S1)                                                   |

### Block 6 – "is X-ray done there"

| Lang | User (ASR)                                         | Agent                                                                                                     | Verdict                                          |
| ---- | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| bn   | `আপনাদের ওখানে এক্স রে হয়` | "এক্স-রে টেস্ট আমাদের এখানে একটাই: বুকের এক্স-রে 400 টাকা"      | ✅ (single member answered directly)             |
| en   | `can i do the x ray there` / `yes`             | "You want the Chest X-Ray (PA view) tests, is that right?" → 400 + counter note                          | ✅ ⚠️ grammar: "tests" for one test (cosmetic) |
| hi   | `वहां पे एक्सरे होता है`       | "एक्स-रे टेस्ट हमारे यहाँ एक ही है: छाती का एक्स-रे 400 रुपये" | ✅                                               |

### Block 7 – sugar PP + thyroid profile + vitamin B12

| Lang | User (ASR)                                                                                                                                                                | Agent                                                                    | Verdict                        |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ------------------------------ |
| bn   | `আমি সুগার পিপি হাইরেয়েড প্রোফাইল আর ভিটামিন বি টেলভে টেস্ট করব মোট কত টাকা পড়বে` / `হ্যাঁ` | 120 + 700 + 1200 = 2020                                                  | ✅                             |
| en   | `i will do sugar peep hide profile and vitamin b twelve ts what will be the price` / `yes`                                                                            | read-back Thyroid, Blood Sugar PP, Vitamin B12 → 2020                   | ✅ (ASR`peep hide` repaired) |
| hi   | `मैं आयरॉइड प्रोफाइले ब्लड शुगर और विटामिन ब ल्वेल्वे टेस्ट कराना चाहता हूँ कितना लगेगा`        | read-back Thyroid,**Blood Group**, **Vitamin D**             | ❌ D2, D3                      |
| hi   | repeat (`थायरॉइड प्रोफाइले ब्लड शुगर और विटामिन ब ल्वेल्वे का दाम जानना चाहता हूं`)                         | **priced Thyroid 700 + Blood Group 200 + Vitamin D 1800 = ₹2700** | ❌**D1** (S1)            |

### Block 10–13 – ECG

| Lang          | User (ASR)                                                                          | Agent                                                 | Verdict                                          |
| ------------- | ----------------------------------------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------ |
| bn            | `ইসিজি টেস্টের দাম কত` / `হ্যাঁ`                          | read-back, 300                                        | ✅                                               |
| en            | `i'd like to know the price of ec test`                                           | `Which tests do you mean? Could you say the names?` | ❌ D4/D5 (`ec`; 0.29 vs anything; no recovery) |
| hi            | `एसीजी टेस्ट का प्राइस क्या है` / `हाँ बिलकुल` | read-back "ईसीजी" → 300                         | ✅                                               |
| en (next day) | `i would like to know the price of ecg`                                           | read-back ECG → 300                                  | ✅ (ASR varied by day/device)                    |

### Block 15 – Pap smear + USG whole abdomen + USG pregnancy profile

| Lang | User (ASR)                                                                                                                                                       | Agent                                                                                                                                         | Verdict                                          |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| bn   | `আমি প্যাপসফিয়ার প্রেগন্যান্সি আলট্রাসাউন্ড আর পেটের আলট্রাসাউন্ড করব …`                       | read-back 3 tests ✅, then**`দুঃখিত। আমি ঠিক বুঝতে পারিনি`** with no user line                                | ❌ D9 (empty-ASR turn after a correct read-back) |
| bn   | repeat with`পড়ব`                                                                                                                                          | priced 1600 + 900 + 1500 = 4000                                                                                                               | ✅ (correct set, through D1's path)              |
| en   | `i want to do papsmere and ussh abdomen and whole pregnancy profile so what will be the cost` / `yes absolutely` / `thank you`                             | read-back 3 tests → 4000 → "You are welcome"                                                                                                | ✅                                               |
| hi   | `मैं पप्स्मियर उस प्रेगनेंसी प्रोफाइले करना चाहता हूँ कितना लगेगा`                                      | read-back**Pap Smear only**; then `माफ़ कीजिए … धीरे बोलेंगे` (no user line)                                     | ❌ D3, D14, D9                                   |
| hi   | `उस होल आप्डोमन और उस प्रेग्नेंसी प्रोफाइले कराना चाहता हूँ`                                                  | `अच्छा, बताती हूँ` + `ठीक से सुन नहीं पाई` + `…समझ नहीं पाई` (two re-asks, no user line between) | ❌ D3, D8, D9                                    |
| hi   | `मैं पप्स्मियर उस होल आप्डोमन और उस प्रेगनेंट से प्रोफाइले कराना चाहता हूँ कितना लगेगा` | read-back Pap Smear only                                                                                                                      | ❌ D3, D14                                       |

### Block 17 – "USG, both"

| Lang | User (ASR)                                                           | Agent                                                                                                                             | Verdict                                                   |
| ---- | -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| bn   | `আমি ইউএসজি করাবো কত পড়বে`                   | "আলট্রাসাউন্ডের টেস্ট 2টা: পেটের 1500, প্রেগন্যান্সি 1600। কোনটা করাতে চান?" | ✅                                                        |
| bn   | `দুটোই করাবো`                                            | **same list again** (before the fix)                                                                                        | ❌→ ✅ D12 (now 1500 + 1600 = 3100; verified on the pod) |
| en   | `i want to do usg so what will be the price`                       | "We have 2 ultrasound tests… Which one would you like to book?"                                                                  | ✅                                                        |
| hi   | `मैं उस कढ़ना चाहता हूँ कितना लगेगा`   | `किस टेस्ट की कीमत जाननी है, ज़रा बताइए?`                                                         | ❌ D3, D8 (`उस` is the word "that")                   |
| hi   | `मैं उस कराना चाहता हूं`                         | booking-is-at-the-counter note (a book intent with no test)                                                                       | ⚠️ unhelpful but harmless                               |
| hi   | `मैं उस कढ़ाना चाहता हूँ कितना लगेगा` | `किस टेस्ट की कीमत जाननी है?` (identical)                                                                  | ❌ D8                                                     |

### Block 20–21 – dengue NS1 + IgG

| Lang | User (ASR)                                                                                                               | Agent                                                                                                                 | Verdict                                                                                |
| ---- | ------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| bn   | `আমি ডেঙ্গু এনএসওয়ান আর ডেঙ্গু আইজিজি করাব মোট কত করবে` / `হ্যাঁ` | read-back NS1 + IgG → 1800                                                                                           | ✅                                                                                     |
| en   | `i want to do dingo enis one and dingo i gzi what will be the cost`                                                    | `Alright… Which tests do you mean? Could you say the names?`                                                       | ❌ D5 (`dingo enis one` 0.67 vs `elisa`; nothing ≥ 0.72)                          |
| hi   | `मैं डेंगू इग और डेंगू नस ओन करना चाहता हूँ कितना लगेगा`                    | "डेंगू के टेस्ट 2 हैं… कौन सा करवाना चाहेंगे?" →`दोनों` → 900 + 900 = 1800 | ✅ the**group fallback worked** (D13) and the new "all/both" path priced the sum |

### Block 23–24 – HBsAg + HCV + HIV

| Lang          | User (ASR)                                                                                                                       | Agent                                                                                                                                                                                                                             | Verdict                                                                            |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| bn            | `আমি এইচভিএসএইজি এইচসিভি আর এইচআইভি টেস্ট করাব মোট কত পড়বে` / `হ্যাঁ` | read-back HCV, HBsAg, HIV → 1500                                                                                                                                                                                                 | ✅                                                                                 |
| en            | `i want to jvs egv and hiv tests what will be the price`                                                                       | read-back**"hiv test" only**                                                                                                                                                                                                | ❌ D5, D14                                                                         |
| en (Banglish) | `আই ওয়ান্ট এইচ সি ভি এইচ বি এসএইজি এন্ড এইচ আই ভি টেস্ট`                           | read-back**HCV, HBsAg** in Bengali script inside English; HIV dropped                                                                                                                                                       | ❌ D4 (`এইচ আই ভি` 0.67), D6                                              |
| en            | `i want to do hvsg and hiv test`                                                                                               | read-back HIV only                                                                                                                                                                                                                | ❌ D5                                                                              |
| en            | `i want to do hbsgh hcv and hiv test` / `yes`                                                                                | read-back 3 → 600 + 400 + 500 = 1500                                                                                                                                                                                             | ✅ after 4 attempts; HIV spoken as "hiv" → "হিব" ❌ D7                         |
| hi            | `मैं और आइचीफी तीनों टेस्ट करना चाहता हूँ कितना लगेगा`                              | `कौन कौन से टेस्ट की बात…` → two `पास से किसी और की आवाज़…` → `समझ नहीं पाई` → `[undefined] undefined` → `मैं आपको स्टाफ़ से जोड़ रही हूँ` | ❌ D8, D10, D11 (escalated a single, slow speaker after 4 non-understanding turns) |

### Block 26–27 – HCV + ECG

| Lang          | User (ASR)                                                                                         | Agent                                                                      | Verdict                                      |
| ------------- | -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- | -------------------------------------------- |
| bn            | `আমি হর ইক টেস্ট করব মোট কত পড়বে`                                      | read-back**"ইকো টেস্টের দাম"** (Echo)                   | ⚠️ wrong guess, protected by the read-back |
| bn            | `আমি এইচসিভি আর ইসিজি টেস্টের দাম জানতে চাই` / `হ্যাঁ` | 600 + 300 = 900                                                            | ✅                                           |
| en            | `i want to do h cv and ec test what will be the price`                                           | `Which tests do you mean?`                                               | ❌ D4                                        |
| en (Banglish) | `এইচসিভি অ্যান্ড ইসিজি`                                                       | read-back (Bengali script) +`Sorry. I did not understand` (no user line) | ❌ D6, D9                                    |
| en            | `i want to know h c v and ec test`                                                               | (no reply captured; the call ended)                                        | ❌ D4                                        |
| bn            | `ম্যানে এইচসিভি ওর ইসিজি টেস্ট কা দাম জানা চা তাহ`        | `আপনি কি এইচসিভি-এর কথা বলছেন?` (only HCV asked)  | ⚠️ D14                                     |
| hi            | `मैं ह और टेस्ट का दाम जानना चाहता हूं`                             | `किस टेस्ट की कीमत जाननी है?`                       | ❌ D5                                        |
| hi            | `आइट और`                                                                                    | `नाम ठीक से सुन नहीं पाई…`                            | ❌ D5                                        |
| hi            | `एचसीवी और ईसीजी` / `हाँ`                                                      | 600 + 300 = 900                                                            | ✅                                           |

---

# Appendix B. Offline reproduction output (grounding layer)

Method: `Catalogue(payload)` from the live seed; `ground_tests(text, lang, catalogue)`; `match_test_group(text, test_rows)`.

```
B3  [bn] ম্যালেরিয়া টেস্ট ইউরিক অ্যাসিড আর ভিটামিন ব টেলভে …      -> Malaria, Uric Acid, Vitamin B12
E3a [en] i want to do malariot uric acid test and vitamin vitual …   -> Uric Acid, Vitamin D            (malaria dropped)
E3b [en] ম্যালেরিয়া টেস্ট ইউরিক অ্যাসিড টেস্ট এন্ড ভিটামিন বি টুয়েলভ -> Malaria, Uric, B12  (Bengali-script forms)
H3a [hi] मलेरिया टेस्ट यूरिक्स टेस्ट और विटामिन ब ल्वेल्वे …        -> Malaria, Vitamin D            (uric dropped)
H3b [hi] … यूरिक एसिड टेस्ट और विटामिन ब ल्वेल्वे टेस्ट            -> Malaria, Uric Acid, Vitamin D
H3c [hi] … और विटामिन बी ल्वेल्वे                                     -> Malaria, Uric Acid, Vitamin D  == pending -> PRICED
E7  [en] i will do sugar peep hide profile and vitamin b twelve ts   -> Thyroid, Blood Sugar PP, Vitamin B12   (correct)
H7a [hi] मैं आयरॉइड प्रोफाइले ब्लड शुगर और विटामिन ब ल्वेल्वे …      -> Thyroid, Blood Group, Vitamin D
H7b [hi] मैं थायरॉइड प्रोफाइले ब्लड शुगर … का दाम …               -> Thyroid, Blood Group, Vitamin D == pending -> PRICED
E10a[en] i'd like to know the price of ec test                       -> []
H10 [hi] एसीजी टेस्ट का प्राइस क्या है                              -> ECG
B15 [bn] আমি প্যাপসফিয়ার প্রেগন্যান্সি আলট্রাসাউন্ড আর …            -> USG Pregnancy, Pap Smear, USG Whole Abdomen
E15 [en] i want to do papsmere and ussh abdomen and whole pregnancy… -> Pap Smear, USG Whole Abdomen, USG Pregnancy
H15a[hi] मैं पप्स्मियर उस प्रेगनेंसी प्रोफाइले …                    -> Pap Smear
H15b[hi] उस होल आप्डोमन और उस प्रेग्नेंसी प्रोफाइले …               -> []
H15c[hi] मैं पप्स्मियर उस होल आप्डोमन और उस प्रेगनेंट से …         -> Pap Smear
H17a[hi] मैं उस कढ़ना चाहता हूँ कितना लगेगा                         -> []
E20 [en] i want to do dingo enis one and dingo i gzi …              -> []
H20 [hi] मैं डेंगू इग और डेंगू नस ओन …                             -> Dengue NS1  + group=dengue
B23 [bn] আমি এইচভিএসএইজি এইচসিভি আর এইচআইভি টেস্ট …                -> HCV, HBsAg, HIV
E23a[en] i want to jvs egv and hiv tests …                           -> HIV
E23b[en] আই ওয়ান্ট এইচ সি ভি এইচ বি এসএইজি এন্ড এইচ আই ভি টেস্ট    -> HCV, HBsAg              (HIV dropped)
E23c[en] i want to do hvsg and hiv test                              -> HIV
E23d[en] i want to do hbsgh hcv and hiv test                         -> HCV, HBsAg, HIV
H23 [hi] मैं और आइचीफी तीनों टेस्ट …                                -> []
B26a[bn] আমি হর ইক টেস্ট করব মোট কত পড়বে                            -> 2D Echo
E26a[en] i want to do h cv and ec test …                             -> []
E26b[en] i want to know h c v and ec test                            -> []
H26a[hi] मैं ह और टेस्ट का दाम जानना चाहता हूं                      -> []
H26b[hi] आइट और                                                      -> []
```

Form-score evidence (best-scoring form per test, `COMMIT_FLOOR = 0.72`):

```
hi  'विटामिन ब ल्वेल्वे'   D 0.84 | B12 0.75 | Creatinine 0.47
hi  'विटामिन बी ल्वेल्वे'   D 0.90 | B12 0.80
hi  'ब्लड शुगर'            Blood Group 0.74 | Sugar Fasting 0.67 | Sugar PP 0.62
hi  'उस होल आप्डोमन'       USG Whole Abdomen 0.60            (below floor)
hi  'उस प्रेगनेंसी प्रोफाइले' USG Pregnancy 0.74 | Lipid Profile 0.70
hi  'डेंगू इग'              NS1 0.63 | IgG 0.60               (below floor)
hi  'डेंगू नस ओन'           NS1 0.83
en  'vitamin vitual'       D 0.88 | B12 0.78
en  'ec' / 'ec test'       best 0.44 (nothing)  — 'ecg' alone = 1.00
en  'e c g' / 'h c v'      0.35 / 0.50 (nothing) — 'hcv' alone = 1.00
en  'dingo enis one'       'elisa' 0.67 (nothing, and wrong test if it had passed)
en  'jvs egv' / 'hvsg'     0.38 / 0.67 (nothing)
bn  'এইচ আই ভি টেস্ট'      HIV 0.67 | HCV 0.60              (below floor)
bn  'এইচআইভি'              HCV 0.71 | HIV 0.70              (HIV alias carries "test")
bn  'এইচ বি এসএইজি'        HBsAg 0.74
```

---

# Appendix C. Suggested alias / normalisation additions (to review with a native listener before shipping)

| Language | Test                       | Add as spoken forms                                                                                                                                                           | Reason                                               |
| -------- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| hi       | Vitamin B12                | `विटामिन बी ट्वेल्व`, `बी ट्वेल्व`, `विटामिन बी 12`, `बी 12`, `विटामिन बी ल्वेल्व`                              | English number inside Hindi; ASR`ल्वेल्वे` |
| hi       | Blood Sugar (Fasting / PP) | bare`ब्लड शुगर`, `शुगर`, `शुगर टेस्ट` → **choice**, not a test                                                                              | RC-2                                                 |
| hi       | USG Whole Abdomen          | `यूएसजी`, `अल्ट्रासाउंड`, `होल एब्डोमेन`, `होल अब्डोमेन`, `होल आप्डोमन`, `यूएसजी होल एब्डोमेन` | ASR`आप्डोमन`                                |
| hi       | USG (either)               | `उस` **only next to `होल`/`प्रेगनेंसी`/`अल्ट्रासाउंड` windows**, never alone                                                         | `उस` is a common Hindi word                      |
| hi       | USG Pregnancy              | `प्रेगनेंसी प्रोफाइल`, `यूएसजी प्रेगनेंसी प्रोफाइल`, `प्रेग्नेंसी प्रोफाइल`                                | block 15                                             |
| hi       | Dengue IgG                 | `डेंगू इग`, `डेंगू आई जी जी`, `डेंगू आईजी`                                                                                                   | block 20                                             |
| hi       | Dengue NS1                 | `डेंगू नस वन`, `डेंगू एन एस वन`, `डेंगू नस ओन`                                                                                             | block 20                                             |
| hi       | HIV                        | `एचआईवी`, `एच आई वी` (no "test")                                                                                                                              | bare acronym                                         |
| hi       | HBsAg                      | `एच बी एस ए जी`, `एचबीएस`                                                                                                                                  | spaced letters                                       |
| en       | Vitamin B12                | `vitamin b twelve`, `b twelve`, `vitamin b 12`, `b12`                                                                                                                 | already in part; keep                                |
| en       | ECG                        | `ec`, `e c g`, `ekg`, `e c g test` (acronym recovery, with read-back)                                                                                                 | RC-4                                                 |
| en       | HCV / HBsAg / HIV          | `h c v`, `hcv test`, `h b s a g`, `hbs ag`, `h i v`                                                                                                                 | RC-4                                                 |
| en       | Dengue                     | `dengue`, `dingo`(no: ASR noise — handle by biasing, not alias), `dengu`                                                                                               | RC-5                                                 |
| bn       | HIV                        | `এইচ আই ভি`, `এইচআইভি` (no "টেস্ট")                                                                                                                    | block 24                                             |
| bn       | HCV / HBsAg                | `এইচ সি ভি`, `এইচ বি এস এজি`                                                                                                                             | spaced letters                                       |
| bn       | Pap smear                  | `প্যাপসফিয়ার`, `প্যাপ স্ফিয়ার`                                                                                                                 | block 15                                             |
| bn       | Uric acid                  | `ইউরিক অ্যাসিক`                                                                                                                                                 | block 3                                              |

# Appendix D. Sources consulted

* NVIDIA NeMo, *Word Boosting* (CTC-WS, GPU-PB, Flashlight): https://docs.nvidia.com/nemo-framework/user-guide/26.02/nemotoolkit/asr/asr_customization/word_boosting.html
* *CTC-based Word Spotter for context biasing* (arXiv 2406.07096): https://arxiv.org/html/2406.07096v1
* *TurboBias: Universal ASR Context-Biasing* (arXiv 2508.07014): https://arxiv.org/html/2508.07014v2
* *SpellMapper: neural spellchecker for ASR customization* (arXiv 2306.02317): https://arxiv.org/pdf/2306.02317 ; NeMo docs: https://docs.nvidia.com/deeplearning/nemo/user-guide/docs/en/main/nlp/spellchecking_asr_customization.html
* Problem spotting and verification strategies in spoken dialogue (Eurospeech '99): https://foreman.virt.dacs.utwente.nl/~theune/PUBS/Problem_spotting_Eurospeech99.pdf
* CAMMIA confirmation comparison (Interspeech 2004): https://www.isca-archive.org/interspeech_2004/sagawa04_interspeech.html
* Project files cited by line number: `main.py` (2333–2362, 1398–1410, 1577, 3557), `agent/entity_grounding.py` (186–), `agent/form_index.py` (132–), `agent/fast_path.py` (90–133, 327–), `agent/speech_norm.py` (433, 490), `agent/channel_quality.py` (114), `agent/phrases.py` (167), `clinic-api/i18n_content.py` (19–47), `static/pcm/index.html` (207–210), `agent/asr.py` (header, 157–170)
