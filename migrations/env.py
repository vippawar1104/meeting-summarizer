from alembic import context
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from app.core.config import get_settings
from app.db import models  # noqa: F401  (registers tables)

target_metadata = SQLModel.metadata


def run() -> None:
    engine = create_engine(get_settings().database_url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run()
