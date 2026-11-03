"""
app/services/viral_pattern_service.py
Viral Pattern Engine – Feature #3

Maintains a database of viral video characteristics.
Tracks: hook_type, duration, emotional_tone, topic_category,
        engagement_ratios, completion_rate.

Uses this dataset to:
  1. Store new patterns after every completed analysis
  2. Query similar patterns for improved virality prediction
  3. Generate an explainable virality breakdown (Feature #9)
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import viral_patterns_stored
from app.models.intelligence import ViralPattern, HookType, EmotionalTone
from app.models.analysis import Analysis
from app.services.ai_service import _chat, _parse_json

logger = get_logger("viral_pattern_service")


# ── Pattern storage ───────────────────────────────────────────────────────────

async def store_pattern_from_analysis(
    analysis: Analysis,
    db: AsyncSession,
    engagement_ratio: Optional[float] = None,
    has_face: Optional[bool] = None,
    has_music: Optional[bool] = None,
    platform: str = "unknown",
) -> Optional[ViralPattern]:
    """
    Persist a ViralPattern record if the analysis meets the minimum virality threshold.
    Called automatically at the end of the analysis pipeline.
    """
    score = analysis.virality_score or 0
    if score < settings.VIRAL_PATTERN_MIN_SCORE:
        return None  # only learn from high-performing content

    # Map analysis fields to pattern
    hook_type = _classify_hook_type(analysis.hook_suggestions or [], analysis.hook_transcript or "")
    emotional_tone = _classify_emotional_tone(analysis.emotional_hooks or [])
    scene_rate = _compute_scene_rate(analysis.scenes or [], analysis.media)
    wpm = _compute_wpm(analysis.media)

    pattern = ViralPattern(
        analysis_id         = analysis.id,
        hook_type           = hook_type,
        hook_duration_sec   = 3.0,
        video_duration_sec  = getattr(analysis.media, "duration_sec", None),
        emotional_tone      = emotional_tone,
        topic_category      = analysis.niche_alignment or "general",
        scene_change_rate   = scene_rate,
        speech_pace_wpm     = wpm,
        has_captions        = True,
        has_music           = has_music,
        has_face            = has_face,
        platform            = platform,
        virality_score      = analysis.virality_score,
        engagement_ratio    = engagement_ratio,
        feature_vector      = _build_feature_vector(analysis),
        source              = "user_upload",
    )

    db.add(pattern)
    await db.flush()
    viral_patterns_stored.inc()
    logger.info("Stored viral pattern %s (score=%d)", pattern.id, score)
    return pattern


# ── Virality prediction enhancement ──────────────────────────────────────────

async def get_similar_patterns(
    virality_score: int,
    topic_category: Optional[str],
    hook_type: Optional[str],
    db: AsyncSession,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """
    Retrieve similar high-performing patterns to use as comparison context
    for improved virality predictions.
    """
    conditions = [ViralPattern.virality_score >= (virality_score - 15)]
    if topic_category:
        conditions.append(ViralPattern.topic_category == topic_category)

    result = await db.execute(
        select(ViralPattern)
        .where(and_(*conditions))
        .order_by(desc(ViralPattern.virality_score))
        .limit(limit)
    )
    patterns = result.scalars().all()
    return [_pattern_to_dict(p) for p in patterns]


# ── Viral score explainer ─────────────────────────────────────────────────────

async def explain_virality_score(
    analysis: Analysis,
    similar_patterns: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    Feature #9 – Produce a detailed, factor-by-factor breakdown of the virality score.
    Returns a structured explanation with positive and negative contributors.

    Example output:
    {
      "virality_score": 78,
      "grade": "B+",
      "summary": "Strong hook and emotional resonance, hindered by slow mid-section.",
      "factors": [
        {"factor": "Hook strength",    "impact": +20, "evidence": "..."},
        {"factor": "Emotional trigger", "impact": +15, "evidence": "..."},
        {"factor": "Trend alignment",   "impact": +12, "evidence": "..."},
        {"factor": "Pacing",            "impact": -9,  "evidence": "..."},
      ],
      "ceiling_blockers": ["..."],
      "quick_wins": ["..."]
    }
    """
    system = (
        "You are an expert viral content analyst. Your job is to explain WHY a video "
        "got a specific virality score using concrete evidence from the transcript and "
        "analysis data. Be specific, direct, and actionable. Respond ONLY with valid JSON."
    )

    similar_text = ""
    if similar_patterns:
        similar_text = f"\nSimilar high-performing patterns in database: {similar_patterns[:3]}"

    user = f"""
Virality Score: {analysis.virality_score}/100

Analysis data:
- Hook score: {analysis.hook_score}/10
- Hook text: "{analysis.hook_transcript or 'N/A'}"
- Pacing score: {analysis.pacing_score}/10
- Value density: {analysis.value_density}/10
- Emotional hooks detected: {analysis.emotional_hooks or []}
- Trend alignment: {analysis.trend_alignment}
- Story structure: {analysis.story_structure}
- Call to action: {analysis.call_to_action or 'None detected'}
- Scene change rate: {len(analysis.scenes or [])} scenes
- Virality reasons from AI: {analysis.virality_reasons or []}
{similar_text}

Break down the virality score into contributing factors with numeric impact (+/-).
Total impacts should roughly sum to the virality score.

Return JSON:
{{
  "virality_score": {analysis.virality_score},
  "grade": "<A+|A|B+|B|C+|C|D|F>",
  "summary": "<2-sentence plain English explanation>",
  "factors": [
    {{
      "factor": "<factor name>",
      "impact": <int, positive or negative>,
      "evidence": "<specific evidence from the video>",
      "category": "<hook|emotion|pacing|trend|cta|structure|visual>"
    }}
  ],
  "ceiling_blockers": [<str>, ...],
  "quick_wins": [<str>, ...],
  "potential_score_if_fixed": <int 0-100>
}}
"""
    raw = await _chat(system, user, max_tokens=1500)
    return _parse_json(raw)


