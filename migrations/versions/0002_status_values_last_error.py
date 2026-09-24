"""store status values not names; add last_error

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.execute("UPDATE jobs SET status = lower(status)")
    op.add_column("jobs", sa.Column("last_error", sa.String(), nullable=True))
    with op.batch_alter_table("jobs") as batch:
        batch.alter_column("status", type_=sa.String(16), existing_nullable=False)
        for col in ("created_at", "updated_at"):
            batch.alter_column(
                col,
                type_=sa.DateTime(timezone=True),
                existing_nullable=False,
                postgresql_using=f"{col} AT TIME ZONE 'UTC'",
            )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("last_error")
