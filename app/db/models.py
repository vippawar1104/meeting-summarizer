import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


class Job(SQLModel, table=True):
    """One review of one PR head SHA. The DB row is the source of truth; Redis is a transport."""

    __tablename__ = "jobs"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    # installation + repo + PR + head SHA: a redelivery can never create a second review.
    idempotency_key: str = Field(unique=True, index=True)
    installation_id: int = Field(index=True)
    repo_full_name: str
    pr_number: int
    head_sha: str
    changed_lines: int = 0
    status: JobStatus = Field(default=JobStatus.QUEUED, index=True)
    attempts: int = 0
    delivery_id: str
    correlation_id: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
