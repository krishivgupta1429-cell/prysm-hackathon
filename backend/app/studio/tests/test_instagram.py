"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests for the Instagram connect + Creator DNA feature: the OAuth state handling, token storage, import with missing
metrics, the performance index, the DNA edge cases, the fixture guard and the disconnect deletion. No network and no model
call (the model function is replaced). Tests that need a database use a throwaway Postgres database
(IG_TEST_DATABASE_URL, default the local stack on port 55436, database prysm_ig_test) and are skipped when none is reachable.
"""
import asyncio
import functools
import json
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.studio.instagram import dna as D
from app.studio.instagram import graph, importer, oauth, personalise
from app.studio.instagram import metrics as M
from app.studio.instagram.models import CreatorDna, IgAccount, IgReel, IgReelInsight
from app.studio.tests.fake_redis import FakeRedis


# ------------------------------------------------------------------ helpers

@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """Tests set their own environment: whatever backend/.env holds (INSTAGRAM_*, TOKEN_ENCRYPTION_KEY) must not leak in."""
    from app.studio import config
    monkeypatch.setattr(config, "_dotenv", lambda: {})


def aio(fn):
    """Run an async test with asyncio.run (works without the pytest-asyncio plugin)."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return asyncio.run(fn(*a, **k))
    return wrapper


class Redis(FakeRedis):
    async def getdel(self, key):
        v = self.data.pop(key, None)
        return None if v is None else str(v)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True


def reel(n, index=None, wt=None, fp=None, caption="c"):
    return {"performance_index": index, "watch_through": wt, "fingerprint": fp, "caption": caption,
            "posted_at": f"2026-10-{30 - n:02d}T10:00:00+00:00"}


def fp(hook="question", fmt="talking_head", pacing="fast", ost=True, audio="speech", topic="t"):
    return {"hook_type": hook, "format": fmt, "pacing": pacing, "audio_type": audio, "on_screen_text": ost, "topic": topic}


@pytest.fixture(autouse=True)
def ig_settings(monkeypatch):
    monkeypatch.setattr(settings, "INSTAGRAM_APP_ID", "app-id-for-tests")
    monkeypatch.setattr(settings, "INSTAGRAM_APP_SECRET", "secret-for-tests")
    monkeypatch.setattr(settings, "INSTAGRAM_REDIRECT_URI", "https://example.test/auth/instagram/callback")
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "FRONTEND_URL", "http://localhost:5177")
    monkeypatch.setattr(settings, "INSTAGRAM_FRONTEND_URL", "")
    monkeypatch.delenv("INSTAGRAM_FIXTURE", raising=False)
    monkeypatch.delenv("COACH_PERSONALISE", raising=False)


# ------------------------------------------------------------------ performance index, watch-through, medians

def test_performance_index_is_relative_to_the_median_not_the_mean():
    reels = [{"reach": v} for v in (1000, 1000, 1000, 1000, 9000)]      # one viral outlier
    idx, basis, med = M.performance_indices(reels)
    assert basis == "reach" and med == 1000
    assert idx == [1.0, 1.0, 1.0, 1.0, 9.0]


def test_performance_index_falls_back_to_views_and_leaves_missing_as_none():
    reels = [{"reach": None, "views": 200}, {"reach": None, "views": 100}, {"reach": None, "views": None}]
    idx, basis, med = M.performance_indices(reels)
    assert basis == "views" and med == 150
    assert idx[0] == pytest.approx(1.333, abs=1e-3) and idx[2] is None


def test_performance_index_none_when_no_reach_and_no_views():
    idx, basis, med = M.performance_indices([{"reach": None}, {"views": None}])
    assert idx == [None, None] and basis is None and med is None


def test_zero_is_a_real_value_but_does_not_become_the_median():
    assert M.account_median([0, 0, 10, 20]) == 15


def test_watch_through():
    assert M.watch_through(15000, 20) == 0.75
    assert M.watch_through(30000, 20) == 1.5            # looping reels can exceed 1
    assert M.watch_through(None, 20) is None
    assert M.watch_through(15000, None) is None
    assert M.watch_through(15000, 0) is None


