"""Reel embeddings for storage and own-reel matching.

    embed_reel(media)                              -> {"mean", "frame_count", "usage"}   (up to 8 evenly spaced frames)
    nearest_reels(db, vector, user_id, k=5)        -> ranked OWN reels, with the metrics already stored for each
    rank_nearest(query, rows, k)                   -> pure ranking by cosine similarity

Own reels only: the query is always filtered by user_id. Metrics are never computed or guessed here: they are read from
the Instagram tables by media id string at query time (raw SQL, so this package does not import those models). If those
tables are missing, or a reel has no row, the reel is still listed with metrics=None and a plain reason.

Honest limit: "looks similar" is a statement about pictures. Two reels that look alike can perform very differently.
"""
import uuid

import numpy as np

from app.studio.dino import frames as fr

LABEL = "looks similar to your own reel (estimate)"
NOTE = ("Ranked by how similar the pictures look (cosine similarity of DINOv2 frame embeddings). Similar look does not "
        "mean similar results: the numbers shown are that reel's own stored metrics.")
METRIC_COLUMNS = ("permalink", "posted_at", "duration_s", "like_count", "comments_count", "performance_index",
                  "performance_basis", "watch_through")


def mean_embedding(frame_emb) -> np.ndarray:
    """L2-normalised mean of unit frame vectors, float64 (n_dims,)."""
    a = np.asarray(frame_emb, dtype=np.float64)
    if a.ndim != 2 or a.shape[0] == 0:
        raise ValueError("need a non-empty (n, dim) array")
    a = a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-12)
    m = a.mean(axis=0)
    n = np.linalg.norm(m)
    if n < 1e-12:
        raise ValueError("the mean vector is zero")
    return m / n


def embed_reel(media: str, embed=None, n: int = 8) -> dict:
    """Embed up to `n` evenly spaced frames of a video file; returns the mean vector for storage.
    Raises DinoUnavailable when the model service is off or fails; never returns a made-up vector."""
    usage: dict = {}
    if embed is None:
        from app.studio.dino import client
        embed = lambda jpgs: client.embed_jpegs(jpgs, usage=usage)
    sampled = fr.sample_even(media, n)
    emb = embed([j for _, j in sampled])
    return {"mean": mean_embedding(emb).astype(np.float32).tolist(), "frame_count": len(sampled), "usage": usage}


def rank_nearest(query, rows: list[tuple[str, list[float]]], k: int = 5) -> list[dict]:
    """rows = [(media_id, vector)] -> the top k by cosine similarity: [{"media_id", "similarity"}], best first."""
    q = np.asarray(query, dtype=np.float64)
    q = q / max(np.linalg.norm(q), 1e-12)
    scored = []
    for media_id, vec in rows:
        v = np.asarray(vec, dtype=np.float64)
        scored.append({"media_id": media_id, "similarity": round(float(np.dot(q, v / max(np.linalg.norm(v), 1e-12))), 4)})
    scored.sort(key=lambda r: (-r["similarity"], r["media_id"]))
    return scored[:max(0, int(k))]


# ------------------------------------------------------------------ database

async def save_reel_embedding(db, user_id, media_id: str, mean: list[float], frame_count: int) -> None:
    from sqlalchemy.dialects.postgresql import insert
    from app.models.dino_embedding import ReelVisualEmbedding as T
    stmt = insert(T).values(id=uuid.uuid4(), user_id=user_id, media_id=str(media_id), mean_vector=mean, frame_count=int(frame_count))
    stmt = stmt.on_conflict_do_update(constraint="uq_reel_visual_embedding_user_media",
                                      set_={"mean_vector": stmt.excluded.mean_vector, "frame_count": stmt.excluded.frame_count})
    await db.execute(stmt)
    await db.commit()


async def save_analysis_embedding(db, analysis_id, mean: list[float], frame_count: int) -> None:
    from sqlalchemy.dialects.postgresql import insert
    from app.models.dino_embedding import AnalysisVisualEmbedding as T
    stmt = insert(T).values(analysis_id=analysis_id, mean_vector=mean, frame_count=int(frame_count))
    stmt = stmt.on_conflict_do_update(index_elements=["analysis_id"],
                                      set_={"mean_vector": stmt.excluded.mean_vector, "frame_count": stmt.excluded.frame_count})
    await db.execute(stmt)
    await db.commit()


async def load_analysis_embedding(db, analysis_id) -> list[float] | None:
    from sqlalchemy import select
    from app.models.dino_embedding import AnalysisVisualEmbedding as T
    row = (await db.execute(select(T.mean_vector).where(T.analysis_id == analysis_id))).first()
    return None if row is None else [float(x) for x in row[0]]


async def ig_metrics_lookup(db, user_id, media_ids: list[str]) -> tuple[dict, str | None]:
    """({media_id: {metric: value}}, reason). Reads the Instagram branch's ig_reel / ig_account tables by media id string;
    (.., reason) with an empty dict when they do not exist. Values are exactly what is stored (None stays None)."""
    from sqlalchemy import text
    if not media_ids:
        return {}, None
    exists = (await db.execute(text("select to_regclass('public.ig_reel') is not null and to_regclass('public.ig_account') is not null"))).scalar()
    if not exists:
        return {}, "the Instagram reel tables are not present in this database"
    cols = ", ".join(f"r.{c}" for c in METRIC_COLUMNS)
    rows = (await db.execute(text(
        f"select r.media_id, {cols} from ig_reel r join ig_account a on a.id = r.account_id "
        "where a.user_id = :u and r.media_id = any(:ids)"), {"u": user_id, "ids": list(media_ids)})).mappings().all()
    out = {}
    for r in rows:
        d = {c: r[c] for c in METRIC_COLUMNS}
        d["posted_at"] = d["posted_at"].isoformat() if d.get("posted_at") is not None else None
        out[r["media_id"]] = d
    return out, None


async def nearest_reels(db, vector, user_id, k: int = 5, metrics_lookup=ig_metrics_lookup) -> dict:
    """The user's own reels that look most like `vector`, best first, each with the metrics already stored for it."""
    from sqlalchemy import select
    from app.models.dino_embedding import ReelVisualEmbedding as T
    q = [float(x) for x in np.asarray(vector, dtype=np.float64) / max(np.linalg.norm(vector), 1e-12)]
    dist = T.mean_vector.cosine_distance(q)
    rows = (await db.execute(select(T.media_id, (1 - dist).label("sim")).where(T.user_id == user_id)
                             .order_by(dist).limit(max(0, int(k))))).all()
    base = {"label": LABEL, "note": NOTE, "available": True, "compared_with": 0}
    if not rows:
        base.update(reels=[], reason="no reel embeddings are stored for this user yet")
        return base
    count = (await db.execute(select(T.media_id).where(T.user_id == user_id))).all()
    metrics, reason = await metrics_lookup(db, user_id, [r[0] for r in rows])
    reels = []
    for media_id, sim in rows:
        m = metrics.get(media_id)
        reels.append({"media_id": media_id, "similarity": round(float(sim), 4), "metrics": m,
                      "metrics_note": None if m is not None else (reason or "no stored metrics for this reel")})
    base.update(reels=reels, compared_with=len(count))
    return base
