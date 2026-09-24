from collections.abc import Callable
from typing import Any

from redis.asyncio import Redis

from app.queue.redis_queue import now_ms

_BUCKET = """
local cap, rate, now, cost = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens, ts = tonumber(data[1]), tonumber(data[2])
if tokens == nil then tokens = cap; ts = now end
tokens = math.min(cap, tokens + (now - ts) * rate / 1000)
local wait = 0
if tokens >= cost then
  tokens = tokens - cost
else
  wait = math.ceil((cost - tokens) * 1000 / rate)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', now)
redis.call('PEXPIRE', KEYS[1], 3600000)
return wait
"""


class RateLimiter:
    """Token bucket per installation: `burst` calls at once, refilled at `per_minute`."""

    def __init__(
        self,
        redis: Redis,
        *,
        per_minute: float,
        burst: int,
        prefix: str = "rate:",
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._p, self._clock = prefix, clock
        self._rate_per_s = per_minute / 60.0
        self._burst = burst
        self._script: Any = redis.register_script(_BUCKET)

    async def acquire(self, installation_id: int, cost: int = 1) -> float:
        """Returns 0 if allowed (and consumes), else seconds to wait (nothing consumed)."""
        wait_ms = await self._script(
            keys=[f"{self._p}{installation_id}"],
            args=[self._burst, self._rate_per_s, self._clock(), cost],
        )
        return int(wait_ms) / 1000.0
