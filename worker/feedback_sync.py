from datetime import UTC, datetime, timedelta
from typing import Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.db.models import FindingRow
from app.feedback.apply import reaction_feedback

log = structlog.get_logger()


class ReactionSource(Protocol):
    async def list_comment_reactions(
        self, inst: int, repo: str, comment_id: int
    ) -> list[dict[str, object]] | None: ...


async def sync_reactions(
    sm: async_sessionmaker[AsyncSession],
    github: ReactionSource,
    *,
    limit: int = 100,
    max_age_days: int = 14,
    recheck_after_minutes: int = 30,
    now: datetime | None = None,
) -> int:
    """GitHub sends no webhook for reactions, so poll recent comments. Oldest-checked first, so a
    large backlog is worked through fairly. Returns how many findings changed."""
    now = now or datetime.now(UTC)
    changed = 0
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(FindingRow)
                    .where(
                        col(FindingRow.github_comment_id).is_not(None),
                        col(FindingRow.created_at) > now - timedelta(days=max_age_days),
                        (col(FindingRow.reactions_checked_at).is_(None))
                        | (
                            col(FindingRow.reactions_checked_at)
                            < now - timedelta(minutes=recheck_after_minutes)
                        ),
                        # a deliberate `@reviewly dismiss/accept` reply outranks reactions
                        (col(FindingRow.feedback_source).is_(None))
                        | (col(FindingRow.feedback_source) != "command"),
                    )
                    .order_by(col(FindingRow.reactions_checked_at).asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            assert row.github_comment_id is not None
            try:
                reactions = await github.list_comment_reactions(
                    row.installation_id, row.repo_full_name, row.github_comment_id
                )
            except Exception:
                log.exception("reaction_fetch_failed", finding=row.id)
                continue  # try again next round; do not mark it as checked
            row.reactions_checked_at = now
            if reactions is None:  # the comment was deleted
                continue
            verdict = reaction_feedback(reactions)
            if verdict != row.feedback:
                row.feedback, row.feedback_at = verdict, now if verdict else None
                row.feedback_source = "reaction" if verdict else None
                changed += 1
        await s.commit()
    return changed