def test_availability_and_missing_metrics():
    reels = [{"reach": 5, "reels_skip_rate": None}, {"reach": None, "reels_skip_rate": None}]
    assert M.availability(reels)["reach"] == 1
    assert "reels_skip_rate" in M.missing_metrics(reels) and "reach" not in M.missing_metrics(reels)


# ------------------------------------------------------------------ OAuth state and URLs

@aio
async def test_state_is_single_use_and_unknown_state_is_refused():
    r = Redis()
    state = await oauth.create_state(r, "user-1")
    assert r.ttl[f"ig_oauth_state:{state}"] == 600
    assert await oauth.consume_state(r, state) == "user-1"
    assert await oauth.consume_state(r, state) is None      # used up
    assert await oauth.consume_state(r, "made-up") is None
    assert await oauth.consume_state(r, None) is None


def test_authorize_url_asks_for_exactly_the_two_scopes():
    from urllib.parse import parse_qs, urlparse
    url = oauth.authorize_url("STATE")
    q = parse_qs(urlparse(url).query)
    assert urlparse(url).netloc == "www.instagram.com"
    assert q["scope"] == ["instagram_business_basic,instagram_business_manage_insights"]
    assert q["state"] == ["STATE"] and q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["https://example.test/auth/instagram/callback"]


@aio
async def test_callback_checks_state_before_exchanging_the_code():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(oauth.OAuthError):
            await oauth.complete_connection(None, Redis(), code="abc", state="forged", client=c)
    assert calls == []                                    # no request to Instagram at all


@aio
async def test_code_exchange_strips_the_hash_suffix_and_reads_both_response_shapes():
    seen = {}

    def handler(request):
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"data": [{"access_token": "short", "user_id": 77, "permissions": "x"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        got = await oauth.exchange_code("thecode#_", c)
    assert got["access_token"] == "short" and got["user_id"] == "77"
    assert "code=thecode&" in seen["body"] + "&" or seen["body"].endswith("code=thecode")
    assert "%23" not in seen["body"]


@aio
async def test_failed_long_lived_exchange_raises_instead_of_keeping_the_short_token():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={}))) as c:
        with pytest.raises(oauth.OAuthError):
            await oauth.to_long_lived("short", c)


# ------------------------------------------------------------------ fixture guard

def test_fixture_mode_is_refused_unless_every_condition_holds(monkeypatch):
    assert oauth.fixture_enabled("127.0.0.1") is False                       # flag off
    monkeypatch.setenv("INSTAGRAM_FIXTURE", "1")
    assert oauth.fixture_enabled("127.0.0.1") is True
    assert oauth.fixture_enabled("203.0.113.9") is False                      # not a local request
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert oauth.fixture_enabled("127.0.0.1") is False                        # production
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://app.example.com")
    assert oauth.fixture_enabled("127.0.0.1") is False                        # frontend is not on localhost


# ------------------------------------------------------------------ Graph client

def _insight_payload(**vals):
    return {"data": [{"name": k, "values": [{"value": v}]} for k, v in vals.items()]}


@aio
async def test_insights_empty_data_is_none_not_zero_and_real_zero_is_kept():
    def handler(request):
        return httpx.Response(200, json={"data": [
            {"name": "reach", "values": [{"value": 0}]},                      # a real zero
            {"name": "views", "values": []},                                  # nothing served
            {"name": "likes"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        out = await graph.InstagramSource("t", c).insights("m1")
    assert out["reach"] == 0.0 and out["views"] is None and out["likes"] is None and out["saved"] is None


@aio
async def test_insights_falls_back_to_one_call_per_metric_when_the_combination_is_refused():
    calls = []

    def handler(request):
        metric = request.url.params["metric"]
        calls.append(metric)
        if "," in metric:
            return httpx.Response(400, json={"error": {"code": 100, "message": "unknown error"}})
        if metric == "reels_skip_rate":
            return httpx.Response(400, json={"error": {"code": 100}})
        return httpx.Response(200, json=_insight_payload(**{metric: 5}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        out = await graph.InstagramSource("t", c).insights("m1")
    assert out["reach"] == 5.0 and out["reels_skip_rate"] is None
    assert len(calls) == 1 + len(M.METRICS)


@aio
async def test_rate_limit_and_bad_token_are_raised():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"error": {"code": 4}}))) as c:
        with pytest.raises(graph.RateLimited):
            await graph.InstagramSource("t", c).insights("m")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"error": {"code": 190}}))) as c:
        with pytest.raises(graph.TokenInvalid):
            await graph.InstagramSource("t", c).insights("m")


