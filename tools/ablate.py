"""Compare candidate-rule variants on a labelled set (sample or mutants)."""

from __future__ import annotations

import json
import os
import sys
import tokenize
from io import StringIO
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from selector import classify, diff  # noqa: E402
from selector.candidates import Maps  # noqa: E402
from selector.pack import WORKER_COST_MS, pack  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SHORT_CIRCUIT = {"and", "or", "not"}


def short_circuit_changed(old_text: str | None, new_text: str | None) -> bool:
    if not old_text or not new_text:
        return True

    def counts(source: str) -> dict[str, int]:
        out: dict[str, int] = {}
        try:
            for tok in tokenize.generate_tokens(StringIO(source).readline):
                if tok.string in SHORT_CIRCUIT:
                    out[tok.string] = out.get(tok.string, 0) + 1
        except (tokenize.TokenError, IndentationError, SyntaxError):
            return {}
        return out

    return counts(old_text) != counts(new_text)


def change_mask(change, maps, opts) -> int:
    kind, path = change.kind, change.path
    if kind == "ignored":
        return 0
    if kind == "py_src":
        if change.deleted_file:
            return maps.full
        if not change.token_changed:
            return maps.opens.get(path, 0) if not opts["comment_wide"] else (
                maps.pkg_users.get(path, 0) | maps.opens.get(path, 0)
            )
        if change.module_level:
            mask = maps.pkg_users.get(path, 0) | maps.opens.get(path, 0)
            if opts.get("module") == "class" and getattr(change, "class_names", None):
                mask = maps.opens.get(path, 0)
                for name in change.class_names:
                    mask |= maps.names.get(name, 0)
            if opts["names"]:
                for name in change.names:
                    mask |= maps.names.get(name, 0)
            if opts["fixtures"]:
                stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
                mask |= maps.fixtures.get(stem, 0)
            if opts["class_only"]:
                mask = maps.pkg_users.get(path, 0) | maps.opens.get(path, 0)
                for name in change.names & change.class_names:
                    mask |= maps.names.get(name, 0)
                if opts["fixtures"]:
                    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
                    mask |= maps.fixtures.get(stem, 0)
            return mask or maps.full
        lines = set(change.changed_lines)
        if opts["span"] == "always" and change.span:
            lines |= set(range(change.span[0], change.span[1] + 1))
        elif opts["span"] == "shortcircuit" and change.span and change.short_circuit:
            lines |= set(range(change.span[0], change.span[1] + 1))
        mask = maps.opens.get(path, 0) | maps.line_bits(path, lines)
        if not mask:
            mask = maps.pkg_users.get(path, 0)
        return mask or maps.full
    if kind == "py_test":
        if change.module_level or change.deleted_file:
            return maps.file_bits.get(path, maps.full)
        return maps.line_bits(path, change.changed_lines) | maps.file_bits.get(path, 0)
    if kind == "conftest":
        return maps.full
    if kind == "fixture":
        return maps.opens.get(path, 0) or maps.full
    if kind == "packaging":
        return maps.opens.get(path, 0) | maps.file_bits.get("tests/test_sqlglotc.py", 0)
    mask = maps.opens.get(path, 0)
    if mask:
        return mask
    return 0 if path.startswith("sqlglot/") else maps.full


def load_changes(data: Path, checkout: Path) -> dict[str, list]:
    out = {}
    for patch in sorted((data / "commits").glob("*.patch")):
        changes = []
        text = patch.read_text(encoding="utf-8", errors="surrogateescape")
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
            change = classify.classify(file_patch, old, new)
            change.short_circuit = short_circuit_changed(old, new)
            change.class_names = classify._enclosing_class(old, change.changed_lines) if old else set()
            changes.append(change)
        out[patch.stem] = changes
    return out


def main() -> None:
    data = Path(os.environ.get("DATA_DIR", REPO / "kit/sample"))
    checkout = Path(os.path.expanduser(os.environ.get("SQLGLOT_DIR", "~/blast/sqlglot")))
    answers_path = Path(sys.argv[1]) if len(sys.argv) > 1 else data / "answers.json"
    maps = Maps.load(str(REPO / "maps/maps.pkl.gz"))
    tests = json.loads((data / "tests.json").read_text())["tests"]
    durations = {t["number"]: round(t["seconds"] * 1000) for t in tests}
    number_of = {t["id"]: t["number"] for t in tests}
    answers = json.loads(answers_path.read_text())["commits"]
    changes = load_changes(data, checkout)

    results = []
    grid = list(
        product(
            ["always"],
            [True, False],
            [True],
            [False, True],
            ["users", "class"],
        )
    )
    for span, names, fixtures, comment_wide, module in grid:
        opts = {
            "span": span,
            "names": names,
            "fixtures": fixtures,
            "class_only": False,
            "comment_wide": comment_wide,
            "module": module,
        }
        total = 0
        misses = 0
        for commit, change_list in changes.items():
            mask = 0
            for change in change_list:
                mask |= change_mask(change, maps, opts)
            numbers = {number_of[i] for i in maps.ids(mask & maps.full) if i in number_of}
            failing = answers[commit]["failing"]
            missed = [n for n in failing if n not in numbers]
            cost = 0
            if numbers:
                workers = pack({n: durations[n] for n in numbers})
                cost = max(sum(durations[n] for n in w) for w in workers) + WORKER_COST_MS * len(workers)
            total += cost + (300_000 if missed else 0)
            misses += 1 if missed else 0
        results.append((total / 1000, misses, opts))

    results.sort()
    for total, misses, opts in results:
        print(f"{total:8.1f}  misses={misses:5d}  {opts}")


if __name__ == "__main__":
    main()
