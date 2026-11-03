"""
app/services/content_strategist_service.py
AI Content Strategist – Feature #6

Generates personalised viral video ideas based on:
  • creator niche (from CreatorProfile)
  • past performance data
  • trending topics (from viral pattern DB + AI knowledge)

Each idea includes: hook, outline (3 key points), caption, hashtags,
recommended platform, estimated virality range.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import content_ideas_generated
from app.services.ai_service import _chat, _parse_json
from app.services.memory_service import get_creator_context

logger = get_logger("content_strategist")


# ── Main entry point ──────────────────────────────────────────────────────────

async def generate_content_ideas(
    user_id: str,
    db: AsyncSession,
    count: int = 10,
    theme: Optional[str] = None,
    platform_focus: Optional[str] = None,    # instagram | tiktok | youtube | all
    trend_inject: Optional[List[str]] = None, # manually inject trending topics
) -> List[Dict[str, Any]]:
    """
    Generate `count` detailed video ideas personalised to the creator.
    Returns structured list, each with hook, outline, caption, hashtags.
    """
    creator_ctx = await get_creator_context(user_id, db)

    system = _build_strategist_system(creator_ctx)
    user_msg = _build_strategist_prompt(
        creator_ctx, count, theme, platform_focus, trend_inject
    )

    raw = await _chat(system, user_msg, max_tokens=3500)
    ideas = _parse_ideas(raw)

    content_ideas_generated.inc(len(ideas))
    logger.info("Generated %d content ideas for user %s", len(ideas), user_id)
    return ideas


async def generate_script(
    user_id: str,
    db: AsyncSession,
    idea: Dict[str, Any],
    duration_target_sec: int = 45,
    style: str = "conversational",    # conversational | educational | storytelling
) -> Dict[str, Any]:
    """
    Expand a single idea into a full word-for-word script with timing cues.
    """
    creator_ctx = await get_creator_context(user_id, db)
    niche = creator_ctx.get("primary_niche", "general")
    hook_score_avg = creator_ctx.get("avg_hook_score", 6.0)

    system = (
        f"You are a viral short-form video scriptwriter specialising in the {niche} niche. "
        f"Write scripts in a {style} style. This creator averages a {hook_score_avg}/10 hook score "
        "— push them to do better. Include timing markers. Respond ONLY with valid JSON."
    )
    user_msg = f"""
Video idea:
{json.dumps(idea, indent=2)}

Target duration: {duration_target_sec} seconds
Style: {style}
Creator niche: {niche}

Write a complete, word-for-word script with timing cues.

Return JSON:
{{
  "title": "<video title>",
  "total_duration_estimate_sec": <int>,
  "hook": {{
    "text": "<exact opening line>",
    "duration_sec": <int>,
    "delivery_note": "<how to say it>"
  }},
  "sections": [
    {{
      "name": "<section name>",
      "start_sec": <int>,
      "end_sec": <int>,
      "script": "<word-for-word text>",
      "visual_note": "<what to show on screen>",
      "energy_level": "<calm|medium|high>"
    }}
  ],
  "cta": "<exact call-to-action line>",
  "b_roll_ideas": [<str>, ...],
  "music_vibe": "<description>"
}}
"""
    raw = await _chat(system, user_msg, max_tokens=2500)
    return _parse_json(raw)


async def get_trend_report(
    user_id: str,
    db: AsyncSession,
    niche: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate a real-time trend report for the creator's niche.
    Uses Claude's knowledge + creator context.
    """
    creator_ctx = await get_creator_context(user_id, db)
    effective_niche = niche or creator_ctx.get("primary_niche", "general content")
    top_topics = creator_ctx.get("top_performing_topics", [])[:5]

    system = (
        "You are a social media trend analyst with deep knowledge of current viral content. "
        "Respond ONLY with valid JSON."
    )
    user_msg = f"""
Creator niche: {effective_niche}
Creator's best topics so far: {json.dumps(top_topics)}

Analyse the current trend landscape for this niche.

Return JSON:
{{
  "niche": "{effective_niche}",
  "hot_trends": [
    {{
      "topic": "<trend name>",
      "momentum": "<rising|peak|declining>",
      "content_angle": "<how to use this trend>",
      "urgency": "<days_to_act: int>"
    }}
  ],
  "evergreen_topics": [<str>, ...],
  "untapped_opportunities": [<str>, ...],
  "formats_to_try": [<str>, ...],
  "competitor_themes": [<str>, ...],
  "weekly_content_calendar": [
    {{
      "day": "<Mon|Tue|...|Sun>",
      "topic": "<topic>",
      "format": "<format>",
      "best_time": "<HH:MM>"
    }}
  ]
}}
"""
    raw = await _chat(system, user_msg, max_tokens=2000)
    return _parse_json(raw)