@aio
async def test_list_reels_keeps_only_reels_and_stops_at_n():
    items = [{"id": str(i), "media_product_type": "REELS" if i % 2 == 0 else "FEED", "media_type": "VIDEO"} for i in range(20)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": items}))) as c:
        got = await graph.InstagramSource("t", c).list_reels(4)
    assert [g["id"] for g in got] == ["0", "2", "4", "6"]


# ------------------------------------------------------------------ DNA edge cases (model replaced)

class Ask:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    async def __call__(self, content, max_tokens, spend, *, system=None):
        self.calls += 1
        spend.add(0.01)
        return json.dumps(self.reply) if not isinstance(self.reply, str) else self.reply


def full_reply():
    return {"summary": "Reels 1, 2 open with a question and did best. Reels 7, 8 are slow. Keep the question opening. Change the slow starts.",
            "strongest": [{"claim": "TOP reels Reels 1, 2 open with a question", "reels": [1, 2]}],
            "weakest": [{"claim": "Slow starts", "reels": [7, 8]}, {"claim": "Invented", "reels": [99]}, {"claim": "No citation", "reels": []}],
            "keep": [{"claim": "Question opening", "reels": [1]}], "change": [{"claim": "Slow starts", "reels": [8]}]}


def ten_reels(outlier=False):
    rs = []
    for n in range(1, 11):
        good = n <= 3
        idx = 2.0 if good else (0.5 if n >= 8 else 1.0)
        if outlier and n == 1:
            idx = 8.0
        rs.append(reel(n, idx, 0.7 if good else 0.3, fp("question" if good else "visual_only", pacing="fast" if good else "slow")))
    return rs


@aio
async def test_dna_with_too_few_reels_makes_no_model_call_and_claims_no_pattern(monkeypatch):
    ask = Ask(full_reply())
    monkeypatch.setattr(D, "_ask", ask)
    out = await D.build_dna([reel(n, 1.0, 0.5, fp()) for n in range(1, 4)], "reach", [], D.Spend(1))
    assert ask.calls == 0
    assert out["dna"]["mode"] == "descriptive" and "too few" in out["summary"].lower()
    assert "not proof" in out["summary"] and "3 most recent" in out["summary"]
    assert out["dna"]["keep"] == [] and out["dna"]["change"] == []


@aio
async def test_dna_with_no_insights_does_not_rank(monkeypatch):
    ask = Ask(full_reply())
    monkeypatch.setattr(D, "_ask", ask)
    out = await D.build_dna([reel(n, None, None, fp()) for n in range(1, 9)], None, ["reach", "views"], D.Spend(1))
    assert ask.calls == 0 and out["dna"]["mode"] == "descriptive"
    assert "reach" in out["summary"] and "views" in out["summary"]        # says which metrics are missing
    assert "too few reach or view numbers" in out["summary"]


@aio
async def test_full_dna_drops_invalid_and_uncited_claims_and_cleans_labels(monkeypatch):
    monkeypatch.setattr(D, "_ask", Ask(full_reply()))
    out = await D.build_dna(ten_reels(), "reach", [], D.Spend(1))
    dna = out["dna"]
    assert dna["mode"] == "full"
    assert [c["reels"] for c in dna["weakest"]] == [[7, 8]]                # reel 99 and the empty citation are gone
    assert dna["strongest"][0]["claim"].startswith("These reels open")     # internal TOP label removed
    assert all(c["reels"] for k in ("strongest", "weakest", "keep", "change") for c in dna[k])
    assert "10 most recent" in out["summary"] and "not proof" in out["summary"]


@aio
async def test_dna_summary_citing_a_reel_that_does_not_exist_is_replaced_by_one_built_from_the_claims(monkeypatch):
    bad = full_reply()
    bad["summary"] = "Reel 55 is the best."
    monkeypatch.setattr(D, "_ask", Ask(bad))
    out = await D.build_dna(ten_reels(), "reach", [], D.Spend(1))
    assert "Reel 55" not in out["summary"] and "Reels 1, 2" in out["summary"]


@aio
async def test_dna_flags_a_viral_outlier_and_says_medians_were_used(monkeypatch):
    monkeypatch.setattr(D, "_ask", Ask(full_reply()))
    out = await D.build_dna(ten_reels(outlier=True), "reach", [], D.Spend(1))
    assert out["dna"]["outlier"] is True and "medians" in out["summary"]
    out2 = await D.build_dna(ten_reels(), "reach", [], D.Spend(1))
    assert out2["dna"]["outlier"] is False


@aio
async def test_dna_survives_a_model_that_returns_garbage(monkeypatch):
    monkeypatch.setattr(D, "_ask", Ask("not json at all"))
    out = await D.build_dna(ten_reels(), "reach", [], D.Spend(1))
    assert out["dna"]["mode"] == "descriptive" and "re-import" in out["summary"]


def test_split_top_bottom_never_overlaps_and_needs_four_scored_reels():
    reels = [{"n": i, "score": s} for i, s in enumerate([3, 2, 1.5, 1, 1, 0.9, 0.4], 1)]
    top, bottom, _ = M.split_top_bottom(reels)
    assert {r["n"] for r in top}.isdisjoint({r["n"] for r in bottom}) and top[0]["n"] == 1 and bottom[-1]["n"] == 7
    assert M.split_top_bottom(reels[:3]) == ([], [], False)


def test_cost_cap_stops_fingerprints_but_keeps_room_for_the_summary():
    s = D.Spend(1.0)
    assert s.room_for_fingerprint()
    s.add(0.9)
    assert not s.room_for_fingerprint()


def test_fingerprint_is_normalised_to_allowed_values():
    f = D.normalise_fingerprint({"hook_type": "Bold Claim", "format": "nonsense", "pacing": "FAST", "audio_type": "??", "on_screen_text": "yes", "topic": "x" * 200})
    assert f["hook_type"] == "bold_claim" and f["format"] == "other" and f["pacing"] == "fast" and f["audio_type"] == "unknown"
    assert f["on_screen_text"] is True and len(f["topic"]) == 60
    assert D.normalise_fingerprint(None) is None


# ------------------------------------------------------------------ personalisation text

class FakeDna:
    def __init__(self, mode="full"):
        self.sample_size = 10
        self.dna = {"mode": mode, "strongest": [{"claim": "Question openings", "reels": [1, 2]}], "weakest": [{"claim": "Slow starts", "reels": [8]}],
                    "keep": [{"claim": "Keep questions", "reels": [1]}], "change": [{"claim": "Fix slow starts", "reels": [8]}], "top": [1, 2], "bottom": [8, 9]}


class FakeAcct:
    is_fixture = False


def test_addendum_is_empty_without_dna_or_when_switched_off_or_not_full(monkeypatch):
    assert personalise.addendum(None) == ""
    assert personalise.addendum({"dna": None, "account": FakeAcct(), "reels": []}) == ""
    assert personalise.addendum({"dna": FakeDna("descriptive"), "account": FakeAcct(), "reels": []}) == ""
    data = {"dna": FakeDna(), "account": FakeAcct(), "reels": []}
    text_on = personalise.addendum(data)
    assert text_on.startswith("\n\nCREATOR DNA ADDENDUM") and "Question openings (Reels 1, 2)" in text_on and "not proof" in text_on
    monkeypatch.setenv("COACH_PERSONALISE", "0")
    assert personalise.addendum(data) == ""


def test_addendum_marks_fixture_data():
    a = FakeAcct()
    a.is_fixture = True
    assert "TEST DATA" in personalise.addendum({"dna": FakeDna(), "account": a, "reels": []})


def test_compare_says_which_group_the_new_video_resembles():
    reels = [{"n": 1, "fingerprint": fp("question")}, {"n": 2, "fingerprint": fp("question")},
             {"n": 8, "fingerprint": fp("visual_only")}, {"n": 9, "fingerprint": fp("visual_only")}]
    rows = personalise.compare(fp("question", pacing="fast"), reels, [1, 2], [8, 9])
    hook = next(r for r in rows if r["field"] == "hook_type")
    assert hook["verdict"] == "top" and hook["top_match"] == 2 and hook["bottom_match"] == 0 and hook["top_reels"] == [1, 2]
    assert personalise.compare(None, reels, [1, 2], [8, 9]) == []


def test_baseline_prompt_is_unchanged_when_no_addendum():
    import inspect
    from app.services import prysm_analyse_service as svc
    assert inspect.signature(svc.run_full_analysis).parameters["creator_dna_addendum"].default == ""


# ------------------------------------------------------------------ database tests

DB_URL = os.environ.get("IG_TEST_DATABASE_URL", "postgresql+asyncpg://prysm@127.0.0.1:55436/prysm_ig_test")
ADMIN_URL = DB_URL.rsplit("/", 1)[0] + "/postgres"


@asynccontextmanager
async def dbf():
    """Yields a session factory on a throwaway database (created on first use); skips the test when none is reachable."""
    engine = None
    try:
        admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        async with admin.connect() as c:
            if not (await c.execute(text("SELECT 1 FROM pg_database WHERE datname='prysm_ig_test'"))).scalar():
                await c.execute(text("CREATE DATABASE prysm_ig_test"))
        await admin.dispose()
        engine = create_async_engine(DB_URL)
        from app.core.database import Base
        from app.models.user import User
        async with engine.begin() as conn:
            await conn.run_sync(lambda sc: Base.metadata.create_all(sc, tables=[
                User.__table__, IgAccount.__table__, IgReel.__table__, IgReelInsight.__table__, CreatorDna.__table__]))
            await conn.execute(text("ALTER TABLE ig_reel ADD COLUMN IF NOT EXISTS analysis_level VARCHAR(20)"))   # older throwaway database
    except Exception as e:  # no database reachable
        pytest.skip(f"no test database: {type(e).__name__}")
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def make_user(factory):
    from app.models.user import User
    uid = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=uid, email=f"{uid.hex[:8]}@example.org", username=f"u{uid.hex[:8]}", hashed_pw="x", is_verified=True))
        await db.commit()
    return uid


