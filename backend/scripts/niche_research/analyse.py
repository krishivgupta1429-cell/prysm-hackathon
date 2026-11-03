"""CLI entry point for the niche research tool.

Usage:
    # Analyse all videos listed in input.csv
    python -m scripts.niche_research.analyse --input input.csv

    # Skip the analysis step and just re-run aggregation over existing results
    python -m scripts.niche_research.analyse --aggregate-only

    # Limit to a subset of niches
    python -m scripts.niche_research.analyse --input input.csv --niche dance --niche fitness

    # Dry run — print what WOULD be done
    python -m scripts.niche_research.analyse --input input.csv --dry-run

CSV schema (header required):
    url,niche,view_count
    https://www.instagram.com/reel/Cxxxxx/,dance,1200000
    https://www.instagram.com/reel/Cyyyyy/,fitness,84000
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import sys
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .aggregator import aggregate_all
from .config import SETTINGS
from .region_tagger import tag_record
from .scorer import score_video
from .video_pipeline import prepare_video


# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("niche_research")


# ── CSV parsing ──────────────────────────────────────────────────────────────

def read_input_csv(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            url = (r.get("url") or "").strip()
            niche = (r.get("niche") or "").strip().lower()
            if not url or not niche:
                continue
            rows.append({
                "url": url,
                "niche": niche,
                "view_count": (r.get("view_count") or "").strip(),
                "uploader": (r.get("uploader") or "").strip(),
                "like_count": (r.get("like_count") or "").strip(),
                "comment_count": (r.get("comment_count") or "").strip(),
                "duration_sec": (r.get("duration_sec") or "").strip(),
                "follower_count": (r.get("follower_count") or "").strip(),
            })
    return rows


# ── Resume helpers ───────────────────────────────────────────────────────────

def _per_niche_path(niche: str) -> Path:
    return SETTINGS.results_dir / f"per_video_{niche}.json"


def load_existing(niche: str) -> List[Dict]:
    p = _per_niche_path(niche)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        log.warning("Existing %s is corrupt; starting fresh", p)
        return []


def already_done_urls(niche: str) -> Set[str]:
    """Return URLs that have been successfully scored (no error + has actual scores)."""
    return {
        r.get("url") for r in load_existing(niche)
        if r.get("url") and not r.get("error") and r.get("scores_by_param")
    }


def save_result(niche: str, record: Dict) -> None:
    """Upsert a record into per_video_<niche>.json by URL (atomic-ish)."""
    p = _per_niche_path(niche)
    existing = load_existing(niche)
    url = record.get("url")
    # Replace an existing record for the same URL (avoids duplicates on retry)
    updated = [r for r in existing if r.get("url") != url]
    updated.append(record)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(updated, indent=2, default=str))
    tmp.replace(p)
    _write_to_research_reels_db(record)


# ── Batch6 research_reels DB write ───────────────────────────────────────────

_BATCH6_PIPELINE_VERSION = "batch6_sonnet46_gemini25pro_8frames"
_BATCH6_SOURCE = "batch6"
_SCORED_PARAM_MAP: Dict[str, str] = {
    "Hook & Opening":               "score_hook",
    "Retention & Pacing":           "score_retention",
    "Audio & Music":                "score_audio",
    "Visual Quality & Originality": "score_visual",
    "Trend Alignment":              "score_trend",
    "Shareability":                 "score_shareability",
}
_ADVISORY_PARAMS = {"On-Screen Text", "Save Potential", "Replay Value", "Call to Action"}

_INSERT_RESEARCH_REEL = """
INSERT INTO research_reels (
    id, instagram_url, batch_source, pipeline_version,
    niche, creator_handle, follower_count, follower_tier,
    region, content_subformat,
    view_count, like_count, comment_count, upload_date, duration_sec,
    days_since_upload, log_relative_views, views_per_follower, engagement_rate,
    score_hook, score_retention, score_audio, score_visual, score_trend, score_shareability,
    advisory_json, raw_pipeline_json, r2_frames_prefix, r2_audio_key, error
) VALUES (
    %(id)s, %(instagram_url)s, %(batch_source)s, %(pipeline_version)s,
    %(niche)s, %(creator_handle)s, %(follower_count)s, %(follower_tier)s,
    %(region)s, %(content_subformat)s,
    %(view_count)s, %(like_count)s, %(comment_count)s, %(upload_date)s, %(duration_sec)s,
    %(days_since_upload)s, %(log_relative_views)s, %(views_per_follower)s, %(engagement_rate)s,
    %(score_hook)s, %(score_retention)s, %(score_audio)s, %(score_visual)s,
    %(score_trend)s, %(score_shareability)s,
    %(advisory_json)s::jsonb, %(raw_pipeline_json)s::jsonb,
    %(r2_frames_prefix)s, %(r2_audio_key)s, %(error)s
)
ON CONFLICT (instagram_url) DO NOTHING
"""


def _safe_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _safe_float_val(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_upload_date(raw: Any) -> Optional[date]:
    if not raw:
        return None
    s = str(raw).strip()
    if "T" in s or (len(s) > 8 and "-" in s):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    if s.isdigit() and len(s) == 8:
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            pass
    return None


def _write_to_research_reels_db(record: Dict[str, Any]) -> None:
    """Write one batch6 record to research_reels. Errors are logged, never raised."""
    try:
        import psycopg2  # noqa: PLC0415
        db_url = os.environ.get("DATABASE_URL", "postgresql://postgres:200807@localhost:5432/prysm")
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

        meta = record.get("metadata") or {}
        scores = record.get("scores_by_param") or {}
        today = date.today()

        view_count = _safe_int(meta.get("viewCount"))
        like_count = _safe_int(meta.get("likeCount"))
        comment_count = _safe_int(meta.get("commentCount"))
        upload_date = _parse_upload_date(meta.get("uploadDate"))
        days_since = (today - upload_date).days if upload_date else None

        engagement_rate: Optional[float] = None
        if view_count and view_count > 0:
            engagement_rate = ((like_count or 0) + (comment_count or 0)) / view_count

        advisory = {k: scores[k] for k in _ADVISORY_PARAMS if k in scores} or None

        row = {
            "id": str(uuid.uuid4()),
            "instagram_url": record.get("url", ""),
            "batch_source": _BATCH6_SOURCE,
            "pipeline_version": _BATCH6_PIPELINE_VERSION,
            "niche": record.get("niche") or None,
            "creator_handle": (meta.get("uploader") or "").strip().lower() or None,
            "follower_count": None,
            "follower_tier": None,
            "region": record.get("region") or None,
            "content_subformat": None,
            "view_count": view_count,
            "like_count": like_count,
            "comment_count": comment_count,
            "upload_date": upload_date,
            "duration_sec": _safe_float_val(record.get("duration_sec") or meta.get("duration")),
            "days_since_upload": days_since,
            "log_relative_views": None,  # computed in post-process batch step
            "views_per_follower": None,
            "engagement_rate": engagement_rate,
            "score_hook":         _safe_float_val(scores.get("Hook & Opening")),
            "score_retention":    _safe_float_val(scores.get("Retention & Pacing")),
            "score_audio":        _safe_float_val(scores.get("Audio & Music")),
            "score_visual":       _safe_float_val(scores.get("Visual Quality & Originality")),
            "score_trend":        _safe_float_val(scores.get("Trend Alignment")),
            "score_shareability": _safe_float_val(scores.get("Shareability")),
            "advisory_json": json.dumps(advisory) if advisory else None,
            "raw_pipeline_json": json.dumps(
                {k: v for k, v in record.items() if k != "vision"}, default=str
            ),
            "r2_frames_prefix": None,
            "r2_audio_key": None,
            "error": record.get("error") or None,
        }

        conn = psycopg2.connect(db_url)
        try:
            with conn.cursor() as cur:
                cur.execute(_INSERT_RESEARCH_REEL, row)
            conn.commit()
        finally:
            conn.close()

    except Exception as exc:
        log.warning("[research_reels] DB write failed for %s: %s", record.get("url"), exc)


# ── View count pre-filter ────────────────────────────────────────────────────

def _meets_min_views(row: Dict[str, str], min_views: int) -> bool:
    v = row.get("view_count", "").strip()
    if not v:
        return True  # no view data — include and let scorer decide
    try:
        return int(v) >= min_views
    except ValueError:
        return True


# ── Worker ───────────────────────────────────────────────────────────────────

def analyse_one(url: str, niche: str, row: Optional[Dict[str, str]] = None) -> Dict:
    t0 = time.time()
    try:
        # Build prefilled metadata from CSV so we skip redundant API calls
        prefilled = None
        if row:
            try:
                prefilled = {
                    "uploader": row.get("uploader") or "",
                    "viewCount": int(row["view_count"]) if row.get("view_count") else 0,
                    "likeCount": int(row["like_count"]) if row.get("like_count") else 0,
                    "commentCount": int(row["comment_count"]) if row.get("comment_count") else 0,
                    "duration": float(row["duration_sec"]) if row.get("duration_sec") else 0.0,
                }
            except (ValueError, TypeError):
                prefilled = None

        assets = prepare_video(url, niche, prefilled_metadata=prefilled)
        if assets.error:
            log.warning("[%s] aborting: %s", assets.cache_key, assets.error)
            return tag_record({
                "url": url, "niche": niche, "cache_key": assets.cache_key,
                "error": assets.error, "elapsed_sec": round(time.time() - t0, 1),
            })
        record = score_video(assets)
        record["elapsed_sec"] = round(time.time() - t0, 1)
        return tag_record(record)
    except Exception as e:
        log.exception("[%s] worker crashed: %s", url, e)
        return tag_record({"url": url, "niche": niche, "error": f"crash: {e}",
                           "elapsed_sec": round(time.time() - t0, 1)})


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Prysm niche-research analyser")
    ap.add_argument("--input", type=Path, help="CSV file with url,niche[,view_count]")
    ap.add_argument("--aggregate-only", action="store_true",
                    help="Skip analysis; recompute aggregates + markdown from existing per_video_*.json")
    ap.add_argument("--niche", action="append", default=[],
                    help="Limit to specific niches (can be passed multiple times)")
    ap.add_argument("--min-views", type=int, default=5000,
                    help="Skip reels with fewer views than this (default: 5000)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be analysed; do not call any APIs or LLMs")
    ap.add_argument("--no-resume", action="store_true",
                    help="Re-analyse URLs even if already in per_video_<niche>.json")
    args = ap.parse_args()

    log.info("Settings: backend=%s | model=%s | url=%s | transcribe=%s | parallel=%d",
             SETTINGS.llm_backend, SETTINGS.llm_model, SETTINGS.llm_base_url,
             SETTINGS.transcribe_backend, SETTINGS.max_parallel)

    if args.aggregate_only:
        log.info("Aggregate-only mode")
        aggregate_all()
        return 0

    if not args.input:
        log.error("Either --input or --aggregate-only is required")
        return 2
    if not args.input.exists():
        log.error("Input file not found: %s", args.input)
        return 2

    rows = read_input_csv(args.input)
    if args.niche:
        wanted = {n.lower() for n in args.niche}
        rows = [r for r in rows if r["niche"] in wanted]

    if args.min_views > 0:
        before = len(rows)
        rows = [r for r in rows if _meets_min_views(r, args.min_views)]
        log.info("Min-views filter (>=%d): kept %d / %d reels", args.min_views, len(rows), before)

    log.info("Loaded %d rows from %s", len(rows), args.input)

    # Build url→row lookup for prefilled metadata
    url_to_row: Dict[str, Dict[str, str]] = {r["url"]: r for r in rows}

    # Group by niche: niche → list of rows
    by_niche: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_niche[r["niche"]].append(r)

    # Resume filter
    if SETTINGS.resume and not args.no_resume:
        for niche, niche_rows in list(by_niche.items()):
            done = already_done_urls(niche)
            new_rows = [r for r in niche_rows if r["url"] not in done]
            skipped = len(niche_rows) - len(new_rows)
            by_niche[niche] = new_rows
            if skipped:
                log.info("[%s] resume: skipping %d already-done URLs", niche, skipped)

    todo = [(r["url"], r["niche"], r) for niche_rows in by_niche.values() for r in niche_rows]
    log.info("Total URLs to analyse: %d (across %d niches)", len(todo), len(by_niche))

    if args.dry_run:
        for niche, niche_rows in by_niche.items():
            log.info("[%s] %d urls", niche, len(niche_rows))
            for r in niche_rows[:5]:
                log.info("    - %s", r["url"])
            if len(niche_rows) > 5:
                log.info("    ... and %d more", len(niche_rows) - 5)
        return 0

    if not todo:
        log.info("Nothing to do. Running aggregation only.")
        aggregate_all()
        return 0

    # Parallel run
    completed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=SETTINGS.max_parallel) as pool:
        futures = {pool.submit(analyse_one, u, n, row): (u, n) for u, n, row in todo}
        for fut in as_completed(futures):
            url, niche = futures[fut]
            try:
                rec = fut.result()
            except Exception as e:
                log.exception("Future failed for %s: %s", url, e)
                rec = {"url": url, "niche": niche, "error": f"future_crash: {e}"}
            save_result(niche, rec)
            if rec.get("error"):
                failed += 1
            completed += 1
            log.info("Progress: %d/%d done (%d failed)", completed, len(todo), failed)

    log.info("Analysis complete: %d successful, %d failed", completed - failed, failed)
    log.info("Aggregating …")
    aggregate_all()
    log.info("Done. Results in %s", SETTINGS.results_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
