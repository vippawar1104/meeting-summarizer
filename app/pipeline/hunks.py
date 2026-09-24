import re
from dataclasses import dataclass

from app.github.diff import DiffLine, FileDiff, Hunk


@dataclass
class HunkGroup:
    """A slice of one file's diff that fits in a single LLM call."""

    path: str
    text: str
    valid_lines: set[int]
    added: int
    added_text: str = ""  # the raw text of added lines, used as the retrieval query
    norm_text: str = ""  # formatting-insensitive form of the group, used as the cache key

    @property
    def chars(self) -> int:
        return len(self.text)


_TOKEN = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\w+|[^\w\s]")


def normalize_line(ln: DiffLine) -> str:
    """Ignores spacing inside a line (so reformatting still matches) but never anything that
    changes meaning: tokens, string contents, operators and the indentation itself are kept."""
    indent = ln.content[: len(ln.content) - len(ln.content.lstrip())]
    return f"{ln.new_no}|{ln.kind}|{indent!r}|" + " ".join(_TOKEN.findall(ln.content))


def render_line(ln: DiffLine) -> str:
    """Render with the new-file line number so the model can cite exact lines."""
    if ln.kind == "add":
        return f"{ln.new_no:>5} + {ln.content}"
    if ln.kind == "del":
        return f"{'':>5} - {ln.content}"
    return f"{ln.new_no:>5}   {ln.content}"


def _split_hunk(hunk: Hunk, max_chars: int) -> list[list[DiffLine]]:
    """Cut an oversized hunk into runs of lines that each fit the budget."""
    runs: list[list[DiffLine]] = [[]]
    size = 0
    for ln in hunk.lines:
        cost = len(render_line(ln)) + 1
        if runs[-1] and size + cost > max_chars:
            runs.append([])
            size = 0
        runs[-1].append(ln)
        size += cost
    return runs


def _render(path: str, header: str, lines: list[DiffLine]) -> str:
    return f"{header}\n" + "\n".join(render_line(ln) for ln in lines)


def group_file(f: FileDiff, max_chars: int) -> list[HunkGroup]:
    groups: list[HunkGroup] = []
    pending: list[tuple[str, list[DiffLine]]] = []
    pending_size = 0

    def flush() -> None:
        nonlocal pending, pending_size
        if not pending:
            return
        body = "\n".join(_render(f.path, header, lines) for header, lines in pending)
        all_lines = [ln for _, lines in pending for ln in lines]
        groups.append(
            HunkGroup(
                path=f.path,
                text=f"File: {f.path}\n{body}",
                valid_lines={ln.new_no for ln in all_lines if ln.new_no and ln.kind != "del"},
                added=sum(1 for ln in all_lines if ln.kind == "add"),
                added_text="\n".join(ln.content for ln in all_lines if ln.kind == "add"),
                norm_text="\n".join(normalize_line(ln) for ln in all_lines),
            )
        )
        pending, pending_size = [], 0

    for hunk in f.hunks:
        for i, run in enumerate(_split_hunk(hunk, max_chars)):
            header = hunk.header if i == 0 else f"{hunk.header} (continued)"
            size = sum(len(render_line(ln)) + 1 for ln in run) + len(header)
            if pending and pending_size + size > max_chars:
                flush()
            pending.append((header, run))
            pending_size += size
    flush()
    return groups


def group_files(files: list[FileDiff], max_chars: int) -> list[HunkGroup]:
    return [g for f in files for g in group_file(f, max_chars)]


def select_within_budget(
    groups: list[HunkGroup], budget_chars: int
) -> tuple[list[HunkGroup], list[HunkGroup]]:
    """Partial-review mode: keep the smallest groups first so more files get reviewed."""
    chosen: list[HunkGroup] = []
    dropped: list[HunkGroup] = []
    used = 0
    for g in sorted(groups, key=lambda g: g.chars):
        if used + g.chars <= budget_chars:
            chosen.append(g)
            used += g.chars
        else:
            dropped.append(g)
    order = {id(g): i for i, g in enumerate(groups)}
    chosen.sort(key=lambda g: order[id(g)])
    return chosen, dropped
