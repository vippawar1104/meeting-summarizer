import asyncio
import random
import time
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import correlation_id
from app.core.metrics import JOB_QUEUE_SECONDS, JOB_SECONDS, JOBS
from app.core.tracing import span
from app.db.models import Job, JobStatus, _now
from app.llm.base import AllProvidersFailed
from app.queue.errors import PermanentError, SkipJob
from app.queue.redis_queue import Claimed, RedisJobQueue
from app.queue.retry import backoff_seconds

log = structlog.get_logger()

__all__ = ["PermanentError", "SkipJob", "Worker"]

Handler = Callable[[Job], Awaitable[None]]


def _age_s(job: Job) -> float:
    """Seconds since the job was created (the DB may hand back a naive timestamp)."""
    created, now = job.created_at, _now()
    if created.tzinfo is None and now.tzinfo is not None:
        created = created.replace(tzinfo=now.tzinfo)
    return max(0.0, (now - created).total_seconds())


def _observe_queue_wait(job: Job) -> None:
    """Metrics must never fail a job, so an unmeasurable age is swallowed."""
    try:
        JOB_QUEUE_SECONDS.observe(_age_s(job))
    except Exception:
        log.debug("queue_wait_unmeasurable", job_id=job.id)


class Worker:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        queue: RedisJobQueue,
        handler: Handler,
        settings: Settings,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._sm = sessionmaker
        self._q = queue
        self._handler = handler
        self._s = settings
        self._rng = rng
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopping = False
        self._slot_freed = asyncio.Event()

    # ---- scheduling -------------------------------------------------------------------------

    async def tick(self) -> int:
        """One scheduling pass: maintenance, then fill free slots. Returns jobs started."""
        await self._q.promote()
        for job_id in await self._q.reap():
            await self._after_reap(job_id)
        started = 0
        while not self._stopping and len(self._tasks) < self._s.worker_concurrency:
            claimed = await self._q.claim(
                visibility_ms=self._s.visibility_timeout_s * 1000,
                cap=self._s.per_installation_cap,
            )
            if claimed is None:
                break
            task = asyncio.create_task(self._process(claimed))
            self._tasks.add(task)
            task.add_done_callback(self._on_task_done)
            started += 1
        return started

    def _on_task_done(self, task: "asyncio.Task[None]") -> None:
        self._tasks.discard(task)
        self._slot_freed.set()  # wake the loop now instead of at the next poll

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def run(self) -> None:
        while not self._stopping:
            try:
                await self.tick()
            except Exception:
                # Redis/DB blip: keep the worker alive; jobs are safe in Postgres + visibility.
                log.exception("tick_failed")
            # Poll for new work, but start the next job the moment a slot frees up.
            self._slot_freed.clear()
            try:
                await asyncio.wait_for(self._slot_freed.wait(), timeout=self._s.poll_interval_s)
            except TimeoutError:
                pass

    async def shutdown(self, grace_s: float) -> None:
        """Stop claiming, let in-flight jobs finish, requeue whatever is still running."""
        self._stopping = True
        self._slot_freed.set()  # let run() notice now instead of after its poll interval
        if not self._tasks:
            return
        _, pending = await asyncio.wait(list(self._tasks), timeout=grace_s)
        for task in pending:
            task.cancel()  # _process releases the job back to the queue on cancellation
        await asyncio.gather(*pending, return_exceptions=True)

    # ---- one job ----------------------------------------------------------------------------

    async def _process(self, claimed: Claimed) -> None:
        job = await self._start(claimed.job_id)
        if job is None:
            return
        token = correlation_id.set(job.correlation_id)
        heartbeat = asyncio.create_task(self._heartbeat(job.id))
        started = time.monotonic()
        if job.attempts == 1:  # a retry's wait is backoff, not queueing
            _observe_queue_wait(job)
        outcome = "done"
        try:
            with span("job", kind=job.kind, job_id=job.id, repo=job.repo_full_name):
                await asyncio.wait_for(self._handler(job), timeout=self._s.job_timeout_s)
        except asyncio.CancelledError:
            outcome = "released"
            await asyncio.shield(self._release(job))
            raise
        except SkipJob as exc:
            outcome = "skipped"
            await self._finish(job, note=f"skipped: {exc}")
        except PermanentError as exc:
            outcome = "dead"
            await self._fail(job, exc, permanent=True)
        except Exception as exc:
            outcome = await self._fail(job, exc, permanent=False)
        else:
            await self._finish(job)
        finally:
            heartbeat.cancel()
            correlation_id.reset(token)
            JOBS.labels(job.kind, outcome).inc()
            JOB_SECONDS.labels(job.kind).observe(time.monotonic() - started)

    async def _start(self, job_id: str) -> Job | None:
        async with self._sm() as s:
            job = await s.get(Job, job_id)
            if job is None or job.status in (JobStatus.DONE, JobStatus.DEAD):
                # Stale queue entry (e.g. crashed between DB commit and ack): just drop it.
                await self._q.ack(job_id)
                return None
            job.status = JobStatus.RUNNING
            job.attempts += 1
            job.updated_at = _now()
            await s.commit()
            return job

    async def _heartbeat(self, job_id: str) -> None:
        interval = self._s.visibility_timeout_s / 3
        while True:
            await asyncio.sleep(interval)
            await self._q.extend(job_id, self._s.visibility_timeout_s * 1000)

    async def _finish(self, job: Job, note: str | None = None) -> None:
        await self._set(job.id, JobStatus.DONE, error=note)
        await self._q.ack(job.id)
        log.info("job_done", job_id=job.id, attempts=job.attempts)

    async def _fail(self, job: Job, exc: Exception, *, permanent: bool) -> str:
        """Returns the outcome label: `dead`, `retry` or `deferred`."""
        if (
            isinstance(exc, AllProvidersFailed)
            and exc.outage
            and _age_s(job) < self._s.llm_outage_window_s
        ):
            return await self._defer(job, exc)
        error = f"{type(exc).__name__}: {exc}"[:500]
        if permanent or job.attempts >= self._s.max_attempts:
            await self._set(job.id, JobStatus.DEAD, error=error)
            await self._q.dead(job.id)
            log.error("job_dead", job_id=job.id, attempts=job.attempts, error=error)
            return "dead"
        delay = backoff_seconds(
            job.attempts, self._s.backoff_base_s, self._s.backoff_cap_s, self._rng
        )
        await self._set(job.id, JobStatus.QUEUED, error=error)
        await self._q.retry(job.id, delay)
        log.warning(
            "job_retry", job_id=job.id, attempts=job.attempts, delay_s=round(delay, 1), error=error
        )
        return "retry"

    async def _defer(self, job: Job, exc: Exception) -> str:
        """An LLM outage is not this job's fault: wait for the models to come back, keep attempts."""
        delay = self._s.llm_outage_retry_s * (0.5 + self._rng())
        async with self._sm() as s:
            row = await s.get(Job, job.id)
            if row is not None:
                row.status = JobStatus.QUEUED
                row.attempts = max(0, row.attempts - 1)
                row.last_error = f"deferred: {exc}"[:500]
                row.updated_at = _now()
                await s.commit()
        await self._q.retry(job.id, delay)
        log.warning("job_deferred", job_id=job.id, delay_s=round(delay, 1))
        return "deferred"

    async def _release(self, job: Job) -> None:
        async with self._sm() as s:
            row = await s.get(Job, job.id)
            if row is not None:
                row.status = JobStatus.QUEUED
                row.attempts = max(0, row.attempts - 1)  # an interrupted run is not a failure
                row.last_error = "released on shutdown"
                row.updated_at = _now()
                await s.commit()
        await self._q.release(job.id)

    async def _after_reap(self, job_id: str) -> None:
        """A visibility timeout fired: the worker died or hung. Retry, or give up if exhausted."""
        async with self._sm() as s:
            job = await s.get(Job, job_id)
            if job is None or job.status in (JobStatus.DONE, JobStatus.DEAD):
                await self._q.ack(job_id)
                return
            if job.attempts >= self._s.max_attempts:
                job.status = JobStatus.DEAD
                job.last_error = "visibility timeout: attempts exhausted"
                job.updated_at = _now()
                await s.commit()
                await self._q.dead(job_id)
                log.error("job_dead", job_id=job_id, reason="visibility_timeout")
                return
            job.status = JobStatus.QUEUED
            job.last_error = "visibility timeout expired"
            job.updated_at = _now()
            await s.commit()
        log.warning("job_reaped", job_id=job_id)

    async def _set(self, job_id: str, status: JobStatus, *, error: str | None) -> None:
        async with self._sm() as s:
            job = await s.get(Job, job_id)
            if job is not None:
                job.status = status
                job.last_error = error
                job.updated_at = _now()
                await s.commit()
