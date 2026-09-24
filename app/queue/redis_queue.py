"""Fair, prioritised job queue on Redis sorted sets with atomic Lua transitions.

Why not Redis Streams: a consumer group is one FIFO. We need per-installation fairness, a
per-installation concurrency cap and small-PR priority, which need per-tenant ordering.

Keys (all under a prefix):
  ready:{inst}        ZSET job_id -> priority score (lower runs first)
  insts               ZSET inst -> last-served sequence (least recently served is picked first)
  inflight            ZSET job_id -> visibility deadline (ms)
  inflight_set:{inst} SET of that installation's in-flight job ids (for the concurrency cap)
  delayed             ZSET job_id -> ready-at (ms), used for retry backoff
  meta                HASH job_id -> "inst|score"; present exactly while a job is tracked
  dead                LIST dead-letter queue
Payloads live in Postgres; Redis only holds ids, so losing Redis loses no data (see reconciler).
"""

import time
from collections.abc import Callable
from typing import Any, NamedTuple

from redis.asyncio import Redis

# Priority: score = enqueue time + a bounded penalty per changed line. Small PRs jump ahead of
# large ones, but the penalty is capped so a big PR waits at most MAX_PENALTY_MS behind newer
# small ones and cannot starve.
PENALTY_MS_PER_LINE = 50
MAX_PENALTY_LINES = 2000
REAP_BATCH = 100
PROMOTE_BATCH = 100


def now_ms() -> int:
    return int(time.time() * 1000)


class Claimed(NamedTuple):
    job_id: str
    installation_id: int


_LIB = """
local function requeue(p, job, inst, score)
  redis.call('ZADD', p..'ready:'..inst, score, job)
  local ls = redis.call('HGET', p..'last_served', inst)
  if not ls then ls = '0' end
  redis.call('ZADD', p..'insts', 'NX', ls, inst)
end
local function meta_of(p, job)
  local m = redis.call('HGET', p..'meta', job)
  if not m then return nil, nil end
  return string.match(m, '^(.-)|(.*)$')
end
local function untrack_inflight(p, job, inst)
  redis.call('SREM', p..'inflight_set:'..inst, job)
  redis.call('ZREM', p..'inflight', job)
end
local p = ARGV[1]
"""

_ENQUEUE = (
    _LIB
    + """
local job, inst, score = ARGV[2], ARGV[3], ARGV[4]
if redis.call('HEXISTS', p..'meta', job) == 1 then return 0 end
redis.call('HSET', p..'meta', job, inst..'|'..score)
requeue(p, job, inst, score)
return 1
"""
)

_CLAIM = (
    _LIB
    + """
local now, vis, cap = tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])
local insts = redis.call('ZRANGE', p..'insts', 0, -1)
for _, inst in ipairs(insts) do
  if redis.call('SCARD', p..'inflight_set:'..inst) < cap then
    local popped = redis.call('ZPOPMIN', p..'ready:'..inst)
    if #popped == 0 then
      redis.call('ZREM', p..'insts', inst)
    else
      local job = popped[1]
      redis.call('SADD', p..'inflight_set:'..inst, job)
      redis.call('ZADD', p..'inflight', now + vis, job)
      local seq = redis.call('INCR', p..'seq')
      redis.call('HSET', p..'last_served', inst, seq)
      if redis.call('ZCARD', p..'ready:'..inst) == 0 then
        redis.call('ZREM', p..'insts', inst)
      else
        redis.call('ZADD', p..'insts', seq, inst)
      end
      return {job, inst}
    end
  end
end
return false
"""
)

_ACK = (
    _LIB
    + """
local job = ARGV[2]
local inst = meta_of(p, job)
if inst then untrack_inflight(p, job, inst) end
redis.call('HDEL', p..'meta', job)
return 1
"""
)

_DEAD = (
    _LIB
    + """
local job = ARGV[2]
local inst = meta_of(p, job)
if inst then
  untrack_inflight(p, job, inst)
  redis.call('ZREM', p..'ready:'..inst, job)
end
redis.call('ZREM', p..'delayed', job)
redis.call('HDEL', p..'meta', job)
redis.call('LPUSH', p..'dead', job)
return 1
"""
)

_RETRY = (
    _LIB
    + """
local job, ready_at = ARGV[2], ARGV[3]
local inst = meta_of(p, job)
if not inst then return 0 end
untrack_inflight(p, job, inst)
redis.call('ZADD', p..'delayed', ready_at, job)
return 1
"""
)

