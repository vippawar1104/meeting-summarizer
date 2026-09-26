"""feedback linkage on findings, usage metering, subscriptions

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "0005"
down_revision = "0004"

S = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    with op.batch_alter_table("findings") as batch:
        batch.add_column(sa.Column("feedback_source", sa.String(), nullable=True))
        batch.add_column(
            sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("github_comment_id", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column("reactions_checked_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.create_index("ix_findings_github_comment_id", "findings", ["github_comment_id"])

    op.create_table(
        "usage",
        sa.Column("installation_id", sa.Integer(), primary_key=True),
        sa.Column("period", S(), primary_key=True),
        sa.Column("reviews", sa.Integer(), nullable=False),
        sa.Column("findings", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "subscriptions",
        sa.Column("installation_id", sa.Integer(), primary_key=True),
        sa.Column("plan", S(), nullable=False),
        sa.Column("status", S(), nullable=False),
        sa.Column("stripe_customer_id", S(), nullable=True),
        sa.Column("stripe_subscription_id", S(), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_subscriptions_stripe_customer_id", "subscriptions", ["stripe_customer_id"])
    op.create_index(
        "ix_subscriptions_stripe_subscription_id", "subscriptions", ["stripe_subscription_id"]
    )


def downgrade() -> None:
    op.drop_table("subscriptions")
    op.drop_table("usage")
    op.drop_index("ix_findings_github_comment_id", "findings")
    with op.batch_alter_table("findings") as batch:
        for col in ("reactions_checked_at", "github_comment_id", "resolved", "feedback_source"):
            batch.drop_column(col)
