import time
from collections.abc import Callable

import structlog

from app.llm.base import (
    AllProvidersFailed,
    BadRequest,
    LLMProvider,
    LLMResult,
    Message,
    RetryableLLMError,
)
from app.llm.breaker import CircuitBreaker

log = structlog.get_logger()


class LLMRouter:
    """One interface over several providers: ordered fallback, a circuit breaker per provider."""

    def __init__(
        self,
        providers: list[LLMProvider],
        *,
        breaker_threshold: int = 3,
        breaker_cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.providers = providers
        self.breakers = {
            p.name: CircuitBreaker(breaker_threshold, breaker_cooldown_s, clock) for p in providers
        }

    async def complete(self, messages: list[Message], *, json_mode: bool = True) -> LLMResult:
        errors: list[str] = []
        for provider in self.providers:
            breaker = self.breakers[provider.name]
            if not breaker.allow():
                errors.append(f"{provider.name}: circuit open")
                continue
            try:
                result = await provider.complete(messages, json_mode=json_mode)
            except RetryableLLMError as exc:
                breaker.record_failure()
                errors.append(f"{provider.name}: {exc}")
                log.warning("llm_provider_failed", provider=provider.name, error=str(exc))
                continue
            except BadRequest as exc:
                # The provider is healthy, it just rejected this request; another may accept it.
                breaker.record_success()
                errors.append(f"{provider.name}: {exc}")
                log.warning("llm_request_rejected", provider=provider.name, error=str(exc))
                continue
            breaker.record_success()
            return result
        raise AllProvidersFailed(errors or ["no providers configured"])

    def status(self) -> dict[str, str]:
        return {name: b.state for name, b in self.breakers.items()}