_RELEASE = (
    _LIB
    + """
local job = ARGV[2]
local inst, score = meta_of(p, job)
if not inst then return 0 end
untrack_inflight(p, job, inst)
requeue(p, job, inst, score)
return 1
"""
)

_EXTEND = (
    _LIB
    + """
return redis.call('ZADD', p..'inflight', 'XX', 'CH', ARGV[3], ARGV[2])
"""
)

_PROMOTE = (
    _LIB
    + """
local due = redis.call('ZRANGEBYSCORE', p..'delayed', '-inf', ARGV[2], 'LIMIT', 0, ARGV[3])
local n = 0
for _, job in ipairs(due) do
  redis.call('ZREM', p..'delayed', job)
  local inst, score = meta_of(p, job)
  if inst then requeue(p, job, inst, score); n = n + 1 end
end
return n
"""
)

_REAP = (
    _LIB
    + """
local due = redis.call('ZRANGEBYSCORE', p..'inflight', '-inf', ARGV[2], 'LIMIT', 0, ARGV[3])
local out = {}
for _, job in ipairs(due) do
  local inst, score = meta_of(p, job)
  redis.call('ZREM', p..'inflight', job)
  if inst then
    redis.call('SREM', p..'inflight_set:'..inst, job)
    requeue(p, job, inst, score)
    table.insert(out, job)
  end
end
return out
"""
)


def _s(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class RedisJobQueue:
    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = "rq:",
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._r = redis
        self._p = prefix
        self.clock = clock
        reg = redis.register_script
        self._enqueue, self._claim, self._ack = reg(_ENQUEUE), reg(_CLAIM), reg(_ACK)
        self._dead, self._retry, self._release = reg(_DEAD), reg(_RETRY), reg(_RELEASE)
        self._extend, self._promote, self._reap = reg(_EXTEND), reg(_PROMOTE), reg(_REAP)

    async def _call(self, script: Any, *args: Any) -> Any:
        return await script(keys=[], args=[self._p, *args])

    async def enqueue(self, job_id: str, installation_id: int, changed_lines: int) -> bool:
        """Idempotent: returns False if the job is already tracked."""
        penalty = min(changed_lines, MAX_PENALTY_LINES) * PENALTY_MS_PER_LINE
        score = self.clock() + penalty
        return bool(await self._call(self._enqueue, job_id, installation_id, score))

    async def claim(self, *, visibility_ms: int, cap: int) -> Claimed | None:
        res = await self._call(self._claim, self.clock(), visibility_ms, cap)
        if not res:
            return None
        return Claimed(_s(res[0]), int(_s(res[1])))

    async def ack(self, job_id: str) -> None:
        await self._call(self._ack, job_id)

    async def dead(self, job_id: str) -> None:
        await self._call(self._dead, job_id)

    async def retry(self, job_id: str, delay_s: float) -> None:
        await self._call(self._retry, job_id, self.clock() + int(delay_s * 1000))

    async def release(self, job_id: str) -> None:
        """Put an in-flight job straight back on the ready set (used on graceful shutdown)."""
        await self._call(self._release, job_id)

    async def extend(self, job_id: str, visibility_ms: int) -> bool:
        return bool(await self._call(self._extend, job_id, self.clock() + visibility_ms))

    async def promote(self) -> int:
        """Move retries whose backoff has elapsed onto the ready set."""
        return int(await self._call(self._promote, self.clock(), PROMOTE_BATCH))

    async def reap(self) -> list[str]:
        """Requeue jobs whose visibility deadline passed (the worker died or hung)."""
        res = await self._call(self._reap, self.clock(), REAP_BATCH)
        return [_s(j) for j in res]

    async def is_tracked(self, job_id: str) -> bool:
        return bool(await self._r.hexists(f"{self._p}meta", job_id))

    async def dead_letters(self) -> list[str]:
        return [_s(j) for j in await self._r.lrange(f"{self._p}dead", 0, -1)]

    async def depth(self) -> dict[str, int]:
        ready = 0
        for inst in await self._r.zrange(f"{self._p}insts", 0, -1):
            ready += await self._r.zcard(f"{self._p}ready:{_s(inst)}")
        return {
            "ready": ready,
            "delayed": await self._r.zcard(f"{self._p}delayed"),
            "inflight": await self._r.zcard(f"{self._p}inflight"),
            "dead": await self._r.llen(f"{self._p}dead"),
        }
