"""Label synthetic commits by running the full suite on each (local only, never shipped)."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

COPY_ALWAYS = ("pyproject.toml", "setup.py", "setup.cfg", "README.md", "MANIFEST.in")
COPY_DIRS = ("sqlglot", "tests", "sqlglotc")

RESULT_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)")


def prepare(work: Path, root: Path) -> None:
    for name in COPY_DIRS:
        src = root / name
        if src.exists():
            shutil.copytree(src, work / name, symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkout", default=os.path.expanduser("~/blast/sqlglot"))
    ap.add_argument("--commits", default="validation/commits")
    ap.add_argument("--tests", default="kit/sample/tests.json")
    ap.add_argument("--out", default="validation/answers.json")
    ap.add_argument("-n", "--workers", default="8")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    root = Path(os.path.abspath(os.path.expanduser(args.checkout)))
    tests = json.loads(Path(args.tests).read_text())["tests"]
    by_id = {t["id"]: t["number"] for t in tests}
    out_path = Path(args.out)
    answers = {}
    if out_path.exists():
        answers = json.loads(out_path.read_text()).get("commits", {})
    patches = sorted(Path(args.commits).glob("*.patch"))
    if args.only:
        wanted = set(args.only.split(","))
        patches = [p for p in patches if p.stem in wanted]

    work = Path(tempfile.mkdtemp(prefix="lbl_"))
    prepare(work, root)
    env = dict(os.environ, SKIP_INTEGRATION="0", PYTHONDONTWRITEBYTECODE="1")
    env["PYTHONPATH"] = str(work)
    try:
        for index, patch in enumerate(patches):
            if patch.stem in answers and not answers[patch.stem].get("apply_failed"):
                continue
            applied = subprocess.run(
                ["git", "apply", "-p1", str(patch.resolve())], cwd=work, capture_output=True
            )
            if applied.returncode != 0:
                print(f"{patch.stem}: apply failed {applied.stderr[:200]!r}", file=sys.stderr)
                answers[patch.stem] = {"failing": [], "apply_failed": True}
                out_path.write_text(json.dumps({"commits": answers}, indent=1))
                continue
            cmd = [
                sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                "--timeout=60", "-o", "addopts=", f"-n{args.workers}", "tests",
            ]
            proc = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True)
            failing = set()
            for line in proc.stdout.splitlines():
                match = RESULT_RE.match(line)
                if not match:
                    continue
                nodeid = match.group(1)
                if nodeid in by_id:
                    failing.add(by_id[nodeid])
            answers[patch.stem] = {"failing": sorted(failing)}
            print(
                f"[{index + 1}/{len(patches)}] {patch.stem}: {len(failing)} failing "
                f"({proc.returncode})",
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
