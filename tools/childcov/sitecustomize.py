"""Coverage recorder for sqlglot's spawned worker processes.

Imported via sitecustomize when ``BLAST_COV_DIR`` is set; the plugin passes the
owning test's node id in ``BLAST_COV_TEST`` so lines executed in a spawn child
are attributed to that test.
"""

from __future__ import annotations

import atexit
import builtins
import os
import pickle
import sys
from array import array

DIR = os.environ.get("BLAST_COV_DIR")
ROOT = os.environ.get("BLAST_COV_ROOT") or os.getcwd()
NODE = os.environ.get("BLAST_COV_TEST") or ""

if DIR:
    _root = os.path.abspath(ROOT)
    _files: list[str] = []
    _fid: dict[str, int] = {}
    _lines: set[int] = set()
    _opens: set[str] = set()

    def _rel(path: str) -> str | None:
        try:
            ap = os.path.abspath(path)
        except (OSError, ValueError):
            return None
        if not ap.startswith(_root):
            return None
        return os.path.relpath(ap, _root).replace(os.sep, "/")

    def _file_id(rel: str) -> int:
        fid = _fid.get(rel)
        if fid is None:
            fid = _fid[rel] = len(_files)
            _files.append(rel)
        return fid

    def _on_line(code, line):
        rel = _rel(code.co_filename)
        if rel is not None and "/site-packages/" not in rel:
            _lines.add(_file_id(rel) * 1_000_000 + line)
        return sys.monitoring.DISABLE

    def _install() -> bool:
        try:
            mon = sys.monitoring
        except AttributeError:
            return False
        for tool_id in (4, 3, 2, 0):
            try:
                mon.use_tool_id(tool_id, "blast-child")
            except ValueError:
                continue
            mon.register_callback(tool_id, mon.events.LINE, _on_line)
            mon.set_events(tool_id, mon.events.LINE)
            return True
        return False

    _original_open = builtins.open

    def _open_hook(file, *args, **kwargs):
        try:
            if isinstance(file, (str, bytes, os.PathLike)):
                path = os.fsdecode(file)
                if not os.path.isabs(path):
                    path = os.path.join(os.getcwd(), path)
                rel = _rel(path)
                if rel is not None:
                    _opens.add(rel)
        except Exception:
            pass
        return _original_open(file, *args, **kwargs)

    _dumped = [False]

    def _dump():
        if _dumped[0]:
            return
        _dumped[0] = True
        try:
            payload = {
                "node": NODE,
                "files": _files,
                "lines": array("q", sorted(_lines)),
                "opens": sorted(_opens),
            }
            target = os.path.join(DIR, f"child_{os.getpid()}_{abs(hash(NODE)) % 10**8}.pkl")
            with _original_open(target, "wb") as fh:
                pickle.dump(payload, fh, protocol=4)
        except Exception:
            pass

    if _install():
        builtins.open = _open_hook
        atexit.register(_dump)
        try:
            # multiprocessing's spawn bootstrap exits through os._exit(), so
            # atexit handlers never run there; its own finalizers do.
            from multiprocessing.util import Finalize

            Finalize(None, _dump, exitpriority=-1000)
        except Exception:
            pass
