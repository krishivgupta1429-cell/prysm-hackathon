"""
build_training_table.py
=======================
Converts per_video_<niche>.json files from the niche-research pipeline into a
single flat CSV suitable for training a regression model that predicts relative
content performance from Prysm's 10 scoring parameters.

IMPORTANT — do not use the `region` column as a model feature yet.
As of the August 2026 batch run, >80% of records are tagged "unknown" and
<2% are tagged "international". The signal-to-noise ratio is too low for the
model to learn anything meaningful from it. The column is included in the
output for later filtering / inspection once region coverage improves.

Also note: ~50% of records across all allowlist niches have view_count = 0
despite having non-zero like_count, which indicates a scrape miss (the view
counter field was empty in the Instagram response). These records are excluded
at label computation and logged explicitly — they are NOT genuine zero-view
videos.

Usage
-----
    cd prysm-backend\ 3
    python -m scripts.niche_research.build_training_table

Output
------
    scripts/niche_research/results/training_table.csv
"""

from __future__ import annotations

import csv
import glob
import json
import logging
import math
import os
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
OUTPUT_CSV = RESULTS_DIR / "training_table.csv"

# Only these niches pass the scope filter (enough creator diversity per audit).
NICHE_ALLOWLIST: Set[str] = {
    "education",
    "beauty",
    "memes",
    "dance",
    "fitness",
    "diy_crafts",
    "vlogs",
}

# Minimum reels per creator to be included in label computation.
MIN_REELS_PER_CREATOR = 4

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_training_table")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _shortcode_from_url(url: str) -> str:
    """Extract the Instagram shortcode from a reel/post URL.

    Handles both /p/<code>/ and /reel/<code>/ forms.
    Falls back to the raw URL string if no match — still a stable unique ID.
    """
    m = re.search(r"/(?:p|reel|reels)/([A-Za-z0-9_-]+)", url or "")
    return m.group(1) if m else (url or "").strip()


def _col_name(param_name: str) -> str:
    """Convert a parameter display name to a snake_case column name.

    'Hook & Opening'             -> 'hook_opening_score'
    'Visual Quality & Originality' -> 'visual_quality_originality_score'
    """
    # lowercase, strip special chars, collapse spaces/underscores
    cleaned = re.sub(r"[^a-z0-9 ]", "", param_name.lower())
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    return f"{cleaned}_score"


def _safe_int(val: Any, field: str, context: str) -> Optional[int]:
    """Parse a value to int, returning None and logging on failure."""
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        log.debug("Could not parse %s=%r as int in %s", field, val, context)
        return None


# ---------------------------------------------------------------------------
# Step 1 + 2: Load all files, extract raw rows
# ---------------------------------------------------------------------------

