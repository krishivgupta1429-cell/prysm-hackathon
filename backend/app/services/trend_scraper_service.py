"""
app/services/trend_scraper_service.py
Weekly cron scraper: Apify → trending_audio + niche_hashtags + niche_formats tables.

Run via Railway cron (weekly) OR manually:
    python -m app.services.trend_scraper_service

TWO-PRONGED PIPELINE per niche:
  1. Profile scrape (primary): Scrape top creator profiles per niche using
     apify/instagram-profile-scraper. Extract format patterns, hook styles,
     and audio titles from their high-performing posts.
     → Writes to: niche_formats + trending_audio

  2. Hashtag scrape (supplementary): Scrape the top seed hashtag for additional
     audio signal breadth. Extract audio titles + co-hashtags.
     → Writes to: trending_audio + niche_hashtags

  3. Prune rows older than 8 weeks.

Profile-based data is higher quality: these are viral creators, not just
any post that used a trending hashtag.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

NICHES = [
    "Finance", "Fitness", "Comedy", "Education", "Lifestyle",
    "Business", "Food", "Tech", "Fashion", "Travel",
    "Dance", "Bollywood", "IndianCreator",
    "Motivation", "Gaming", "Beauty", "Music", "Sports",
    "Health & Wellness", "DIY & Crafts", "Photography",
    "Entertainment", "Marketing", "Vlogs", "Self Improvement",
    "Memes", "News & Politics",
]

# Top creator handles per niche — high-follower accounts that consistently go viral
# These are scraped for format patterns, audio trends, and hook styles
NICHE_CREATORS = {
    "Finance": ["humphreytalks", "andreijikh", "grahamstephan", "minoritymindset", "financialtorchlight"],
    "Fitness": ["chrisandroo", "natacha.oceane", "jeffnippard", "heatherrobertsonfit", "sydneycummings_"],
    "Comedy": ["khaby.lame", "trevorwallace", "kallmekris", "mattbellassai", "devonrodriguezart"],
    "Education": ["markrober", "veritasium", "zackdfilms", "kurzgesagt", "smartereveryday"],
    "Lifestyle": ["emmachamberlain", "zadiethoffman", "bestdressed", "abeautyedit", "nadia_aboulhosn"],
    "Business": ["garyvee", "alexhormozi", "patrickbetdavid", "shanehummus", "jasontfeifer"],
    "Food": ["gordonramsay", "joshuaweissman", "babish", "bingingwithbabish", "ethanlebuchef"],
    "Tech": ["mkbhd", "linustechtips", "mrwhosetheboss", "arunmaini", "theprimeagen"],
    "Fashion": ["mannymus", "leena.lm", "clo_official", "youdressyourselfright", "thefashionfolk"],
    "Travel": ["kylieversm", "drewbinsky", "benbartt", "caraandcody", "funforlouis"],
    "Dance": ["charlidamelio", "addisonraee", "mattwille", "notthisway_", "birdman_dances"],
    "Bollywood": ["koimoikore", "bollyvibes", "itskajalchugh", "dancecoversbyshiva", "nikhil.vijay"],
    "IndianCreator": ["bhuvan.bam22", "carryminati", "be.younick", "triggeredinsaan", "ashishchanchlani"],
    "Motivation": ["davidgoggins", "lewishowes", "tonyfair", "garyvee", "robdial"],
    "Gaming": ["ninja", "timthetatman", "dr.lupo", "pokimane", "shroud"],
    "Beauty": ["jamescharles", "nikkietutorials", "jackieaina", "glamzilla", "hindash"],
    "Music": ["adammelchor", "peachy_music", "oliviarodrigo", "lukecombs", "camilacabello"],
    "Sports": ["espn", "bleacherreport", "houseofhighlights", "worldstar", "nba"],
    "Health & Wellness": ["drjoshdaxe", "mbgsocial", "nickmiracleguide", "katimorton", "healingandlove"],
    "DIY & Crafts": ["moriah.elizabeth", "robertobalza", "hgtv", "diydave", "craftingwithkatiana"],
    "Photography": ["petersmckinnon", "brandon.woelfel", "chrisbrinleejr", "joshuanullman", "mango_street"],
    "Entertainment": ["thewillsmith", "kevinhart4real", "zendaya", "therock", "selenagomez"],
    "Marketing": ["garyvee", "neilpatel", "marketingharry", "joepulizzi", "austinarmbruster"],
    "Vlogs": ["daviddobrik", "vlogbrothers", "jamescharles", "caseyniestat", "mrbeast"],
    "Self Improvement": ["jamesclear", "brendonburchard", "tonyrobbins", "briandean", "lewishowes"],
    "Memes": ["daquan", "9gag", "thefunnyintrovert", "fuckjerry", "bestmemes"],
    "News & Politics": ["nowthisnews", "ajplusenglish", "seeker", "vox", "theintercept"],
}

NICHE_HASHTAGS = {
    "Finance": ["finance", "investing", "stockmarket", "personalfinance", "money"],
    "Fitness": ["fitness", "gym", "workout", "fitnessmotivation", "bodybuilding"],
    "Comedy": ["comedy", "funny", "comedyreels", "funnyvideos", "standupcomedy"],
    "Education": ["education", "learning", "studytips", "edutok", "knowledge"],
    "Lifestyle": ["lifestyle", "aesthetic", "dailyvlog", "lifestyleblogger", "dayinmylife"],
    "Business": ["business", "entrepreneur", "startup", "smallbusiness", "hustle"],
    "Food": ["food", "recipe", "cooking", "foodie", "healthyfood"],
    "Tech": ["tech", "technology", "gadgets", "programming", "aitools"],
    "Fashion": ["fashion", "ootd", "outfitoftheday", "fashiontrends", "styleinspo"],
    "Travel": ["travel", "wanderlust", "travelreels", "travelvlog", "adventure"],
    "Dance": ["dancereels", "dancecover", "dancechallenge", "danceindia", "choreography"],
    "Bollywood": ["bollywooddance", "bollywoodnews", "bollywoodsongs", "punjabimusic", "hindimusic"],
    "IndianCreator": ["reelsindia", "indianreels", "indiancreator", "desivibes", "bharatviral"],
    "Motivation": ["motivation", "mindset", "selfmotivation", "successmindset", "growthmindset"],
    "Gaming": ["gaming", "gamer", "gamingcommunity", "gamingclips", "gamereels"],
    "Beauty": ["beauty", "makeup", "skincare", "beautyreels", "makeuptutorial"],
    "Music": ["music", "musician", "musicreels", "originalmusic", "livemusic"],
    "Sports": ["sports", "athlete", "sportsmotivation", "sportsreels", "fitness"],
    "Health & Wellness": ["wellness", "healthylifestyle", "mentalhealth", "selfcare", "holistichealth"],
    "DIY & Crafts": ["diy", "crafts", "handmade", "diycrafts", "crafting"],
    "Photography": ["photography", "photographer", "photooftheday", "photographytips", "mobilephotography"],
    "Entertainment": ["entertainment", "celebrity", "popculturereels", "tvshows", "movies"],
    "Marketing": ["marketing", "digitalmarketing", "contentmarketing", "socialmediatips", "growthhacking"],
    "Vlogs": ["vlog", "vlogger", "dayinmylife", "vlogging", "lifestylevlog"],
    "Self Improvement": ["selfimprovement", "personaldevelopment", "habits", "productivitytips", "betterlife"],
    "Memes": ["memes", "memereels", "funnyreels", "relatablecontent", "humor"],
    "News & Politics": ["news", "currentevents", "politics", "worldnews", "breakingnews"],
}

# Format keywords to detect in captions — ordered by specificity (longest match first)
# Maps detected keyword/phrase → canonical format label
FORMAT_PATTERNS = {
    # Hook styles
    r"\bpov\b": ("POV", "hook_style"),
    r"\bday in (my|the) life\b": ("Day in my life", "content_structure"),
    r"\bgrwm\b": ("GRWM", "content_structure"),
    r"\bget ready with me\b": ("GRWM", "content_structure"),
    r"\btransformation\b": ("Transformation", "content_structure"),
    r"\bbefore (and|&) after\b": ("Before & After", "content_structure"),
    r"\bstitch\b": ("Stitch", "content_structure"),
    r"\bduet\b": ("Duet", "content_structure"),
    r"\btrend(ing)?\b": ("Trend video", "hook_style"),
    r"\bchallenge\b": ("Challenge", "hook_style"),
    r"\btutorial\b": ("Tutorial", "content_structure"),
    r"\bhow (to|i)\b": ("How-to", "content_structure"),
    r"\brating\b": ("Rating", "content_structure"),
    r"\bi tried\b": ("I tried it", "hook_style"),
    r"\bvlog\b": ("Vlog", "content_structure"),
    r"\bstorytime\b": ("Storytime", "content_structure"),
    r"\bstory time\b": ("Storytime", "content_structure"),
    r"\bexposing\b": ("Exposé", "hook_style"),
    r"\bthe truth about\b": ("Truth reveal", "hook_style"),
    r"\bnobody talks about\b": ("Hidden truth", "hook_style"),
    r"\bthings (i|you) (never|didn'?t) know\b": ("Things you didn't know", "hook_style"),
    r"\bwhat (happened|no one tells you)\b": ("What happened story", "hook_style"),
    r"\breaction\b": ("Reaction", "content_structure"),
    r"\bunboxing\b": ("Unboxing", "content_structure"),
    r"\breview\b": ("Review", "content_structure"),
    r"\bq\s*(&|and)\s*a\b": ("Q&A", "content_structure"),
    r"\bthis or that\b": ("This or That", "content_structure"),
    r"\bwould you rather\b": ("Would you rather", "content_structure"),
    r"\bmy routine\b": ("Routine", "content_structure"),
    r"\b(morning|night|evening) routine\b": ("Routine", "content_structure"),
    r"\bproductivity\b": ("Productivity content", "content_structure"),
    r"\bstudy with me\b": ("Study with me", "content_structure"),
    r"\bwork with me\b": ("Work with me", "content_structure"),
    r"\bclean with me\b": ("Clean with me", "content_structure"),
    r"\bcook with me\b": ("Cook with me", "content_structure"),
    r"\bshop with me\b": ("Shop with me", "content_structure"),
    r"\bcome with me\b": ("Come with me", "content_structure"),
    r"\bhaul\b": ("Haul", "content_structure"),
    r"\bwishlist\b": ("Wishlist", "content_structure"),
    r"\bgifting\b": ("Gift ideas", "content_structure"),
    r"\bpart \d+\b": ("Series/Part content", "content_structure"),
    r"\b\d+ things\b": ("Listicle", "hook_style"),
    r"\b\d+ ways\b": ("Listicle", "hook_style"),
    r"\b\d+ (tips|tricks|hacks|mistakes|lessons|reasons)\b": ("Listicle", "hook_style"),
    r"\bwatch till (the )?end\b": ("Watch till end hook", "caption_style"),
    r"\bcomment if\b": ("Comment CTA caption", "caption_style"),
    r"\bsave this\b": ("Save CTA caption", "caption_style"),
    r"\bshare (this|if)\b": ("Share CTA caption", "caption_style"),
    r"\bfollow for more\b": ("Follow CTA caption", "caption_style"),
    r"\blink in bio\b": ("Link in bio CTA", "caption_style"),
}


# Hashtags so generic they appear in every niche — exclude from niche recommendations
GENERIC_BLACKLIST = {
    "reels", "viral", "trending", "foryou", "fyp", "explore", "instagram",
    "instagood", "video", "reel", "love", "follow", "like", "share",
    "india", "reelsinstagram", "reelsindia", "reelviral",
    "explorepage", "instareels", "instadaily", "igreels",
    "dancereels", "dancecover", "dancechallenge", "dancevideo", "dance",
    "bollywood", "bollywooddance", "indianreels", "indiancreator", "desi",
    "fitness", "workout", "gym", "gaming", "gamer", "beauty", "makeup",
    "music", "musician", "sports", "athlete", "photography", "photographer",
    "travel", "vlog", "vlogger", "memes", "news", "politics", "marketing",
    "motivation", "education", "lifestyle", "fashion", "food", "tech",
    "business", "comedy", "funny", "entertainment", "wellness", "diy", "crafts",
}


def _week_start() -> datetime:
    """Monday 00:00 UTC of the current week."""
    today = datetime.now(timezone.utc)
    return (today - timedelta(days=today.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _extract_hook_line(caption: str) -> Optional[str]:
    """Extract the first line of a caption as the hook."""
    if not caption:
        return None
    lines = [l.strip() for l in caption.split("\n") if l.strip()]
    if lines:
        first = lines[0]
        # Only return if it looks like a real hook (not just hashtags)
        if not first.startswith("#") and len(first) > 10:
            return first[:120]  # cap at 120 chars
    return None


def _detect_formats(caption: str) -> List[tuple]:
    """
    Scan caption for format keywords.
    Returns list of (format_label, format_type) tuples.
    """
    if not caption:
        return []
    caption_lower = caption.lower()
    found = []
    seen_labels = set()
    for pattern, (label, ftype) in FORMAT_PATTERNS.items():
        if label not in seen_labels and re.search(pattern, caption_lower):
            found.append((label, ftype))
            seen_labels.add(label)
    return found


def _extract_audio_from_post(post: Dict[str, Any]) -> Optional[str]:
    """Extract a non-generic audio title from an Apify post item.
    Handles both instagram-scraper flat musicInfo and instagram-hashtag-scraper nested format."""
    music = post.get("musicInfo", {}) or {}

    # instagram-scraper (profile scrape) returns flat: {song_name, artist_name, uses_original_audio}
    flat_title = music.get("song_name", "")
    flat_artist = music.get("artist_name", "")
    uses_original = music.get("uses_original_audio", False)

    if flat_title and not uses_original and flat_title.lower() not in ("original audio", "original sound", ""):
        label = f"{flat_title} — {flat_artist}" if flat_artist else flat_title
        return label

    # instagram-hashtag-scraper returns nested: {music_info: {music_asset_info: {title, ...}}}
    music_info = (music.get("music_info") or {})
    asset = (music_info.get("music_asset_info") or {})
    orig = (music.get("original_sound_info") or {})
    consumption = (music_info.get("music_consumption_info") or {})
    ig_artist_nested = (consumption.get("ig_artist") or {})
    ig_artist_orig = (orig.get("ig_artist") or {})

    nested_title = (
        asset.get("title") or
        asset.get("sanitized_title") or
        orig.get("original_audio_title") or
        music.get("music_title") or ""
    )
    nested_artist = (
        asset.get("display_artist") or
        ig_artist_nested.get("full_name") or
        ig_artist_orig.get("full_name") or
        music.get("artist_name") or ""
    )
    if nested_title and nested_title.lower() not in ("original audio", "original sound", ""):
        label = f"{nested_title} — {nested_artist}" if nested_artist else nested_title
        return label

    return None


def _scrape_creator_profiles(niche: str, apify_token: str, posts_per_creator: int = 20) -> Dict[str, Any]:
    """
    Scrape top creator profiles for a niche using instagram-scraper with profile directUrls.
    Extracts format patterns, hook lines, and audio titles from their posts.
    Only uses posts from the last 30 days to stay current.
    Uses actor shu8hvrXbJbY3Eb9W (instagram-scraper) which supports profile post scraping.
    """
    import requests as _requests

    creators = NICHE_CREATORS.get(niche, [])
    if not creators:
        logger.warning(f"[Scraper] No creators defined for niche: {niche}")
        return {}

    # Use up to 3 creators to keep Apify costs manageable
    target_creators = creators[:3]
    profile_urls = [f"https://www.instagram.com/{handle}/" for handle in target_creators]
    logger.info(f"[Scraper] Scraping profiles for {niche}: {target_creators}")

    try:
        resp = _requests.post(
            "https://api.apify.com/v2/acts/shu8hvrXbJbY3Eb9W/run-sync-get-dataset-items",
            params={"token": apify_token},
            json={
                "directUrls": profile_urls,
                "resultsType": "posts",
                "resultsLimit": posts_per_creator,
            },
            timeout=180,
        )
        resp.raise_for_status()
        items = resp.json()
    except Exception as e:
        logger.error(f"[Scraper] Profile scrape failed for {niche}: {e}")
        return {}

    # Filter out error items (items where likes is None and caption is empty)
    items = [it for it in items if it.get("likesCount") is not None or it.get("caption")]

    if not items:
        logger.warning(f"[Scraper] No valid posts returned for {niche} creators")
        return {}

    logger.info(f"[Scraper] Got {len(items)} valid posts from {niche} creators")

    # Cutoff: only consider posts from last 60 days (weekly scrape ensures data stays fresh)
    cutoff = datetime.now(timezone.utc) - timedelta(days=60)

    # ── Format pattern aggregation ─────────────────────────────────────────────
    # format_label → {type, frequency, total_likes, total_comments, hooks, creators}
    format_data: Dict[str, Dict[str, Any]] = {}

    # ── Audio aggregation ──────────────────────────────────────────────────────
    audio_counts: Dict[str, Dict[str, Any]] = {}

    for post in items:
        # Filter by recency
        timestamp_str = post.get("timestamp") or post.get("taken_at_timestamp")
        if timestamp_str:
            try:
                if isinstance(timestamp_str, int):
                    post_time = datetime.fromtimestamp(timestamp_str, tz=timezone.utc)
                else:
                    post_time = datetime.fromisoformat(str(timestamp_str).replace("Z", "+00:00"))
                if post_time < cutoff:
                    continue
            except Exception:
                pass  # If we can't parse, include it anyway

        likes = post.get("likesCount", 0) or 0
        comments = post.get("commentsCount", 0) or 0
        caption = post.get("caption", "") or ""
        owner = post.get("ownerUsername", "") or post.get("username", "") or ""

        # Detect formats in this post's caption
        formats_found = _detect_formats(caption)
        hook_line = _extract_hook_line(caption)

        for label, ftype in formats_found:
            if label not in format_data:
                format_data[label] = {
                    "format_label": label,
                    "format_type": ftype,
                    "frequency": 0,
                    "total_likes": 0,
                    "total_comments": 0,
                    "example_hooks": [],
                    "source_creators": set(),
                }
            fd = format_data[label]
            fd["frequency"] += 1
            fd["total_likes"] += likes
            fd["total_comments"] += comments
            if owner:
                fd["source_creators"].add(owner)
            if hook_line and len(fd["example_hooks"]) < 5:
                fd["example_hooks"].append(hook_line)

        # Extract audio
        audio_label = _extract_audio_from_post(post)
        if audio_label:
            key = audio_label.lower().strip()
            if key not in audio_counts:
                audio_counts[key] = {
                    "audio_title": audio_label,
                    "usage_count": 0,
                    "avg_likes": 0,
                    "total_likes": 0,
                    "sample_posts": [],
                    "source": "creator_profile",
                }
            audio_counts[key]["usage_count"] += 1
            audio_counts[key]["total_likes"] += likes
            if len(audio_counts[key]["sample_posts"]) < 3:
                audio_counts[key]["sample_posts"].append({
                    "likes": likes,
                    "comments": comments,
                    "caption_preview": caption[:100],
                    "creator": owner,
                })

    # Finalize format data
    top_formats = []
    for label, fd in format_data.items():
        if fd["frequency"] >= 1:  # at least 1 occurrence
            avg_likes = round(fd["total_likes"] / fd["frequency"], 1) if fd["frequency"] > 0 else 0
            avg_comments = round(fd["total_comments"] / fd["frequency"], 1) if fd["frequency"] > 0 else 0
            top_formats.append({
                "format_label": fd["format_label"],
                "format_type": fd["format_type"],
                "frequency": fd["frequency"],
                "avg_likes": avg_likes,
                "avg_comments": avg_comments,
                "example_hooks": fd["example_hooks"][:5],
                "source_creators": list(fd["source_creators"])[:5],
            })
    # Sort by frequency × avg_likes (engagement-weighted)
    top_formats.sort(key=lambda x: x["frequency"] * (x["avg_likes"] + 1), reverse=True)
    top_formats = top_formats[:20]

    # Finalize audio data
    top_audios = sorted(audio_counts.values(), key=lambda x: x["usage_count"], reverse=True)[:15]
    for a in top_audios:
        a["avg_likes"] = round(a["total_likes"] / a["usage_count"], 1) if a["usage_count"] > 0 else 0
        del a["total_likes"]

    logger.info(
        f"[Scraper] {niche} creators: {len(top_formats)} format patterns, "
        f"{len(top_audios)} audios from {len(items)} posts"
    )
    return {"formats": top_formats, "audios": top_audios, "source": "creator_profiles"}


def _scrape_hashtag(niche: str, apify_token: str, results_limit: int = 30) -> Dict[str, Any]:
    """
    Supplementary hashtag scrape for additional audio + hashtag breadth.
    Uses apify/instagram-hashtag-scraper on the primary seed hashtag.
    Lower limit than before since creator profiles are now primary source.
    """
    import requests as _requests

    hashtags = NICHE_HASHTAGS.get(niche, [niche.lower()])
    search_tag = hashtags[0]
    logger.info(f"[Scraper] Supplementary hashtag scrape: #{search_tag} for {niche}")

    try:
        resp = _requests.post(
            "https://api.apify.com/v2/acts/apify~instagram-hashtag-scraper/run-sync-get-dataset-items",
            params={"token": apify_token},
            json={
                "hashtags": [search_tag],
                "resultsLimit": results_limit,
                "resultsType": "posts",
            },
            timeout=120,
        )
        resp.raise_for_status()
        items = resp.json()
    except Exception as e:
        logger.error(f"[Scraper] Hashtag scrape failed for {niche}: {e}")
        return {}

    if not items:
        return {}

    audio_counts: Dict[str, Dict[str, Any]] = {}
    hashtag_counts: Dict[str, Dict[str, Any]] = {}

    for post in items:
        likes = post.get("likesCount", 0) or 0
        caption = post.get("caption", "") or ""
        tags_in_post = re.findall(r"#(\w+)", caption)

        audio_label = _extract_audio_from_post(post)
        if audio_label:
            key = audio_label.lower().strip()
            if key not in audio_counts:
                audio_counts[key] = {
                    "audio_title": audio_label,
                    "usage_count": 0,
                    "total_likes": 0,
                    "sample_posts": [],
                    "co_hashtags": [],
                    "source": "hashtag_scrape",
                }
            audio_counts[key]["usage_count"] += 1
            audio_counts[key]["total_likes"] += likes
            if len(audio_counts[key]["sample_posts"]) < 2:
                audio_counts[key]["sample_posts"].append({
                    "likes": likes,
                    "caption_preview": caption[:80],
                })
            for tag in tags_in_post:
                tl = tag.lower()
                if tl not in GENERIC_BLACKLIST and len(tl) > 2:
                    if tl not in audio_counts[key]["co_hashtags"]:
                        audio_counts[key]["co_hashtags"].append(tl)

        for tag in tags_in_post:
            tl = tag.lower()
            if tl in GENERIC_BLACKLIST or len(tl) <= 2:
                continue
            if tl in [h.lower() for h in hashtags]:
                continue
            if tl not in hashtag_counts:
                hashtag_counts[tl] = {"hashtag": tag, "frequency": 0, "total_likes": 0}
            hashtag_counts[tl]["frequency"] += 1
            hashtag_counts[tl]["total_likes"] += likes

    top_audios = sorted(audio_counts.values(), key=lambda x: x["usage_count"], reverse=True)[:10]
    for a in top_audios:
        a["avg_likes"] = round(a["total_likes"] / a["usage_count"], 1) if a["usage_count"] > 0 else 0
        del a["total_likes"]

    top_hashtags = sorted(hashtag_counts.values(), key=lambda x: x["frequency"], reverse=True)[:30]
    for h in top_hashtags:
        h["avg_likes"] = round(h["total_likes"] / h["frequency"], 1) if h["frequency"] > 0 else 0
        del h["total_likes"]

    logger.info(
        f"[Scraper] {niche} hashtag: {len(top_audios)} audios, "
        f"{len(top_hashtags)} hashtags from {len(items)} posts"
    )
    return {"audios": top_audios, "hashtags": top_hashtags}


async def run_weekly_scrape(niches: Optional[List[str]] = None, platform: str = "instagram"):
    """
    Entry point for the weekly cron job.
    Scrapes all niches using creator profiles (primary) + hashtags (supplementary).
    Writes to niche_formats + trending_audio + niche_hashtags.
    Safe to call multiple times in the same week — upserts by (niche, platform, week_start).
    """
    from app.core.config import settings
    from app.core.database import AsyncSessionLocal

    if not settings.APIFY_API_TOKEN:
        logger.error("[Scraper] No APIFY_API_TOKEN — cannot scrape")
        return {"error": "No Apify token"}

    target_niches = niches or NICHES
    week = _week_start()
    results = {"week": week.isoformat(), "niches": {}}

    for niche in target_niches:
        try:
            # Step 1: Creator profile scrape (primary — format patterns + quality audio)
            creator_data = _scrape_creator_profiles(niche, settings.APIFY_API_TOKEN)

            # Step 2: Hashtag scrape (supplementary — additional audio + hashtags)
            hashtag_data = _scrape_hashtag(niche, settings.APIFY_API_TOKEN)

            if not creator_data and not hashtag_data:
                results["niches"][niche] = {"status": "no_data"}
                continue

            # Merge audio from both sources — deduplicate by lowercase title
            merged_audios = _merge_audios(
                creator_data.get("audios", []),
                hashtag_data.get("audios", []),
            )

            combined = {
                "formats": creator_data.get("formats", []),
                "audios": merged_audios,
                "hashtags": hashtag_data.get("hashtags", []),
            }

            async with AsyncSessionLocal() as db:
                await _upsert_niche_data(db, niche, platform, week, combined)
                await db.commit()

            results["niches"][niche] = {
                "status": "ok",
                "formats": len(combined.get("formats", [])),
                "audios": len(combined.get("audios", [])),
                "hashtags": len(combined.get("hashtags", [])),
            }
        except Exception as e:
            logger.error(f"[Scraper] Failed for {niche}: {e}", exc_info=True)
            results["niches"][niche] = {"status": "error", "error": str(e)}

    # Prune rows older than 8 weeks
    try:
        async with AsyncSessionLocal() as db:
            await _prune_old_rows(db, cutoff_weeks=8)
            await db.commit()
        logger.info("[Scraper] Pruned rows older than 8 weeks")
    except Exception as e:
        logger.error(f"[Scraper] Prune failed: {e}")

    logger.info(f"[Scraper] Weekly scrape complete: {results}")
    return results


def _merge_audios(primary: List[Dict], secondary: List[Dict]) -> List[Dict]:
    """
    Merge audio lists from creator profiles (primary) and hashtag scrape (secondary).
    Creator-profile audios take precedence; hashtag audios fill in gaps.
    Deduplicates by lowercase title prefix.
    """
    seen: Dict[str, Dict] = {}
    for a in primary:
        key = a["audio_title"].lower()[:40]
        seen[key] = a
    for a in secondary:
        key = a["audio_title"].lower()[:40]
        if key not in seen:
            seen[key] = a
    merged = list(seen.values())
    merged.sort(key=lambda x: x["usage_count"], reverse=True)
    return merged[:20]


async def _upsert_niche_data(db, niche: str, platform: str, week: datetime, data: Dict[str, Any]):
    """Delete this week's existing rows for the niche and insert fresh ones."""
    from sqlalchemy import text
    import json as _json

    now = datetime.now(timezone.utc)

    # Delete existing rows (idempotent re-run)
    await db.execute(
        text("DELETE FROM trending_audio WHERE niche=:n AND platform=:p AND week_start=:w"),
        {"n": niche, "p": platform, "w": week},
    )
    await db.execute(
        text("DELETE FROM niche_hashtags WHERE niche=:n AND platform=:p AND week_start=:w"),
        {"n": niche, "p": platform, "w": week},
    )
    await db.execute(
        text("DELETE FROM niche_formats WHERE niche=:n AND platform=:p AND week_start=:w"),
        {"n": niche, "p": platform, "w": week},
    )

    # Insert format rows
    for fmt in data.get("formats", []):
        await db.execute(
            text("""
                INSERT INTO niche_formats
                    (id, niche, platform, format_label, format_type, frequency,
                     avg_likes, avg_comments, example_hooks, source_creators, week_start, created_at)
                VALUES
                    (:id, :niche, :platform, :format_label, :format_type, :frequency,
                     :avg_likes, :avg_comments, cast(:example_hooks as json),
                     cast(:source_creators as json), :week_start, :created_at)
            """),
            {
                "id": str(uuid.uuid4()),
                "niche": niche,
                "platform": platform,
                "format_label": fmt["format_label"],
                "format_type": fmt.get("format_type", ""),
                "frequency": fmt["frequency"],
                "avg_likes": fmt["avg_likes"],
                "avg_comments": fmt.get("avg_comments", 0.0),
                "example_hooks": _json.dumps(fmt.get("example_hooks", [])),
                "source_creators": _json.dumps(fmt.get("source_creators", [])),
                "week_start": week,
                "created_at": now,
            },
        )

    # Insert audio rows
    for audio in data.get("audios", []):
        # Handle both formats: "title" key or "audio_title" key
        title = audio.get("audio_title") or audio.get("title", "")
        if not title:
            continue
        # Split "Title — Artist" back if needed
        artist = audio.get("artist_name", "")
        if not artist and " — " in title:
            parts = title.split(" — ", 1)
            title = parts[0]
            artist = parts[1]
        await db.execute(
            text("""
                INSERT INTO trending_audio
                    (id, niche, platform, audio_title, artist_name, usage_count,
                     avg_likes, genre, mood, co_hashtags, week_start, sample_posts, created_at)
                VALUES
                    (:id, :niche, :platform, :audio_title, :artist_name, :usage_count,
                     :avg_likes, :genre, :mood, cast(:co_hashtags as json),
                     :week_start, cast(:sample_posts as json), :created_at)
            """),
            {
                "id": str(uuid.uuid4()),
                "niche": niche,
                "platform": platform,
                "audio_title": title,
                "artist_name": artist,
                "usage_count": audio["usage_count"],
                "avg_likes": audio["avg_likes"],
                "genre": None,
                "mood": None,
                "co_hashtags": _json.dumps(audio.get("co_hashtags", [])),
                "week_start": week,
                "sample_posts": _json.dumps(audio.get("sample_posts", [])),
                "created_at": now,
            },
        )

    # Insert hashtag rows
    for htag in data.get("hashtags", []):
        await db.execute(
            text("""
                INSERT INTO niche_hashtags
                    (id, niche, platform, hashtag, frequency, avg_likes, week_start, created_at)
                VALUES
                    (:id, :niche, :platform, :hashtag, :frequency, :avg_likes, :week_start, :created_at)
            """),
            {
                "id": str(uuid.uuid4()),
                "niche": niche,
                "platform": platform,
                "hashtag": htag["hashtag"],
                "frequency": htag["frequency"],
                "avg_likes": htag["avg_likes"],
                "week_start": week,
                "created_at": now,
            },
        )

    logger.info(
        f"[Scraper] Wrote {len(data.get('formats', []))} formats + "
        f"{len(data.get('audios', []))} audios + "
        f"{len(data.get('hashtags', []))} hashtags for {niche}/{platform}"
    )


