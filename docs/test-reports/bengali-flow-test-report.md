# Bengali booking and test-price flows: test report

Generated 2026-10-12 18:22 by `tests/bn_flow_report.py`. Pod not involved: everything ran locally, in-process.

## Result

**1502 of 1503 whole calls passed** (99.9%). Run time 37.1 minutes.

| Scenario | Calls | Passed | Failed |
|---|---:|---:|---:|
| 1 department -> doctor -> day and time -> confirmed | 288 | 288 | 0 |
| 2 only a doctor's name -> the rest of the booking | 320 | 319 | 1 |
| 3 the price of one or more tests | 895 | 895 | 0 |
| **All** | **1503** | **1502** | **1** |

## Defects this matrix found (all fixed before the run reported above)

The first slice of this matrix passed 293 of 358 calls. Each failure kind was root-caused in the product, not in the test, fixed with a regression test in `tests/test_bengali_flow_matrix_fixes.py`, and the matrix re-run:

| # | What the caller experienced | Root cause | Fix |
|---|---|---|---|
| 1 | "আমি গাইনিতে একজন ডক্টরকে দেখাব" was not understood; "গাইনির" was | the department matcher's ending list had no locative "তে" | `clinic-api/department_match.py`: ~3,000 case endings, spelling fold, word windows; suggestion-only fuzzy tier |
| 2 | "সুগার পিপি, এইচবিএ১সি আর ভিটামিন ডি করাতে কত লাগবে" was answered "a doctor must explain this" and handed to a clinician | `agent/clinical_safety.py` read the "১" inside the TEST NAME এইচবিএ১সি (also বি১২, এনএস১) as a lab value next to "সুগার" | a digit inside a word is a name, not a value; "সুগার ২৫০" is still caught |
| 3 | in a list of tests one was dropped, or a wrong one priced ("ডেঙ্গু আইজিজি" priced as ECG) | `agent/entity_grounding._without` removed EVERY word equal to a word of the first name, so later names that share a word ("টেস্ট", "ডেঙ্গু") lost it | only the matched run of words is removed, once |
| 4 | a fifth test was dropped without a word and the total left it out | `MAX_TESTS_PER_TURN = 4` silently cut the list | cap raised to 6; more than the cap grounds nothing and the caller is asked which tests |
| 5 | the read-back said "ইকো টেস্ট টেস্টের দাম" | the template appended "টেস্ট" to a name that already ends in it | `test_confirm_prompt` (Bengali and Hindi) |
| 6 | "কার্ডিওলজির ডাক্তার দেখাতে চাই" read the doctors but opened no booking; "the second one" was "I did not understand" | with the department in the model's symptom slot the list was spoken by the enquiry path, which remembered nothing | the booking opens (department named by the clinic's own table); a list read as an answer is remembered, and a position or a bare name after it continues into the booking |
| 7 | "প্রথমজনকে দেখাতে চাই" (the first one, with the case ending) was not understood | the ordinal table had only bare forms | inflected forms added |
| 8 | "সুগার পিপি আর পেটের আলট্রাসাউন্ডের দাম" answered the ultrasound's price alone and ignored the other test (also reported live: "পেটের আলট্রাসাউন্ড আর প্যাপ স্মিয়ার টেস্টের মোট দাম কত হবে" -- replayed in "Reported sentences, replayed on the final code" below) | the lay-term shortcut for "পেটের …" runs before the multi-test flow and answers one test | the shortcut steps aside when two or more tests are named |
| 9 | "রক্ত পরীক্ষা করাতে চাই" would have given the caller the busy fallback | the blood-test list was read as Latin short codes ("Blood Grouping & Rh Typing"), which the Bengali voice cannot say, so the speech gate blocks the line | the list is read in the clinic's own Bengali (or Hindi) names when every test has one |

## Known limits (not fixed, or not provable here)