# ── Database statistics ───────────────────────────────────────────────────────

async def get_pattern_stats(db: AsyncSession) -> Dict[str, Any]:
    """Aggregate statistics over the viral pattern database."""
    total = await db.scalar(select(func.count(ViralPattern.id)))
    avg_score = await db.scalar(select(func.avg(ViralPattern.virality_score)))
    top_hook_types = await db.execute(
        select(ViralPattern.hook_type, func.count().label("count"))
        .group_by(ViralPattern.hook_type)
        .order_by(desc("count"))
        .limit(5)
    )
    top_tones = await db.execute(
        select(ViralPattern.emotional_tone, func.avg(ViralPattern.virality_score).label("avg_score"))
        .group_by(ViralPattern.emotional_tone)
        .order_by(desc("avg_score"))
        .limit(5)
    )
    return {
        "total_patterns":     total or 0,
        "avg_virality_score": round(float(avg_score or 0), 1),
        "top_hook_types":     [{"type": r[0], "count": r[1]} for r in top_hook_types],
        "top_emotional_tones": [{"tone": r[0], "avg_score": round(float(r[1]), 1)} for r in top_tones],
    }


# ── Private helpers ───────────────────────────────────────────────────────────

def _classify_hook_type(suggestions: List[str], hook_text: str) -> Optional[HookType]:
    text = (hook_text + " ".join(suggestions)).lower()
    if "?" in hook_text:
        return HookType.QUESTION
    if any(w in text for w in ["secret", "truth", "never told", "nobody"]):
        return HookType.BOLD_CLAIM
    if any(w in text for w in ["how to", "step", "way to"]):
        return HookType.HOW_TO
    if any(w in text for w in ["story", "once", "this is what"]):
        return HookType.STORY
    if any(w in text for w in ["vs", "versus", "difference", "compare"]):
        return HookType.CONTRAST
    return HookType.BOLD_CLAIM


def _classify_emotional_tone(emotional_hooks: List[str]) -> Optional[EmotionalTone]:
    joined = " ".join(emotional_hooks).lower()
    if any(w in joined for w in ["funny", "laugh", "humor", "comedy"]):
        return EmotionalTone.FUNNY
    if any(w in joined for w in ["inspir", "motivat", "achieve"]):
        return EmotionalTone.INSPIRING
    if any(w in joined for w in ["shock", "surprise", "unbeliev"]):
        return EmotionalTone.SHOCKING
    if any(w in joined for w in ["learn", "tip", "how", "lesson"]):
        return EmotionalTone.EDUCATIONAL
    if any(w in joined for w in ["relat", "same", "me too", "real life"]):
        return EmotionalTone.RELATABLE
    return EmotionalTone.EDUCATIONAL


def _compute_scene_rate(scenes: List[dict], media) -> Optional[float]:
    dur = getattr(media, "duration_sec", None) if media else None
    if scenes and dur and dur > 0:
        return round(len(scenes) / dur, 3)
    return None


def _compute_wpm(media) -> Optional[int]:
    transcript = getattr(media, "transcript", None) if media else None
    dur = getattr(media, "duration_sec", None) if media else None
    if transcript and dur and dur > 0:
        words = len(transcript.split())
        return int((words / dur) * 60)
    return None


def _build_feature_vector(analysis: Analysis) -> List[float]:
    """Simple numeric feature vector for future ML training."""
    return [
        float(analysis.hook_score or 0) / 10,
        float(analysis.virality_score or 0) / 100,
        float(analysis.value_density or 0) / 10,
        float(analysis.pacing_score or 0) / 10,
        1.0 if analysis.call_to_action else 0.0,
        1.0 if analysis.emotional_hooks else 0.0,
        {"low": 0.0, "medium": 0.5, "high": 1.0}.get(analysis.trend_alignment or "", 0.5),
    ]


def _pattern_to_dict(p: ViralPattern) -> Dict[str, Any]:
    return {
        "hook_type":         p.hook_type,
        "video_duration_sec": p.video_duration_sec,
        "emotional_tone":    p.emotional_tone,
        "topic_category":    p.topic_category,
        "virality_score":    p.virality_score,
        "scene_change_rate": p.scene_change_rate,
        "speech_pace_wpm":   p.speech_pace_wpm,
    }
