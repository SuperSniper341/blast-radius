import os
from pathlib import Path

import pytest

from selector import diff

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "sample" / "commits"
CHECKOUT = Path(os.path.expanduser(os.environ.get("SQLGLOT_DIR", "~/sqlglot")))


def test_new_file():
    text = (
        "diff --git a/sqlglot/new_mod.py b/sqlglot/new_mod.py\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/sqlglot/new_mod.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+X = 1\n"
        "+Y = 2\n"
    )
    files = diff.parse_patch(text)
    assert len(files) == 1
    assert files[0].is_new
    assert diff.apply_patch(None, files[0]) == "X = 1\nY = 2\n"


def test_deleted_file():
    text = (
        "diff --git a/sqlglot/gone.py b/sqlglot/gone.py\n"
        "deleted file mode 100644\n"
        "--- a/sqlglot/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-X = 1\n"
        "-Y = 2\n"
    )
    files = diff.parse_patch(text)
    assert files[0].is_deleted
    assert diff.apply_patch("X = 1\nY = 2\n", files[0]) is None


def test_context_mismatch_raises():
    text = (
        "diff --git a/f.py b/f.py\n"
        "--- a/f.py\n"
        "+++ b/f.py\n"
        "@@ -1,2 +1,2 @@\n"
        " nope\n"
        "-old\n"
        "+new\n"
    )
    files = diff.parse_patch(text)
    with pytest.raises(diff.PatchError):
        diff.apply_patch("yes\nold\n", files[0])


def test_replacement_keeps_surroundings():
    text = (
        "diff --git a/f.py b/f.py\n"
        "--- a/f.py\n"
        "+++ b/f.py\n"
        "@@ -1,3 +1,3 @@\n"
        " a\n"
        "-b\n"
        "+B\n"
        " c\n"
    )
    files = diff.parse_patch(text)
    assert diff.apply_patch("a\nb\nc\n", files[0]) == "a\nB\nc\n"


@pytest.mark.skipif(not CHECKOUT.exists(), reason="needs the pinned sqlglot checkout")
def test_sample_patches_apply_cleanly():
    patches = sorted(SAMPLE.glob("*.patch"))
    assert len(patches) == 30
    for patch in patches:
        for file_patch in diff.parse_patch(patch.read_text(encoding="utf-8")):
            path = file_patch.path
            assert path
            if file_patch.is_new:
                assert file_patch.hunks
                continue
            old = (CHECKOUT / path).read_text(encoding="utf-8")
            new = diff.apply_patch(old, file_patch)
            if file_patch.is_deleted:
                assert new is None
            else:
                assert new is not None
                assert new != old


def test_changed_old_lines_counts_insertion_neighbours():
    text = (
        "diff --git a/f.py b/f.py\n"
        "--- a/f.py\n"
        "+++ b/f.py\n"
        "@@ -2,2 +2,3 @@\n"
        " b\n"
        "+inserted\n"
        " c\n"
    )
    files = diff.parse_patch(text)
    assert diff.changed_old_lines(files[0]) == {2, 3}
