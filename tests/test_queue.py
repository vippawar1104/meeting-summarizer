import asyncio

import fakeredis

from app.queue.redis_queue import RedisJobQueue

VIS = 30_000
BIG = 100


async def claim(q, cap=BIG):
    return await q.claim(visibility_ms=VIS, cap=cap)


async def test_enqueue_is_idempotent(queue):
    assert await queue.enqueue("j1", 1, 10) is True
    assert await queue.enqueue("j1", 1, 10) is False
    assert (await queue.depth())["ready"] == 1


async def test_claim_on_empty_queue_returns_none(queue):
    assert await claim(queue) is None


async def test_claim_returns_job_and_installation(queue):
    await queue.enqueue("j1", 7, 10)
    got = await claim(queue)
    assert (got.job_id, got.installation_id) == ("j1", 7)
    d = await queue.depth()
    assert d["ready"] == 0 and d["inflight"] == 1


async def test_claimed_job_is_not_claimed_twice(queue):
    await queue.enqueue("j1", 1, 10)
    await queue.enqueue("j2", 1, 10)
    a, b = await claim(queue), await claim(queue)
    assert {a.job_id, b.job_id} == {"j1", "j2"}
    assert await claim(queue) is None


async def test_small_pr_runs_before_large_in_same_installation(queue):
    await queue.enqueue("big", 1, 1500)
    await queue.enqueue("small", 1, 5)
    assert (await claim(queue)).job_id == "small"


async def test_large_pr_cannot_be_starved_by_newer_small_ones(queue, clock):
    await queue.enqueue("huge", 1, 1_000_000)  # penalty is capped, not proportional
    clock.advance(150)  # longer than the 100 s maximum penalty
    await queue.enqueue("small", 1, 1)
    assert (await claim(queue)).job_id == "huge"


async def test_quiet_installation_is_served_after_one_noisy_job(queue):
    for i in range(10):
        await queue.enqueue(f"a{i}", 1, 10)
    await queue.enqueue("b0", 2, 10)
    first_two = [(await claim(queue)).installation_id for _ in range(2)]
    assert set(first_two) == {1, 2}


async def test_round_robin_across_installations(queue):
    for inst in (1, 2, 3):
        for i in range(3):
            await queue.enqueue(f"{inst}-{i}", inst, 10)
    order = [(await claim(queue)).installation_id for _ in range(9)]
    for start in (0, 3, 6):
        assert set(order[start : start + 3]) == {1, 2, 3}


async def test_concurrency_cap_blocks_noisy_installation(queue):
    for i in range(3):
        await queue.enqueue(f"a{i}", 1, 10)
    await queue.enqueue("b0", 2, 10)
    got = [await claim(queue, cap=2) for _ in range(4)]
    assert [g.installation_id if g else None for g in got].count(1) == 2
    assert got[3] is None
    assert any(g.installation_id == 2 for g in got if g)


async def test_ack_frees_capacity(queue):
    for i in range(3):
        await queue.enqueue(f"a{i}", 1, 10)
    first = await claim(queue, cap=2)
    await claim(queue, cap=2)
    assert await claim(queue, cap=2) is None
    await queue.ack(first.job_id)
    assert (await claim(queue, cap=2)) is not None


async def test_ack_unknown_and_double_ack_are_safe(queue):
    await queue.ack("nope")
    await queue.enqueue("j1", 1, 10)
    await claim(queue)
    await queue.ack("j1")
    await queue.ack("j1")
    assert not await queue.is_tracked("j1")


async def test_retry_is_hidden_until_backoff_elapses(queue, clock):
    await queue.enqueue("j1", 1, 10)
    await claim(queue)
    await queue.retry("j1", 10)
    assert await queue.promote() == 0
    assert await claim(queue) is None
    clock.advance(11)
    assert await queue.promote() == 1
    assert (await claim(queue)).job_id == "j1"


async def test_promote_moves_only_due_jobs(queue, clock):
    for j in ("soon", "later"):
        await queue.enqueue(j, 1, 10)
        await claim(queue)
    await queue.retry("soon", 5)
    await queue.retry("later", 50)
    clock.advance(6)
    assert await queue.promote() == 1
    d = await queue.depth()
    assert d["ready"] == 1 and d["delayed"] == 1


