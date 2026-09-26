import hmac
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles

from app.api import auth, config, dashboard, health, llm_settings, setup, stripe_webhook, webhooks
from app.core.config import Settings, get_settings, insecure_settings
from app.core.crypto import DEV_KEY, SecretBox
from app.core.logging import configure_logging, correlation_id
from app.core.metrics import QUEUE_DEPTH, WEBHOOK_SECONDS, render
from app.core.pinned_http import make_pinned_client
from app.core.redis import make_redis
from app.core.security_headers import security_headers
from app.core.tracing import configure_tracing
from app.db.session import make_engine, make_sessionmaker
from app.queue.redis_queue import RedisJobQueue


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings.otlp_endpoint, "reviewly-api")
    if problems := insecure_settings(settings):
        raise RuntimeError("refusing to start with insecure settings: " + "; ".join(problems))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Tests pre-populate app.state; production builds real clients here.
        if not hasattr(app.state, "redis"):
            app.state.redis = make_redis(settings.redis_url)
        if not hasattr(app.state, "box"):
            app.state.box = SecretBox(settings.encryption_key or DEV_KEY)
        if not hasattr(app.state, "http"):
            app.state.http = httpx.AsyncClient(timeout=30)
        if not hasattr(app.state, "pinned_http") and settings.env != "dev":
            app.state.pinned_http = make_pinned_client()
        if not hasattr(app.state, "sessionmaker"):
            app.state.engine = make_engine(
                settings.database_url,
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
            )
            app.state.sessionmaker = make_sessionmaker(app.state.engine)
        if not hasattr(app.state, "queue"):
            app.state.queue = RedisJobQueue(app.state.redis, prefix=settings.queue_prefix)
        yield
        await app.state.redis.aclose()
        await app.state.http.aclose()
        if hasattr(app.state, "pinned_http"):
            await app.state.pinned_http.aclose()
        if hasattr(app.state, "engine"):
            await app.state.engine.dispose()

    app = FastAPI(title="Reviewly", lifespan=lifespan)
    app.state.settings = settings

    @app.middleware("http")
    async def add_correlation_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        cid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = correlation_id.set(cid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            correlation_id.reset(token)
            if request.url.path == "/webhooks/github":
                WEBHOOK_SECONDS.observe(time.perf_counter() - started)
        response.headers["x-request-id"] = cid
        for name, value in security_headers(
            request.url.path, production=settings.env == "prod"
        ).items():
            response.headers.setdefault(name, value)
        return response

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        if settings.metrics_token:
            supplied = request.headers.get("authorization", "")
            if not hmac.compare_digest(supplied, f"Bearer {settings.metrics_token}"):
                raise HTTPException(status_code=401, detail="metrics token required")
        try:  # queue depth is read at scrape time; an unreachable Redis just omits the update
            for state, count in (await request.app.state.queue.depth()).items():
                QUEUE_DEPTH.labels(state=state).set(count)
        except Exception:
            pass
        return Response(render(), media_type="text/plain; version=0.0.4")

    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(stripe_webhook.router)
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(llm_settings.router)
    app.include_router(config.router)
    app.include_router(setup.router)

    # The built dashboard (if present) is served from the same origin, last so API routes win.
    dist = Path(__file__).resolve().parent.parent / "dashboard" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    return app


app = create_app()
