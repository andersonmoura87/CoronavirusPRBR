"""
FastAPI dependency injection providers.

Centralising dependencies here means:
  - Tests can override them with `app.dependency_overrides`.
  - The DB session lifecycle (open → yield → close) is managed in one place.
  - Future additions (auth, rate-limiting) are added here without touching routers.
"""

from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.config import settings

# One engine per process — connection pool is shared across all requests.
# pool_pre_ping=True recycles stale connections so the API survives a DB restart
# without needing a pod restart.
engine = create_async_engine(
    settings.database_url,
    echo=settings.environment == "development",
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an async SQLAlchemy session.

    Usage:
        @router.get("/example")
        async def endpoint(session: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
