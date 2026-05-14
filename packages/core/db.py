"""Database client wrapper with async connection pooling.

Usage:
    from packages.core.db import get_session, engine

    async with get_session() as session:
        result = await session.execute(select(Family))
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

load_dotenv()

# Convert postgresql:// to postgresql+asyncpg:// for async driver
_sync_url = os.getenv(
    "DATABASE_URL", "postgresql://foodleaf:foodleaf@localhost:5432/foodleaf"
)
DATABASE_URL = _sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
    echo=False,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_session():
    """Yield an async SQLAlchemy session. Auto-commits on success, rolls back on error."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Sync URL for Alembic migrations (Alembic doesn't use async)
SYNC_DATABASE_URL = _sync_url
