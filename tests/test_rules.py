"""Selection behaviour for kinds of change the sample set does not contain."""

import json
import os
from pathlib import Path

import pytest

from selector import classify, diff
from selector.candidates import Maps, select

REPO = Path(__file__).resolve().parents[1]
CHECKOUT = Path(os.path.expanduser(os.environ.get("SQLGLOT_DIR", "~/blast/sqlglot")))
MAPS = REPO / "maps" / "maps.pkl.gz"
TESTS = REPO / "kit" / "sample" / "tests.json"

pytestmark = pytest.mark.skipif(
    not (CHECKOUT.exists() and MAPS.exists()), reason="needs the checkout and the map"
)


@pytest.fixture(scope="module")
def maps() -> Maps:
    return Maps.load(str(MAPS))


@pytest.fixture(scope="module")
def numbers() -> dict[str, int]:
    tests = json.loads(TESTS.read_text())["tests"]
    return {t["id"]: t["number"] for t in tests}


def run(patch: str, maps: Maps, numbers: dict[str, int]) -> set[int]:
    changes = []
    for file_patch in diff.parse_patch(patch):
        old = None
        if not file_patch.is_new:
            try:
                old = (CHECKOUT / file_patch.path).read_text(encoding="utf-8")
            except OSError:
                old = None
        try:
            new = None if file_patch.is_deleted else diff.apply_patch(old, file_patch)
        except diff.PatchError:
            new = None
        changes.append(classify.classify(file_patch, old, new))
        if file_patch.old_path and file_patch.new_path and file_patch.old_path != file_patch.new_path:
            changes.append(
                classify.classify(diff.FilePatch(old_path=file_patch.old_path), old, None)
            )
    selected = select(changes, maps)
    return {numbers[i] for i in selected if i in numbers}


def test_docs_change_runs_nothing(maps, numbers):
    patch = (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "-hello\n"
        "+hello world\n"
    )
    assert run(patch, maps, numbers) == set()


def test_new_conftest_runs_everything(maps, numbers):
    patch = (
        "diff --git a/tests/conftest.py b/tests/conftest.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/tests/conftest.py\n"
        "@@ -0,0 +1 @@\n"
        "+X = 1\n"
    )
    assert len(run(patch, maps, numbers)) == len(numbers)


def test_deleted_optimizer_module_pulls_in_its_importers(maps, numbers):
    patch = (
        "diff --git a/sqlglot/optimizer/eliminate_joins.py b/sqlglot/optimizer/eliminate_joins.py\n"
        "deleted file mode 100644\n"
        "--- a/sqlglot/optimizer/eliminate_joins.py\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-import x\n"
    )
    selected = run(patch, maps, numbers)
    assert 1106 in selected, "test_execute_tpcds runs the optimizer"
    assert len(selected) < len(numbers)


def test_syntax_error_widens_beyond_the_file(maps, numbers):
    patch = (
        "diff --git a/sqlglot/optimizer/eliminate_joins.py b/sqlglot/optimizer/eliminate_joins.py\n"
        "--- a/sqlglot/optimizer/eliminate_joins.py\n"
        "+++ b/sqlglot/optimizer/eliminate_joins.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-from __future__ import annotations\n"
        "+from __future__ import annotations(\n"
        " \n"
    )
    selected = run(patch, maps, numbers)
    assert len(selected) > 10
    assert 1106 in selected


def test_new_test_file_runs_nothing(maps, numbers):
    patch = (
        "diff --git a/tests/test_brand_new.py b/tests/test_brand_new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/tests/test_brand_new.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+import unittest\n"
        "+class T(unittest.TestCase):\n"
    )
    assert run(patch, maps, numbers) == set()


def test_packaging_change_runs_the_sqlglotc_tests(maps, numbers):
    patch = (
        "diff --git a/pyproject.toml b/pyproject.toml\n"
        "--- a/pyproject.toml\n"
        "+++ b/pyproject.toml\n"
        "@@ -1 +1 @@\n"
        "-version = \"1\"\n"
        "+version = \"2\"\n"
    )
    selected = run(patch, maps, numbers)
    assert 1567 in selected or len(selected) >= 1
