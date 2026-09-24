"""usage columns on jobs; findings table

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "0003"
down_revision = "0002"

S = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("review_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"))
    op.create_table(
        "findings",
        sa.Column("id", S(), primary_key=True),
        sa.Column("job_id", S(), nullable=False),
        sa.Column("installation_id", sa.Integer(), nullable=False),
        sa.Column("repo_full_name", S(), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("file", S(), nullable=False),
        sa.Column("line", sa.Integer(), nullable=False),
        sa.Column("severity", S(), nullable=False),
        sa.Column("category", S(), nullable=False),
        sa.Column("message", S(), nullable=False),
        sa.Column("suggested_patch", S(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("fingerprint", S(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("job_id", "installation_id", "repo_full_name", "fingerprint"):
        op.create_index(f"ix_findings_{col}", "findings", [col])


def downgrade() -> None:
    op.drop_table("findings")
    with op.batch_alter_table("jobs") as batch:
        for col in ("cost_usd", "completion_tokens", "prompt_tokens", "review_id"):
            batch.drop_column(col)
