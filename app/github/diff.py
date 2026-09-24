"""Unified-diff parser producing per-file hunks with exact old/new line numbers."""

import re
from dataclasses import dataclass, field
from typing import Any, Literal

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
GIT_HEADER_RE = re.compile(r'^diff --git (?P<a>"?a/.+?"?) (?P<b>"?b/.+?"?)$')

Kind = Literal["add", "del", "ctx"]
Status = Literal["added", "modified", "deleted", "renamed"]


@dataclass(frozen=True)
class DiffLine:
    kind: Kind
    old_no: int | None
    new_no: int | None
    content: str


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    header: str
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    path: str  # path in the new tree (the old path for a deletion)
    old_path: str | None = None
    status: Status = "modified"
    binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "add")

    @property
    def deleted(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "del")

    @property
    def commentable_lines(self) -> set[int]:
        """New-file line numbers GitHub accepts an inline RIGHT-side comment on."""
        return {
            ln.new_no
            for h in self.hunks
            for ln in h.lines
            if ln.new_no is not None and ln.kind in ("add", "ctx")
        }


def _unquote(path: str) -> str:
    """Undo git's C-style quoting of unusual paths."""
    if not (path.startswith('"') and path.endswith('"')):
        return path
    body = path[1:-1]
    try:
        return body.encode("latin-1").decode("unicode_escape").encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return body


def _strip_prefix(path: str, prefix: str) -> str:
    path = _unquote(path.strip())
    return path[len(prefix) :] if path.startswith(prefix) else path


def parse_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    hunk: Hunk | None = None
    old_left = new_left = 0
    old_no = new_no = 0

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1

        # Inside a hunk the counters, not the line prefixes, decide where the hunk ends. A removed
        # line whose text starts with "-- " looks like a "--- " file header otherwise.
        if hunk is not None and (old_left > 0 or new_left > 0):
            if raw.startswith("\\"):  # "\ No newline at end of file"
                continue
            prefix, content = (raw[:1], raw[1:]) if raw else (" ", "")
            if prefix == "+":
                hunk.lines.append(DiffLine("add", None, new_no, content))
                new_no += 1
                new_left -= 1
            elif prefix == "-":
                hunk.lines.append(DiffLine("del", old_no, None, content))
                old_no += 1
                old_left -= 1
            else:
                hunk.lines.append(DiffLine("ctx", old_no, new_no, content))
                old_no += 1
                new_no += 1
                old_left -= 1
                new_left -= 1
            continue

        raw = raw.rstrip("\r")
        if raw.startswith("diff --git "):
            hunk = None
            m = GIT_HEADER_RE.match(raw)
            path = _strip_prefix(m["b"], "b/") if m else raw[len("diff --git ") :]
            cur = FileDiff(path=path, old_path=_strip_prefix(m["a"], "a/") if m else None)
            files.append(cur)
        elif cur is None:
            continue
        elif raw.startswith("new file mode"):
            cur.status = "added"
        elif raw.startswith("deleted file mode"):
            cur.status = "deleted"
        elif raw.startswith("rename from "):
            cur.status, cur.old_path = "renamed", _unquote(raw[len("rename from ") :])
        elif raw.startswith("rename to "):
            cur.status, cur.path = "renamed", _unquote(raw[len("rename to ") :])
        elif raw.startswith(("Binary files", "GIT binary patch")):
            cur.binary = True
        elif raw.startswith("--- "):
            target = raw[4:].split("\t")[0]
            if target != "/dev/null":
                cur.old_path = _strip_prefix(target, "a/")
            else:
                cur.status = "added"
        elif raw.startswith("+++ "):
            target = raw[4:].split("\t")[0]
            if target == "/dev/null":
                cur.status = "deleted"
                cur.path = cur.old_path or cur.path
            else:
                cur.path = _strip_prefix(target, "b/")
        else:
            m2 = HUNK_RE.match(raw)
            if m2:
                old_start, new_start = int(m2[1]), int(m2[3])
                old_len = int(m2[2]) if m2[2] is not None else 1
                new_len = int(m2[4]) if m2[4] is not None else 1
                hunk = Hunk(old_start, old_len, new_start, new_len, raw)
                cur.hunks.append(hunk)
                old_left, new_left = old_len, new_len
                old_no, new_no = old_start, new_start
    return files


def build_diff_from_files(files: list[dict[str, Any]]) -> str:
    """Rebuild a unified diff from GitHub's `pulls/{n}/files` payload (used for huge PRs)."""
    parts: list[str] = []
    for f in files:
        name, old = f["filename"], f.get("previous_filename") or f["filename"]
        status = f.get("status", "modified")
        parts.append(f"diff --git a/{old} b/{name}")
        if status == "added":
            parts.append("new file mode 100644")
        elif status == "removed":
            parts.append("deleted file mode 100644")
        elif status == "renamed":
            parts += [f"rename from {old}", f"rename to {name}"]
        patch = f.get("patch")
        if patch is None:
            parts.append(f"Binary files a/{old} and b/{name} differ")
            continue
        parts += [
            "--- /dev/null" if status == "added" else f"--- a/{old}",
            "+++ /dev/null" if status == "removed" else f"+++ b/{name}",
            patch,
        ]
    return "\n".join(parts) + "\n"
