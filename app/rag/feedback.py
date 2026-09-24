from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.db.models import FindingRow


@dataclass(frozen=True)
class FeedbackNote:
    file: str
    category: str
    message: str


@dataclass
class FeedbackContext:
    accepted: list[FeedbackNote] = field(default_factory=list)
    dismissed: list[FeedbackNote] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.accepted or self.dismissed)


async def past_feedback(
    sm: async_sessionmaker[AsyncSession],
    installation_id: int,
    repo: str,
    paths: set[str],
    limit: int = 5,
) -> FeedbackContext:
    """This repo's own history of accepted/dismissed findings, same-file ones first."""
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(FindingRow)
                    .where(
                        col(FindingRow.installation_id) == installation_id,
                        col(FindingRow.repo_full_name) == repo,
                        col(FindingRow.feedback).in_(["accepted", "dismissed"]),
                    )
                    .order_by(col(FindingRow.feedback_at).desc())
                    .limit(60)
                )
            )
            .scalars()
            .all()
        )
    ctx = FeedbackContext()
    seen: set[str] = set()
    for row in sorted(rows, key=lambda r: r.file not in paths):  # stable: keeps recency order
        if row.fingerprint in seen:
            continue
        seen.add(row.fingerprint)
        bucket = ctx.accepted if row.feedback == "accepted" else ctx.dismissed
        if len(bucket) < limit:
            bucket.append(FeedbackNote(row.file, row.category, row.message[:200]))
    return ctx
