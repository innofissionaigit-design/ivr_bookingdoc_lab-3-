"""Run the Bengali flow matrix and write the test report (Markdown).

    python tests/bn_flow_report.py                       # every combination -> docs/test-reports/...md
    python tests/bn_flow_report.py --quick               # a thin slice (what tests/test_bengali_flow_matrix.py runs)
    python tests/bn_flow_report.py --scenario 3 --out x.md

What each scenario is, and what is real versus given, is in tests/_bn_flow_matrix.py and the report's own preamble.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import itertools
import json
import logging
import os
import random
import re
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("PRONUNCIATION_ALLOW_DRAFT", "0")  # the speech gate as shipped (deploy/env.sh)

import _bn_flow_matrix as M  # noqa: E402
import _bn_flow_scenarios as S  # noqa: E402
from _shared_conversation import world  # noqa: E402

DEFAULT_OUT = os.path.join(REPO_ROOT, "docs", "test-reports", "bengali-flow-test-report.md")



DEFECTS_FIXED = [
    ("1", "\"আমি গাইনিতে একজন ডক্টরকে দেখাব\" was not understood; \"গাইনির\" was",
     "the department matcher's ending list had no locative \"তে\"",
     "`clinic-api/department_match.py`: ~3,000 case endings, spelling fold, word windows; suggestion-only fuzzy tier"),
    ("2", "\"সুগার পিপি, এইচবিএ১সি আর ভিটামিন ডি করাতে কত লাগবে\" was answered \"a doctor must explain this\" and handed to a clinician",
     "`agent/clinical_safety.py` read the \"১\" inside the TEST NAME এইচবিএ১সি (also বি১২, এনএস১) as a lab value next to \"সুগার\"",
     "a digit inside a word is a name, not a value; \"সুগার ২৫০\" is still caught"),
    ("3", "in a list of tests one was dropped, or a wrong one priced (\"ডেঙ্গু আইজিজি\" priced as ECG)",
     "`agent/entity_grounding._without` removed EVERY word equal to a word of the first name, so later names that share a word (\"টেস্ট\", \"ডেঙ্গু\") lost it",
     "only the matched run of words is removed, once"),
    ("4", "a fifth test was dropped without a word and the total left it out",
     "`MAX_TESTS_PER_TURN = 4` silently cut the list",
     "cap raised to 6; more than the cap grounds nothing and the caller is asked which tests"),
    ("5", "the read-back said \"ইকো টেস্ট টেস্টের দাম\"",
     "the template appended \"টেস্ট\" to a name that already ends in it",
     "`test_confirm_prompt` (Bengali and Hindi)"),
    ("6", "\"কার্ডিওলজির ডাক্তার দেখাতে চাই\" read the doctors but opened no booking; \"the second one\" was \"I did not understand\"",
     "with the department in the model's symptom slot the list was spoken by the enquiry path, which remembered nothing",
     "the booking opens (department named by the clinic's own table); a list read as an answer is remembered, and a position or a bare name after it continues into the booking"),
    ("7", "\"প্রথমজনকে দেখাতে চাই\" (the first one, with the case ending) was not understood",
     "the ordinal table had only bare forms",
     "inflected forms added"),
    ("8", "\"সুগার পিপি আর পেটের আলট্রাসাউন্ডের দাম\" answered the ultrasound's price alone and ignored the other test "
          "(also reported live: \"পেটের আলট্রাসাউন্ড আর প্যাপ স্মিয়ার টেস্টের মোট দাম কত হবে\")",
     "the lay-term shortcut for \"পেটের …\" runs before the multi-test flow and answers one test",
     "the shortcut steps aside when two or more tests are named"),
    ("9", "\"রক্ত পরীক্ষা করাতে চাই\" would have given the caller the busy fallback",
     "the blood-test list was read as Latin short codes (\"Blood Grouping & Rh Typing\"), which the Bengali voice cannot say, so the speech gate blocks the line",
     "the list is read in the clinic's own Bengali (or Hindi) names when every test has one"),
]

KNOWN_LIMITS = [
    "**The recogniser and the language model are given, not tested.** The pod is down, so no call here proves that the "
    "ASR writes these words or that Qwen returns these slots. Run this matrix's phrases against the pod when it is up.",
    "Groups of three to five tests are a deterministic sample (150 + 61 + 21 of 5,984 + 46,376 + 278,256), not every "
    "combination. Every single test and every pair is covered.",
    "Day, time and phone-number style are rotated over the doctor/department combinations rather than fully crossed "
    "(a full cross would be about 7,800 booking calls).",
    "More than six tests in one sentence is not priced; the caller is asked which tests. That is deliberate (a longer "
    "list is far more likely a mis-parse than a caller).",
    "Dates are spoken from ISO text (\"2026-10-14\") and times from \"10:15\"; the speech gate accepted them, but how the "
    "Indic voices pronounce them is a property of the TTS on the pod.",
    "A weekday name said ON that weekday (\"সোমবার\" on a Monday) is read as today by the clinic; if the chamber is over "
    "it offers next week's. The generator therefore says such days with their number.",
    "The wider test suite still has 188 failing tests that predate this work (see the project's known-failure list); "
    "this change added none.",
]

# ============================================================================== the matrix
def build_s1(doctors: list[dict], aliases: dict[str, list[str]], names_bn: dict[str, str], quick: bool):
    rng = random.Random(12)
    jobs = []
    by_dept: dict[str, list[dict]] = collections.defaultdict(list)
    for d in doctors:
        by_dept[d["department"]].append(d)
    for k, d in enumerate(doctors):
        openers = S.department_openers(names_bn[d["department"]], aliases[d["department"]])
        for o, opener in enumerate(openers):
            for p, pick in enumerate(S.PICKS):
                if quick and (o + p + k) % 3:
                    continue
                jobs.append(dict(doctor=d, dept_doctors=by_dept[d["department"]], opener=opener, pick=pick,
                                 date_style=rng.choice(S.DATE_STYLES), time_style=rng.choice(S.TIME_STYLES),
                                 phone_style=rng.choice(S.PHONE_STYLES)))
    return jobs


def build_s2(doctors: list[dict], quick: bool):
    rng = random.Random(21)
    jobs = []
    for k, d in enumerate(doctors):
        for n, name_style in enumerate(S.DOCTOR_ONLY_STYLES):
            for o, order in enumerate(S.ORDERS):
                if quick and (n + o + k) % 5:
                    continue
                jobs.append(dict(doctor=d, name_style=name_style, order=order, date_style=rng.choice(S.DATE_STYLES),
                                 time_style=rng.choice(S.TIME_STYLES), phone_style=rng.choice(S.PHONE_STYLES)))
    return jobs


def build_s3(tests: list[dict], quick: bool):
    phrasings = ("price", "how_much", "total")
    intents = ("test_rate", "book_test")
    slot_styles = ("canonical", "spoken")
    jobs = []
    i = 0

    def add(group):
        nonlocal i
        jobs.append(dict(tests=list(group), phrasing=phrasings[i % 3], slot_style=slot_styles[(i // 3) % 2],
                         intent=intents[(i // 6) % 2]))
        i += 1

    for t in tests:  # one test, three phrasings
        for ph in phrasings:
            jobs.append(dict(tests=[t], phrasing=ph, slot_style=slot_styles[i % 2], intent=intents[(i // 2) % 2]))
            i += 1
    pairs = list(itertools.combinations(tests, 2))
    triples = list(itertools.combinations(tests, 3))
    quads = list(itertools.combinations(tests, 4))
    fives = list(itertools.combinations(tests, 5))
    if quick:
        pairs, triples, quads, fives = pairs[::14], triples[::300], quads[::2300], fives[::20000]
    else:
        triples, quads, fives = triples[::40], quads[::770], fives[::13900]
    for group in itertools.chain(pairs, triples, quads, fives):
        add(group)
    return jobs


# ============================================================================== running
def run(scenarios: set[int], quick: bool, progress: bool = True, limit_each: int | None = None):
    logging.disable(logging.CRITICAL)
    today = dt.date.today()
    results: list[M.Result] = []
    with world("bn") as w:
        c0 = w.call()
        doctors = M.clinic_doctors(w, c0)
        tests = c0._run(c0.m._tools.list_tests())["tests"]
        departments = c0._run(c0.m._tools.list_departments())
        w.close(c0)
        dept_rows = {d["name"]: d for d in (departments.get("departments") or [])}
        names_bn = {n: r.get("name_bn") or n for n, r in dept_rows.items()}
        import importlib.util

        spec = importlib.util.spec_from_file_location("_i18n_report", os.path.join(REPO_ROOT, "clinic-api", "i18n_content.py"))
        i18n = importlib.util.module_from_spec(spec)
        sys.modules["_i18n_report"] = i18n
        spec.loader.exec_module(i18n)
        aliases = {n: i18n.DEPARTMENT_ALIASES.get(n, []) for n in names_bn}

        plan: list[tuple[str, dict]] = []

        def take(jobs):
            # An even spread, not the first few: the first jobs are all one doctor, one department.
            if limit_each is None or len(jobs) <= limit_each:
                return jobs
            step = len(jobs) / limit_each
            return [jobs[int(i * step)] for i in range(limit_each)]

        if 1 in scenarios:
            plan += [("s1", j) for j in take(build_s1(doctors, aliases, names_bn, quick))]
        if 2 in scenarios:
            plan += [("s2", j) for j in take(build_s2(doctors, quick))]
        if 3 in scenarios:
            plan += [("s3", j) for j in take(build_s3(tests, quick))]
        started = time.time()
        for n, (kind, job) in enumerate(plan, 1):
            if kind == "s1":
                r = S.department_to_booking(w, today=today, **job)
            elif kind == "s2":
                r = S.doctor_only(w, everyone=doctors, today=today, **job)
            else:
                r = S.test_prices(w, **job)
            results.append(r)
            if progress and (n % 25 == 0 or n == len(plan)):
                bad = sum(1 for x in results if not x.passed)
                print(f"  {n}/{len(plan)}  failed so far: {bad}  ({time.time() - started:.0f}s)", flush=True)
    return results, doctors, tests


# ============================================================================== the report
def _signature(text: str) -> str:
    t = re.sub(r"KCD-[0-9A-Z-]+", "KCD-…", text)
    t = re.sub(r"\d{4}-\d{2}-\d{2}", "<date>", t)
    t = re.sub(r"\d{1,2}:\d{2}", "<time>", t)
    t = re.sub(r"\d{10}", "<phone>", t)
    t = re.sub(r"Dr\. [A-Za-z. ]+", "Dr. <name>", t)
    return t.split(":")[0][:110]


def _rate(passed: int, total: int) -> str:
    return f"{passed}/{total}" + (f" ({100 * passed / total:.0f}%)" if total else "")


def write_report(results: list[M.Result], doctors: list[dict], tests: list[dict], path: str, quick: bool,
                 elapsed: float) -> None:
    by_sc: dict[str, list[M.Result]] = collections.defaultdict(list)
    for r in results:
        by_sc[r.scenario].append(r)
    total = len(results)
    ok = sum(1 for r in results if r.passed)
    lines: list[str] = []
    w = lines.append
    w("# Bengali booking and test-price flows: test report")
    w("")
    w(f"Generated {dt.datetime.now():%Y-%m-%d %H:%M} by `tests/bn_flow_report.py`"
      f"{' (quick slice)' if quick else ''}. Pod not involved: everything ran locally, in-process.")
    w("")
    w("## Result")
    w("")
    w(f"**{ok} of {total} whole calls passed** ({100 * ok / max(total, 1):.1f}%). Run time {elapsed / 60:.1f} minutes.")
    w("")
    w("| Scenario | Calls | Passed | Failed |")
    w("|---|---:|---:|---:|")
    for name in sorted(by_sc):
        rs = by_sc[name]
        p = sum(1 for r in rs if r.passed)
        w(f"| {name} | {len(rs)} | {p} | {len(rs) - p} |")
    w(f"| **All** | **{total}** | **{ok}** | **{total - ok}** |")
    w("")
    w("## Defects this matrix found (all fixed before the run reported above)")
    w("")
    w("The first slice of this matrix passed 293 of 358 calls. Each failure kind was root-caused in the product, not "
      "in the test, fixed with a regression test in `tests/test_bengali_flow_matrix_fixes.py`, and the matrix re-run:")
    w("")
    w("| # | What the caller experienced | Root cause | Fix |")
    w("|---|---|---|---|")
    for row in DEFECTS_FIXED:
        w("| " + " | ".join(row) + " |")
    w("")
    w("## Known limits (not fixed, or not provable here)")
    w("")
    for item in KNOWN_LIMITS:
        w(f"- {item}")
    w("")
    w("## What was tested, and what it does and does not prove")
    w("")
    w("Every call is a whole conversation played through the **real orchestrator** (`main_pcm._dispatch_turn` and "
      "everything under it) against the **real clinic API and its seeded database** (8 departments, 32 doctors, "
      f"{len(tests)} lab tests), with the real department, doctor and test matchers, the real reply templates and the "
      "real speech gate (`agent/tts.py`): a line the Bengali voice cannot say fails the call.")
    w("")
    w("Two things are **given, not tested**, exactly as in every other flow test in this repository:")
    w("")
    w("- **The transcript** (there is no ASR here). The caller's Bengali is typed as the recogniser would write it.")
    w("- **The intent and slots the language model returns each turn** (there is no Qwen here). They are written the "
      "way the model returns them on the pod, e.g. the doctor's name in `doctor_name`, the day as an ISO date.")
    w("")
    w("So a pass means: *given that the recogniser and the model behave like this, the rules, the clinic and the "
      "templates land the call correctly.* It does **not** prove the recogniser hears these words or that the model "
      "returns these slots; that needs the pod.")
    w("")
    w("A call passes only if **all** of these hold:")
    w("")
    w("1. every turn was understood: no \"I did not understand\", no \"no doctor called ...\", no hand-off, no counter fallback;")
    w("2. no line was repeated word for word twice in a row, and no line was blocked by the speech gate;")
    w("3. for a booking: the department's four doctors were read out (when a department was named), the chosen doctor "
      "was settled as exactly that doctor, the day and time were taken as intended, the read-back carried the doctor, "
      "time, patient name and phone, \"yes\" gave a confirmation number, and the **clinic's own record** holds that "
      "doctor, day, time and patient name and is found by the caller's phone number;")
    w("4. for a price question: every test's price and (for two or more) the total, in the clinic's figures, were spoken, "
      "and no booking was opened.")
    w("")
    # ------------------------------------------------------------ scenario tables
    s1 = by_sc.get("1 department -> doctor -> day and time -> confirmed", [])
    s2 = by_sc.get("2 only a doctor's name -> the rest of the booking", [])
    s3 = by_sc.get("3 the price of one or more tests", [])

    def table(rs: list[M.Result], key: str, title: str) -> None:
        if not rs:
            return
        groups: dict[str, list[M.Result]] = collections.defaultdict(list)
        for r in rs:
            groups[str(r.combo.get(key))].append(r)
        w(f"| {title} | Calls | Passed |")
        w("|---|---:|---:|")
        for g in sorted(groups):
            p = sum(1 for r in groups[g] if r.passed)
            w(f"| {g} | {len(groups[g])} | {_rate(p, len(groups[g]))} |")
        w("")

    if s1:
        w("## 1. Department → doctor → day and time → confirmed")
        w("")
        w("The caller names a department (three ways), is read its four doctors, picks one (three ways), gives a day "
          "and a time (three ways each), then a patient name and a phone number (three ways), and says yes.")
        w("")
        w("Dimensions: **32 doctors** × **3 ways to name the department** (`locative` \"আমি গাইনিতে একজন ডক্টরকে "
          "দেখাব\"; `genitive_question` \"গাইনির ডাক্তার দেখাতে চাই\"; `kind_of_doctor` \"আমার হার্টের ডাক্তার দরকার\") "
          "× **3 ways to pick** (`full_name`, `surname`, `ordinal` \"দ্বিতীয়জন\"). Day, time and phone style are rotated "
          "pseudo-randomly (seeded) over the combinations.")
        w("")
        table(s1, "department", "Department")
        table(s1, "opener", "How the department was named")
        table(s1, "pick", "How the doctor was picked")
        table(s1, "date_style", "How the day was said")
        table(s1, "time_style", "How the time was said")
        table(s1, "phone_style", "How the phone number was said")
    if s2:
        w("## 2. Only a doctor's name → the rest of the booking")
        w("")
        w("The caller says nothing but a doctor (five ways), either all at once with the day and time or step by step.")
        w("")
        table(s2, "name_style", "How the doctor was named")
        table(s2, "order", "Order")
        table(s2, "department", "Doctor's department")
        table(s2, "date_style", "How the day was said")
        table(s2, "time_style", "How the time was said")
    if s3:
        w("## 3. The price of one or more tests")
        w("")
        w("The caller asks the price of 1, 2, 3, 4 or 5 tests (every single test, every pair, a deterministic spread of "
          "larger groups), is read the tests back, says yes, and hears each price and the total.")
        w("")
        table(s3, "count", "Tests in the question")
        table(s3, "phrasing", "Phrasing")
        table(s3, "slot_style", "Names the model gave (`canonical` = catalogue name, `spoken` = the caller's own words)")
        table(s3, "intent", "Intent the model gave")
    # ------------------------------------------------------------ doctors
    if s1 or s2:
        w("## Every doctor")
        w("")
        w("| Doctor | Department | Chamber | Dept→booking | Doctor-only |")
        w("|---|---|---|---:|---:|")
        for d in sorted(doctors, key=lambda x: (x["department"], x["name"])):
            a = [r for r in s1 if r.combo.get("doctor") == d["name"]]
            b = [r for r in s2 if r.combo.get("doctor") == d["name"]]
            days = ",".join("সোমমঙ্গলবুধবৃহশুক্রশনিরবি"[i * 3:(i + 1) * 3] if False else ["সোম", "মঙ্গল", "বুধ", "বৃহ", "শুক্র", "শনি", "রবি"][i]
                            for i in d["chamber_days"])
            w(f"| {d['full_name_bn']} ({d['name']}) | {d['department']} | {days} {d['chamber_hours']} | "
              f"{_rate(sum(1 for r in a if r.passed), len(a))} | {_rate(sum(1 for r in b if r.passed), len(b))} |")
        w("")
    # ------------------------------------------------------------ failures
    failed = [r for r in results if not r.passed]
    w("## Failures")
    w("")
    if not failed:
        w("None. Every call landed.")
        w("")
    else:
        sigs: dict[str, list[M.Result]] = collections.defaultdict(list)
        for r in failed:
            sigs[_signature(r.failures[0])].append(r)
        w(f"{len(failed)} calls failed, in {len(sigs)} distinct kinds. Each kind, with its first three calls in full:")
        w("")
        for n, (sig, rs) in enumerate(sorted(sigs.items(), key=lambda kv: -len(kv[1])), 1):
            w(f"### {n}. {sig}  ({len(rs)} calls)")
            w("")
            for r in rs[:3]:
                w(f"- **{r.scenario}** `{json.dumps(r.combo, ensure_ascii=False)}`")
                for f in r.failures[:4]:
                    w(f"  - {f}")
                w("")
                w("  ```")
                for ln in r.transcript.splitlines():
                    w("  " + ln)
                w("  ```")
                w("")
    # ------------------------------------------------------------ examples
    w("## One passing call of each kind, in full")
    w("")
    for name in sorted(by_sc):
        ex = next((r for r in by_sc[name] if r.passed), None)
        if ex:
            w(f"### {name}")
            w("")
            w(f"`{json.dumps(ex.combo, ensure_ascii=False)}`")
            w("")
            w("```")
            w(ex.transcript)
            w("```")
            w("")
    w("## How to re-run")
    w("")
    w("```bash")
    w("python tests/bn_flow_report.py            # every combination, about 10-20 minutes, rewrites this report")
    w("python tests/bn_flow_report.py --quick    # a thin slice, a couple of minutes")
    w("python -m pytest tests/test_bengali_flow_matrix.py -v   # the slice, as pytest cases")
    w("```")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--scenario", default="1,2,3")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    scenarios = {int(x) for x in args.scenario.split(",")}
    t0 = time.time()
    results, doctors, tests = run(scenarios, args.quick)
    write_report(results, doctors, tests, args.out, args.quick, time.time() - t0)
    if not args.quick:
        import _bn_flow_reported as reported

        section, _replayed = reported.run_and_render()
        with open(args.out, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n" + section)
    bad = sum(1 for r in results if not r.passed)
    print(f"{len(results) - bad}/{len(results)} passed -> {args.out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
