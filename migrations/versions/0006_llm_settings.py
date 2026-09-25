"""per-installation LLM provider settings (bring your own key)

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "0006"
down_revision = "0005"

S = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    op.create_table(
        "llm_settings",
        sa.Column("installation_id", sa.Integer(), primary_key=True),
        sa.Column("provider", S(), nullable=False),
        sa.Column("model", S(), nullable=False),
        sa.Column("base_url", S(), nullable=True),
        sa.Column("api_key_encrypted", S(), nullable=False),
        sa.Column("key_hint", S(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_error", S(), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("llm_settings")
