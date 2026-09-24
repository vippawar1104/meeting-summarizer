import asyncio
import signal

import structlog
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.models import Job
from app.db.session import make_engine, make_sessionmaker
from app.queue.redis_queue import RedisJobQueue
from worker.reconciler import reconcile
from worker.runner import Worker

log = structlog.get_logger()


async def review_stub(job: Job) -> None:
    """Placeholder until the review pipeline lands in milestone 3."""
    log.info("review_stub", job_id=job.id, repo=job.repo_full_name, pr=job.pr_number)


async def reconcile_loop(worker_stop: asyncio.Event, sm, queue, settings) -> None:  # type: ignore[no-untyped-def]
    while not worker_stop.is_set():
        try:
            await reconcile(sm, queue, grace_s=settings.reconcile_grace_s)
        except Exception:
            log.exception("reconcile_failed")
        try:
            await asyncio.wait_for(worker_stop.wait(), timeout=settings.reconcile_interval_s)
        except TimeoutError:
            pass


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    redis = Redis.from_url(settings.redis_url)
    queue = RedisJobQueue(redis, prefix=settings.queue_prefix)
    worker = Worker(sessionmaker=sm, queue=queue, handler=review_stub, settings=settings)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    loop_task = asyncio.create_task(worker.run())
    rec_task = asyncio.create_task(reconcile_loop(stop, sm, queue, settings))
    log.info("worker_started", concurrency=settings.worker_concurrency)
    await stop.wait()
    log.info("worker_stopping")
    await worker.shutdown(settings.shutdown_grace_s)
    await loop_task
    await rec_task
    await redis.aclose()
    await engine.dispose()
    log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(run())
