"""
app/services/creator_scraper_service.py
Creator Profile Intelligence – Feature #2

Scrapes a creator's last N posts from Instagram / TikTok / YouTube,
runs AI pattern analysis, and returns deep insights:
  • best posting times
  • most successful hooks
  • best video length
  • topic clusters
  • engagement patterns

Scraping strategy:
  - YouTube : yt-dlp channel playlist (free, no API key needed)
  - TikTok  : yt-dlp user page (rate-limited, requires cookies for private)
  - Instagram: RapidAPI (instagram-scraper-api2) or Apify actor
"""

import asyncio
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import scraping_jobs_total
from app.services.ai_service import _chat, _parse_json

logger = get_logger("creator_scraper")

MAX_POSTS = settings.SCRAPER_MAX_POSTS


# ── Platform router ───────────────────────────────────────────────────────────

async def scrape_creator(
    handle: str,
    platform: str,                       # instagram | tiktok | youtube
    max_posts: int = MAX_POSTS,
) -> Dict[str, Any]:
    """
    Entry point. Returns raw post list + derived metadata.
    Each post dict should have at minimum:
      { title, description, duration_sec, timestamp, engagement }
    """
    handle = handle.lstrip("@")
    scraping_jobs_total.labels(platform=platform, status="started").inc()

    try:
        if platform == "youtube":
            posts = await _scrape_youtube(handle, max_posts)
        elif platform == "tiktok":
            posts = await _scrape_tiktok(handle, max_posts)
        elif platform == "instagram":
            posts = await _scrape_instagram(handle, max_posts)
        else:
            raise ValueError(f"Unsupported platform: {platform}")

        scraping_jobs_total.labels(platform=platform, status="complete").inc()
        logger.info("Scraped %d posts for @%s on %s", len(posts), handle, platform)
        return {"posts": posts, "count": len(posts)}
    except Exception as exc:
        scraping_jobs_total.labels(platform=platform, status="failed").inc()
        logger.error("Scraping failed for @%s on %s: %s", handle, platform, exc)
        raise


# ── YouTube scraper (yt-dlp) ──────────────────────────────────────────────────

