"""Built during the Meta Global AI Developer Hackathon (new work). DINOv2 wired into the real screens: embeddings at Instagram
import (non-blocking), the backfill, the analysis embedding and attention strip, "Closest to your Reel N", disconnect, and the
flag-off stubs. The model is mocked (no network, no GPU); database tests use the same throwaway Postgres as test_instagram.py
(IG_TEST_DATABASE_URL) and skip when none is reachable."""
import asyncio
import json
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select, text

from app.studio import config
from app.studio.dino import attention, client, wire
from app.studio.dino.client import DinoUnavailable
from app.studio.instagram import dna as D
from app.studio.instagram import hook as H
from app.studio.instagram import importer, oauth, personalise
from app.studio.instagram.models import IgAccount, IgReel, IgReelInsight
from app.studio.tests.test_dino import _emb_steps, _make_video, _unit
from app.studio.tests.test_instagram import FakeSource, Redis, aio, dbf as ig_dbf, fake_model, make_user


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(config, "_dotenv", lambda: {})
    for k in ("DINO_WIRE", "DINO_BACKEND", "DINO_MODAL_URL", "DINO_MODAL_TOKEN", "DINO_PLANNER_HINTS", "DINO_EDIT_RENDER_CHECK", "COACH_PERSONALISE"):
        monkeypatch.delenv(k, raising=False)


def vec(seed, base=None, noise=0.0):
    r = np.random.RandomState(seed)
    v = r.randn(768) if base is None else base + noise * r.randn(768)
    return _unit(v).astype(np.float32).tolist()


def fake_embedding(seed=1):
    return {"mean": vec(seed), "frame_count": 8, "usage": {"est_cost_usd": 0.0001, "frames": 8}}


# ------------------------------------------------------------------ pure functions

def test_compact_attention_is_labelled_estimate_has_no_percentages_and_keeps_marks(tmp_path):
    video = _make_video(tmp_path / "v.mp4", seconds=6)
    emb = _emb_steps(25, change_at=(8,), seed=3)
    strip = attention.attention_strip(video, embed=lambda jpgs: emb[:len(jpgs)] if len(jpgs) <= len(emb) else np.vstack([emb] * 3)[:len(jpgs)])
    out = wire.compact_attention(strip, {"frames": 25, "gpu_seconds": 0.1, "est_cost_usd": 0.00003, "cold": False})
    assert out["estimate"] is True and "estimate" in out["label"]
    for key in ("visual_change", "audio_energy", "audio_change"):
        vals = out[key]
        assert vals is not None and len(vals) == len(strip["times"])
        assert all(v is None or 0.0 <= v <= 1.0 for v in vals)
    assert out["reasons"] == {"visual_change": None, "audio_energy": None, "audio_change": None}
    assert isinstance(out["static_stretches"], list) and isinstance(out["loudness_dips"], list)
    blob = json.dumps(out)
    assert "%" not in blob and "percent" not in blob.lower() and "retention" not in blob.lower()
    assert "not measured viewer data" in out["note"]


def test_compact_attention_says_why_a_part_is_missing(tmp_path, monkeypatch):
    video = _make_video(tmp_path / "v.mp4", seconds=4, audio=False)
    monkeypatch.setenv("DINO_BACKEND", "off")
    out = wire.compact_attention(attention.attention_strip(video))
    assert out["visual_change"] is None and "off" in out["reasons"]["visual_change"].lower()
    assert out["audio_energy"] is None and out["reasons"]["audio_energy"] == "no audio track"
    assert out["static_stretches"] == [] and out["loudness_dips"] == []


def test_plain_lines_use_only_stored_numbers_and_say_nothing_for_missing_ones():
    r = {"insights": {"reach": 4210.0, "views": 6000.0, "reels_skip_rate": 28.0}, "vs_usual": {"label": "Reach 25% below your usual"},
         "watch_through": 0.41, "like_count": 120, "comments_count": None}
    lines = wire._plain_lines(r)
    assert lines[0] == "Reached 4,210 accounts (25% below your usual)"
    assert "Viewers watched on average 41% of it" in lines and "28% of views skipped early" in lines and "120 likes" in lines
    nothing = wire._plain_lines({"insights": {}, "vs_usual": None, "watch_through": None, "like_count": None, "comments_count": None})
    assert len(nothing) == 1 and "no numbers" in nothing[0]
    views_only = wire._plain_lines({"insights": {"views": 900.0}, "vs_usual": {"label": "Views about your usual"}, "watch_through": None})
    assert views_only[0] == "Had 900 views (about your usual)"


