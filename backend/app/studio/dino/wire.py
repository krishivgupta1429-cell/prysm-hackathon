"""Built during the Meta Global AI Developer Hackathon (new work).

Glue between the DINOv2 building blocks (store, attention, integrity) and the real screens. Everything here is soft: a
failure, DINO_BACKEND=off or a missing Modal service is logged (never with secrets) and returns None / available=false.
It never raises into an import or an analysis.

    embed_for_import(video)            one reel's mean embedding while the Instagram import has the video on disk
    analyse_video(video)               analysis side: mean embedding + compact attention strip (run beside the analysis)
    compact_attention(strip)           the attention strip as small JSON for the results page (no percentages)
    closest_payload(db, user_id, id)   "Closest to your Reel N" for the results page, with that reel's real metrics in words
    backfill(...)                      embeddings for reels that were imported before this existed
    planner_hints_for_edit / edit_render_check   FLAG-OFF STUBS (see the bottom): wired after feature/auto-edit is merged

Flags (backend/.env): DINO_WIRE=0 turns the whole wiring off (default on; the model itself is still DINO_BACKEND).
"""
import asyncio
import logging
import tempfile
from pathlib import Path

from app.studio import config
from app.studio.dino import attention, store
from app.studio.dino.client import DinoUnavailable

log = logging.getLogger(__name__)

IMPORT_EMBED_TIMEOUT_S = 90.0
ANALYSIS_TIMEOUT_S = 240.0
BACKFILL_COST_CAP_USD = 0.25
SIMILARITY_NOTE = "Looks similar is about pictures, not results. Two reels that look alike can perform very differently."
SMALL_SAMPLE_NOTE = "You have fewer than 3 imported reels, so this ranking is a small sample: it only says which of them looks closest."
# Rough bands for the cosine similarity of the mean picture embeddings. Not calibrated on viewer data; measured on the DINOv2 dev
# fixtures: the same clip after an edit scored 0.996, unrelated clips 0.09 to 0.23. They only decide the words next to the number.
BAND_HIGH, BAND_MID = 0.8, 0.5
VIEWER_NOTE = ("Estimates from the picture and the sound of this video, not measured viewer data. "
               "A still stretch or a quiet moment is not automatically a problem.")


def wire_enabled() -> bool:
    return (config.get_env("DINO_WIRE") or "1").strip() != "0"


def _flag(name: str) -> bool:
    return (config.get_env(name) or "0").strip() == "1"


def _why(e: Exception) -> str:
    return config.redact(f"{type(e).__name__}: {e}")[:200]


# ------------------------------------------------------------------ embeddings

def _embed_sync(video: str) -> dict:
    return store.embed_reel(video)


async def embed_video(video: str, timeout: float = IMPORT_EMBED_TIMEOUT_S) -> dict | None:
    """{"mean", "frame_count", "usage"} or None (reason logged). Never raises."""
    if not wire_enabled():
        return None
    from app.studio.dino import client
    if client.backend_choice() != "modal":   # off: say so without cutting frames first
        log.info("[DINO] embedding skipped: DINOv2 is off (DINO_BACKEND=off or no service configured)")
        return None
    try:
        return await asyncio.wait_for(asyncio.to_thread(_embed_sync, video), timeout)
    except DinoUnavailable as e:
        log.info("[DINO] embedding skipped: %s", config.redact(str(e))[:200])
    except asyncio.TimeoutError:
        log.info("[DINO] embedding skipped: no answer within %ds", int(timeout))
    except Exception as e:
        log.info("[DINO] embedding skipped: %s", _why(e))
    return None


embed_for_import = embed_video


def _clamp(spans, duration) -> list:
    """Bins are half a second wide, so the last span can end past the video's end: cut it at the duration."""
    out = []
    for sp in spans or []:
        end = min(float(sp["end"]), float(duration)) if duration else float(sp["end"])
        if end > float(sp["start"]):
            out.append({**sp, "end": round(end, 2), **({"duration": round(end - float(sp["start"]), 2)} if "duration" in sp else {})})
    return out


def compact_attention(strip: dict, usage: dict | None = None) -> dict:
    """The attention strip as small JSON: three score rows on one 0.5 s grid (values 0 to 1, no percentages), the static
    stretches and loudness dips, and why a part is missing. Everything is labelled estimate."""
    def row(vals):
        return None if vals is None else [None if v is None else round(float(v), 2) for v in vals]
    planner = strip.get("planner") or {}
    visual, energy, change = strip.get("visual") or {}, strip.get("energy") or {}, strip.get("change") or {}
    return {
        "label": "attention strip (estimate)", "estimate": True, "note": VIEWER_NOTE,
        "bin_s": strip.get("bin_s"), "duration": strip.get("duration"),
        "visual_change": row(strip.get("visual_change")), "audio_energy": row(strip.get("audio_energy")),
        "audio_change": row(strip.get("audio_change")),
        "static_stretches": _clamp(planner.get("static_stretches"), strip.get("duration")),
        "loudness_dips": _clamp(planner.get("loudness_dips"), strip.get("duration")),
        "reasons": {"visual_change": None if visual.get("available") else visual.get("reason") or "not available",
                    "audio_energy": None if energy.get("available") else energy.get("reason") or "not available",
                    "audio_change": None if change.get("available") else change.get("reason") or "not available"},
        "usage": {k: (usage or {}).get(k) for k in ("frames", "gpu_seconds", "est_cost_usd", "cold")} if usage else None,
    }


