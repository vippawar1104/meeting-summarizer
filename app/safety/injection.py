"""Heuristics that flag text aimed at an AI reviewer. Detection never changes what the model is
told to do (the prompt already treats all of this as data); it exists so attempts are visible."""

import re
from dataclasses import dataclass

from app.github.diff import FileDiff

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore-instructions", re.compile(r"(?i)\b(?:ignore|disregard|forget|override)\b[^.\n]{0,30}\b(?:previous|prior|above|earlier|all|any|your|the)\b[^.\n]{0,20}\b(?:instructions?|rules?|prompts?|guidelines?)\b")),
    ("approve-request", re.compile(r"(?i)\b(?:approve|rubber[- ]?stamp)\b[^.\n]{0,25}\b(?:this|the)\b[^.\n]{0,15}\b(?:pr|pull request|change|commit|diff|code)\b")),
    ("suppress-findings", re.compile(r"(?i)\b(?:do not|don't|never|no need to|skip)\s+(?:report|flag|mention|raise|comment on)\s+(?:this|these|any|the|anything|it|those)\s+(?:issues?|findings?|bugs?|vulnerabilit\w+|problems?|warnings?)")),
    ("prompt-exfiltration", re.compile(r"(?i)\b(?:reveal|print|show|repeat|output|leak)\b[^.\n]{0,25}\b(?:system prompt|your prompt|your instructions|hidden instructions)\b")),
    ("role-hijack", re.compile(r"(?i)\b(?:you are now|pretend (?:to be|you are)|new instructions?:|act as (?:an?|the) (?:ai|assistant|reviewer|different|unrestricted|helpful))")),
    ("addresses-ai-reviewer", re.compile(r"(?i)\b(?:ai|llm|language model|automated)\s+(?:code\s+)?(?:reviewer|review bot|assistant|agent)\b[^.\n]{0,40}\b(?:must|should|please|need to|has to)\b")),
    ("delimiter-forgery", re.compile(r"(?i)</?untrusted[_-]")),
]  # fmt: skip


@dataclass(frozen=True)
class InjectionSignal:
    label: str
    file: str | None
    line: int | None  # new-file line number, when it came from the diff


def detect(text: str) -> list[str]:
    return sorted({label for label, pat in _PATTERNS if pat.search(text)})


def scan_diff(files: list[FileDiff]) -> list[InjectionSignal]:
    """Only added lines: an attacker can only introduce text through what they add."""
    found: list[InjectionSignal] = []
    for f in files:
        for h in f.hunks:
            for ln in h.lines:
                if ln.kind == "add" and ln.new_no is not None:
                    found += [InjectionSignal(lbl, f.path, ln.new_no) for lbl in detect(ln.content)]
    return found
