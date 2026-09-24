"""Mine candidate cases from cloned repositories.

Bug cases use the "reversed fix" technique: for a commit that fixed a bug, diff the fixed version
against its parent the other way round. That diff *introduces* the bug, so it is what a reviewer
should have caught, and the label is the code the fix had to change. Only source files are kept
(tests, docs and changelogs are dropped), and the fix's own message is never shown to the model.

    python -m eval.mine --repos DIR --out candidates.jsonl
"""

import argparse
import json
import re
import subprocess
from pathlib import Path

from app.github.diff import parse_diff

SOURCE_EXT = (".py", ".js", ".ts", ".go", ".mjs", ".cjs")
NOT_SOURCE = re.compile(
    r"(^|/)(tests?|__tests__|docs?|examples?|scripts|\.github|node_modules)(/|$)|(_test\.go|\.test\.[jt]s|\.spec\.[jt]s|test_[^/]*\.py|conftest\.py)$"
)
FIX = re.compile(r"^(fix|bugfix|hotfix|resolve|correct)\b", re.I)
NOT_A_BUG = re.compile(
    r"(?i)\b(typo|docs?|documentation|readme|changelog|lint|format|style|comment|spelling|whitespace|bump|ci\b|test|coverage|deprecat|type hint|typing|mypy|pyright|flake8|version)\b"
)
CLEAN_SUBJECT = re.compile(
    r"^(add|allow|support|use|make|move|rename|refactor|improve|simplify|extract|implement|update|remove|replace|introduce|enable)\b",
    re.I,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    ).stdout


def source_files(repo: Path, commit: str) -> list[str]:
    names = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).split()
    return [n for n in names if n.endswith(SOURCE_EXT) and not NOT_SOURCE.search(n)]


def reversed_diff(repo: Path, commit: str, files: list[str]) -> str:
    return git(
        repo, "diff", "-U3", "--no-color", "--no-ext-diff", commit, f"{commit}^", "--", *files
    )


def forward_diff(repo: Path, commit: str, files: list[str]) -> str:
    return git(
        repo, "diff", "-U3", "--no-color", "--no-ext-diff", f"{commit}^", commit, "--", *files
    )


def label_runs(diff: str) -> list[dict[str, object]]:
    """Contiguous added lines (the code containing the bug), merged when at most one line apart."""
    labels: list[dict[str, object]] = []
    for f in parse_diff(diff):
        added = sorted(
            ln.new_no for h in f.hunks for ln in h.lines if ln.kind == "add" and ln.new_no
        )
        start = prev = None
        for n in added:
            if start is None:
                start = prev = n
            elif n - prev <= 1:  # type: ignore[operator]
                prev = n
            else:
                labels.append({"file": f.path, "start": start, "end": prev})
                start = prev = n
        if start is not None:
            labels.append({"file": f.path, "start": start, "end": prev})
    return labels


def changed_lines(diff: str) -> int:
    return sum(f.added + f.deleted for f in parse_diff(diff))


def mine_repo(repo: Path, kind: str, limit: int) -> list[dict[str, object]]:
    out = git(repo, "log", "--no-merges", "--format=%H%x1f%s%x1f%as", "--skip=40")
    rows = []
    for line in out.splitlines():
        commit, subject, date = line.split("\x1f")
        want = (
            FIX.match(subject) and not NOT_A_BUG.search(subject)
            if kind == "bug"
            else CLEAN_SUBJECT.match(subject) and not NOT_A_BUG.search(subject)
        )
        if not want:
            continue
        files = source_files(repo, commit)
        if not 1 <= len(files) <= 3:
            continue
        diff = (
            reversed_diff(repo, commit, files)
            if kind == "bug"
            else forward_diff(repo, commit, files)
        )
        size = changed_lines(diff)
        if not (6 <= size <= 70):
            continue
        labels = label_runs(diff) if kind == "bug" else []
        if kind == "bug" and not 1 <= len(labels) <= 4:
            continue
        if kind == "clean" and git(repo, "log", "--oneline", f"--grep={commit[:8]}").strip():
            continue  # a later commit refers to it (revert or follow-up fix): not "clean"
        rows.append({"repo": repo.name, "kind": kind, "commit": commit, "subject": subject,
                     "date": date, "files": files, "size": size, "labels": labels})  # fmt: skip
        if len(rows) >= limit:
            break
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--per-repo", type=int, default=25)
    args = ap.parse_args()
    with args.out.open("w") as fh:
        for repo in sorted(p for p in args.repos.iterdir() if (p / ".git").exists()):
            for kind in ("bug", "clean"):
                for row in mine_repo(repo, kind, args.per_repo):
                    fh.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