def _strip_sync(video: str, usage: dict) -> dict:
    return compact_attention(attention.attention_strip(video, None, usage), usage)


async def analyse_video(video: str) -> dict:
    """Analysis side. {"embedding": {...}|None, "attention": {...}|None}. The two parts are independent; each is None on failure."""
    out: dict = {"embedding": None, "attention": None}
    if not wire_enabled():
        return out
    usage: dict = {}

    async def strip():
        try:
            return await asyncio.to_thread(_strip_sync, video, usage)
        except Exception as e:   # includes a file ffprobe cannot read
            log.info("[DINO] attention strip skipped: %s", _why(e))
            return None

    emb, att = await asyncio.gather(embed_video(video, ANALYSIS_TIMEOUT_S), strip())
    out["embedding"], out["attention"] = emb, att
    return out


async def run_analysis_part(task: "asyncio.Task | None") -> dict:
    """Await the task started beside the analysis; never raises, never waits longer than ANALYSIS_TIMEOUT_S."""
    if task is None:
        return {"embedding": None, "attention": None}
    try:
        return await asyncio.wait_for(task, ANALYSIS_TIMEOUT_S)
    except Exception as e:
        log.info("[DINO] analysis part skipped: %s", _why(e))
        return {"embedding": None, "attention": None}


async def save_for_analysis(session_factory, analysis_id, embedding: dict | None) -> bool:
    if not embedding:
        return False
    try:
        async with session_factory() as db:
            await store.save_analysis_embedding(db, analysis_id, embedding["mean"], embedding["frame_count"])
        return True
    except Exception as e:
        log.info("[DINO] could not save the analysis embedding: %s", _why(e))
        return False


async def save_reel_embeddings(session_factory, user_id, found: dict[str, dict], keep_media_ids: list[str]) -> int:
    """Store embeddings {media_id: {"mean","frame_count"}} for a user and drop stored ones whose reel is no longer imported."""
    saved = 0
    try:
        async with session_factory() as db:
            for media_id, e in found.items():
                try:
                    await store.save_reel_embedding(db, user_id, media_id, e["mean"], e["frame_count"])
                    saved += 1
                except Exception as ex:
                    await db.rollback()
                    log.info("[DINO] could not save a reel embedding: %s", _why(ex))
            await prune_embeddings(db, user_id, keep_media_ids)
    except Exception as e:
        log.info("[DINO] could not save reel embeddings: %s", _why(e))
    return saved


async def prune_embeddings(db, user_id, keep_media_ids: list[str]) -> None:
    from sqlalchemy import delete
    from app.models.dino_embedding import ReelVisualEmbedding as T
    await db.execute(delete(T).where(T.user_id == user_id, T.media_id.notin_(list(keep_media_ids) or [""])))
    await db.commit()


async def delete_embeddings(db, user_id) -> int:
    from sqlalchemy import delete
    from app.models.dino_embedding import ReelVisualEmbedding as T
    return (await db.execute(delete(T).where(T.user_id == user_id))).rowcount or 0


# ------------------------------------------------------------------ closest own reel (results page)

def similarity_band(sim: float) -> str:
    return "looks very alike" if sim >= BAND_HIGH else "looks somewhat alike" if sim >= BAND_MID else "looks quite different"


def _plain_lines(r: dict) -> list[str]:
    """That reel's REAL stored numbers in words. A missing number gives no line (nothing is estimated)."""
    lines = []
    ins = r.get("insights") or {}
    reach, views = ins.get("reach"), ins.get("views")
    vs = r.get("vs_usual")
    if reach is not None:
        lines.append(f"Reached {round(reach):,} accounts" + (f" ({vs['label'].split(' ', 1)[1]})" if vs and vs.get("label", "").startswith("Reach") else ""))
    elif views is not None:
        lines.append(f"Had {round(views):,} views" + (f" ({vs['label'].split(' ', 1)[1]})" if vs and vs.get("label", "").startswith("Views") else ""))
    elif vs:
        lines.append(vs["label"])
    if r.get("watch_through") is not None:
        lines.append(f"Viewers watched on average {round(r['watch_through'] * 100)}% of it")
    skip = ins.get("reels_skip_rate")
    if skip is not None:
        lines.append(f"{round(skip)}% of views skipped early")
    likes, comments = r.get("like_count"), r.get("comments_count")
    if likes is not None or comments is not None:
        bits = ([f"{likes:,} likes"] if likes is not None else []) + ([f"{comments:,} comments"] if comments is not None else [])
        lines.append(" and ".join(bits))
    if not lines:
        lines.append("Instagram gave no numbers for this reel yet (they can lag up to 48 hours)")
    return lines


