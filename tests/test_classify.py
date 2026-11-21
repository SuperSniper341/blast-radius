import os
from pathlib import Path

from selector import classify, diff

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "sample" / "commits"
CHECKOUT = Path(os.path.expanduser(os.environ.get("SQLGLOT_DIR", "~/sqlglot")))


def load(commit: str):
    text = (SAMPLE / f"{commit}.patch").read_text(encoding="utf-8")
    out = []
    for file_patch in diff.parse_patch(text):
        old = None
        if not file_patch.is_new and CHECKOUT.exists():
            old = (CHECKOUT / file_patch.path).read_text(encoding="utf-8")
        new = None if file_patch.is_deleted else diff.apply_patch(old, file_patch)
        out.append(classify.classify(file_patch, old, new))
    return out


def test_comment_only_is_not_a_token_change():
    for commit in ("9u5k", "bap8", "f4xa"):
        changes = load(commit)
        assert changes, commit
        for change in changes:
            assert change.kind == "py_src", commit
            assert change.token_changed is False, commit
            assert change.module_level is False, commit


def test_operator_flip_inside_method():
    (change,) = load("2haz")
    assert change.path == "sqlglot/dialects/dialect.py"
    assert change.token_changed is True
    assert change.module_level is False
    assert change.changed_lines == {1280}


def test_class_body_constant_is_module_level():
    (change,) = load("4d63")
    assert change.module_level is True
    assert "NUMERIC_ESCAPES" in change.names


def test_class_attribute_keeps_class_name():
    (change,) = load("535z")
    assert change.module_level is True
    assert "WEEK_OFFSET" in change.names
    assert "TSQL" in change.names


def test_lambda_body_is_not_module_level():
    (change,) = load("6ze8")
    assert change.path == "sqlglot/parsers/dremio.py"
    assert change.token_changed is True
    assert change.module_level is False


def test_fixture_kind():
    (change,) = load("2vdj")
    assert change.kind == "fixture"
    assert change.token_changed is True


def test_literal_inside_string_counts_as_change():
    old = 'X = """\nhello\n"""\n'
    new = 'X = """\nhello there\n"""\n'
    text = (
        "diff --git a/sqlglot/s.py b/sqlglot/s.py\n"
        "--- a/sqlglot/s.py\n"
        "+++ b/sqlglot/s.py\n"
        "@@ -1,3 +1,3 @@\n"
        ' X = """\n'
        "-hello\n"
        "+hello there\n"
        ' """\n'
    )
    (file_patch,) = diff.parse_patch(text)
    change = classify.classify(file_patch, old, new)
    assert change.token_changed is True


def _one(patch_text: str, old: str | None):
    (file_patch,) = diff.parse_patch(patch_text)
    new = None if file_patch.is_deleted else diff.apply_patch(old, file_patch)
    return classify.classify(file_patch, old, new)


LAMBDA_OLD = 'FUNCS = {\n    "A": lambda args: exp.Foo(this=seq_get(args, 0)),\n}\n'


def test_lambda_body_change_is_a_body_change():
    text = (
        "diff --git a/sqlglot/p.py b/sqlglot/p.py\n"
        "--- a/sqlglot/p.py\n"
        "+++ b/sqlglot/p.py\n"
        "@@ -1,3 +1,3 @@\n"
        " FUNCS = {\n"
        "-    \"A\": lambda args: exp.Foo(this=seq_get(args, 0)),\n"
        "+    \"A\": lambda args: exp.Foo(this=seq_get(args, 1)),\n"
        " }\n"
    )
    change = _one(text, LAMBDA_OLD)
    assert change.module_level is False
    assert change.token_changed is True


def test_lambda_header_change_is_module_level():
    text = (
        "diff --git a/sqlglot/p.py b/sqlglot/p.py\n"
        "--- a/sqlglot/p.py\n"
        "+++ b/sqlglot/p.py\n"
        "@@ -1,3 +1,3 @@\n"
        " FUNCS = {\n"
        "-    \"A\": lambda args: exp.Foo(this=seq_get(args, 0)),\n"
        "+    \"B\": lambda args: exp.Foo(this=seq_get(args, 0)),\n"
        " }\n"
    )
    change = _one(text, LAMBDA_OLD)
    assert change.module_level is True


def test_syntax_error_is_module_level():
    text = (
        "diff --git a/sqlglot/p.py b/sqlglot/p.py\n"
        "--- a/sqlglot/p.py\n"
        "+++ b/sqlglot/p.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def f():\n"
        "-    return 1\n"
        "+    return (1\n"
    )
    change = _one(text, "def f():\n    return 1\n")
    assert change.syntax_error is True
    assert change.module_level is True


def test_mode_only_change_is_not_a_token_change():
    text = (
        "diff --git a/sqlglot/p.py b/sqlglot/p.py\n"
        "old mode 100644\n"
        "new mode 100755\n"
    )
    change = _one(text, "x = 1\n")
    assert change.token_changed is False
    assert change.module_level is False


def test_rename_is_module_level():
    text = (
        "diff --git a/sqlglot/old.py b/sqlglot/new.py\n"
        "similarity index 100%\n"
        "rename from sqlglot/old.py\n"
        "rename to sqlglot/new.py\n"
    )
    change = _one(text, "x = 1\n")
    assert change.module_level is True
