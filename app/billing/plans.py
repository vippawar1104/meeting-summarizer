from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.billing.usage import period_of, usage_for
from app.core.config import Settings
from app.db.models import SubscriptionRow

PAID_STATUSES = {"active", "trialing"}
PAST_DUE_GRACE = timedelta(days=3)  # a failed card should not cut off reviews the same hour


@dataclass
class PlanStatus:
    plan: str  # free | pro
    limit: int | None  # reviews per month, None = unlimited
    used: int

    @property
    def allowed(self) -> bool:
        return self.limit is None or self.used < self.limit


def is_paid(sub: SubscriptionRow | None, now: datetime) -> bool:
    if sub is None or sub.plan != "pro":
        return False
    if sub.status in PAID_STATUSES:
        return True
    if sub.status == "past_due" and sub.current_period_end is not None:
        end = sub.current_period_end
        end = end if end.tzinfo else end.replace(tzinfo=UTC)
        return now < end + PAST_DUE_GRACE
    return False


async def plan_status(
    sm: async_sessionmaker[AsyncSession],
    settings: Settings,
    installation_id: int,
    now: datetime | None = None,
) -> PlanStatus:
    now = now or datetime.now(UTC)
    async with sm() as s:
        sub = await s.get(SubscriptionRow, installation_id)
    usage = await usage_for(sm, installation_id, period_of(now))
    used = usage.reviews if usage else 0
    if is_paid(sub, now):
        return PlanStatus("pro", None, used)
    return PlanStatus("free", settings.free_reviews_per_month or None, used)