@aio
async def test_embedding_never_raises_when_off_failing_slow_or_disabled(tmp_path, monkeypatch):
    video = _make_video(tmp_path / "v.mp4", seconds=2)
    assert await wire.embed_for_import(video) is None                      # no service configured: off, no frames are even cut
    monkeypatch.setenv("DINO_BACKEND", "modal")
    monkeypatch.setenv("DINO_MODAL_URL", "https://example.invalid")
    monkeypatch.setenv("DINO_MODAL_TOKEN", "tok-secret-value")

    def boom(_v):
        raise DinoUnavailable("the DINOv2 service answered HTTP 503: down")
    monkeypatch.setattr(wire, "_embed_sync", boom)
    assert await wire.embed_for_import(video) is None
    monkeypatch.setattr(wire, "_embed_sync", lambda v: (_ for _ in ()).throw(RuntimeError("tok-secret-value leaked?")))
    assert await wire.embed_for_import(video) is None
    import time
    monkeypatch.setattr(wire, "_embed_sync", lambda v: time.sleep(0.5) or fake_embedding())
    assert await wire.embed_video(video, timeout=0.05) is None             # slow service: gives up, does not block
    monkeypatch.setattr(wire, "_embed_sync", lambda v: fake_embedding(5))
    assert (await wire.embed_for_import(video))["frame_count"] == 8
    monkeypatch.setenv("DINO_WIRE", "0")
    assert await wire.embed_for_import(video) is None and await wire.analyse_video(video) == {"embedding": None, "attention": None}


@aio
async def test_analyse_video_with_the_model_off_still_gives_the_audio_strip(tmp_path):
    video = _make_video(tmp_path / "v.mp4", seconds=4)
    out = await wire.analyse_video(video)
    assert out["embedding"] is None
    att = out["attention"]
    assert att["visual_change"] is None and att["audio_energy"] is not None and att["audio_change"] is not None


@aio
async def test_analysis_part_never_raises_and_never_waits_forever(monkeypatch):
    assert await wire.run_analysis_part(None) == {"embedding": None, "attention": None}

    async def bad():
        raise RuntimeError("x")
    assert await wire.run_analysis_part(asyncio.create_task(bad())) == {"embedding": None, "attention": None}
    monkeypatch.setattr(wire, "ANALYSIS_TIMEOUT_S", 0.05)

    async def slow():
        await asyncio.sleep(2)
    t = asyncio.create_task(slow())
    assert await wire.run_analysis_part(t) == {"embedding": None, "attention": None}
    await asyncio.sleep(0)
    assert t.cancelled() or t.done()


def test_stubs_are_flag_off_and_nothing_calls_them(tmp_path, monkeypatch):
    video = _make_video(tmp_path / "v.mp4", seconds=2)
    assert wire.planner_hints_for_edit(video) is None
    assert wire.edit_render_check(video, video) is None
    monkeypatch.setenv("DINO_PLANNER_HINTS", "1")
    hints = wire.planner_hints_for_edit(video)
    assert hints is not None and hints["label"] == "planner hints (estimate)"
    monkeypatch.setenv("DINO_EDIT_RENDER_CHECK", "1")
    monkeypatch.setenv("DINO_BACKEND", "off")
    assert wire.edit_render_check(video, video) is None                    # model off: soft None, not an exception
    root = Path(__file__).resolve().parents[2]                              # backend/app
    callers = [p.relative_to(root).as_posix() for p in root.rglob("*.py")
               if ("planner_hints_for_edit" in p.read_text() or "edit_render_check" in p.read_text())
               and p.name not in ("wire.py",) and not p.name.startswith("test_")]
    assert callers == []                                                   # the auto-edit engine is untouched
    for p in ("services/auto_edit_service.py",):
        assert "dino" not in (root / p).read_text().lower()


# ------------------------------------------------------------------ database

@asynccontextmanager
async def dbf():
    async with ig_dbf() as factory:
        from app.core.database import Base
        from app.models.dino_embedding import AnalysisVisualEmbedding, ReelVisualEmbedding
        async with factory() as s:
            conn = await s.connection()
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(lambda sc: Base.metadata.create_all(sc, tables=[ReelVisualEmbedding.__table__, AnalysisVisualEmbedding.__table__]))
            await s.commit()
        yield factory