def _thumb(acct, url):
    """The reel's thumbnail as the Creator DNA page serves it: a fixture's own generated image, else Instagram's link (it may have expired)."""
    if url and url.startswith("fixture://thumb/"):
        return f"/instagram/fixture-thumb/{url.rsplit('/', 1)[1]}" if getattr(acct, "is_fixture", False) else None
    return url or None


async def closest_payload(db, user_id, analysis_id, personalise_load=None) -> dict:
    """What the results page shows under "Closest to your Reel N". Reels are numbered like the Creator DNA (newest = Reel 1)."""
    from sqlalchemy import select
    from app.models.prysm_analysis import PrysmAnalysis
    from app.studio.instagram import personalise
    row = (await db.execute(select(PrysmAnalysis.user_id).where(PrysmAnalysis.id == analysis_id))).first()
    if row is None or str(row[0]) != str(user_id):
        return {"found": False}
    base = {"found": True, "label": store.LABEL, "note": SIMILARITY_NOTE}
    data = await (personalise_load or personalise.load)(db, user_id)
    if data is None or not data["reels"]:
        return {**base, "connected": data is not None, "available": False, "ranked": [],
                "reason": "No imported reels yet." if data is not None else "Instagram is not connected."}
    reels = {r["media_id"]: r for r in data["reels"]}
    vec = await store.load_analysis_embedding(db, analysis_id)
    if vec is None:
        return {**base, "connected": True, "available": False, "ranked": [], "own_reels": len(reels),
                "reason": "This video has no picture embedding (the model service was off when it was analysed)."}

    async def no_metrics(db_, user_, ids):
        return {}, None
    near = await store.nearest_reels(db, vec, user_id, k=max(30, len(reels)), metrics_lookup=no_metrics)
    ranked = []
    for item in near.get("reels", []):
        r = reels.get(item["media_id"])
        if r is None:
            continue   # an embedding of a reel that is no longer imported
        ranked.append({"n": r["n"], "similarity": item["similarity"], "band": similarity_band(item["similarity"]), "permalink": r.get("permalink"),
                       "postedAt": r["posted_at"].isoformat() if r.get("posted_at") else None, "partial": bool(r.get("partial")),
                       "thumbnailUrl": _thumb(data["account"], r.get("thumbnail_url")),
                       "lines": _plain_lines(r), "hookWords": r.get("hook_words") if r.get("hook_words_status") == "transcribed" else None})
    missing = len(reels) - len(ranked)
    out = {**base, "connected": True, "isFixture": bool(data["account"].is_fixture), "own_reels": len(reels), "compared_with": len(ranked),
           "missing_embeddings": missing, "ranked": ranked[:3], "all_count": len(ranked), "small_sample": len(reels) < 3,
           "small_sample_note": SMALL_SAMPLE_NOTE if len(reels) < 3 else None}
    if not ranked:
        out.update(available=False, reason="None of your imported reels has a picture embedding yet. Use \"Compute picture matching\" on the Creator DNA page.")
    else:
        out["available"] = True
    return out


# ------------------------------------------------------------------ backfill for reels imported before this existed

