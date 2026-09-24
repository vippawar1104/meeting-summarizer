import asyncio
import random
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import correlation_id
from app.db.models import Job, JobStatus, _now
from app.queue.errors import PermanentError, SkipJob
from app.queue.redis_queue import Claimed, RedisJobQueue
from app.queue.retry import backoff_seconds

log = structlog.get_logger()

__all__ = ["PermanentError", "SkipJob", "Worker"]

Handler = Callable[[Job], Awaitable[None]]


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
            task.add_done_callback(self._tasks.discard)
            started += 1
        return started

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
            await asyncio.sleep(self._s.poll_interval_s)

    async def shutdown(self, grace_s: float) -> None:
        """Stop claiming, let in-flight jobs finish, requeue whatever is still running."""
        self._stopping = True
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
        try:
            await asyncio.wait_for(self._handler(job), timeout=self._s.job_timeout_s)
        except asyncio.CancelledError:
            await asyncio.shield(self._release(job))
            raise
        except SkipJob as exc:
            await self._finish(job, note=f"skipped: {exc}")
        except PermanentError as exc:
            await self._fail(job, exc, permanent=True)
        except Exception as exc:
            await self._fail(job, exc, permanent=False)
        else:
            await self._finish(job)
        finally:
            heartbeat.cancel()
            correlation_id.reset(token)

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

    async def _fail(self, job: Job, exc: Exception, *, permanent: bool) -> None:
        error = f"{type(exc).__name__}: {exc}"[:500]
        if permanent or job.attempts >= self._s.max_attempts:
            await self._set(job.id, JobStatus.DEAD, error=error)
            await self._q.dead(job.id)
            log.error("job_dead", job_id=job.id, attempts=job.attempts, error=error)
            return
        delay = backoff_seconds(
            job.attempts, self._s.backoff_base_s, self._s.backoff_cap_s, self._rng
        )
        await self._set(job.id, JobStatus.QUEUED, error=error)
        await self._q.retry(job.id, delay)
        log.warning("job_retry", job_id=job.id, attempts=job.attempts, delay_s=round(delay, 1))

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
