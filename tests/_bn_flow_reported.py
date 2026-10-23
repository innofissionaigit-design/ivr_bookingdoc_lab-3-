"""Sentences a person reported (or the first matrix run caught) that were answered wrongly, replayed on the code as it
is now, with the transcript and a check for each. The report's last section is generated from this, so it always shows
the behaviour of the code that was just tested, not a quotation of the past.

    python tests/_bn_flow_reported.py            # prints the section
"""

from __future__ import annotations

import logging
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SECTION_TITLE = "Reported sentences, replayed on the final code"

# (label, what the caller says, intent the model gave, slots it gave, kind)
#   kind "tests": every named test priced and a total; "lay": the one-test lay-term answer (unchanged);
#   "blood": the blood-test list, speakable; "department": the department's doctors are read.
CASES = [
    ("reported live: ultrasound + Pap smear", "পেটের আলট্রাসাউন্ড আর প্যাপ স্মিয়ার টেস্টের মোট দাম কত হবে", "test_rate",
     {"test_names": ["USG Whole Abdomen", "Pap Smear"]}, "tests"),
    ("first run: sugar PP + TSH + ultrasound", "সুগার পিপি, টিএসএইচ আর পেটের আলট্রাসাউন্ড টেস্টের মোট দাম কত হবে", "test_rate",
     {"test_names": ["Blood Sugar PP", "TSH", "USG Whole Abdomen"]}, "tests"),
    ("first run: sugar PP + ultrasound", "সুগার পিপি আর পেটের আলট্রাসাউন্ড করাতে কত লাগবে", "book_test",
     {"test_names": ["Blood Sugar PP", "USG Whole Abdomen"]}, "tests"),
    ("first run: four tests with the ultrasound", "ইসিজি, পেটের আলট্রাসাউন্ড, ইকো টেস্ট আর টিএমটি-এর দাম কত", "test_rate",
     {"test_names": ["ECG", "USG Whole Abdomen", "2D Echocardiography", "TMT (Treadmill Test)"]}, "tests"),
    ("first run: a test dropped from a list", "ইউরিন টেস্ট, ইকো টেস্ট আর পিএসএ করাতে কত লাগবে", "test_rate",
     {"test_names": ["Urine Routine Examination", "2D Echocardiography", "PSA (Prostate Specific Antigen)"]}, "tests"),
    ("first run: a wrong test (ECG) priced for Dengue IgG",
     "কিডনি ফাংশন টেস্ট, ডেঙ্গু এনএস১, ডেঙ্গু আইজিজি আর ইকো টেস্টের মোট দাম কত হবে", "book_test",
     {"test_names": ["Kidney Function Test (KFT)", "Dengue NS1 Antigen", "Dengue IgG/IgM", "2D Echocardiography"]}, "tests"),
    ("first run: handed to a clinician because of the 1 in HbA1c", "সুগার পিপি, এইচবিএ১সি আর ভিটামিন ডি করাতে কত লাগবে",
     "test_rate", {"test_names": ["Blood Sugar PP", "HbA1c", "Vitamin D (25-OH)"]}, "tests"),
    ("first run: a fifth test cut without a word", "সিবিসি, ইএসআর, ব্লাড সুগার ফাস্টিং, সুগার পিপি আর এইচবিএ১সি করাতে কত লাগবে",
     "test_rate", {"test_names": ["Complete Blood Count (CBC)", "ESR", "Blood Sugar Fasting", "Blood Sugar PP", "HbA1c"]}, "tests"),
    ("unchanged: the ultrasound alone is still the lay-term answer", "পেটের ছবি তুলতে কত লাগবে", "test_rate",
     {"test_name": "USG Whole Abdomen"}, "lay"),
    ("first run: the blood-test list was in Latin letters", "রক্ত পরীক্ষা করাতে চাই", "book_test", {}, "blood"),
    ("the original miss: the locative", "আমি গাইনিতে একজন ডক্টরকে দেখাব", "book_appointment", {}, "department"),
]


