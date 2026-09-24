import asyncio

import pytest

from app.core.logging import correlation_id
from app.db.models import Job, JobStatus
from tests.helpers import add_job, make_settings
from worker.runner import PermanentError, Worker


def make_worker(wenv, handler, **settings):
    return Worker(
        sessionmaker=wenv["sm"],
        queue=wenv["queue"],
        handler=handler,
        settings=make_settings(**settings),
        rng=lambda: 0.0,  # deterministic: backoff = ceiling / 2
    )


async def drain(worker, max_ticks=50):
    """Run scheduling passes until nothing is running or claimable."""
    for _ in range(max_ticks):
        started = await worker.tick()
        await worker.wait_idle()
        if started == 0:
            return


async def load(wenv, job_id) -> Job:
    async with wenv["sm"]() as s:
        return await s.get(Job, job_id)


async def ok(job):
    return None


async def test_successful_job_is_marked_done_and_acked(wenv):
    [jid] = await add_job(wenv["sm"], wenv["queue"])
    w = make_worker(wenv, ok)
    await drain(w)
    job = await load(wenv, jid)
    assert job.status == JobStatus.DONE and job.attempts == 1
    assert not await wenv["queue"].is_tracked(jid)


async def test_transient_failures_are_retried_with_backoff_then_succeed(wenv):
    calls = []

    async def flaky(job):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("boom")

    [jid] = await add_job(wenv["sm"], wenv["queue"])
    w = make_worker(wenv, flaky)
    await drain(w)
    job = await load(wenv, jid)
    assert job.status == JobStatus.QUEUED and "boom" in job.last_error  # waiting on backoff
    for _ in range(5):
        wenv["clock"].advance(20)
        await drain(w)
    job = await load(wenv, jid)
    assert job.status == JobStatus.DONE and job.attempts == 3


async def test_retry_is_not_attempted_before_backoff_elapses(wenv):
    calls = []

    async def fail(job):
        calls.append(1)
        raise RuntimeError("x")

    await add_job(wenv["sm"], wenv["queue"])
    w = make_worker(wenv, fail)
    await drain(w)
    wenv["clock"].advance(0.5)  # base 2s, floor 1s: still waiting
    await drain(w)
    assert len(calls) == 1


async def test_exhausted_retries_go_to_dead_letter_queue(wenv):
    async def fail(job):
        raise RuntimeError("always")

    [jid] = await add_job(wenv["sm"], wenv["queue"])
    w = make_worker(wenv, fail, max_attempts=3)
    for _ in range(6):
        wenv["clock"].advance(30)
        await drain(w)
    job = await load(wenv, jid)
    assert job.status == JobStatus.DEAD and job.attempts == 3
    assert "always" in job.last_error
    assert await wenv["queue"].dead_letters() == [jid]


async def test_permanent_error_skips_retries(wenv):
    async def gone(job):
        raise PermanentError("PR deleted")

    [jid] = await add_job(wenv["sm"], wenv["queue"])
    await drain(make_worker(wenv, gone))
    job = await load(wenv, jid)
    assert job.status == JobStatus.DEAD and job.attempts == 1


async def test_handler_timeout_counts_as_a_failure_and_is_retried(wenv):
    async def hang(job):
        await asyncio.sleep(10)

    [jid] = await add_job(wenv["sm"], wenv["queue"])
    await drain(make_worker(wenv, hang, job_timeout_s=0.05))
    job = await load(wenv, jid)
    assert job.status == JobStatus.QUEUED and job.attempts == 1
    assert "TimeoutError" in job.last_error


async def test_poison_job_does_not_block_others(wenv):
    async def handler(job):
        if job.pr_number == 0:
            raise PermanentError("poison")

    ids = await add_job(wenv["sm"], wenv["queue"], n=4)
    await drain(make_worker(wenv, handler))
    statuses = [(await load(wenv, i)).status for i in ids]
    assert statuses.count(JobStatus.DEAD) == 1 and statuses.count(JobStatus.DONE) == 3


async def test_handler_sees_the_jobs_correlation_id(wenv):
    seen = []

    async def handler(job):
        seen.append(correlation_id.get())

    [jid] = await add_job(wenv["sm"], wenv["queue"], inst=5)
    await drain(make_worker(wenv, handler))
    assert seen == [(await load(wenv, jid)).correlation_id]


# ---- fault injection: crashes, hangs, shutdown -----------------------------------------------


async def test_crashed_worker_job_is_reclaimed_after_visibility_timeout(wenv):
    """Worker claims a job then dies without ack or DB update; another worker finishes it."""
    [jid] = await add_job(wenv["sm"], wenv["queue"])
    await wenv["queue"].claim(visibility_ms=30_000, cap=2)  # the "crashed" worker
    async with wenv["sm"]() as s:
        job = await s.get(Job, jid)
        job.status, job.attempts = JobStatus.RUNNING, 1
        await s.commit()
    survivor = make_worker(wenv, ok)
    await drain(survivor)
    assert (await load(wenv, jid)).status == JobStatus.RUNNING  # still within visibility window
    wenv["clock"].advance(31)
    await drain(survivor)
    job = await load(wenv, jid)
    assert job.status == JobStatus.DONE and job.attempts == 2


async def test_reaped_job_with_exhausted_attempts_is_dead_lettered(wenv):
    [jid] = await add_job(wenv["sm"], wenv["queue"])
    await wenv["queue"].claim(visibility_ms=30_000, cap=2)
    async with wenv["sm"]() as s:
        job = await s.get(Job, jid)
        job.status, job.attempts = JobStatus.RUNNING, 3  # already at max_attempts
        await s.commit()
    wenv["clock"].advance(31)
    await drain(make_worker(wenv, ok, max_attempts=3))
    assert (await load(wenv, jid)).status == JobStatus.DEAD
    assert await wenv["queue"].dead_letters() == [jid]


