import asyncio
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

from app.cost.budget import TokenBudget
from app.cost.guard import (
    BudgetExceeded,
    GuardedRouter,
    InstallationRateLimited,
    current_installation,
    estimate_tokens,
)
from app.cost.ratelimit import RateLimiter
from app.llm.base import AllProvidersFailed, LLMResult, Message
from tests.helpers import FakeClock
from tests.secrets_fixtures import fake_secrets


def redis():
    return fakeredis.FakeAsyncRedis()


class DayClock:
    def __init__(self):
        self.t = datetime(2026, 5, 1, 12, tzinfo=UTC)

    def __call__(self):
        return self.t


# ---- token budget -----------------------------------------------------------------------------


async def test_reservations_within_the_limit_are_counted():
    b = TokenBudget(redis(), 1000)
    assert await b.reserve(1, 400) and await b.reserve(1, 600)
    assert await b.used(1) == 1000 and await b.remaining(1) == 0


async def test_a_reservation_that_would_exceed_the_limit_is_refused_and_not_counted():
    b = TokenBudget(redis(), 1000)
    await b.reserve(1, 900)
    assert not await b.reserve(1, 200)
    assert await b.used(1) == 900


async def test_zero_limit_means_unlimited_but_usage_is_still_recorded():
    b = TokenBudget(redis(), 0)
    assert await b.reserve(1, 10**9)
    assert await b.used(1) == 10**9 and await b.remaining(1) is None


async def test_settle_replaces_the_estimate_with_actual_usage():
    b = TokenBudget(redis(), 10_000)
    await b.reserve(1, 3000)
    await b.settle(1, reserved=3000, actual=1200)
    assert await b.used(1) == 1200
    await b.reserve(1, 500)
    await b.settle(1, reserved=500, actual=900)  # actual usage can exceed the estimate
    assert await b.used(1) == 2100


async def test_installations_have_independent_budgets():
    b = TokenBudget(redis(), 100)
    await b.reserve(1, 100)
    assert await b.reserve(2, 100) and not await b.reserve(1, 1)


async def test_the_budget_resets_each_utc_day():
    clock = DayClock()
    b = TokenBudget(redis(), 100, clock=clock)
    await b.reserve(1, 100)
    assert not await b.reserve(1, 1)
    clock.t += timedelta(days=1)
    assert await b.reserve(1, 100) and await b.used(1) == 100


async def test_counters_expire_on_their_own():
    r = redis()
    await TokenBudget(r, 100, clock=DayClock()).reserve(1, 10)
    [key] = await r.keys("budget:*")
    assert 0 < await r.ttl(key) <= 3 * 24 * 3600


async def test_concurrent_reservations_can_never_exceed_the_limit():
    b = TokenBudget(redis(), 200)
    results = await asyncio.gather(*(b.reserve(1, 10) for _ in range(50)))
    assert sum(results) == 20 and await b.used(1) == 200


# ---- rate limiter -----------------------------------------------------------------------------


async def test_burst_is_allowed_then_calls_must_wait():
    clock = FakeClock()
    rl = RateLimiter(redis(), per_minute=60, burst=3, clock=clock)
    assert [await rl.acquire(1) for _ in range(3)] == [0, 0, 0]
    wait = await rl.acquire(1)
    assert wait == pytest.approx(1.0, abs=0.01)  # 60/min = 1 token per second


async def test_the_bucket_refills_over_time():
    clock = FakeClock()
    rl = RateLimiter(redis(), per_minute=60, burst=2, clock=clock)
    await rl.acquire(1)
    await rl.acquire(1)
    assert await rl.acquire(1) > 0
    clock.advance(1.5)
    assert await rl.acquire(1) == 0


async def test_a_refused_call_consumes_nothing():
    clock = FakeClock()
    rl = RateLimiter(redis(), per_minute=60, burst=1, clock=clock)
    await rl.acquire(1)
    first = await rl.acquire(1)
    second = await rl.acquire(1)
    assert first == pytest.approx(second, abs=0.01)


async def test_the_bucket_never_holds_more_than_the_burst():
    clock = FakeClock()
    rl = RateLimiter(redis(), per_minute=60, burst=2, clock=clock)
    clock.advance(3600)
    assert [await rl.acquire(1) for _ in range(2)] == [0, 0] and await rl.acquire(1) > 0


async def test_installations_are_limited_independently():
    rl = RateLimiter(redis(), per_minute=60, burst=1, clock=FakeClock())
    await rl.acquire(1)
    assert await rl.acquire(1) > 0 and await rl.acquire(2) == 0


async def test_concurrent_acquires_never_exceed_the_burst():
    rl = RateLimiter(redis(), per_minute=60, burst=10, clock=FakeClock())
    waits = await asyncio.gather(*(rl.acquire(1) for _ in range(30)))
    assert sum(1 for w in waits if w == 0) == 10


# ---- guarded router ---------------------------------------------------------------------------


