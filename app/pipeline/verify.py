"""Second pass: a fresh model call re-checks each finding against the diff and drops unsupported ones.

It only ever removes findings, and it fails open: if the check itself cannot be made or parsed, the
findings are kept, because losing a real finding to a verifier outage is worse than posting one
unverified finding. Prompt-injection text in the diff cannot add anything either: the verdicts
are parsed as booleans by index and nothing else from the reply is used.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from app.llm.base import Completer, LLMError, Message
from app.pipeline.findings import Finding, extract_json
from app.pipeline.hunks import HunkGroup
from app.pipeline.prompts import defang

log = structlog.get_logger()

SYSTEM = (Path(__file__).resolve().parent.parent / "prompts" / "verify.txt").read_text()


@dataclass
class VerifyOutcome:
    kept: list[Finding]
    dropped: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    failed: bool = False  # the check could not be made; every finding was kept


def render(group: HunkGroup, findings: list[Finding]) -> str:
    lines = ["<untrusted_diff>", defang(group.text), "</untrusted_diff>", "", "Findings to check:"]
    for i, f in enumerate(findings):
        lines.append(f"[{i}] {f.file}:{f.line} ({f.category.value}) {defang(f.message)}")
        if f.suggested_patch:
            lines.append(f"    suggested replacement: {defang(f.suggested_patch)}")
    lines += ["", "Return the JSON object now."]
    return "\n".join(lines)


def parse_verdicts(text: str, count: int) -> set[int] | None:
    """Indexes to drop, or None if the reply cannot be trusted at all."""
    try:
        obj: Any = extract_json(text)
    except ValueError:
        return None
    items = obj.get("verdicts") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        return None
    drop: set[int] = set()
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        idx, keep = item.get("index"), item.get("keep")
        if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < count:
            continue
        if not isinstance(keep, bool):
            continue  # "maybe", null, "false" (a string): not a clear verdict, so keep
        seen.add(idx)
        if not keep:
            drop.add(idx)
    return drop if seen else None


async def verify_findings(
    router: Completer, group: HunkGroup, findings: list[Finding]
) -> VerifyOutcome:
    if not findings:
        return VerifyOutcome(kept=[])
    try:
        res = await router.complete(
            [Message("system", SYSTEM), Message("user", render(group, findings))]
        )
    except LLMError as exc:
        log.warning("verify_call_failed", error=str(exc))
        return VerifyOutcome(kept=list(findings), failed=True)
    out = VerifyOutcome(
        kept=list(findings),
        prompt_tokens=res.prompt_tokens,
        completion_tokens=res.completion_tokens,
        cost_usd=res.cost_usd,
    )
    drop = parse_verdicts(res.text, len(findings))
    if drop is None:
        log.warning("verify_reply_unusable")
        out.failed = True
        return out
    out.kept = [f for i, f in enumerate(findings) if i not in drop]
    out.dropped = len(drop)
    return out
