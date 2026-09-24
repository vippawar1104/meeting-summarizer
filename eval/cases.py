"""Labeled evaluation cases.

A case is a diff plus what a correct reviewer should say about it:
  bug          the diff introduces a known bug; `labels` mark where
  clean        real merged change with no known problem; any finding counts as a false alarm
  adversarial  hand-written diff that attacks the reviewer (prompt injection, secrets, hidden text)
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATASET = Path(__file__).parent / "dataset" / "cases"


@dataclass(frozen=True)
class Label:
    file: str
    start: int  # new-file line numbers, inclusive
    end: int
    note: str = ""


@dataclass(frozen=True)
class Allowed:
    """A finding that is expected and must not be counted as a false alarm."""

    file: str
    line: int
    kind: str  # e.g. "injection_note"


@dataclass
class Case:
    id: str
    kind: str
    language: str
    title: str
    diff: str
    labels: list[Label] = field(default_factory=list)
    allowed: list[Allowed] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def load_case(directory: Path) -> Case:
    raw = json.loads((directory / "case.json").read_text())
    return Case(
        id=raw["id"],
        kind=raw["kind"],
        language=raw.get("language", ""),
        title=raw["title"],
        diff=(directory / "diff.patch").read_text(),
        labels=[Label(**lb) for lb in raw.get("labels", [])],
        allowed=[Allowed(**a) for a in raw.get("allowed", [])],
        meta=raw.get("meta", {}),
    )


def load_cases(
    root: Path = DATASET, kinds: set[str] | None = None, limit: int | None = None
) -> list[Case]:
    cases = [load_case(d) for d in sorted(root.iterdir()) if (d / "case.json").is_file()]
    if kinds:
        cases = [c for c in cases if c.kind in kinds]
    return cases[:limit] if limit else cases
