import hashlib
import json
from datetime import timedelta

from redis.asyncio import Redis


class ReviewCache:
    """Caches the raw LLM reply for a review section, scoped to one installation.

    The key covers everything that shaped the answer: prompt version, system prompt, the section's
    formatting-normalised diff (with its line numbers), repo rules, retrieved context and
    maintainer feedback. It is deliberately NOT embedding-similarity based: two hunks that differ
    by one operator embed almost identically, and replaying an old review over a changed condition
    is exactly the mistake a reviewer must not make. A hit therefore means "same code, same
    situation", including code that was only reformatted.
    """

    def __init__(self, redis: Redis, ttl_days: int, *, prefix: str = "cache:") -> None:
        self._r, self._ttl, self._p = redis, int(timedelta(days=ttl_days).total_seconds()), prefix

    @staticmethod
    def key(
        prompt_version: str,
        system: str,
        section_norm: str,
        rules: list[str],
        context: str,
        feedback: str,
        model_tag: str = "",
    ) -> str:
        blob = json.dumps(
            [prompt_version, system, section_norm, rules, context, feedback, model_tag]
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def _k(self, installation_id: int, key: str) -> str:
        return f"{self._p}{installation_id}:{key}"

    async def get(self, installation_id: int, key: str) -> str | None:
        raw = await self._r.get(self._k(installation_id, key))
        return None if raw is None else str(json.loads(raw)["response"])

    async def put(self, installation_id: int, key: str, response: str) -> None:
        await self._r.set(
            self._k(installation_id, key), json.dumps({"response": response}), ex=self._ttl
        )

    async def clear_installation(self, installation_id: int) -> int:
        removed = 0
        async for k in self._r.scan_iter(match=f"{self._p}{installation_id}:*", count=500):
            removed += await self._r.delete(k)
        return removed
