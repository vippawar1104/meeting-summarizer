"""Worker entrypoint. Queue consumption is implemented in Milestone 2."""

import asyncio

import structlog

from app.core.config import get_settings
from app.core.logging import configure_logging


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    structlog.get_logger().info("worker_started", stream=settings.job_stream)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(run())
