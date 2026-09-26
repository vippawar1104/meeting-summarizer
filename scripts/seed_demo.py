"""Fill a LOCAL database with believable demo data so the dashboard has something to show.

    REVIEWLY_ENV=dev python -m scripts.seed_demo [--installation 42]

Refuses to run unless the environment is `dev`. Never point this at real data.
"""

import argparse
import asyncio
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlmodel import SQLModel, col

from app.core.config import get_settings
from app.db.models import FindingRow, Job, JobStatus, UsageRow
from app.db.session import make_engine, make_sessionmaker

REPOS = ["acme/widgets", "acme/api", "acme/web"]
CATEGORIES = [
    ("bug", 0.83),
    ("security", 0.57),
    ("testing", 0.0),
    ("risk", 0.6),
    ("performance", 0.4),
]
MESSAGES = {
    "bug": "Loop bound is off by one, so the last item is skipped.",
    "security": "User input is interpolated into a SQL string without parameters.",
    "testing": "New branch has no test covering the error path.",
    "risk": "Changing this column type needs a data migration first.",
    "performance": "This query runs once per item; batch it.",
}


async def seed(installation: int, database_url: str | None = None) -> None:
    settings = get_settings()
    if settings.env != "dev":
        raise SystemExit("refusing to seed: REVIEWLY_ENV is not 'dev'")
    engine = make_engine(database_url or settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    sm = make_sessionmaker(engine)
    rng = random.Random(7)
    now = datetime.now(UTC)

    async with sm() as s:
        # Re-running must be safe: remove what an earlier run created (demo rows only), then re-create.
        old = select(col(Job.id)).where(
            col(Job.installation_id) == installation, col(Job.delivery_id) == "demo"
        )
        await s.execute(delete(FindingRow).where(col(FindingRow.job_id).in_(old)))
        await s.execute(
            delete(Job).where(
                col(Job.installation_id) == installation, col(Job.delivery_id) == "demo"
            )
        )
        await s.execute(delete(UsageRow).where(col(UsageRow.installation_id) == installation))
        for i in range(14):
            repo, pr = REPOS[i % 3], 10 + i
            status = JobStatus.DEAD if i == 5 else JobStatus.DONE
            job = Job(
                idempotency_key=f"demo:{installation}:{i}", installation_id=installation, repo_full_name=repo,
                pr_number=pr, head_sha=f"{rng.getrandbits(40):010x}", status=status, delivery_id="demo",
                correlation_id="demo", prompt_tokens=rng.randint(2000, 9000), completion_tokens=rng.randint(200, 1200),
                cost_usd=0.0, created_at=now - timedelta(hours=7 * i + 1), review_id=1000 + i,
                last_error="skipped: superseded by a newer commit" if i == 8 else ("boom" if i == 5 else None),
            )  # fmt: skip
            s.add(job)
            if status is not JobStatus.DEAD and i != 8:
                for k in range(rng.randint(1, 4)):
                    category, p_accept = CATEGORIES[rng.randrange(len(CATEGORIES))]
                    judged = rng.random() < 0.75
                    fb = (
                        ("accepted" if rng.random() < p_accept else "dismissed") if judged else None
                    )
                    s.add(
                        FindingRow(
                            job_id=job.id, installation_id=installation, repo_full_name=repo, pr_number=pr,
                            file=f"src/module_{k}.py", line=rng.randint(5, 200), severity=rng.choice(["high", "medium", "low"]),
                            category=category, message=MESSAGES[category], confidence=round(rng.uniform(0.7, 0.97), 2),
                            fingerprint=f"demo-{i}-{k}", feedback=fb, feedback_source="reaction" if fb else None,
                            resolved=fb == "accepted" and rng.random() < 0.6, github_comment_id=5_000_000_000 + i * 10 + k,
                            created_at=job.created_at,
                        )
                    )  # fmt: skip
        for months_ago, reviews in ((3, 6), (2, 11), (1, 15), (0, 3)):
            month = (
                (now.replace(day=1) - timedelta(days=30 * months_ago)).strftime("%Y-%m")
                if months_ago
                else now.strftime("%Y-%m")
            )
            s.add(UsageRow(installation_id=installation, period=month, reviews=reviews, findings=reviews * 2,
                           prompt_tokens=reviews * 5200, completion_tokens=reviews * 700, cost_usd=0.0))  # fmt: skip
        await s.commit()
    await engine.dispose()
    print(f"seeded demo data for installation {installation}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--installation", type=int, default=42)
    asyncio.run(seed(ap.parse_args().installation))
