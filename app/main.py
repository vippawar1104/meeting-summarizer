import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from redis.asyncio import Redis

from app.api import health, webhooks
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, correlation_id
from app.db.session import make_engine, make_sessionmaker
from app.queue.redis_queue import RedisJobQueue


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Tests pre-populate app.state; production builds real clients here.
        if not hasattr(app.state, "redis"):
            app.state.redis = Redis.from_url(settings.redis_url)
        if not hasattr(app.state, "sessionmaker"):
            app.state.engine = make_engine(settings.database_url)
            app.state.sessionmaker = make_sessionmaker(app.state.engine)
        if not hasattr(app.state, "queue"):
            app.state.queue = RedisJobQueue(app.state.redis, prefix=settings.queue_prefix)
        yield
        await app.state.redis.aclose()
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
    return app


app = create_app()
