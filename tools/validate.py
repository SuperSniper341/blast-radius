"""Score the selector against a labelled corpus and group the outcome by kind."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from selector import classify, diff  # noqa: E402
from selector.candidates import Maps, select  # noqa: E402
from selector.pack import WORKER_COST_MS, pack  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
MISS_COST = 300_000


def main() -> None:
    data = Path(sys.argv[1] if len(sys.argv) > 1 else REPO / "validation")
    checkout = Path(os.path.expanduser(os.environ.get("SQLGLOT_DIR", "~/blast/sqlglot")))
    answers_path = Path(sys.argv[2]) if len(sys.argv) > 2 else data / "answers.json"
    maps = Maps.load(str(REPO / "maps/maps.pkl.gz"))
    tests = json.loads((REPO / "kit/sample/tests.json").read_text())["tests"]
    durations = {t["number"]: round(t["seconds"] * 1000) for t in tests}
    number_of = {t["id"]: t["number"] for t in tests}
    answers = json.loads(answers_path.read_text())["commits"]
    kinds: dict[str, list[str]] = {}
    kinds_path = data / "kinds.json"
    if kinds_path.exists():
        for kind, commits in json.loads(kinds_path.read_text()).items():
            for commit in commits:
                kinds[commit] = kind

    by_kind: dict[str, dict[str, int]] = {}
    total = 0
    misses_total = 0
    rows = []
    for patch in sorted((data / "commits").glob("*.patch")):
        commit = patch.stem
        if commit not in answers:
            continue
        text = patch.read_text(encoding="utf-8", errors="surrogateescape")
        changes = []
        for file_patch in diff.parse_patch(text):
            if not file_patch.path:
                continue
            old = None
            if not file_patch.is_new:
                try:
                    old = (checkout / file_patch.path).read_text(
                        encoding="utf-8", errors="surrogateescape"
                    )
                except OSError:
                    old = None
            try:
                new = None if file_patch.is_deleted else diff.apply_patch(old, file_patch)
            except diff.PatchError:
                new = None
            changes.append(classify.classify(file_patch, old, new))
        selected = select(changes, maps)
        numbers = {number_of[i] for i in selected if i in number_of}
        failing = answers[commit]["failing"]
        missed = [n for n in failing if n not in numbers]
        cost = 0
        if numbers:
            workers = pack({n: durations[n] for n in numbers})
            cost = max(sum(durations[n] for n in w) for w in workers) + WORKER_COST_MS * len(workers)
        truth = 0
        if failing:
            tw = pack({n: durations[n] for n in failing})
            truth = max(sum(durations[n] for n in w) for w in tw) + WORKER_COST_MS * len(tw)
        total += cost + (MISS_COST if missed else 0)
        misses_total += 1 if missed else 0
        kind = kinds.get(commit, "?")
        bucket = by_kind.setdefault(kind, {"n": 0, "miss": 0, "cost": 0, "truth": 0})
        bucket["n"] += 1
        bucket["miss"] += 1 if missed else 0
        bucket["cost"] += cost
        bucket["truth"] += truth
        if missed:
            rows.append((commit, kind, [f"{n}:{durations[n]}ms" for n in missed][:4]))

    print(f"{'kind':12} {'n':>3} {'miss':>5} {'cost':>9} {'truth':>9} {'waste':>9}")
    for kind, b in sorted(by_kind.items()):
        print(
            f"{kind:12} {b['n']:3d} {b['miss']:5d} {b['cost'] / 1000:9.1f} "
            f"{b['truth'] / 1000:9.1f} {(b['cost'] - b['truth']) / 1000:9.1f}"
        )
    print(f"\ntotal {total / 1000:.1f} s, misses {misses_total}")
    for commit, kind, missed in rows:
        print(f"  MISS {commit} ({kind}): {missed}")


if __name__ == "__main__":
    main()
