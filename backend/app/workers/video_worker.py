"""
app/workers/video_worker.py
Celery tasks for video ingestion:
  1. Download (if link-based)
  2. Extract audio with FFmpeg
  3. Transcribe with Whisper
  4. Trigger analysis worker
"""

import asyncio
import logging
from typing import Optional

from app.workers.celery_app import celery_app
from app.core.cache import set_job_status

logger = logging.getLogger(__name__)


def _run(coro):
    """Run an async coroutine from a sync Celery task."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@celery_app.task(
    name="app.workers.video_worker.process_video",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="video",
)
def process_video(self, media_id: str, user_id: str, link: Optional[str] = None):
    """
    Main video processing pipeline task.
    Steps: download → extract audio → transcribe → enqueue analysis
    """
    logger.info("[video_worker] Starting process_video for media_id=%s", media_id)
    task_id = self.request.id

    async def _pipeline():
        from app.core.database import AsyncSessionLocal
        from app.services.video_service import ingest_link, extract_audio, extract_video_metadata
        from app.services.transcription_service import transcribe, extract_hook_transcript, build_timed_segments
        from app.models.media import Media, MediaStatus
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            # 1. Load media record
            result = await db.execute(select(Media).where(Media.id == media_id))
            media = result.scalar_one_or_none()
            if not media:
                raise ValueError(f"Media record {media_id} not found.")

            await set_job_status(task_id, {"status": "downloading", "media_id": media_id})
            media.status = MediaStatus.PROCESSING
            await db.commit()

            # 2. Download if link-based
            if link:
                logger.info("[video_worker] Downloading from URL: %s", link)
                meta = await ingest_link(link, user_id)
                media.storage_path = meta["storage_path"]
                media.storage_url  = meta["storage_url"]
                media.duration_sec = meta.get("duration_sec")
                media.width        = meta.get("width")
                media.height       = meta.get("height")
                media.fps          = meta.get("fps")
                media.title        = meta.get("title") or media.title
                media.file_size_bytes = meta.get("file_size_bytes")
                await db.commit()

            if not media.storage_path:
                raise ValueError("No storage path found for media.")

            # 3. Extract audio
            await set_job_status(task_id, {"status": "extracting_audio", "media_id": media_id})
            audio_path = await extract_audio(media.storage_path)
            media.audio_path = audio_path
            await db.commit()

            # 4. Transcribe
            await set_job_status(task_id, {"status": "transcribing", "media_id": media_id})
            from app.core.config import settings
            whisper_result = await transcribe(audio_path, settings.WHISPER_MODEL, settings.WHISPER_DEVICE)
            transcript = whisper_result.get("text", "")
            segments   = build_timed_segments(whisper_result.get("segments", []))

            media.transcript = transcript
            await db.commit()

            await set_job_status(task_id, {"status": "transcribed", "media_id": media_id})
            logger.info("[video_worker] Transcription done for %s (%d chars)", media_id, len(transcript))

    try:
        _run(_pipeline())
    except Exception as exc:
        logger.exception("[video_worker] Error in process_video: %s", exc)
        _run(set_job_status(task_id, {"status": "failed", "error": str(exc), "media_id": media_id}))
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            _run(_mark_media_failed(media_id, str(exc)))
        return

    # 5. Enqueue analysis worker
    from app.workers.analysis_worker import run_full_analysis
    run_full_analysis.apply_async(
        kwargs={"media_id": media_id, "user_id": user_id},
        queue="analysis",
    )
    logger.info("[video_worker] Enqueued analysis for media_id=%s", media_id)


async def _mark_media_failed(media_id: str, reason: str):
    from app.core.database import AsyncSessionLocal
    from app.models.media import Media, MediaStatus
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Media).where(Media.id == media_id))
        media = result.scalar_one_or_none()
        if media:
            media.status = MediaStatus.FAILED
            await db.commit()