# ── Prompt builders ───────────────────────────────────────────────────────────

def _build_strategist_system(creator_ctx: Dict[str, Any]) -> str:
    niche = creator_ctx.get("primary_niche", "general")
    style = creator_ctx.get("content_style_summary", "")
    total = creator_ctx.get("total_videos_analyzed", 0)
    avg_virality = creator_ctx.get("avg_virality_score")
    best_topics = creator_ctx.get("top_performing_topics", [])[:3]

    profile_note = ""
    if total > 0:
        profile_note = (
            f"\n\nCreator context:\n"
            f"- Niche: {niche}\n"
            f"- Style: {style}\n"
            f"- Avg virality: {avg_virality}/100\n"
            f"- Best topics: {json.dumps(best_topics)}\n"
            f"- Videos analysed: {total}"
        )

    return (
        "You are a world-class viral content strategist with a proven track record of "
        "creating 10M+ view videos. You understand hook psychology, platform algorithms, "
        "and audience behaviour at a deep level. "
        "Generate content ideas that are specific, actionable, and tailored to this creator."
        + profile_note
        + "\nRespond ONLY with valid JSON."
    )


def _build_strategist_prompt(
    creator_ctx: Dict[str, Any],
    count: int,
    theme: Optional[str],
    platform_focus: Optional[str],
    trend_inject: Optional[List[str]],
) -> str:
    niche = creator_ctx.get("primary_niche", "general")
    weak_hooks = creator_ctx.get("recommendations", {})

    trend_note = ""
    if trend_inject:
        trend_note = f"\nIncorporate these trending topics where relevant: {trend_inject}"

    theme_note = f"\nTheme/angle focus: {theme}" if theme else ""
    platform_note = f"\nPrimary platform: {platform_focus}" if platform_focus else ""

    return f"""
Generate exactly {count} viral short-form video ideas for a creator in the {niche} niche.
{theme_note}{platform_note}{trend_note}

For each idea, be extremely specific. No generic ideas.

Return a JSON array of exactly {count} ideas:
[
  {{
    "id": <int 1-{count}>,
    "title": "<compelling video title>",
    "hook": "<exact opening line — the first words spoken>",
    "hook_type": "<question|bold_claim|story|contrast|how_to|list|shock>",
    "outline": [
      "<key point 1>",
      "<key point 2>",
      "<key point 3>"
    ],
    "caption": "<platform-optimised caption with emojis>",
    "hashtags": [<str>, ...],
    "recommended_platform": "<instagram|tiktok|youtube|all>",
    "format": "<talking head|voiceover|tutorial|storytelling|list|pov>",
    "estimated_virality_range": "<40-60|60-75|75-90|90+>",
    "why_itll_work": "<2 sentences explaining the psychology>",
    "b_roll_needed": [<str>, ...],
    "urgency": "<evergreen|trending_now|seasonal>"
  }}
]
"""


# ── Parser ────────────────────────────────────────────────────────────────────

def _parse_ideas(raw: str) -> List[Dict[str, Any]]:
    data = _parse_json(raw)
    if isinstance(data, list):
        return data
    # Try to find array in dict
    for val in data.values():
        if isinstance(val, list):
            return val
    return [{"error": "Could not parse ideas", "raw": str(raw)[:300]}]