class FakeRouter:
    def __init__(self, tokens=(100, 20), exc=None):
        self.calls, self.tokens, self.exc = [], tokens, exc
        self.providers = []

    def status(self):
        return {}

    async def complete(self, messages, *, json_mode=True):
        self.calls.append(messages)
        if self.exc:
            raise self.exc
        return LLMResult("ok", *self.tokens, "fake", "m", 0.0)


MSGS = [Message("system", "sys"), Message("user", "hello world " * 40)]


@pytest.fixture(autouse=True)
def _installation():
    token = current_installation.set(7)
    yield
    current_installation.reset(token)


async def test_calls_pass_through_and_settle_to_real_usage():
    r = redis()
    budget = TokenBudget(r, 100_000)
    inner = FakeRouter(tokens=(300, 50))
    res = await GuardedRouter(inner, budget=budget).complete(MSGS)
    assert res.text == "ok" and await budget.used(7) == 350


async def test_an_exhausted_budget_stops_the_call_before_any_provider_is_contacted():
    budget = TokenBudget(redis(), 1000)
    await budget.reserve(7, 1000)
    inner = FakeRouter()
    with pytest.raises(BudgetExceeded):
        await GuardedRouter(inner, budget=budget).complete(MSGS)
    assert inner.calls == []


async def test_a_failed_provider_call_refunds_its_reservation():
    budget = TokenBudget(redis(), 100_000)
    inner = FakeRouter(exc=AllProvidersFailed(["down"]))
    with pytest.raises(AllProvidersFailed):
        await GuardedRouter(inner, budget=budget).complete(MSGS)
    assert await budget.used(7) == 0


async def test_the_budget_is_tracked_per_installation_context():
    budget = TokenBudget(redis(), 100_000)
    g = GuardedRouter(FakeRouter(tokens=(10, 10)), budget=budget)
    await g.complete(MSGS)
    token = current_installation.set(8)
    try:
        await g.complete(MSGS)
    finally:
        current_installation.reset(token)
    assert await budget.used(7) == await budget.used(8) == 20


async def test_no_installation_context_means_no_metering():
    token = current_installation.set(None)
    try:
        budget = TokenBudget(redis(), 1)  # would refuse everything if it were applied
        res = await GuardedRouter(FakeRouter(), budget=budget).complete(MSGS)
    finally:
        current_installation.reset(token)
    assert res.text == "ok"


async def test_rate_limit_waits_briefly_then_proceeds():
    clock, slept = FakeClock(), []

    async def fake_sleep(seconds):
        slept.append(seconds)
        clock.advance(seconds)

    limiter = RateLimiter(redis(), per_minute=60, burst=1, clock=clock)
    g = GuardedRouter(FakeRouter(), limiter=limiter, sleep=fake_sleep)
    await g.complete(MSGS)
    await g.complete(MSGS)
    assert len(slept) == 1 and slept[0] == pytest.approx(1.0, abs=0.01)


async def test_rate_limit_gives_up_when_the_wait_is_too_long():
    limiter = RateLimiter(redis(), per_minute=6, burst=1, clock=FakeClock())  # 10 s per token
    inner = FakeRouter()
    g = GuardedRouter(inner, limiter=limiter, max_wait_s=2)
    await g.complete(MSGS)
    with pytest.raises(InstallationRateLimited) as ei:
        await g.complete(MSGS)
    assert ei.value.retry_after_s == pytest.approx(10, abs=0.1) and len(inner.calls) == 1


async def test_a_rate_limited_call_does_not_reserve_budget():
    budget = TokenBudget(redis(), 100_000)
    limiter = RateLimiter(redis(), per_minute=6, burst=1, clock=FakeClock())
    g = GuardedRouter(FakeRouter(tokens=(10, 10)), budget=budget, limiter=limiter, max_wait_s=0)
    await g.complete(MSGS)
    with pytest.raises(InstallationRateLimited):
        await g.complete(MSGS)
    assert await budget.used(7) == 20


async def test_secrets_are_redacted_from_user_messages_before_reaching_any_provider():
    secret = fake_secrets()["github_token"]
    inner = FakeRouter()
    await GuardedRouter(inner).complete(
        [
            Message("system", "sys"),
            Message("user", f'token = "{secret}"'),
            Message("assistant", f"saw {secret}"),
        ]
    )
    sent = "\n".join(m.content for m in inner.calls[0])
    assert secret not in sent and "[REDACTED:" in sent


async def test_the_system_prompt_is_never_rewritten():
    inner = FakeRouter()
    system = 'Reply with {"findings": []} and password = "not-a-real-one-1234"'
    await GuardedRouter(inner).complete([Message("system", system), Message("user", "x")])
    assert inner.calls[0][0].content == system


async def test_redaction_can_be_disabled_for_offline_tooling():
    secret = fake_secrets()["github_token"]
    inner = FakeRouter()
    await GuardedRouter(inner, redact=False).complete([Message("user", secret)])
    assert inner.calls[0][0].content == secret


def test_token_estimate_grows_with_prompt_size_and_includes_answer_room():
    small, big = (
        estimate_tokens([Message("user", "a" * 40)]),
        estimate_tokens([Message("user", "a" * 4000)]),
    )
    assert big - small == 990 and small >= 1500