async def add_reels(factory, uid, specs, fixture=True):
    """specs: [(media_id, days_ago, reach, watch_through, vs_index)] -> an account and its reels (Reel 1 = newest)."""
    async with factory() as db:
        await oauth.save_connection(db, uid, token="tok", ig_user_id="ig1", username="me", account_type="BUSINESS", expires_in=3600, scopes="a", is_fixture=fixture)
        acct = (await db.execute(select(IgAccount).where(IgAccount.user_id == uid))).scalar_one()
        now = datetime.now(timezone.utc)
        for media_id, days, reach, wt, idx in specs:
            r = IgReel(account_id=acct.id, media_id=media_id, permalink=f"https://x/{media_id}", posted_at=now - timedelta(days=days), caption="c",
                       duration_s=20.0, like_count=40, comments_count=3, analysis_level="full", hook_words="Stop scrolling", hook_words_status="transcribed",
                       performance_index=idx, performance_basis="reach", watch_through=wt, frames_used=6)
            db.add(r)
            await db.flush()
            for m, v in (("reach", reach), ("reels_skip_rate", 30.0 if reach else None)):
                db.add(IgReelInsight(reel_id=r.id, metric=m, value=v, available=v is not None))
        await db.commit()


async def make_analysis(factory, uid):
    aid = uuid.uuid4()
    async with factory() as db:
        await db.execute(text("insert into prysm_analyses (id, user_id) values (:i, :u)"), {"i": aid, "u": uid})
        await db.commit()
    return aid


@aio
async def test_closest_reel_ranks_own_reels_numbers_them_and_shows_real_numbers_in_words():
    from app.studio.dino import store
    q = vec(11)
    async with dbf() as factory:
        uid, other = await make_user(factory), await make_user(factory)
        await add_reels(factory, uid, [("a", 1, 5000.0, 0.62, 1.4), ("b", 5, 2000.0, 0.30, 0.6), ("c", 9, None, None, None), ("d", 12, 3000.0, 0.5, 1.0)])
        aid = await make_analysis(factory, uid)
        async with factory() as db:
            await store.save_analysis_embedding(db, aid, q, 8)
            await store.save_reel_embedding(db, uid, "a", vec(1), 8)                       # unrelated picture
            await store.save_reel_embedding(db, uid, "b", vec(0, np.array(q), 0.002), 8)   # nearly the same picture
            await store.save_reel_embedding(db, uid, "c", vec(0, np.array(q), 0.3), 8)
            await store.save_reel_embedding(db, uid, "gone", q, 8)                         # a reel that is no longer imported: ignored
            await store.save_reel_embedding(db, other, "x", q, 8)                           # someone else's: never shown
            out = await wire.closest_payload(db, uid, aid)
        assert out["found"] and out["available"] and out["connected"]
        assert [r["n"] for r in out["ranked"]] == [2, 3, 1]                                   # b is Reel 2, c Reel 3, a Reel 1; d has no embedding
        assert out["ranked"][0]["similarity"] > 0.99 > out["ranked"][1]["similarity"] > out["ranked"][2]["similarity"]
        assert out["own_reels"] == 4 and out["compared_with"] == 3 and out["missing_embeddings"] == 1
        assert out["small_sample"] is False and out["small_sample_note"] is None
        best = out["ranked"][0]
        assert [r["band"] for r in out["ranked"]] == ["looks very alike", "looks quite different", "looks quite different"]
        assert best["lines"][0] == "Reached 2,000 accounts (40% below your usual)"
        assert "Viewers watched on average 30% of it" in best["lines"] and best["permalink"] == "https://x/b"
        assert best["hookWords"] == "Stop scrolling"
        assert "pictures, not results" in out["note"]
        no_reach = out["ranked"][1]["lines"]                                                    # Reel 3: Instagram gave nothing
        assert not any("Reached" in l for l in no_reach)
        assert all("%" not in l or "watched" in l or "skipped" in l or "usual" in l for l in best["lines"])
        async with factory() as db:
            assert (await wire.closest_payload(db, other, aid)) == {"found": False}               # not their analysis
            assert (await wire.closest_payload(db, uid, uuid.uuid4())) == {"found": False}


@aio
async def test_closest_reel_with_fewer_than_three_reels_ranks_and_says_small_sample():
    from app.studio.dino import store
    async with dbf() as factory:
        uid = await make_user(factory)
        await add_reels(factory, uid, [("a", 1, 900.0, 0.5, 1.0), ("b", 4, 1100.0, 0.4, 1.2)])
        aid = await make_analysis(factory, uid)
        async with factory() as db:
            await store.save_analysis_embedding(db, aid, vec(3), 8)
            await store.save_reel_embedding(db, uid, "a", vec(3, np.array(vec(3)), 0.005), 8)
            await store.save_reel_embedding(db, uid, "b", vec(4), 8)
            out = await wire.closest_payload(db, uid, aid)
        assert out["available"] and out["small_sample"] is True and "fewer than 3" in out["small_sample_note"]
        assert [r["n"] for r in out["ranked"]] == [1, 2]


