from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(url: str, *, pool_size: int = 5, max_overflow: int = 5) -> AsyncEngine:
    """Postgres pools are per process: web processes x (pool_size + max_overflow) must stay under
    the database's connection limit. SQLite (tests) uses its own pool, which takes no such options."""
    if url.startswith("sqlite"):
        return create_async_engine(url, pool_pre_ping=True)
    return create_async_engine(
        url, pool_pre_ping=True, pool_size=pool_size, max_overflow=max_overflow
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with maker() as session:
        yield session
