"""Label synthetic commits by running the full suite on each (local only, never shipped).

Mutants can hang or explode in memory, so a crashed worker must not lose the whole
result: the run uses thread-based timeouts (the worker survives), a per-process
address-space cap, and junit XML (written even when the session is interrupted).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path

COPY_ALWAYS = ("pyproject.toml", "setup.py", "setup.cfg", "README.md", "MANIFEST.in")
COPY_DIRS = ("sqlglot", "tests", "sqlglotc")
def prepare(work: Path, root: Path) -> None:
    for name in COPY_DIRS:
        src = root / name
        if src.exists():
            shutil.copytree(
                src, work / name, symlinks=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
    for name in COPY_ALWAYS:
        if (root / name).exists():
            shutil.copy(root / name, work / name)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "base"],
        cwd=work,
        check=True,
    )


def reset(work: Path) -> None:
    subprocess.run(["git", "checkout", "-q", "--", "."], cwd=work, check=True)
    subprocess.run(["git", "clean", "-qfdx"], cwd=work, check=True)


def nodeids_from_junit(path: Path, known_files: set[str]) -> set[str]:
    """Rebuild pytest node ids. junit's classname hides the file: find the longest
    leading run of dotted components that names a real test module."""
    out: set[str] = set()
    try:
        tree = ElementTree.parse(path)
    except (OSError, ElementTree.ParseError):
        return out
    for case in tree.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        rel = case.get("file") or ""
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        parts = [c for c in classname.split(".") if c]
        if not rel:
            for cut in range(len(parts), 0, -1):
                candidate = "/".join(parts[:cut]) + ".py"
                if candidate in known_files:
                    rel = candidate
                    parts = parts[cut:]
                    break
            else:
                continue
        elif parts and parts[0] == rel[:-3].replace("/", ".").split(".")[0]:
            rel_parts = rel[:-3].split("/")
            parts = parts[len(rel_parts):]
        nodeid = "::".join([rel, *parts, name])
        if nodeid:
            out.add(nodeid)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkout", default=os.path.expanduser("~/blast/sqlglot"))
    ap.add_argument("--commits", default="validation/commits")
    ap.add_argument("--tests", default="kit/sample/tests.json")
    ap.add_argument("--out", default="validation/answers.json")
    ap.add_argument("-n", "--workers", default="4")
    ap.add_argument("--only", default="")
    ap.add_argument("--kinds", default="", help="comma separated mutant kinds to label")
    ap.add_argument("--relabel", action="store_true")
    args = ap.parse_args()

    root = Path(os.path.abspath(os.path.expanduser(args.checkout)))
    tests = json.loads(Path(args.tests).read_text())["tests"]
    by_id = {t["id"]: t["number"] for t in tests}
    known_files = {t["file"] for t in tests}
    out_path = Path(args.out)
    answers = {}
    if out_path.exists() and not args.relabel:
        answers = json.loads(out_path.read_text()).get("commits", {})
    elif out_path.exists():
        answers = json.loads(out_path.read_text()).get("commits", {})

    patches = sorted(Path(args.commits).glob("*.patch"))
    wanted: set[str] = set(args.only.split(",")) if args.only else set()
    if args.kinds:
        kinds_path = Path(args.commits).parent / "kinds.json"
        kinds = json.loads(kinds_path.read_text())
        for kind in args.kinds.split(","):
            wanted.update(kinds.get(kind, ()))
    if wanted:
        patches = [p for p in patches if p.stem in wanted]

    work = Path(tempfile.mkdtemp(prefix="lbl_"))
    prepare(work, root)
    env = dict(os.environ, SKIP_INTEGRATION="0", PYTHONDONTWRITEBYTECODE="1")
    env["PYTHONPATH"] = str(work)
    try:
        for index, patch in enumerate(patches):
            if patch.stem in answers and not args.relabel:
                continue
            applied = subprocess.run(
                ["git", "apply", "-p1", str(patch.resolve())], cwd=work, capture_output=True
            )
            if applied.returncode != 0:
                print(f"{patch.stem}: apply failed {applied.stderr[:200]!r}", file=sys.stderr)
                answers[patch.stem] = {"failing": [], "apply_failed": True}
                out_path.write_text(json.dumps({"commits": answers}, indent=1))
                continue
            report = work / "report.xml"
            cmd = [
                sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                "--timeout=25", "--timeout-method=thread", "-o", "addopts=",
                f"-n{args.workers}", f"--junitxml={report}", "tests",
            ]
            proc = subprocess.run(
                cmd, cwd=work, env=env, capture_output=True, text=True, timeout=1800,
            )
            failing = set()
            for nodeid in nodeids_from_junit(report, known_files):
                if nodeid in by_id:
                    failing.add(nodeid)
            numbers = sorted({by_id[n] for n in failing})
            answers[patch.stem] = {"failing": numbers, "exit": proc.returncode}
            print(
                f"[{index + 1}/{len(patches)}] {patch.stem}: {len(numbers)} failing "
                f"(exit {proc.returncode})",
                file=sys.stderr,
                flush=True,
            )
            reset(work)
            out_path.write_text(json.dumps({"commits": answers}, indent=1))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out_path.write_text(json.dumps({"commits": answers}, indent=1))
    print(f"wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