@aio
async def test_closest_reel_explains_every_unavailable_case():
    from app.studio.dino import store
    async with dbf() as factory:
        uid = await make_user(factory)
        aid = await make_analysis(factory, uid)
        async with factory() as db:
            out = await wire.closest_payload(db, uid, aid)
        assert out["available"] is False and out["connected"] is False and out["ranked"] == []
        await add_reels(factory, uid, [("a", 1, 900.0, 0.5, 1.0)])
        async with factory() as db:
            out = await wire.closest_payload(db, uid, aid)
        assert out["available"] is False and "no picture embedding" in out["reason"]            # analysed while the model was off
        async with factory() as db:
            await store.save_analysis_embedding(db, aid, vec(3), 8)
            out = await wire.closest_payload(db, uid, aid)
        assert out["available"] is False and "Compute picture matching" in out["reason"] and out["missing_embeddings"] == 1


class VideoSource(FakeSource):
    """Every reel downloads a tiny real video (like Instagram would); a failed download for ids in `novideo`."""
    def __init__(self, n=4, novideo=()):
        super().__init__(n)
        self.novideo = set(novideo)

    async def list_reels(self, count):
        reels = await super().list_reels(count)
        for r in reels:
            r["media_url"] = None if r["id"] in self.novideo else f"v://{r['id']}"
        return reels

    async def download(self, raw, dest):
        if not raw.get("media_url"):
            return False
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", dest], check=True)
        return True

    async def thumbnail(self, raw):
        return None


def patch_import(monkeypatch):
    async def fake_transcribe(video, tmp, client=None):
        return H.HookResult("no_speech")
    monkeypatch.setattr(H, "transcribe_hook", fake_transcribe)
    fake_model(monkeypatch)


async def do_import(factory, uid, source, n):
    async with factory() as db:
        await oauth.save_connection(db, uid, token="tok", ig_user_id="ig1", username="me", account_type="BUSINESS", expires_in=3600, scopes="a", is_fixture=True)
    return await importer.run_import(redis=Redis(), session_factory=factory, user_id=uid, count=n, job_id=uuid.uuid4().hex, source=source)


async def stored(factory, uid):
    from app.models.dino_embedding import ReelVisualEmbedding as T
    async with factory() as db:
        return sorted(m for (m,) in (await db.execute(select(T.media_id).where(T.user_id == uid))).all())


@aio
async def test_import_embeds_each_reel_and_a_reimport_drops_embeddings_of_reels_no_longer_imported(monkeypatch):
    patch_import(monkeypatch)
    seen = []

    async def fake_embed(video, *a, **k):
        assert Path(video).exists()                                           # runs while the temp video exists
        seen.append(video)
        return fake_embedding(len(seen))
    monkeypatch.setattr(wire, "embed_for_import", fake_embed)
    async with dbf() as factory:
        uid = await make_user(factory)
        summary = await do_import(factory, uid, VideoSource(4, novideo={"m1"}), 4)
        assert summary["status"] == "done" and summary["picture_embeddings"] == 3
        assert summary["picture_embeddings_usd"] == pytest.approx(0.0003)
        assert await stored(factory, uid) == ["m0", "m2", "m3"]               # the reel without a video link has none
        assert all(not Path(v).exists() for v in seen)                        # nothing kept
        summary = await importer.run_import(redis=Redis(), session_factory=factory, user_id=uid, count=2, job_id="j2", source=VideoSource(2))
        assert summary["status"] == "done"
        assert await stored(factory, uid) == ["m0", "m1"]                     # m1 now has a video; m2 and m3 are no longer imported: pruned


@aio
async def test_a_failing_or_off_model_never_blocks_the_import_and_stops_after_the_first_failure(monkeypatch):
    patch_import(monkeypatch)
    calls = []

    async def failing(video, *a, **k):
        calls.append(1)
        return None                                                            # what embed_for_import returns on any failure
    monkeypatch.setattr(wire, "embed_for_import", failing)
    async with dbf() as factory:
        uid = await make_user(factory)
        summary = await do_import(factory, uid, VideoSource(4), 4)
        assert summary["status"] == "done" and summary["imported"] == 4 and summary["picture_embeddings"] == 0
        assert len(calls) < 4 + 1 and len(calls) >= 1                          # the first failure stops later tries (concurrency 3 allows a few in flight)
        assert await stored(factory, uid) == []

        async def raising(video, *a, **k):
            raise RuntimeError("unexpected")
        monkeypatch.setattr(wire, "embed_for_import", raising)
        summary = await importer.run_import(redis=Redis(), session_factory=factory, user_id=uid, count=3, job_id="j3", source=VideoSource(3))
        assert summary["status"] == "done" and summary["imported"] == 3

        monkeypatch.undo()
        patch_import(monkeypatch)                                              # the real wire with DINO_BACKEND unset = off
        monkeypatch.setattr(config, "_dotenv", lambda: {})
        summary = await importer.run_import(redis=Redis(), session_factory=factory, user_id=uid, count=3, job_id="j4", source=VideoSource(3))
        assert summary["status"] == "done" and summary["picture_embeddings"] == 0


