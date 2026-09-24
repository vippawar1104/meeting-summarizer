import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

import structlog

from app.cost.budget import TokenBudget
from app.cost.ratelimit import RateLimiter
from app.llm.base import LLMError, LLMProvider, LLMResult, Message
from app.llm.router import LLMRouter
from app.safety.redact import redact_text

log = structlog.get_logger()

# Set by whoever is doing work for an installation (the review pipeline). Calls made with no
# installation (evals, scripts) are simply not metered.
current_installation: ContextVar[int | None] = ContextVar("current_installation", default=None)

COMPLETION_RESERVE_TOKENS = 1500


class BudgetExceeded(LLMError):
    """The installation's daily token budget is used up. Not retryable today."""


class InstallationRateLimited(LLMError):
    def __init__(self, retry_after_s: float) -> None:
        super().__init__(f"installation rate limit reached; retry in {retry_after_s:.1f}s")
        self.retry_after_s = retry_after_s


def estimate_tokens(messages: list[Message]) -> int:
    """Rough (4 chars/token) prompt size plus room for the answer. Settled to real usage after."""
    return sum(len(m.content) for m in messages) // 4 + COMPLETION_RESERVE_TOKENS


class GuardedRouter:
    """Wraps the LLM router with, in order: secret redaction, rate limit, token budget.

    Redaction here is the last line of defence: whatever the caller built, nothing that looks like
    a secret is sent to a provider.
    """

    def __init__(
        self,
        inner: LLMRouter,
        *,
        budget: TokenBudget | None = None,
        limiter: RateLimiter | None = None,
        redact: bool = True,
        max_wait_s: float = 5.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._inner, self._budget, self._limiter = inner, budget, limiter
        self._redact, self._max_wait_s, self._sleep = redact, max_wait_s, sleep

    @property
    def providers(self) -> list[LLMProvider]:
        return self._inner.providers

    def status(self) -> dict[str, str]:
        return self._inner.status()

    def _clean(self, messages: list[Message]) -> list[Message]:
        if not self._redact:
            return messages
        cleaned, total = [], 0
        for m in messages:
            if m.role == "system":  # our own prompt, not user data
                cleaned.append(m)
                continue
            text, report = redact_text(m.content)
            total += report.total
            cleaned.append(Message(m.role, text))
        if total:
            log.warning("secrets_redacted_before_llm", count=total)
        return cleaned

    async def complete(self, messages: list[Message], *, json_mode: bool = True) -> LLMResult:
        messages = self._clean(messages)
        inst = current_installation.get()
        reserved = 0
        if inst is not None:
            await self._rate_limit(inst)
            if self._budget is not None:
                reserved = estimate_tokens(messages)
                if not await self._budget.reserve(inst, reserved):
                    raise BudgetExceeded(f"daily token budget reached for installation {inst}")
        try:
            result = await self._inner.complete(messages, json_mode=json_mode)
        except BaseException:
            if inst is not None and self._budget is not None:
                await self._budget.settle(inst, reserved, 0)  # nothing was spent
            raise
        if inst is not None and self._budget is not None:
            await self._budget.settle(
                inst, reserved, result.prompt_tokens + result.completion_tokens
            )
        return result

    async def _rate_limit(self, inst: int) -> None:
        if self._limiter is None:
            return
        wait = await self._limiter.acquire(inst)
        if wait <= 0:
            return
        if wait > self._max_wait_s:
            raise InstallationRateLimited(wait)
        await self._sleep(wait)
        wait = await self._limiter.acquire(inst)
        if wait > 0:
            raise InstallationRateLimited(wait)