async def _scrape_youtube(handle: str, max_posts: int) -> List[Dict[str, Any]]:
    """Use yt-dlp to pull video metadata from a YouTube channel."""
    # Handle formats: @handle, channel/UCxxx, or just a name
    url = f"https://www.youtube.com/@{handle}/videos"

    cmd = [
        "yt-dlp",
        "--no-download",
        "--flat-playlist",
        "--playlist-end", str(max_posts),
        "--print-json",
        "--quiet",
        url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    posts = []
    for line in stdout.decode().splitlines():
        try:
            data = json.loads(line)
            posts.append({
                "id":           data.get("id"),
                "title":        data.get("title", ""),
                "description":  data.get("description", "")[:500],
                "duration_sec": data.get("duration"),
                "timestamp":    _parse_yt_ts(data.get("timestamp")),
                "view_count":   data.get("view_count"),
                "like_count":   data.get("like_count"),
                "comment_count": data.get("comment_count"),
                "url":          f"https://youtu.be/{data.get('id')}",
                "thumbnail":    data.get("thumbnail"),
                "tags":         data.get("tags", [])[:10],
            })
        except (json.JSONDecodeError, KeyError):
            continue

    return posts


def _parse_yt_ts(ts) -> Optional[str]:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:
        return str(ts)


# ── TikTok scraper (yt-dlp user page) ────────────────────────────────────────

async def _scrape_tiktok(handle: str, max_posts: int) -> List[Dict[str, Any]]:
    """
    Pull TikTok video metadata. yt-dlp can scrape public profiles.
    For private content, Apify actor 'clockworks/tiktok-scraper' is used.
    """
    if settings.APIFY_API_TOKEN:
        return await _scrape_tiktok_apify(handle, max_posts)

    url = f"https://www.tiktok.com/@{handle}"
    cmd = [
        "yt-dlp",
        "--no-download",
        "--flat-playlist",
        "--playlist-end", str(max_posts),
        "--print-json",
        "--quiet",
        "--no-warnings",
        url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()

    posts = []
    for line in stdout.decode().splitlines():
        try:
            data = json.loads(line)
            posts.append({
                "id":           data.get("id"),
                "title":        data.get("title", ""),
                "description":  data.get("description", "")[:300],
                "duration_sec": data.get("duration"),
                "timestamp":    _parse_yt_ts(data.get("timestamp")),
                "view_count":   data.get("view_count"),
                "like_count":   data.get("like_count"),
                "comment_count": data.get("comment_count"),
                "share_count":  data.get("repost_count"),
                "url":          data.get("webpage_url"),
            })
        except (json.JSONDecodeError, KeyError):
            continue

    return posts


async def _scrape_tiktok_apify(handle: str, max_posts: int) -> List[Dict[str, Any]]:
    """Apify TikTok scraper actor as a fallback for better reliability."""
    actor_id = "clockworks/tiktok-scraper"
    url = f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
    params = {"token": settings.APIFY_API_TOKEN}
    payload = {
        "profiles": [handle],
        "resultsPerPage": max_posts,
        "shouldDownloadVideos": False,
    }
    async with httpx.AsyncClient(timeout=settings.SCRAPER_TIMEOUT_SEC) as client:
        resp = await client.post(url, params=params, json=payload)
        resp.raise_for_status()
        items = resp.json()

    posts = []
    for item in items[:max_posts]:
        posts.append({
            "id":           item.get("id"),
            "title":        item.get("text", "")[:200],
            "description":  item.get("text", "")[:300],
            "duration_sec": item.get("videoMeta", {}).get("duration"),
            "timestamp":    item.get("createTimeISO"),
            "view_count":   item.get("playCount"),
            "like_count":   item.get("diggCount"),
            "comment_count": item.get("commentCount"),
            "share_count":  item.get("shareCount"),
        })
    return posts


# ── Instagram scraper (Apify) ─────────────────────────────────────────────────

async def _scrape_instagram(handle: str, max_posts: int) -> List[Dict[str, Any]]:
    """
    Uses Apify instagram-scraper actor to pull public reel/post metadata.
    """
    from app.services.apify_service import scrape_instagram_profile
    posts = await scrape_instagram_profile(handle, max_posts)
    # Normalize to creator_scraper expected shape
    return [
        {
            "id":            p.get("id"),
            "title":         (p.get("caption") or "")[:200],
            "description":   (p.get("caption") or "")[:300],
            "duration_sec":  p.get("duration_sec"),
            "timestamp":     p.get("timestamp"),
            "view_count":    p.get("view_count"),
            "like_count":    p.get("like_count"),
            "comment_count": p.get("comment_count"),
            "media_type":    p.get("type", "Video"),
            "hashtags":      p.get("hashtags", []),
            "url":           p.get("url"),
        }
        for p in posts
    ]


# ── Pattern Analysis Engine ───────────────────────────────────────────────────

def compute_engagement_patterns(posts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Derive statistical engagement patterns from raw post data.
    No AI needed – pure aggregation.
    """
    if not posts:
        return {}

    by_hour: Dict[int, List[float]]    = defaultdict(list)
    by_day:  Dict[int, List[float]]    = defaultdict(list)
    by_length: Dict[str, List[float]]  = defaultdict(list)

    valid_posts = []
    for post in posts:
        ts_str = post.get("timestamp")
        views  = post.get("view_count") or 0
        likes  = post.get("like_count") or 0
        dur    = post.get("duration_sec")
        engagement = (likes / views) if views > 0 else 0

        if ts_str:
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                by_hour[dt.hour].append(engagement)
                by_day[dt.weekday()].append(engagement)  # 0=Mon
            except Exception:
                pass

        if dur:
            bucket = (
                "under_15s" if dur < 15 else
                "15_30s"    if dur < 30 else
                "30_60s"    if dur < 60 else
                "over_60s"
            )
            by_length[bucket].append(engagement)

        valid_posts.append({"duration_sec": dur, "engagement": engagement, "views": views})

    def _avg(lst): return round(sum(lst) / len(lst), 4) if lst else 0

    best_hours = sorted(
        [{"hour": h, "avg_engagement": _avg(v)} for h, v in by_hour.items()],
        key=lambda x: x["avg_engagement"], reverse=True,
    )[:3]

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    best_days = sorted(
        [{"day": day_names[d], "avg_engagement": _avg(v)} for d, v in by_day.items()],
        key=lambda x: x["avg_engagement"], reverse=True,
    )[:3]

    length_perf = {k: _avg(v) for k, v in by_length.items()}
    best_length_bucket = max(length_perf, key=length_perf.get) if length_perf else None

    # Best absolute duration (weighted median by engagement)
    dur_vals = [p["duration_sec"] for p in valid_posts if p["duration_sec"] and p["engagement"] > 0]
    best_duration = round(sum(dur_vals) / len(dur_vals), 1) if dur_vals else None

    return {
        "by_hour":           by_hour,
        "by_day":            by_day,
        "best_posting_times": [{"day": d["day"], "hour": h["hour"], "avg_engagement": h["avg_engagement"]}
                                for d in best_days for h in best_hours[:1]],
        "best_hours":        best_hours,
        "best_days":         best_days,
        "by_length":         length_perf,
        "best_length_bucket": best_length_bucket,
        "best_video_length_sec": best_duration,
    }


async def analyze_creator_patterns(
    posts: List[Dict[str, Any]],
    handle: str,
    platform: str,
    follower_count: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Use Claude to cluster topics, extract hooks, and generate strategic insights.
    """
    engagement_data = compute_engagement_patterns(posts)

    # Build post summaries for Claude (token-efficient)
    summaries = [
        f"[{i+1}] Title: {p.get('title','')[:100]} | "
        f"Views: {p.get('view_count','?')} | "
        f"Likes: {p.get('like_count','?')} | "
        f"Duration: {p.get('duration_sec','?')}s"
        for i, p in enumerate(posts[:40])
    ]
    posts_text = "\n".join(summaries)

    system = (
        "You are a social media analytics expert. Analyse the creator's post history "
        "and extract actionable patterns. Respond ONLY with valid JSON."
    )
    user = f"""
Creator: @{handle} on {platform}
Followers: {follower_count or "unknown"}
Posts analysed: {len(posts)}

Post performance data:
{posts_text}

Engagement stats:
- Best posting hours: {engagement_data.get('best_hours', [])}
- Best days: {engagement_data.get('best_days', [])}
- Best video length bucket: {engagement_data.get('best_length_bucket')}

Return JSON:
{{
  "top_hooks": [<str>, ...],
  "topic_clusters": [
    {{
      "cluster": "<name>",
      "keywords": [<str>],
      "post_count": <int>,
      "avg_performance": "<low|medium|high>"
    }}
  ],
  "content_style": "<description in 1-2 sentences>",
  "niche_summary": "<creator niche in 1 sentence>",
  "strategic_insights": [<str>, ...],
  "recommended_improvements": [<str>, ...],
  "avg_virality_estimate": <int 0-100>
}}
"""
    raw = await _chat(system, user, max_tokens=2000)
    ai_data = _parse_json(raw)

    return {
        **ai_data,
        "posts_scraped": len(posts),
        "best_video_length_sec": engagement_data.get("best_video_length_sec"),
        "best_posting_times": engagement_data.get("best_posting_times", []),
        "engagement_patterns": {
            "best_hours":   engagement_data.get("best_hours", []),
            "best_days":    engagement_data.get("best_days", []),
            "by_length":    engagement_data.get("by_length", {}),
        },
    }
