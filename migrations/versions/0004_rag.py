"""job kind, finding feedback, and the pgvector code index (Postgres only)

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("kind", sa.String(), nullable=False, server_default="review"))
    with op.batch_alter_table("findings") as batch:
        batch.add_column(sa.Column("feedback", sa.String(), nullable=True))
        batch.add_column(sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=True))

    if op.get_bind().dialect.name != "postgresql":
        return  # the code index needs pgvector; tests use the in-memory store instead
    from app.rag.pg_store import rag_metadata

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    rag_metadata.create_all(op.get_bind())


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TABLE IF EXISTS code_chunks, indexed_files, repo_index")
    with op.batch_alter_table("findings") as batch:
        batch.drop_column("feedback_at")
        batch.drop_column("feedback")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("kind")
