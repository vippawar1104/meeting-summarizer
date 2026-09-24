from app.db.models import Job
from app.queue.errors import PermanentError
from worker.runner import Handler


def dispatch(handlers: dict[str, Handler]) -> Handler:
    """Route a job to the handler for its kind (review, index, purge)."""

    async def handler(job: Job) -> None:
        target = handlers.get(job.kind)
        if target is None:
            raise PermanentError(f"no handler for job kind {job.kind!r}")
        await target(job)

    return handler
