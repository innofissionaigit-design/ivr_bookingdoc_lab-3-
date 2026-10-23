"""The Bengali booking and test-price flows, as whole calls (tests/_bn_flow_matrix.py, tests/_bn_flow_scenarios.py).

By default a spread of 12 calls per scenario (about 90 seconds) so this belongs in the ordinary suite. The whole
matrix -- every doctor, department, test pair -- is `python tests/bn_flow_report.py`, which also writes the report;
set BN_FLOW_FULL=1 to run all of it here instead.

    python -m pytest tests/test_bengali_flow_matrix.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("PRONUNCIATION_ALLOW_DRAFT", "0")

import bn_flow_report as report  # noqa: E402

FULL = os.environ.get("BN_FLOW_FULL") == "1"


@pytest.fixture(scope="module")
def results():
    out, _doctors, _tests = report.run({1, 2, 3}, quick=not FULL, progress=False, limit_each=None if FULL else 12)
    return out


@pytest.mark.parametrize("scenario", ["1 ", "2 ", "3 "])
def test_every_call_of_the_scenario_lands(results, scenario):
    mine = [r for r in results if r.scenario.startswith(scenario)]
    assert mine, "the scenario ran no calls"
    failed = [(r.combo, r.failures[:2]) for r in mine if not r.passed]
    assert failed == [], f"{len(failed)} of {len(mine)} calls failed; first: {failed[:2]}"
