"""
app/services/competitor_service.py
Competitor Analysis – Feature #7

Allows a creator to analyse a competitor's account by username.
Scrapes their videos, detects viral patterns, extracts strategies,
and identifies gap opportunities for the requesting creator.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.metrics import competitor_analyses_total
from app.services.ai_service import _chat, _parse_json
from app.services.creator_scraper_service import scrape_creator, compute_engagement_patterns
from app.services.memory_service import get_creator_context

logger = get_logger("competitor_service")


# ── Main entry point ──────────────────────────────────────────────────────────

async def analyze_competitor(
    competitor_handle: str,
    platform: str,
    requesting_user_id: str,
    db: AsyncSession,
    max_posts: int = 50,
) -> Dict[str, Any]:
    """
    Full competitor intelligence pipeline:
      1. Scrape competitor posts
      2. Compute engagement patterns
      3. AI-powered strategy extraction
      4. Gap analysis vs requesting creator
    """
    logger.info("Starting competitor analysis: @%s on %s", competitor_handle, platform)

    # Step 1: Scrape competitor
    scrape_result = await scrape_creator(competitor_handle, platform, max_posts)
    posts = scrape_result.get("posts", [])

    if not posts:
        raise ValueError(f"No posts found for @{competitor_handle} on {platform}.")

    # Step 2: Compute statistical patterns
    engagement_data = compute_engagement_patterns(posts)

    # Step 3: Load requesting creator's profile for gap analysis
    creator_ctx = await get_creator_context(requesting_user_id, db)

    # Step 4: AI strategy extraction
    strategy_data = await _extract_competitor_strategy(
        competitor_handle, platform, posts, engagement_data
    )

    # Step 5: Gap analysis
    gap_data = await _analyze_gaps(
        competitor_handle, strategy_data, creator_ctx
    )

    competitor_analyses_total.inc()

    return {
        "competitor_handle":      competitor_handle,
        "platform":               platform,
        "posts_analysed":         len(posts),
        "top_performing_content": _extract_top_posts(posts, limit=10),
        "viral_patterns":         strategy_data.get("viral_patterns", {}),
        "engagement_strategies":  strategy_data.get("engagement_strategies", []),
        "posting_schedule":       {
            "best_hours": engagement_data.get("best_hours", []),
            "best_days":  engagement_data.get("best_days", []),
        },
        "content_pillars":        strategy_data.get("content_pillars", []),
        "gap_opportunities":      gap_data.get("gap_opportunities", []),
        "threat_level":           gap_data.get("threat_level", "medium"),
        "strategic_recommendations": gap_data.get("recommendations", []),
        "competitor_strengths":   strategy_data.get("strengths", []),
        "competitor_weaknesses":  strategy_data.get("weaknesses", []),
    }


async def _extract_competitor_strategy(
    handle: str,
    platform: str,
    posts: List[Dict],
    engagement_data: Dict,
) -> Dict[str, Any]:
    """Use Claude to extract the competitor's content strategy from post data."""
    top_posts = _extract_top_posts(posts, limit=15)
    posts_text = "\n".join(
        f"- \"{p.get('title', '')[:100]}\" | "
        f"Views: {p.get('view_count','?')} | "
        f"Likes: {p.get('like_count','?')} | "
        f"Duration: {p.get('duration_sec','?')}s"
        for p in top_posts
    )

    system = (
        "You are a competitive intelligence analyst specialising in social media content. "
        "Extract the exact content playbook this creator uses. Respond ONLY with valid JSON."
    )
    user = f"""
Competitor: @{handle} on {platform}
Total posts analysed: {len(posts)}
Best performing content:
{posts_text}

Engagement patterns:
- Best hours: {engagement_data.get('best_hours', [])}
- Best days: {engagement_data.get('best_days', [])}
- Best length bucket: {engagement_data.get('best_length_bucket')}

Reverse-engineer their strategy.

Return JSON:
{{
  "viral_patterns": {{
    "dominant_hook_types":   [<str>, ...],
    "avg_duration_sec":      <float>,
    "posting_frequency":     "<description>",
    "emotional_tone":        "<dominant tone>",
    "topic_distribution":    [<str>, ...]
  }},
  "engagement_strategies": [
    {{
      "strategy":      "<strategy name>",
      "description":   "<how they use it>",
      "frequency":     "<always|often|sometimes>",
      "effectiveness": "<high|medium|low>"
    }}
  ],
  "content_pillars": [<str>, ...],
  "hook_formulas":   [<str>, ...],
  "strengths":       [<str>, ...],
  "weaknesses":      [<str>, ...]
}}
"""
    raw = await _chat(system, user, max_tokens=2000)
    return _parse_json(raw)


async def _analyze_gaps(
    competitor_handle: str,
    competitor_strategy: Dict,
    creator_ctx: Dict,
) -> Dict[str, Any]:
    """Compare creator vs competitor and identify strategic opportunities."""
    system = (
        "You are a competitive strategy consultant. Identify specific opportunities "
        "where the requesting creator can outperform this competitor. "
        "Respond ONLY with valid JSON."
    )
    user = f"""
Requesting creator profile:
- Niche: {creator_ctx.get('primary_niche', 'unknown')}
- Avg virality: {creator_ctx.get('avg_virality_score', 'N/A')}
- Top topics: {json.dumps(creator_ctx.get('top_performing_topics', [])[:5])}
- Total videos: {creator_ctx.get('total_videos_analyzed', 0)}

Competitor (@{competitor_handle}) strategy summary:
- Content pillars: {competitor_strategy.get('content_pillars', [])}
- Viral patterns: {json.dumps(competitor_strategy.get('viral_patterns', {}))}
- Strengths: {competitor_strategy.get('strengths', [])}
- Weaknesses: {competitor_strategy.get('weaknesses', [])}

Identify strategic gaps and opportunities.

Return JSON:
{{
  "gap_opportunities": [
    {{
      "topic":       "<untapped topic>",
      "reason":      "<why competitor misses it>",
      "potential":   "<high|medium|low>",
      "angle":       "<how to approach it>"
    }}
  ],
  "threat_level": "<low|medium|high>",
  "competitive_advantages": [<str>, ...],
  "recommendations": [<str>, ...],
  "differentiation_strategy": "<2-sentence summary>"
}}
"""
    raw = await _chat(system, user, max_tokens=1500)
    return _parse_json(raw)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_top_posts(posts: List[Dict], limit: int = 10) -> List[Dict]:
    """Sort posts by view count descending and return top N."""
    scored = []
    for p in posts:
        views = p.get("view_count") or 0
        likes = p.get("like_count") or 0
        score = views + (likes * 10)   # weight likes more
        scored.append({**p, "_score": score})
    scored.sort(key=lambda x: x["_score"], reverse=True)
    return [
        {k: v for k, v in p.items() if k != "_score"}
        for p in scored[:limit]
    ]
