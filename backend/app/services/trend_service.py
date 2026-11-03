"""
app/services/trend_service.py
Real trend data from DB cache + Gemini Google Search (free, real-time).

Pipeline:
  1. Check Redis cache (trends cached for 24 hours per niche)
  2. DB lookup — pre-scraped weekly data if available (fast, < 5ms)
  3. Gemini Google Search — real-time trend context focused on video keywords
  4. Combine both into structured trend data
  5. Cache result for 24 hours

Apify is NO LONGER used for trend scraping — too expensive at scale.
Apify is still used only for Instagram URL video download in prysm_analyse_service.py.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Gemini: Google Search for latest trends ──────────────────────────────────

# Niches that should include India/South Asia geo-context in Gemini search
_INDIA_NICHES = {"dance", "bollywood", "indiancreator", "food"}


def search_current_trends(
    niche: str,
    platform: str,
    keywords: Optional[List[str]] = None,
    mood: Optional[str] = None,
    energy: Optional[str] = None,
    scene_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Use Gemini with Google Search grounding for real-time trend data.

    keywords:   content keywords from vision (e.g. ['outfit transformation', 'lehenga'])
    mood:       music mood from Gemini audio (e.g. 'romantic', 'energetic', 'melancholic')
    energy:     music energy from Gemini audio (e.g. 'high', 'medium', 'low')
    scene_type: dominant scene type from vision (e.g. 'transformation', 'talking-head', 'B-roll montage')
    """
    if not settings.GEMINI_API_KEY:
        logger.warning("[Trends] No Gemini key — skipping Google search")
        return {}

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.GEMINI_API_KEY)

        # Add India geo-context for relevant niches
        india_niches = niche.lower() in _INDIA_NICHES
        geo_context = (
            " Focus specifically on India, Indian creators, and Bollywood/desi content trends."
            if india_niches else ""
        )
        geo_note = (
            " Include trending Bollywood songs being used in reels, viral Indian dance challenges,"
            " and top Indian creator handles currently going viral."
            if india_niches else ""
        )

        # Build search subject from keywords if available
        if keywords:
            keyword_str = ", ".join(keywords[:4])
            search_subject = f"content about '{keyword_str}' (niche: {niche})"
            keyword_note = f"\n\nThis specific video is about: {keyword_str}. Focus your trend search on whether THIS specific topic/format is currently trending, not just {niche} in general."
        else:
            search_subject = f"content in the {niche} niche"
            keyword_note = ""

        # Build vibe context from audio + vision signals — this is the key enrichment
        vibe_parts = []
        if scene_type and scene_type.lower() not in ("unknown", "none", ""):
            vibe_parts.append(f"video format/style: {scene_type}")
        if mood and mood.lower() not in ("unknown", "none", ""):
            vibe_parts.append(f"mood: {mood}")
        if energy and energy.lower() not in ("unknown", "none", ""):
            vibe_parts.append(f"energy level: {energy}")

        if vibe_parts:
            vibe_str = ", ".join(vibe_parts)
            vibe_note = f"\n\nTHIS VIDEO'S VIBE: {vibe_str}. This is critical for audio recommendations — ONLY suggest songs that match this exact mood and energy. A {energy or 'medium'}-energy {mood or ''} {scene_type or ''} video needs audio that fits that vibe specifically, not just any trending song in the niche."
            audio_instruction = f"list 5-8 specific song/audio track names that are currently trending on {platform} for {niche} content AND match the vibe of this video ({vibe_str}) — include artist name, e.g. 'Espresso by Sabrina Carpenter'. These must be REAL songs being widely used in reels right now that fit this specific mood and energy. Do NOT suggest high-energy hype tracks for a calm video or vice versa."
        else:
            vibe_note = ""
            audio_instruction = f"list 5-8 specific song/audio track names that are currently trending on {platform} for this type of content — include artist name, e.g. 'Espresso by Sabrina Carpenter', 'APT. by Rose ft. Bruno Mars'. These must be REAL songs being widely used in reels right now for this content type and mood."

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"""Search Google for the LATEST trending {search_subject} on {platform} right now in 2026.{geo_context}{keyword_note}{vibe_note}

Find real, current data and return ONLY valid JSON:
{{
  "trendingFormats": ["list 5 specific content formats currently going viral for this type of content"],
  "trendingTopics": ["list 5 specific topics trending right now that match this video's content"],
  "viralTechniques": ["list 3 specific editing or content techniques currently driving views for this type of content"],
  "trendingAudios": ["{audio_instruction}"],
  "bestPostingTimes": "specific best times to post this type of content on {platform}",
  "platformUpdates": "any recent {platform} algorithm changes or feature updates affecting this type of creator",
  "contentTips": ["3 specific actionable tips based on what top creators making similar content are doing right now"],
  "avoidThese": ["2-3 formats or approaches that are declining for this type of content"],
  "audienceInsights": "what audiences are currently responding to most for this specific type of content"
}}
{geo_note}
Be specific with real current information. Reference the actual topic and vibe of the video, not generic niche advice.""",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )

        # response.text can be None when Google Search grounding is used —
        # in that case the text lives in candidates[0].content.parts
        raw = response.text
        if not raw:
            try:
                parts = response.candidates[0].content.parts
                raw = "".join(p.text for p in parts if hasattr(p, "text") and p.text)
            except Exception:
                raw = ""

        if not raw:
            logger.error("[Trends] Gemini returned empty response")
            return {}

        raw = raw.replace("```json", "").replace("```", "").strip()

        # Extract JSON object — Gemini sometimes wraps JSON in prose
        if not raw.startswith("{"):
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start != -1 and end > start:
                raw = raw[start:end]

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            try:
                from json_repair import repair_json
                result = json.loads(repair_json(raw))
            except Exception:
                logger.error(f"[Trends] Gemini JSON parse failed, raw={raw[:200]}")
                return {}

        result["source"] = "gemini_google_search"
        logger.info(
            f"[Trends] Google search: {len(result.get('trendingFormats', []))} formats, "
            f"{len(result.get('trendingTopics', []))} topics, "
            f"vibe={vibe_str if vibe_parts else 'generic'}"
        )
        return result

    except Exception as e:
        logger.error(f"[Trends] Gemini search failed: {e}")
        return {}


