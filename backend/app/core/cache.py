"""
app/core/cache.py
Redis connection and helper utilities for caching analysis results,
rate-limiting, and session management.
"""

import json
import logging
from typing import Any, Optional
import redis.asyncio as aioredis
from app.core.config import settings

logger = logging.getLogger(__name__)

redis_client: aioredis.Redis = aioredis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=20,
)


class CacheKeys:
    """Namespaced cache key templates."""
    ANALYSIS_RESULT   = "analysis:{video_id}"
    USER_CREDITS      = "credits:{user_id}"
    CREATOR_MEMORY    = "memory:{user_id}"
    TRENDING_TOPICS   = "trends:global"
    RATE_LIMIT        = "rate:{user_id}:{endpoint}"
    JOB_STATUS        = "job:{job_id}"
    EMAIL_VERIFY      = "email_verify:{token}"
    PASSWORD_RESET    = "password_reset:{token}"
    EMAIL_RATE        = "email_rate:{email}"


async def cache_set(key: str, value: Any, ttl: int = 3600) -> None:
    """Serialize and store a value with an optional TTL (seconds)."""
    try:
        await redis_client.setex(key, ttl, json.dumps(value))
    except Exception as exc:
        logger.warning("Cache SET failed for key %s: %s", key, exc)


async def cache_get(key: str) -> Optional[Any]:
    """Retrieve and deserialize a cached value."""
    try:
        raw = await redis_client.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("Cache GET failed for key %s: %s", key, exc)
        return None


async def cache_delete(key: str) -> None:
    try:
        await redis_client.delete(key)
    except Exception as exc:
        logger.warning("Cache DELETE failed for key %s: %s", key, exc)


async def cache_invalidate_pattern(pattern: str) -> int:
    """Delete all keys matching a glob pattern. Returns number of deleted keys."""
    keys = await redis_client.keys(pattern)
    if keys:
        return await redis_client.delete(*keys)
    return 0


async def rate_limit_check(user_id: str, endpoint: str, limit: int = 10, window: int = 60) -> bool:
    """
    Sliding-window rate limiter.
    Returns True if the request is allowed, False if rate limit exceeded.
    """
    key = CacheKeys.RATE_LIMIT.format(user_id=user_id, endpoint=endpoint)
    pipe = redis_client.pipeline()
    pipe.incr(key)
    pipe.expire(key, window)
    results = await pipe.execute()
    current_count = results[0]
    return current_count <= limit


async def set_job_status(job_id: str, status: dict, ttl: int = 86400) -> None:
    await cache_set(CacheKeys.JOB_STATUS.format(job_id=job_id), status, ttl=ttl)


async def get_job_status(job_id: str) -> Optional[dict]:
    return await cache_get(CacheKeys.JOB_STATUS.format(job_id=job_id))
