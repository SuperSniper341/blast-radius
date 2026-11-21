"""Parse and apply unified diffs without shelling out to git or patch."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class PatchError(Exception):
    pass


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: list[str] = field(default_factory=list)
    no_newline: bool = False


@dataclass
class FilePatch:
    old_path: str | None = None
    new_path: str | None = None
    hunks: list[Hunk] = field(default_factory=list)
    is_binary: bool = False
    mode_only: bool = False

    @property
    def path(self) -> str | None:
        return self.new_path if self.new_path is not None else self.old_path

    @property
    def is_new(self) -> bool:
        return self.old_path is None

    @property
    def is_deleted(self) -> bool:
        return self.new_path is None


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DIFF_RE = re.compile(r"^diff --git a/(.*) b/(.*)$")


def _strip_prefix(path: str) -> str | None:
    path = path.split("\t", 1)[0].rstrip()
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1].encode().decode("unicode_escape")
    if path == "/dev/null":
        return None
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[2:]
    return path


def parse_patch(text: str) -> list[FilePatch]:
    files: list[FilePatch] = []
    cur: FilePatch | None = None
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.startswith("diff --git "):
            m = _DIFF_RE.match(line)
            cur = FilePatch()
            if m:
                cur.old_path = _strip_prefix("a/" + m.group(1))
                cur.new_path = _strip_prefix("b/" + m.group(2))
            files.append(cur)
            i += 1
            continue
        if cur is None:
            if line.startswith("--- "):
                cur = FilePatch()
                files.append(cur)
            else:
                i += 1
                continue
        if line.startswith("new file mode"):
            cur.old_path = None
        elif line.startswith("deleted file mode"):
            cur.new_path = None
        elif line.startswith("rename from "):
            cur.old_path = _strip_prefix(line[len("rename from "):])
        elif line.startswith("rename to "):
            cur.new_path = _strip_prefix(line[len("rename to "):])
        elif line.startswith("GIT binary patch") or line.startswith("Binary files"):
            cur.is_binary = True
        elif line.startswith("--- "):
            cur.old_path = _strip_prefix(line[4:])
        elif line.startswith("+++ "):
            cur.new_path = _strip_prefix(line[4:])
        elif _HUNK_RE.match(line):
            m = _HUNK_RE.match(line)
            assert m is not None
            hunk = Hunk(
                old_start=int(m.group(1)),
                old_len=int(m.group(2)) if m.group(2) is not None else 1,
                new_start=int(m.group(3)),
                new_len=int(m.group(4)) if m.group(4) is not None else 1,
            )
            cur.hunks.append(hunk)
            old_left, new_left = hunk.old_len, hunk.new_len
            i += 1
            while i < n:
                body = lines[i]
                tag = body[:1]
                if body.startswith("\\"):
                    hunk.no_newline = True
                    i += 1
                    continue
                if tag == " ":
                    old_left -= 1
                    new_left -= 1
                elif tag == "-":
                    old_left -= 1
                elif tag == "+":
                    new_left -= 1
                else:
                    break
                hunk.lines.append(body)
                i += 1
                if old_left <= 0 and new_left <= 0:
                    break
            continue
        elif line.startswith("index ") or line.startswith("old mode") or line.startswith("new mode"):
            if not cur.hunks and cur.old_path and cur.new_path and cur.old_path == cur.new_path:
                cur.mode_only = True
        i += 1
    return files


def _split(text: str) -> tuple[list[str], bool]:
    if text == "":
        return [], False
    final_newline = text.endswith("\n")
    parts = text.split("\n")
    if final_newline:
        parts.pop()
    return parts, final_newline


def apply_patch(old_text: str | None, fp: FilePatch) -> str | None:
    """Return the patched content, or None when the file is deleted."""
    if fp.is_binary:
        raise PatchError(f"binary patch for {fp.path}")
    if fp.is_deleted:
        return None
    base, final_newline = _split(old_text or "")
    if old_text is None:
        final_newline = True
    out: list[str] = []
    cursor = 0
    drop_final_newline = False
    for hunk in fp.hunks:
        start = hunk.old_start - 1 if hunk.old_len or hunk.old_start else 0
        if start < cursor:
            start = cursor
        if start > len(base):
            raise PatchError(f"hunk starts past end of {fp.path}")
        out.extend(base[cursor:start])
        cursor = start
        for body in hunk.lines:
            tag, content = body[:1], body[1:]
            if tag == " ":
                if cursor >= len(base) or base[cursor] != content:
                    raise PatchError(f"context mismatch in {fp.path} at line {cursor + 1}")
                out.append(content)
                cursor += 1
            elif tag == "-":
                if cursor >= len(base) or base[cursor] != content:
                    raise PatchError(f"removal mismatch in {fp.path} at line {cursor + 1}")
                cursor += 1
            elif tag == "+":
                out.append(content)
        if hunk.no_newline:
            drop_final_newline = True
    out.extend(base[cursor:])
    if drop_final_newline:
        final_newline = False
    text = "\n".join(out)
    if final_newline and drop_final_newline:
        text = "\n".join(out)
    return text + ("\n" if final_newline else "")


def changed_old_lines(fp: FilePatch) -> set[int]:
    """Old-file line numbers touched. Insertions count the lines either side."""
    touched: set[int] = set()
    for hunk in fp.hunks:
        lineno = hunk.old_start
        prev = lineno - 1
        body_lines = hunk.lines
        for offset, body in enumerate(body_lines):
            tag = body[:1]
            before = body_lines[offset - 1][:1] if offset else ""
            after = body_lines[offset + 1][:1] if offset + 1 < len(body_lines) else ""
            if tag == " ":
                prev = lineno
                lineno += 1
            elif tag == "-":
                touched.add(lineno)
                prev = lineno
                lineno += 1
            elif tag == "+":
                if before == "-" or after == "-":
                    continue
                if prev >= 1:
                    touched.add(prev)
                touched.add(lineno)
    return touched


def added_lines(fp: FilePatch) -> set[int]:
    """New-file line numbers of added lines (used for new files)."""
    added: set[int] = set()
    for hunk in fp.hunks:
        lineno = hunk.new_start
        for body in hunk.lines:
            tag = body[:1]
            if tag == "+":
                added.add(lineno)
                lineno += 1
            elif tag == " ":
                lineno += 1
    return added
