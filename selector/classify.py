"""Classify one file's change: is it behavioural, and where."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field

from .diff import FilePatch, changed_old_lines

PY_SRC_PREFIX = "sqlglot/"
TESTS_PREFIX = "tests/"
FIXTURE_PREFIX = "tests/fixtures/"

# Files outside sqlglot/ and tests/ that packaging/metadata tests read.
PACKAGING = {
    "setup.py",
    "setup.cfg",
    "pyproject.toml",
    "MANIFEST.in",
    "Makefile",
    "sqlglotc/setup.py",
    "sqlglotc/pyproject.toml",
    "sqlglotc/MANIFEST.in",
}

# Paths that no test can observe.
IGNORED_PREFIXES = (
    "docs/",
    "posts/",
    "benchmarks/",
    "pdoc/",
    ".github/",
    "sqlglot-integration-tests/",
)
IGNORED_FILES = {
    "README.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "sqlglot.png",
    ".gitignore",
    ".gitattributes",
    ".pre-commit-config.yaml",
}


@dataclass
class Change:
    path: str
    kind: str  # py_src | py_test | conftest | fixture | packaging | other | ignored
    token_changed: bool
    module_level: bool
    changed_lines: set[int] = field(default_factory=set)
    names: set[str] = field(default_factory=set)
    span: tuple[int, int] | None = None
    new_file: bool = False
    deleted_file: bool = False
    text: str | None = None


def kind_of(path: str) -> str:
    if path.startswith(FIXTURE_PREFIX):
        return "fixture"
    if path.endswith("conftest.py"):
        return "conftest"
    if path.startswith(PY_SRC_PREFIX) and path.endswith(".py"):
        return "py_src"
    if path.startswith(TESTS_PREFIX) and path.endswith(".py"):
        return "py_test"
    if path in PACKAGING:
        return "packaging"
    if path.startswith(IGNORED_PREFIXES) or path in IGNORED_FILES:
        return "ignored"
    return "other"


def _tokens(source: str) -> list[tuple[int, str]] | None:
    try:
        return [
            (tok.type, tok.string)
            for tok in tokenize.generate_tokens(io.StringIO(source).readline)
            if tok.type not in (tokenize.COMMENT, tokenize.NL)
        ]
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return None


def token_equivalent(old: str | None, new: str | None) -> bool:
    if old is None or new is None:
        return False
    a = _tokens(old)
    b = _tokens(new)
    if a is None or b is None:
        return False
    return a == b


def _function_body_lines(source: str) -> set[int] | None:
    """Line numbers strictly inside a function body (not decorators/signature)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Lambda):
            body = node.body
            first = getattr(body, "lineno", node.lineno)
            last = getattr(body, "end_lineno", None) or first
            inside.update(range(first, last + 1))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body:
                first = body[0].lineno
                ends = [
                    getattr(child, "end_lineno", None) or getattr(child, "lineno", 0)
                    for child in ast.walk(node)
                ]
                last = max(ends)
                inside.update(range(first, last + 1))
    return inside


_ASSIGN_RE = re.compile(r'^\s*([A-Za-z_]\w*)\s*[:=]')
_KEY_RE = re.compile(r'^\s*"([^"]+)"\s*:')
_STR_RE = re.compile(r'^\s*(?:[A-Za-z_]\w*\s*[=:]\s*)?"([^"]+)"')


def _names_on_line(line: str) -> set[str]:
    out: set[str] = set()
    for pattern in (_ASSIGN_RE, _KEY_RE, _STR_RE):
        match = pattern.match(line)
        if match:
            out.add(match.group(1))
    return out


def _enclosing_span(source: str, lines: set[int]) -> tuple[int, int] | None:
    """Innermost function body covering the changed lines.

    A change to a condition can make an operand evaluate where it did not
    before (short-circuit change), so every line of the enclosing function is
    a candidate line, not only the edited one.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    best: tuple[int, int] | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Lambda):
            body = node.body
            first = getattr(body, "lineno", node.lineno)
            last = getattr(body, "end_lineno", None) or first
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.body:
                continue
            first = node.body[0].lineno
            ends = [
                getattr(child, "end_lineno", None) or getattr(child, "lineno", 0)
                for child in ast.walk(node)
            ]
            last = max(ends)
        else:
            continue
        if not any(first <= line <= last for line in lines):
            continue
        if best is None or (last - first) < (best[1] - best[0]):
            best = (first, last)
    return best


def _enclosing_class(source: str, lines: set[int]) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        last = getattr(node, "end_lineno", node.lineno) or node.lineno
        if any(node.lineno <= line <= last for line in lines):
            out.add(node.name)
    return out


def classify(fp: FilePatch, old_text: str | None, new_text: str | None) -> Change:
    path = fp.path or ""
    kind = kind_of(path)
    change = Change(
        path=path,
        kind=kind,
        token_changed=True,
        module_level=True,
        changed_lines=changed_old_lines(fp),
        new_file=fp.is_new,
        deleted_file=fp.is_deleted,
        text=new_text,
    )

    if fp.is_binary or (not fp.hunks and not (fp.is_new or fp.is_deleted)):
        # Nothing we can read: assume it changes behaviour.
        change.token_changed = True
        change.module_level = True
        return change

    if fp.old_path and fp.new_path and fp.old_path != fp.new_path:
        change.module_level = True
        return change

    if fp.mode_only and not fp.hunks:
        change.token_changed = False
        change.module_level = False
        change.changed_lines = set()
        return change

    if not path.endswith(".py"):
        return change

    if not fp.is_new and not fp.is_deleted and token_equivalent(old_text, new_text):
        change.token_changed = False
        change.module_level = False
        return change

    if fp.is_new or fp.is_deleted or old_text is None or new_text is None:
        change.module_level = True
        return change

    inside = _function_body_lines(old_text)
    if inside is None:
        change.module_level = True
        return change
    change.module_level = any(line not in inside for line in change.changed_lines)
    if not change.module_level:
        change.span = _enclosing_span(old_text, change.changed_lines)
    if change.module_level:
        source_lines = old_text.splitlines()
        change.names |= _enclosing_class(old_text, change.changed_lines)
        for lineno in change.changed_lines:
            if 1 <= lineno <= len(source_lines):
                change.names |= _names_on_line(source_lines[lineno - 1])
    return change
