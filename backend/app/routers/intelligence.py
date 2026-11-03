"""
app/routers/intelligence.py
v2 Intelligence endpoints:

  Creator Profile Intelligence
  POST /intelligence/scrape          – scrape a creator handle
  GET  /intelligence/scrape/{id}     – get scrape result

  Viral Pattern Engine
  GET  /intelligence/patterns        – viral pattern database stats
  GET  /intelligence/patterns/similar – similar patterns for a video

  Virality Explanation
  GET  /intelligence/explain/{video_id} – detailed score breakdown

  Timeline Feedback
  GET  /intelligence/timeline/{video_id} – timestamp-level feedback

  Content Strategist
  POST /intelligence/ideas           – generate content ideas
  POST /intelligence/script          – expand idea to full script
  GET  /intelligence/trends          – trend report

  Competitor Analysis
  POST /intelligence/competitor      – start competitor analysis
  GET  /intelligence/competitor/{id} – get competitor analysis result
"""

import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.core.database import get_db
from app.core.security import get_current_user, require_credits
from app.core.cache import cache_get, cache_set, CacheKeys, get_job_status
from app.core.config import settings
from app.models.user import User
from app.models.analysis import Analysis, AnalysisStatus
from app.models.intelligence import (
    CreatorIntelligence, ScrapingStatus,
    CompetitorAnalysis, TimelineFeedback,
)
from app.services import viral_pattern_service, timeline_service, content_strategist_service
from app.services.memory_service import get_creator_context

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class ScrapeRequest(BaseModel):
    handle: str
    platform: str   # instagram | tiktok | youtube

    @field_validator("platform")
    @classmethod
    def valid_platform(cls, v):
        if v not in ("instagram", "tiktok", "youtube"):
            raise ValueError("platform must be instagram, tiktok, or youtube")
        return v

    @field_validator("handle")
    @classmethod
    def clean_handle(cls, v):
        return v.lstrip("@").strip()


class CompetitorRequest(BaseModel):
    handle: str
    platform: str

    @field_validator("platform")
    @classmethod
    def valid_platform(cls, v):
        if v not in ("instagram", "tiktok", "youtube"):
            raise ValueError("platform must be instagram, tiktok, or youtube")
        return v


class IdeasRequest(BaseModel):
    count:           int = 10
    theme:           Optional[str] = None
    platform_focus:  Optional[str] = None
    trend_inject:    Optional[List[str]] = None


class ScriptRequest(BaseModel):
    idea:               dict
    duration_target_sec: int  = 45
    style:              str   = "conversational"


# ── Creator Handle Scraping ───────────────────────────────────────────────────

@router.post("/scrape", status_code=202)
async def scrape_creator_handle(
    body: ScrapeRequest,
    current_user: User = Depends(require_credits),
    db: AsyncSession = Depends(get_db),
):
    """Scrape a social media creator handle for pattern analysis."""
    # Deduct credits
    current_user.credits -= settings.CREDITS_PER_CREATOR_SCRAPE

    intel = CreatorIntelligence(
        user_id=current_user.id,
        handle=body.handle,
        platform=body.platform,
        status=ScrapingStatus.PENDING,
    )
    db.add(intel)
    await db.flush()

    from app.workers.intelligence_worker import scrape_creator_handle as worker_task
    task = worker_task.apply_async(
        kwargs={
            "intelligence_id": str(intel.id),
            "handle":          body.handle,
            "platform":        body.platform,
            "user_id":         str(current_user.id),
        },
        queue="intelligence",
    )
    intel.celery_task_id = task.id
    await db.commit()

    return {
        "intelligence_id": str(intel.id),
        "task_id":         task.id,
        "message":         f"Scraping @{body.handle} on {body.platform}.",
        "credits_used":    settings.CREDITS_PER_CREATOR_SCRAPE,
    }