class FakeSource:
    is_fixture = True

    def __init__(self, n=8, missing=()):
        self.n, self.missing = n, set(missing)
        self.downloads = []

    async def aclose(self):
        pass

    async def list_reels(self, count):
        now = datetime.now(timezone.utc)
        return [{"id": f"m{i}", "caption": f"cap {i}", "permalink": f"https://x/{i}", "timestamp": (now - timedelta(days=i + 3)).isoformat(),
                 "like_count": 10 + i, "comments_count": i, "media_url": None, "thumbnail_url": "t"} for i in range(min(count, self.n))]

    async def insights(self, media_id):
        i = int(media_id[1:])
        out = {m: None for m in M.METRICS}
        if i == 0:                      # a fresh reel: nothing served yet
            return out
        out.update(reach=1000.0 + 100 * i, views=1500.0 + 100 * i, likes=50.0, ig_reels_avg_watch_time=8000.0, reels_skip_rate=0.4)
        for m in self.missing:
            out[m] = None
        return out

    async def download(self, reel, dest):
        self.downloads.append(dest)
        return False                    # like licensed audio: no media_url

    async def thumbnail(self, reel):
        return None


def fake_model(monkeypatch):
    async def fake_fp(blocks, caption, spend, level="full"):
        spend.add(0.002)
        return fp()
    monkeypatch.setattr(D, "fingerprint_reel", fake_fp)
    monkeypatch.setattr(D, "_ask", Ask({   # reels here have no video, so the claims are only about reach (hook/pacing would be ungrounded)
        "summary": "Reels 7, 8 reached the most people. Reels 2, 3 reached the fewest.",
        "strongest": [{"claim": "These reels reached the most people", "reels": [7, 8]}],
        "weakest": [{"claim": "These reels reached the fewest people", "reels": [2, 3]}],
        "keep": [{"claim": "Keep what the top reels did", "reels": [8]}], "change": [{"claim": "Rework the weakest reels", "reels": [2]}]}))


