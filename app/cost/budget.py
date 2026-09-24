from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

_RESERVE = """
local limit, n, ttl = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
local cur = tonumber(redis.call('GET', KEYS[1]) or '0')
if limit > 0 and cur + n > limit then return -1 end
local new = redis.call('INCRBY', KEYS[1], n)
redis.call('EXPIRE', KEYS[1], ttl)
return new
"""

DAY_TTL_S = 3 * 24 * 3600  # keep yesterday's counter around for inspection, then let it expire


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TokenBudget:
    """Daily (UTC) token budget per installation.

    Callers reserve an estimate before an LLM call and settle to the real usage afterwards, so
    concurrent calls can never collectively overshoot the limit by more than their estimates'
    error. A limit of 0 means unlimited (usage is still counted).
    """

    def __init__(
        self,
        redis: Redis,
        daily_limit: int,
        *,
        prefix: str = "budget:",
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._r, self.limit, self._p, self._clock = redis, daily_limit, prefix, clock
        self._reserve_script: Any = redis.register_script(_RESERVE)

    def _key(self, installation_id: int) -> str:
        return f"{self._p}{installation_id}:{self._clock():%Y%m%d}"

    async def reserve(self, installation_id: int, tokens: int) -> bool:
        res = await self._reserve_script(
            keys=[self._key(installation_id)], args=[self.limit, max(tokens, 0), DAY_TTL_S]
        )
        return int(res) >= 0

    async def settle(self, installation_id: int, reserved: int, actual: int) -> None:
        """Replace an earlier reservation with what was really used (may exceed the limit)."""
        delta = actual - reserved
        if delta:
            await self._r.incrby(self._key(installation_id), delta)

    async def used(self, installation_id: int) -> int:
        raw = await self._r.get(self._key(installation_id))
        return int(raw) if raw else 0

    async def remaining(self, installation_id: int) -> int | None:
        return None if self.limit == 0 else max(0, self.limit - await self.used(installation_id))
