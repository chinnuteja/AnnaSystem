"""Async Redis client wrapper.

Usage:
    from packages.core.redis_client import get_redis

    redis = await get_redis()
    await redis.set("key", "value")
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from redis.asyncio import Redis

# Same repo root as apps/api/app/core/config.py (…/foodleaf/.env), regardless of process cwd.
_REDIS_CLIENT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REDIS_CLIENT_ROOT / ".env")
load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

_redis: Redis | None = None


async def get_redis() -> Redis:
    """Return a shared async Redis connection. Creates one on first call."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def close_redis():
    """Cleanly close the Redis connection pool."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
