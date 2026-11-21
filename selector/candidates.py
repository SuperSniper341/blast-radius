"""Turn classified changes into a set of test ids."""

from __future__ import annotations

import ast
import gzip
import pickle

from .classify import Change


class Maps:
    def __init__(self, payload: dict):
        self.tests: list[str] = payload["tests"]
        self.index: dict[str, int] = {name: i for i, name in enumerate(self.tests)}
        self.cov: dict[str, dict[int, int]] = payload["cov"]
        self.opens: dict[str, int] = payload["opens"]
        self.imports: dict[str, int] = payload["imports"]
        self.pkg_users: dict[str, int] = payload["pkg_users"]
        self.file_bits: dict[str, int] = payload.get("file_bits", {})
        self.names: dict[str, int] = payload.get("names", {})
        self.fixtures: dict[str, int] = payload.get("fixtures", {})
        self.full: int = (1 << len(self.tests)) - 1

    @classmethod
    def load(cls, path: str) -> "Maps":
        with gzip.open(path, "rb") as fh:
            return cls(pickle.load(fh))

    def ids(self, bits: int) -> list[str]:
        out = []
        tests = self.tests
        while bits:
            low = bits & -bits
            out.append(tests[low.bit_length() - 1])
            bits ^= low
        return out

    def line_bits(self, path: str, lines: set[int]) -> int:
        table = self.cov.get(path)
        if not table:
            return 0
        mask = 0
        for line in lines:
            mask |= table.get(line, 0)
        return mask

    def module_bits(self, path: str, wide: bool = False) -> int:
        """Users of a file whose module-level code changed.

        Line coverage cannot see a class attribute that other code reads, so
        this widens to everyone that runs the file plus everyone whose fixtures
        name it (a ``# dialect: tsql`` fixture line, for instance).
        """
        mask = self.pkg_users.get(path, 0) | self.opens.get(path, 0)
        stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        mask |= self.fixtures.get(stem, 0)
        if wide:
            mask |= self.imports.get(path, 0)
        if not mask:
            return self.full
        return mask


def _test_method_bits(path: str, lines: set[int], source: str, maps: Maps) -> int:
    """Tests of a test file whose method body covers the changed lines."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return maps.file_bits.get(path, maps.full)
    hits = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0].lineno if node.body else node.lineno
        last = getattr(node, "end_lineno", node.lineno) or node.lineno
        if not any(first <= line <= last for line in lines):
            continue
        qual = node.name
        idx = maps.index.get(f"{path}::{qual}")
        if idx is None:
            parent = node.parent if hasattr(node, "parent") else None
            cls = _enclosing_class(tree, node)
            if cls:
                idx = maps.index.get(f"{path}::{cls}::{qual}")
        if idx is not None:
            hits |= 1 << idx
    return hits or maps.file_bits.get(path, maps.full)


def _enclosing_class(tree: ast.AST, target: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and target in ast.walk(node):
            return node.name
    return None


def change_bits(change: Change, maps: Maps, wide_imports: bool = False) -> int:
    kind = change.kind
    path = change.path

    if kind == "ignored":
        return 0

    if kind == "py_src":
        if change.deleted_file:
            return maps.full
        if not change.token_changed:
            return maps.opens.get(path, 0)
        if change.module_level:
            return maps.module_bits(path, wide=wide_imports)
        lines = change.changed_lines
        if change.span is not None:
            lines = set(range(change.span[0], change.span[1] + 1))
        mask = maps.opens.get(path, 0) | maps.line_bits(path, lines)
        return mask or maps.module_bits(path, wide=wide_imports)

    if kind == "py_test":
        if change.module_level or change.deleted_file:
            return maps.file_bits.get(path, maps.full)
        mask = maps.line_bits(path, change.changed_lines) | maps.file_bits.get(path, 0)
        if change.text is not None:
            mask |= _test_method_bits(path, change.changed_lines, change.text, maps)
        return mask or maps.file_bits.get(path, maps.full)

    if kind == "conftest":
        return maps.full

    if kind == "fixture":
        return maps.opens.get(path, 0) or maps.full

    if kind == "packaging":
        return maps.opens.get(path, 0) | maps.file_bits.get(
            "tests/test_sqlglotc.py", 0
        ) | maps.file_bits.get("tests/test_dialect_entry_points.py", 0)

    mask = maps.opens.get(path, 0)
    if mask:
        return mask
    if path.startswith("sqlglot/"):
        return 0
    return maps.full


def select(changes: list[Change], maps: Maps, wide_imports: bool = False) -> set[str]:
    mask = 0
    for change in changes:
        mask |= change_bits(change, maps, wide_imports=wide_imports)
    return set(maps.ids(mask & maps.full))
