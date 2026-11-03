"""
app/services/apify_service.py
Apify scraping service — three Instagram actors:
  1. apify/instagram-scraper       — creator profile + post performance
  2. apify/instagram-reel-scraper  — individual Reel deep data (transcript, shares, etc.)
  3. apify/instagram-hashtag-scraper — real-time hashtag intelligence
"""

import httpx
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("apify_service")

APIFY_BASE = "https://api.apify.com/v2/acts"
TIMEOUT = 120


async def _run_actor(actor_id: str, payload: Dict) -> List[Dict]:
    """Run an Apify actor synchronously and return dataset items."""
    if not settings.APIFY_API_TOKEN:
        logger.warning("APIFY_API_TOKEN not set — skipping Apify call")
        return []

    url = f"{APIFY_BASE}/{actor_id}/run-sync-get-dataset-items"
    params = {"token": settings.APIFY_API_TOKEN}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(url, params=params, json=payload)
        resp.raise_for_status()
        return resp.json()


# ── 1. Instagram Profile + Posts Scraper ─────────────────────────────────────

async def scrape_instagram_profile(
    handle: str,
    max_posts: int = 30,
) -> List[Dict[str, Any]]:
    """
    Scrape a public Instagram profile's recent posts/reels.
    Returns list of posts with views, likes, comments, caption, timestamp.
    """
    handle = handle.lstrip("@")
    logger.info("Apify: scraping profile @%s (max %d posts)", handle, max_posts)

    items = await _run_actor(
        "apify/instagram-scraper",
        {
            "usernames": [handle],
            "resultsType": "posts",
            "resultsLimit": max_posts,
            "addParentData": False,
        },
    )

    posts = []
    for item in items:
        posts.append({
            "id":            item.get("id"),
            "url":           item.get("url"),
            "caption":       (item.get("caption") or "")[:500],
            "timestamp":     item.get("timestamp"),
            "view_count":    item.get("videoViewCount") or item.get("playCount") or 0,
            "like_count":    item.get("likesCount") or 0,
            "comment_count": item.get("commentsCount") or 0,
            "duration_sec":  item.get("videoDuration"),
            "hashtags":      item.get("hashtags", []),
            "mentions":      item.get("mentions", []),
            "type":          item.get("type", "Video"),
            "thumbnail":     item.get("displayUrl"),
        })

    logger.info("Apify: got %d posts for @%s", len(posts), handle)
    return posts


# ── 2. Instagram Reel Deep Scraper ────────────────────────────────────────────

async def scrape_reel(
    reel_url_or_username: str,
    max_reels: int = 10,
) -> List[Dict[str, Any]]:
    """
    Deep scrape individual Reels — gets transcript, shares, duration, hashtags.
    Pass either a direct Reel URL or an Instagram username.
    """
    logger.info("Apify: deep scraping reels for %s", reel_url_or_username)

    items = await _run_actor(
        "apify/instagram-reel-scraper",
        {
            "urls": [reel_url_or_username],
            "resultsLimit": max_reels,
            "includeTranscript": True,
        },
    )

    reels = []
    for item in items:
        reels.append({
            "id":            item.get("id"),
            "url":           item.get("url"),
            "caption":       (item.get("caption") or "")[:500],
            "transcript":    (item.get("transcript") or "")[:2000],
            "timestamp":     item.get("timestamp"),
            "view_count":    item.get("videoViewCount") or item.get("playCount") or 0,
            "like_count":    item.get("likesCount") or 0,
            "comment_count": item.get("commentsCount") or 0,
            "share_count":   item.get("sharesCount") or 0,
            "duration_sec":  item.get("videoDuration"),
            "hashtags":      item.get("hashtags", []),
            "audio":         item.get("musicInfo", {}).get("songName") or item.get("audio"),
            "thumbnail":     item.get("displayUrl"),
        })

    logger.info("Apify: got %d reels", len(reels))
    return reels


# ── 3. Instagram Hashtag Intelligence ────────────────────────────────────────

async def scrape_hashtag(
    hashtag: str,
    max_posts: int = 50,
) -> Dict[str, Any]:
    """
    Scrape posts under a hashtag to assess reach, engagement, and saturation.
    Returns summary stats useful for hashtag recommendations.
    """
    hashtag = hashtag.lstrip("#")
    logger.info("Apify: scraping hashtag #%s", hashtag)

    items = await _run_actor(
        "apify/instagram-hashtag-scraper",
        {
            "hashtags": [hashtag],
            "resultsLimit": max_posts,
        },
    )

    if not items:
        return {"hashtag": hashtag, "post_count": 0, "avg_likes": 0, "avg_views": 0}

    total_likes = sum(i.get("likesCount") or 0 for i in items)
    total_views = sum(i.get("videoViewCount") or i.get("playCount") or 0 for i in items)
    total_comments = sum(i.get("commentsCount") or 0 for i in items)
    count = len(items)

    avg_likes = round(total_likes / count) if count else 0
    avg_views = round(total_views / count) if count else 0
    avg_comments = round(total_comments / count) if count else 0
    avg_engagement = round((total_likes + total_comments) / max(total_views, 1) * 100, 2)

    # Saturation estimate: high avg_views + low avg_engagement = saturated
    if avg_views > 100000 and avg_engagement < 2:
        saturation = "high"
    elif avg_views > 50000 and avg_engagement < 5:
        saturation = "medium"
    else:
        saturation = "low"

    return {
        "hashtag":        hashtag,
        "posts_sampled":  count,
        "avg_likes":      avg_likes,
        "avg_views":      avg_views,
        "avg_comments":   avg_comments,
        "avg_engagement_pct": avg_engagement,
        "saturation":     saturation,
        "recommended":    saturation in ("low", "medium"),
    }


async def analyze_hashtags(hashtags: List[str], max_per_tag: int = 30) -> List[Dict[str, Any]]:
    """
    Analyze a list of hashtags and return ranked recommendations.
    Used to validate/replace hashtags in Prysm analysis results.
    """
    import asyncio
    results = await asyncio.gather(
        *[scrape_hashtag(tag, max_per_tag) for tag in hashtags[:10]],
        return_exceptions=True,
    )

    valid = [r for r in results if isinstance(r, dict)]
    # Sort by recommended first, then by avg_views descending
    valid.sort(key=lambda x: (not x.get("recommended"), -x.get("avg_views", 0)))
    return valid
