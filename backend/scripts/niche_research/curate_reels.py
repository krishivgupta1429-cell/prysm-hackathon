"""Curate niche-specific reels from a known creators list.

Per creator:
  1. Pull `pull_n` recent posts via Apify (over-request to leave room for filtering).
  2. Drop image-only posts (we want reels = videos).
  3. Drop sponsored / brand-promo posts via caption + hashtag heuristics.
  4. Drop off-niche posts via Qwen3-VL thumbnail check (catches subtle cases like
     a comedy creator's vlog day, or a fitness creator's recipe video).
  5. Keep up to `target_per_creator` clean reels.

Output:
  reels_input.csv — same schema as fetch_reels.py, ready for analyse.py
  curation_report.json — per-creator stats (pulled / kept / dropped breakdown)

Usage:
    # Default: 20 reels per creator, pull 35 candidates each
    python -m scripts.niche_research.curate_reels

    # Limit niches
    python -m scripts.niche_research.curate_reels --niche dance --niche comedy

    # Tune
    python -m scripts.niche_research.curate_reels --target 25 --pull 40
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import SETTINGS
from .llm_client import chat_with_vision, extract_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("curate_reels")


APIFY_BASE = "https://api.apify.com/v2/acts"
SCRAPER_ACTOR = "apify~instagram-scraper"


# ── Niche definitions used by both text + LLM filters ────────────────────────

NICHE_DEFINITIONS: Dict[str, str] = {
    "comedy":  "primarily a comedy skit, joke, sketch, or humorous performance",
    "fitness": "primarily a workout demonstration, exercise tutorial, gym training, or visible physical training session",
    "dance":   "primarily a dance performance or choreography (NOT a fitness video that includes movement, NOT a vlog)",
    "music":   "primarily a music performance, cover song, or musical creation",
    "gaming":  "primarily gameplay footage, gaming setup, or game commentary",
    "vlogs":   "primarily a personal day-in-the-life vlog or storytelling vlog",
    "tech":    "primarily a tech product review, gadget breakdown, or tech tutorial",
    "travel":  "primarily a travel destination feature, scenic location, or travel tip",
}

NICHE_KEYWORDS: Dict[str, List[str]] = {
    "comedy":  ["comedy", "funny", "joke", "skit", "humour", "humor", "meme", "lol", "haha"],
    "fitness": ["fitness", "workout", "gym", "exercise", "training", "transformation", "muscle", "abs", "cardio", "lift", "pushup", "pullup"],
    "dance":   ["dance", "choreography", "choreo", "dancer", "step", "performance"],
    "music":   ["music", "song", "cover", "singing", "musician", "vocals", "guitar", "piano"],
    "gaming":  ["gaming", "gameplay", "game", "valorant", "bgmi", "esports", "stream"],
    "vlogs":   ["vlog", "day in my life", "diary", "morning routine", "daily"],
    "tech":    ["tech", "gadget", "review", "smartphone", "laptop", "iphone", "android", "coding"],
    "travel":  ["travel", "trip", "destination", "trekking", "backpack", "explore"],
}

SPONSORED_MARKERS = [
    "#ad", "#paid", "#sponsored", "#partnership", "#collab", "#promotion",
    "paid partnership", "brand collab", "in collaboration with",
    "use code", "discount code", "promo code", "sponsored by",
    "ambassador for", "thanks to", "gifted by",
]


# ── Apify ────────────────────────────────────────────────────────────────────

def fetch_creator_posts(username: str, n: int, token: str) -> List[Dict[str, Any]]:
    url = f"{APIFY_BASE}/{SCRAPER_ACTOR}/run-sync-get-dataset-items?token={token}"
    payload = {
        "directUrls": [f"https://www.instagram.com/{username}/"],
        "resultsType": "posts",
        "resultsLimit": n,
    }
    log.info("    → Apify @%s (limit=%d)", username, n)
    try:
        r = requests.post(url, json=payload, timeout=300)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return [p for p in data if not p.get("error")]
    except Exception as e:
        log.warning("    Apify error @%s: %s", username, e)
    return []


# ── Filters ──────────────────────────────────────────────────────────────────

def is_video(post: Dict[str, Any]) -> bool:
    t = (post.get("type") or "").lower()
    if t == "video":
        return True
    if (post.get("productType") or "").lower() in ("clips", "reel"):
        return True
    return bool(post.get("videoUrl") or post.get("videoPlayCount"))


def is_sponsored(post: Dict[str, Any]) -> bool:
    """Cheap caption + hashtag check for branded content."""
    blob = ((post.get("caption") or "") + " " + " ".join(post.get("hashtags") or [])).lower()
    return any(marker in blob for marker in SPONSORED_MARKERS)


def text_niche_signal(post: Dict[str, Any], niche: str) -> Optional[bool]:
    """Cheap text-only on-niche check.
    Returns True if strong on-niche keyword found, None if ambiguous (let LLM decide).
    Returns False is reserved for cases we're confident it's off-niche; we don't use that here
    since absence-of-keyword isn't proof of off-niche.
    """
    blob = ((post.get("caption") or "") + " " + " ".join(post.get("hashtags") or [])).lower()
    if any(kw in blob for kw in NICHE_KEYWORDS.get(niche, [])):
        return True
    return None


def llm_on_niche_check(post: Dict[str, Any], niche: str) -> bool:
    """Use Qwen3-VL on the thumbnail to decide on-niche or not. Strict prompt."""
    image_url = post.get("displayUrl") or post.get("thumbnailUrl")
    if not image_url:
        return False

    caption = (post.get("caption") or "")[:300]
    niche_def = NICHE_DEFINITIONS.get(niche, f"primarily about the niche '{niche}'")

    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    try:
        img = requests.get(image_url, timeout=20)
        img.raise_for_status()
        tmp.write(img.content)
        tmp.flush()
        tmp.close()

        prompt = (
            f"Look at this Instagram reel thumbnail. Caption: \"{caption}\"\n\n"
            f"Decide: is this video {niche_def}?\n\n"
            f"Be STRICT. Reject (false) if the video is incidentally related to {niche} "
            f"but actually about something else (brand promo, vlog, different genre).\n\n"
            f"Answer ONLY in this JSON format:\n"
            f'{{"on_niche": true/false, "reason": "one short sentence"}}'
        )
        raw = chat_with_vision(
            system_prompt="You judge whether Instagram reels are on-topic for a given niche. Be strict.",
            user_prompt=prompt,
            image_paths=[tmp.name],
            max_tokens=200,
        )
        parsed = extract_json(raw)
        return bool(parsed.get("on_niche"))
    except Exception as e:
        log.warning("    LLM check failed: %s", e)
        return False
    finally:
        try:
            Path(tmp.name).unlink()
        except Exception:
            pass


# ── Per-creator orchestration ────────────────────────────────────────────────

def curate_creator(username: str, niche: str, target: int, pull_n: int,
                   token: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Returns (kept_reels, stats)."""
    stats = {"pulled": 0, "non_video": 0, "sponsored": 0, "off_niche_text": 0,
             "off_niche_llm": 0, "on_niche_text": 0, "on_niche_llm": 0, "kept": 0}

    posts = fetch_creator_posts(username, pull_n, token)
    stats["pulled"] = len(posts)
    if not posts:
        return [], stats

    kept: List[Dict[str, Any]] = []
    for post in posts:
        if len(kept) >= target:
            break
        if not is_video(post):
            stats["non_video"] += 1
            continue
        if is_sponsored(post):
            stats["sponsored"] += 1
            continue

        sig = text_niche_signal(post, niche)
        if sig is True:
            stats["on_niche_text"] += 1
            kept.append(post)
            continue

        # Ambiguous → ask Qwen3-VL
        if llm_on_niche_check(post, niche):
            stats["on_niche_llm"] += 1
            kept.append(post)
        else:
            stats["off_niche_llm"] += 1

    stats["kept"] = len(kept)
    return kept, stats


