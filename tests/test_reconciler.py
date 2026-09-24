from datetime import UTC, datetime, timedelta

from app.db.models import Job, JobStatus
from tests.helpers import add_job, make_settings
from tests.test_worker import drain, load, make_worker, ok
from worker.reconciler import reconcile


def later(seconds=120):
    return lambda: datetime.now(UTC) + timedelta(seconds=seconds)


async def test_redis_flush_loses_no_jobs(wenv):
    """Simulate a Redis restart with no persistence: every queued job is recovered."""
    ids = await add_job(wenv["sm"], wenv["queue"], inst=1, n=6, tag="a")
    ids += await add_job(wenv["sm"], wenv["queue"], inst=2, n=4, tag="b")
    await wenv["redis"].flushall()
    assert (await wenv["queue"].depth())["ready"] == 0

    recovered = await reconcile(wenv["sm"], wenv["queue"], grace_s=60, now=later())
    assert recovered == 10
    await drain(make_worker(wenv, ok, worker_concurrency=4, per_installation_cap=4))
    assert {(await load(wenv, i)).status for i in ids} == {JobStatus.DONE}


async def test_jobs_still_tracked_in_redis_are_left_alone(wenv):
    await add_job(wenv["sm"], wenv["queue"], n=3)
    assert await reconcile(wenv["sm"], wenv["queue"], grace_s=60, now=later()) == 0
    assert (await wenv["queue"].depth())["ready"] == 3


async def test_recent_jobs_are_not_touched_within_grace_period(wenv):
    await add_job(wenv["sm"], None, n=2)  # in DB only, e.g. the webhook is mid-request
    assert await reconcile(wenv["sm"], wenv["queue"], grace_s=60) == 0


async def test_job_committed_but_never_enqueued_is_recovered(wenv):
    [jid] = await add_job(wenv["sm"], None)
    assert await reconcile(wenv["sm"], wenv["queue"], grace_s=60, now=later()) == 1
    assert await wenv["queue"].is_tracked(jid)


async def test_orphaned_running_job_is_reset_and_requeued(wenv):
    [jid] = await add_job(wenv["sm"], None)
    async with wenv["sm"]() as s:
        job = await s.get(Job, jid)
        job.status = JobStatus.RUNNING
        await s.commit()
    await reconcile(wenv["sm"], wenv["queue"], grace_s=60, now=later())
    job = await load(wenv, jid)
    assert job.status == JobStatus.QUEUED and "reconciler" in job.last_error
    await drain(make_worker(wenv, ok))
    assert (await load(wenv, jid)).status == JobStatus.DONE


async def test_finished_jobs_are_never_requeued(wenv):
    ids = await add_job(wenv["sm"], None, n=2)
    async with wenv["sm"]() as s:
        for status, jid in zip((JobStatus.DONE, JobStatus.DEAD), ids, strict=True):
            (await s.get(Job, jid)).status = status
        await s.commit()
    assert await reconcile(wenv["sm"], wenv["queue"], grace_s=60, now=later()) == 0


async def test_reconcile_twice_does_not_duplicate(wenv):
    await add_job(wenv["sm"], None, n=3)
    await reconcile(wenv["sm"], wenv["queue"], grace_s=60, now=later())
    assert await reconcile(wenv["sm"], wenv["queue"], grace_s=60, now=later()) == 0
    assert (await wenv["queue"].depth())["ready"] == 3


async def test_settings_helper_smoke():
    assert make_settings().max_attempts == 3
