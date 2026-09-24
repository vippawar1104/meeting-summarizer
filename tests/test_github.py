import asyncio
import json
from datetime import UTC, datetime, timedelta

import fakeredis
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.github.auth import GitHubAppAuth
from app.github.client import (
    GitHubClient,
    GitHubError,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubTransient,
    GitHubValidation,
)

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
PUBLIC_KEY = _KEY.public_key()
NOW = 1_800_000_000.0


def iso(seconds_from_now):
    return (datetime.fromtimestamp(NOW, UTC) + timedelta(seconds=seconds_from_now)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def make_auth(handler, redis=None):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    redis = redis or fakeredis.FakeAsyncRedis()
    return GitHubAppAuth("123", PRIVATE_PEM, http, redis, clock=lambda: NOW), redis


class TokenServer:
    def __init__(self, expires_in=3600):
        self.mints, self.expires_in = 0, expires_in

    def __call__(self, req: httpx.Request):
        self.mints += 1
        assert req.url.path.endswith("/access_tokens")
        return httpx.Response(
            201, json={"token": f"tok{self.mints}", "expires_at": iso(self.expires_in)}
        )


# ---- auth ------------------------------------------------------------------------------------


def test_app_jwt_is_rs256_signed_short_lived_and_names_the_app():
    auth, _ = make_auth(TokenServer())
    claims = jwt.decode(
        auth.app_jwt(),
        PUBLIC_KEY,
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims["iss"] == "123"
    assert claims["exp"] - claims["iat"] <= 10 * 60


async def test_token_is_minted_once_then_served_from_cache():
    server = TokenServer()
    auth, _ = make_auth(server)
    assert await auth.installation_token(1) == "tok1"
    assert await auth.installation_token(1) == "tok1"
    assert server.mints == 1


async def test_installations_have_separate_tokens():
    server = TokenServer()
    auth, _ = make_auth(server)
    assert await auth.installation_token(1) != await auth.installation_token(2)


async def test_cache_ttl_leaves_a_refresh_margin_before_expiry():
    auth, redis = make_auth(TokenServer(expires_in=3600))
    await auth.installation_token(1)
    ttl = await redis.ttl("gh:token:1")
    assert 3600 - 300 - 5 <= ttl <= 3600 - 300


async def test_token_about_to_expire_is_not_cached():
    server = TokenServer(expires_in=200)  # inside the 5 minute margin
    auth, redis = make_auth(server)
    await auth.installation_token(1)
    assert await redis.get("gh:token:1") is None
    await auth.installation_token(1)
    assert server.mints == 2


async def test_concurrent_callers_share_one_mint():
    server = TokenServer()
    auth, _ = make_auth(server)
    tokens = await asyncio.gather(*(auth.installation_token(1) for _ in range(20)))
    assert set(tokens) == {"tok1"} and server.mints == 1


async def test_invalidate_forces_a_fresh_token():
    server = TokenServer()
    auth, _ = make_auth(server)
    await auth.installation_token(1)
    await auth.invalidate(1)
    assert await auth.installation_token(1) == "tok2"


async def test_mint_failure_raises():
    auth, _ = make_auth(lambda req: httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(httpx.HTTPStatusError):
        await auth.installation_token(1)


# ---- client ----------------------------------------------------------------------------------


class FakeAuth:
    def __init__(self):
        self.refreshes, self.n = 0, 0

    async def installation_token(self, installation_id):
        self.n += 1
        return f"tok{self.n}"

    async def invalidate(self, installation_id):
        self.refreshes += 1


def make_client(handler):
    auth = FakeAuth()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubClient(auth, http), auth


async def test_get_pr_sends_bearer_token():
    seen = {}

    def handler(req):
        seen["auth"], seen["path"] = req.headers["authorization"], req.url.path
        return httpx.Response(200, json={"number": 7})

    client, _ = make_client(handler)
    assert (await client.get_pr(1, "o/r", 7))["number"] == 7
    assert seen == {"auth": "Bearer tok1", "path": "/repos/o/r/pulls/7"}


async def test_get_diff_requests_the_diff_media_type():
    seen = {}

    def handler(req):
        seen["accept"] = req.headers["accept"]
        return httpx.Response(200, text="diff --git a/x b/x\n")

    client, _ = make_client(handler)
    assert (await client.get_diff(1, "o/r", 7)).startswith("diff --git")
    assert seen["accept"] == "application/vnd.github.v3.diff"


async def test_huge_diff_falls_back_to_the_paginated_files_endpoint():
    pages = {1: [{"filename": f"f{i}.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"} for i in range(100)],
             2: [{"filename": "last.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"}]}  # fmt: skip

    def handler(req):
        if req.url.path.endswith("/files"):
            return httpx.Response(200, json=pages[int(req.url.params["page"])])
        return httpx.Response(406, json={"message": "Sorry, the diff exceeded the maximum"})

    client, _ = make_client(handler)
    diff = await client.get_diff(1, "o/r", 7)
    assert diff.count("diff --git") == 101 and "last.py" in diff


async def test_get_file_returns_none_when_missing_and_passes_the_ref():
    seen = {}

    def handler(req):
        seen["ref"] = req.url.params.get("ref")
        return httpx.Response(404, json={"message": "Not Found"})

    client, _ = make_client(handler)
    assert await client.get_file(1, "o/r", ".reviewly.yml", "basesha") is None
    assert seen["ref"] == "basesha"


async def test_get_file_returns_text():
    client, _ = make_client(lambda req: httpx.Response(200, text="strictness: high"))
    assert await client.get_file(1, "o/r", ".reviewly.yml", "x") == "strictness: high"


async def test_list_reviews_follows_pagination():
    def handler(req):
        page = int(req.url.params["page"])
        return httpx.Response(
            200, json=[{"id": i} for i in range(100)] if page == 1 else [{"id": 100}]
        )

    client, _ = make_client(handler)
    assert len(await client.list_reviews(1, "o/r", 7)) == 101


async def test_create_review_posts_the_payload():
    seen = {}

    def handler(req):
        seen["body"], seen["method"] = json.loads(req.content), req.method
        return httpx.Response(200, json={"id": 55})

    client, _ = make_client(handler)
    assert (await client.create_review(1, "o/r", 7, {"event": "COMMENT"}))["id"] == 55
    assert seen == {"body": {"event": "COMMENT"}, "method": "POST"}


async def test_401_refreshes_the_token_once_and_retries():
    calls = []

    def handler(req):
        calls.append(req.headers["authorization"])
        return httpx.Response(401) if len(calls) == 1 else httpx.Response(200, json={})

    client, auth = make_client(handler)
    await client.get_pr(1, "o/r", 7)
    assert calls == ["Bearer tok1", "Bearer tok2"] and auth.refreshes == 1


async def test_persistent_401_gives_up_instead_of_looping():
    client, auth = make_client(lambda req: httpx.Response(401, text="bad credentials"))
    with pytest.raises(GitHubError):
        await client.get_pr(1, "o/r", 7)
    assert auth.refreshes == 1


@pytest.mark.parametrize(
    "resp,exc",
    [
        (httpx.Response(404, text="nf"), GitHubNotFound),
        (httpx.Response(429, headers={"retry-after": "9"}), GitHubRateLimited),
        (httpx.Response(403, headers={"x-ratelimit-remaining": "0"}), GitHubRateLimited),
        (httpx.Response(403, text="You have exceeded a secondary rate limit"), GitHubRateLimited),
        (httpx.Response(403, text="Resource not accessible"), GitHubError),
        (httpx.Response(422, text="Validation Failed"), GitHubValidation),
        (httpx.Response(502, text="bad gateway"), GitHubTransient),
    ],
)
async def test_error_status_mapping(resp, exc):
    client, _ = make_client(lambda req: resp)
    with pytest.raises(exc):
        await client.get_pr(1, "o/r", 7)


async def test_rate_limit_carries_retry_after():
    client, _ = make_client(lambda req: httpx.Response(429, headers={"retry-after": "9"}))
    with pytest.raises(GitHubRateLimited) as ei:
        await client.get_pr(1, "o/r", 7)
    assert ei.value.retry_after_s == 9


async def test_network_error_is_transient():
    def handler(req):
        raise httpx.ConnectError("refused")

    client, _ = make_client(handler)
    with pytest.raises(GitHubTransient):
        await client.get_pr(1, "o/r", 7)
