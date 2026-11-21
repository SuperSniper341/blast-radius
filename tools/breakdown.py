"""Per-commit cost breakdown for a run: our selection vs the labelled truth."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from selector.pack import WORKER_COST_MS, pack  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    data = Path(os.environ.get("DATA_DIR", REPO / "sample"))
    answers_path = Path(sys.argv[1]) if len(sys.argv) > 1 else data / "answers.json"
    out = Path(os.environ.get("OUT", REPO / "output.jsonl"))
    tests = json.loads((data / "tests.json").read_text())["tests"]
    durations = {t["number"]: round(t["seconds"] * 1000) for t in tests}
    answers = json.loads(answers_path.read_text())["commits"]
    lines = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]

    total = 0
    rows = []
    for entry in lines:
        commit = entry["commit"]
        workers = [w for w in entry["workers"] if w]
        numbers = [n for w in workers for n in w]
        cost = 0
        if workers:
            cost = max(sum(durations[n] for n in w) for w in workers) + WORKER_COST_MS * len(workers)
        failing = answers.get(commit, {}).get("failing", [])
        missed = [n for n in failing if n not in numbers]
        truth = pack({n: durations[n] for n in failing}) if failing else []
        truth_cost = 0
        if truth:
            truth_cost = max(sum(durations[n] for n in w) for w in truth) + WORKER_COST_MS * len(truth)
        total += cost + (300_000 if missed else 0)
        rows.append((cost - truth_cost, commit, len(numbers), len(workers), cost / 1000, truth_cost / 1000, len(missed)))

    rows.sort(reverse=True)
    print(f"{'waste':>8} {'commit':8} {'sel':>5} {'wk':>2} {'cost':>8} {'truth':>8} {'miss':>4}")
    for waste, commit, sel, wk, cost, truth_cost, missed in rows:
        print(f"{waste/1000:8.2f} {commit:8} {sel:5d} {wk:2d} {cost:8.2f} {truth_cost:8.2f} {missed:4d}")
    print(f"\ntotal {total/1000:.1f} s over {len(rows)} commits")


if __name__ == "__main__":
    main()
