"""Built during the Meta Global AI Developer Hackathon (new work). DINOv2 integration: curve, static segments, render
integrity, own-reel ranking, off/unavailable behaviour, audio curves. Embeddings are mocked: no network, no GPU. Videos are
tiny ffmpeg-generated clips; audio is synthetic (silence, tone, a sudden loud hit)."""
import json
import subprocess

import numpy as np
import pytest

from app.studio.dino import attention, audio as au, client, frames as fr, integrity, store, visual as vis
from app.studio.dino.client import DinoUnavailable

SR = 16000


def _unit(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def _emb_steps(n, change_at=(), dim=768, seed=0):
    """n unit vectors that stay constant, then jump to a new direction at each index in change_at."""
    rng = np.random.RandomState(seed)
    cur, out = rng.randn(dim), []
    for i in range(n):
        if i in change_at:
            cur = rng.randn(dim)
        out.append(cur)
    return _unit(np.array(out))


def _make_video(path, seconds=6, audio=True, size="160x284"):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=10:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return str(path)


# ------------------------------------------------------------------ visual change curve

def test_constant_frames_are_one_static_segment_and_flat_curve():
    c = vis.change_curve_from_embeddings(_emb_steps(24), fps=4)  # 6 s
    assert c["label"] == "visual change (estimate)" and c["available"]
    assert max(c["raw"]) < 1e-6 and max(c["scores"]) == 0.0  # a still video is not stretched to look busy
    assert c["static_segments"] == [{"start": 0.0, "end": 6.0, "duration": 6.0}]
    assert len(c["times"]) == len(c["scores"]) == 12


def test_jump_lands_in_the_right_half_second_and_scores_are_0_to_1():
    # frame 10 is the first frame of the new scene: the change is between 2.25 s and 2.5 s -> bin 4 (2.0-2.5 s)
    c = vis.change_curve_from_embeddings(_emb_steps(24, change_at={10}), fps=4)
    assert int(np.argmax(c["scores"])) == 4 and c["scores"][4] == 1.0
    assert all(0.0 <= s <= 1.0 for s in c["scores"])
    assert [s["start"] for s in c["static_segments"]] == [0.0, 2.5]  # 0-2.0 s and 2.5-6.0 s, the cut bin splits them


def test_short_still_run_is_not_a_static_segment():
    raw = np.array([0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.5])  # a 1.0 s run, then a 1.5 s run
    segs = vis.static_segments_from_raw(raw, 0.5, 0.02, 1.5)
    assert segs == [{"start": 1.5, "end": 3.0, "duration": 1.5}]


def test_two_frames_needed():
    with pytest.raises(ValueError):
        vis.change_curve_from_embeddings(_emb_steps(1))


def test_visual_change_curve_on_a_video_uses_the_embed_function(tmp_path):
    video = _make_video(tmp_path / "v.mp4", seconds=4, audio=False)
    seen = {}

    def embed(jpgs):
        seen["n"] = len(jpgs)
        assert all(j[:2] == b"\xff\xd8" for j in jpgs)
        return _emb_steps(len(jpgs), change_at={8})

    c = vis.visual_change_curve(video, embed=embed)
    assert seen["n"] == 16 and c["duration"] == pytest.approx(4.0, abs=0.2)
    assert c["scores"][3] == 1.0  # change between 1.75 and 2.0 s


# ------------------------------------------------------------------ off / unavailable

def test_client_off_raises_unavailable(monkeypatch):
    monkeypatch.setattr(client.config, "get_env", lambda name: {"DINO_BACKEND": "off"}.get(name))
    assert client.backend_choice() == "off"
    with pytest.raises(DinoUnavailable):
        client.embed_jpegs([b"\xff\xd8x"])
    assert client.health()["ok"] is False


def test_backend_default_is_off_without_url_and_token(monkeypatch):
    monkeypatch.setattr(client.config, "get_env", lambda name: None)
    assert client.backend_choice() == "off"
    monkeypatch.setattr(client.config, "get_env", lambda name: {"DINO_MODAL_URL": "https://x", "DINO_MODAL_TOKEN": "t"}.get(name))
    assert client.backend_choice() == "modal"


def _modal_env(monkeypatch):
    monkeypatch.setattr(client.config, "get_env", lambda name: {"DINO_BACKEND": "modal", "DINO_MODAL_URL": "https://x", "DINO_MODAL_TOKEN": "sekret"}.get(name))


def test_client_chunks_collects_usage_and_checks_shape(monkeypatch):
    _modal_env(monkeypatch)
    calls = []

    def post(url, token, payload, timeout):
        calls.append(len(payload["frames"]))
        e = _emb_steps(len(payload["frames"]))
        return {"available": True, "embeddings": e.tolist(), "gpu": "NVIDIA A10", "gpu_seconds": 1.0, "cold": len(calls) == 1}

    usage = {}
    out = client.embed_jpegs([b"\xff\xd8a"] * 100, usage=usage, post=post)
    assert out.shape == (100, 768) and calls == [48, 48, 4]
    assert usage["calls"] == 3 and usage["frames"] == 100 and usage["cold"] is True and usage["est_cost_usd"] == pytest.approx(3 * 0.000306)


def test_client_rejects_bad_replies_and_never_leaks_the_token(monkeypatch):
    _modal_env(monkeypatch)
    with pytest.raises(DinoUnavailable, match="unexpected shape"):
        client.embed_jpegs([b"a"], post=lambda *a: {"available": True, "embeddings": [[1.0, 2.0]]})
    with pytest.raises(DinoUnavailable, match="unit length"):
        client.embed_jpegs([b"a"], post=lambda *a: {"available": True, "embeddings": (np.ones((1, 768)) * 3).tolist()})

    def boom(url, token, payload, timeout):
        raise ConnectionError(f"connect failed for token {token}")

    with pytest.raises(DinoUnavailable) as e:
        client.embed_jpegs([b"a"], post=boom)
    assert "could not reach" in str(e.value) and "sekret" not in str(e.value)


def test_attention_strip_with_dino_off_still_gives_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(client.config, "get_env", lambda name: {"DINO_BACKEND": "off"}.get(name))
    video = _make_video(tmp_path / "v.mp4", seconds=4)
    s = attention.attention_strip(video)
    assert s["visual"]["available"] is False and "off" in s["visual"]["reason"] and s["visual_change"] is None
    assert s["audio_energy"] and len(s["audio_energy"]) == len(s["times"]) == 8
    assert s["planner"]["available"] == {"visual": False, "audio": True} and s["planner"]["static_stretches"] == []
    json.dumps(s)  # plain JSON


def test_attention_strip_with_mock_embeddings(tmp_path):
    video = _make_video(tmp_path / "v.mp4", seconds=6)
    s = attention.attention_strip(video, embed=lambda jpgs: _emb_steps(len(jpgs), change_at={12}))
    assert s["visual"]["available"] and len(s["visual_change"]) == len(s["times"]) == 12
    assert s["planner"]["static_stretches"] and s["label"].endswith("(estimate)")
    json.dumps(s)


def test_attention_strip_without_audio_says_so(tmp_path):
    video = _make_video(tmp_path / "v.mp4", seconds=3, audio=False)
    s = attention.attention_strip(video, embed=lambda jpgs: _emb_steps(len(jpgs)))
    assert s["audio_energy"] is None and s["energy"]["reason"] == "no audio track" and s["beats"]["available"] is False


# ------------------------------------------------------------------ render integrity

def test_kept_intervals_follow_the_cuts():
    edl = {"ops": [{"type": "cut_segment", "enabled": True, "params": {"start": 10.0, "end": 14.0}},
                   {"type": "cut_segment", "enabled": False, "params": {"start": 1.0, "end": 2.0}},
                   {"type": "zoom_punch", "params": {"at": 3}}]}
    assert integrity.kept_intervals(edl, 20.0) == [(0.0, 10.0, 0.0), (10.0, 16.0, 14.0)]
    assert integrity.kept_intervals(None, 20.0) == [(0.0, 20.0, 0.0)]


def test_plan_pairs_maps_edited_time_to_source_time():
    edl = {"ops": [{"type": "cut_segment", "params": {"start": 4.0, "end": 6.0}}]}
    segs = integrity.plan_pairs(edl, None, 10.0, 8.0, segment_s=2.0, samples=1)
    pairs = [p for s in segs for p in s["pairs"]]
    assert pairs[0] == (1.0, 1.0) and pairs[-1] == (7.0, 9.0)  # after the cut, edited 7.0 s is source 9.0 s
    explicit = integrity.plan_pairs(None, [(1.0, 5.0), {"out": 2.0, "src": 6.0}], 10, 10, samples=3)
    assert explicit == [{"start": 1.0, "end": 2.0, "pairs": [(1.0, 5.0), (2.0, 6.0)]}]


def test_score_segments_flags_below_threshold_only():
    segs = [{"start": 0, "end": 2, "pairs": [(0, 0), (1, 1)]}, {"start": 2, "end": 4, "pairs": [(2, 2), (3, 3)]}, {"start": 4, "end": 6, "pairs": [(4, 4)]}]
    out = integrity.score_segments(segs, [0.95, 0.9, 0.5, 0.6, None], threshold=0.8)
    assert [s["flagged"] for s in out] == [False, True, True]
    assert out[0]["similarity"] == pytest.approx(0.925) and out[1]["min_similarity"] == 0.5
    assert out[2]["similarity"] is None and out[2]["reason"] == "no frame could be read"
    # exactly at the threshold is not flagged
    assert integrity.score_segments(segs[:1], [0.8, 0.8], threshold=0.8)[0]["flagged"] is False


def test_render_integrity_flags_the_broken_segment(tmp_path):
    original = _make_video(tmp_path / "o.mp4", seconds=8, audio=False)
    edited = _make_video(tmp_path / "e.mp4", seconds=8, audio=False)
    bad = {"hit": False}

    def embed(jpgs):
        n = len(jpgs) // 2  # source frames first, edited frames second (as render_integrity sends them)
        src = _emb_steps(n)
        out = src.copy()
        out[3:6] = _unit(np.random.RandomState(9).randn(3, 768))  # edited samples 3-5 (segment 2) show something else
        bad["hit"] = True
        return np.concatenate([src, out])

    res = integrity.render_integrity(original, edited, embed=embed)
    assert bad["hit"] and res["available"] and len(res["segments"]) == 4
    assert [s["flagged"] for s in res["segments"]] == [False, True, False, False]
    assert res["flagged_count"] == 1 and res["ok"] is False and "cut-off face" in res["note"]
    ok = integrity.render_integrity(original, edited, embed=lambda j: np.concatenate([_emb_steps(len(j) // 2)] * 2))
    assert ok["ok"] and ok["mean_similarity"] == pytest.approx(1.0, abs=1e-6)


def test_render_integrity_off_raises_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(client.config, "get_env", lambda name: {"DINO_BACKEND": "off"}.get(name))
    v = _make_video(tmp_path / "o.mp4", seconds=4, audio=False)
    with pytest.raises(DinoUnavailable):
        integrity.render_integrity(v, v)


# ------------------------------------------------------------------ own-reel matching

def test_rank_nearest_orders_by_cosine_and_ties_by_id():
    q = _unit(np.random.RandomState(1).randn(768))
    a = q + 0.05 * np.random.RandomState(2).randn(768)
    b = 10 * q + 1.0 * np.random.RandomState(3).randn(768)  # between a and the random c
    c = np.random.RandomState(4).randn(768)
    rows = [("c", c.tolist()), ("b", b.tolist()), ("a", a.tolist()), ("a2", (a * 7).tolist())]  # scale must not matter
    out = store.rank_nearest(q, rows, k=3)
    assert [r["media_id"] for r in out] == ["a", "a2", "b"] and out[0]["similarity"] == out[1]["similarity"]
    assert out[0]["similarity"] > out[2]["similarity"]
    assert store.rank_nearest(q, [], k=3) == [] and store.rank_nearest(q, rows, k=0) == []


def test_mean_embedding_is_unit_and_rejects_degenerate_input():
    m = store.mean_embedding(_emb_steps(8, change_at={4}))
    assert m.shape == (768,) and np.linalg.norm(m) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        store.mean_embedding(np.zeros((0, 768)))
    with pytest.raises(ValueError):
        store.mean_embedding(np.array([[1.0, 0.0], [-1.0, 0.0]]))


def test_embed_reel_uses_up_to_8_even_frames(tmp_path):
    video = _make_video(tmp_path / "v.mp4", seconds=6, audio=False)
    got = {}
    res = store.embed_reel(video, embed=lambda j: got.setdefault("n", len(j)) and _emb_steps(len(j)))
    assert got["n"] == 8 and res["frame_count"] == 8 and len(res["mean"]) == 768


def test_embed_reel_off_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(client.config, "get_env", lambda name: {"DINO_BACKEND": "off"}.get(name))
    with pytest.raises(DinoUnavailable):
        store.embed_reel(_make_video(tmp_path / "v.mp4", seconds=3, audio=False))


# ------------------------------------------------------------------ audio curves (synthetic)

def _tone(seconds, amp=0.3, f=440.0):
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def test_silence():
    x = np.zeros(SR * 4, np.float32)
    e = au.energy_from_samples(x)
    assert set(e["db"]) == {au.DB_FLOOR} and max(e["scores"]) == 0.0 and len(e["db"]) == 8
    assert max(au.change_from_samples(x)["scores"]) == 0.0
    assert au.onsets_from_samples(x)["onsets"] == [] and au.onsets_from_samples(x)["beats"] == []


def test_steady_tone_is_loud_flat_and_unchanging():
    x = _tone(4, 0.3)
    e = au.energy_from_samples(x)
    assert e["db"][0] == pytest.approx(20 * np.log10(0.3 / np.sqrt(2)), abs=0.3)  # RMS of a sine
    assert max(e["db"]) - min(e["db"]) < 0.5
    ch = au.change_from_samples(x)
    assert max(ch["scores"]) < 0.1  # nothing changes; the floor stops it being stretched
    assert au.onsets_from_samples(x)["beats"] == []


def test_sudden_loud_hit_shows_in_energy_change_and_onsets():
    x = _tone(6, 0.02)
    hit = np.random.RandomState(0).randn(3200).astype(np.float32) * 0.5
    x[int(3.0 * SR):int(3.0 * SR) + 3200] += hit  # 0.2 s noise burst at 3.0 s -> bin 6
    e = au.energy_from_samples(x)
    assert int(np.argmax(e["db"])) == 6 and e["db"][6] - e["db"][2] > 15
    ch = au.change_from_samples(x)
    assert int(np.argmax(ch["scores"])) == 6 and ch["scores"][6] == 1.0
    assert max(ch["scores"][:5]) < 0.2
    on = au.onsets_from_samples(x)
    assert any(abs(t - 3.0) < 0.15 for t in on["onsets"]) and on["method"] == "librosa"


def test_click_track_has_beats_and_speech_like_noise_does_not():
    x = np.zeros(SR * 12, np.float32)
    for t in np.arange(0.5, 11.8, 0.5):  # 120 bpm
        i = int(t * SR)
        n = np.arange(400)
        x[i:i + 400] += (np.sin(2 * np.pi * 1000 * n / SR) * np.exp(-n / 80)).astype(np.float32)
    b = au.onsets_from_samples(x)
    assert b["beat_confidence"] > 0.5 and 110 <= b["tempo_bpm"] <= 130 and len(b["beats"]) > 15
    noise = (np.random.RandomState(0).randn(SR * 12) * 0.05).astype(np.float32)
    nb = au.onsets_from_samples(noise)
    assert nb["beats"] == [] and nb["tempo_bpm"] is None and "no steady beat" in nb["beat_note"]


def test_loudness_dips_finds_a_quiet_stretch():
    x = _tone(8, 0.3)
    x[int(3 * SR):int(5 * SR)] *= 0.02  # 2 s about 34 dB quieter
    dips = au.loudness_dips(au.energy_from_samples(x))
    assert len(dips) == 1 and dips[0]["start"] == 3.0 and dips[0]["end"] == 5.0
    assert au.loudness_dips({"available": False}) == []


def test_audio_curves_from_a_file_and_no_audio(tmp_path):
    v = _make_video(tmp_path / "a.mp4", seconds=3)
    assert au.audio_energy_curve(v)["available"] and au.audio_change_curve(v)["available"]
    n = _make_video(tmp_path / "n.mp4", seconds=3, audio=False)
    assert au.audio_energy_curve(n) == {"label": "audio energy (estimate)", "available": False, "reason": "no audio track"}
    assert au.beat_onsets(n)["available"] is False


def test_no_encodec_in_the_new_code():
    from pathlib import Path
    import app.studio.dino as pkg
    for p in Path(pkg.__file__).parent.glob("*.py"):
        src = p.read_text().lower()
        # the licence note may mention it; nothing may import or load it
        assert "import encodec" not in src and "encodec_24khz" not in src and "from encodec" not in src, p.name


# ------------------------------------------------------------------ router

def test_router_is_flag_gated_and_requires_login():
    import inspect
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.core.config import Settings
    from app import main
    from app.studio.dino import router as r
    assert Settings.model_fields["DEV_DINO"].default is False
    assert "if settings.DEV_DINO" in inspect.getsource(main)
    app = FastAPI()
    app.include_router(r.router)
    c = TestClient(app)
    assert c.get("/dino/status").status_code in (401, 403)
    assert c.post("/dino/curve", files={"video": ("v.mp4", b"x")}).status_code in (401, 403)
    assert c.get("/dino/nearest/00000000-0000-0000-0000-000000000000").status_code in (401, 403)


# ------------------------------------------------------------------ database (real Postgres + pgvector; skipped when unreachable)

def _run_db(coro_fn):
    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from app.core.config import settings

    async def go():
        engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
        try:
            async with engine.connect() as c:
                from sqlalchemy import text
                ok = (await c.execute(text("select to_regclass('public.reel_visual_embedding') is not null and to_regclass('public.prysm_analyses') is not null"))).scalar()
            if not ok:
                return "skip"
        except Exception:
            await engine.dispose()
            return "skip"
        try:
            async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as db:
                return await coro_fn(db)
        finally:
            await engine.dispose()

    out = asyncio.run(go())
    if out == "skip":
        pytest.skip("no Postgres with the DINOv2 tables (run alembic upgrade head)")
    return out


def test_nearest_reels_in_postgres_is_own_reels_only_and_joins_metrics_by_media_id():
    import uuid
    from sqlalchemy import delete, text
    from app.models.user import User
    from app.models.prysm_analysis import PrysmAnalysis

    base = _emb_steps(1, seed=5)[0]
    near = _unit(base + 0.05 * np.random.RandomState(1).randn(768))
    mid = _unit(base + 0.5 * np.random.RandomState(2).randn(768))
    far = _unit(np.random.RandomState(3).randn(768))
    tag = uuid.uuid4().hex[:8]

    async def go(db):
        u1 = User(email=f"dino_{tag}_a@example.test", username=f"dino_{tag}_a", hashed_pw="x")
        u2 = User(email=f"dino_{tag}_b@example.test", username=f"dino_{tag}_b", hashed_pw="x")
        db.add_all([u1, u2])
        await db.commit()
        uid1, uid2 = u1.id, u2.id  # plain values: the objects expire after a rollback
        created_ig = False
        try:
            await store.save_reel_embedding(db, uid1, "m_far", far.tolist(), 8)
            await store.save_reel_embedding(db, uid1, "m_near", near.tolist(), 8)
            await store.save_reel_embedding(db, uid1, "m_mid", mid.tolist(), 8)
            await store.save_reel_embedding(db, uid2, "m_other_user", base.tolist(), 8)  # identical, but someone else's
            await store.save_reel_embedding(db, uid1, "m_mid", mid.tolist(), 6)  # upsert, not a duplicate
            res = await store.nearest_reels(db, base, uid1, k=5)
            assert [r["media_id"] for r in res["reels"]] == ["m_near", "m_mid", "m_far"] and res["compared_with"] == 3
            assert res["reels"][0]["similarity"] > res["reels"][1]["similarity"] > res["reels"][2]["similarity"]
            # metrics: joined by media id string from the Instagram tables when they exist
            exists = (await db.execute(text("select to_regclass('public.ig_reel') is not null"))).scalar()
            if not exists:
                assert res["reels"][0]["metrics"] is None and "not present" in res["reels"][0]["metrics_note"]
                await db.execute(text("create table ig_account (id uuid primary key, user_id uuid)"))
                await db.execute(text("create table ig_reel (id uuid primary key, account_id uuid, media_id varchar(60), permalink text, posted_at timestamptz, "
                                      "duration_s float, like_count int, comments_count int, performance_index float, performance_basis varchar(20), watch_through float)"))
                created_ig = True
                acc = uuid.uuid4()
                await db.execute(text("insert into ig_account values (:a, :u)"), {"a": acc, "u": uid1})
                await db.execute(text("insert into ig_reel (id, account_id, media_id, like_count, performance_index, watch_through) values (:i, :a, 'm_near', 12, 1.5, NULL)"),
                                 {"i": uuid.uuid4(), "a": acc})
                await db.commit()
                res = await store.nearest_reels(db, base, uid1, k=2)
                assert res["reels"][0]["metrics"]["like_count"] == 12 and res["reels"][0]["metrics"]["watch_through"] is None  # stored None stays None
                assert res["reels"][1]["metrics"] is None and res["reels"][1]["metrics_note"] == "no stored metrics for this reel"
            # analysis embedding round trip
            a = PrysmAnalysis(user_id=uid1)
            db.add(a)
            await db.commit()
            assert await store.load_analysis_embedding(db, a.id) is None
            await store.save_analysis_embedding(db, a.id, base.tolist(), 8)
            await store.save_analysis_embedding(db, a.id, near.tolist(), 8)
            got = np.array(await store.load_analysis_embedding(db, a.id))
            assert got.shape == (768,) and float(got @ near) > 0.999
        finally:
            await db.rollback()
            if created_ig:
                await db.execute(text("drop table ig_reel"))
                await db.execute(text("drop table ig_account"))
            await db.execute(delete(User).where(User.id.in_([uid1, uid2])))  # cascades to the embeddings and the analysis
            await db.commit()
        return True

    assert _run_db(go)
