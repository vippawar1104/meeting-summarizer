import pytest

from app.core.config import Settings
from app.llm.base import (
    AllProvidersFailed,
    BadRequest,
    LLMResult,
    Message,
    ProviderUnavailable,
    RateLimited,
)
from app.llm.breaker import CircuitBreaker
from app.llm.factory import build_router
from app.llm.router import LLMRouter

MSGS = [Message("user", "hi")]


class FakeProvider:
    def __init__(self, name, outcomes=None):
        self.name, self.model, self.calls = name, f"{name}-model", 0
        self.outcomes = list(outcomes or [])  # exceptions are raised, anything else is ignored
        self.seen_json_mode = None

    async def complete(self, messages, *, json_mode=True):
        self.calls += 1
        self.seen_json_mode = json_mode
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
        return LLMResult("ok", 1, 1, self.name, self.model, 0.0)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def down():
    return ProviderUnavailable("down")


# ---- circuit breaker -------------------------------------------------------------------------


def test_breaker_starts_closed_and_allows():
    assert CircuitBreaker().allow()


def test_breaker_opens_after_threshold_consecutive_failures():
    b = CircuitBreaker(threshold=3)
    for _ in range(2):
        b.record_failure()
    assert b.state == "closed"
    b.record_failure()
    assert b.state == "open" and not b.allow()


def test_success_resets_the_failure_count():
    b = CircuitBreaker(threshold=3)
    b.record_failure()
    b.record_failure()
    b.record_success()
    b.record_failure()
    b.record_failure()
    assert b.state == "closed"


def test_open_breaker_blocks_until_cooldown_then_allows_a_single_trial():
    clock = Clock()
    b = CircuitBreaker(threshold=1, cooldown_s=30, clock=clock)
    b.record_failure()
    clock.t = 29
    assert not b.allow()
    clock.t = 31
    assert b.allow() and b.state == "half_open"
    assert not b.allow()  # only one probe at a time


def test_trial_success_closes_and_trial_failure_reopens():
    clock = Clock()
    b = CircuitBreaker(threshold=1, cooldown_s=10, clock=clock)
    b.record_failure()
    clock.t = 11
    assert b.allow()
    b.record_failure()
    assert b.state == "open" and not b.allow()
    clock.t = 22
    assert b.allow()
    b.record_success()
    assert b.state == "closed" and b.allow()


# ---- router ----------------------------------------------------------------------------------


async def test_first_healthy_provider_wins():
    a, b = FakeProvider("a"), FakeProvider("b")
    res = await LLMRouter([a, b]).complete(MSGS)
    assert res.provider == "a" and b.calls == 0


@pytest.mark.parametrize("err", [RateLimited("429"), ProviderUnavailable("500")])
async def test_falls_back_when_primary_fails(err):
    a, b = FakeProvider("a", [err]), FakeProvider("b")
    res = await LLMRouter([a, b]).complete(MSGS)
    assert res.provider == "b" and (a.calls, b.calls) == (1, 1)


async def test_bad_request_falls_through_without_tripping_the_breaker():
    a = FakeProvider("a", [BadRequest("blocked")] * 5)
    b = FakeProvider("b")
    router = LLMRouter([a, b], breaker_threshold=2)
    for _ in range(5):
        assert (await router.complete(MSGS)).provider == "b"
    assert router.status()["a"] == "closed" and a.calls == 5


async def test_breaker_stops_calling_a_dead_provider():
    a, b = FakeProvider("a", [down()] * 10), FakeProvider("b")
    router = LLMRouter([a, b], breaker_threshold=3)
    for _ in range(10):
        await router.complete(MSGS)
    assert a.calls == 3 and b.calls == 10  # after 3 failures a is skipped entirely
    assert router.status() == {"a": "open", "b": "closed"}


async def test_provider_is_retried_after_cooldown_and_recovers():
    clock = Clock()
    a, b = FakeProvider("a", [down(), down()]), FakeProvider("b")
    router = LLMRouter([a, b], breaker_threshold=2, breaker_cooldown_s=30, clock=clock)
    for _ in range(3):
        await router.complete(MSGS)
    assert router.status()["a"] == "open"
    clock.t = 31
    res = await router.complete(MSGS)  # trial call succeeds: a is healthy again
    assert res.provider == "a" and router.status()["a"] == "closed"


async def test_all_providers_failing_raises_with_every_reason():
    router = LLMRouter([FakeProvider("a", [down()]), FakeProvider("b", [RateLimited("slow")])])
    with pytest.raises(AllProvidersFailed) as ei:
        await router.complete(MSGS)
    assert len(ei.value.errors) == 2
    assert "a: down" in str(ei.value) and "b: slow" in str(ei.value)


async def test_no_providers_configured_is_a_clear_error():
    with pytest.raises(AllProvidersFailed, match="no providers"):
        await LLMRouter([]).complete(MSGS)


async def test_json_mode_is_forwarded():
    a = FakeProvider("a")
    await LLMRouter([a]).complete(MSGS, json_mode=False)
    assert a.seen_json_mode is False


async def test_all_open_breakers_fail_fast_without_calling_providers():
    a = FakeProvider("a", [down()] * 3)
    router = LLMRouter([a], breaker_threshold=1)
    with pytest.raises(AllProvidersFailed):
        await router.complete(MSGS)
    with pytest.raises(AllProvidersFailed, match="circuit open"):
        await router.complete(MSGS)
    assert a.calls == 1


# ---- factory ---------------------------------------------------------------------------------


def test_factory_only_builds_providers_that_have_keys_in_configured_order():
    s = Settings(provider_order="mistral, gemini,groq", gemini_api_key="g", mistral_api_key="m")
    assert [p.name for p in build_router(s, http=None).providers] == ["mistral", "gemini"]  # type: ignore[arg-type]


def test_factory_ignores_unknown_names_and_handles_no_keys():
    s = Settings(provider_order="bogus,gemini", gemini_api_key=None)
    assert build_router(s, http=None).providers == []  # type: ignore[arg-type]
