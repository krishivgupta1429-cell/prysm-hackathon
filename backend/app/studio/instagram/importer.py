"""Built during the Meta Global AI Developer Hackathon (new work).

The import job: the N most recent reels of the connected account, their insights, temporary frames -> fingerprints,
the derived numbers, the Creator DNA. Videos and frames are never stored. Missing data stays missing (None / "not
available"); nothing is invented. Progress goes through the existing app.studio.progress.Progress (stage -> status in
Redis) plus a small job record.
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, select

from app.studio.instagram import dna as D
from app.studio.instagram import frames as F
from app.studio.instagram import metrics as M
from app.studio.instagram.graph import RateLimited, TokenInvalid
from app.studio.instagram.models import CreatorDna, IgAccount, IgReel, IgReelInsight

log = logging.getLogger(__name__)
MIN_COUNT, MAX_COUNT, DEFAULT_COUNT = 1, 30, 10
JOB_KEY, JOB_TTL = "ig:import:{}", 3600
LOCK_KEY, LOCK_TTL = "ig:import_lock:{}", 1800
CONCURRENCY = 3


def clamp_count(n: Any) -> int:
    try:
        return max(MIN_COUNT, min(MAX_COUNT, int(n)))
    except (TypeError, ValueError):
        return DEFAULT_COUNT


def _parse_ts(s: str | None) -> Optional[datetime]:
    if not s:
        return None
    try:   # Instagram: 2026-10-01T12:00:00+0000
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None


async def _job_write(redis, job_id: str, **fields):
    key = JOB_KEY.format(job_id)
    try:
        cur = json.loads(await redis.get(key) or "{}")
        cur.update(fields)
        await redis.setex(key, JOB_TTL, json.dumps(cur))
    except Exception as e:
        log.debug("job write failed: %s", e)


async def job_status(redis, job_id: str) -> dict | None:
    raw = await redis.get(JOB_KEY.format(job_id))
    if not raw:
        return None
    job = json.loads(raw)
    try:
        prog = json.loads(await redis.get(f"prysm:progress:{job_id}") or "null")
    except Exception:
        prog = None
    job["progress"] = prog or {}
    return job


async def start_import(redis, user_id: str, count: int) -> tuple[str | None, str | None]:
    """(job_id, None) or (None, reason). One import per user at a time."""
    job_id = uuid.uuid4().hex
    got = await redis.set(LOCK_KEY.format(user_id), job_id, nx=True, ex=LOCK_TTL)
    if not got:
        return None, "An import is already running for this account."
    await _job_write(redis, job_id, status="running", user_id=str(user_id), requested=count, started=time.time())
    return job_id, None


async def _one_reel(src, raw: dict, idx: int, spend: D.Spend, sem: asyncio.Semaphore, progress, state: dict) -> dict:
    """Everything for one reel. Never raises except RateLimited/TokenInvalid (kept in `state`)."""
    stage = f"reel_{idx:02d}"
    async with sem:
        progress(stage, "running")
        rec: dict[str, Any] = {
            "media_id": raw["id"], "permalink": raw.get("permalink"), "caption": raw.get("caption"),
            "posted_at": _parse_ts(raw.get("timestamp")), "like_count": raw.get("like_count"),
            "comments_count": raw.get("comments_count"), "duration_s": None, "frames_used": 0, "fingerprint": None,
            "note": None, "insights": {m: None for m in M.METRICS}, "analysis_level": "full",
        }
        notes = []
        try:
            if state.get("stop"):
                rec["note"] = "Skipped: the import stopped early (rate limit or token problem)."
                progress(stage, "skipped")
                return rec
            rec["insights"] = await src.insights(raw["id"])
            if all(v is None for v in rec["insights"].values()):
                notes.append("Instagram returned no insights for this reel (data can lag up to 48 hours).")
            jpegs, dur = await F.frames_from_download(lambda p: src.download(raw, p))
            rec["duration_s"] = dur
            if not jpegs:
                # no video file from Instagram (licensed audio, removed media, download failed): say so on the reel, never "fully analysed"
                thumb = await src.thumbnail(raw)
                if thumb:
                    jpegs = [thumb]
                    rec["analysis_level"] = "thumbnail_caption"
                    notes.append("No video link from Instagram (often licensed audio): read from the thumbnail and caption only. "
                                 "No watch-through, and hook, format and pacing are not judged." if dur is None else
                                 "The video downloaded but no frames could be read: thumbnail and caption only; hook, format and pacing are not judged.")
                else:
                    rec["analysis_level"] = "caption_only"
                    notes.append("No video or thumbnail from Instagram: read from the caption only. "
                                 "No watch-through, and hook, format and pacing are not judged.")
            rec["frames_used"] = len(jpegs)
            if spend.room_for_fingerprint():
                rec["fingerprint"] = await D.fingerprint_reel([F.to_image_block(j) for j in jpegs], raw.get("caption"), spend,
                                                              rec["analysis_level"])
                if rec["fingerprint"] is None:
                    notes.append("The fingerprint call did not return a usable answer.")
            else:
                notes.append("Fingerprint skipped: the cost cap for this import was reached.")
            del jpegs
            progress(stage, "ok")
        except RateLimited:
            state["stop"] = state["rate_limited"] = True
            notes.append("Instagram's rate limit was reached; this reel has no insights.")
            progress(stage, "error")
        except TokenInvalid:
            state["stop"] = state["token_invalid"] = True
            notes.append("Instagram rejected the token.")
            progress(stage, "error")
        except Exception as e:
            log.warning("[Import] reel %s failed: %s", idx, type(e).__name__)
            notes.append("This reel could not be fully read.")
            progress(stage, "error")
        rec["note"] = " ".join(notes) or None
        return rec


def derive(records: list[dict]) -> dict:
    """performance_index and watch_through per record (in place); returns the account-level numbers."""
    flat = [{**r["insights"], "duration_s": r["duration_s"]} for r in records]
    idx, basis, med = M.performance_indices(flat)
    for r, pi in zip(records, idx):
        r["performance_index"], r["performance_basis"] = pi, basis
        r["watch_through"] = M.watch_through(r["insights"].get("ig_reels_avg_watch_time"), r["duration_s"])
    return {"basis": basis, "median": med, "availability": M.availability(flat), "missing": M.missing_metrics(flat),
            "avg_watch_through": M.mean(r["watch_through"] for r in records),
            "avg_skip_rate": M.mean(r["insights"].get("reels_skip_rate") for r in records),
            "median_reach": M.account_median(r["insights"].get("reach") for r in records),
            "avg_reach": M.mean(r["insights"].get("reach") for r in records)}


async def run_import(*, redis, session_factory, user_id: str, count: int, job_id: str, source, progress=None, record_spend=None) -> dict:
    """Runs the whole import and returns the job summary. Writes the job record; always releases the lock."""
    progress = progress or (lambda stage, status: None)
    spend = D.Spend(D.cost_cap_usd())
    state: dict[str, Any] = {}
    try:
        progress("fetch_reels", "running")
        raws = await source.list_reels(count)
        progress("fetch_reels", "ok")
        await _job_write(redis, job_id, found=len(raws))
        sem = asyncio.Semaphore(CONCURRENCY)
        recs = await asyncio.gather(*[_one_reel(source, r, i + 1, spend, sem, progress, state) for i, r in enumerate(raws)])
        recs = list(recs)
        agg = derive(recs) if recs else {"basis": None, "median": None, "availability": {}, "missing": list(M.METRICS),
                                         "avg_watch_through": None, "avg_skip_rate": None, "median_reach": None, "avg_reach": None}
        progress("creator_dna", "running")
        reel_dicts = [{"performance_index": r.get("performance_index"), "watch_through": r.get("watch_through"),
                       "fingerprint": r.get("fingerprint"), "caption": r.get("caption"),
                       "analysis_level": r.get("analysis_level"), "media_id": r.get("media_id"),
                       "posted_at": (r["posted_at"].isoformat() if r.get("posted_at") else ""), "_rec": r} for r in recs]
        built = await D.build_dna(reel_dicts, agg["basis"], agg["missing"], spend) if recs else None
        progress("creator_dna", "ok" if built else "skipped")
        # reel numbers in the DNA ("Reel 1" = newest) come from D.prepare; the API numbers reels the same way (posted_at, newest first)
        async with session_factory() as db:
            acct = (await db.execute(select(IgAccount).where(IgAccount.user_id == user_id))).scalar_one_or_none()
            if acct is None:   # disconnected while importing: write nothing
                raise RuntimeError("account was disconnected during the import")
            await _replace_data(db, acct, recs, built, agg, spend, len(raws), count, state)
            await db.commit()
        if record_spend and spend.usd:
            await record_spend(spend.usd)
        summary = {"status": "done", "imported": len(recs), "requested": count, "missing_metrics": agg["missing"],
                   "partial": sum(1 for r in recs if r.get("analysis_level") != "full"),
                   "rate_limited": bool(state.get("rate_limited")), "token_invalid": bool(state.get("token_invalid")),
                   "cost_usd": spend.usd, "model_calls": spend.calls}
        await _job_write(redis, job_id, **summary)
        return summary
    except TokenInvalid:
        summary = {"status": "error", "error": "Instagram rejected the connection. Please connect again."}
    except RateLimited:
        summary = {"status": "error", "error": "Instagram's rate limit was reached before any reel was read. Try again later."}
    except Exception as e:
        log.exception("[Import] failed")
        summary = {"status": "error", "error": "The import failed: " + type(e).__name__}
    finally:
        try:
            await redis.delete(LOCK_KEY.format(user_id))
        except Exception:
            pass
    if record_spend and spend.usd:
        await record_spend(spend.usd)
    await _job_write(redis, job_id, cost_usd=spend.usd, **summary)
    return summary


async def _replace_data(db, acct: IgAccount, recs: list[dict], built: dict | None, agg: dict, spend: D.Spend,
                        found: int, requested: int, state: dict):
    """A re-import replaces the previous reels, insights and DNA of this account (never accumulates stale rows)."""
    old = [r for (r,) in (await db.execute(select(IgReel.id).where(IgReel.account_id == acct.id))).all()]
    if old:
        await db.execute(delete(IgReelInsight).where(IgReelInsight.reel_id.in_(old)))
    await db.execute(delete(IgReel).where(IgReel.account_id == acct.id))
    await db.execute(delete(CreatorDna).where(CreatorDna.account_id == acct.id))
    await db.flush()
    for r in recs:
        reel = IgReel(account_id=acct.id, media_id=r["media_id"], permalink=r["permalink"], posted_at=r["posted_at"],
                      caption=r["caption"], duration_s=r["duration_s"], like_count=r["like_count"],
                      comments_count=r["comments_count"], frames_used=r["frames_used"], analysis_level=r.get("analysis_level"),
                      performance_index=r.get("performance_index"), performance_basis=r.get("performance_basis"),
                      watch_through=r.get("watch_through"), fingerprint=r["fingerprint"], note=r["note"])
        db.add(reel)
        await db.flush()
        for m in M.METRICS:
            v = r["insights"].get(m)
            db.add(IgReelInsight(reel_id=reel.id, metric=m, value=v, available=v is not None))
    now = datetime.now(timezone.utc)
    acct.last_import_at, acct.last_import_requested = now, requested
    acct.last_import_summary = {"imported": len(recs), "found": found, "requested": requested, "missing_metrics": agg["missing"],
                                "partial": sum(1 for r in recs if r.get("analysis_level") != "full"),
                                "rate_limited": bool(state.get("rate_limited")), "token_invalid": bool(state.get("token_invalid"))}
    if built:
        db.add(CreatorDna(account_id=acct.id, user_id=acct.user_id, dna=built["dna"], summary=built["summary"],
                          sample_size=built["sample_size"], metrics_available=agg["availability"],
                          medians={"basis": agg["basis"], "median": agg["median"], "median_reach": agg["median_reach"],
                                   "avg_reach": agg["avg_reach"], "avg_watch_through": agg["avg_watch_through"],
                                   "avg_skip_rate": agg["avg_skip_rate"]},
                          model=D.dna_model(), cost_usd=spend.usd))
