import asyncio
import signal

import httpx
import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.models import Job
from app.db.session import make_engine, make_sessionmaker
from app.github.auth import GitHubAppAuth
from app.github.client import GitHubClient
from app.llm.factory import build_router
from app.pipeline.review import ReviewPipeline
from app.queue.redis_queue import RedisJobQueue
from worker.reconciler import reconcile
from worker.runner import Handler, Worker

log = structlog.get_logger()


async def review_stub(job: Job) -> None:
    """Used when GitHub or LLM credentials are not configured, so the queue still drains."""
    log.warning("review_stub_no_credentials", job_id=job.id, repo=job.repo_full_name)


def build_handler(
    settings: Settings,
    sm: async_sessionmaker[AsyncSession],
    redis: Redis,
    http: httpx.AsyncClient,
) -> Handler:
    if not (settings.github_app_id and settings.github_private_key):
        return review_stub
    router = build_router(settings, http)
    if not router.providers:
        return review_stub
    auth = GitHubAppAuth(
        settings.github_app_id,
        settings.github_private_key,
        http,
        redis,
        api_url=settings.github_api_url,
    )
    github = GitHubClient(auth, http, api_url=settings.github_api_url)
    pipeline = ReviewPipeline(github, router, settings, sm)

    async def handler(job: Job) -> None:
        await pipeline.run(job)

    return handler


async def reconcile_loop(
    worker_stop: asyncio.Event,
    sm: async_sessionmaker[AsyncSession],
    queue: RedisJobQueue,
    settings: Settings,
) -> None:
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
    http = httpx.AsyncClient()
    handler = build_handler(settings, sm, redis, http)
    worker = Worker(sessionmaker=sm, queue=queue, handler=handler, settings=settings)

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
    await http.aclose()
    await redis.aclose()
    await engine.dispose()
    log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(run())