async def test_retry_on_unknown_job_is_a_noop(queue):
    await queue.retry("ghost", 1)
    assert (await queue.depth())["delayed"] == 0


async def test_expired_visibility_requeues_job(queue, clock):
    await queue.enqueue("j1", 1, 10)
    await claim(queue)
    clock.advance(31)
    assert await queue.reap() == ["j1"]
    assert (await claim(queue)).job_id == "j1"


async def test_unexpired_job_is_not_reaped(queue, clock):
    await queue.enqueue("j1", 1, 10)
    await claim(queue)
    clock.advance(10)
    assert await queue.reap() == []
    assert (await queue.depth())["inflight"] == 1


async def test_extend_pushes_deadline_out(queue, clock):
    await queue.enqueue("j1", 1, 10)
    await claim(queue)
    clock.advance(20)
    assert await queue.extend("j1", VIS)
    clock.advance(20)
    assert await queue.reap() == []


async def test_extend_on_unknown_job_returns_false(queue):
    assert await queue.extend("ghost", VIS) is False


async def test_reap_frees_installation_capacity(queue, clock):
    for i in range(2):
        await queue.enqueue(f"a{i}", 1, 10)
    await claim(queue, cap=1)
    assert await claim(queue, cap=1) is None
    clock.advance(31)
    await queue.reap()
    assert await claim(queue, cap=1) is not None


async def test_release_requeues_immediately(queue):
    await queue.enqueue("j1", 1, 10)
    await claim(queue)
    await queue.release("j1")
    d = await queue.depth()
    assert d["ready"] == 1 and d["inflight"] == 0
    assert (await claim(queue)).job_id == "j1"


async def test_dead_goes_to_dlq_and_is_untracked(queue):
    await queue.enqueue("j1", 1, 10)
    await claim(queue)
    await queue.dead("j1")
    assert await queue.dead_letters() == ["j1"]
    assert not await queue.is_tracked("j1")
    assert await claim(queue) is None


async def test_dead_removes_a_job_waiting_in_retry(queue):
    await queue.enqueue("j1", 1, 10)
    await claim(queue)
    await queue.retry("j1", 30)
    await queue.dead("j1")
    assert (await queue.depth())["delayed"] == 0


async def test_is_tracked_across_lifecycle(queue):
    assert not await queue.is_tracked("j1")
    await queue.enqueue("j1", 1, 10)
    assert await queue.is_tracked("j1")
    await claim(queue)
    assert await queue.is_tracked("j1")
    await queue.retry("j1", 5)
    assert await queue.is_tracked("j1")
    await queue.ack("j1")
    assert not await queue.is_tracked("j1")


async def test_reenqueue_allowed_after_ack(queue):
    await queue.enqueue("j1", 1, 10)
    await claim(queue)
    await queue.ack("j1")
    assert await queue.enqueue("j1", 1, 10) is True


async def test_concurrent_claims_never_return_the_same_job(queue):
    for i in range(10):
        await queue.enqueue(f"j{i}", i, 10)
    results = await asyncio.gather(*[claim(queue) for _ in range(25)])
    got = [r.job_id for r in results if r]
    assert len(got) == 10 and len(set(got)) == 10


async def test_queues_with_different_prefixes_are_isolated(clock):
    redis = fakeredis.FakeAsyncRedis()
    a, b = (
        RedisJobQueue(redis, prefix="a:", clock=clock),
        RedisJobQueue(redis, prefix="b:", clock=clock),
    )
    await a.enqueue("j1", 1, 10)
    assert await claim(b) is None
    assert (await claim(a)).job_id == "j1"


async def test_depth_reports_all_states(queue):
    for i in range(4):
        await queue.enqueue(f"j{i}", i, 10)
    await claim(queue)
    await claim(queue)
    third = await claim(queue)
    await queue.retry(third.job_id, 5)
    d = await queue.depth()
    assert d == {"ready": 1, "delayed": 1, "inflight": 2, "dead": 0}


async def test_reaped_job_leaves_inflight_and_is_reaped_only_once(queue, clock):
    await queue.enqueue("j1", 1, 10)
    await claim(queue)
    clock.advance(31)
    assert await queue.reap() == ["j1"]
    assert (await queue.depth())["inflight"] == 0
    clock.advance(31)
    assert await queue.reap() == []
