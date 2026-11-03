"""
app/services/chatbot_service.py
ARIA – Creator AI's conversational assistant.
Uses Claude with creator memory context for personalised advice.
"""

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

import anthropic

from app.core.config import settings
from app.services.memory_service import get_creator_context
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_client: Optional[anthropic.AsyncAnthropic] = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


ARIA_BASE_SYSTEM = """You are Prysm AI — the AI creative strategist inside Prysm, an AI-powered video analysis platform built exclusively for Instagram Reels creators.

What Prysm does:
- Analyses Instagram Reels across 8 weighted parameters: Hook Strength (25%), Pacing & Cuts (15%), Audio Quality & Choice (15%), Visual Quality (10%), Captions & Text Overlay (10%), Call To Action (10%), Trend Alignment (10%), and Thumbnail Frame (5%).
- Prysm ONLY supports Instagram Reels. It does NOT support TikTok, YouTube Shorts, or any other platform.

Your capabilities:
- Analyse a creator's past video performance from their profile data
- Suggest new Instagram Reels ideas tailored to their niche and style
- Answer questions about content strategy, hooks, virality, and Instagram trends
- Generate reel scripts, captions, and hook lines on demand
- Identify patterns in what works for this specific creator on Instagram

Always be direct, specific, and actionable. Use data from the creator's profile when available to personalise your advice. Speak like a knowledgeable Instagram content strategist, not a generic chatbot. NEVER mention TikTok or YouTube Shorts — Prysm is Instagram-only.

{creator_context_section}
"""


def _build_system_prompt(creator_context: Optional[Dict[str, Any]] = None) -> str:
    if not creator_context or not creator_context.get("total_videos_analyzed"):
        context_section = "No creator history available yet – give general expert advice."
    else:
        ctx = creator_context
        context_section = f"""
## Creator Profile Summary
- Videos Analyzed: {ctx.get("total_videos_analyzed", 0)}
- Avg Virality Score: {ctx.get("avg_virality_score", "N/A")} / 100
- Avg Hook Score: {ctx.get("avg_hook_score", "N/A")} / 10
- Primary Niche: {ctx.get("primary_niche", "Unknown")}
- Top Performing Topics: {json.dumps(ctx.get("top_performing_topics", [])[:5])}
- Recent Virality Trend: {json.dumps(ctx.get("virality_trend", [])[-5:])}
- Style Summary: {ctx.get("content_style_summary", "Not yet generated")}

Use this data to personalise every recommendation.
"""

    return ARIA_BASE_SYSTEM.format(creator_context_section=context_section)


async def chat(
    user_id: str,
    messages: List[Dict[str, str]],
    db: AsyncSession,
    analysis_context: Optional[Dict[str, Any]] = None,
    stream: bool = False,
) -> Dict[str, Any]:
    """
    Send a chat turn to ARIA and return the assistant response.

    Args:
        user_id: the authenticated user
        messages: full conversation history [{"role": "user|assistant", "content": "..."}]
        db: database session for loading creator context
        analysis_context: optionally inject a specific video analysis
        stream: if True, returns a streaming response (caller handles SSE)

    Returns:
        {"content": str, "tokens_used": int}
    """
    creator_ctx = await get_creator_context(user_id, db)
    system = _build_system_prompt(creator_ctx)

    # Inject analysis context as a system-level preamble if provided
    if analysis_context:
        analysis_snippet = json.dumps(analysis_context, indent=2)[:2000]
        system += f"\n\n## Current Video Analysis Under Discussion\n```json\n{analysis_snippet}\n```"

    client = _get_client()

    if stream:
        return await _stream_response(client, system, messages)

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=messages,
    )
    content = response.content[0].text
    tokens  = response.usage.input_tokens + response.usage.output_tokens

    return {"content": content, "tokens_used": tokens}


async def _stream_response(
    client: anthropic.AsyncAnthropic,
    system: str,
    messages: List[Dict[str, str]],
) -> AsyncIterator[str]:
    """Returns an async generator of text chunks for SSE streaming."""
    async with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text


async def generate_video_ideas(
    user_id: str,
    db: AsyncSession,
    count: int = 5,
    theme: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Generate personalised video ideas using creator memory.
    """
    creator_ctx = await get_creator_context(user_id, db)
    niche = creator_ctx.get("primary_niche", "general")
    top_topics = creator_ctx.get("top_performing_topics", [])

    system = _build_system_prompt(creator_ctx)
    user_msg = (
        f"Generate {count} specific, actionable short-form video ideas "
        f"for a creator in the '{niche}' niche"
        + (f", focusing on the theme: {theme}" if theme else "")
        + ". For each idea, include: title, hook, key points (3), and recommended platform. "
        "Respond ONLY with a valid JSON array."
    )

    from app.services.ai_service import _chat
    raw = await _chat(system, user_msg, max_tokens=2048)

    import re, json
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
    text = fenced.group(1) if fenced else raw
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return [{"error": "Could not parse ideas", "raw": raw[:500]}]
