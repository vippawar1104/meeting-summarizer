from redis.asyncio import Redis

from app.db.models import Job


async def enqueue(redis: Redis, stream: str, job: Job) -> None:
    """Publish a job id to the stream. Payload stays in Postgres; the stream carries only ids."""
    await redis.xadd(
        stream,
        {
            "job_id": job.id,
            "installation_id": str(job.installation_id),
            "changed_lines": str(job.changed_lines),
            "correlation_id": job.correlation_id,
        },
    )
