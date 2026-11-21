"""Emit one JSON line per commit: the tests worth running, split over workers."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

from . import classify, diff, pack
from .candidates import Maps, select

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CHECKOUT = Path(os.environ.get("SQLGLOT_DIR", "/app/sqlglot"))
MAPS_PATH = os.environ.get("MAPS", "/app/maps/maps.pkl.gz")


def log(message: str) -> None:
    print(message, file=sys.stderr)


def load_old_text(root: Path, relative: str) -> str | None:
    target = root / relative
    try:
        return target.read_text(encoding="utf-8", errors="surrogateescape")
    except (OSError, ValueError):
        return None


def changes_for(text: str, root: Path) -> list[classify.Change]:
    changes: list[classify.Change] = []
    for file_patch in diff.parse_patch(text):
        path = file_patch.path
        if not path:
            continue
        old_text = None if file_patch.is_new else load_old_text(root, path)
        new_text = None
        if not file_patch.is_deleted:
            try:
                new_text = diff.apply_patch(old_text, file_patch)
            except diff.PatchError:
                new_text = None
        changes.append(classify.classify(file_patch, old_text, new_text))
        if file_patch.old_path and file_patch.new_path and file_patch.old_path != file_patch.new_path:
            # A rename is also the disappearance of the old module.
            gone = diff.FilePatch(old_path=file_patch.old_path, new_path=None)
            changes.append(classify.classify(gone, old_text, None))
    return changes


def main() -> None:
    tests = json.loads((DATA_DIR / "tests.json").read_text(encoding="utf-8"))["tests"]
    durations = {test["number"]: round(test["seconds"] * 1000) for test in tests}
    id_of_number = {test["number"]: test["id"] for test in tests}
    numbers_by_id = {test["id"]: test["number"] for test in tests}

    maps = Maps.load(MAPS_PATH)
    unmapped = [num for num, test_id in id_of_number.items() if test_id not in maps.index]
    if unmapped:
        log(f"warning: {len(unmapped)} tests missing from maps, always selected")

    everything = pack.pack({num: durations[num] for num in durations})
    all_numbers = set(durations)
    commit_dir = DATA_DIR / "commits"
    patches = sorted(commit_dir.glob("*.patch"))
    log(f"{len(patches)} commits, {len(tests)} tests, maps {len(maps.tests)} ids")

    for patch_path in patches:
        commit = patch_path.stem
        try:
            text = patch_path.read_text(encoding="utf-8", errors="surrogateescape")
            changes = changes_for(text, CHECKOUT)
            selected = select(changes, maps)
            numbers = {numbers_by_id[tid] for tid in selected if tid in numbers_by_id}
            numbers.update(unmapped)
            if numbers == all_numbers:
                workers = everything
            else:
                workers = pack.pack({num: durations[num] for num in numbers})
        except Exception:  # noqa: BLE001 - never lose a commit
            log(f"{commit}: fallback to every test\n{traceback.format_exc()}")
            workers = everything
        print(json.dumps({"commit": commit, "workers": workers}), flush=True)

if __name__ == "__main__":
    main()