def load_all_records() -> Tuple[List[Dict[str, Any]], Set[str]]:
    """Read every per_video_*.json (excluding .bak) and return:
    - raw_rows: list of dicts with all extracted fields
    - all_param_names: union of every parameter name seen across all files
    """
    pattern = str(RESULTS_DIR / "per_video_*.json")
    files = sorted(
        f for f in glob.glob(pattern)
        if not f.endswith(".bak")
    )

    if not files:
        raise FileNotFoundError(
            f"No per_video_*.json files found in {RESULTS_DIR}"
        )

    log.info("Found %d niche files to process", len(files))

    raw_rows: List[Dict[str, Any]] = []
    all_param_names: Set[str] = set()

    for fpath in files:
        source_file = os.path.basename(fpath)
        niche = source_file.replace("per_video_", "").replace(".json", "")

        try:
            with open(fpath, encoding="utf-8") as f:
                records = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Failed to load %s: %s — skipping file", source_file, exc)
            continue

        if not isinstance(records, list):
            log.error("%s is not a JSON list — skipping", source_file)
            continue

        log.info("  %s: %d records", source_file, len(records))

        for idx, rec in enumerate(records):
            context = f"{source_file}[{idx}]"

            # Guard against completely malformed records
            if not isinstance(rec, dict):
                log.warning("%s: record is not a dict — skipping", context)
                continue

            # --- Identity fields ---
            url = rec.get("url") or ""
            video_id = _shortcode_from_url(url)

            metadata = rec.get("metadata") or {}
            if not isinstance(metadata, dict):
                log.warning("%s: 'metadata' is not a dict — using empty", context)
                metadata = {}

            creator = str(metadata.get("uploader") or "").strip()
            rec_niche = str(rec.get("niche") or niche).strip()
            upload_date = str(metadata.get("uploadDate") or "").strip()

            # --- Error flag ---
            error_val = rec.get("error")
            has_error = bool(error_val)  # null/None/empty string → False

            # --- Feature: per-parameter scores ---
            # Build a {param_name: score} dict; score may be None if absent.
            param_scores: Dict[str, Optional[int]] = {}
            scoring = rec.get("scoring")
            if scoring is None:
                # Key absent entirely — log once per niche, not per record
                pass
            elif not isinstance(scoring, dict):
                log.warning("%s: 'scoring' is not a dict — ignoring", context)
            else:
                parameters = scoring.get("parameters")
                if parameters is None:
                    pass  # no parameters key — leave param_scores empty
                elif not isinstance(parameters, list):
                    log.warning(
                        "%s: scoring.parameters is not a list — ignoring", context
                    )
                else:
                    for param in parameters:
                        if not isinstance(param, dict):
                            continue
                        pname = str(param.get("name") or "").strip()
                        if not pname:
                            continue
                        all_param_names.add(pname)
                        raw_score = param.get("score")
                        param_scores[pname] = _safe_int(
                            raw_score, "score", context
                        )

            # --- Feature: numeric top-level fields ---
            duration_sec = rec.get("duration_sec")
            if duration_sec is not None:
                try:
                    duration_sec = float(duration_sec)
                except (TypeError, ValueError):
                    duration_sec = None

            frames_used = _safe_int(rec.get("frames_used"), "frames_used", context)
            transcript_len = _safe_int(
                rec.get("transcript_len"), "transcript_len", context
            )

            # --- Region (informational — not a model feature yet) ---
            region = str(rec.get("region") or "unknown").strip()

            # --- Raw metadata ---
            view_count = _safe_int(
                metadata.get("viewCount"), "viewCount", context
            )
            like_count = _safe_int(
                metadata.get("likeCount"), "likeCount", context
            )
            comment_count = _safe_int(
                metadata.get("commentCount"), "commentCount", context
            )

            raw_rows.append({
                # Identity
                "video_id":      video_id,
                "url":           url,
                "creator":       creator,
                "niche":         rec_niche,
                "upload_date":   upload_date,
                "source_file":   source_file,
                # Quality control
                "has_error":     has_error,
                # Param scores (flat dict, merged into row later)
                "_param_scores": param_scores,
                # Numeric features
                "duration_sec":  duration_sec,
                "frames_used":   frames_used,
                "transcript_len": transcript_len,
                # Region (not a model feature — see module docstring)
                "region":        region,
                # Raw metadata
                "view_count":    view_count,
                "like_count":    like_count,
                "comment_count": comment_count,
            })

    return raw_rows, all_param_names


# ---------------------------------------------------------------------------
# Step 3: Exclude has_error=True records
# ---------------------------------------------------------------------------

