"""No-model reviewers. They exist to prove the metrics behave: an empty reviewer must score zero
recall and no false alarms, and a naive regex reviewer gives a floor any real model should beat."""

import json
import re

from app.llm.base import LLMResult, Message

_LINE = re.compile(r"^\s*(\d+) \+ (.*)$")
_FILE = re.compile(r"^File: (.+)$")

_RULES: list[tuple[re.Pattern[str], str, str, str]] = [
    (re.compile(r"^\s*except\s*:"), "bug", "medium", "Bare except swallows every error, including KeyboardInterrupt."),
    (re.compile(r"\beval\(|\bexec\("), "security", "high", "eval/exec on dynamic input can run arbitrary code."),
    (re.compile(r"""(?i)(password|secret|api[_-]?key|token)\s*[:=]\s*["'][^"']{6,}["']"""), "security", "high", "Hardcoded credential."),
    (re.compile(r"(?i)\b(md5|sha1)\("), "security", "medium", "Weak hash function."),
    (re.compile(r"(?i)(execute|query)\(\s*f?[\"'].*(\{|%s|\+)"), "security", "high", "SQL built from string formatting; use parameters."),
    (re.compile(r"\binnerHTML\s*="), "security", "medium", "Assigning innerHTML can enable XSS."),
    (re.compile(r"[!=]=\s*None\b"), "risk", "low", "Compare to None with `is`, not `==`."),
]  # fmt: skip


class NullRouter:
    """Never finds anything."""

    providers: list[object] = [object()]

    def status(self) -> dict[str, str]:
        return {}

    async def complete(self, messages: list[Message], *, json_mode: bool = True) -> LLMResult:
        return LLMResult('{"findings": []}', 0, 0, "baseline", "null", 0.0)


class RegexRouter:
    """Flags a handful of classic anti-patterns on added lines."""

    providers: list[object] = [object()]

    def status(self) -> dict[str, str]:
        return {}

    async def complete(self, messages: list[Message], *, json_mode: bool = True) -> LLMResult:
        findings, current = [], ""
        for raw in messages[-1].content.splitlines():
            if m := _FILE.match(raw):
                current = m[1]
            elif (m := _LINE.match(raw)) and current:
                for pattern, category, severity, text in _RULES:
                    if pattern.search(m[2]):
                        findings.append({
                            "file": current, "line": int(m[1]), "severity": severity,
                            "category": category, "message": text, "confidence": 0.7,
                        })  # fmt: skip
                        break
        return LLMResult(json.dumps({"findings": findings}), 0, 0, "baseline", "regex", 0.0)