@aio
async def test_backfill_embeds_only_missing_reels_skips_no_video_and_reports_cost(monkeypatch):
    patch_import(monkeypatch)
    async with dbf() as factory:
        uid = await make_user(factory)
        await do_import(factory, uid, VideoSource(4, novideo={"m3"}), 4)         # model off during this import: no embeddings
        assert await stored(factory, uid) == []
        embedded = []

        async def fake_embed(video, *a, **k):
            embedded.append(video)
            return fake_embedding(len(embedded))
        monkeypatch.setattr(wire, "embed_video", fake_embed)
        # the model is off: the backfill says so and computes nothing
        out = await wire.backfill(session_factory=factory, user_id=uid, source=VideoSource(4, novideo={"m3"}))
        assert out["status"] == "error" and "off" in out["error"] and embedded == []
        monkeypatch.setattr(client, "backend_choice", lambda: "modal")
        out = await wire.backfill(session_factory=factory, user_id=uid, source=VideoSource(4, novideo={"m3"}))
        assert out["status"] == "done" and out["embedded"] == 3 and out["skipped_no_video"] == 1 and out["already"] == 0
        assert out["cost_usd"] == pytest.approx(0.0003) and len(embedded) == 3
        assert all(not Path(v).exists() for v in embedded)
        assert await stored(factory, uid) == ["m0", "m1", "m2"]
        out = await wire.backfill(session_factory=factory, user_id=uid, source=VideoSource(4, novideo={"m3"}))   # again: only m3 is missing and still has no video
        assert out["embedded"] == 0 and out["already"] == 3 and out["skipped_no_video"] == 1
        async with factory() as db:
            st = await wire.status(db, uid)
        assert st["reels"] == 4 and st["embedded"] == 3 and st["connected"] is True and st["missing"] == 0 and st["no_video"] == 1   # m3 never had a video file: not offered again
        out = await wire.backfill(session_factory=factory, user_id=uuid.uuid4(), source=VideoSource(1))
        assert out["status"] == "error"                                         # not connected


@aio
async def test_backfill_stops_at_its_cost_cap(monkeypatch):
    patch_import(monkeypatch)
    monkeypatch.setattr(client, "backend_choice", lambda: "modal")
    monkeypatch.setattr(wire, "BACKFILL_COST_CAP_USD", 0.0)

    async def fake_embed(video, *a, **k):
        return fake_embedding()
    monkeypatch.setattr(wire, "embed_video", fake_embed)
    async with dbf() as factory:
        uid = await make_user(factory)
        await do_import(factory, uid, VideoSource(3), 3)
        out = await wire.backfill(session_factory=factory, user_id=uid, source=VideoSource(3))
        assert out["embedded"] == 0 and out["failed"] == 3


@aio
async def test_disconnect_deletes_the_picture_embeddings():
    from app.studio.dino import store
    async with dbf() as factory:
        uid = await make_user(factory)
        await add_reels(factory, uid, [("a", 1, 900.0, 0.5, 1.0)])
        async with factory() as db:
            await store.save_reel_embedding(db, uid, "a", vec(1), 8)
        assert await stored(factory, uid) == ["a"]
        async with factory() as db:
            counts = await oauth.disconnect(db, uid)
        assert counts["picture_embeddings"] == 1 and await stored(factory, uid) == []


def test_the_closest_reels_thumbnail_follows_the_creator_dna_rules():
    import types
    from app.studio.dino import wire
    fixture, real = types.SimpleNamespace(is_fixture=True), types.SimpleNamespace(is_fixture=False)
    assert wire._thumb(fixture, "fixture://thumb/3") == "/instagram/fixture-thumb/3"
    assert wire._thumb(real, "fixture://thumb/3") is None
    assert wire._thumb(real, "https://cdn.example/t.jpg") == "https://cdn.example/t.jpg" and wire._thumb(real, None) is None
