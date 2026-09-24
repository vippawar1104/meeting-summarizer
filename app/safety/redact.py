"""Remove secrets and invisible characters from text before it leaves for an LLM or embedder.

Redaction is line-preserving: a line is never added or removed, so diff line numbers stay exact.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace

from app.github.diff import DiffLine, FileDiff

# Zero-width, bidi-control and Unicode "tag" characters can hide instructions from human reviewers
# while remaining visible to a model, and bidi controls disguise code (Trojan Source).
_HIDDEN = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿\U000e0000-\U000e007f]")


def strip_hidden(text: str) -> tuple[str, int]:
    cleaned, n = _HIDDEN.subn("", text)
    return cleaned, n


_PLACEHOLDER_VALUE = re.compile(
    r"^(\[REDACTED:\w+\]|\$\{?\w+\}?|%\(?\w+\)?s?|<.*>|\{\{.*\}\}|x+|\*+|\.{3,}|none|null|true|false|changeme|"
    r"your[-_ ].*|example.*|env\..*|process\.env.*|os\.environ.*)$",
    re.I,
)

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")),
    ("github_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("stripe_key", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("anthropic_or_openai_key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{32,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    (
        "url_credentials",
        re.compile(r"(?<=://)(?!\[REDACTED:)[^\s/:@'\"{}<>]+:[^\s/@'\"{}<>]+(?=@)"),
    ),
    ("bearer_token", re.compile(r"(?i)(?<=\bbearer )[A-Za-z0-9._~+/=-]{20,}")),
    ("key_material", re.compile(r"^\s*[A-Za-z0-9+/]{60,}={0,2}\s*$")),
]
_PRIVATE_KEY_INLINE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"
)
_PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PRIVATE_KEY_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (\b[A-Za-z0-9_.\-]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key
        |private[_-]?key|credential|auth)[A-Za-z0-9_.\-]*["']?\s*[:=]\s*)
    (["'])([^"'\s]{8,})\2
    """
)
_QUOTED_BLOB = re.compile(r"""(["'])([A-Za-z0-9+/=_\-]{24,})\1""")


def _entropy(s: str) -> float:
    counts = Counter(s)
    return -sum(c / len(s) * math.log2(c / len(s)) for c in counts.values())


def _placeholder(kind: str) -> str:
    return f"[REDACTED:{kind}]"


@dataclass
class RedactionReport:
    by_kind: Counter[str] = field(default_factory=Counter)
    hidden_chars: int = 0

    @property
    def total(self) -> int:
        return sum(self.by_kind.values())

    def merge(self, other: "RedactionReport") -> None:
        self.by_kind.update(other.by_kind)
        self.hidden_chars += other.hidden_chars


class _State:
    """Tracks whether we are inside a multi-line PEM private key."""

    in_private_key = False


def _redact_line(line: str, state: _State, report: RedactionReport) -> str:
    if state.in_private_key:
        if _PRIVATE_KEY_END.search(line):
            state.in_private_key = False
        report.by_kind["private_key"] += 1
        return _placeholder("private_key")
    if _PRIVATE_KEY_INLINE.search(line):
        report.by_kind["private_key"] += 1
        return _PRIVATE_KEY_INLINE.sub(_placeholder("private_key"), line)
    if _PRIVATE_KEY_BEGIN.search(line):
        state.in_private_key = True
        report.by_kind["private_key"] += 1
        return _placeholder("private_key")

    for kind, pattern in _PATTERNS:
        if pattern.search(line):
            report.by_kind[kind] += len(pattern.findall(line))
            line = pattern.sub(_placeholder(kind), line)

    def assignment(m: re.Match[str]) -> str:
        if _PLACEHOLDER_VALUE.match(m[3]):
            return m[0]
        report.by_kind["assigned_secret"] += 1
        return f"{m[1]}{m[2]}{_placeholder('assigned_secret')}{m[2]}"

    line = _ASSIGNMENT.sub(assignment, line)

    def blob(m: re.Match[str]) -> str:
        value = m[2]
        if (
            _entropy(value) >= 4.2
            and any(c.isdigit() for c in value)
            and any(c.isalpha() for c in value)
        ):
            report.by_kind["high_entropy_string"] += 1
            return f"{m[1]}{_placeholder('high_entropy_string')}{m[1]}"
        return m[0]

    return _QUOTED_BLOB.sub(blob, line)


def redact_text(text: str) -> tuple[str, RedactionReport]:
    report = RedactionReport()
    text, report.hidden_chars = strip_hidden(text)
    state = _State()
    lines = [_redact_line(ln, state, report) for ln in text.split("\n")]
    return "\n".join(lines), report


def redact_diff(files: list[FileDiff]) -> RedactionReport:
    """Redact every line of every hunk in place, including removed lines (they leave too)."""
    report = RedactionReport()
    for f in files:
        state = _State()
        for hunk in f.hunks:
            state.in_private_key = False  # hunks are not contiguous
            cleaned: list[DiffLine] = []
            for ln in hunk.lines:
                content, hidden = strip_hidden(ln.content)
                report.hidden_chars += hidden
                cleaned.append(replace(ln, content=_redact_line(content, state, report)))
            hunk.lines = cleaned
    return report
