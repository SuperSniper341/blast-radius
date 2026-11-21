"""Generate synthetic commits (patches against the pinned checkout) for validation.

    python -m tools.mutants --checkout ~/blast/sqlglot --out validation --n 120

Only for local use: the labelled output must never be shipped.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import random
from pathlib import Path

SKIP_DIRS = {".git", "docs", "posts", "benchmarks", "pdoc", ".github", "sqlglot-integration-tests"}


def iter_sources(root: Path, sub: str, suffix: str = ".py"):
    base = root / sub
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(suffix):
                yield Path(dirpath) / name


def make_patch(root: Path, rel: str, old: str, new: str) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3)
    )
    if not diff:
        return ""
    body = "".join(diff)
    return f"diff --git a/{rel} b/{rel}\n{body}"


OP_SWAPS = [
    (" and ", " or "),
    (" or ", " and "),
    (" == ", " != "),
    (" != ", " == "),
    (" is not ", " is "),
    (" is None", " is not None"),
    (" is not None", " is None"),
    (" <= ", " < "),
    (" < ", " <= "),
    (" >= ", " > "),
    (" > ", " >= "),
    ("not ", ""),
]


def mutate_code_line(line: str, rng: random.Random) -> str | None:
    if "def " in line or "class " in line or line.lstrip().startswith("#"):
        return None
    rng.shuffle(OP_SWAPS)
    for old, new in OP_SWAPS:
        if old in line:
            return line.replace(old, new, 1)
    if "True" in line:
        return line.replace("True", "False", 1)
    if "False" in line:
        return line.replace("False", "True", 1)
    return None


def pick_line(lines: list[str], rng: random.Random, need) -> int | None:
    candidates = [i for i, line in enumerate(lines) if need(line.strip("\n"))]
    if not candidates:
        return None
    return rng.choice(candidates)


def op_code(root: Path, rng: random.Random) -> tuple[str, str] | None:
    files = list(iter_sources(root, "sqlglot"))
    for _ in range(20):
        path = rng.choice(files)
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        lines = text.splitlines(keepends=True)
        idx = pick_line(lines, rng, lambda s: mutate_code_line(s, random.Random(0)) is not None)
        if idx is None:
            continue
        new_line = mutate_code_line(lines[idx], rng)
        if new_line is None or new_line == lines[idx]:
            continue
        lines[idx] = new_line
        patch = make_patch(root, rel, text, "".join(lines))
        if patch:
            return "code", patch
    return None


def op_comment(root: Path, rng: random.Random) -> tuple[str, str] | None:
    files = list(iter_sources(root, "sqlglot"))
    path = rng.choice(files)
    rel = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    lines = text.splitlines(keepends=True)
    idx = pick_line(lines, rng, lambda s: bool(s) and not s.startswith("#"))
    if idx is None:
        return None
    indent = lines[idx][: len(lines[idx]) - len(lines[idx].lstrip())]
    lines.insert(idx, f"{indent}# note\n")
    patch = make_patch(root, rel, text, "".join(lines))
    return ("comment", patch) if patch else None


def op_string(root: Path, rng: random.Random) -> tuple[str, str] | None:
    """Edit inside a string literal or docstring - not harmless."""
    files = list(iter_sources(root, "sqlglot"))
    for _ in range(20):
        path = rng.choice(files)
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        lines = text.splitlines(keepends=True)
        idx = pick_line(lines, rng, lambda s: s.count('"') >= 2 or s.count("'") >= 2)
        if idx is None:
            continue
        line = lines[idx]
        pos = max(line.find('"'), line.find("'"))
        if pos < 0 or pos + 1 >= len(line):
            continue
        lines[idx] = line[: pos + 1] + "Z" + line[pos + 1 :]
        patch = make_patch(root, rel, text, "".join(lines))
        if patch:
            return "string", patch
    return None


def op_fixture(root: Path, rng: random.Random) -> tuple[str, str] | None:
    files = list(iter_sources(root, "tests/fixtures", ".sql"))
    for _ in range(20):
        path = rng.choice(files)
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        lines = text.splitlines(keepends=True)
        idx = pick_line(
            lines,
            rng,
            lambda s: len(s) > 12 and not s.startswith("#") and ";" in s,
        )
        if idx is None:
            continue
        lines[idx] = lines[idx].replace(";", " ;", 1).replace("SELECT", "SELECT 1+", 1)
        patch = make_patch(root, rel, text, "".join(lines))
        if patch:
            return "fixture", patch
    return None


def op_test(root: Path, rng: random.Random) -> tuple[str, str] | None:
    files = list(iter_sources(root, "tests"))
    for _ in range(20):
        path = rng.choice(files)
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        lines = text.splitlines(keepends=True)
        idx = pick_line(
            lines,
            rng,
            lambda s: "assert" in s or "==" in s or "==" in s or "True" in s or "False" in s,
        )
        if idx is None:
            continue
        line = lines[idx]
        if "==" in line:
            lines[idx] = line.replace("==", "!=", 1)
        elif "True" in line:
            lines[idx] = line.replace("True", "False", 1)
        elif "False" in line:
            lines[idx] = line.replace("False", "True", 1)
        else:
            continue
        patch = make_patch(root, rel, text, "".join(lines))
        if patch:
            return "test", patch
    return None


def op_new_file(root: Path, rng: random.Random) -> tuple[str, str] | None:
    rel = f"sqlglot/_scratch_{rng.randrange(10**6)}.py"
    body = "def helper(x):\n    return x\n"
    lines = body.splitlines(keepends=True)
    diff = "".join(
        difflib.unified_diff([], lines, fromfile="/dev/null", tofile=f"b/{rel}", n=3)
    )
    return "new_file", f"diff --git a/{rel} b/{rel}\nnew file mode 100644\n{diff}"


def op_class_body(root: Path, rng: random.Random) -> tuple[str, str] | None:
    """Flip a constant inside a class body (like a dialect flag)."""
    files = list(iter_sources(root, "sqlglot"))
    for _ in range(30):
        path = rng.choice(files)
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        lines = text.splitlines(keepends=True)
        idx = pick_line(
            lines,
            rng,
            lambda s: s[:1].isspace() and (" = " in s) and not s.lstrip().startswith(("#", "def", "return")),
        )
        if idx is None:
            continue
        line = lines[idx]
        if " = False" in line:
            lines[idx] = line.replace(" = False", " = True", 1)
        elif " = True" in line:
            lines[idx] = line.replace(" = True", " = False", 1)
        elif " = 1\n" in line:
            lines[idx] = line.replace(" = 1\n", " = 2\n", 1)
        elif " = -1" in line:
            lines[idx] = line.replace(" = -1", " = -2", 1)
        else:
            continue
        patch = make_patch(root, rel, text, "".join(lines))
        if patch:
            return "class_body", patch
    return None


def op_reflow(root: Path, rng: random.Random) -> tuple[str, str] | None:
    """Split a call across two lines: same tokens, different line numbers."""
    files = list(iter_sources(root, "sqlglot"))
    for _ in range(20):
        path = rng.choice(files)
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        lines = text.splitlines(keepends=True)
        idx = pick_line(
            lines,
            rng,
            lambda s: s.rstrip().endswith(")") and s.count(",") >= 2 and len(s) > 40,
        )
        if idx is None:
            continue
        line = lines[idx]
        cut = line.find("(", line.find("(") + 1)
        if cut < 0:
            continue
        indent = line[: len(line) - len(line.lstrip())] + "    "
        head, tail = line[: cut + 1], line[cut + 1 :]
        lines[idx] = f"{head}\n{indent}{tail}"
        patch = make_patch(root, rel, text, "".join(lines))
        if patch:
            return "reflow", patch
    return None


OPS = [
    op_code, op_comment, op_string, op_fixture, op_test, op_class_body, op_new_file, op_reflow,
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkout", default=os.path.expanduser("~/blast/sqlglot"))
    ap.add_argument("--out", default="validation")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    root = Path(os.path.abspath(os.path.expanduser(args.checkout)))
    out = Path(args.out)
    commits = out / "commits"
    commits.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    kinds: dict[str, list[str]] = {}
    made = 0
    attempt = 0
    while made < args.n and attempt < args.n * 6:
        attempt += 1
        op = OPS[made % len(OPS)]
        result = op(root, rng)
        if not result:
            continue
        kind, patch = result
        name = f"m{made:04d}"
        (commits / f"{name}.patch").write_text(patch, encoding="utf-8", newline="\n")
        kinds.setdefault(kind, []).append(name)
        made += 1
    (out / "kinds.json").write_text(json.dumps(kinds, indent=1), encoding="utf-8")
    print(f"wrote {made} patches to {commits}")


if __name__ == "__main__":
    main()
