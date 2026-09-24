from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Job
from app.queue.redis_queue import RedisJobQueue


class FakeClock:
    def __init__(self, start_ms: int = 1_000_000) -> None:
        self.ms = start_ms

    def __call__(self) -> int:
        return self.ms

    def advance(self, seconds: float) -> None:
        self.ms += int(seconds * 1000)


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "worker_concurrency": 4,
        "per_installation_cap": 2,
        "visibility_timeout_s": 30,
        "job_timeout_s": 5,
        "max_attempts": 3,
        "backoff_base_s": 2.0,
        "backoff_cap_s": 16.0,
        "poll_interval_s": 0.01,
        "reconcile_grace_s": 60.0,
        "shutdown_grace_s": 0.2,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def add_job(
    sm: async_sessionmaker[AsyncSession],
    queue: RedisJobQueue | None,
    *,
    inst: int = 1,
    n: int = 1,
    lines: int = 10,
    tag: str = "",
) -> list[str]:
    """Insert `n` queued jobs for an installation (and enqueue them unless queue is None)."""
    ids = []
    async with sm() as s:
        for i in range(n):
            job = Job(
                idempotency_key=f"{inst}:{tag}:{i}:{id(s)}:{len(ids)}",
                installation_id=inst,
                repo_full_name="a/b",
                pr_number=i,
                head_sha=f"sha{i}",
                changed_lines=lines,
                delivery_id=f"d{inst}-{i}",
                correlation_id=f"c{inst}-{i}",
            )
            s.add(job)
            ids.append(job.id)
        await s.commit()
    if queue is not None:
        for jid in ids:
            await queue.enqueue(jid, inst, lines)
    return ids
