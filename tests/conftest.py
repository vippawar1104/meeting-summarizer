import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from typing import Any

import fakeredis
import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import SQLModel

from app.core.config import Settings
from app.db.session import make_engine, make_sessionmaker
from app.main import create_app

SECRET = "test-secret"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def pr_payload(
    action: str = "opened", sha: str = "abc123", number: int = 7, installation: int = 42
) -> dict[str, Any]:
    return {
        "action": action,
        "installation": {"id": installation},
        "repository": {"full_name": "acme/widgets"},
        "pull_request": {
            "number": number,
            "head": {"sha": sha},
            "additions": 10,
            "deletions": 5,
        },
    }


@pytest.fixture
async def env(tmp_path: Any) -> AsyncIterator[dict[str, Any]]:
    settings = Settings(
        github_webhook_secret=SECRET, database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db"
    )
    engine = make_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    redis = fakeredis.FakeAsyncRedis()
    app = create_app(settings)
    app.state.redis = redis
    app.state.sessionmaker = make_sessionmaker(engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield {
            "client": client,
            "redis": redis,
            "sessionmaker": app.state.sessionmaker,
            "stream": settings.job_stream,
            "app": app,
        }
    await engine.dispose()


async def post_webhook(
    client: AsyncClient,
    payload: dict[str, Any],
    delivery: str = "d-1",
    event: str = "pull_request",
    signature: str | None = None,
) -> Any:
    body = json.dumps(payload).encode()
    return await client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": signature or sign(body),
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "Content-Type": "application/json",
        },
    )