# ── CSV IO ───────────────────────────────────────────────────────────────────

def load_creators(path: Path) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    with open(path, newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return out
    header = [c.strip().lower() for c in rows[0]]
    body = rows[1:] if header and header[0] == "niche" else rows
    for r in body:
        if not r:
            continue
        niche = (r[0] or "").strip().lower()
        creators = [c.strip().lstrip("@") for c in r[1:] if c.strip()]
        if niche and creators:
            out[niche] = creators
    return out


def write_reels_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = ["url", "niche", "view_count", "like_count", "comment_count", "uploader", "duration_sec"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def post_to_row(post: Dict[str, Any], niche: str) -> Dict[str, Any]:
    return {
        "url": post.get("url") or "",
        "niche": niche,
        "view_count": post.get("videoPlayCount") or post.get("videoViewCount") or "",
        "like_count": post.get("likesCount") or "",
        "comment_count": post.get("commentsCount") or "",
        "uploader": post.get("ownerUsername") or "",
        "duration_sec": post.get("videoDuration") or "",
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    here = Path(__file__).resolve().parent
    default_creators = here / "niche_creators.csv"
    default_out = here / "reels_input.csv"
    default_report = here / "curation_report.json"

    ap = argparse.ArgumentParser(description="Curate niche-specific reels from a known creators list")
    ap.add_argument("--creators", type=Path, default=default_creators)
    ap.add_argument("--niche", action="append", default=[], help="Limit to specific niches")
    ap.add_argument("--target", type=int, default=20, help="Reels to keep per creator (default: 20)")
    ap.add_argument("--pull", type=int, default=35, help="Posts to pull per creator before filtering (default: 35)")
    ap.add_argument("--output", type=Path, default=default_out)
    ap.add_argument("--report", type=Path, default=default_report)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = SETTINGS.apify_api_token
    if not token and not args.dry_run:
        log.error("APIFY_API_TOKEN not set in .env")
        return 2
    if not args.creators.exists():
        log.error("Creators file not found: %s", args.creators)
        return 2

    creators_map = load_creators(args.creators)
    if args.niche:
        wanted = {n.lower() for n in args.niche}
        creators_map = {k: v for k, v in creators_map.items() if k in wanted}

    log.info("Plan: %d niches, %d reels per creator (pulling %d, filtering down)",
             len(creators_map), args.target, args.pull)
    for niche, creators in creators_map.items():
        log.info("  %s: %s", niche, ", ".join("@" + c for c in creators))

    if args.dry_run:
        return 0

    all_rows: List[Dict[str, Any]] = []
    full_report: Dict[str, Dict[str, Any]] = {}

    for niche, creators in creators_map.items():
        log.info("════════════════════════════════════════════════════")
        log.info("NICHE: %s", niche.upper())
        log.info("════════════════════════════════════════════════════")
        niche_report: Dict[str, Any] = {}

        for username in creators:
            log.info("  ── @%s ──", username)
            kept, stats = curate_creator(username, niche, args.target, args.pull, token)
            niche_report[username] = stats
            for post in kept:
                all_rows.append(post_to_row(post, niche))
            log.info("    stats: pulled=%d non_video=%d sponsored=%d on_text=%d on_llm=%d off_llm=%d → kept=%d",
                     stats["pulled"], stats["non_video"], stats["sponsored"],
                     stats["on_niche_text"], stats["on_niche_llm"], stats["off_niche_llm"],
                     stats["kept"])
            time.sleep(1)

        full_report[niche] = niche_report

    write_reels_csv(all_rows, args.output)
    args.report.write_text(json.dumps(full_report, indent=2))
    log.info("════════════════════════════════════════════════════")
    log.info("Done. %d total reels → %s", len(all_rows), args.output)
    log.info("Report → %s", args.report)

    # Per-niche summary
    by_niche: Dict[str, int] = defaultdict(int)
    for r in all_rows:
        by_niche[r["niche"]] += 1
    log.info("Per-niche counts:")
    for niche in sorted(by_niche.keys()):
        log.info("  %-12s %d reels", niche, by_niche[niche])

    return 0


if __name__ == "__main__":
    sys.exit(main())