async def run(factory, uid, source, count=8):
    job_id = uuid.uuid4().hex
    redis = Redis()
    async with factory() as db:
        await oauth.save_connection(db, uid, token="tok", ig_user_id="ig1", username="me", account_type="BUSINESS", expires_in=3600 * 24 * 60,
                                    scopes="a,b", is_fixture=True)
    return await importer.run_import(redis=redis, session_factory=factory, user_id=uid, count=count, job_id=job_id, source=source), redis


@aio
async def test_token_is_stored_where_the_baseline_stores_it_and_expiry_in_ig_account():
    async with dbf() as session_factory:
        from app.models.user import User
        uid = await make_user(session_factory)
        async with session_factory() as db:
            acct = await oauth.save_connection(db, uid, token="long-lived", ig_user_id="ig9", username="creator", account_type="MEDIA_CREATOR",
                                               expires_in=5183944, scopes="instagram_business_basic,instagram_business_manage_insights")
            user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
            assert user.instagram_access_token == "long-lived" and user.instagram_user_id == "ig9" and user.instagram_username == "creator"
            assert acct.token_expires_at - datetime.now(timezone.utc) > timedelta(days=59)


@aio
async def test_a_token_close_to_expiry_is_refreshed_and_a_fresh_one_is_not(monkeypatch):
    async with dbf() as session_factory:
        from app.models.user import User
        uid = await make_user(session_factory)
        refreshed = []

        async def fake_refresh(token, client=None):
            refreshed.append(token)
            return "renewed", 5183944

        monkeypatch.setattr(oauth, "refresh_long_lived", fake_refresh)
        async with session_factory() as db:
            await oauth.save_connection(db, uid, token="old", ig_user_id="ig1", username="a", account_type=None, expires_in=3600 * 24 * 60, scopes="s")
            user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
            assert await oauth.ensure_fresh_token(db, user) == "old" and refreshed == []          # 60 days left
            acct = await oauth.get_account(db, uid)
            acct.token_expires_at = datetime.now(timezone.utc) + timedelta(days=5)
            acct.token_refreshed_at = datetime.now(timezone.utc) - timedelta(days=30)
            await db.commit()
            assert await oauth.ensure_fresh_token(db, user) == "renewed" and refreshed == ["old"]


