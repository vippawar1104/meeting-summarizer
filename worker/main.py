import asyncio
import signal

import httpx
import structlog
from prometheus_client import start_http_server
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.crypto import DEV_KEY, SecretBox
from app.core.logging import configure_logging
from app.core.metrics import registry
from app.core.pinned_http import make_pinned_client
from app.core.redis import make_redis
from app.core.tracing import configure_tracing
from app.cost.budget import TokenBudget
from app.cost.cache import ReviewCache
from app.cost.guard import GuardedRouter
from app.cost.ratelimit import RateLimiter
from app.db.models import Job
from app.db.session import make_engine, make_sessionmaker
from app.github.auth import GitHubAppAuth
from app.github.client import GitHubClient
from app.llm.byok import ByokResolver
from app.llm.factory import build_router
from app.pipeline.review import ReviewPipeline
from app.queue.redis_queue import RedisJobQueue
from app.rag.embeddings import build_embedder
from app.rag.indexer import RepoIndexer, purge_expired
from app.rag.pg_store import PgChunkStore
from app.rag.rerank import LLMReranker, NoopReranker
from app.rag.retrieval import Retriever
from worker.dispatch import dispatch
from worker.feedback_sync import sync_reactions
from worker.handlers import make_index_handler, make_purge_handler
from worker.reconciler import reconcile
from worker.runner import Handler, Worker

log = structlog.get_logger()


async def review_stub(job: Job) -> None:
    """Used when GitHub or LLM credentials are not configured, so the queue still drains."""
    log.warning("review_stub_no_credentials", job_id=job.id, repo=job.repo_full_name)


def build_github(settings: Settings, redis: Redis, http: httpx.AsyncClient) -> GitHubClient | None:
    if not (settings.github_app_id and settings.github_private_key):
        return None
    auth = GitHubAppAuth(
        settings.github_app_id,
        settings.github_private_key,
        http,
        redis,
        api_url=settings.github_api_url,
    )
    return GitHubClient(auth, http, api_url=settings.github_api_url)


def build_handler(
    settings: Settings,
    sm: async_sessionmaker[AsyncSession],
    redis: Redis,
    http: httpx.AsyncClient,
    store: PgChunkStore,
    github: GitHubClient | None,
) -> Handler:
    cache = ReviewCache(redis, settings.cache_ttl_days) if settings.cache_enabled else None
    handlers: dict[str, Handler] = {
        "review": review_stub,
        "index": review_stub,
        "purge": make_purge_handler(store, cache),
    }
    if github is None:
        return dispatch(handlers)

    embedder = build_embedder(settings, http)
    indexer = RepoIndexer(github, embedder, store, settings)

    handlers["index"] = make_index_handler(indexer)

    # Every LLM call (review, repair, rerank) goes through the guard: redaction, rate limit, budget.
    limiter = RateLimiter(
        redis, per_minute=settings.llm_rate_per_min, burst=settings.llm_rate_burst
    )
    router = GuardedRouter(
        build_router(settings, http),
        budget=TokenBudget(redis, settings.daily_token_budget),
        limiter=limiter,
        redact=settings.redaction_enabled,
        max_wait_s=settings.llm_rate_max_wait_s,
    )
    byok = ByokResolver(
        sm,
        SecretBox(settings.encryption_key or DEV_KEY),
        http,
        limiter,
        allow_http=settings.env == "dev",
        pinned_http=None if settings.env == "dev" else make_pinned_client(),
    )
    retriever = None
    if settings.rag_enabled:
        reranker = LLMReranker(router) if settings.rerank_enabled else NoopReranker()
        retriever = Retriever(embedder, store, reranker, settings)
    pipeline = ReviewPipeline(github, router, settings, sm, retriever, cache, byok.router_for)

    async def review(job: Job) -> None:
        await pipeline.run(job)

    handlers["review"] = review
    return dispatch(handlers)


async def feedback_loop(
    stop: asyncio.Event,
    sm: async_sessionmaker[AsyncSession],
    github: GitHubClient | None,
) -> None:
    while not stop.is_set():
        if github is not None:
            try:
                await sync_reactions(sm, github)
            except Exception:
                log.exception("reaction_sync_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=600)
        except TimeoutError:
            pass


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
    configure_tracing(settings.otlp_endpoint, "reviewly-worker")
    if settings.worker_metrics_port:
        start_http_server(settings.worker_metrics_port, registry=registry)
    engine = make_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    sm = make_sessionmaker(engine)
    redis = make_redis(settings.redis_url)
    queue = RedisJobQueue(redis, prefix=settings.queue_prefix)
    http = httpx.AsyncClient()
    store = PgChunkStore(sm)
    github = build_github(settings, redis, http)
    handler = build_handler(settings, sm, redis, http, store, github)
    worker = Worker(sessionmaker=sm, queue=queue, handler=handler, settings=settings)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    loop_task = asyncio.create_task(worker.run())
    rec_task = asyncio.create_task(reconcile_loop(stop, sm, queue, settings))
    ret_task = asyncio.create_task(retention_loop(stop, store, settings))
    fb_task = asyncio.create_task(feedback_loop(stop, sm, github))
    log.info("worker_started", concurrency=settings.worker_concurrency)
    await stop.wait()
    log.info("worker_stopping")
    await worker.shutdown(settings.shutdown_grace_s)
    await loop_task
    await rec_task
    await ret_task
    await fb_task
    await http.aclose()
    await redis.aclose()
    await engine.dispose()
    log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(run())
