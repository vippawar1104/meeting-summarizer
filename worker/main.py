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
from app.rag.embeddings import build_embedder
from app.rag.indexer import RepoIndexer, purge_expired
from app.rag.pg_store import PgChunkStore
from app.rag.rerank import LLMReranker, NoopReranker
from app.rag.retrieval import Retriever
from worker.dispatch import dispatch
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
    store: PgChunkStore,
) -> Handler:
    async def purge(job: Job) -> None:
        if job.repo_full_name == "*":
            await store.delete_installation(job.installation_id)
        else:
            await store.delete_repo(job.installation_id, job.repo_full_name)

    handlers: dict[str, Handler] = {"review": review_stub, "index": review_stub, "purge": purge}
    if not (settings.github_app_id and settings.github_private_key):
        return dispatch(handlers)

    auth = GitHubAppAuth(
        settings.github_app_id,
        settings.github_private_key,
        http,
        redis,
        api_url=settings.github_api_url,
    )
    github = GitHubClient(auth, http, api_url=settings.github_api_url)
    embedder = build_embedder(settings, http)
    indexer = RepoIndexer(github, embedder, store, settings)

    async def index(job: Job) -> None:
        sha = None if job.head_sha == "HEAD" else job.head_sha
        await indexer.index_repo(job.installation_id, job.repo_full_name, sha)

    handlers["index"] = index

    router = build_router(settings, http)
    if router.providers:
        retriever = None
        if settings.rag_enabled:
            reranker = LLMReranker(router) if settings.rerank_enabled else NoopReranker()
            retriever = Retriever(embedder, store, reranker, settings)
        pipeline = ReviewPipeline(github, router, settings, sm, retriever)

        async def review(job: Job) -> None:
            await pipeline.run(job)

        handlers["review"] = review
    return dispatch(handlers)


async def retention_loop(stop: asyncio.Event, store: PgChunkStore, settings: Settings) -> None:
    while not stop.is_set():
        try:
            await purge_expired(store, settings.retention_days)
        except Exception:
            log.exception("retention_purge_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=6 * 3600)
        except TimeoutError:
            pass


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
    store = PgChunkStore(sm)
    handler = build_handler(settings, sm, redis, http, store)
    worker = Worker(sessionmaker=sm, queue=queue, handler=handler, settings=settings)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    loop_task = asyncio.create_task(worker.run())
    rec_task = asyncio.create_task(reconcile_loop(stop, sm, queue, settings))
    ret_task = asyncio.create_task(retention_loop(stop, store, settings))
    log.info("worker_started", concurrency=settings.worker_concurrency)
    await stop.wait()
    log.info("worker_stopping")
    await worker.shutdown(settings.shutdown_grace_s)
    await loop_task
    await rec_task
    await ret_task
    await http.aclose()
    await redis.aclose()
    await engine.dispose()
    log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(run())
