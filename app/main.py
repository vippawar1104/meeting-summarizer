import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from app.api import auth, dashboard, health, llm_settings, stripe_webhook, webhooks
from app.core.config import Settings, get_settings, insecure_settings
from app.core.crypto import DEV_KEY, SecretBox
from app.core.logging import configure_logging, correlation_id
from app.db.session import make_engine, make_sessionmaker
from app.queue.redis_queue import RedisJobQueue


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if problems := insecure_settings(settings):
        raise RuntimeError("refusing to start with insecure settings: " + "; ".join(problems))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Tests pre-populate app.state; production builds real clients here.
        if not hasattr(app.state, "redis"):
            app.state.redis = Redis.from_url(settings.redis_url)
        if not hasattr(app.state, "box"):
            app.state.box = SecretBox(settings.encryption_key or DEV_KEY)
        if not hasattr(app.state, "http"):
            app.state.http = httpx.AsyncClient(timeout=30)
        if not hasattr(app.state, "sessionmaker"):
            app.state.engine = make_engine(settings.database_url)
            app.state.sessionmaker = make_sessionmaker(app.state.engine)
        if not hasattr(app.state, "queue"):
            app.state.queue = RedisJobQueue(app.state.redis, prefix=settings.queue_prefix)
        yield
        await app.state.redis.aclose()
        await app.state.http.aclose()
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
        try:
            response = await call_next(request)
        finally:
            correlation_id.reset(token)
        response.headers["x-request-id"] = cid
        return response

    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(stripe_webhook.router)
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(llm_settings.router)

    # The built dashboard (if present) is served from the same origin, last so API routes win.
    dist = Path(__file__).resolve().parent.parent / "dashboard" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    return app


app = create_app()
