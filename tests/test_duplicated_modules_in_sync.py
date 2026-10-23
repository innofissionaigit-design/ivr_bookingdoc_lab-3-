"""The three modules deliberately duplicated across the clinic-api boundary
must not drift apart.

`clinic-api/` simulates a separate Spring Boot service this repo does not
own, so it never imports from `agent/` -- the two halves only ever talk over
HTTP (clinic-api/main.py's module docstring; the mirror of the one-import
rule in CLAUDE.md section 1). The consequence is three copied modules:

    agent/phonetic_match.py   <-> clinic-api/phonetic_match.py
    agent/gazetteer.py        <-> clinic-api/gazetteer.py
    agent/phrase_proximity.py <-> clinic-api/phrase_proximity.py

Each copy's docstring says "keep the two copies in sync if the folding
rules change on one side", and until this file nothing checked that. The
instruction was the only guard, which is the kind of guard that holds right
up until the day someone fixes a matching bug on one side.

WHY DRIFT HERE IS A TRUTH-BOUNDARY PROBLEM, NOT A TIDINESS ONE
--------------------------------------------------------------
These modules decide whether a caller's imperfectly-said name matches a
real doctor or a real test. `agent/` uses them to decide whether to offer a
near match; clinic-api uses them to resolve the name it will return facts
for. If the two folds disagree, the agent and the data layer disagree about
who the caller named -- which is the "Doctor Nobody" failure mode CLAUDE.md
section 1 exists to prevent, arriving through the back door rather than
through the model.

Comments and docstrings ARE allowed to differ, and do: each copy explains
itself from its own side of the boundary. So this compares the parsed code
with docstrings stripped, not the bytes -- otherwise the test would fail on
a comment improvement and get deleted for crying wolf.
"""

from __future__ import annotations

import ast
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DUPLICATED = (
    "phonetic_match.py",
    "gazetteer.py",
    "phrase_proximity.py",
)


def _code_only(path: str) -> str:
    """The module's structure with every docstring removed.

    Comments never reach the AST, so they need no handling; docstrings do,
    and they are exactly what legitimately differs between the copies.
    """
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), path)
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.dump(ast.fix_missing_locations(tree))


@pytest.mark.parametrize("filename", DUPLICATED)
def test_both_copies_exist(filename: str) -> None:
    """A copy being deleted is its own kind of drift: the import-free
    boundary means the other side would keep working and nobody would find
    out from a traceback."""
    for half in ("agent", "clinic-api"):
        path = os.path.join(REPO_ROOT, half, filename)
        assert os.path.exists(path), f"{half}/{filename} is missing"


@pytest.mark.parametrize("filename", DUPLICATED)
def test_copies_are_code_identical(filename: str) -> None:
    agent_copy = os.path.join(REPO_ROOT, "agent", filename)
    api_copy = os.path.join(REPO_ROOT, "clinic-api", filename)
    assert _code_only(agent_copy) == _code_only(api_copy), (
        f"agent/{filename} and clinic-api/{filename} have drifted apart.\n"
        "These are deliberate copies (clinic-api never imports agent/), so a "
        "change to one must be made to the other. Docstrings and comments may "
        "differ; code may not. Diff them and port the change across."
    )
