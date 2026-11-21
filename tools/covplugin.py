"""pytest plugin: per-test line coverage and per-test file opens.

Run as: python -m pytest -p tools.covplugin --cov-out=/path/raw.pkl tests
"""

from __future__ import annotations

import builtins
import glob
import io
import os
import pickle
import shutil
import sys
import tempfile
from array import array

import pytest

_ROOT: str = ""
_OUT: str = ""
_recorder: "Recorder | None" = None


def pytest_addoption(parser):
    parser.addoption("--cov-out", action="store", default="raw_cov.pkl")
    parser.addoption("--cov-root", action="store", default="")


class Recorder:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.files: list[str] = []
        self.fid: dict[str, int] = {}
        self.per_test: dict[str, array] = {}
        self.opens: dict[str, list[str]] = {}
        self.cur: set[int] | None = None
        self.cur_opens: set[str] | None = None
        self.cur_node: str | None = None
        self.orig_open = builtins.open
        self.orig_io_open = io.open
        self._tool = sys.monitoring.COVERAGE_ID
        self.child_dir = tempfile.mkdtemp(prefix="blastcov_")
        os.environ["BLAST_COV_DIR"] = self.child_dir
        os.environ["BLAST_COV_ROOT"] = self.root

    # -- path handling -------------------------------------------------
    def rel(self, path: str) -> str | None:
        try:
            ap = os.path.abspath(path)
        except (OSError, ValueError):
            return None
        if not ap.startswith(self.root):
            return None
        rel = os.path.relpath(ap, self.root).replace(os.sep, "/")
        return rel

    def file_id(self, path: str) -> int:
        fid = self.fid.get(path)
        if fid is None:
            fid = self.fid[path] = len(self.files)
            self.files.append(path)
        return fid

    # -- monitoring ----------------------------------------------------
    def on_line(self, code, line):
        cur = self.cur
        if cur is None:
            return sys.monitoring.DISABLE
        rel = self.rel(code.co_filename)
        if rel is None or "/site-packages/" in rel:
            return sys.monitoring.DISABLE
        cur.add(self.file_id(rel) * 1_000_000 + line)
        return sys.monitoring.DISABLE

    def install(self):
        mon = sys.monitoring
        mon.use_tool_id(self._tool, "blast-radius")
        mon.register_callback(self._tool, mon.events.LINE, self.on_line)
        mon.set_events(self._tool, mon.events.LINE)

        rec = self
        wrap = self.orig_open

        def open_hook(file, *args, **kwargs):
            cur = rec.cur_opens
            if cur is not None and isinstance(file, (str, bytes, os.PathLike)):
                path = os.fsdecode(file)
                if not os.path.isabs(path):
                    path = os.path.join(os.getcwd(), path)
                rel = rec.rel(path)
                if rel is not None:
                    cur.add(rel)
            return wrap(file, *args, **kwargs)

        builtins.open = open_hook
        io.open = open_hook

    def uninstall(self):
        builtins.open = self.orig_open
        io.open = self.orig_io_open
        mon = sys.monitoring
        mon.set_events(self._tool, 0)
        mon.register_callback(self._tool, mon.events.LINE, None)
        mon.free_tool_id(self._tool)

    def start(self, nodeid: str):
        os.environ["BLAST_COV_TEST"] = nodeid
        self.cur = set()
        self.cur_opens = set()
        self.cur_node = nodeid
        sys.monitoring.restart_events()

    def stop(self):
        if self.cur_node is None:
            return
        lines = self.cur or set()
        self.per_test[self.cur_node] = array("q", sorted(lines))
        if self.cur_opens:
            self.opens[self.cur_node] = sorted(self.cur_opens)
        self.cur = None
        self.cur_opens = None
        self.cur_node = None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    rec = _recorder
    if rec is None:
        yield
        return
    rec.start(item.nodeid)
    try:
        yield
    finally:
        rec.stop()


def pytest_configure(config):
    global _recorder, _ROOT
    _ROOT = os.path.abspath(config.getoption("--cov-root") or os.getcwd())
    _recorder = Recorder(_ROOT)
    _recorder.install()


def merge_child_coverage(rec: "Recorder") -> None:
    """Fold coverage dumped by spawned worker processes into their owning test."""
    for dump in glob.glob(os.path.join(rec.child_dir, "child_*.pkl")):
        try:
            with rec.orig_open(dump, "rb") as fh:
                payload = pickle.load(fh)
        except (OSError, pickle.UnpicklingError, EOFError):
            continue
        node = payload.get("node") or ""
        if not node:
            continue
        existing = rec.per_test.get(node)
        packed = set(existing) if existing is not None else set()
        for value in payload.get("lines", ()):
            fid, line = divmod(value, 1_000_000)
            rel = payload["files"][fid]
            packed.add(rec.file_id(rel) * 1_000_000 + line)
        rec.per_test[node] = array("q", sorted(packed))
        if payload.get("opens"):
            opens = set(rec.opens.get(node, ()))
            opens.update(payload["opens"])
            rec.opens[node] = sorted(opens)


def pytest_unconfigure(config):
    global _recorder
    rec = _recorder
    if rec is None:
        return
    merge_child_coverage(rec)
    rec.uninstall()
    out = config.getoption("--cov-out")
    with rec.orig_open(out, "wb") as fh:
        pickle.dump(
            {"files": rec.files, "per_test": rec.per_test, "opens": rec.opens}, fh, protocol=4
        )
    shutil.rmtree(rec.child_dir, ignore_errors=True)
    _recorder = None
