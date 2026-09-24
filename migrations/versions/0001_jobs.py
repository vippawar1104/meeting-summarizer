"""jobs table

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), primary_key=True),
        sa.Column("idempotency_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("installation_id", sa.Integer(), nullable=False),
        sa.Column("repo_full_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("changed_lines", sa.Integer(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("delivery_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("correlation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_jobs_idempotency_key", "jobs", ["idempotency_key"], unique=True)
    op.create_index("ix_jobs_installation_id", "jobs", ["installation_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])


def downgrade() -> None:
    op.drop_table("jobs")
