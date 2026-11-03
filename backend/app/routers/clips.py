"""
app/routers/clips.py
Automatic Clip Generation endpoints – Feature #8

  POST /clips/                 – submit a media_id for clip generation
  GET  /clips/{job_id}         – get clip job status + results
  GET  /clips/                 – list all clip jobs for user
  GET  /clips/{job_id}/download/{n} – serve a generated clip file
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.core.database import get_db
from app.core.security import get_current_user, require_credits
from app.core.cache import get_job_status
from app.core.config import settings
from app.models.user import User
from app.models.media import Media
from app.models.intelligence import ClipJob, ClipStatus
import os

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class ClipRequest(BaseModel):
    media_id:       str
    max_clips:      int   = 5
    min_clip_sec:   float = 15.0
    max_clip_sec:   float = 60.0
    add_captions:   bool  = True
    caption_style:  str   = "tiktok"   # tiktok | minimal | bold

    @field_validator("caption_style")
    @classmethod
    def valid_style(cls, v):
        if v not in ("tiktok", "minimal", "bold"):
            raise ValueError("caption_style must be tiktok, minimal, or bold")
        return v

    @field_validator("max_clips")
    @classmethod
    def sane_max(cls, v):
        return min(max(1, v), 10)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/", status_code=202)
async def submit_clip_job(
    body: ClipRequest,
    current_user: User = Depends(require_credits),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit a (long) video for automatic short-clip generation.
    The video must already be uploaded and transcribed (status=READY).
    """
    # Validate media ownership
    media_result = await db.execute(
        select(Media).where(
            Media.id      == body.media_id,
            Media.user_id == current_user.id,
        )
    )
    media = media_result.scalar_one_or_none()
    if not media:
        raise HTTPException(404, "Media not found.")
    if not media.storage_path:
        raise HTTPException(422, "Media has no storage path. Please upload first.")
    if media.duration_sec and media.duration_sec < body.min_clip_sec * 2:
        raise HTTPException(
            422,
            f"Video is too short ({media.duration_sec:.0f}s) for clip generation. "
            f"Upload a video longer than {body.min_clip_sec * 2:.0f}s."
        )

    # Deduct credits
    current_user.credits -= settings.CREDITS_PER_CLIP_GENERATION

    job = ClipJob(
        user_id=current_user.id,
        media_id=body.media_id,
        status=ClipStatus.QUEUED,
        max_clips=body.max_clips,
        min_clip_sec=body.min_clip_sec,
        max_clip_sec=body.max_clip_sec,
        add_captions=body.add_captions,
        caption_style=body.caption_style,
    )
    db.add(job)
    await db.flush()

    from app.workers.intelligence_worker import generate_video_clips
    task = generate_video_clips.apply_async(
        kwargs={
            "clip_job_id": str(job.id),
            "media_id":    str(body.media_id),
            "user_id":     str(current_user.id),
        },
        queue="clips",
    )
    job.celery_task_id = task.id
    await db.commit()

    return {
        "clip_job_id":  str(job.id),
        "task_id":      task.id,
        "message":      f"Clip generation queued. Will generate up to {body.max_clips} clips.",
        "credits_used": settings.CREDITS_PER_CLIP_GENERATION,
    }


@router.get("/{job_id}")
async def get_clip_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Poll clip job status and retrieve results when complete."""
    result = await db.execute(
        select(ClipJob).where(
            ClipJob.id      == job_id,
            ClipJob.user_id == current_user.id,
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Clip job not found.")

    job_status = await get_job_status(job.celery_task_id) if job.celery_task_id else None

    if job.status in (ClipStatus.QUEUED, ClipStatus.PROCESSING):
        return {
            "clip_job_id": str(job.id),
            "status":      job.status,
            "job_status":  job_status,
        }

    if job.status == ClipStatus.FAILED:
        raise HTTPException(500, f"Clip generation failed: {job.error_message}")

    return {
        "clip_job_id":  str(job.id),
        "media_id":     str(job.media_id),
        "status":       job.status,
        "total_clips":  job.total_clips,
        "clips":        job.clips,
        "settings": {
            "caption_style": job.caption_style,
            "add_captions":  job.add_captions,
        },
        "created_at":   job.created_at.isoformat(),
    }


@router.get("/")
async def list_clip_jobs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ClipJob)
        .where(ClipJob.user_id == current_user.id)
        .order_by(desc(ClipJob.created_at))
        .limit(30)
    )
    jobs = result.scalars().all()
    return [
        {
            "clip_job_id": str(j.id),
            "media_id":    str(j.media_id),
            "status":      j.status,
            "total_clips": j.total_clips,
            "created_at":  j.created_at.isoformat(),
        }
        for j in jobs
    ]


@router.get("/{job_id}/download/{clip_number}")
async def download_clip(
    job_id: str,
    clip_number: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Serve a generated clip file for download."""
    result = await db.execute(
        select(ClipJob).where(
            ClipJob.id      == job_id,
            ClipJob.user_id == current_user.id,
            ClipJob.status  == ClipStatus.COMPLETE,
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Clip job not found or not complete.")

    clips = job.clips or []
    clip = next((c for c in clips if c.get("clip_number") == clip_number), None)
    if not clip:
        raise HTTPException(404, f"Clip #{clip_number} not found.")

    storage_path = clip.get("storage_path")
    if not storage_path or not os.path.exists(storage_path):
        raise HTTPException(404, "Clip file not found on disk.")

    return FileResponse(
        path=storage_path,
        filename=f"clip_{clip_number:02d}.mp4",
        media_type="video/mp4",
    )
