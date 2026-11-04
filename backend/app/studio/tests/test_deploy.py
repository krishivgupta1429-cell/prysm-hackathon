"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests for a public deployment: the production startup check (every problem named, no value ever printed, silent outside
production), the per-user daily analysis limit, CORS in production, and the database bootstrap states.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.studio import prod_check, safety
from app.studio.safety import Limits
from app.studio.tests.fake_redis import FakeRedis

NOW = datetime(2026, 11, 3, 12, 30, tzinfo=timezone.utc)
SECRET_A = "a" * 40
SECRET_B = "b" * 40


def good_env():
    return {
        "APP_ENV": "production", "SECRET_KEY": SECRET_A, "JWT_SECRET": SECRET_B,
        "DATABASE_URL": "postgresql+asyncpg://u:p@db.internal:5432/prysm", "REDIS_URL": "redis://default:p@redis.internal:6379",
        "FRONTEND_URL": "https://prysm.example-app.dev", "AI_PROVIDER": "meta", "META_API_KEY": "k", "GROQ_API_KEY": "k", "GEMINI_API_KEY": "k",
        "R2_ACCOUNT_ID": "a", "R2_ACCESS_KEY_ID": "b", "R2_SECRET_ACCESS_KEY": "c", "R2_BUCKET_NAME": "bucket",
        "DEMO_AUTO_VERIFY": "1", "STUDIO_DAILY_SPEND_CAP_USD": "10", "STUDIO_USER_HOURLY_LIMIT": "3", "STUDIO_USER_DAILY_LIMIT": "8",
        "TOKEN_ENCRYPTION_KEY": "x",
    }


def build(env, **over):
    env = {**env, **over}
    s = Settings(_env_file=None, **{k: v for k, v in env.items() if k in Settings.model_fields or k.lower() in Settings.model_fields})
    lookup = lambda n: env.get(n)
    return s, lookup


def names(errors):
    return {e.split(":")[0] for e in errors}


def test_a_complete_production_environment_has_no_errors():
    s, g = build(good_env())
    errors, _ = prod_check.problems(s, g)
    assert errors == []


def test_outside_production_nothing_is_checked():
    s, g = build({"APP_ENV": "development"})
    assert prod_check.problems(s, g) == ([], [])
    prod_check.enforce(s, g)   # does not raise


def test_every_missing_or_placeholder_variable_is_named_once_with_no_value():
    env = good_env()
    for k in ("SECRET_KEY", "JWT_SECRET", "DATABASE_URL", "REDIS_URL", "FRONTEND_URL", "META_API_KEY", "GROQ_API_KEY", "R2_BUCKET_NAME",
              "STUDIO_DAILY_SPEND_CAP_USD", "STUDIO_USER_HOURLY_LIMIT", "STUDIO_USER_DAILY_LIMIT", "TOKEN_ENCRYPTION_KEY"):
        env.pop(k)
    s, g = build(env, AUTO_EDIT_ENABLED="1")   # Auto-edit is off by default; the R2 variables are required once it is on
    bad = names(prod_check.problems(s, g)[0])
    assert {"SECRET_KEY", "JWT_SECRET", "DATABASE_URL", "REDIS_URL", "FRONTEND_URL", "META_API_KEY", "GROQ_API_KEY", "R2_BUCKET_NAME",
            "STUDIO_DAILY_SPEND_CAP_USD", "STUDIO_USER_HOURLY_LIMIT", "STUDIO_USER_DAILY_LIMIT", "TOKEN_ENCRYPTION_KEY"} <= bad
    errors = prod_check.problems(s, g)[0]
    assert len([e for e in errors if e.startswith("STUDIO_USER_DAILY_LIMIT")]) == 1   # named once
    with pytest.raises(RuntimeError) as e:
        prod_check.enforce(s, g)
    text = str(e.value)
    assert "SECRET_KEY" in text and "docs/deploy-checklist.md" in text


def test_the_error_never_contains_a_secret_value():
    s, g = build(good_env(), SECRET_KEY="change-me-in-production-" + "z" * 20, GROQ_API_KEY="")
    with pytest.raises(RuntimeError) as e:
        prod_check.enforce(s, g)
    assert "z" * 20 not in str(e.value)


@pytest.mark.parametrize("flag", ["DEV_BAKEOFF", "DEV_DINO", "STUDIO_EXPOSE_COST", "DEBUG_SAVE_INPUTS", "DEBUG"])
def test_dev_switches_refuse_to_start_in_production(flag):
    s, g = build(good_env(), **{flag: "1"})
    assert flag in names(prod_check.problems(s, g)[0])


def test_the_fixture_mode_of_instagram_refuses_to_start():
    s, g = build(good_env(), INSTAGRAM_FIXTURE="1")
    assert "INSTAGRAM_FIXTURE" in names(prod_check.problems(s, g)[0])


def test_cost_caps_must_be_chosen_on_purpose():
    env = good_env()
    env.pop("STUDIO_USER_DAILY_LIMIT")
    s, g = build(env)
    assert "STUDIO_USER_DAILY_LIMIT" in names(prod_check.problems(s, g)[0])   # the code default (0 = unlimited) is not accepted
    s, g = build(good_env(), STUDIO_DAILY_SPEND_CAP_USD="0")
    assert "STUDIO_DAILY_SPEND_CAP_USD" in names(prod_check.problems(s, g)[0])


