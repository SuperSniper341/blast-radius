"""Run the selector locally the way the scorer does and report the total.

    python -m tools.eval_local --data kit/sample --answers kit/sample/answers.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load_scorer():
    spec = importlib.util.spec_from_file_location("kit_score", REPO / "kit" / "score.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="kit/sample")
    ap.add_argument("--answers", default="kit/sample/answers.json")
    ap.add_argument("--checkout", default=os.path.expanduser("~/blast/sqlglot"))
    ap.add_argument("--maps", default=str(REPO / "maps" / "maps.pkl.gz"))
    ap.add_argument("--tests-file", default="")
    ap.add_argument("--out", default=str(REPO / "output.jsonl"))
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()

    data = Path(args.data).resolve()
    env = dict(os.environ)
    env.update(
        {
            "DATA_DIR": str(data),
            "SQLGLOT_DIR": os.path.abspath(os.path.expanduser(args.checkout)),
            "MAPS": args.maps,
        }
    )
    for item in args.env:
        key, _, value = item.partition("=")
        env[key] = value

    started = time.time()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    proc = subprocess.run(
        [sys.executable, "-m", "selector.main"],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=None,
    )
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    wall = time.time() - started
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)

    Path(args.out).write_bytes(proc.stdout)
    tests_path = Path(args.tests_file) if args.tests_file else data / "tests.json"
    tests = json.loads(tests_path.read_text())["tests"]
    answers = json.loads(Path(args.answers).read_text())["commits"]
    scorer = load_scorer()
    result = scorer.evaluate_run(proc.stdout, tests, answers)
    if not result["ok"]:
        print(f"REJECTED: {result}", file=sys.stderr)
        return 1

    simulated_ms = result["simulated_milliseconds"]
    total_ms = simulated_ms + round(cpu * 1000)
    if args.compact:
        print(
            f"simulated={simulated_ms / 1000:.1f} "
            f"cpu={cpu:.2f} misses={len(result['missed_commits'])} "
            f"{result['missed_commits']}"
        )
        return 0

    print(
        json.dumps(
            {
                "simulated_s": round(simulated_ms / 1000, 3),
                "cpu_s": round(cpu, 3),
                "total_s": round(total_ms / 1000, 3),
                "misses": len(result["missed_commits"]),
                "missed": result["missed_commits"],
                "wall_s": round(wall, 2),
                "exit": proc.returncode,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
