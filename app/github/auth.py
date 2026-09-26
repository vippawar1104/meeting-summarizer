import asyncio
import time
from collections.abc import Callable
from datetime import datetime

import httpx
import jwt
from redis.asyncio import Redis

TOKEN_REFRESH_MARGIN_S = 300  # renew 5 minutes before GitHub expires the token


class GitHubAppAuth:
    """GitHub App JWT -> per-installation access tokens, cached in Redis and refreshed early."""

    def __init__(
        self,
        app_id: str,
        private_key: str,
        http: httpx.AsyncClient,
        redis: Redis,
        *,
        api_url: str = "https://api.github.com",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._app_id, self._http, self._redis = app_id, http, redis
        self._key = private_key.replace("\\n", "\n")  # also accept a one-line key with literal \n
        self._api, self._clock = api_url, clock
        self._locks: dict[int, asyncio.Lock] = {}

    def app_jwt(self) -> str:
        now = int(self._clock())
        claims = {"iat": now - 60, "exp": now + 9 * 60, "iss": self._app_id}
        return jwt.encode(claims, self._key, algorithm="RS256")

    def _cache_key(self, installation_id: int) -> str:
        return f"gh:token:{installation_id}"

    async def invalidate(self, installation_id: int) -> None:
        await self._redis.delete(self._cache_key(installation_id))

    async def installation_token(self, installation_id: int) -> str:
        cached = await self._redis.get(self._cache_key(installation_id))
        if cached:
            return cached.decode() if isinstance(cached, bytes) else str(cached)
        # One refresh per installation at a time, so a burst of jobs does not mint N tokens.
        lock = self._locks.setdefault(installation_id, asyncio.Lock())
        async with lock:
            cached = await self._redis.get(self._cache_key(installation_id))
            if cached:
                return cached.decode() if isinstance(cached, bytes) else str(cached)
            return await self._mint(installation_id)

    async def _mint(self, installation_id: int) -> str:
        resp = await self._http.post(
            f"{self._api}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {self.app_jwt()}",
                "Accept": "application/vnd.github+json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        token: str = data["token"]
        expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).timestamp()
        ttl = int(expires - self._clock() - TOKEN_REFRESH_MARGIN_S)
        if ttl > 0:
            await self._redis.set(self._cache_key(installation_id), token, ex=ttl)
        return token
