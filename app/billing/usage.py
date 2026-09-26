from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.db.models import UsageRow


def period_of(when: datetime) -> str:
    return f"{when:%Y-%m}"


async def record_review(
    sm: async_sessionmaker[AsyncSession],
    installation_id: int,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    findings: int,
    now: datetime | None = None,
) -> None:
    """Add one review to this month's usage. An atomic UPDATE first, so concurrent workers never
    lose a count; the row is created on first use."""
    period = period_of(now or datetime.now(UTC))

    def bump() -> Any:
        return (
            update(UsageRow)
            .where(col(UsageRow.installation_id) == installation_id, col(UsageRow.period) == period)
            .values(
                reviews=UsageRow.reviews + 1,
                findings=UsageRow.findings + findings,
                prompt_tokens=UsageRow.prompt_tokens + prompt_tokens,
                completion_tokens=UsageRow.completion_tokens + completion_tokens,
                cost_usd=UsageRow.cost_usd + cost_usd,
                updated_at=now or datetime.now(UTC),
            )
        )

    for _ in range(3):
        async with sm() as s:
            res = await s.execute(bump())
            if res.rowcount:  # type: ignore[attr-defined]
                await s.commit()
                return
            s.add(
                UsageRow(
                    installation_id=installation_id,
                    period=period,
                    reviews=1,
                    findings=findings,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_usd=cost_usd,
                )
            )
            try:
                await s.commit()
                return
            except IntegrityError:  # another worker created the row first: loop and UPDATE it
                await s.rollback()
    raise RuntimeError("could not record usage")


async def usage_for(
    sm: async_sessionmaker[AsyncSession], installation_id: int, period: str
) -> UsageRow | None:
    async with sm() as s:
        return await s.get(UsageRow, (installation_id, period))


async def usage_series(
    sm: async_sessionmaker[AsyncSession], installation_id: int, months: int = 6
) -> list[UsageRow]:
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(UsageRow)
                    .where(col(UsageRow.installation_id) == installation_id)
                    .order_by(col(UsageRow.period).desc())
                    .limit(months)
                )
            )
            .scalars()
            .all()
        )
    return sorted(rows, key=lambda r: r.period)