async def backfill(*, session_factory, user_id, source, progress=None) -> dict:
    """Embeds every imported reel that has no stored embedding: re-lists the account's reels (media links expire, so they are
    fetched fresh), downloads each video to a temp folder, embeds it, deletes it. Cents of Modal GPU for a whole account."""
    from sqlalchemy import select
    from app.models.dino_embedding import ReelVisualEmbedding as T
    from app.studio.instagram.models import IgAccount, IgReel
    progress = progress or (lambda stage, status: None)
    async with session_factory() as db:
        acct = (await db.execute(select(IgAccount).where(IgAccount.user_id == user_id))).scalar_one_or_none()
        if acct is None:
            return {"status": "error", "error": "Instagram is not connected."}
        have = {m for (m,) in (await db.execute(select(T.media_id).where(T.user_id == user_id))).all()}
        mine = [m for (m,) in (await db.execute(select(IgReel.media_id).where(IgReel.account_id == acct.id))).all()]
    todo = [m for m in mine if m not in have]
    summary = {"status": "done", "reels": len(mine), "already": len(mine) - len(todo), "embedded": 0, "skipped_no_video": 0,
               "failed": 0, "cost_usd": 0.0}
    if not todo:
        return summary
    if not wire_enabled():
        return {**summary, "status": "error", "error": "Picture matching is switched off (DINO_WIRE=0)."}
    from app.studio.dino import client
    if client.backend_choice() != "modal":
        return {**summary, "status": "error", "error": "The picture model is off (DINO_BACKEND=off), so nothing was computed."}
    raws = {r["id"]: r for r in await source.list_reels(max(30, len(mine)))}
    sem = asyncio.Semaphore(2)
    found: dict[str, dict] = {}

    async def one(media_id: str):
        raw = raws.get(media_id)
        if raw is None or not raw.get("media_url"):
            summary["skipped_no_video"] += 1
            return
        if summary["cost_usd"] >= BACKFILL_COST_CAP_USD:
            summary["failed"] += 1
            return
        async with sem, _tmpdir() as tmp:
            dest = str(Path(tmp) / "reel.mp4")
            if not await source.download(raw, dest):
                summary["skipped_no_video"] += 1
                return
            emb = await embed_video(dest)
            if emb is None:
                summary["failed"] += 1
                return
            found[media_id] = emb
            summary["cost_usd"] = round(summary["cost_usd"] + float((emb.get("usage") or {}).get("est_cost_usd") or 0.0), 6)

    progress("backfill", "running")
    await asyncio.gather(*[one(m) for m in todo])
    async with session_factory() as db:
        for media_id, e in found.items():
            try:
                await store.save_reel_embedding(db, user_id, media_id, e["mean"], e["frame_count"])
                summary["embedded"] += 1
            except Exception as ex:
                await db.rollback()
                summary["failed"] += 1
                log.info("[DINO] backfill could not save one embedding: %s", _why(ex))
    progress("backfill", "ok")
    return summary


class _tmpdir:
    """Async context manager around a TemporaryDirectory (the video is deleted in every case)."""
    def __init__(self):
        self._t = tempfile.TemporaryDirectory(prefix="dino_backfill_")

    async def __aenter__(self):
        return self._t.__enter__()

    async def __aexit__(self, *a):
        self._t.cleanup()


async def status(db, user_id) -> dict:
    """How many imported reels have a stored embedding (for the Creator DNA page's backfill button)."""
    from sqlalchemy import func, select
    from app.models.dino_embedding import ReelVisualEmbedding as T
    from app.studio.dino import client
    from app.studio.instagram.models import IgAccount, IgReel
    acct = (await db.execute(select(IgAccount).where(IgAccount.user_id == user_id))).scalar_one_or_none()
    if acct is None:
        return {"connected": False}
    rows = (await db.execute(select(IgReel.media_id, IgReel.analysis_level).where(IgReel.account_id == acct.id))).all()
    media = [m for m, _ in rows]
    no_video = {m for m, lvl in rows if lvl in ("thumbnail_caption", "caption_only")}   # Instagram gave no video file: nothing to embed
    have = {m for (m,) in (await db.execute(select(T.media_id).where(T.user_id == user_id))).all()}
    return {"connected": True, "reels": len(media), "embedded": len([m for m in media if m in have]),
            "no_video": len(no_video - have), "missing": len([m for m in media if m not in have and m not in no_video]),
            "model": client.backend_choice() == "modal" and wire_enabled(), "isFixture": bool(acct.is_fixture)}


# ------------------------------------------------------------------ FLAG-OFF STUBS (not wired yet)
# feature/auto-edit is still changing, so nothing here touches the edit engine. When that branch is merged, call these from
# the planner and from the render step. Both are no-ops unless their flag is "1", and even then they only return data.

def planner_hints_for_edit(video: str) -> dict | None:
    """STUB, flag DINO_PLANNER_HINTS=1 (default off). Will give the edit planner the static stretches, loudness dips, onsets and
    beats of the ORIGINAL video (attention.planner_hints). Today it is not called by anything."""
    if not _flag("DINO_PLANNER_HINTS"):
        return None
    try:
        return attention.attention_strip(video)["planner"]
    except Exception as e:
        log.info("[DINO] planner hints skipped: %s", _why(e))
        return None


def edit_render_check(original: str, edited: str, edl: dict | None = None) -> dict | None:
    """STUB, flag DINO_EDIT_RENDER_CHECK=1 (default off). Will compare the rendered edit with its source after the render
    (integrity.render_integrity) so the editor can warn or block on ok == false. Today it is not called by anything."""
    if not _flag("DINO_EDIT_RENDER_CHECK"):
        return None
    try:
        from app.studio.dino import integrity
        return integrity.render_integrity(original, edited, edl)
    except Exception as e:
        log.info("[DINO] render check skipped: %s", _why(e))
        return None
