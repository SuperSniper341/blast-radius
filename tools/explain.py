"""Explain, for each sample commit, which failing tests were not selected."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from selector import classify, diff  # noqa: E402
from selector.candidates import Maps, change_bits, select  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    data = Path(os.environ.get("DATA_DIR", REPO / "kit/sample"))
    checkout = Path(os.path.expanduser(os.environ.get("SQLGLOT_DIR", "~/blast/sqlglot")))
    maps = Maps.load(str(REPO / "maps/maps.pkl.gz"))
    tests = json.loads((data / "tests.json").read_text())["tests"]
    answers = json.loads((data / "answers.json").read_text())["commits"]
    by_number = {t["number"]: t for t in tests}

    for patch in sorted((data / "commits").glob("*.patch")):
        commit = patch.stem
        text = patch.read_text(encoding="utf-8", errors="surrogateescape")
        changes = []
        for fp in diff.parse_patch(text):
            if not fp.path:
                continue
            old = None
            if not fp.is_new:
                try:
                    old = (checkout / fp.path).read_text(encoding="utf-8", errors="surrogateescape")
                except OSError:
                    old = None
            try:
                new = None if fp.is_deleted else diff.apply_patch(old, fp)
            except diff.PatchError:
                new = None
            changes.append(classify.classify(fp, old, new))
        selected = select(changes, maps)
        failing = answers[commit]["failing"]
        missed = [n for n in failing if by_number[n]["id"] not in selected]
        if not missed:
            continue
        print(f"=== {commit} ({answers[commit]['kind']}) missed {len(missed)}/{len(failing)}")
        for change in changes:
            bits = change_bits(change, maps)
            n = bin(bits).count("1")
            print(
                f"  change {change.path} kind={change.kind} tok={change.token_changed} "
                f"mod={change.module_level} lines={sorted(change.changed_lines)[:6]} cands={n}"
            )
        for number in missed:
            test = by_number[number]
            path = test["file"]
            table = maps.cov.get(path, {})
            print(f"  - {number} {test['id']} ({test['seconds']}s) lines_in_file={len(table)}")


if __name__ == "__main__":
    main()