async def _prune_old_rows(db, cutoff_weeks: int = 8):
    from sqlalchemy import text
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=cutoff_weeks)
    await db.execute(text("DELETE FROM trending_audio WHERE week_start < :cutoff"), {"cutoff": cutoff})
    await db.execute(text("DELETE FROM niche_hashtags WHERE week_start < :cutoff"), {"cutoff": cutoff})
    await db.execute(text("DELETE FROM niche_formats WHERE week_start < :cutoff"), {"cutoff": cutoff})


async def get_trending_from_db(niche: str, platform: str = "instagram") -> Dict[str, Any]:
    """
    Read the latest week's trending data from DB for a given niche.
    Now returns format patterns (primary) + audio + hashtags.
    Returns {} if no data found for this week or last week.
    """
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    week = _week_start()
    last_week = week - timedelta(weeks=1)

    async with AsyncSessionLocal() as db:
        for target_week in (week, last_week):
            # Format patterns (from creator profiles)
            format_rows = (await db.execute(
                text("""
                    SELECT format_label, format_type, frequency, avg_likes,
                           avg_comments, example_hooks, source_creators
                    FROM niche_formats
                    WHERE niche=:n AND platform=:p AND week_start=:w
                    ORDER BY frequency DESC, avg_likes DESC
                    LIMIT 12
                """),
                {"n": niche, "p": platform, "w": target_week},
            )).fetchall()

            audio_rows = (await db.execute(
                text("""
                    SELECT audio_title, artist_name, usage_count, avg_likes,
                           genre, mood, co_hashtags, sample_posts
                    FROM trending_audio
                    WHERE niche=:n AND platform=:p AND week_start=:w
                    ORDER BY usage_count DESC
                    LIMIT 15
                """),
                {"n": niche, "p": platform, "w": target_week},
            )).fetchall()

            hashtag_rows = (await db.execute(
                text("""
                    SELECT hashtag, frequency, avg_likes
                    FROM niche_hashtags
                    WHERE niche=:n AND platform=:p AND week_start=:w
                    ORDER BY frequency DESC
                    LIMIT 20
                """),
                {"n": niche, "p": platform, "w": target_week},
            )).fetchall()

            if format_rows or audio_rows or hashtag_rows:
                import json as _json

                # Build format summaries
                formats = []
                for r in format_rows:
                    hooks = r[5]
                    hook_list = _json.loads(hooks) if isinstance(hooks, str) else (hooks or [])
                    formats.append({
                        "label": r[0],
                        "type": r[1] or "",
                        "frequency": r[2],
                        "avg_likes": r[3],
                        "example_hooks": hook_list[:3],
                    })

                audios = []
                for r in audio_rows:
                    title = r[0]
                    artist = r[1] or ""
                    label = f"{title} — {artist}" if artist else title
                    audios.append(label)

                hashtags = [f"#{r[0]}" for r in hashtag_rows]

                avg_likes = round(
                    sum(r[3] for r in audio_rows) / len(audio_rows), 0
                ) if audio_rows else 0

                top_posts = []
                for r in audio_rows[:5]:
                    raw = r[7]
                    posts = _json.loads(raw) if isinstance(raw, str) else (raw or [])
                    for p in posts[:1]:
                        top_posts.append({**p, "audio": r[0]})

                staleness = "this week" if target_week == week else "last week"
                logger.info(
                    f"[Scraper] DB hit ({staleness}): {len(formats)} formats, "
                    f"{len(audios)} audios, {len(hashtags)} hashtags for {niche}/{platform}"
                )
                return {
                    "source": f"db_{staleness.replace(' ', '_')}",
                    "postsAnalyzed": len(audio_rows) + len(hashtag_rows) + len(format_rows),
                    "trendingFormats": formats,
                    "trendingHashtags": hashtags,
                    "trendingAudios": audios,
                    "avgLikes": int(avg_likes),
                    "avgComments": 0,
                    "topPosts": top_posts,
                }

    logger.info(f"[Scraper] No DB data for {niche}/{platform}")
    return {}


