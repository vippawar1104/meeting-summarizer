import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Column
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    """Timezone-aware UTC; SQLModel stores datetimes as timestamptz and rejects naive values."""
    return datetime.now(UTC)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    DEAD = "dead"


class Job(SQLModel, table=True):
    """One review of one PR head SHA. The DB row is the source of truth; Redis is a transport."""

    __tablename__ = "jobs"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    kind: str = Field(default="review")  # review | index | purge
    # installation + repo + PR + head SHA: a redelivery can never create a second review.
    idempotency_key: str = Field(unique=True, index=True)
    installation_id: int = Field(index=True)
    repo_full_name: str
    pr_number: int
    head_sha: str
    changed_lines: int = 0
    # Store the enum *value* ("queued"), not the member name, so raw SQL and code agree.
    status: JobStatus = Field(
        default=JobStatus.QUEUED,
        sa_column=Column(
            SAEnum(
                JobStatus,
                native_enum=False,
                length=16,
                values_callable=lambda e: [m.value for m in e],
            ),
            index=True,
            nullable=False,
        ),
    )
    attempts: int = 0
    last_error: str | None = None
    review_id: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    delivery_id: str
    correlation_id: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class FindingRow(SQLModel, table=True):
    """A finding that was posted to GitHub. Feeds the feedback loop and the dashboard."""

    __tablename__ = "findings"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    job_id: str = Field(index=True)
    installation_id: int = Field(index=True)
    repo_full_name: str = Field(index=True)
    pr_number: int
    file: str
    line: int
    severity: str
    category: str
    message: str
    suggested_patch: str | None = None
    confidence: float
    fingerprint: str = Field(index=True)  # stable id for "the same finding", used to suppress FPs
    feedback: str | None = None  # accepted | dismissed, set by the feedback loop
    feedback_at: datetime | None = None
    feedback_source: str | None = None  # reaction | command
    resolved: bool = False  # the review thread was resolved on GitHub (informational)
    # GitHub review-comment ids exceed 32 bits, hence BigInteger.
    github_comment_id: int | None = Field(
        default=None, sa_column=Column(BigInteger, index=True, nullable=True)
    )
    reactions_checked_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)


class UsageRow(SQLModel, table=True):
    """One row per installation per calendar month (UTC). The basis for the free tier and billing."""

    __tablename__ = "usage"

    installation_id: int = Field(primary_key=True)
    period: str = Field(primary_key=True)  # "2026-09"
    reviews: int = 0
    findings: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    updated_at: datetime = Field(default_factory=_now)


class SubscriptionRow(SQLModel, table=True):
    __tablename__ = "subscriptions"

    installation_id: int = Field(primary_key=True)
    plan: str = "free"  # free | pro
    status: str = "active"  # mirrors Stripe: active, past_due, canceled, ...
    stripe_customer_id: str | None = Field(default=None, index=True)
    stripe_subscription_id: str | None = Field(default=None, index=True)
    current_period_end: datetime | None = None
    updated_at: datetime = Field(default_factory=_now)