# ── Combined: Merge Apify + Gemini data ──────────────────────────────────────

async def get_trend_data(
    niche: str,
    platform: str,
    keywords: Optional[List[str]] = None,
    mood: Optional[str] = None,
    energy: Optional[str] = None,
    scene_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get trend data by combining:
    1. DB lookup — weekly pre-scraped format/audio data if available (instant, free)
    2. Gemini Google Search — real-time trend context focused on video keywords + vibe (free)
    3. Redis cache — avoids repeat Gemini calls within 24h

    keywords:   content-specific keywords from vision (e.g. ['outfit transformation', 'lehenga'])
    mood:       music mood from Gemini audio (e.g. 'romantic', 'energetic', 'melancholic')
    energy:     music energy from Gemini audio (e.g. 'high', 'medium', 'low')
    scene_type: dominant scene type from vision (e.g. 'transformation', 'talking-head')

    keywords/mood/energy/scene_type are NOT included in cache key — Gemini runs fresh
    per-analysis when any vibe signal is present so recommendations match this specific video.
    """
    import asyncio
    from app.core.cache import cache_get, cache_set

    cache_key = f"trends:{niche.lower()}:{platform.lower()}"

    # Only use cache when no vibe signals — vibe signals make each search video-specific
    has_vibe_signals = any([keywords, mood, energy, scene_type])
    if not has_vibe_signals:
        cached = await cache_get(cache_key)
        if cached:
            logger.info(f"[Trends] Redis cache hit for {niche}/{platform}")
            return cached

    logger.info(
        f"[Trends] Fetching fresh data for {niche}/{platform} "
        f"(vibe: mood={mood}, energy={energy}, scene={scene_type}, keywords={keywords})"
    )

    loop = asyncio.get_event_loop()

    # ── DB lookup + Gemini in PARALLEL ────────────────────────────────────────
    # DB is instant (< 5ms). Gemini takes 3-8s. Running together saves time.

    async def _db_lookup():
        try:
            from app.services.trend_scraper_service import get_trending_from_db
            return await get_trending_from_db(niche, platform)
        except Exception as e:
            logger.warning(f"[Trends] DB lookup failed: {e}")
            return {}

    async def _gemini_search():
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(
                    None, search_current_trends,
                    niche, platform, keywords, mood, energy, scene_type,
                ),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            logger.error(f"[Trends] Gemini search timed out after 60s for {niche}/{platform}")
            return {}
        except Exception as e:
            logger.error(f"[Trends] Gemini search failed: {type(e).__name__}: {e}")
            return {}

    db_data, google_data = await asyncio.gather(
        _db_lookup(), _gemini_search(), return_exceptions=True
    )

    if isinstance(db_data, Exception):
        logger.error(f"[Trends] DB lookup failed: {db_data}")
        db_data = {}
    if isinstance(google_data, Exception):
        logger.error(f"[Trends] Gemini search failed: {google_data}")
        google_data = {}

    # DB format patterns (structured) + Gemini format strings
    db_formats = db_data.get("trendingFormats", [])
    gemini_formats = google_data.get("trendingFormats", [])

    # Merge audio lists: DB scraped audios (highest quality) + Gemini real-time audios
    db_audios = db_data.get("trendingAudios", [])
    gemini_audios = google_data.get("trendingAudios", [])
    # DB audios first (verified scraped), then Gemini audios deduplicated
    merged_audios = list(db_audios)
    for a in gemini_audios:
        if a not in merged_audios:
            merged_audios.append(a)

    combined = {
        "niche": niche,
        "platform": platform,
        "trendingHashtags": db_data.get("trendingHashtags", []),
        "trendingAudios": merged_audios,
        "trendingFormats": gemini_formats,
        "trendingFormatsDB": db_formats,
        "trendingTopics": google_data.get("trendingTopics", []),
        "avgLikes": db_data.get("avgLikes", 0),
        "contentTips": google_data.get("contentTips", []),
        "avoidThese": google_data.get("avoidThese", []),
        "bestPostingTimes": google_data.get("bestPostingTimes", ""),
        "topPosts": db_data.get("topPosts", []),
        "dataSources": [],
    }

    if db_data:
        n_posts = db_data.get("postsAnalyzed", 0)
        n_formats = len(db_formats)
        label = f"DB cache ({n_posts} posts"
        if n_formats:
            label += f", {n_formats} format patterns"
        label += ")"
        combined["dataSources"].append(label)
    if google_data:
        vibe_label = ""
        if mood or energy or scene_type:
            vibe_parts = [p for p in [scene_type, mood, energy] if p]
            vibe_label = f" [{', '.join(vibe_parts)}]"
        combined["dataSources"].append(f"Google Search via Gemini (real-time{vibe_label})")

    # Only cache generic (no vibe) results — vibe-specific results are per-video
    if not has_vibe_signals:
        await cache_set(cache_key, combined, ttl=86400)

    logger.info(
        f"[Trends] Combined: {len(combined['trendingFormats'])} gemini formats, "
        f"{len(combined['trendingFormatsDB'])} db formats, "
        f"{len(combined['trendingAudios'])} audios, sources={combined['dataSources']}"
    )
    return combined


def build_trend_context(trend_data: Dict[str, Any], gemini_audio: Optional[Dict[str, Any]] = None) -> str:
    """Convert trend data into a context string for Claude.
    ORDER: formats/techniques/topics first (actionable), audio second, hashtags last (least important).
    Hashtags on Instagram Reels do not significantly extend reach (per Adam Mosseri, 2023).
    The Trend Alignment fix must lead with FORMAT and AUDIO signals — never hashtags."""
    has_instagram = bool(trend_data and (trend_data.get("trendingHashtags") or trend_data.get("trendingFormatsDB")))
    has_google = bool(trend_data and trend_data.get("trendingFormats"))

    if not has_instagram and not has_google:
        return (
            "\n\nTREND DATA: No scraped trend data available for this niche right now. "
            "For Trend Alignment, evaluate the video purely on: (1) whether its FORMAT matches "
            "currently viral formats you know from your training, (2) whether the AUDIO STYLE "
            "fits what is trending for this content type, (3) whether the HOOK STYLE follows "
            "proven high-performing patterns for this niche. Do NOT invent specific hashtag "
            "recommendations — hashtags barely affect reach on Reels. Be honest and specific "
            "about what format/audio/hook changes would actually improve distribution."
        )

    sources = trend_data.get("dataSources", [])
    ctx = "\n\nREAL TREND DATA (sourced from " + " + ".join(sources) + "):"

    # ── 1. DB FORMAT SIGNALS — from top creator profiles (highest quality) ────
    db_formats = trend_data.get("trendingFormatsDB", [])
    if db_formats:
        ctx += "\n- TOP FORMATS used by viral creators in this niche THIS WEEK (scraped from top creator profiles):"
        for fmt in db_formats[:8]:
            label = fmt.get("label", "")
            freq = fmt.get("frequency", 0)
            avg_likes = fmt.get("avg_likes", 0)
            hooks = fmt.get("example_hooks", [])
            line = f"\n  • [{label}] — seen {freq}x, avg {avg_likes:,.0f} likes"
            if hooks:
                line += f' | Hook example: "{hooks[0][:80]}"'
            ctx += line

    # ── 2. GEMINI FORMAT SIGNALS — real-time Google Search context ───────────
    if trend_data.get("trendingFormats"):
        ctx += f"\n- VIRAL FORMATS right now (Google Search): {', '.join(trend_data['trendingFormats'][:5])}"

    if trend_data.get("trendingTopics"):
        ctx += f"\n- TRENDING TOPICS creators are making content about: {', '.join(trend_data['trendingTopics'][:5])}"

    if trend_data.get("contentTips"):
        ctx += f"\n- What top creators are doing RIGHT NOW: {'; '.join(trend_data['contentTips'][:3])}"

    if trend_data.get("avoidThese"):
        ctx += f"\n- Formats/approaches that are DECLINING — avoid: {'; '.join(trend_data['avoidThese'][:3])}"

    if trend_data.get("bestPostingTimes"):
        ctx += f"\n- Best posting times: {trend_data['bestPostingTimes']}"

    # ── 3. AUDIO SIGNALS — second most important ──────────────────────────────
    if trend_data.get("trendingAudios"):
        ctx += f"\n- TRENDING AUDIOS (scraped from viral reels this week): {', '.join(trend_data['trendingAudios'][:8])}"

    if trend_data.get("topPosts"):
        ctx += "\n- Top performing posts this week (real Instagram data):"
        for p in trend_data["topPosts"][:3]:
            ctx += f"\n  • {p.get('likes', 0):,} likes | audio: {p.get('audio', 'unknown')} | \"{p.get('caption_preview', '')[:70]}\""

    if trend_data.get("avgLikes"):
        ctx += f"\n- Engagement benchmark: avg {trend_data['avgLikes']:,} likes per top post in this niche"

    # ── 4. HASHTAGS — least important, listed last ────────────────────────────
    if trend_data.get("trendingHashtags"):
        ctx += f"\n- Hashtags appearing in top posts (context only — NOT the primary fix): {', '.join(trend_data['trendingHashtags'][:10])}"

    # ── Audio profile of THIS video ───────────────────────────────────────────
    audio_profile = ""
    if gemini_audio:
        parts = []
        if gemini_audio.get("hasMusic"):
            parts.append(f"genre: {gemini_audio.get('musicGenre', 'unknown')}")
            parts.append(f"mood: {gemini_audio.get('musicMood', 'unknown')}")
            parts.append(f"energy: {gemini_audio.get('musicEnergy', 'unknown')}")
        if gemini_audio.get("hasSpeech"):
            parts.append(f"has speech ({gemini_audio.get('speechLanguage', 'unknown')})")
        if parts:
            audio_profile = f"\n\nTHIS VIDEO'S AUDIO PROFILE: {', '.join(parts)}"

    ctx += audio_profile
    ctx += """

TREND ALIGNMENT SCORING RULES — READ CAREFULLY:
1. HASHTAGS DO NOT DRIVE REACH ON REELS. Instagram's own algorithm team confirmed this. NEVER make hashtag recommendations the primary fix for Trend Alignment. Hashtags belong only in suggestedHashtags, not in the Trend Alignment problem/fix.
2. The Trend Alignment fix MUST lead with one of: (a) a specific FORMAT the creator should use, (b) an AUDIO the creator should switch to or keep, (c) a HOOK STYLE that's currently performing, or (d) a CONTENT ANGLE that's trending right now.
3. If viral formats are listed above, compare the creator's video format DIRECTLY against them. Name the gap specifically: "Your video uses [X format] but the top-performing content in this niche right now uses [Y format] because [reason]."
4. If trending audios are listed above, assess whether the creator's current audio matches. If they're already using a trending audio style, say so as a strength. If not, suggest a specific switch with a reason tied to the video's mood/energy.
5. If trending topics are listed above, assess whether the creator's content angle is timely. If the topic is trending, that's a major score boost. If it's not in the current conversation, suggest the angle adjustment.
6. Be honest when the video is already trend-aligned. A high score with a specific compliment ("Your POV format and the audio choice are both well-aligned with what's performing this week") is better than inventing a problem."""

    if trend_data.get("trendingAudios") and audio_profile:
        ctx += """

AUDIO RULES:
- If the video uses dialogue/speech as its core content, do NOT suggest replacing the audio with music. The speech IS the content.
- Only suggest a trending audio switch if it matches the video's mood and energy. Never suggest a hype audio for a calm video.
- If none of the trending audios fit, say so: "Your current audio suits this content better than the trending options."
- Never recommend audio just because it's trending. Fit > trendiness."""
    elif not trend_data.get("trendingAudios"):
        ctx += "\n- No scraped audio data available for this niche. For the trendingAudioSuggestion field, use your own knowledge of what songs are currently trending on Instagram Reels for this niche and video mood — name a specific real song and artist. Do not tell the user to go search themselves."

    ctx += "\n- The goal is: if they follow your suggestions using THIS real data, their next video has a measurably higher chance of going viral."

    return ctx
