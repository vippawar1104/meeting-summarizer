from alembic import context
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from app.core.config import get_settings
from app.db import models  # noqa: F401  (registers tables)
from app.rag.pg_store import RAG_TABLES, rag_metadata

target_metadata = [SQLModel.metadata, rag_metadata]


def run() -> None:
    engine = create_engine(get_settings().database_url)
    with engine.connect() as conn:
        is_pg = conn.dialect.name == "postgresql"

        def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
            # The pgvector tables only exist on Postgres.
            return is_pg or not (type_ == "table" and name in RAG_TABLES)

        context.configure(
            connection=conn, target_metadata=target_metadata, include_object=include_object
        )
        with context.begin_transaction():
            context.run_migrations()


run()
