from collections.abc import Callable
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job, JobStatus, _now
from app.queue.redis_queue import RedisJobQueue

log = structlog.get_logger()

BATCH = 500


async def reconcile(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: RedisJobQueue,
    *,
    grace_s: float,
    now: Callable[[], datetime] = _now,
) -> int:
    """Re-enqueue jobs that Postgres says are pending but Redis no longer tracks.

    Covers Redis restarts/flushes and webhooks whose enqueue failed after the DB commit. Postgres
    is the source of truth; Redis is disposable. Returns the number of jobs re-enqueued.
    """
    cutoff = now() - timedelta(seconds=grace_s)
    async with sessionmaker() as s:
        rows = (
            await s.execute(
                select(Job)
                .where(
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),  # type: ignore[attr-defined]
                    Job.updated_at < cutoff,  # type: ignore[arg-type]
                )
                .limit(BATCH)
            )
        ).scalars()
        recovered = 0
        for job in rows:
            if await queue.is_tracked(job.id):
                continue
            if job.status == JobStatus.RUNNING:
                job.status = JobStatus.QUEUED
                job.last_error = "recovered by reconciler"
            job.updated_at = now()
            await queue.enqueue(job.id, job.installation_id, job.changed_lines)
            recovered += 1
        await s.commit()
    if recovered:
        log.warning("reconciled_jobs", count=recovered)
    return recovered
