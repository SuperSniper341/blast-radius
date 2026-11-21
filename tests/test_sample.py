"""End-to-end: the shipped selector on the sample set must miss nothing and stay cheap."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECKOUT = Path(os.path.expanduser(os.environ.get("SQLGLOT_DIR", "~/blast/sqlglot")))
MAPS = REPO / "maps" / "maps.pkl.gz"
SAMPLE = REPO / "kit" / "sample"
BUDGET_MILLISECONDS = 200_000

pytestmark = pytest.mark.skipif(
    not (CHECKOUT.exists() and MAPS.exists()), reason="needs the checkout and the map"
)


def load_scorer():
    spec = importlib.util.spec_from_file_location("kit_score", REPO / "kit" / "score.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_selector() -> bytes:
    env = dict(os.environ)
    env.update({"DATA_DIR": str(SAMPLE), "SQLGLOT_DIR": str(CHECKOUT), "MAPS": str(MAPS)})
    return subprocess.run(
        [sys.executable, "-m", "selector.main"],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout


def test_sample_is_covered_within_budget():
    stdout = run_selector()
    tests = json.loads((SAMPLE / "tests.json").read_text())["tests"]
    answers = json.loads((SAMPLE / "answers.json").read_text())["commits"]
    result = load_scorer().evaluate_run(stdout, tests, answers)
    assert result["ok"], result
    assert result["missed_commits"] == []
    assert result["simulated_milliseconds"] <= BUDGET_MILLISECONDS