def exclude_errors(
    raw_rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Remove records where has_error=True.

    Returns (clean_rows, per_niche_error_counts).
    """
    per_niche_errors: Dict[str, int] = defaultdict(int)
    clean: List[Dict[str, Any]] = []

    for row in raw_rows:
        if row["has_error"]:
            per_niche_errors[row["niche"]] += 1
        else:
            clean.append(row)

    return clean, dict(per_niche_errors)


# ---------------------------------------------------------------------------
# Step 5: Scope filter — apply allowlist
# ---------------------------------------------------------------------------

def apply_niche_allowlist(
    rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """Keep only rows whose niche is in NICHE_ALLOWLIST.

    Returns (filtered_rows, excluded_count).
    """
    allowed = [r for r in rows if r["niche"] in NICHE_ALLOWLIST]
    excluded_count = len(rows) - len(allowed)
    return allowed, excluded_count


# ---------------------------------------------------------------------------
# Step 4: Label computation
# ---------------------------------------------------------------------------

def compute_labels(
    rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Compute log_relative_views for each row.

    Pipeline:
      a. Group all rows by creator across all niches globally.
      b. Drop creators with fewer than MIN_REELS_PER_CREATOR reels.
      c. Compute creator_median_views per surviving creator.
      d. Compute log2(view_count / creator_median_views) per video.
         Exclude rows where view_count or creator_median_views is 0 / None.

    Returns (labeled_rows, stats_dict).
    """
    # --- a. Count reels per creator (across all niches) ---
    creator_views: Dict[str, List[int]] = defaultdict(list)
    for row in rows:
        creator = row["creator"]
        vc = row["view_count"]
        if creator:
            creator_views[creator].append(vc if vc is not None else 0)

    # --- b. Drop creators with <MIN_REELS_PER_CREATOR ---
    dropped_creators: Set[str] = {
        c for c, views in creator_views.items()
        if len(views) < MIN_REELS_PER_CREATOR
    }
    n_dropped_creators = len(dropped_creators)
    n_dropped_for_few_reels = sum(
        len(creator_views[c]) for c in dropped_creators
    )

    rows_after_creator_filter = [
        r for r in rows if r["creator"] not in dropped_creators
    ]

    # --- c. Compute creator median views (non-zero views only for the median) ---
    # We compute the median from ALL of a creator's reels in our dataset, but
    # we only include non-zero view_counts in the median computation.
    # A creator whose EVERY reel has view_count=0 (all scrape misses) would
    # produce a median of 0, which we can't divide by — handled in step d.
    creator_median: Dict[str, Optional[float]] = {}
    for creator, views in creator_views.items():
        if creator in dropped_creators:
            continue
        nonzero = [v for v in views if v and v > 0]
        if nonzero:
            creator_median[creator] = statistics.median(nonzero)
        else:
            creator_median[creator] = None  # all scrape misses for this creator

    # --- d. Compute label ---
    labeled: List[Dict[str, Any]] = []
    n_excluded_zero_views = 0
    n_excluded_scrape_miss = 0  # view_count=0 but likes>0 → scrape miss
    n_excluded_null_median = 0

    for row in rows_after_creator_filter:
        vc = row["view_count"]
        creator = row["creator"]
        median_v = creator_median.get(creator)

        # view_count missing or None
        if vc is None:
            log.debug(
                "Excluding %s: view_count is None", row["video_id"]
            )
            n_excluded_zero_views += 1
            continue

        # view_count = 0 — almost certainly a scrape miss (viewCount field
        # was empty in the Instagram GraphQL response). Log separately.
        if vc == 0:
            lc = row.get("like_count") or 0
            if lc and lc > 0:
                # Has likes → genuine video that was scraped, just view count missing
                n_excluded_scrape_miss += 1
            else:
                n_excluded_zero_views += 1
            continue

        # Creator median is None (all their reels had zero views)
        if median_v is None or median_v == 0:
            log.debug(
                "Excluding %s: creator %r has no usable median "
                "(all their scraped reels have view_count=0)",
                row["video_id"], creator,
            )
            n_excluded_null_median += 1
            continue

        # Safe to compute label
        ratio = vc / median_v
        log_relative_views = math.log2(ratio)

        row = dict(row)  # shallow copy — don't mutate the input list
        row["creator_median_views"] = round(median_v, 2)
        row["log_relative_views"] = round(log_relative_views, 6)
        labeled.append(row)

    stats = {
        "n_dropped_creators":     n_dropped_creators,
        "n_dropped_few_reels":    n_dropped_for_few_reels,
        "n_excluded_scrape_miss": n_excluded_scrape_miss,
        "n_excluded_zero_views":  n_excluded_zero_views,
        "n_excluded_null_median": n_excluded_null_median,
    }
    return labeled, stats


# ---------------------------------------------------------------------------
# Step 6: Flatten and write CSV
# ---------------------------------------------------------------------------

def write_csv(
    rows: List[Dict[str, Any]],
    all_param_names: Set[str],
    output_path: Path,
) -> None:
    """Flatten per-param score dicts into columns and write the CSV."""

    # Deterministic column order for parameters (alphabetical by display name,
    # then convert to column name — keeps the file stable across re-runs).
    sorted_param_names = sorted(all_param_names)
    param_col_names = [_col_name(n) for n in sorted_param_names]

    # Check for collisions (two different display names mapping to same col name)
    if len(set(param_col_names)) < len(param_col_names):
        seen: Dict[str, str] = {}
        for pn, cn in zip(sorted_param_names, param_col_names):
            if cn in seen:
                log.warning(
                    "Column name collision: '%s' and '%s' both -> '%s'",
                    seen[cn], pn, cn,
                )
            seen[cn] = pn

    fieldnames = [
        # Identity (not model features)
        "video_id",
        "url",
        "creator",
        "niche",
        "upload_date",
        "source_file",
        # Label
        "log_relative_views",
        "creator_median_views",
        # Scoring parameters (model features)
        *param_col_names,
        # Numeric video features
        "duration_sec",
        "frames_used",
        "transcript_len",
        # Region (NOT a model feature yet — see module docstring)
        "region",
        # Raw metadata (supplementary, not primary features)
        "view_count",
        "like_count",
        "comment_count",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for row in rows:
            flat = dict(row)
            # Expand _param_scores into individual columns
            param_scores: Dict[str, Optional[int]] = flat.pop("_param_scores", {})
            for pname, col in zip(sorted_param_names, param_col_names):
                flat[col] = param_scores.get(pname)  # None if missing for this video
            # Remove internal fields not in fieldnames
            flat.pop("has_error", None)
            writer.writerow(flat)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=" * 60)
    log.info("Building training table from niche-research results")
    log.info("=" * 60)

    # --- Load ---
    raw_rows, all_param_names = load_all_records()
    total_read = len(raw_rows)
    log.info("Total records read: %d", total_read)
    log.info(
        "Parameter names discovered (%d): %s",
        len(all_param_names),
        sorted(all_param_names),
    )

    # --- Step 3: Remove errors ---
    clean_rows, per_niche_errors = exclude_errors(raw_rows)
    n_excluded_errors = total_read - len(clean_rows)
    log.info("Excluded for has_error=True: %d", n_excluded_errors)
    for niche, count in sorted(per_niche_errors.items()):
        log.info("  %-20s %d errors", niche, count)

    # --- Step 5: Scope filter (allowlist) ---
    # Report stats on ALL niches first (for visibility), then filter.
    niche_counts_before: Dict[str, int] = defaultdict(int)
    for r in clean_rows:
        niche_counts_before[r["niche"]] += 1
    log.info("Non-error records per niche (before allowlist filter):")
    for niche, cnt in sorted(niche_counts_before.items()):
        marker = "✓" if niche in NICHE_ALLOWLIST else "✗ (excluded)"
        log.info("  %-25s %4d  %s", niche, cnt, marker)

    allowlist_rows, n_excluded_niche = apply_niche_allowlist(clean_rows)
    log.info(
        "Excluded for niche not in allowlist: %d  (kept %d)",
        n_excluded_niche, len(allowlist_rows),
    )

    # --- Step 4: Label computation ---
    labeled_rows, label_stats = compute_labels(allowlist_rows)

    log.info("Label computation stats:")
    log.info(
        "  Creators with <%d reels (dropped):  %d creators, %d videos",
        MIN_REELS_PER_CREATOR,
        label_stats["n_dropped_creators"],
        label_stats["n_dropped_few_reels"],
    )
    log.info(
        "  Excluded — view_count=0 with likes>0 (scrape miss): %d",
        label_stats["n_excluded_scrape_miss"],
    )
    log.info(
        "  Excluded — view_count=0 or None (genuine missing):  %d",
        label_stats["n_excluded_zero_views"],
    )
    log.info(
        "  Excluded — creator median is 0 (all reels scrape-missed): %d",
        label_stats["n_excluded_null_median"],
    )

    # --- Step 6: Write ---
    write_csv(labeled_rows, all_param_names, OUTPUT_CSV)

    # --- Summary ---
    final_creators = len({r["creator"] for r in labeled_rows})
    niche_breakdown: Dict[str, int] = defaultdict(int)
    for r in labeled_rows:
        niche_breakdown[r["niche"]] += 1

    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    log.info("Total videos read:                        %d", total_read)
    log.info("Excluded — has_error=True:                %d", n_excluded_errors)
    log.info("Excluded — niche not in allowlist:        %d", n_excluded_niche)
    log.info(
        "Excluded — creator has <%d reels:         %d",
        MIN_REELS_PER_CREATOR,
        label_stats["n_dropped_few_reels"],
    )
    log.info(
        "Excluded — view_count=0, scrape miss:    %d",
        label_stats["n_excluded_scrape_miss"],
    )
    log.info(
        "Excluded — view_count missing/zero:       %d",
        label_stats["n_excluded_zero_views"],
    )
    log.info(
        "Excluded — creator median unusable:       %d",
        label_stats["n_excluded_null_median"],
    )
    log.info("─" * 40)
    log.info("Final rows in training_table.csv:         %d", len(labeled_rows))
    log.info("Final unique creators:                    %d", final_creators)
    log.info("Breakdown by niche:")
    for niche, cnt in sorted(niche_breakdown.items()):
        log.info("  %-20s %4d", niche, cnt)
    log.info("Output: %s", OUTPUT_CSV)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
