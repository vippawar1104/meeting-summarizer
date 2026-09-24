import re
from dataclasses import dataclass

from app.pipeline.findings import SEVERITY_WEIGHT, Finding

_WORD = re.compile(r"[a-z0-9_]{3,}")


@dataclass
class MergeStats:
    unknown_file: int = 0
    line_not_in_diff: int = 0
    below_confidence: int = 0
    duplicates: int = 0
    over_cap: int = 0


def normalize_path(path: str, known: dict[str, set[int]]) -> str | None:
    p = path.strip()
    for candidate in (p, p.removeprefix("./"), p.removeprefix("b/"), p.removeprefix("a/")):
        if candidate in known:
            return candidate
    return None


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _similar(a: Finding, b: Finding) -> bool:
    if a.file != b.file:
        return False
    if a.line == b.line and a.category == b.category:
        return True
    if abs(a.line - b.line) > 2:
        return False
    ta, tb = _tokens(a.message), _tokens(b.message)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.6


def _strength(f: Finding) -> tuple[int, float]:
    return (SEVERITY_WEIGHT[f.severity], f.confidence)


def _better(a: Finding, b: Finding) -> Finding:
    return a if _strength(a) >= _strength(b) else b


def rank_key(f: Finding) -> tuple[int, float, str, int]:
    return (-SEVERITY_WEIGHT[f.severity], -f.confidence, f.file, f.line)


def merge_findings(
    findings: list[Finding],
    *,
    valid_lines: dict[str, set[int]],
    min_confidence: float,
    max_comments: int,
) -> tuple[list[Finding], MergeStats]:
    """Verify every finding points at a real diff line, then filter, dedupe, rank and cap."""
    stats = MergeStats()
    verified: list[Finding] = []
    for f in findings:
        path = normalize_path(f.file, valid_lines)
        if path is None:
            stats.unknown_file += 1
        elif f.line not in valid_lines[path]:
            stats.line_not_in_diff += 1  # never post a comment on a line GitHub has no diff for
        elif f.confidence < min_confidence:
            stats.below_confidence += 1
        else:
            verified.append(f.model_copy(update={"file": path}))

    unique: list[Finding] = []
    for f in sorted(verified, key=rank_key):
        for i, kept in enumerate(unique):
            if _similar(f, kept):
                unique[i] = _better(kept, f)
                stats.duplicates += 1
                break
        else:
            unique.append(f)

    unique.sort(key=rank_key)
    stats.over_cap = max(0, len(unique) - max_comments)
    return unique[:max_comments], stats
