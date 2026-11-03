"""
backfill_research_reels.py

Backfills the research_reels table from the batch1_5 per_video_*.json files.
Safe to re-run: inserts use ON CONFLICT (instagram_url) DO NOTHING.

Usage:
    python scripts/niche_research/backfill_research_reels.py [--db-url postgresql://...]
    # defaults to DATABASE_URL env var or local dev DB
"""
import argparse
import json
import math
import os
import statistics
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras


# ── Constants ─────────────────────────────────────────────────────────────────

RESULTS_DIR = Path(__file__).resolve().parent / "results"
PIPELINE_VERSION = "batch1_5_original"
BATCH_SOURCE = "batch1_5"
TODAY = date.today()

SCORED_PARAM_MAP: Dict[str, str] = {
    "Hook & Opening":               "score_hook",
    "Retention & Pacing":           "score_retention",
    "Audio & Music":                "score_audio",
    "Visual Quality & Originality": "score_visual",
    "Trend Alignment":              "score_trend",
    "Shareability":                 "score_shareability",
}
ADVISORY_PARAMS = {"On-Screen Text", "Save Potential", "Replay Value", "Call to Action"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_upload_date(raw: Any) -> Optional[date]:
    """Parse YYYYMMDD integer strings and ISO 8601 timestamps."""
    if not raw:
        return None
    s = str(raw).strip()
    # ISO 8601: "2026-04-24T16:40:35.000Z"
    if "T" in s or "-" in s[:8]:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    # Integer YYYYMMDD: "20260802"
    if s.isdigit() and len(s) == 8:
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            pass
    return None


def _safe_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_niche_file(path: Path) -> List[Dict[str, Any]]:
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: expected JSON array, got {type(data).__name__}")
    return data


def _compute_log_relative_views(
    records: List[Dict[str, Any]],
) -> Dict[str, Optional[float]]:
    """
    Returns {url: log_relative_views} for all records.
    Groups by (niche, creator_handle), computes median view_count per group,
    then log2(view_count / median). Null when view_count missing or median = 0.
    """
    # Build groups: (niche, creator_handle) → [view_counts]
    from collections import defaultdict
    groups: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    record_keys: Dict[str, Tuple[str, str]] = {}

    for rec in records:
        url = rec.get("url", "")
        niche = rec.get("niche", "") or ""
        meta = rec.get("metadata") or {}
        creator = (meta.get("uploader") or "").strip().lower()
        vc = _safe_float(meta.get("viewCount"))
        group_key = (niche, creator)
        record_keys[url] = group_key
        if vc and vc > 0:
            groups[group_key].append(vc)

    # Median per group
    medians: Dict[Tuple[str, str], float] = {}
    for key, views in groups.items():
        medians[key] = statistics.median(views)

    result: Dict[str, Optional[float]] = {}
    for rec in records:
        url = rec.get("url", "")
        meta = rec.get("metadata") or {}
        vc = _safe_float(meta.get("viewCount"))
        group_key = record_keys.get(url)
        median = medians.get(group_key, 0) if group_key else 0
        if vc and vc > 0 and median and median > 0:
            result[url] = math.log2(vc / median)
        else:
            result[url] = None
    return result


def _build_row(rec: Dict[str, Any], log_rel: Optional[float]) -> Dict[str, Any]:
    meta = rec.get("metadata") or {}
    scores = rec.get("scores_by_param") or {}

    view_count = _safe_int(meta.get("viewCount"))
    like_count = _safe_int(meta.get("likeCount"))
    comment_count = _safe_int(meta.get("commentCount"))
    upload_date = _parse_upload_date(meta.get("uploadDate"))

    days_since_upload: Optional[int] = None
    if upload_date:
        days_since_upload = (TODAY - upload_date).days

    engagement_rate: Optional[float] = None
    if view_count and view_count > 0:
        likes = like_count or 0
        comments = comment_count or 0
        engagement_rate = (likes + comments) / view_count

    advisory: Optional[Dict[str, Any]] = None
    if scores:
        advisory = {k: scores[k] for k in ADVISORY_PARAMS if k in scores}
        if not advisory:
            advisory = None

    raw_pipeline: Dict[str, Any] = {
        k: rec[k] for k in rec
        if k not in ("vision",)  # skip heavy per-frame vision blobs
    }

    return {
        "id": str(uuid.uuid4()),
        "instagram_url": rec.get("url", ""),
        "batch_source": BATCH_SOURCE,
        "pipeline_version": PIPELINE_VERSION,
        "niche": rec.get("niche") or None,
        "creator_handle": (meta.get("uploader") or "").strip().lower() or None,
        "follower_count": None,
        "follower_tier": None,
        "region": rec.get("region") or None,
        "content_subformat": None,
        "view_count": view_count,
        "like_count": like_count,
        "comment_count": comment_count,
        "upload_date": upload_date,
        "duration_sec": _safe_float(rec.get("duration_sec") or meta.get("duration")),
        "days_since_upload": days_since_upload,
        "log_relative_views": log_rel,
        "views_per_follower": None,
        "engagement_rate": engagement_rate,
        "score_hook":         _safe_float(scores.get("Hook & Opening")),
        "score_retention":    _safe_float(scores.get("Retention & Pacing")),
        "score_audio":        _safe_float(scores.get("Audio & Music")),
        "score_visual":       _safe_float(scores.get("Visual Quality & Originality")),
        "score_trend":        _safe_float(scores.get("Trend Alignment")),
        "score_shareability": _safe_float(scores.get("Shareability")),
        "advisory_json": json.dumps(advisory) if advisory else None,
        "raw_pipeline_json": json.dumps(raw_pipeline),
        "r2_frames_prefix": None,
        "r2_audio_key": None,
        "error": rec.get("error") or None,
    }


INSERT_SQL = """
INSERT INTO research_reels (
    id, instagram_url, batch_source, pipeline_version,
    niche, creator_handle, follower_count, follower_tier,
    region, content_subformat,
    view_count, like_count, comment_count, upload_date, duration_sec,
    days_since_upload, log_relative_views, views_per_follower, engagement_rate,
    score_hook, score_retention, score_audio, score_visual, score_trend, score_shareability,
    advisory_json, raw_pipeline_json,
    r2_frames_prefix, r2_audio_key, error
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill research_reels from batch1_5 JSON files")
    ap.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL", "postgresql://postgres:200807@localhost:5432/prysm"),
        help="psycopg2-compatible PostgreSQL URL",
    )
    ap.add_argument("--dry-run", action="store_true", help="Parse and build rows but do not insert")
    args = ap.parse_args()

    # Strip asyncpg driver prefix if present
    db_url = args.db_url.replace("postgresql+asyncpg://", "postgresql://")

    # Discover niche files
    niche_files = sorted(
        p for p in RESULTS_DIR.glob("per_video_*.json")
        if not p.name.endswith(".bak")
    )
    if not niche_files:
        print(f"ERROR: No per_video_*.json files found in {RESULTS_DIR}")
        return 1

    print(f"Found {len(niche_files)} niche files")

    # Load all records and compute log_relative_views globally
    all_records: List[Dict[str, Any]] = []
    for p in niche_files:
        records = _load_niche_file(p)
        all_records.extend(records)

    print(f"Loaded {len(all_records)} total records")
    log_rel_map = _compute_log_relative_views(all_records)

    if args.dry_run:
        print("[dry-run] Skipping DB insert. Building rows to verify…")
        built = 0
        for rec in all_records:
            try:
                _build_row(rec, log_rel_map.get(rec.get("url", "")))
                built += 1
            except Exception as e:
                print(f"  ERROR building row for {rec.get('url')}: {e}")
        print(f"[dry-run] Built {built}/{len(all_records)} rows successfully")
        return 0

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    cur = conn.cursor()

    total_inserted = 0
    total_skipped = 0
    total_errors = 0

    for p in niche_files:
        niche = p.name.replace("per_video_", "").replace(".json", "")
        records = _load_niche_file(p)
        niche_inserted = 0
        niche_skipped = 0
        niche_errors = 0

        rows = []
        for rec in records:
            try:
                row = _build_row(rec, log_rel_map.get(rec.get("url", "")))
                if not row["instagram_url"]:
                    niche_errors += 1
                    continue
                rows.append(row)
            except Exception as e:
                print(f"  [build error] {rec.get('url')}: {e}")
                niche_errors += 1

        for row in rows:
            try:
                cur.execute(INSERT_SQL, row)
                if cur.rowcount == 1:
                    niche_inserted += 1
                else:
                    niche_skipped += 1
            except Exception as e:
                conn.rollback()
                print(f"  [insert error] {row['instagram_url']}: {e}")
                niche_errors += 1
                continue

        conn.commit()
        total_inserted += niche_inserted
        total_skipped += niche_skipped
        total_errors += niche_errors
        print(
            f"  {niche:30s}: {len(records):5d} records → "
            f"{niche_inserted} inserted, {niche_skipped} skipped, {niche_errors} errors"
        )

    cur.close()
    conn.close()

    print(f"\nDone: {total_inserted} inserted, {total_skipped} already existed, {total_errors} errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
