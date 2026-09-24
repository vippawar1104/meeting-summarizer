"""Check the dataset is well-formed. Exits non-zero on any problem.

python -m eval.validate
"""

import re
import sys
from collections import Counter

from app.github.diff import parse_diff
from eval.cases import Case, load_cases

MIN_TOTAL, MIN_BUG, MIN_CLEAN, MIN_ADVERSARIAL = 50, 30, 10, 5
LEAKY_TITLE = re.compile(r"(?i)\b(fix|fixes|fixed|bug|crash|regression|error|hotfix|resolve)\b")
REQUIRED_META = {
    "bug": ("repo", "license", "commit", "url"),
    "clean": ("repo", "license", "commit", "url"),
    "adversarial": ("source", "license"),
}


def problems(case: Case) -> list[str]:
    out: list[str] = []
    files = {f.path: f for f in parse_diff(case.diff)}
    if not files:
        return [f"{case.id}: diff does not parse into any file"]
    if case.kind not in REQUIRED_META:
        out.append(f"{case.id}: unknown kind {case.kind!r}")
    for key in REQUIRED_META.get(case.kind, ()):
        if not case.meta.get(key):
            out.append(f"{case.id}: meta.{key} is missing")
    if case.kind == "bug":
        if not case.labels:
            out.append(f"{case.id}: bug case has no labels")
        if LEAKY_TITLE.search(case.title):
            out.append(f"{case.id}: title {case.title!r} gives the bug away")
    if case.kind == "clean" and case.labels:
        out.append(f"{case.id}: clean case has labels")
    for lb in case.labels:
        f = files.get(lb.file)
        if f is None:
            out.append(f"{case.id}: label file {lb.file} is not in the diff")
        elif lb.start > lb.end or not ({lb.start, lb.end} & f.commentable_lines):
            out.append(
                f"{case.id}: label {lb.file}:{lb.start}-{lb.end} is not on a commentable line"
            )
    for a in case.allowed:
        f = files.get(a.file)
        if f is None or a.line not in f.commentable_lines:
            out.append(f"{case.id}: allowed finding {a.file}:{a.line} is not on a commentable line")
    return out


def main() -> int:
    cases = load_cases()
    errors: list[str] = []
    ids = Counter(c.id for c in cases)
    errors += [f"duplicate case id {i}" for i, n in ids.items() if n > 1]
    for c in cases:
        errors += problems(c)
    kinds = Counter(c.kind for c in cases)
    for label, have, need in (
        ("cases", len(cases), MIN_TOTAL),
        ("bug cases", kinds["bug"], MIN_BUG),
        ("clean cases", kinds["clean"], MIN_CLEAN),
        ("adversarial cases", kinds["adversarial"], MIN_ADVERSARIAL),
    ):
        if have < need:
            errors.append(f"only {have} {label}, need at least {need}")
    langs = Counter(c.language for c in cases)
    print(f"{len(cases)} cases: {dict(kinds)}  languages: {dict(langs)}")
    for e in errors:
        print("ERROR", e)
    print("dataset OK" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
