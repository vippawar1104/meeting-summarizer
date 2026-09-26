import time
from collections.abc import Callable

import structlog

from app.core.metrics import BREAKER_OPEN, LLM_CALLS, LLM_COST, LLM_SECONDS, LLM_TOKENS
from app.core.tracing import span
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
        # Two models of the same provider (e.g. two Groq models) must not share one breaker.
        names = [p.name for p in providers]
        self._ids = {
            id(p): p.name if names.count(p.name) == 1 else f"{p.name}:{p.model}" for p in providers
        }
        self.breakers = {
            self._ids[id(p)]: CircuitBreaker(breaker_threshold, breaker_cooldown_s, clock)
            for p in providers
        }

    async def complete(self, messages: list[Message], *, json_mode: bool = True) -> LLMResult:
        errors: list[str] = []
        outage = False
        for provider in self.providers:
            pid = self._ids[id(provider)]
            breaker = self.breakers[pid]
            if not breaker.allow():
                errors.append(f"{pid}: circuit open")
                outage = True
                BREAKER_OPEN.labels(provider=pid).set(1)
                continue
            started = time.monotonic()
            try:
                with span("llm.complete", provider=pid, model=provider.model):
                    result = await provider.complete(messages, json_mode=json_mode)
            except RetryableLLMError as exc:
                outage = True
                LLM_CALLS.labels(provider.name, provider.model, "error").inc()
                LLM_SECONDS.labels(provider.name).observe(time.monotonic() - started)
                breaker.record_failure()
                BREAKER_OPEN.labels(provider=pid).set(0 if breaker.state == "closed" else 1)
                errors.append(f"{pid}: {exc}")
                log.warning("llm_provider_failed", provider=pid, error=str(exc))
                continue
            except BadRequest as exc:
                # The provider is healthy, it just rejected this request; another may accept it.
                LLM_CALLS.labels(provider.name, provider.model, "rejected").inc()
                breaker.record_success()
                errors.append(f"{pid}: {exc}")
                log.warning("llm_request_rejected", provider=pid, error=str(exc))
                continue
            breaker.record_success()
            BREAKER_OPEN.labels(provider=pid).set(0)
            LLM_CALLS.labels(provider.name, provider.model, "ok").inc()
            LLM_SECONDS.labels(provider.name).observe(time.monotonic() - started)
            LLM_TOKENS.labels(provider.name, provider.model, "prompt").inc(result.prompt_tokens)
            LLM_TOKENS.labels(provider.name, provider.model, "completion").inc(
                result.completion_tokens
            )
            LLM_COST.labels(provider.name, provider.model).inc(result.cost_usd)
            return result
        raise AllProvidersFailed(errors or ["no providers configured"], outage=outage)

    def status(self) -> dict[str, str]:
        return {name: b.state for name, b in self.breakers.items()}
