"""Auto-discover mid-tier, niche-pure creators for each niche.

Three-stage pipeline per niche:

  1. DISCOVER  — pull ~50 posts per discovery hashtag, collect unique uploader
                 usernames. This casts a wide net — most candidates won't be
                 mid-tier or niche-pure, but we'll filter them down.
  2. SIZE FILTER — for each candidate, fetch profile info; keep only those whose
                   follower count falls in [min_followers, max_followers].
  3. PURITY CHECK — for each surviving candidate, pull their last 6 posts and
                    ask Qwen3-VL whether each is on-niche. Keep creators whose
                    on-niche ratio meets the threshold.

Output: writes to niche_creators.csv (overwrites only the niches we processed;
existing rows for other niches are preserved).

Usage:
    # Run discovery for all 8 priority niches with defaults
    python -m scripts.niche_research.discover_creators

    # Run for specific niches
    python -m scripts.niche_research.discover_creators --niche dance --niche music

    # Tune the filters
    python -m scripts.niche_research.discover_creators --min-followers 30000 --max-followers 500000 --purity-threshold 3

    # Dry run — print plan, no API calls
    python -m scripts.niche_research.discover_creators --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from .config import SETTINGS
from .llm_client import chat_with_vision, extract_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("discover_creators")

APIFY_BASE = "https://api.apify.com/v2/acts"
HASHTAG_ACTOR = "apify~instagram-hashtag-scraper"
SCRAPER_ACTOR = "apify~instagram-scraper"


# ── Stage 1: Discover candidate uploaders from hashtags ──────────────────────

def discover_uploaders(hashtags: List[str], per_tag: int, token: str) -> Set[str]:
    """Pull posts from each hashtag and collect unique uploader usernames."""
    uploaders: Set[str] = set()
    for tag in hashtags:
        url = f"{APIFY_BASE}/{HASHTAG_ACTOR}/run-sync-get-dataset-items?token={token}"
        payload = {"hashtags": [tag.lstrip("#")], "resultsLimit": per_tag, "resultsType": "posts"}
        log.info("    → #%s (limit=%d)", tag, per_tag)
        try:
            r = requests.post(url, json=payload, timeout=300)
            r.raise_for_status()
            data = r.json()
            new = 0
            for post in data if isinstance(data, list) else []:
                username = (post.get("ownerUsername") or "").strip()
                if username and username not in uploaders:
                    uploaders.add(username)
                    new += 1
            log.info("      +%d new uploaders (running total: %d)", new, len(uploaders))
        except Exception as e:
            log.warning("      Apify error for #%s: %s", tag, e)
        time.sleep(1)
    return uploaders


# ── Username sanity filter ───────────────────────────────────────────────────

# If a candidate's handle contains a strong cross-niche signal, skip them.
# Example: a username like "fitness_a_lifestyle" should not be considered
# a dance candidate even if their feed has some dance moves.
_CROSS_NICHE_HINTS: Dict[str, List[str]] = {
    "comedy":  ["fitness", "gym", "tech", "gadget", "travel", "music_", "dance", "food", "recipe", "vlog", "news"],
    "fitness": ["comedy", "music_", "dance_", "tech_", "gadget", "travel", "food", "recipe", "news"],
    "gaming":  ["fitness", "comedy", "music_", "dance", "food", "travel", "fashion", "beauty"],
    "music":   ["fitness", "gym", "tech_", "gadget", "travel", "food", "fashion", "news"],
    "dance":   ["fitness", "gym_", "tech_", "gadget", "food", "recipe", "news", "vlog"],
    "vlogs":   ["fitness_", "tech_", "gadget"],
    "tech":    ["fitness", "music_", "dance", "food", "recipe", "fashion", "beauty"],
    "travel":  ["fitness_", "gym_", "tech_", "gadget"],
}


def looks_off_niche(username: str, niche: str) -> bool:
    u = username.lower()
    for marker in _CROSS_NICHE_HINTS.get(niche, []):
        if marker in u:
            return True
    return False


# ── Stage 2: Filter candidates by follower count ─────────────────────────────

def fetch_profile_info(username: str, token: str) -> Optional[Dict[str, Any]]:
    """Fetch a single profile's metadata. Uses resultsType=details which returns
    follower count + bio without pulling all their posts."""
    url = f"{APIFY_BASE}/{SCRAPER_ACTOR}/run-sync-get-dataset-items?token={token}"
    payload = {
        "directUrls": [f"https://www.instagram.com/{username}/"],
        "resultsType": "details",
        "resultsLimit": 1,
    }
    try:
        r = requests.post(url, json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            first = data[0]
            if first.get("error"):
                return None
            return first
    except Exception as e:
        log.warning("    profile fetch error @%s: %s", username, e)
    return None


def filter_by_size(candidates: Set[str], min_followers: int, max_followers: int,
                   niche: str, token: str, max_workers: int = 4) -> List[Tuple[str, int]]:
    """Return [(username, follower_count), ...] for candidates in the band.
    Pre-filters out usernames that obviously belong to a different niche."""
    # Drop obviously off-niche usernames before spending API calls
    pre = [u for u in candidates if not looks_off_niche(u, niche)]
    dropped = len(candidates) - len(pre)
    if dropped:
        log.info("  → username pre-filter dropped %d cross-niche-looking handles", dropped)

    in_band: List[Tuple[str, int]] = []
    log.info("  → fetching profile info for %d candidates (parallel=%d)…", len(pre), max_workers)

    def task(u: str) -> Optional[Tuple[str, int]]:
        info = fetch_profile_info(u, token)
        if not info:
            return None
        followers = info.get("followersCount") or 0
        if min_followers <= followers <= max_followers:
            return (u, followers)
        return None

    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(task, u): u for u in pre}
        for fut in as_completed(futs):
            try:
                res = fut.result()
            except Exception:
                res = None
            if res:
                in_band.append(res)
            completed += 1
            if completed % 10 == 0:
                log.info("    profile check: %d/%d done, %d in band so far",
                         completed, len(pre), len(in_band))
    return sorted(in_band, key=lambda x: x[1], reverse=True)  # bigger first within band


# ── Stage 3: Niche purity check ──────────────────────────────────────────────

def fetch_recent_posts(username: str, n: int, token: str) -> List[Dict[str, Any]]:
    url = f"{APIFY_BASE}/{SCRAPER_ACTOR}/run-sync-get-dataset-items?token={token}"
    payload = {
        "directUrls": [f"https://www.instagram.com/{username}/"],
        "resultsType": "posts",
        "resultsLimit": n,
    }
    try:
        r = requests.post(url, json=payload, timeout=180)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return [p for p in data if not p.get("error")]
    except Exception as e:
        log.warning("    recent-posts fetch error @%s: %s", username, e)
    return []


def quick_text_check(post: Dict[str, Any], niche: str) -> Optional[bool]:
    """Cheap pre-check: caption + hashtag heuristics for clearly-sponsored or
    clearly-off-niche posts. Returns True/False if we can decide; None if unclear."""
    caption = (post.get("caption") or "").lower()
    hashtags = " ".join((post.get("hashtags") or [])).lower()
    blob = caption + " " + hashtags

    # Sponsored / brand collab markers
    SPONSORED = ["#ad", "#paid", "#sponsored", "#partnership", "#collab", "paid partnership",
                 "brand collab", "in collaboration with", "use code", "discount code",
                 "sponsored by", "ambassador for"]
    if any(marker in blob for marker in SPONSORED):
        return False

    # Strong on-niche keyword presence
    NICHE_KEYWORDS = {
        "comedy": ["comedy", "funny", "joke", "skit", "humour", "humor"],
        "fitness": ["fitness", "workout", "gym", "exercise", "training", "transformation"],
        "gaming": ["gaming", "gameplay", "game", "stream", "valorant", "bgmi", "esports"],
        "music": ["music", "song", "cover", "singing", "musician", "vocals", "guitar"],
        "dance": ["dance", "choreography", "choreo", "dancer", "movement"],
        "vlogs": ["vlog", "day in my life", "daily", "diary", "life update"],
        "tech": ["tech", "gadget", "review", "smartphone", "laptop", "iphone", "android", "coding"],
        "travel": ["travel", "trip", "destination", "trekking", "backpack", "explore"],
    }
    if niche in NICHE_KEYWORDS:
        if any(kw in blob for kw in NICHE_KEYWORDS[niche]):
            return True
    return None  # let the LLM decide


NICHE_DEFINITIONS = {
    "comedy": "primarily a comedy skit, joke, or humorous performance — NOT a vlog, opinion piece, or reaction video",
    "fitness": "primarily a workout demonstration, exercise tutorial, or visible physical training — NOT a wellness vlog or food video",
    "gaming": "primarily gameplay footage, gaming setup, or game commentary — NOT a casual vlog from a gamer",
    "music": "primarily a music performance, cover, or musical creation — NOT a vlog, dance video, or music recommendation",
    "dance": "primarily a dance performance or choreography — NOT a fitness video that contains some dance, NOT a music video, NOT a vlog",
    "vlogs": "primarily a personal day-in-the-life or storytelling vlog — NOT a tutorial, brand promo, or staged skit",
    "tech": "primarily a technology product review, tech tutorial, or gadget breakdown — NOT a general unboxing or lifestyle review",
    "travel": "primarily a travel destination feature, scenic location, or travel tip — NOT a hotel ad, brand collaboration, or generic vlog",
}


def llm_post_relevance(post: Dict[str, Any], niche: str) -> bool:
    """Use Qwen3-VL on the thumbnail + caption to decide if a post is on-niche."""
    image_url = post.get("displayUrl") or post.get("thumbnailUrl")
    caption = (post.get("caption") or "")[:300]
    if not image_url:
        return False  # can't decide → exclude

    niche_def = NICHE_DEFINITIONS.get(niche, f"primarily about the niche '{niche}'")

    # Download thumbnail to a tmp file (Qwen3-VL wants a local path)
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    try:
        img = requests.get(image_url, timeout=20)
        img.raise_for_status()
        tmp.write(img.content)
        tmp.flush()
        tmp.close()

        prompt = (
            f"Look at this Instagram post thumbnail. Caption: \"{caption}\"\n\n"
            f"Decide: is this post {niche_def}?\n\n"
            f"Be STRICT. If the post is incidentally related to {niche} but is actually about something else "
            f"(brand promo, sponsored, vlog, different genre), answer false.\n\n"
            f"Answer ONLY in this JSON format:\n"
            f'{{"on_niche": true/false, "reason": "one short sentence"}}'
        )
        raw = chat_with_vision(
            system_prompt="You judge whether Instagram posts are on-topic for a given niche.",
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


def purity_score(username: str, niche: str, sample_size: int, token: str) -> Tuple[int, int]:
    """For a creator, fetch their last N posts and count how many are on-niche.
    Returns (on_niche_count, total_checked)."""
    posts = fetch_recent_posts(username, sample_size, token)
    if not posts:
        return (0, 0)

    on_niche = 0
    for p in posts:
        # Cheap text check first
        text_verdict = quick_text_check(p, niche)
        if text_verdict is True:
            on_niche += 1
            continue
        if text_verdict is False:
            continue
        # Fall back to LLM for ambiguous posts
        if llm_post_relevance(p, niche):
            on_niche += 1
    return (on_niche, len(posts))


# ── Hashtag map IO ───────────────────────────────────────────────────────────

def load_hashtag_map(path: Path) -> Dict[str, List[str]]:
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
        if not niche:
            continue
        rest = [c.strip() for c in r[1:] if c and c.strip()]
        tags: List[str] = []
        for cell in rest:
            for piece in cell.replace(";", ",").split(","):
                piece = piece.strip().lstrip("#")
                if piece:
                    tags.append(piece)
        out[niche] = tags
    return out


# ── Creators CSV merge ───────────────────────────────────────────────────────

def update_creators_csv(path: Path, niche: str, creators: List[str]) -> None:
    """Replace the row for `niche` in the creators CSV; preserve other rows."""
    rows: List[List[str]] = []
    if path.exists():
        with open(path, newline="") as f:
            rows = list(csv.reader(f))
    if not rows:
        rows = [["niche", "creator1", "creator2", "creator3", "creator4", "creator5"]]

    # Pad to 6 columns
    new_row = [niche] + creators[:5] + [""] * (5 - min(5, len(creators)))

    found = False
    for i, r in enumerate(rows):
        if i == 0:
            continue  # header
        if r and r[0].strip().lower() == niche:
            rows[i] = new_row
            found = True
            break
    if not found:
        rows.append(new_row)

    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    here = Path(__file__).resolve().parent
    default_hashtags = here / "discovery_hashtags.csv"
    default_out = here / "niche_creators.csv"

    ap = argparse.ArgumentParser(description="Auto-discover mid-tier, niche-pure creators")
    ap.add_argument("--hashtags", type=Path, default=default_hashtags,
                    help=f"Hashtag map (default: {default_hashtags.name})")
    ap.add_argument("--niche", action="append", default=[],
                    help="Limit to specific niches; can pass multiple times")
    ap.add_argument("--per-tag", type=int, default=40,
                    help="Posts to pull per discovery hashtag (default: 40)")
    ap.add_argument("--min-followers", type=int, default=20_000,
                    help="Minimum follower count (default: 20,000)")
    ap.add_argument("--max-followers", type=int, default=500_000,
                    help="Maximum follower count (default: 500,000)")
    ap.add_argument("--purity-sample", type=int, default=6,
                    help="Recent posts per candidate to check niche purity (default: 6)")
    ap.add_argument("--purity-threshold", type=int, default=4,
                    help="Min on-niche posts out of sample (default: 4 of 6)")
    ap.add_argument("--keep-top", type=int, default=5,
                    help="Top N vetted creators to keep per niche (default: 5)")
    ap.add_argument("--max-purity-checks", type=int, default=15,
                    help="Cap how many candidates to run purity check on per niche, to save time (default: 15)")
    ap.add_argument("--dry-run", action="store_true", help="Print plan, no API calls")
    args = ap.parse_args()

    token = SETTINGS.apify_api_token
    if not token and not args.dry_run:
        log.error("APIFY_API_TOKEN not set in .env")
        return 2

    if not args.hashtags.exists():
        log.error("Hashtag map not found: %s", args.hashtags)
        return 2

    hashtag_map = load_hashtag_map(args.hashtags)
    if args.niche:
        wanted = {n.lower() for n in args.niche}
        hashtag_map = {k: v for k, v in hashtag_map.items() if k in wanted}

    if not hashtag_map:
        log.error("No niches matched. Available: %s", list(load_hashtag_map(args.hashtags).keys()))
        return 2

    log.info("Discovery plan: %d niches, follower band [%s-%s], purity %d/%d",
             len(hashtag_map), f"{args.min_followers:,}", f"{args.max_followers:,}",
             args.purity_threshold, args.purity_sample)
    for niche, tags in hashtag_map.items():
        log.info("  %s → %s", niche, ", ".join("#" + t for t in tags))

    if args.dry_run:
        log.info("Dry run — exiting before any API calls.")
        return 0

    creators_csv = default_out

    for niche, tags in hashtag_map.items():
        log.info("════════════════════════════════════════════════════")
        log.info("NICHE: %s", niche.upper())
        log.info("════════════════════════════════════════════════════")

        # Stage 1
        log.info("Stage 1: discovering uploaders…")
        candidates = discover_uploaders(tags, args.per_tag, token)
        log.info("  → %d unique candidate uploaders", len(candidates))
        if not candidates:
            log.warning("No candidates discovered for %s", niche)
            continue

        # Stage 2
        log.info("Stage 2: filtering by follower band [%s, %s]…",
                 f"{args.min_followers:,}", f"{args.max_followers:,}")
        in_band = filter_by_size(candidates, args.min_followers, args.max_followers, niche, token)
        log.info("  → %d candidates in band", len(in_band))
        if not in_band:
            log.warning("No in-band candidates for %s — try widening follower range", niche)
            continue

        # Cap purity checks (LLM is slow/expensive in time)
        purity_pool = in_band[:args.max_purity_checks]
        log.info("Stage 3: niche purity check (last %d posts, threshold %d on-niche) on top %d…",
                 args.purity_sample, args.purity_threshold, len(purity_pool))

        vetted: List[Tuple[str, int, int, int]] = []  # (username, followers, on_niche, total)
        for username, followers in purity_pool:
            on_niche, total = purity_score(username, niche, args.purity_sample, token)
            log.info("  @%s (%s followers): %d/%d on-niche", username, f"{followers:,}", on_niche, total)
            if total > 0 and on_niche >= args.purity_threshold:
                vetted.append((username, followers, on_niche, total))
                if len(vetted) >= args.keep_top:
                    break  # we have enough — don't burn more API calls

        if not vetted:
            log.warning("No vetted creators for %s — try lowering --purity-threshold", niche)
            continue

        # Save
        usernames = [v[0] for v in vetted[:args.keep_top]]
        update_creators_csv(creators_csv, niche, usernames)
        log.info("✓ %s → saved %d creators: %s", niche, len(usernames), ", ".join("@" + u for u in usernames))

    log.info("════════════════════════════════════════════════════")
    log.info("Discovery complete. Updated: %s", creators_csv)
    log.info("Next: python -m scripts.niche_research.fetch_reels --limit 100")
    return 0


if __name__ == "__main__":
    sys.exit(main())