def _check(kind, spoken, tests_named, rates, call):
    from agent.reply_templates_i18n import _rupees

    reply = " ".join(spoken)
    problems = []
    if call.blocked:
        problems.append("a line the voice cannot say was spoken")
    if kind == "tests":
        for t in tests_named:
            if _rupees(rates[t]) not in reply:
                problems.append(f"price of {t} missing")
        if len(tests_named) > 1 and _rupees(sum(rates[t] for t in tests_named)) not in reply:
            problems.append("total missing")
        if "প্রেসক্রিপশনের একটা ছবি" in reply:
            problems.append("answered with the lay-term prescription note")
    elif kind == "lay":
        if "প্রেসক্রিপশনের একটা ছবি" in reply or "৯৬৩৫৫৮৮৩০৬" in reply:
            problems.append("the removed WhatsApp prescription sentence is being spoken again")
    elif kind == "blood":
        import re

        if re.search(r"[A-Za-z]{3,}", reply):
            problems.append("Latin letters in a Bengali reply")
    return problems


def replay(w) -> list[dict]:
    import _bn_flow_scenarios as S
    from agent.entity_grounding import ground_tests  # noqa: F401  (import kept so a missing module fails loudly)

    probe = w.call()
    rates = {t["test_name"]: t["rate_inr"] for t in probe._run(probe.m._tools.list_tests())["tests"]}
    gynae = [d["full_name_bn"] for d in probe._run(probe.m._tools.list_doctors())["doctors"]
             if d["department"].startswith("Gynae")]
    w.close(probe)
    out = []
    for label, said, intent, slots, kind in CASES:
        call = w.call()
        S.load_catalogue(call)
        spoken = call.say(said, intent, dict(slots))
        if spoken and spoken[-1].endswith("ঠিক তো?"):
            spoken += call.say("হ্যাঁ", "unclear", {})
        named = list(slots.get("test_names") or ([slots["test_name"]] if slots.get("test_name") else []))
        problems = _check(kind, spoken, named, rates, call)
        if kind == "department":
            joined = " ".join(spoken)
            problems += [f"{n} not listed" for n in gynae if n not in joined]
        out.append({"label": label, "said": said, "spoken": spoken, "ok": not problems, "problems": problems})
        w.close(call)
    return out


def render(results: list[dict]) -> str:
    good = sum(1 for r in results if r["ok"])
    lines = [f"## {SECTION_TITLE}", ""]
    lines.append(
        f"Each sentence below was answered wrongly by an earlier build: reported by a person, or caught by the first "
        f"matrix run. They are replayed here through the same harness on the code that produced this report. "
        f"**{good} of {len(results)} are answered correctly.**"
    )
    lines.append("")
    lines.append("| # | Sentence | Earlier problem | Now |")
    lines.append("|---|---|---|---|")
    for n, r in enumerate(results, 1):
        lines.append(f"| {n} | {r['said']} | {r['label']} | {'correct' if r['ok'] else 'WRONG: ' + '; '.join(r['problems'])} |")
    lines.append("")
    for n, r in enumerate(results, 1):
        lines.append(f"### {n}. {r['label']}")
        lines.append("")
        lines.append("```")
        lines.append(f"[User] {r['said']}")
        for i, line in enumerate(r["spoken"]):
            lines.append(f"[AI]   {line}")
            if line.endswith("ঠিক তো?") and i + 1 < len(r["spoken"]):
                lines.append("[User] হ্যাঁ")
        lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"


def run_and_render() -> tuple[str, list[dict]]:
    os.environ.setdefault("PRONUNCIATION_ALLOW_DRAFT", "0")
    logging.disable(logging.CRITICAL)
    from _shared_conversation import world

    with world("bn") as w:
        results = replay(w)
    return render(results), results


if __name__ == "__main__":
    text, results = run_and_render()
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    sys.exit(0 if all(r["ok"] for r in results) else 1)
