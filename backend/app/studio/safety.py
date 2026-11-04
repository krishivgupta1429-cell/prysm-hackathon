"""Built during the Meta Global AI Developer Hackathon (new work).

The hidden budget safety net for the hackathon demo deployment, plus the "unlimited" view of an account.

The demo has no plans or credits, so vendor spend is protected quietly instead: a daily spend cap (the sum of the real
cost of finished analyses per UTC day), one running analysis per user, an hourly analysis limit per user, a sign-up
limit per IP, and a cap on analyses running at once across all backend processes. Every limit is a small Redis counter
with an expiry, so a crashed job can never block anyone for long. Rejections are HTTP 429 with a polite message that
says nothing about credits.

This module imports nothing from the baseline: callers pass the async Redis client and the limits in.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

DAILY_BUDGET_MESSAGE = "Prysm's demo budget for today is used up. Please try again tomorrow."
ALREADY_RUNNING_MESSAGE = "You already have an analysis running. Please wait for it to finish."
HOURLY_LIMIT_MESSAGE = "You've reached the hourly limit for analyses. Please try again in a little while."
DAILY_LIMIT_MESSAGE = "You've reached today's limit for analyses. Please come back tomorrow."
REGISTER_LIMIT_MESSAGE = "Too many sign-ups from this network. Please try again later."

UNLIMITED_PLAN = "creator"          # the top plan: every feature the website gates is open
UNLIMITED_CREDITS = 999_999.0       # what payloads report as the balance; the website shows no balance in Studio mode

PREFIX = "prysm:studio"
SPEND_TTL_S = 48 * 3600
HOUR_TTL_S = 2 * 3600
DAY_TTL_S = 36 * 3600
SLOT_TTL_S = 30 * 60                # a slot whose job died frees itself after this long


class LimitExceeded(Exception):
    """A safety-net limit was hit; `status_code` and `message` go straight into the HTTP response."""
    status_code = 429

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Limits:
    daily_spend_cap_usd: float = 10.0
    user_max_concurrent: int = 1
    user_hourly_limit: int = 10
    user_daily_limit: int = 0           # 0 = no per-user daily limit (a public deployment sets one: STUDIO_USER_DAILY_LIMIT)
    register_per_ip_hourly: int = 5
    max_concurrent: int = 2


def limits_from_settings(settings) -> Limits:
    """The safety-net limits from the app settings (the only place they are read, so routers pass one object around)."""
    return Limits(daily_spend_cap_usd=settings.STUDIO_DAILY_SPEND_CAP_USD, user_max_concurrent=settings.STUDIO_USER_MAX_CONCURRENT,
                  user_hourly_limit=settings.STUDIO_USER_HOURLY_LIMIT, user_daily_limit=settings.STUDIO_USER_DAILY_LIMIT,
                  register_per_ip_hourly=settings.STUDIO_REGISTER_PER_IP_HOURLY,
                  max_concurrent=settings.STUDIO_MAX_CONCURRENT)


def unlimited_view(enabled: bool, plan, credits):
    """The (plan, credits) a payload should report: the top plan and an unlimited balance when unlimited mode is on."""
    return (UNLIMITED_PLAN, UNLIMITED_CREDITS) if enabled else (plan, credits)


def _utc(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _day_key(now: datetime | None) -> str:
    return f"{PREFIX}:spend:{_utc(now):%Y%m%d}"


def _hour_stamp(now: datetime | None) -> str:
    return f"{_utc(now):%Y%m%d%H}"


async def spent_today(redis, now: datetime | None = None) -> float:
    raw = await redis.get(_day_key(now))
    return float(raw) if raw else 0.0


async def record_spend(redis, usd: float, now: datetime | None = None) -> None:
    """Add the real cost of a finished analysis to today's total."""
    if not usd or usd <= 0:
        return
    key = _day_key(now)
    await redis.incrbyfloat(key, float(usd))
    await redis.expire(key, SPEND_TTL_S)


async def check_daily_cap(redis, limits: Limits, now: datetime | None = None) -> None:
    if await spent_today(redis, now) >= limits.daily_spend_cap_usd:
        raise LimitExceeded(DAILY_BUDGET_MESSAGE)


async def _acquire(redis, key: str, limit: int) -> bool:
    """INCR-based slot: take one if the count stays within the limit, else give it back."""
    count = await redis.incr(key)
    await redis.expire(key, SLOT_TTL_S)
    if count > limit:
        await redis.decr(key)
        return False
    return True


async def _release(redis, key: str) -> None:
    if await redis.decr(key) < 0:
        await redis.incr(key)  # never go below zero


async def admit(redis, user_id: str, limits: Limits, now: datetime | None = None) -> None:
    """Let one analysis start for this user, or raise LimitExceeded. On success the caller must later call release_user."""
    await check_daily_cap(redis, limits, now)
    if not await _acquire(redis, f"{PREFIX}:running:{user_id}", limits.user_max_concurrent):
        raise LimitExceeded(ALREADY_RUNNING_MESSAGE)
    hour_key = f"{PREFIX}:hour:{user_id}:{_hour_stamp(now)}"
    count = await redis.incr(hour_key)
    await redis.expire(hour_key, HOUR_TTL_S)
    if count > limits.user_hourly_limit:
        await release_user(redis, user_id)
        raise LimitExceeded(HOURLY_LIMIT_MESSAGE)
    if limits.user_daily_limit > 0:
        day_key = f"{PREFIX}:day:{user_id}:{_utc(now):%Y%m%d}"
        day_count = await redis.incr(day_key)
        await redis.expire(day_key, DAY_TTL_S)
        if day_count > limits.user_daily_limit:
            await release_user(redis, user_id)
            raise LimitExceeded(DAILY_LIMIT_MESSAGE)


async def release_user(redis, user_id: str) -> None:
    await _release(redis, f"{PREFIX}:running:{user_id}")


async def check_register(redis, ip: str, limits: Limits, now: datetime | None = None) -> None:
    """Count a sign-up from this IP; raise LimitExceeded past the hourly limit."""
    key = f"{PREFIX}:register:{ip}:{_hour_stamp(now)}"
    count = await redis.incr(key)
    await redis.expire(key, HOUR_TTL_S)
    if count > limits.register_per_ip_hourly:
        raise LimitExceeded(REGISTER_LIMIT_MESSAGE)


GLOBAL_KEY = f"{PREFIX}:running_total"


async def acquire_global_slot(redis, limits: Limits, poll_s: float = 2.0, max_wait_s: float = 600.0) -> bool:
    """Wait (queue) until fewer than max_concurrent analyses run across all backend processes, then take a slot.
    Returns False if it waited max_wait_s without getting one."""
    waited = 0.0
    while True:
        if await _acquire(redis, GLOBAL_KEY, limits.max_concurrent):
            return True
        if waited >= max_wait_s:
            return False
        await asyncio.sleep(poll_s)
        waited += poll_s


async def release_global_slot(redis) -> None:
    await _release(redis, GLOBAL_KEY)