@router.get("/scrape/{intelligence_id}")
async def get_scrape_result(
    intelligence_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CreatorIntelligence).where(
            CreatorIntelligence.id == intelligence_id,
            CreatorIntelligence.user_id == current_user.id,
        )
    )
    intel = result.scalar_one_or_none()
    if not intel:
        raise HTTPException(404, "Creator intelligence record not found.")

    job_status = await get_job_status(intel.celery_task_id) if intel.celery_task_id else None

    if intel.status == ScrapingStatus.PENDING:
        return {"status": "pending", "job_status": job_status}
    if intel.status == ScrapingStatus.RUNNING:
        return {"status": "running", "job_status": job_status}
    if intel.status == ScrapingStatus.FAILED:
        raise HTTPException(500, f"Scraping failed: {intel.error_message}")

    return {
        "intelligence_id":       str(intel.id),
        "handle":                intel.handle,
        "platform":              intel.platform,
        "posts_scraped":         intel.posts_scraped,
        "niche_summary":         intel.niche_summary,
        "best_posting_times":    intel.best_posting_times,
        "best_video_length_sec": intel.best_video_length_sec,
        "top_hooks":             intel.top_hooks,
        "topic_clusters":        intel.topic_clusters,
        "engagement_patterns":   intel.engagement_patterns,
        "avg_virality_score":    intel.avg_virality_score,
        "strategic_insights":    intel.strategic_insights,
        "created_at":            intel.created_at.isoformat(),
    }


@router.get("/scrape/")
async def list_scrapes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CreatorIntelligence)
        .where(CreatorIntelligence.user_id == current_user.id)
        .order_by(desc(CreatorIntelligence.created_at))
        .limit(20)
    )
    items = result.scalars().all()
    return [
        {
            "id":       str(i.id),
            "handle":   i.handle,
            "platform": i.platform,
            "status":   i.status,
            "posts_scraped": i.posts_scraped,
            "created_at": i.created_at.isoformat(),
        }
        for i in items
    ]


# ── Viral Pattern Engine ──────────────────────────────────────────────────────

@router.get("/patterns")
async def get_viral_pattern_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate stats over the viral pattern database."""
    stats = await viral_pattern_service.get_pattern_stats(db)
    return stats


@router.get("/patterns/similar")
async def get_similar_patterns(
    virality_score: int = 70,
    topic_category: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    patterns = await viral_pattern_service.get_similar_patterns(
        virality_score=virality_score,
        topic_category=topic_category,
        hook_type=None,
        db=db,
    )
    return {"patterns": patterns, "count": len(patterns)}


# ── Virality Score Explanation ────────────────────────────────────────────────

@router.get("/explain/{video_id}")
async def explain_virality(
    video_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Feature #9: Detailed factor-by-factor virality score breakdown.
    """
    cache_key = f"explain:{video_id}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    result = await db.execute(
        select(Analysis).where(
            Analysis.media_id == video_id,
            Analysis.user_id  == current_user.id,
            Analysis.status   == AnalysisStatus.COMPLETE,
        )
    )
    analysis = result.scalar_one_or_none()
    if not analysis:
        raise HTTPException(404, "Completed analysis not found.")

    similar = await viral_pattern_service.get_similar_patterns(
        virality_score=analysis.virality_score or 50,
        topic_category=analysis.niche_alignment,
        hook_type=None,
        db=db,
        limit=5,
    )

    explanation = await viral_pattern_service.explain_virality_score(analysis, similar)
    await cache_set(cache_key, explanation, ttl=86400)
    return explanation


# ── Timeline Feedback ─────────────────────────────────────────────────────────

