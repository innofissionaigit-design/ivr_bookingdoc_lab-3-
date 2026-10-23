"""Turn scripts/live_story_check.py's JSON into a per-story Markdown evidence section (transcripts, checks, language).

    python scripts/live_story_report.py live_run.json stories.json notes.json > docs/story_evidence.md

`stories.json` maps a story number to its sprint-sheet text (title, user story, acceptance criteria); `notes.json` maps a
scenario id to a hand-written verdict and explanation. Both are optional: without them the section shows the evidence
only. Nothing here judges a story -- the verdict is the reviewer's, the evidence is the live run's.
"""

from __future__ import annotations

import json
import re
import sys
from collections import OrderedDict

LANG_NAME = {"bn": "Bengali", "hi": "Hindi", "en": "English"}


def story_number(story: str) -> str | None:
    m = re.match(r"#(\d+)", story)
    return m.group(1) if m else None


def cell(text: str | None) -> str:
    return (text or "—").replace("|", "\\|").replace("\n", " ")


def main() -> None:
    runs = json.load(open(sys.argv[1], encoding="utf-8"))
    stories = json.load(open(sys.argv[2], encoding="utf-8")) if len(sys.argv) > 2 else {}
    notes = json.load(open(sys.argv[3], encoding="utf-8")) if len(sys.argv) > 3 else {}

    by_id: OrderedDict[str, list[dict]] = OrderedDict()
    for r in runs:
        by_id.setdefault(r["id"], []).append(r)

    out = []
    for sid, results in by_id.items():
        head = results[0]["story"]
        out.append(f"### {sid} — {head}\n")
        num = story_number(head)
        if num and num in stories:
            s = stories[num]
            out.append(f"**User story.** {s['user_story']}\n")
            out.append(f"**Acceptance criteria.** {s['acceptance']}\n")
        note = notes.get(sid)
        if note:
            out.append(f"**Verdict: {note['verdict']}.** {note['why']}\n")
        out.append("| Language | Live result | Reply in caller's language |")
        out.append("|---|---|---|")
        for r in results:
            lang_ok = "yes" if not r.get("wrong_language_replies") else "**NO**"
            res = "PASS" if r["passed"] else ("ERROR: " + r["error"] if r.get("error") else "FAIL")
            out.append(f"| {LANG_NAME.get(r['lang'], r['lang'])} | {res} | {lang_ok} |")
        out.append("")
        for r in results:
            out.append(f"<details><summary>{LANG_NAME.get(r['lang'], r['lang'])} transcript</summary>\n")
            out.append("| # | Caller said (TTS) | Agent heard (ASR) | Agent replied | Reply language |")
            out.append("|---|---|---|---|---|")
            for i, t in enumerate(r.get("turns") or [], 1):
                out.append(
                    f"| {i} | {cell(t['said'])} | {cell(t['heard'])} | {cell(t['reply'])} | {t.get('reply_lang') or '—'} |"
                )
            failed = [c for c in r.get("checks") or [] if not c["ok"]]
            if failed:
                out.append("\nUnmet expectation(s): " + "; ".join(f"`{c['expect']}`" for c in failed))
            out.append("\n</details>\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
