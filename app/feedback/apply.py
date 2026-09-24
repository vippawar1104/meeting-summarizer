"""Turn what people do with Reviewly's comments into accepted/dismissed feedback.

Signals, strongest first:
  * a reply command: `@reviewly dismiss` / `@reviewly accept`
  * reactions on the comment: 👍 ❤️ 🎉 🚀 count as accepted, 👎 😕 as dismissed (bots ignored)
Resolving a review thread is recorded too, but only as `resolved`: people resolve threads for many
reasons, so it is shown on the dashboard and never counted in precision.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.db.models import FindingRow

POSITIVE = {"+1", "heart", "hooray", "rocket"}
NEGATIVE = {"-1", "confused"}
_COMMAND = re.compile(r"^\s*@reviewly\s+(?P<word>[a-z]+)\b", re.I)
_ACCEPT_WORDS = {"accept", "agree", "valid", "correct"}
_DISMISS_WORDS = {"dismiss", "ignore", "reject", "wrong", "invalid"}


def _now() -> datetime:
    return datetime.now(UTC)


def parse_command(body: str) -> str | None:
    m = _COMMAND.match(body or "")
    if not m:
        return None
    word = m["word"].lower()
    if word in _ACCEPT_WORDS:
        return "accepted"
    if word in _DISMISS_WORDS:
        return "dismissed"
    return None


def reaction_feedback(reactions: Iterable[dict[str, Any]]) -> str | None:
    """Net human sentiment from a comment's reactions; ties and no signal give None."""
    score = 0
    for r in reactions:
        if (r.get("user") or {}).get("type") == "Bot":
            continue
        content = r.get("content")
        score += 1 if content in POSITIVE else -1 if content in NEGATIVE else 0
    return "accepted" if score > 0 else "dismissed" if score < 0 else None


async def record_feedback(
    sm: async_sessionmaker[AsyncSession],
    installation_id: int,
    comment_id: int,
    feedback: str,
    source: str,
) -> bool:
    """Set feedback on the finding behind a GitHub comment. Scoped to the installation, so one
    tenant can never touch another's findings. Returns whether a finding was updated."""
    assert feedback in ("accepted", "dismissed") and source in ("reaction", "command")
    async with sm() as s:
        res = await s.execute(
            update(FindingRow)
            .where(
                col(FindingRow.installation_id) == installation_id,
                col(FindingRow.github_comment_id) == comment_id,
                # a deliberate command is never overwritten by a later reaction poll
                ~((col(FindingRow.feedback_source) == "command") & (source == "reaction")),
            )
            .values(feedback=feedback, feedback_source=source, feedback_at=_now())
        )
        await s.commit()
        return bool(res.rowcount)  # type: ignore[attr-defined]


async def set_resolved(
    sm: async_sessionmaker[AsyncSession],
    installation_id: int,
    comment_ids: list[int],
    resolved: bool,
) -> int:
    if not comment_ids:
        return 0
    async with sm() as s:
        res = await s.execute(
            update(FindingRow)
            .where(
                col(FindingRow.installation_id) == installation_id,
                col(FindingRow.github_comment_id).in_(comment_ids),
            )
            .values(resolved=resolved)
        )
        await s.commit()
        return int(res.rowcount)  # type: ignore[attr-defined]


async def suppressed_fingerprints(
    sm: async_sessionmaker[AsyncSession],
    installation_id: int,
    repo: str,
    min_dismissals: int = 2,
) -> frozenset[str]:
    """Findings this repo's maintainers keep dismissing (and never accept) stop being posted."""
    async with sm() as s:
        rows = (
            await s.execute(
                select(
                    col(FindingRow.fingerprint),
                    func.sum(case((col(FindingRow.feedback) == "dismissed", 1), else_=0)),
                    func.sum(case((col(FindingRow.feedback) == "accepted", 1), else_=0)),
                )
                .where(
                    col(FindingRow.installation_id) == installation_id,
                    col(FindingRow.repo_full_name) == repo,
                    col(FindingRow.feedback).is_not(None),
                )
                .group_by(col(FindingRow.fingerprint))
            )
        ).all()
    return frozenset(
        fp for fp, dismissed, accepted in rows if dismissed >= min_dismissals and not accepted
    )


@dataclass
class RuleStat:
    category: str
    posted: int
    accepted: int
    dismissed: int
    resolved: int

    @property
    def pending(self) -> int:
        return self.posted - self.accepted - self.dismissed

    @property
    def precision(self) -> float | None:
        judged = self.accepted + self.dismissed
        return self.accepted / judged if judged else None


async def rule_stats(
    sm: async_sessionmaker[AsyncSession], installation_id: int, repo: str | None = None
) -> list[RuleStat]:
    """Precision per rule (category): of the findings people judged, how many did they accept."""
    q = (
        select(
            col(FindingRow.category),
            func.count(),
            func.sum(case((col(FindingRow.feedback) == "accepted", 1), else_=0)),
            func.sum(case((col(FindingRow.feedback) == "dismissed", 1), else_=0)),
            func.sum(case((col(FindingRow.resolved).is_(True), 1), else_=0)),
        )
        .where(col(FindingRow.installation_id) == installation_id)
        .group_by(col(FindingRow.category))
        .order_by(col(FindingRow.category))
    )
    if repo:
        q = q.where(col(FindingRow.repo_full_name) == repo)
    async with sm() as s:
        return [
            RuleStat(c, n, a or 0, d or 0, r or 0) for c, n, a, d, r in (await s.execute(q)).all()
        ]
