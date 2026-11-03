"""Hackathon-built (Meta Global AI Developer Hackathon).

Offline tests (no network, database or real Redis) for STUDIO_UNLIMITED and the hidden budget safety net: unlimited mode
skips the credit gate and every deduction and reports the top plan, the default path is unchanged, and each safety-net
limit (daily spend cap, one running analysis per user, hourly limit, sign-ups per IP, global concurrency) answers
HTTP 429 with its polite message that never mentions credits.
"""
import asyncio
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.core.config import settings
from app.routers import auth, prysm as router
from app.studio import safety
from app.studio.safety import Limits
from app.studio.tests.fake_redis import FakeRedis
from app.studio.tests.test_studio_engine_switch import FakeSession, FakeUser, fake_result
from app.studio.tests.test_studio_separation import RegDB

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


# ---------------------------------------------------------------- the router: /prysm/analyse

@pytest.fixture(scope="module")
def short_video(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    path = tmp_path_factory.mktemp("vid") / "short.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=1", "-t", "5",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)
    return path


class Reached(Exception):
    """Raised by the fake cache once the request has passed every guard (the next step would queue the job)."""


def call_analyse(video_path, monkeypatch, credits=10.0, user_id="u1"):
    user = FakeUser(id=user_id, credits=credits, is_verified=True)

    async def fake_user(token, db):
        return user

    async def fake_set(*a, **k):
        raise Reached()

    monkeypatch.setattr(router, "get_current_user_optional", fake_user)
    monkeypatch.setattr(router, "cache_set", fake_set)
    upload = UploadFile(file=open(video_path, "rb"), size=video_path.stat().st_size, filename="short.mp4")
    return run(router.analyse_video(video=upload, url=None, niche="Fashion", platform="Instagram Reels", brief="", contentGoal="",
                                    intent="my_video", authorization="Bearer x", db=FakeSession(user, [])))


def test_by_default_an_empty_balance_is_still_refused_with_402(short_video, redis, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "legacy"); monkeypatch.setattr(settings, "STUDIO_UNLIMITED", False)
    with pytest.raises(HTTPException) as e:
        call_analyse(short_video, monkeypatch, credits=0.0)
    assert e.value.status_code == 402


@pytest.mark.parametrize("engine", ["legacy", "studio"])
def test_unlimited_mode_skips_the_credit_gate(short_video, redis, monkeypatch, engine):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", engine); monkeypatch.setattr(settings, "STUDIO_UNLIMITED", True)
    with pytest.raises(Reached):  # got past the credit gate and every guard, as far as queueing the job
        call_analyse(short_video, monkeypatch, credits=0.0)


def test_the_daily_cap_answers_429_before_the_upload_is_processed(short_video, redis, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "studio"); monkeypatch.setattr(settings, "STUDIO_UNLIMITED", True)
    monkeypatch.setattr(settings, "STUDIO_DAILY_SPEND_CAP_USD", 0.0)
    with pytest.raises(HTTPException) as e:
        call_analyse(short_video, monkeypatch)
    assert e.value.status_code == 429 and e.value.detail == safety.DAILY_BUDGET_MESSAGE


def test_a_second_analysis_by_the_same_user_answers_429_while_the_first_is_running(short_video, redis, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "studio"); monkeypatch.setattr(settings, "STUDIO_UNLIMITED", True)
    monkeypatch.setattr(settings, "STUDIO_USER_MAX_CONCURRENT", 1)
    with pytest.raises(Reached):
        call_analyse(short_video, monkeypatch)
    with pytest.raises(HTTPException) as e:
        call_analyse(short_video, monkeypatch)
    assert e.value.status_code == 429 and e.value.detail == safety.ALREADY_RUNNING_MESSAGE
    with pytest.raises(Reached):  # a different user is fine
        call_analyse(short_video, monkeypatch, user_id="u2")


def test_the_hourly_limit_answers_429_through_the_router(short_video, redis, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "studio"); monkeypatch.setattr(settings, "STUDIO_UNLIMITED", True)
    monkeypatch.setattr(settings, "STUDIO_USER_MAX_CONCURRENT", 5); monkeypatch.setattr(settings, "STUDIO_USER_HOURLY_LIMIT", 2)
    for _ in range(2):
        with pytest.raises(Reached):
            call_analyse(short_video, monkeypatch)
    with pytest.raises(HTTPException) as e:
        call_analyse(short_video, monkeypatch)
    assert e.value.status_code == 429 and e.value.detail == safety.HOURLY_LIMIT_MESSAGE


def test_the_legacy_engine_has_no_safety_net(short_video, redis, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "legacy"); monkeypatch.setattr(settings, "STUDIO_DAILY_SPEND_CAP_USD", 0.0)
    for _ in range(3):
        with pytest.raises(Reached):
            call_analyse(short_video, monkeypatch)
    assert redis.data == {}


# ---------------------------------------------------------------- the job: no deductions, real cost kept

