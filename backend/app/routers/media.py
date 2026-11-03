"""
app/routers/media.py
Media ingestion endpoints:
  POST /media/upload   – multipart video upload
  POST /media/link     – submit a Reel/Short/TikTok URL
  GET  /media/{id}     – get media details and status
  GET  /media/         – list user's media
"""

import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, BackgroundTasks
from pydantic import BaseModel, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.core.database import get_db
from app.core.security import require_credits, get_current_user
from app.models.user import User
from app.models.media import Media, MediaSource, MediaStatus
from app.models.analysis import Analysis, AnalysisStatus
from app.services.video_service import ingest_upload, _detect_source
from app.core.cache import get_job_status

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class MediaResponse(BaseModel):
    id: str
    title: Optional[str]
    source: str
    status: str
    storage_url: Optional[str]
    thumbnail_url: Optional[str]
    duration_sec: Optional[float]
    analysis_id: Optional[str]
    analysis_status: Optional[str]
    job_status: Optional[dict]
    created_at: str


class LinkSubmitRequest(BaseModel):
    url: str
    title: Optional[str] = None


class LinkSubmitResponse(BaseModel):
    media_id: str
    task_id: str
    message: str


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _media_to_response(media: Media, db: AsyncSession) -> MediaResponse:
    # Fetch linked analysis if any
    ar = await db.execute(select(Analysis).where(Analysis.media_id == media.id))
    analysis = ar.scalar_one_or_none()

    job_status = None
    if media.celery_task_id:
        job_status = await get_job_status(media.celery_task_id)

    return MediaResponse(
        id=str(media.id),
        title=media.title,
        source=media.source,
        status=media.status,
        storage_url=media.storage_url,
        thumbnail_url=media.thumbnail_url,
        duration_sec=media.duration_sec,
        analysis_id=str(analysis.id) if analysis else None,
        analysis_status=analysis.status if analysis else None,
        job_status=job_status,
        created_at=media.created_at.isoformat(),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/upload", status_code=202)
async def upload_video(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    current_user: User = Depends(require_credits),
    db: AsyncSession = Depends(get_db),
):
    """Accept a video file, store it, and enqueue processing."""
    try:
        meta = await ingest_upload(file, str(current_user.id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    media = Media(
        user_id=current_user.id,
        title=title or file.filename,
        storage_path=meta["storage_path"],
        storage_url=meta["storage_url"],
        source=MediaSource.UPLOAD,
        status=MediaStatus.PROCESSING,
        duration_sec=meta.get("duration_sec"),
        file_size_bytes=meta.get("file_size_bytes"),
        mime_type=meta.get("mime_type"),
        width=meta.get("width"),
        height=meta.get("height"),
        fps=meta.get("fps"),
    )
    db.add(media)
    await db.flush()

    # Create placeholder Analysis
    analysis = Analysis(media_id=media.id, user_id=current_user.id, status=AnalysisStatus.QUEUED)
    db.add(analysis)
    await db.flush()

    # Enqueue worker
    from app.workers.video_worker import process_video
    task = process_video.apply_async(
        kwargs={"media_id": str(media.id), "user_id": str(current_user.id)},
        queue="video",
    )
    media.celery_task_id = task.id
    await db.commit()

    return {
        "media_id": str(media.id),
        "task_id": task.id,
        "message": "Video uploaded and queued for analysis.",
    }


@router.post("/link", response_model=LinkSubmitResponse, status_code=202)
async def submit_link(
    body: LinkSubmitRequest,
    current_user: User = Depends(require_credits),
    db: AsyncSession = Depends(get_db),
):
    """Submit a social media link for download and analysis."""
    source = _detect_source(body.url)

    media = Media(
        user_id=current_user.id,
        title=body.title,
        original_url=body.url,
        source=source,
        status=MediaStatus.PENDING,
    )
    db.add(media)
    await db.flush()

    analysis = Analysis(media_id=media.id, user_id=current_user.id, status=AnalysisStatus.QUEUED)
    db.add(analysis)
    await db.flush()

    from app.workers.video_worker import process_video
    task = process_video.apply_async(
        kwargs={
            "media_id": str(media.id),
            "user_id":  str(current_user.id),
            "link":     body.url,
        },
        queue="video",
    )
    media.celery_task_id = task.id
    await db.commit()

    return LinkSubmitResponse(
        media_id=str(media.id),
        task_id=task.id,
        message=f"Link submitted and queued. Source detected: {source}.",
    )


@router.get("/{media_id}", response_model=MediaResponse)
async def get_media(
    media_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Media).where(Media.id == media_id, Media.user_id == current_user.id)
    )
    media = result.scalar_one_or_none()
    if not media:
        raise HTTPException(status_code=404, detail="Media not found.")
    return await _media_to_response(media, db)


@router.get("/", response_model=List[MediaResponse])
async def list_media(
    skip: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Media)
        .where(Media.user_id == current_user.id)
        .order_by(desc(Media.created_at))
        .offset(skip)
        .limit(min(limit, 100))
    )
    media_list = result.scalars().all()
    return [await _media_to_response(m, db) for m in media_list]
