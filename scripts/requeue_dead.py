"""Put dead-lettered jobs back in the queue (after fixing whatever killed them).

    python -m scripts.requeue_dead                       # show what would be requeued
    python -m scripts.requeue_dead --apply               # requeue every dead job
    python -m scripts.requeue_dead --apply --installation 42 --since-hours 6

Sets the jobs back to `queued` with a fresh attempt budget; the worker's reconciler re-enqueues
them within REVIEWLY_RECONCILE_INTERVAL_S. Reviews are idempotent per head SHA, so a PR that was
in fact reviewed is skipped rather than reviewed twice.
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlmodel import col

from app.core.config import get_settings
from app.db.models import Job, JobStatus
from app.db.session import make_engine, make_sessionmaker


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually requeue (default: dry run)")
    ap.add_argument("--installation", type=int)
    ap.add_argument("--since-hours", type=float, help="only jobs that died within this window")
    args = ap.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        query = select(Job).where(col(Job.status) == JobStatus.DEAD)
        if args.installation is not None:
            query = query.where(col(Job.installation_id) == args.installation)
        if args.since_hours is not None:
            cutoff = datetime.now(UTC) - timedelta(hours=args.since_hours)
            query = query.where(col(Job.updated_at) >= cutoff)
        jobs = list((await s.execute(query)).scalars())
        for job in jobs:
            print(
                f"{job.id} {job.repo_full_name}#{job.pr_number} {job.kind}: {(job.last_error or '')[:90]}"
            )
            if args.apply:
                job.status, job.attempts = JobStatus.QUEUED, 0
                job.last_error = "requeued by operator"
                job.updated_at = datetime.now(UTC) - timedelta(
                    seconds=settings.reconcile_grace_s + 1
                )
        if args.apply:
            await s.commit()
    await engine.dispose()
    print(
        f"{len(jobs)} dead job(s) {'requeued' if args.apply else 'found (dry run; pass --apply)'}"
    )


if __name__ == "__main__":
    asyncio.run(main())
