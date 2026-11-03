"""Hackathon-built (Meta Global AI Developer Hackathon).

Offline tests (no network, no database, no Redis) for the engine switch in routers/prysm.py: the default is the
legacy engine, ANALYSIS_ENGINE=studio routes /prysm/analyse jobs to the Studio adapter, the baseline credit
deduction works unchanged with Studio's `_cost`, the status endpoint only gains "progress" additively, and the
Studio engine rejects videos over 90 seconds with HTTP 400.
"""
import asyncio
import shutil
import subprocess
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.core.config import Settings, settings
from app.routers import prysm as router
from app.studio.tests.fake_redis import FakeRedis


class FakeUser(SimpleNamespace):
    pass


class FakeResult:
    def __init__(self, user):
        self.user = user

    def scalar_one_or_none(self):
        return self.user

    def scalar_one(self):
        return self.user


class FakeSession:
    """Stands in for an AsyncSession: serves one user and records what is added."""
    def __init__(self, user, added):
        self.user, self.added = user, added

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        return FakeResult(self.user)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def rollback(self):
        pass


def fake_result(total_usd=0.1):
    return {"overallScore": 70, "scoreLabel": "Strong Content", "summary": "s", "topPriorities": [], "parameters": [],
            "frames": [{"timestamp": 0.5, "base64": "QQ=="}], "audioAnalysis": {"hasSpeech": True}, "transcript": "hello",
            "_cost": {"items": [], "total_usd": total_usd}}


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """The Studio safety net talks to Redis: give it an in-memory one (the baseline's own cache calls are faked per test)."""
    redis = FakeRedis()
    monkeypatch.setattr("app.core.cache.redis_client", redis)
    return redis


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Runs _run_analysis_task with a fake database, cache and both engines replaced by recorders."""
    user = FakeUser(id="u1", credits=100.0, plan=SimpleNamespace(value="free"))
    calls = {"legacy": [], "studio": []}
    cache, added = {}, []

    async def fake_cache_set(key, value, ttl=None):
        cache[key] = value

    async def legacy(**kw):
        calls["legacy"].append(kw)
        return fake_result()

    async def studio(**kw):
        calls["studio"].append(kw)
        return fake_result()

    monkeypatch.setattr("app.core.database.AsyncSessionLocal", lambda: FakeSession(user, added))
    monkeypatch.setattr(router, "cache_set", fake_cache_set)
    monkeypatch.setattr("app.services.prysm_analyse_service.run_full_analysis", legacy)
    monkeypatch.setattr("app.studio.adapter.run_studio_analysis", studio)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    def run(job_id="job-1"):
        asyncio.run(router._run_analysis_task(
            job_id=job_id, video_path=str(video), work_dir=str(tmp_path / "work"), niche="Fashion,Lifestyle",
            platform="Instagram Reels", brief="a description", contentGoal="grow", post_metadata=None, intent="my_video",
            source_type="upload", url=None, video_filename="video.mp4", user_id="u1"))

    return SimpleNamespace(run=run, user=user, calls=calls, cache=cache, added=added)


def test_the_default_engine_is_legacy_and_the_studio_settings_have_safe_defaults():
    fields = Settings.model_fields
    assert fields["ANALYSIS_ENGINE"].default == "legacy"
    assert fields["STUDIO_MAX_CONCURRENT"].default == 2 and fields["STUDIO_MAX_VIDEO_SECONDS"].default == 90
    assert fields["STUDIO_RETENTION_HOURS"].default == 24 and fields["STUDIO_WORK_DIR"].default == ""
    assert fields["DEMO_AUTO_VERIFY"].default is False and fields["STUDIO_UNLIMITED"].default is False
    assert "DEMO_START_CREDITS" not in fields


def test_legacy_engine_runs_the_baseline_analysis_only(harness, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "legacy")
    harness.run()
    assert len(harness.calls["legacy"]) == 1 and harness.calls["studio"] == []
    kw = harness.calls["legacy"][0]
    assert kw["niche"] == "Fashion,Lifestyle" and kw["brief"] == "a description" and kw["content_goal"] == "grow"
    assert "job_id" not in kw  # the baseline call is exactly what it was
    assert harness.cache["prysm:job:job-1"]["status"] == "complete"


def test_studio_engine_runs_the_adapter_with_the_same_arguments_plus_the_job_id(harness, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "studio")
    harness.run("job-7")
    assert len(harness.calls["studio"]) == 1 and harness.calls["legacy"] == []
    kw = harness.calls["studio"][0]
    assert {"video_path", "niche", "platform", "brief", "content_goal", "post_metadata", "intent", "source_type"} <= set(kw)
    assert kw["job_id"] == "job-7" and callable(kw["credit_checker"]) and kw["niche"] == "Fashion,Lifestyle"
    assert harness.cache["prysm:job:job-7"]["status"] == "complete"


def test_the_baseline_credit_deduction_works_unchanged_with_the_studio_result(harness, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "studio")
    harness.run()
    assert harness.user.credits == pytest.approx(round(100.0 - 0.1 * 31.44, 1))  # total_usd x 31.44 credits, as the baseline does
    saved = harness.added[0]
    assert saved.overall_score == 70 and saved.total_cost_usd == 0.1 and saved.first_frame_b64 == "QQ=="
    cached = harness.cache[f"prysm:result:{saved.id}"]
    assert not {"frames", "audioAnalysis", "transcript", "_cost"} & set(cached)  # the router still strips the heavy fields


def test_a_studio_failure_is_reported_as_a_failed_job_not_a_crash(harness, monkeypatch):
    async def boom(**kw):
        raise RuntimeError("Prysm supports videos up to 90 seconds.")

    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "studio")
    monkeypatch.setattr("app.studio.adapter.run_studio_analysis", boom)
    harness.run("job-9")
    assert harness.cache["prysm:job:job-9"] == {"status": "failed", "error": "Prysm supports videos up to 90 seconds."}


# ---------------------------------------------------------------- status: "progress" is additive

@pytest.fixture
def status_cache(monkeypatch):
    store = {}

    async def fake_get(key):
        return store.get(key)

    monkeypatch.setattr(router, "cache_get", fake_get)
    return store


def test_the_status_response_is_unchanged_in_legacy_mode_even_if_progress_exists(status_cache, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "legacy")
    status_cache["prysm:progress:j"] = {"watch": "running"}
    assert asyncio.run(router.get_analyse_status("j")) == {"status": "processing"}
    status_cache["prysm:job:j"] = {"status": "complete", "result": {"overallScore": 70}}
    assert asyncio.run(router.get_analyse_status("j")) == {"status": "complete", "result": {"overallScore": 70}}


def test_studio_mode_adds_progress_only_when_there_is_some(status_cache, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "studio")
    assert asyncio.run(router.get_analyse_status("j")) == {"status": "processing"}  # nothing written yet: same as the baseline
    status_cache["prysm:progress:j"] = {"watch": "ok", "speech": "running"}
    assert asyncio.run(router.get_analyse_status("j")) == {"status": "processing", "progress": {"watch": "ok", "speech": "running"}}
    status_cache["prysm:job:j"] = {"status": "failed", "error": "nope"}
    assert asyncio.run(router.get_analyse_status("j"))["error"] == "nope"
    status_cache["prysm:job:j"] = {"status": "credits_exhausted", "plan": "free"}
    out = asyncio.run(router.get_analyse_status("j"))
    assert out["status"] == "credits_exhausted" and out["plan"] == "free" and out["progress"]


# ---------------------------------------------------------------- the 90 s guard in /prysm/analyse

@pytest.fixture(scope="module")
def long_video(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    path = tmp_path_factory.mktemp("vid") / "long.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=1", "-t", "95",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)
    return path


class Reached(Exception):
    """Raised by the fake cache once the request has passed every guard."""


def call_analyse(video_path, monkeypatch):
    user = FakeUser(id=uuid.uuid4(), credits=10.0, is_verified=True)

    async def fake_user(token, db):
        return user

    async def fake_set(*a, **k):
        raise Reached()

    monkeypatch.setattr(router, "get_current_user_optional", fake_user)
    monkeypatch.setattr(router, "cache_set", fake_set)
    upload = UploadFile(file=open(video_path, "rb"), size=video_path.stat().st_size, filename="long.mp4")
    return asyncio.run(router.analyse_video(video=upload, url=None, niche="Fashion", platform="Instagram Reels", brief="", contentGoal="",
                                            intent="my_video", authorization="Bearer x", db=FakeSession(user, [])))


def test_studio_engine_rejects_a_95_second_video_with_http_400(long_video, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "studio")
    with pytest.raises(HTTPException) as e:
        call_analyse(long_video, monkeypatch)
    assert e.value.status_code == 400 and e.value.detail == "Prysm supports videos up to 90 seconds."


def test_the_legacy_engine_still_accepts_it_up_to_the_baselines_five_minutes(long_video, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "legacy")
    with pytest.raises(Reached):  # passed the duration guard and got as far as queueing the job
        call_analyse(long_video, monkeypatch)
