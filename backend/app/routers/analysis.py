"""
app/routers/analysis.py
Analysis result endpoints:
  GET  /analyze/{video_id}        – fetch complete analysis
  GET  /analyze/{video_id}/status – poll job status
  GET  /analyze/                  – list all analyses for user
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.cache import cache_get, CacheKeys
from app.models.user import User
from app.models.analysis import Analysis, AnalysisStatus
from app.models.media import Media

router = APIRouter()


@router.get("/{video_id}", response_model=dict)
async def get_analysis(
    video_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the full structured analysis for a given video ID.
    Serves from Redis cache if available.
    """
    # Try cache first
    cached = await cache_get(CacheKeys.ANALYSIS_RESULT.format(video_id=video_id))
    if cached:
        return cached

    # Verify ownership
    result = await db.execute(
        select(Analysis)
        .join(Media, Analysis.media_id == Media.id)
        .where(
            Analysis.media_id == video_id,
            Media.user_id == current_user.id,
        )
    )
    analysis = result.scalar_one_or_none()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found.")

    if analysis.status == AnalysisStatus.QUEUED:
        raise HTTPException(status_code=202, detail="Analysis is still queued.")
    if analysis.status == AnalysisStatus.RUNNING:
        raise HTTPException(status_code=202, detail="Analysis is still processing.")
    if analysis.status == AnalysisStatus.FAILED:
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {analysis.error_message or 'Unknown error'}",
        )

    return analysis.to_response_dict()


@router.get("/{video_id}/status")
async def get_analysis_status(
    video_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lightweight status polling endpoint."""
    result = await db.execute(
        select(Analysis)
        .join(Media, Analysis.media_id == Media.id)
        .where(
            Analysis.media_id == video_id,
            Media.user_id == current_user.id,
        )
    )
    analysis = result.scalar_one_or_none()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found.")

    # Also get real-time job status from Redis
    media_result = await db.execute(select(Media).where(Media.id == video_id))
    media = media_result.scalar_one_or_none()

    job_status = None
    if media and media.celery_task_id:
        from app.core.cache import get_job_status
        job_status = await get_job_status(media.celery_task_id)

    return {
        "video_id":       video_id,
        "analysis_id":    str(analysis.id),
        "status":         analysis.status,
        "job_status":     job_status,
        "error_message":  analysis.error_message,
    }


@router.get("/", response_model=List[dict])
async def list_analyses(
    skip: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a paginated list of completed analyses for the current user."""
    result = await db.execute(
        select(Analysis)
        .where(
            Analysis.user_id == current_user.id,
            Analysis.status == AnalysisStatus.COMPLETE,
        )
        .order_by(desc(Analysis.created_at))
        .offset(skip)
        .limit(min(limit, 100))
    )
    analyses = result.scalars().all()
    return [a.to_response_dict() for a in analyses]