def test_instagram_needs_a_real_https_callback_when_configured():
    s, g = build(good_env(), INSTAGRAM_APP_ID="123", INSTAGRAM_APP_SECRET="s",
                 INSTAGRAM_REDIRECT_URI="https://YOUR-BACKEND-URL.example.com/auth/instagram/callback")
    assert "INSTAGRAM_REDIRECT_URI" in names(prod_check.problems(s, g)[0])
    s, g = build(good_env(), INSTAGRAM_APP_ID="123", INSTAGRAM_APP_SECRET="s",
                 INSTAGRAM_REDIRECT_URI="https://api.prysm.example-app.dev/auth/instagram/callback")
    assert prod_check.problems(s, g)[0] == []


def test_nobody_could_verify_an_account_without_auto_verify_or_an_email_key():
    env = good_env(); env.pop("DEMO_AUTO_VERIFY")
    s, g = build(env, AUTO_EDIT_ENABLED="1")
    assert "DEMO_AUTO_VERIFY / BREVO_API_KEY" in names(prod_check.problems(s, g)[0])
    s, g = build(env, BREVO_API_KEY="k", AUTO_EDIT_ENABLED="0")
    assert prod_check.problems(s, g)[0] == []


def test_r2_is_not_required_when_auto_edit_is_off():
    env = good_env()
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"):
        env.pop(k)
    s, g = build(env, AUTO_EDIT_ENABLED="0")
    assert prod_check.problems(s, g)[0] == []


def test_cors_origins_must_be_https_in_production():
    s, g = build(good_env(), CORS_ORIGINS="https://a.dev,http://localhost:5173")
    assert "CORS_ORIGINS" in names(prod_check.problems(s, g)[0])


# ---------------------------------------------------------------- the per-user daily limit

def test_a_user_daily_limit_blocks_after_n_analyses_and_resets_the_next_utc_day():
    redis = FakeRedis()
    limits = Limits(user_hourly_limit=100, user_daily_limit=2)
    run = asyncio.run
    for _ in range(2):
        run(safety.admit(redis, "u1", limits, NOW))
        run(safety.release_user(redis, "u1"))
    with pytest.raises(safety.LimitExceeded) as e:
        run(safety.admit(redis, "u1", limits, NOW))
    assert e.value.status_code == 429 and "credit" not in e.value.message.lower()
    run(safety.admit(redis, "u2", limits, NOW))                         # another user is not affected
    run(safety.admit(redis, "u1", limits, NOW + timedelta(days=1)))     # a new day starts at zero


def test_no_daily_limit_by_default():
    assert Limits().user_daily_limit == 0
    assert Settings(_env_file=None).STUDIO_USER_DAILY_LIMIT == 0


def test_a_blocked_daily_attempt_does_not_leave_the_user_marked_as_running():
    redis = FakeRedis()
    limits = Limits(user_hourly_limit=100, user_daily_limit=1, user_max_concurrent=1)
    run = asyncio.run
    run(safety.admit(redis, "u1", limits, NOW)); run(safety.release_user(redis, "u1"))
    with pytest.raises(safety.LimitExceeded):
        run(safety.admit(redis, "u1", limits, NOW))
    # the failed attempt released the running slot, so the "already running" message is not what the next attempt gets
    with pytest.raises(safety.LimitExceeded) as e:
        run(safety.admit(redis, "u1", limits, NOW))
    assert e.value.message == safety.DAILY_LIMIT_MESSAGE


# ---------------------------------------------------------------- production CORS and database bootstrap

def test_production_cors_is_only_the_named_origins_and_development_keeps_the_local_ones():
    s, _ = build(good_env(), CORS_ORIGINS="https://extra.dev/")
    assert prod_check.cors_origins(s) == ["https://extra.dev", "https://prysm.example-app.dev"]
    d, _ = build({"APP_ENV": "development"})
    assert "http://localhost:5173" in prod_check.cors_origins(d)


def test_the_database_bootstrap_has_three_states_and_never_prints_the_url():
    from app.studio import db_bootstrap
    src = open(db_bootstrap.__file__).read()
    assert all(w in src for w in ("fresh", "versioned", "unversioned"))
    assert "print(settings.DATABASE_URL" not in src and "{settings.DATABASE_URL" not in src
    assert db_bootstrap._async_url("postgresql://u:p@h/db").startswith("postgresql+asyncpg://")


def test_auto_edit_defaults_to_off_so_r2_is_not_demanded_by_default():
    env = good_env()
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"):
        env.pop(k)
    s, g = build(env)
    assert s.AUTO_EDIT_ENABLED is False and prod_check.problems(s, g)[0] == []


def test_auto_edit_on_warns_when_its_files_would_live_on_the_servers_disk():
    s, g = build(good_env(), AUTO_EDIT_ENABLED="1")
    errors, warns = prod_check.problems(s, g)
    assert errors == [] and any(w.startswith("AUTO_EDIT_STORAGE") for w in warns)
    s, g = build(good_env(), AUTO_EDIT_ENABLED="1", AUTO_EDIT_STORAGE="s3")
    assert not any(w.startswith("AUTO_EDIT_STORAGE") for w in prod_check.problems(s, g)[1])


def test_auto_edit_s3_storage_accepts_the_checklist_r2_names(monkeypatch):
    from app.studio.autoedit import config as aec, storage as st
    env = {"AUTO_EDIT_STORAGE": "s3", "R2_ACCOUNT_ID": "acct123", "R2_BUCKET_NAME": "bkt", "R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s"}
    seen = {}

    class Fake:
        def __init__(self, bucket, endpoint, access_key, secret_key):
            seen.update(bucket=bucket, endpoint=endpoint)
    monkeypatch.setattr(aec, "env", lambda name, default=None: env.get(name, default))
    monkeypatch.setattr(st, "S3Storage", Fake)
    st.set_storage(None)
    try:
        st.get_storage()
    finally:
        st.set_storage(None)
    assert seen == {"bucket": "bkt", "endpoint": "https://acct123.r2.cloudflarestorage.com"}