@router.get("/timeline/{video_id}")
async def get_timeline_feedback(
    video_id: str,
    regenerate: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Feature #5: Timestamp-based feedback for every video.
    Returns cached result or generates on demand.
    """
    # Check for existing record
    if not regenerate:
        result = await db.execute(
            select(TimelineFeedback)
            .join(Analysis, TimelineFeedback.analysis_id == Analysis.id)
            .where(
                Analysis.media_id == video_id,
                TimelineFeedback.user_id == current_user.id,
            )
        )
        fb = result.scalar_one_or_none()
        if fb:
            return {
                "video_id":      video_id,
                "events":        fb.events,
                "summary":       fb.summary,
                "total_events":  len(fb.events or []),
            }

    # Generate on demand
    analysis_result = await db.execute(
        select(Analysis).where(
            Analysis.media_id == video_id,
            Analysis.user_id  == current_user.id,
            Analysis.status   == AnalysisStatus.COMPLETE,
        )
    )
    analysis = analysis_result.scalar_one_or_none()
    if not analysis:
        raise HTTPException(404, "Completed analysis not found.")

    fb_data = await timeline_service.generate_timeline_feedback(analysis)

    # Persist
    fb_record = TimelineFeedback(
        analysis_id=analysis.id,
        user_id=current_user.id,
        events=fb_data.get("events", []),
        summary=fb_data.get("summary", ""),
    )
    db.add(fb_record)
    await db.commit()

    return {
        "video_id":     video_id,
        "events":       fb_data.get("events", []),
        "summary":      fb_data.get("summary", ""),
        "total_events": fb_data.get("total_events", 0),
        "critical_count": fb_data.get("critical_count", 0),
        "positive_count": fb_data.get("positive_count", 0),
    }


# ── Content Strategist ────────────────────────────────────────────────────────

@router.post("/ideas")
async def generate_ideas(
    body: IdeasRequest,
    current_user: User = Depends(require_credits),
    db: AsyncSession = Depends(get_db),
):
    """Feature #6: Generate personalised viral video ideas."""
    current_user.credits -= settings.CREDITS_PER_CONTENT_IDEAS
    await db.commit()

    ideas = await content_strategist_service.generate_content_ideas(
        user_id=str(current_user.id),
        db=db,
        count=min(body.count, 20),
        theme=body.theme,
        platform_focus=body.platform_focus,
        trend_inject=body.trend_inject,
    )
    return {"ideas": ideas, "count": len(ideas), "credits_used": settings.CREDITS_PER_CONTENT_IDEAS}


@router.post("/script")
async def generate_script(
    body: ScriptRequest,
    current_user: User = Depends(require_credits),
    db: AsyncSession = Depends(get_db),
):
    """Expand a video idea into a full word-for-word script."""
    script = await content_strategist_service.generate_script(
        user_id=str(current_user.id),
        db=db,
        idea=body.idea,
        duration_target_sec=body.duration_target_sec,
        style=body.style,
    )
    return script


@router.get("/trends")
async def get_trend_report(
    niche: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a current trend report for the creator's niche."""
    report = await content_strategist_service.get_trend_report(
        user_id=str(current_user.id),
        db=db,
        niche=niche,
    )
    return report


# ── Competitor Analysis ───────────────────────────────────────────────────────

@router.post("/competitor", status_code=202)
async def start_competitor_analysis(
    body: CompetitorRequest,
    current_user: User = Depends(require_credits),
    db: AsyncSession = Depends(get_db),
):
    """Feature #7: Start a competitor account analysis."""
    current_user.credits -= settings.CREDITS_PER_COMPETITOR_ANALYSIS

    record = CompetitorAnalysis(
        user_id=current_user.id,
        competitor_handle=body.handle.lstrip("@"),
        platform=body.platform,
        status=ScrapingStatus.PENDING,
    )
    db.add(record)
    await db.flush()

    from app.workers.intelligence_worker import run_competitor_analysis
    task = run_competitor_analysis.apply_async(
        kwargs={
            "competitor_analysis_id": str(record.id),
            "competitor_handle":      body.handle.lstrip("@"),
            "platform":               body.platform,
            "requesting_user_id":     str(current_user.id),
        },
        queue="intelligence",
    )
    record.celery_task_id = task.id
    await db.commit()

    return {
        "competitor_analysis_id": str(record.id),
        "task_id":                task.id,
        "message":                f"Analysing @{body.handle} on {body.platform}.",
        "credits_used":           settings.CREDITS_PER_COMPETITOR_ANALYSIS,
    }


@router.get("/competitor/{analysis_id}")
async def get_competitor_analysis(
    analysis_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CompetitorAnalysis).where(
            CompetitorAnalysis.id      == analysis_id,
            CompetitorAnalysis.user_id == current_user.id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "Competitor analysis not found.")

    job_status = await get_job_status(record.celery_task_id) if record.celery_task_id else None

    if record.status in (ScrapingStatus.PENDING, ScrapingStatus.RUNNING):
        return {"status": record.status, "job_status": job_status}
    if record.status == ScrapingStatus.FAILED:
        raise HTTPException(500, f"Analysis failed: {record.error_message}")

    return {
        "competitor_analysis_id":    str(record.id),
        "competitor_handle":         record.competitor_handle,
        "platform":                  record.platform,
        "top_performing_content":    record.top_performing_content,
        "viral_patterns":            record.viral_patterns,
        "engagement_strategies":     record.engagement_strategies,
        "posting_schedule":          record.posting_schedule,
        "content_pillars":           record.content_pillars,
        "gap_opportunities":         record.gap_opportunities,
        "threat_level":              record.threat_level,
        "strategic_recommendations": record.strategic_recommendations,
        "created_at":                record.created_at.isoformat(),
    }