@pytest.fixture
def job(monkeypatch, tmp_path, redis):
    user = FakeUser(id="u1", credits=5.0, plan=SimpleNamespace(value="free"))
    calls, cache, added = [], {}, []

    async def fake_cache_set(key, value, ttl=None):
        cache[key] = value

    async def studio(**kw):
        calls.append(kw)
        return fake_result(total_usd=1.0)  # 1 USD is about 31 credits: far more than the user's 5

    monkeypatch.setattr("app.core.database.AsyncSessionLocal", lambda: FakeSession(user, added))
    monkeypatch.setattr(router, "cache_set", fake_cache_set)
    monkeypatch.setattr("app.studio.adapter.run_studio_analysis", studio)
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "studio")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    redis.data["prysm:studio:running:u1"] = 1  # as left by /prysm/analyse

    def go(job_id="job-1"):
        run(router._run_analysis_task(
            job_id=job_id, video_path=str(video), work_dir=str(tmp_path / "work"), niche="Fashion", platform="Instagram Reels",
            brief="", contentGoal="", post_metadata=None, intent="my_video", source_type="upload", url=None,
            video_filename="video.mp4", user_id="u1"))

    return SimpleNamespace(go=go, user=user, calls=calls, cache=cache, added=added)


def test_unlimited_mode_deducts_nothing_never_exhausts_and_keeps_the_real_cost(job, redis, monkeypatch):
    monkeypatch.setattr(settings, "STUDIO_UNLIMITED", True)
    job.go()
    assert job.calls[0]["credit_checker"] is None  # no mid-pipeline checkpoint
    assert job.user.credits == 5.0  # no final deduction either
    assert job.cache["prysm:job:job-1"]["status"] == "complete"  # never credits_exhausted
    assert job.added[0].total_cost_usd == 1.0  # the real _cost is still saved for reporting
    assert float(redis.data[f"prysm:studio:spend:{datetime.now(timezone.utc):%Y%m%d}"]) == 1.0  # and counts against the daily cap
    assert int(redis.data["prysm:studio:running:u1"]) == 0 and int(redis.data[safety.GLOBAL_KEY]) == 0  # slots released


def test_without_the_flag_the_same_job_still_runs_out_of_credits(job, monkeypatch):
    monkeypatch.setattr(settings, "STUDIO_UNLIMITED", False)
    job.go()
    assert callable(job.calls[0]["credit_checker"])
    assert job.cache["prysm:job:job-1"]["status"] == "credits_exhausted"


def test_a_failed_job_releases_the_user_and_global_slots(job, redis, monkeypatch):
    async def boom(**kw):
        raise RuntimeError("vendor down")

    monkeypatch.setattr("app.studio.adapter.run_studio_analysis", boom)
    monkeypatch.setattr(settings, "STUDIO_UNLIMITED", True)
    job.go("job-2")
    assert job.cache["prysm:job:job-2"]["status"] == "failed"
    assert int(redis.data["prysm:studio:running:u1"]) == 0 and int(redis.data[safety.GLOBAL_KEY]) == 0


# ---------------------------------------------------------------- user payloads and registration

def profile(monkeypatch, unlimited):
    monkeypatch.setattr(settings, "STUDIO_UNLIMITED", unlimited)
    user = SimpleNamespace(id="u1", email="a@example.org", username="a", full_name="A", plan="free", credits=12.34, is_verified=True,
                           auth_provider="email", created_at=NOW)
    return run(auth.get_profile(current_user=user))


def test_auth_me_reports_the_top_plan_and_unlimited_credits_in_unlimited_mode(monkeypatch):
    me = profile(monkeypatch, True)
    assert me.plan == "creator" and me.credits == safety.UNLIMITED_CREDITS


def test_auth_me_is_unchanged_by_default(monkeypatch):
    me = profile(monkeypatch, False)
    assert me.plan == "free" and me.credits == 12.3


def register(monkeypatch, redis, i):
    monkeypatch.setattr(auth, "redis_client", redis)
    request = SimpleNamespace(headers={"x-forwarded-for": "203.0.113.7"}, client=SimpleNamespace(host="127.0.0.1"))
    body = auth.RegisterRequest(email=f"user{i}@example.org", username=f"user{i}", password="Str0ng!Passw0rd", full_name="U")
    return run(auth.register(body, request, RegDB()))


def test_the_sixth_registration_from_one_ip_in_an_hour_answers_429(redis, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "studio"); monkeypatch.setattr(settings, "DEMO_AUTO_VERIFY", True)
    monkeypatch.setattr(settings, "STUDIO_REGISTER_PER_IP_HOURLY", 5)
    for i in range(5):
        assert register(monkeypatch, redis, i)["email"] == f"user{i}@example.org"
    with pytest.raises(HTTPException) as e:
        register(monkeypatch, redis, 6)
    assert e.value.status_code == 429 and e.value.detail == safety.REGISTER_LIMIT_MESSAGE


def test_registration_is_not_limited_when_the_studio_engine_is_off(redis, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "legacy"); monkeypatch.setattr(settings, "STUDIO_REGISTER_PER_IP_HOURLY", 1)
    for i in range(3):
        register(monkeypatch, redis, i)
    assert redis.data == {}


def test_no_safety_net_message_mentions_credits_plans_or_upgrades():
    for message in (safety.DAILY_BUDGET_MESSAGE, safety.ALREADY_RUNNING_MESSAGE, safety.HOURLY_LIMIT_MESSAGE, safety.REGISTER_LIMIT_MESSAGE):
        assert not any(word in message.lower() for word in ("credit", "plan", "upgrade", "pricing", "pay"))
