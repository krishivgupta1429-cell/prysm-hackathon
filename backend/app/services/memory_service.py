"""
app/services/memory_service.py
Manages the Creator Intelligence layer:
  - builds/updates CreatorProfile after each analysis
  - stores content embeddings in pgvector / Pinecone
  - surfaces personalised context for ARIA
"""

import logging
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.chat import CreatorProfile
from app.models.analysis import Analysis, AnalysisStatus
from app.core.config import settings
from app.core.cache import cache_set, cache_get, CacheKeys

logger = logging.getLogger(__name__)


# ── Profile CRUD ──────────────────────────────────────────────────────────────

async def get_or_create_profile(user_id: str, db: AsyncSession) -> CreatorProfile:
    result = await db.execute(
        select(CreatorProfile).where(CreatorProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        profile = CreatorProfile(user_id=user_id)
        db.add(profile)
        await db.flush()
    return profile


async def update_profile_from_analysis(
    user_id: str,
    analysis: Analysis,
    db: AsyncSession,
) -> CreatorProfile:
    """
    Incrementally update the creator profile after a completed analysis.
    Uses exponential moving average for rolling metrics.
    """
    profile = await get_or_create_profile(user_id, db)
    n = profile.total_videos_analyzed or 0

    def _ema(prev: Optional[float], new: Optional[float], alpha: float = 0.3) -> Optional[float]:
        if new is None:
            return prev
        if prev is None:
            return new
        return round(alpha * new + (1 - alpha) * prev, 2)

    profile.total_videos_analyzed = n + 1
    profile.avg_virality_score    = _ema(profile.avg_virality_score, analysis.virality_score)
    profile.avg_hook_score        = _ema(profile.avg_hook_score, analysis.hook_score)
    profile.avg_value_density     = _ema(profile.avg_value_density, analysis.value_density)
    profile.avg_pacing_score      = _ema(profile.avg_pacing_score, analysis.pacing_score)

    # Update trends
    virality_trend = profile.virality_trend or []
    hook_trend     = profile.hook_score_trend or []
    virality_trend.append({"date": analysis.created_at.isoformat(), "score": analysis.virality_score})
    hook_trend.append({"date": analysis.created_at.isoformat(), "score": analysis.hook_score})
    profile.virality_trend    = virality_trend[-50:]   # keep last 50
    profile.hook_score_trend  = hook_trend[-50:]

    # Niche
    if analysis.niche_alignment and not profile.primary_niche:
        profile.primary_niche = analysis.niche_alignment

    # Top topics (simple frequency map)
    topics = profile.top_performing_topics or []
    if analysis.relevant_trends:
        for trend in analysis.relevant_trends[:3]:
            existing = next((t for t in topics if t["topic"] == trend), None)
            if existing:
                existing["count"] = existing.get("count", 1) + 1
                existing["avg_virality"] = _ema(existing.get("avg_virality"), analysis.virality_score)
            else:
                topics.append({"topic": trend, "count": 1, "avg_virality": analysis.virality_score})
    topics.sort(key=lambda x: x.get("avg_virality", 0), reverse=True)
    profile.top_performing_topics = topics[:20]

    await db.flush()

    # Invalidate cache
    await cache_set(
        CacheKeys.CREATOR_MEMORY.format(user_id=str(user_id)),
        _profile_to_dict(profile),
        ttl=3600,
    )

    logger.info("Updated creator profile for user %s (n=%d)", user_id, profile.total_videos_analyzed)
    return profile


# ── Context builder for ARIA ──────────────────────────────────────────────────

async def get_creator_context(
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Returns a compact representation of the creator's history
    suitable for injecting into ARIA's system prompt.
    """
    cached = await cache_get(CacheKeys.CREATOR_MEMORY.format(user_id=str(user_id)))
    if cached:
        return cached

    profile = await get_or_create_profile(user_id, db)
    ctx = _profile_to_dict(profile)
    await cache_set(CacheKeys.CREATOR_MEMORY.format(user_id=str(user_id)), ctx, ttl=3600)
    return ctx


# ── Vector operations ─────────────────────────────────────────────────────────

async def embed_and_store(
    text: str,
    metadata: Dict[str, Any],
    namespace: str = "analyses",
) -> Optional[str]:
    """
    Generate an embedding and store it in the configured vector backend.
    Returns the vector record ID.
    Currently uses pgvector (handled at DB level).
    """
    # pgvector path – handled at DB level (future implementation)
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _profile_to_dict(p: CreatorProfile) -> Dict[str, Any]:
    return {
        "total_videos_analyzed":  p.total_videos_analyzed,
        "avg_virality_score":     p.avg_virality_score,
        "avg_hook_score":         p.avg_hook_score,
        "avg_value_density":      p.avg_value_density,
        "avg_pacing_score":       p.avg_pacing_score,
        "primary_niche":          p.primary_niche,
        "secondary_niches":       p.secondary_niches,
        "top_performing_topics":  p.top_performing_topics,
        "best_performing_hooks":  p.best_performing_hooks,
        "virality_trend":         (p.virality_trend or [])[-10:],
        "hook_score_trend":       (p.hook_score_trend or [])[-10:],
        "content_style_summary":  p.content_style_summary,
        "recommendations":        p.recommendations,
    }