@aio
async def test_import_with_missing_metrics_stores_none_not_zero_and_never_keeps_videos(monkeypatch):
    async with dbf() as session_factory:
        fake_model(monkeypatch)
        uid = await make_user(session_factory)
        src = FakeSource(n=8, missing=("ig_reels_avg_watch_time", "reels_skip_rate"))
        before = set(Path(tempfile.gettempdir()).glob("ig_reel_*"))
        summary, redis = await run(session_factory, uid, src)
        assert summary["status"] == "done" and summary["imported"] == 8
        assert set(summary["missing_metrics"]) >= {"ig_reels_avg_watch_time", "reels_skip_rate", "ig_reels_video_view_total_time"}
        assert set(Path(tempfile.gettempdir()).glob("ig_reel_*")) == before                       # temp folders removed
        async with session_factory() as db:
            acct = await oauth.get_account(db, uid)
            reels = (await db.execute(select(IgReel).where(IgReel.account_id == acct.id))).scalars().all()
            assert len(reels) == 8
            fresh = next(r for r in reels if r.media_id == "m0")
            assert fresh.performance_index is None and fresh.watch_through is None and "no insights" in fresh.note
            assert all(r.watch_through is None for r in reels)                                    # no duration + no watch time: not invented
            rows = (await db.execute(select(IgReelInsight).where(IgReelInsight.reel_id == fresh.id))).scalars().all()
            assert len(rows) == len(M.METRICS) and all(r.value is None and r.available is False for r in rows)
            typical = next(r for r in reels if r.media_id == "m4")
            assert typical.performance_index == pytest.approx(1.0, abs=0.2) and typical.performance_basis == "reach"
            dna = (await db.execute(select(CreatorDna).where(CreatorDna.account_id == acct.id))).scalar_one()
            assert dna.dna["mode"] == "full" and dna.sample_size == 8 and dna.metrics_available["reach"] == 7
            assert acct.last_import_summary["imported"] == 8