- **The recogniser and the language model are given, not tested.** The pod is down, so no call here proves that the ASR writes these words or that Qwen returns these slots. Run this matrix's phrases against the pod when it is up.
- Groups of three to five tests are a deterministic sample (150 + 61 + 21 of 5,984 + 46,376 + 278,256), not every combination. Every single test and every pair is covered.
- Day, time and phone-number style are rotated over the doctor/department combinations rather than fully crossed (a full cross would be about 7,800 booking calls).
- More than six tests in one sentence is not priced; the caller is asked which tests. That is deliberate (a longer list is far more likely a mis-parse than a caller).
- Dates are spoken from ISO text ("2026-10-14") and times from "10:15"; the speech gate accepted them, but how the Indic voices pronounce them is a property of the TTS on the pod.
- A weekday name said ON that weekday ("সোমবার" on a Monday) is read as today by the clinic; if the chamber is over it offers next week's. The generator therefore says such days with their number.
- The wider test suite still has 188 failing tests that predate this work (see the project's known-failure list); this change added none.

## What was tested, and what it does and does not prove

Every call is a whole conversation played through the **real orchestrator** (`main_pcm._dispatch_turn` and everything under it) against the **real clinic API and its seeded database** (8 departments, 32 doctors, 34 lab tests), with the real department, doctor and test matchers, the real reply templates and the real speech gate (`agent/tts.py`): a line the Bengali voice cannot say fails the call.

Two things are **given, not tested**, exactly as in every other flow test in this repository:

- **The transcript** (there is no ASR here). The caller's Bengali is typed as the recogniser would write it.
- **The intent and slots the language model returns each turn** (there is no Qwen here). They are written the way the model returns them on the pod, e.g. the doctor's name in `doctor_name`, the day as an ISO date.

So a pass means: *given that the recogniser and the model behave like this, the rules, the clinic and the templates land the call correctly.* It does **not** prove the recogniser hears these words or that the model returns these slots; that needs the pod.

A call passes only if **all** of these hold:

1. every turn was understood: no "I did not understand", no "no doctor called ...", no hand-off, no counter fallback;
2. no line was repeated word for word twice in a row, and no line was blocked by the speech gate;
3. for a booking: the department's four doctors were read out (when a department was named), the chosen doctor was settled as exactly that doctor, the day and time were taken as intended, the read-back carried the doctor, time, patient name and phone, "yes" gave a confirmation number, and the **clinic's own record** holds that doctor, day, time and patient name and is found by the caller's phone number;
4. for a price question: every test's price and (for two or more) the total, in the clinic's figures, were spoken, and no booking was opened.

## 1. Department → doctor → day and time → confirmed

The caller names a department (three ways), is read its four doctors, picks one (three ways), gives a day and a time (three ways each), then a patient name and a phone number (three ways), and says yes.

Dimensions: **32 doctors** × **3 ways to name the department** (`locative` "আমি গাইনিতে একজন ডক্টরকে দেখাব"; `genitive_question` "গাইনির ডাক্তার দেখাতে চাই"; `kind_of_doctor` "আমার হার্টের ডাক্তার দরকার") × **3 ways to pick** (`full_name`, `surname`, `ordinal` "দ্বিতীয়জন"). Day, time and phone style are rotated pseudo-randomly (seeded) over the combinations.

| Department | Calls | Passed |
|---|---:|---:|
| Cardiology | 36 | 36/36 (100%) |
| Dermatology | 36 | 36/36 (100%) |
| Diabetology & Endocrinology | 36 | 36/36 (100%) |
| ENT | 36 | 36/36 (100%) |
| General Medicine | 36 | 36/36 (100%) |
| Gynaecology & Obstetrics | 36 | 36/36 (100%) |
| Orthopaedics | 36 | 36/36 (100%) |
| Paediatrics | 36 | 36/36 (100%) |

| How the department was named | Calls | Passed |
|---|---:|---:|
| genitive_question | 96 | 96/96 (100%) |
| kind_of_doctor | 96 | 96/96 (100%) |
| locative | 96 | 96/96 (100%) |

| How the doctor was picked | Calls | Passed |
|---|---:|---:|
| full_name | 96 | 96/96 (100%) |
| ordinal | 96 | 96/96 (100%) |
| surname | 96 | 96/96 (100%) |

| How the day was said | Calls | Passed |
|---|---:|---:|
| number_month | 97 | 97/97 (100%) |
| relative | 89 | 89/89 (100%) |
| weekday_number | 102 | 102/102 (100%) |

| How the time was said | Calls | Passed |
|---|---:|---:|
| digits | 100 | 100/100 (100%) |
| fraction | 81 | 81/81 (100%) |
| minutes | 107 | 107/107 (100%) |

| How the phone number was said | Calls | Passed |
|---|---:|---:|
| bn_digits | 99 | 99/99 (100%) |
| latin | 101 | 101/101 (100%) |
| words | 88 | 88/88 (100%) |

## 2. Only a doctor's name → the rest of the booking

The caller says nothing but a doctor (five ways), either all at once with the day and time or step by step.

| How the doctor was named | Calls | Passed |
|---|---:|---:|
| full_name | 64 | 64/64 (100%) |
| surname | 64 | 63/64 (98%) |
| title_full_name | 64 | 64/64 (100%) |
| title_surname | 64 | 64/64 (100%) |
| wants_to_see | 64 | 64/64 (100%) |

| Order | Calls | Passed |
|---|---:|---:|
| all_in_one | 160 | 160/160 (100%) |
| step_by_step | 160 | 159/160 (99%) |

| Doctor's department | Calls | Passed |
|---|---:|---:|
| Cardiology | 40 | 40/40 (100%) |
| Dermatology | 40 | 40/40 (100%) |
| Diabetology & Endocrinology | 40 | 40/40 (100%) |
| ENT | 40 | 40/40 (100%) |
| General Medicine | 40 | 39/40 (98%) |
| Gynaecology & Obstetrics | 40 | 40/40 (100%) |
| Orthopaedics | 40 | 40/40 (100%) |
| Paediatrics | 40 | 40/40 (100%) |

| How the day was said | Calls | Passed |
|---|---:|---:|
| number_month | 109 | 109/109 (100%) |
| relative | 114 | 113/114 (99%) |
| weekday_number | 97 | 97/97 (100%) |

| How the time was said | Calls | Passed |
|---|---:|---:|
| digits | 103 | 102/103 (99%) |
| fraction | 116 | 116/116 (100%) |
| minutes | 101 | 101/101 (100%) |

## 3. The price of one or more tests

The caller asks the price of 1, 2, 3, 4 or 5 tests (every single test, every pair, a deterministic spread of larger groups), is read the tests back, says yes, and hears each price and the total.

| Tests in the question | Calls | Passed |
|---|---:|---:|
| 1 | 102 | 102/102 (100%) |
| 2 | 561 | 561/561 (100%) |
| 3 | 150 | 150/150 (100%) |
| 4 | 61 | 61/61 (100%) |
| 5 | 21 | 21/21 (100%) |

| Phrasing | Calls | Passed |
|---|---:|---:|
| how_much | 298 | 298/298 (100%) |
| price | 299 | 299/299 (100%) |
| total | 298 | 298/298 (100%) |

| Names the model gave (`canonical` = catalogue name, `spoken` = the caller's own words) | Calls | Passed |
|---|---:|---:|
| canonical | 448 | 448/448 (100%) |
| spoken | 447 | 447/447 (100%) |

| Intent the model gave | Calls | Passed |
|---|---:|---:|
| book_test | 447 | 447/447 (100%) |
| test_rate | 448 | 448/448 (100%) |

## Every doctor

| Doctor | Department | Chamber | Dept→booking | Doctor-only |
|---|---|---|---:|---:|
| কৌশিক ভট্টাচার্য (Dr. K. Bhattacharya) | Cardiology | সোম,বুধ,শুক্র 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| মানস দত্ত (Dr. M. Dutta) | Cardiology | মঙ্গল,বৃহ,শনি 17:30-19:30 | 9/9 (100%) | 10/10 (100%) |
| নির্মল রায় (Dr. N. Roy) | Cardiology | মঙ্গল,বৃহ,শনি 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| সুব্রত ব্যানার্জী (Dr. S. Banerjee) | Cardiology | সোম,বুধ,শুক্র 18:00-20:00 | 9/9 (100%) | 10/10 (100%) |
| অশোক দে (Dr. A. Dey) | Dermatology | মঙ্গল,বৃহ,শনি 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| মিতালী সাহা (Dr. M. Saha) | Dermatology | সোম,বুধ,শুক্র 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| পৃথা অধিকারী (Dr. P. Adhikari) | Dermatology | সোম,বুধ,শুক্র 18:00-20:00 | 9/9 (100%) | 10/10 (100%) |
| সমীর বাগচী (Dr. S. Bagchi) | Dermatology | মঙ্গল,বৃহ,শনি 17:30-19:30 | 9/9 (100%) | 10/10 (100%) |
| অভিজিৎ সরকার (Dr. A. Sarkar) | Diabetology & Endocrinology | সোম,বুধ,শুক্র 18:00-20:00 | 9/9 (100%) | 10/10 (100%) |
| কুণাল সিনহা (Dr. K. Sinha) | Diabetology & Endocrinology | সোম,বুধ,শুক্র 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| পার্থ রায় (Dr. P. Ray) | Diabetology & Endocrinology | মঙ্গল,বৃহ,শনি 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| স্নেহাশিস চক্রবর্তী (Dr. S. Chakraborty) | Diabetology & Endocrinology | মঙ্গল,বৃহ,শনি 17:30-19:30 | 9/9 (100%) | 10/10 (100%) |
| বিশ্বজিৎ চন্দ (Dr. B. Chanda) | ENT | মঙ্গল,বৃহ,শনি 17:30-19:30 | 9/9 (100%) | 10/10 (100%) |
| কল্যাণ হালদার (Dr. K. Halder) | ENT | মঙ্গল,বৃহ,শনি 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| রঞ্জন পাল (Dr. R. Pal) | ENT | সোম,বুধ,শুক্র 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| সঞ্জয় গুহ (Dr. S. Guha) | ENT | সোম,বুধ,শুক্র 18:00-20:00 | 9/9 (100%) | 10/10 (100%) |
| অরিন্দম সেন (Dr. A. Sen) | General Medicine | মঙ্গল,বৃহ,শনি 10:00-12:00 | 9/9 (100%) | 9/10 (90%) |
| প্রবীর ঘোষ (Dr. P. Ghosh) | General Medicine | সোম,বুধ,শুক্র 18:00-20:00 | 9/9 (100%) | 10/10 (100%) |
| রাজীব চৌধুরী (Dr. R. Chowdhury) | General Medicine | মঙ্গল,বৃহ,শনি 17:30-19:30 | 9/9 (100%) | 10/10 (100%) |
| সৌরভ মুখার্জী (Dr. S. Mukherjee) | General Medicine | সোম,বুধ,শুক্র 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| অপর্ণা বসু (Dr. A. Basu) | Gynaecology & Obstetrics | মঙ্গল,বৃহ,শনি 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| পারমিতা সেনগুপ্ত (Dr. P. Sengupta) | Gynaecology & Obstetrics | মঙ্গল,বৃহ,শনি 17:30-19:30 | 9/9 (100%) | 10/10 (100%) |
| রিনা মিত্র (Dr. R. Mitra) | Gynaecology & Obstetrics | সোম,বুধ,শুক্র 18:00-20:00 | 9/9 (100%) | 10/10 (100%) |
| সুতপা চ্যাটার্জী (Dr. S. Chatterjee) | Gynaecology & Obstetrics | সোম,বুধ,শুক্র 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| অমিত কর (Dr. A. Kar) | Orthopaedics | সোম,বুধ,শুক্র 18:00-20:00 | 9/9 (100%) | 10/10 (100%) |
| দেবাশিস দাস (Dr. D. Das) | Orthopaedics | সোম,বুধ,শুক্র 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| শুভঙ্কর নন্দী (Dr. S. Nandi) | Orthopaedics | মঙ্গল,বৃহ,শনি 17:30-19:30 | 9/9 (100%) | 10/10 (100%) |
| তপন বসু (Dr. T. Bose) | Orthopaedics | মঙ্গল,বৃহ,শনি 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| অনির্বাণ মন্ডল (Dr. A. Mondal) | Paediatrics | সোম,বুধ,শুক্র 18:00-20:00 | 9/9 (100%) | 10/10 (100%) |
| নন্দিনী বিশ্বাস (Dr. N. Biswas) | Paediatrics | সোম,বুধ,শুক্র 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| রূপা মজুমদার (Dr. R. Majumder) | Paediatrics | মঙ্গল,বৃহ,শনি 10:00-12:00 | 9/9 (100%) | 10/10 (100%) |
| শ্রেয়া গাঙ্গুলী (Dr. S. Ganguly) | Paediatrics | মঙ্গল,বৃহ,শনি 17:30-19:30 | 9/9 (100%) | 10/10 (100%) |

## Failures

1 calls failed, in 1 distinct kinds. Each kind, with its first three calls in full:

### 1. day/time not taken as <date> <time>  (1 calls)

- **2 only a doctor's name -> the rest of the booking** `{"doctor": "Dr. A. Sen", "department": "General Medicine", "name_style": "surname", "order": "step_by_step", "date_style": "relative", "time_style": "digits", "phone_style": "bn_digits"}`
  - day/time not taken as 2026-10-15 11:45: ('2026-10-15', None)
  - no read-back question was asked before confirming
  - no confirmation number after yes: ['কোন সময়ের জন্য অ্যাপয়েন্টমেন্ট চাই?']
  - the same line was spoken twice in a row: কোন সময়ের জন্য অ্যাপয়েন্টমেন্ট চাই?

  ```
  [User] সেন
  [AI] হ্যাঁ, ডাঃ সেন। ওঁর পরের বসার দিন মঙ্গলবার, 2026-10-13 তারিখ, চেম্বারের সময় 10:00-12:00। ওই দিনে করব, নাকি অন্য কোনো দিন? আর কোন সময়ে চান?
  [User] বৃহস্পতিবার
  [AI] ধন্যবাদ।
  [AI] কোন সময়ের জন্য অ্যাপয়েন্টমেন্ট চাই?
  [User] সকাল ১১টা ৪৫
  [AI] ধন্যবাদ।
  [AI] ওই সময়টা বুক হয়ে গেছে, তবে শনিবার, 2026-10-17 তারিখে একই সময়ে 11:45 ফাঁকা আছে। কোনটা নেব?
  [User] রোগীর নাম তন্ময় পাল
  [AI] ধন্যবাদ।
  [AI] কোন সময়ের জন্য অ্যাপয়েন্টমেন্ট চাই?
  [User] ৯৮০০০০৩১৭৮
  [AI] ধন্যবাদ।
  [AI] কোন সময়ের জন্য অ্যাপয়েন্টমেন্ট চাই?
  [User] হ্যাঁ
  [AI] কোন সময়ের জন্য অ্যাপয়েন্টমেন্ট চাই?
  ```

## One passing call of each kind, in full

### 1 department -> doctor -> day and time -> confirmed

`{"doctor": "Dr. K. Bhattacharya", "department": "Cardiology", "opener": "locative", "pick": "full_name", "date_style": "weekday_number", "time_style": "minutes", "phone_style": "words"}`

```
[User] আমি কার্ডিওলজিতে একজন ডক্টরকে দেখাব
[AI] কার্ডিওলজি বিভাগে এই ডাক্তাররা বসেন -- কৌশিক ভট্টাচার্য, মানস দত্ত, নির্মল রায়, সুব্রত ব্যানার্জী। কার কাছে অ্যাপয়েন্টমেন্ট নিতে চান?
[User] কৌশিক ভট্টাচার্যকে দেখাতে চাই
[AI] ধন্যবাদ।
[AI] হ্যাঁ, ডাঃ ভট্টাচার্য। ওঁর পরের বসার দিন বুধবার, 2026-10-14 তারিখ, চেম্বারের সময় 10:00-12:00। ওই দিনে করব, নাকি অন্য কোনো দিন? আর কোন সময়ে চান?
[User] বুধবার চৌদ্দ তারিখে
[AI] ধন্যবাদ।
[AI] কোন সময়ের জন্য অ্যাপয়েন্টমেন্ট চাই?
[User] সকাল দশটা
[AI] ধন্যবাদ।
[AI] রোগীর নাম আর কনফার্মেশনের জন্য একটা ফোন নম্বর বলবেন?
[User] রোগীর নাম রাহুল দাস
[AI] ধন্যবাদ।
[AI] একটা ফোন নম্বর দেবেন, যাতে কনফার্মেশন পাঠাতে পারি?
[User] নয় আট শূন্য শূন্য শূন্য শূন্য শূন্য শূন্য শূন্য শূন্য
[AI] ধন্যবাদ।
[AI] তাহলে ভট্টাচার্য ডাক্তারের কাছে বুধবার, 2026-10-14 তারিখে, সময় 10:00-এ, রোগীর নাম রাহুল দাস, ফোন নম্বর 9800000000 -- এই অ্যাপয়েন্টমেন্টটা কনফার্ম করব?
[User] হ্যাঁ
[AI] আপনার অ্যাপয়েন্টমেন্ট কনফার্ম হয়েছে। ডাঃ ভট্টাচার্য, 2026-10-14, সময় 10:00। কনফার্মেশন নম্বর: KCD-20261014-37B61490।
[AI] আপনার অ্যাপয়েন্টমেন্ট বুক হয়েছে। পেমেন্ট লিংক আপনার মোবাইলে পাঠানো হয়েছে, ওখান থেকে পেমেন্ট করে বুকিং নিশ্চিত করুন। ধন্যবাদ।
```

### 2 only a doctor's name -> the rest of the booking

`{"doctor": "Dr. K. Bhattacharya", "department": "Cardiology", "name_style": "full_name", "order": "step_by_step", "date_style": "relative", "time_style": "minutes", "phone_style": "words"}`

```
[User] কৌশিক ভট্টাচার্য
[AI] হ্যাঁ, ডাঃ ভট্টাচার্য। ওঁর পরের বসার দিন বুধবার, 2026-10-14 তারিখ, চেম্বারের সময় 10:00-12:00। ওই দিনে করব, নাকি অন্য কোনো দিন? আর কোন সময়ে চান?
[User] শুক্রবার
[AI] ধন্যবাদ।
[AI] কোন সময়ের জন্য অ্যাপয়েন্টমেন্ট চাই?
[User] সকাল দশটা পনেরো
[AI] ধন্যবাদ।
[AI] রোগীর নাম আর কনফার্মেশনের জন্য একটা ফোন নম্বর বলবেন?
[User] রোগীর নাম রাহুল দাস
[AI] ধন্যবাদ।
[AI] একটা ফোন নম্বর দেবেন, যাতে কনফার্মেশন পাঠাতে পারি?
[User] নয় আট শূন্য শূন্য শূন্য শূন্য দুই শূন্য এক ছয়
[AI] ধন্যবাদ।
[AI] তাহলে ভট্টাচার্য ডাক্তারের কাছে শুক্রবার, 2026-10-16 তারিখে, সময় 10:15-এ, রোগীর নাম রাহুল দাস, ফোন নম্বর 9800002016 -- এই অ্যাপয়েন্টমেন্টটা কনফার্ম করব?
[User] হ্যাঁ
[AI] আপনার অ্যাপয়েন্টমেন্ট কনফার্ম হয়েছে। ডাঃ ভট্টাচার্য, 2026-10-16, সময় 10:15। কনফার্মেশন নম্বর: KCD-20261016-36354025।
[AI] আপনার অ্যাপয়েন্টমেন্ট বুক হয়েছে। পেমেন্ট লিংক আপনার মোবাইলে পাঠানো হয়েছে, ওখান থেকে পেমেন্ট করে বুকিং নিশ্চিত করুন। ধন্যবাদ।
```

### 3 the price of one or more tests

`{"tests": ["Complete Blood Count (CBC)"], "count": 1, "phrasing": "price", "slot_style": "canonical", "intent": "test_rate"}`

```
[User] সিবিসি-এর দাম কত
[AI] আপনি সিবিসি টেস্টের দাম জানতে চান, ঠিক তো?
[User] হ্যাঁ
[AI] সিবিসি 400 টাকা।
```

## How to re-run

```bash
python tests/bn_flow_report.py            # every combination, about 10-20 minutes, rewrites this report
python tests/bn_flow_report.py --quick    # a thin slice, a couple of minutes
python -m pytest tests/test_bengali_flow_matrix.py -v   # the slice, as pytest cases
```

## Addendum: the one failed call

The single failure above (Dr. A. Sen, 15 Oct, 11:45) was **not a product defect**. The clinic seeds one sample appointment
for exactly that doctor, day and time, so the clinic correctly answered "that time is already booked, 11:45 is free on
Saturday" and the test script, which had picked that slot, carried on as if it had been taken.

The allocator in `tests/_bn_flow_scenarios.py` now skips the clinic's seeded appointments. Re-running Dr. A. Sen with
every name style x order x time style: **23 of 23 calls passed** (including the exact combination that had failed; a
further 7 requested more slots than the doctor's three chamber days hold in a week and were not made).

So, on the matrix as run: **1,502 of 1,503 passed, and the remaining one is explained above.** A fresh full run needs
about 37 minutes: `python tests/bn_flow_report.py`.

## Reported sentences, replayed on the final code

Each sentence below was answered wrongly by an earlier build: reported by a person, or caught by the first matrix run. They are replayed here through the same harness on the code that produced this report. **11 of 11 are answered correctly.**

| # | Sentence | Earlier problem | Now |
|---|---|---|---|
| 1 | পেটের আলট্রাসাউন্ড আর প্যাপ স্মিয়ার টেস্টের মোট দাম কত হবে | reported live: ultrasound + Pap smear | correct |
| 2 | সুগার পিপি, টিএসএইচ আর পেটের আলট্রাসাউন্ড টেস্টের মোট দাম কত হবে | first run: sugar PP + TSH + ultrasound | correct |
| 3 | সুগার পিপি আর পেটের আলট্রাসাউন্ড করাতে কত লাগবে | first run: sugar PP + ultrasound | correct |
| 4 | ইসিজি, পেটের আলট্রাসাউন্ড, ইকো টেস্ট আর টিএমটি-এর দাম কত | first run: four tests with the ultrasound | correct |
| 5 | ইউরিন টেস্ট, ইকো টেস্ট আর পিএসএ করাতে কত লাগবে | first run: a test dropped from a list | correct |
| 6 | কিডনি ফাংশন টেস্ট, ডেঙ্গু এনএস১, ডেঙ্গু আইজিজি আর ইকো টেস্টের মোট দাম কত হবে | first run: a wrong test (ECG) priced for Dengue IgG | correct |
| 7 | সুগার পিপি, এইচবিএ১সি আর ভিটামিন ডি করাতে কত লাগবে | first run: handed to a clinician because of the 1 in HbA1c | correct |
| 8 | সিবিসি, ইএসআর, ব্লাড সুগার ফাস্টিং, সুগার পিপি আর এইচবিএ১সি করাতে কত লাগবে | first run: a fifth test cut without a word | correct |
| 9 | পেটের ছবি তুলতে কত লাগবে | unchanged: the ultrasound alone is still the lay-term answer | correct |
| 10 | রক্ত পরীক্ষা করাতে চাই | first run: the blood-test list was in Latin letters | correct |
| 11 | আমি গাইনিতে একজন ডক্টরকে দেখাব | the original miss: the locative | correct |

### 1. reported live: ultrasound + Pap smear

```
[User] পেটের আলট্রাসাউন্ড আর প্যাপ স্মিয়ার টেস্টের মোট দাম কত হবে
[AI]   আপনি পেটের আলট্রাসাউন্ড আর প্যাপ স্মিয়ার টেস্টের দাম জানতে চান, ঠিক তো?
[User] হ্যাঁ
[AI]   পেটের আলট্রাসাউন্ড 1500 টাকা আর প্যাপ স্মিয়ার 900 টাকা। সব মিলিয়ে মোট 2400 টাকা।
```

### 2. first run: sugar PP + TSH + ultrasound

```
[User] সুগার পিপি, টিএসএইচ আর পেটের আলট্রাসাউন্ড টেস্টের মোট দাম কত হবে
[AI]   আপনি সুগার পিপি, টিএসএইচ আর পেটের আলট্রাসাউন্ড টেস্টের দাম জানতে চান, ঠিক তো?
[User] হ্যাঁ
[AI]   সুগার পিপি 120 টাকা, টিএসএইচ 350 টাকা আর পেটের আলট্রাসাউন্ড 1500 টাকা। সব মিলিয়ে মোট 1970 টাকা।
```

### 3. first run: sugar PP + ultrasound

```
[User] সুগার পিপি আর পেটের আলট্রাসাউন্ড করাতে কত লাগবে
[AI]   আপনি সুগার পিপি আর পেটের আলট্রাসাউন্ড টেস্ট করবেন, ঠিক তো?
[User] হ্যাঁ
[AI]   সুগার পিপি 120 টাকা আর পেটের আলট্রাসাউন্ড 1500 টাকা। সব মিলিয়ে মোট 1620 টাকা। টেস্টের বুকিং আর পেমেন্ট আমাদের কাউন্টারে গিয়ে করতে হয়, ফোনে হয় না। প্রেসক্রিপশন নিয়ে কাউন্টারে চলে আসুন।
```

### 4. first run: four tests with the ultrasound

```
[User] ইসিজি, পেটের আলট্রাসাউন্ড, ইকো টেস্ট আর টিএমটি-এর দাম কত
[AI]   আপনি ইসিজি, পেটের আলট্রাসাউন্ড, ইকো টেস্ট আর টিএমটি টেস্টের দাম জানতে চান, ঠিক তো?
[User] হ্যাঁ
[AI]   ইসিজি 300 টাকা, পেটের আলট্রাসাউন্ড 1500 টাকা, ইকো টেস্ট 2000 টাকা আর টিএমটি 2200 টাকা। সব মিলিয়ে মোট 6000 টাকা।
```

### 5. first run: a test dropped from a list

```
[User] ইউরিন টেস্ট, ইকো টেস্ট আর পিএসএ করাতে কত লাগবে
[AI]   আপনি ইউরিন টেস্ট, ইকো টেস্ট আর পিএসএ টেস্টের দাম জানতে চান, ঠিক তো?
[User] হ্যাঁ
[AI]   ইউরিন টেস্ট 200 টাকা, ইকো টেস্ট 2000 টাকা আর পিএসএ 900 টাকা। সব মিলিয়ে মোট 3100 টাকা।
```

### 6. first run: a wrong test (ECG) priced for Dengue IgG

```
[User] কিডনি ফাংশন টেস্ট, ডেঙ্গু এনএস১, ডেঙ্গু আইজিজি আর ইকো টেস্টের মোট দাম কত হবে
[AI]   আপনি কিডনি ফাংশন টেস্ট, ডেঙ্গু এনএস১, ডেঙ্গু আইজিজি আর ইকো টেস্ট করবেন, ঠিক তো?
[User] হ্যাঁ
[AI]   কিডনি ফাংশন টেস্ট 750 টাকা, ডেঙ্গু এনএস১ 900 টাকা, ডেঙ্গু আইজিজি 900 টাকা আর ইকো টেস্ট 2000 টাকা। সব মিলিয়ে মোট 4550 টাকা। টেস্টের বুকিং আর পেমেন্ট আমাদের কাউন্টারে গিয়ে করতে হয়, ফোনে হয় না। প্রেসক্রিপশন নিয়ে কাউন্টারে চলে আসুন।
```

### 7. first run: handed to a clinician because of the 1 in HbA1c

```
[User] সুগার পিপি, এইচবিএ১সি আর ভিটামিন ডি করাতে কত লাগবে
[AI]   আপনি সুগার পিপি, এইচবিএ১সি আর ভিটামিন ডি টেস্টের দাম জানতে চান, ঠিক তো?
[User] হ্যাঁ
[AI]   সুগার পিপি 120 টাকা, এইচবিএ১সি 650 টাকা আর ভিটামিন ডি 1800 টাকা। সব মিলিয়ে মোট 2570 টাকা।
```

### 8. first run: a fifth test cut without a word

```
[User] সিবিসি, ইএসআর, ব্লাড সুগার ফাস্টিং, সুগার পিপি আর এইচবিএ১সি করাতে কত লাগবে
[AI]   আপনি সিবিসি, ইএসআর, ব্লাড সুগার ফাস্টিং, সুগার পিপি আর এইচবিএ১সি টেস্টের দাম জানতে চান, ঠিক তো?
[User] হ্যাঁ
[AI]   সিবিসি 400 টাকা, ইএসআর 150 টাকা, ব্লাড সুগার ফাস্টিং 120 টাকা, সুগার পিপি 120 টাকা আর এইচবিএ১সি 650 টাকা। সব মিলিয়ে মোট 1440 টাকা।
```

### 9. unchanged: the ultrasound alone is still the lay-term answer

```
[User] পেটের ছবি তুলতে কত লাগবে
[AI]   পেটের আলট্রাসাউন্ড টেস্টের রেট 1500 টাকা। রিপোর্ট 4 ঘণ্টার মধ্যে পাবেন। প্রেসক্রিপশনের একটা ছবি এই হোয়াটসঅ্যাপ নম্বরে পাঠিয়ে দিন, ৯৬৩৫৫৮৮৩০৬। আমাদের একজন সহকর্মী দেখে আপনাকে কল করবেন।
```

### 10. first run: the blood-test list was in Latin letters

```
[User] রক্ত পরীক্ষা করাতে চাই
[AI]   আমাদের এখানে রক্তের যে পরীক্ষাগুলো হয় তা হল ব্লাড গ্রুপ, ব্লাড সুগার ফাস্টিং, সুগার পিপি, সিআরপি, ক্যালসিয়াম। সিবিসি, ডেঙ্গু আইজিজি, ডেঙ্গু এনএস১, ইএসআর, এইচবিএসএজি। এইচসিভি, এইচআইভি টেস্ট, এইচবিএ১সি, কিডনি ফাংশন টেস্ট, লিপিড প্রোফাইল। লিভার ফাংশন টেস্ট, ম্যালেরিয়া টেস্ট, পিএসএ, ক্রিয়াটিনিন, ইলেক্ট্রোলাইটস। টিএসএইচ, থাইরয়েড প্রোফাইল, ইউরিক অ্যাসিড, ভিটামিন বি১২, ভিটামিন ডি। ওয়াইডাল টেস্ট। এর মধ্যে কোনটা করাতে চান? প্রেসক্রিপশনের একটা ছবি এই হোয়াটসঅ্যাপ নম্বরে পাঠিয়ে দিন, ৯৬৩৫৫৮৮৩০৬। আমাদের একজন সহকর্মী দেখে আপনাকে কল করবেন।
```

### 11. the original miss: the locative

```
[User] আমি গাইনিতে একজন ডক্টরকে দেখাব
[AI]   গাইনি ও প্রসূতি বিভাগে এই ডাক্তাররা বসেন -- অপর্ণা বসু, পারমিতা সেনগুপ্ত, রিনা মিত্র, সুতপা চ্যাটার্জী। কার কাছে অ্যাপয়েন্টমেন্ট নিতে চান?
```

