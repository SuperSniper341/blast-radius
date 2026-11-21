"""Build the maps the selector uses, from the pinned sqlglot checkout.

    python -m tools.build_maps --checkout ~/blast/sqlglot --out maps

Writes maps/maps.pkl.gz holding:
  tests      : canonical list of node ids (bit index == position)
  cov        : relpath -> {line: bitset int}
  opens      : relpath -> bitset
  imports    : relpath -> bitset (tests whose import closure reaches the file)
  pkg_users  : relpath -> bitset (tests that executed any line in the file)
"""

from __future__ import annotations

import argparse
import ast
import gzip
import os
import pickle
import subprocess
import sys
import tempfile

SQLGLOT_PREFIX = "sqlglot/"


def run_pytest(checkout: str, raw: str, root: str, extra: list[str]) -> None:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "tools.covplugin",
        "-p",
        "tools.inline_mp",
        f"--cov-out={raw}",
        f"--cov-root={root}",
        *extra,
    ]
    env = dict(os.environ)
    env["SKIP_INTEGRATION"] = "0"
    childcov = os.path.join(os.getcwd(), "tools", "childcov")
    env["PYTHONPATH"] = os.pathsep.join([os.getcwd(), checkout, childcov])
    print("running:", " ".join(cmd), file=sys.stderr)
    proc = subprocess.run(cmd, cwd=checkout, env=env)
    if proc.returncode != 0:
        print(f"pytest exited {proc.returncode}", file=sys.stderr)


def module_path(name: str, tree_files: set[str]) -> list[str]:
    """Files a dotted sqlglot module name can resolve to."""
    rel = name.replace(".", "/")
    out = []
    for cand in (rel + ".py", rel + "/__init__.py"):
        if cand in tree_files:
            out.append(cand)
    return out


