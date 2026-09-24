from dataclasses import dataclass, field

import structlog

from app.llm.base import Completer, LLMError, Message
from app.pipeline.findings import Finding, parse_findings
from app.pipeline.prompts import repair_prompt

log = structlog.get_logger()


@dataclass
class GroupResult:
    findings: list[Finding] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    repaired: bool = False
    parse_failed: bool = False
    dropped_invalid: int = 0
    model: str = ""
    context_chunks: int = 0
    cache_hit: bool = False
    raw_text: str = ""  # the reply the findings came from
    clean: bool = False  # parsed with no errors and no repair: safe to cache


async def review_group(router: Completer, system: str, user: str) -> GroupResult:
    """One LLM call, plus at most one repair retry when the reply is not valid findings JSON.

    Provider outages raise (the job retries); malformed output never does, it just yields fewer
    findings, because one bad section must not sink the whole review.
    """
    messages = [Message("system", system), Message("user", user)]
    res = await router.complete(messages)
    out = GroupResult(
        prompt_tokens=res.prompt_tokens,
        completion_tokens=res.completion_tokens,
        cost_usd=res.cost_usd,
        model=f"{res.provider}/{res.model}",
    )
    parsed = parse_findings(res.text)
    if parsed.errors:
        out.repaired = True
        messages += [
            Message("assistant", res.text[:8000]),
            Message("user", repair_prompt(parsed.errors)),
        ]
        try:
            res2 = await router.complete(messages)
        except LLMError as exc:
            log.warning("repair_call_failed", error=str(exc))
        else:
            out.prompt_tokens += res2.prompt_tokens
            out.completion_tokens += res2.completion_tokens
            out.cost_usd += res2.cost_usd
            out.model = f"{res2.provider}/{res2.model}"
            parsed = parse_findings(res2.text)
        if parsed.errors:
            out.dropped_invalid = len(parsed.errors)
            out.parse_failed = not parsed.findings
    out.findings = parsed.findings
    out.raw_text = res2.text if out.repaired and "res2" in locals() else res.text
    out.clean = not out.repaired and not parsed.errors
    return out