@aio
async def test_reimport_replaces_rather_than_accumulates(monkeypatch):
    async with dbf() as session_factory:
        fake_model(monkeypatch)
        uid = await make_user(session_factory)
        await run(session_factory, uid, FakeSource(n=8))
        redis = Redis()
        await importer.run_import(redis=redis, session_factory=session_factory, user_id=uid, count=6, job_id="j2", source=FakeSource(n=8))
        async with session_factory() as db:
            acct = await oauth.get_account(db, uid)
            assert len((await db.execute(select(IgReel).where(IgReel.account_id == acct.id))).scalars().all()) == 6
            assert len((await db.execute(select(CreatorDna).where(CreatorDna.account_id == acct.id))).scalars().all()) == 1


@aio
async def test_rate_limit_keeps_what_was_read_and_says_so(monkeypatch):
    async with dbf() as session_factory:
        fake_model(monkeypatch)
        uid = await make_user(session_factory)

        class Limited(FakeSource):
            async def insights(self, media_id):
                if int(media_id[1:]) >= 3:
                    raise graph.RateLimited("slow down")
                return await super().insights(media_id)

        summary, _ = await run(session_factory, uid, Limited(n=8))
        assert summary["status"] == "done" and summary["rate_limited"] is True and summary["imported"] == 8


@aio
async def test_only_one_import_runs_per_user():
    r = Redis()
    first, _ = await importer.start_import(r, "u1", 10)
    second, why = await importer.start_import(r, "u1", 10)
    assert first and second is None and "already running" in why


def test_count_is_clamped_to_5_30():
    assert importer.clamp_count(1) == 5 and importer.clamp_count(99) == 30 and importer.clamp_count("x") == 10 and importer.clamp_count(12) == 12


@aio
async def test_disconnect_deletes_the_token_and_all_derived_data(monkeypatch):
    async with dbf() as session_factory:
        from app.models.user import User
        fake_model(monkeypatch)
        uid, other = await make_user(session_factory), await make_user(session_factory)
        await run(session_factory, uid, FakeSource(n=8))
        await run(session_factory, other, FakeSource(n=6))
        async with session_factory() as db:
            counts = await oauth.disconnect(db, uid)
            assert counts["reels"] == 8 and counts["insights"] == 8 * len(M.METRICS)
        async with session_factory() as db:
            assert await oauth.get_account(db, uid) is None
            user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
            assert user.instagram_access_token is None and user.instagram_user_id is None and user.instagram_username is None
            # nothing of this user is left, and the other user's data is untouched
            left = (await db.execute(text("SELECT (SELECT count(*) FROM creator_dna WHERE user_id=:u), (SELECT count(*) FROM ig_account WHERE user_id=:u)"), {"u": uid})).one()
            assert tuple(left) == (0, 0)
            other_acct = await oauth.get_account(db, other)
            assert len((await db.execute(select(IgReel).where(IgReel.account_id == other_acct.id))).scalars().all()) == 6
            orphan = (await db.execute(text("SELECT count(*) FROM ig_reel_insight i LEFT JOIN ig_reel r ON r.id=i.reel_id WHERE r.id IS NULL"))).scalar()
            assert orphan == 0


@aio
async def test_connecting_a_different_account_deletes_the_old_accounts_derived_data(monkeypatch):
    async with dbf() as session_factory:
        fake_model(monkeypatch)
        uid = await make_user(session_factory)
        await run(session_factory, uid, FakeSource(n=8))
        async with session_factory() as db:
            await oauth.save_connection(db, uid, token="t2", ig_user_id="another", username="b", account_type=None, expires_in=100, scopes="s", is_fixture=True)
            acct = await oauth.get_account(db, uid)
            assert acct.ig_user_id == "another"
            assert (await db.execute(select(IgReel).where(IgReel.account_id == acct.id))).scalars().all() == []
