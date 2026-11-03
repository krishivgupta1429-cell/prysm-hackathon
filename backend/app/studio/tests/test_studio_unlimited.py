"""Hackathon-built (Meta Global AI Developer Hackathon).

Offline tests (no network, database or real Redis) for the hidden budget safety net and the unlimited view of an account: each safety-net
limit (daily spend cap, one running analysis per user, hourly limit, sign-ups per IP, global concurrency) answers
HTTP 429 with its polite message that never mentions credits.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.studio import safety
from app.studio.safety import Limits
from app.studio.tests.fake_redis import FakeRedis

NOW = datetime(2026, 11, 3, 12, 30, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("app.core.cache.redis_client", fake)
    return fake


# ---------------------------------------------------------------- the safety net itself

def test_the_daily_spend_cap_blocks_with_the_budget_message_and_expires_after_48_hours(redis):
    limits = Limits(daily_spend_cap_usd=10.0)
    run(safety.record_spend(redis, 6.0, NOW))
    run(safety.admit(redis, "u1", limits, NOW))  # under the cap
    run(safety.release_user(redis, "u1"))
    run(safety.record_spend(redis, 4.0, NOW))
    with pytest.raises(safety.LimitExceeded) as e:
        run(safety.admit(redis, "u1", limits, NOW))
    assert e.value.status_code == 429 and e.value.message == "Prysm's demo budget for today is used up. Please try again tomorrow."
    assert redis.ttl["prysm:studio:spend:20261103"] == 48 * 3600
    run(safety.admit(redis, "u1", limits, NOW + timedelta(days=1)))  # a new UTC day starts at zero


def test_a_cap_of_zero_blocks_everything_which_is_how_the_smoke_test_forces_a_429(redis):
    with pytest.raises(safety.LimitExceeded):
        run(safety.check_daily_cap(redis, Limits(daily_spend_cap_usd=0.0), NOW))


def test_one_running_analysis_per_user(redis):
    limits = Limits(user_max_concurrent=1)
    run(safety.admit(redis, "u1", limits, NOW))
    with pytest.raises(safety.LimitExceeded) as e:
        run(safety.admit(redis, "u1", limits, NOW))
    assert e.value.message == "You already have an analysis running. Please wait for it to finish."
    run(safety.admit(redis, "u2", limits, NOW))  # another user is not affected
    run(safety.release_user(redis, "u1"))
    run(safety.admit(redis, "u1", limits, NOW))  # free again once the first finished


def test_the_user_concurrency_limit_is_configurable_and_a_slot_never_goes_negative(redis):
    limits = Limits(user_max_concurrent=2)
    run(safety.admit(redis, "u1", limits, NOW)); run(safety.admit(redis, "u1", limits, NOW))
    with pytest.raises(safety.LimitExceeded):
        run(safety.admit(redis, "u1", limits, NOW))
    for _ in range(4):
        run(safety.release_user(redis, "u1"))
    assert int(redis.data["prysm:studio:running:u1"]) == 0


def test_the_hourly_limit_blocks_with_a_friendly_message_and_gives_the_running_slot_back(redis):
    limits = Limits(user_hourly_limit=3)
    for _ in range(3):
        run(safety.admit(redis, "u1", limits, NOW)); run(safety.release_user(redis, "u1"))
    with pytest.raises(safety.LimitExceeded) as e:
        run(safety.admit(redis, "u1", limits, NOW))
    assert e.value.status_code == 429 and "hourly limit" in e.value.message and "credit" not in e.value.message.lower()
    assert int(redis.data["prysm:studio:running:u1"]) == 0  # the rejected attempt did not leave the user "running"
    run(safety.admit(redis, "u1", limits, NOW + timedelta(hours=1)))  # next hour


def test_the_registration_limit_is_per_ip_and_per_hour(redis):
    limits = Limits(register_per_ip_hourly=5)
    for _ in range(5):
        run(safety.check_register(redis, "10.0.0.1", limits, NOW))
    with pytest.raises(safety.LimitExceeded) as e:
        run(safety.check_register(redis, "10.0.0.1", limits, NOW))
    assert e.value.status_code == 429 and e.value.message == "Too many sign-ups from this network. Please try again later."
    run(safety.check_register(redis, "10.0.0.2", limits, NOW))
    run(safety.check_register(redis, "10.0.0.1", limits, NOW + timedelta(hours=1)))


def test_global_concurrency_queues_extra_jobs_until_a_slot_frees(redis):
    limits = Limits(max_concurrent=2)
    assert run(safety.acquire_global_slot(redis, limits)) and run(safety.acquire_global_slot(redis, limits))
    assert run(safety.acquire_global_slot(redis, limits, poll_s=0.01, max_wait_s=0.05)) is False  # nobody freed a slot

    async def scenario():
        waiter = asyncio.create_task(safety.acquire_global_slot(redis, limits, poll_s=0.01, max_wait_s=5))
        await asyncio.sleep(0.05)
        assert not waiter.done()  # still queued
        await safety.release_global_slot(redis)
        return await waiter

    assert run(scenario()) is True
    assert int(redis.data[safety.GLOBAL_KEY]) == 2


def test_every_slot_key_expires_so_a_crashed_job_cannot_block_anyone_for_long(redis):
    run(safety.admit(redis, "u1", Limits(), NOW)); run(safety.acquire_global_slot(redis, Limits()))
    assert redis.ttl["prysm:studio:running:u1"] == safety.SLOT_TTL_S and redis.ttl[safety.GLOBAL_KEY] == safety.SLOT_TTL_S


def test_unlimited_view_reports_the_top_plan_only_when_enabled():
    assert safety.unlimited_view(True, "free", 3.0) == ("creator", safety.UNLIMITED_CREDITS)
    assert safety.unlimited_view(False, "free", 3.0) == ("free", 3.0)


def test_no_safety_net_message_mentions_credits_plans_or_upgrades():
    for message in (safety.DAILY_BUDGET_MESSAGE, safety.ALREADY_RUNNING_MESSAGE, safety.HOURLY_LIMIT_MESSAGE, safety.REGISTER_LIMIT_MESSAGE):
        assert not any(word in message.lower() for word in ("credit", "plan", "upgrade", "pricing", "pay"))