def file_imports(path: str, source: str, tree_files: set[str]) -> set[str]:
    """Imports of sqlglot (and tests) modules made by one source file."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in ("sqlglot", "tests"):
                    found.update(module_path(alias.name, tree_files))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            mod = node.module or ""
            if mod.split(".")[0] not in ("sqlglot", "tests"):
                continue
            found.update(module_path(mod, tree_files))
            for alias in node.names:
                found.update(module_path(f"{mod}.{alias.name}", tree_files))
    return found


def build_import_map(root: str) -> dict[str, set[str]]:
    """relpath -> set of relpaths whose import closure reaches it."""
    sources: dict[str, str] = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".py"):
                continue
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if not (rel.startswith(SQLGLOT_PREFIX) or rel.startswith("tests/")):
                continue
            try:
                with open(full, encoding="utf-8") as fh:
                    sources[rel] = fh.read()
            except (OSError, UnicodeDecodeError):
                continue

    tree_files = set(sources)
    direct = {rel: file_imports(rel, src, tree_files) for rel, src in sources.items()}

    # Transitive closure over sqlglot modules only (tests are entry points).
    closure: dict[str, set[str]] = {}
    for module in tree_files:
        seen: set[str] = set()
        stack = [module]
        while stack:
            cur = stack.pop()
            for imp in direct.get(cur, ()):  # noqa: B007
                if imp in seen:
                    continue
                seen.add(imp)
                stack.append(imp)
        closure[module] = seen

    reach: dict[str, set[str]] = {module: set() for module in tree_files}
    for module, seen in closure.items():
        for target in seen:
            reach[target].add(module)
    return reach


def build_fixture_map(root: str, cov: dict, opens: dict) -> dict[str, int]:
    """Module stem -> tests that open a fixture mentioning that name.

    A query fixture tagged ``# dialect: tsql`` makes the test that reads it a
    user of the tsql dialect, however indirectly it reaches the changed code.
    """
    import re

    stems = set()
    for base, dirnames, files in os.walk(os.path.join(root, "sqlglot")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in files:
            if name.endswith(".py"):
                stems.add(name[:-3].lower())
    stems.discard("__init__")

    out: dict[str, int] = {}
    for rel, mask in opens.items():
        if not rel.startswith("tests/fixtures/"):
            continue
        try:
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        words = {word.lower() for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)}
        for word in words & stems:
            out[word] = out.get(word, 0) | mask
    return out


def bits_of(indices) -> int:
    value = 0
    for i in indices:
        value |= 1 << i
    return value


def function_body_lines(root: str, rel: str, cache: dict[str, set[int]]) -> set[int]:
    if rel not in cache:
        inside: set[int] = set()
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
        except (OSError, SyntaxError, UnicodeDecodeError):
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
                    last = max(
                        getattr(c, "end_lineno", None) or getattr(c, "lineno", 0)
                        for c in ast.walk(node)
                    )
                    inside.update(range(node.body[0].lineno, last + 1))
                elif isinstance(node, ast.Lambda):
                    body = node.body
                    inside.update(range(body.lineno, (body.end_lineno or body.lineno) + 1))
        cache[rel] = inside
    return cache[rel]


def spread_shared_setup(root, files, per_test, opens_raw, fixture_lines, collect_lines, collect_opens):
    """Give every test the code its shared setup ran.

    Class fixtures (setUpClass/tearDownClass) run inside the first/last test of a
    class only, and module-level code of a test file runs at collection, inside
    no test. A break there fails every test of the class/file, so those lines
    belong to all of them. Collection lines are kept only when they sit inside a
    function body: module statements executed by an import are module-level
    lines, which the selector handles through file users, not line coverage.
    """
    per_class: dict[str, set[int]] = {}
    for nodeid, packed in fixture_lines.items():
        per_class.setdefault(nodeid.rsplit("::", 1)[0], set()).update(packed)

    body_cache: dict[str, set[int]] = {}
    per_file: dict[str, set[int]] = {}
    for rel, packed in collect_lines.items():
        keep = per_file.setdefault(rel, set())
        for value in packed:
            fid, line = divmod(value, 1_000_000)
            if line in function_body_lines(root, files[fid], body_cache):
                keep.add(value)

    out: dict[str, list[int]] = {}
    out_opens: dict[str, list[str]] = dict(opens_raw)
    for nodeid, packed in per_test.items():
        extra = per_class.get(nodeid.rsplit("::", 1)[0], set())
        rel = nodeid.split("::", 1)[0]
        extra = extra | per_file.get(rel, set())
        out[nodeid] = sorted(set(packed) | extra) if extra else packed
        if rel in collect_opens:
            out_opens[nodeid] = sorted(set(opens_raw.get(nodeid, ())) | set(collect_opens[rel]))
    return out, out_opens


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkout", default=os.path.expanduser("~/blast/sqlglot"))
    ap.add_argument("--out", default="maps")
    ap.add_argument("--reuse-raw", default="")
    ap.add_argument("--extra", nargs="*", default=["tests"])
    args = ap.parse_args()

    checkout = os.path.abspath(os.path.expanduser(args.checkout))
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    if args.reuse_raw:
        raw = args.reuse_raw
    else:
        fd, raw = tempfile.mkstemp(suffix=".pkl")
        os.close(fd)
        run_pytest(checkout, raw, checkout, args.extra)

    with open(raw, "rb") as fh:
        data = pickle.load(fh)
    files: list[str] = data["files"]
    per_test: dict[str, list[int]] = data["per_test"]
    opens_raw: dict[str, list[str]] = data["opens"]

    tests = sorted(per_test)
    index_of = {nodeid: i for i, nodeid in enumerate(tests)}
    print(f"{len(tests)} tests, {len(files)} files", file=sys.stderr)
    per_test, opens_raw = spread_shared_setup(
        checkout, files, per_test, opens_raw, data.get("fixture_lines", {}),
        data.get("collect_lines", {}), data.get("collect_opens", {}),
    )

    cov: dict[str, dict[int, int]] = {}
    pkg_users: dict[str, int] = {}
    for nodeid, packed in per_test.items():
        bit = 1 << index_of[nodeid]
        per_file_lines: dict[str, list[int]] = {}
        for value in packed:
            fid, line = divmod(value, 1_000_000)
            per_file_lines.setdefault(files[fid], []).append(line)
        for rel, line_list in per_file_lines.items():
            pkg_users[rel] = pkg_users.get(rel, 0) | bit
            lines = cov.setdefault(rel, {})
            for line in line_list:
                lines[line] = lines.get(line, 0) | bit

    opens: dict[str, int] = {}
    for nodeid, paths in opens_raw.items():
        bit = 1 << index_of[nodeid]
        for rel in paths:
            opens[rel] = opens.get(rel, 0) | bit

    file_bits: dict[str, int] = {}
    tests_by_file: dict[str, list[str]] = {}
    for nodeid in tests:
        rel = nodeid.split("::", 1)[0]
        tests_by_file.setdefault(rel, []).append(nodeid)
        file_bits[rel] = file_bits.get(rel, 0) | (1 << index_of[nodeid])

    fixture_bits = build_fixture_map(checkout, cov, opens)

    reach = build_import_map(checkout)
    imports: dict[str, int] = {}
    for rel, importers in reach.items():
        mask = 0
        for importer in importers:
            for nodeid in tests_by_file.get(importer, ()):
                mask |= 1 << index_of[nodeid]
        if mask:
            imports[rel] = mask

    rev_users: dict[str, int] = {}
    for rel, importers in reach.items():
        mask = pkg_users.get(rel, 0) | opens.get(rel, 0)
        for importer in importers:
            mask |= pkg_users.get(importer, 0)
        if mask:
            rev_users[rel] = mask

    payload = {
        "tests": tests,
        "cov": cov,
        "opens": opens,
        "pkg_users": pkg_users,
        "imports": imports,
        "rev_users": rev_users,
        "file_bits": file_bits,
        "fixtures": fixture_bits,
    }
    dst = os.path.join(out_dir, "maps.pkl.gz")
    with gzip.open(dst, "wb", compresslevel=9) as fh:
        pickle.dump(payload, fh, protocol=4)
    print(f"wrote {dst} {os.path.getsize(dst) / 1e6:.2f} MB", file=sys.stderr)


if __name__ == "__main__":
    main()