async def lookup_song_in_trends(
    song_name: str,
    platform: str = "instagram",
    fuzzy: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Check if a specific song is currently trending in our DB.
    Searches trending_audio across ALL niches for the past 2 weeks.
    Returns {} if not found.
    """
    if not song_name or len(song_name.strip()) < 2:
        return {}

    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    week = _week_start()
    last_week = week - timedelta(weeks=1)
    cutoff = last_week

    clean = song_name.strip()

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            text("""
                SELECT audio_title, artist_name, usage_count, avg_likes,
                       co_hashtags, niche, week_start
                FROM trending_audio
                WHERE platform = :platform
                  AND week_start >= :cutoff
                  AND audio_title ILIKE :title
                ORDER BY usage_count DESC, avg_likes DESC
                LIMIT 5
            """),
            {"platform": platform, "cutoff": cutoff, "title": clean},
        )).fetchall()

        if not rows and fuzzy and len(clean) >= 4:
            rows = (await db.execute(
                text("""
                    SELECT audio_title, artist_name, usage_count, avg_likes,
                           co_hashtags, niche, week_start
                    FROM trending_audio
                    WHERE platform = :platform
                      AND week_start >= :cutoff
                      AND audio_title ILIKE :pattern
                    ORDER BY usage_count DESC, avg_likes DESC
                    LIMIT 5
                """),
                {"platform": platform, "cutoff": cutoff, "pattern": f"%{clean}%"},
            )).fetchall()

        if not rows:
            logger.info(f"[SongLookup] '{song_name}' not found in trending DB")
            return {}

        import json as _json
        total_usage = sum(r[2] for r in rows)
        best_avg_likes = max(r[3] for r in rows)
        niches_found = list({r[5] for r in rows})

        all_co_tags: List[str] = []
        for r in rows:
            raw = r[4]
            tags = _json.loads(raw) if isinstance(raw, str) else (raw or [])
            all_co_tags.extend(tags)
        seen: set = set()
        co_tags = [t for t in all_co_tags if not (t in seen or seen.add(t))][:10]  # type: ignore

        best_row = rows[0]
        staleness = "this week" if best_row[6] >= week else "last week"
        result = {
            "found": True,
            "audio_title": best_row[0],
            "artist_name": best_row[1] or "",
            "usage_count": total_usage,
            "avg_likes": round(best_avg_likes, 0),
            "niches": niches_found,
            "co_hashtags": co_tags,
            "staleness": staleness,
        }
        logger.info(
            f"[SongLookup] '{song_name}' FOUND: {total_usage} uses across {niches_found}, "
            f"avg_likes={best_avg_likes:.0f}, staleness={staleness}"
        )
        return result


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import sys

    logging.basicConfig(level=logging.INFO)
    niches_arg = sys.argv[1:] or None
    asyncio.run(run_weekly_scrape(niches=niches_arg))