async def test_stale_queue_entry_for_finished_job_is_dropped(wenv):
    calls = []

    async def handler(job):
        calls.append(1)

    [jid] = await add_job(wenv["sm"], wenv["queue"])
    async with wenv["sm"]() as s:
        job = await s.get(Job, jid)
        job.status = JobStatus.DONE
        await s.commit()
    await drain(make_worker(wenv, handler))
    assert calls == [] and not await wenv["queue"].is_tracked(jid)


async def test_queue_entry_without_db_row_is_dropped(wenv):
    await wenv["queue"].enqueue("orphan", 1, 10)
    await drain(make_worker(wenv, ok))
    assert not await wenv["queue"].is_tracked("orphan")


async def test_shutdown_requeues_hung_inflight_job_without_burning_an_attempt(wenv):
    started = asyncio.Event()

    async def hang(job):
        started.set()
        await asyncio.sleep(60)

    [jid] = await add_job(wenv["sm"], wenv["queue"])
    w = make_worker(wenv, hang)
    await w.tick()
    await started.wait()
    await w.shutdown(grace_s=0.05)
    job = await load(wenv, jid)
    assert job.status == JobStatus.QUEUED and job.attempts == 0
    assert (await wenv["queue"].depth())["ready"] == 1
    # and another worker can pick it up
    await drain(make_worker(wenv, ok))
    assert (await load(wenv, jid)).status == JobStatus.DONE


async def test_shutdown_lets_fast_inflight_jobs_finish(wenv):
    async def quick(job):
        await asyncio.sleep(0.02)

    [jid] = await add_job(wenv["sm"], wenv["queue"])
    w = make_worker(wenv, quick)
    await w.tick()
    await w.shutdown(grace_s=2)
    assert (await load(wenv, jid)).status == JobStatus.DONE


async def test_no_new_work_is_claimed_after_shutdown_starts(wenv):
    w = make_worker(wenv, ok)
    await w.shutdown(grace_s=0.01)
    await add_job(wenv["sm"], wenv["queue"])
    assert await w.tick() == 0


async def test_run_loop_survives_a_redis_outage_and_recovers(wenv):
    [jid] = await add_job(wenv["sm"], wenv["queue"])
    queue = wenv["queue"]
    real_claim, state = queue.claim, {"failures": 3}

    async def flaky_claim(**kw):
        if state["failures"] > 0:
            state["failures"] -= 1
            raise ConnectionError("redis unavailable")
        return await real_claim(**kw)

    queue.claim = flaky_claim
    w = make_worker(wenv, ok)
    task = asyncio.create_task(w.run())
    for _ in range(200):
        if (await load(wenv, jid)).status == JobStatus.DONE:
            break
        await asyncio.sleep(0.01)
    await w.shutdown(1)
    await task
    assert (await load(wenv, jid)).status == JobStatus.DONE


# ---- scheduling guarantees under load --------------------------------------------------------


async def test_noisy_installation_does_not_starve_a_quiet_one(wenv):
    order = []

    async def handler(job):
        order.append(job.installation_id)
        await asyncio.sleep(0)

    await add_job(wenv["sm"], wenv["queue"], inst=1, n=20, tag="noisy")
    await add_job(wenv["sm"], wenv["queue"], inst=2, n=2, tag="quiet")
    await drain(make_worker(wenv, handler, worker_concurrency=2))
    assert len(order) == 22
    last_quiet = max(i for i, inst in enumerate(order) if inst == 2)
    assert last_quiet < 6  # served among the first few, not after all 20 noisy jobs


async def test_per_installation_cap_is_never_exceeded(wenv):
    running, peak = 0, 0

    async def handler(job):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1

    await add_job(wenv["sm"], wenv["queue"], inst=1, n=8)
    await drain(make_worker(wenv, handler, worker_concurrency=8, per_installation_cap=2))
    assert peak == 2


async def test_worker_concurrency_limit_is_never_exceeded(wenv):
    running, peak = 0, 0

    async def handler(job):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1

    for inst in range(1, 9):
        await add_job(wenv["sm"], wenv["queue"], inst=inst, tag=str(inst))
    await drain(make_worker(wenv, handler, worker_concurrency=3))
    assert peak == 3


async def test_two_workers_never_process_the_same_job_twice(wenv):
    seen = []

    async def handler(job):
        seen.append(job.id)
        await asyncio.sleep(0.005)

    for inst in range(1, 6):
        await add_job(wenv["sm"], wenv["queue"], inst=inst, n=4, tag=str(inst))
    w1, w2 = make_worker(wenv, handler), make_worker(wenv, handler)
    for _ in range(30):
        await asyncio.gather(w1.tick(), w2.tick())
        await asyncio.gather(w1.wait_idle(), w2.wait_idle())
    assert len(seen) == 20 and len(set(seen)) == 20


@pytest.mark.parametrize("n", [1, 50])
async def test_burst_of_jobs_all_complete(wenv, n):
    ids = await add_job(wenv["sm"], wenv["queue"], inst=1, n=n)
    await drain(make_worker(wenv, ok, worker_concurrency=8, per_installation_cap=8), max_ticks=200)
    statuses = {(await load(wenv, i)).status for i in ids}
    assert statuses == {JobStatus.DONE}
