"""
app/workers/analysis_worker.py
Level 2 Multimodal Analysis Pipeline — rebuilt.

Correct stage order:
  Stage 1  FFmpeg frame extraction (parallel with audio extraction)
  Stage 2  Whisper transcription (audio path)
  Stage 3  GPT-4o vision analysis on extracted frames
  Stage 4  Build VideoFrameSummary — the multimodal fusion object
  Stage 5  Hook analysis  (transcript + hook_frames visual context)
  Stage 6  Virality prediction (transcript + full visual context)
  Stage 7  Content breakdown (transcript + scene type context)
  Stage 8  Trend matching (transcript + format context)
  Stage 9  Caption generation (transcript + thumbnail context)
  Stage 10 Retention analysis (segments + visual pacing signals)
  Stage 11 Thumbnail selection (actual analyzed frame candidates)
  Stage 12 Benchmark (vs creator history)
  Stage 13 Store viral pattern, update creator profile, cache

Stages 1+2 run in parallel (FFmpeg frames + FFmpeg audio both start immediately).
Stage 3 starts as soon as frames are extracted, while Whisper may still be running.
Stage 5+ require both transcript and vision data.
"""

import asyncio
import logging
import time
from typing import Optional

from app.workers.celery_app import celery_app
from app.core.cache import set_job_status, cache_set, CacheKeys
from app.core.metrics import analysis_jobs_total, analysis_duration_seconds

logger = logging.getLogger(__name__)


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@celery_app.task(
    name="app.workers.analysis_worker.run_full_analysis",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="analysis",
)
def run_full_analysis(self, media_id: str, user_id: str):
    task_id = self.request.id
    logger.info("[analysis_worker] Starting Level 2 multimodal analysis for media_id=%s", media_id)
    analysis_jobs_total.labels(status="queued").inc()

    async def _pipeline():
        from app.core.database import AsyncSessionLocal
        from app.models.media import Media, MediaStatus
        from app.models.analysis import Analysis, AnalysisStatus
        from app.models.user import User
        from app.services import (
            ai_service,
            transcription_service,
            video_service,
            memory_service,
        )
        from app.services.vision_service import (
            analyze_video_frames,
            summary_to_context_string,
            empty_summary,
        )
        from app.core.config import settings
        from sqlalchemy import select

        start_time = time.time()

        async with AsyncSessionLocal() as db:

            # ── Load media record ─────────────────────────────────────────────
            result = await db.execute(select(Media).where(Media.id == media_id))
            media = result.scalar_one_or_none()
            if not media:
                raise ValueError(f"Media {media_id} not found.")

            # Load or create Analysis record
            ar = await db.execute(select(Analysis).where(Analysis.media_id == media_id))
            analysis = ar.scalar_one_or_none()
            if not analysis:
                analysis = Analysis(media_id=media_id, user_id=user_id)
                db.add(analysis)
                await db.flush()

            analysis.status = AnalysisStatus.RUNNING
            await db.commit()
            await set_job_status(task_id, {"status": "running", "media_id": media_id})

            video_path = media.storage_path
            duration   = media.duration_sec or 60.0
            title      = media.title

            # ─────────────────────────────────────────────────────────────────
            # STAGE 1+2 (parallel): Frame extraction + Audio extraction
            # Both use FFmpeg. Running them together cuts wall-clock time.
            # ─────────────────────────────────────────────────────────────────
            await set_job_status(task_id, {"status": "extracting_frames_and_audio"})
            logger.info("[analysis_worker] Stage 1+2: parallel frame+audio extraction")

            async def _extract_audio_if_needed():
                if media.audio_path:
                    return media.audio_path
                if not video_path:
                    return None
                path = await video_service.extract_audio(video_path)
                media.audio_path = path
                await db.commit()
                return path

            # Run frame extraction and audio extraction in parallel
            audio_task   = asyncio.create_task(_extract_audio_if_needed())
            vision_task  = asyncio.create_task(
                analyze_video_frames(video_path, duration)
                if video_path and settings.VISION_ENABLED
                else asyncio.coroutine(lambda: empty_summary(duration))()
            )

            audio_path, vision_summary = await asyncio.gather(
                audio_task, vision_task, return_exceptions=True
            )

            # Handle exceptions gracefully — vision failure must not block analysis
            if isinstance(vision_summary, Exception):
                logger.warning("[analysis_worker] Vision analysis failed: %s", vision_summary)
                vision_summary = empty_summary(duration)

            if isinstance(audio_path, Exception):
                logger.warning("[analysis_worker] Audio extraction failed: %s", audio_path)
                audio_path = None

            # ─────────────────────────────────────────────────────────────────
            # STAGE 3: Whisper transcription
            # ─────────────────────────────────────────────────────────────────
            await set_job_status(task_id, {
                "status": "transcribing",
                "frames_analyzed": vision_summary.total_frames_analyzed,
            })
            logger.info(
                "[analysis_worker] Stage 3: Whisper transcription | "
                "Vision: %d frames, %d API calls",
                vision_summary.total_frames_analyzed,
                vision_summary.api_calls_made,
            )

            transcript = media.transcript or ""
            segments_raw = []

            if audio_path:
                whisper_result = await transcription_service.transcribe(
                    audio_path,
                    settings.WHISPER_MODEL,
                    settings.WHISPER_DEVICE,
                )
                transcript = whisper_result.get("text", "")
                segments_raw = transcription_service.build_timed_segments(
                    whisper_result.get("segments", [])
                )
                media.transcript = transcript
                await db.commit()
            elif not transcript:
                logger.warning("[analysis_worker] No audio and no transcript for %s", media_id)

            hook_text = transcription_service.extract_hook_transcript(segments_raw, 3.0)
            analysis.segments = segments_raw

            # ─────────────────────────────────────────────────────────────────
            # STAGE 4: Build multimodal fusion context string
            # This is the VideoFrameSummary serialised for Claude injection
            # ─────────────────────────────────────────────────────────────────
            visual_ctx = summary_to_context_string(vision_summary)
            logger.info(
                "[analysis_worker] Stage 4: Multimodal fusion ready | "
                "Visual context: %d chars | Pacing: %s",
                len(visual_ctx),
                vision_summary.pacing_assessment,
            )

            # Store vision data on analysis
            analysis.scenes = [
                {"timestamp": ts, "score": 1.0}
                for ts in vision_summary.scene_changes
            ]
            await db.commit()

            # ─────────────────────────────────────────────────────────────────
            # STAGE 5: Hook Analysis
            # First model call. Has both hook transcript AND hook frame visuals.
            # ─────────────────────────────────────────────────────────────────
            await set_job_status(task_id, {"status": "hook_analysis"})
            hook_data = await ai_service.analyze_hook(
                hook_transcript=hook_text,
                full_transcript=transcript,
                duration=duration,
                visual_context=visual_ctx,
            )
            analysis.hook_score               = hook_data.get("hook_score")
            analysis.hook_transcript          = hook_text
            analysis.hook_retention_potential = hook_data.get("retention_potential")
            analysis.hook_suggestions         = hook_data.get("suggested_hooks", [])
            await db.commit()
            logger.info(
                "[analysis_worker] Stage 5: Hook score=%.1f (verbal=%.1f, visual=%s)",
                analysis.hook_score or 0,
                hook_data.get("verbal_hook_score", 0),
                hook_data.get("visual_hook_score", "n/a"),
            )

            # ─────────────────────────────────────────────────────────────────
            # STAGE 6: Virality Prediction
            # Now sees face presence, emotion arc, pacing, energy levels
            # ─────────────────────────────────────────────────────────────────
            await set_job_status(task_id, {"status": "virality_prediction"})
            virality_data = await ai_service.predict_virality(
                transcript=transcript,
                hook_score=analysis.hook_score or 5.0,
                duration=duration,
                scenes=analysis.scenes or [],
                visual_context=visual_ctx,
            )
            analysis.virality_score      = virality_data.get("virality_score")
            analysis.virality_confidence = virality_data.get("confidence")
            analysis.virality_reasons    = virality_data.get("reasons", [])
            await db.commit()

            # ─────────────────────────────────────────────────────────────────
            # STAGE 7: Content Breakdown
            # ─────────────────────────────────────────────────────────────────
            await set_job_status(task_id, {"status": "content_breakdown"})
            content_data = await ai_service.analyze_content_breakdown(
                transcript=transcript,
                visual_context=visual_ctx,
            )
            analysis.story_structure = content_data.get("story_structure")
            analysis.value_density   = content_data.get("value_density")
            analysis.emotional_hooks = content_data.get("emotional_hooks", [])
            analysis.call_to_action  = content_data.get("call_to_action")
            await db.commit()

            # ─────────────────────────────────────────────────────────────────
            # STAGE 8: Trend Matching
            # ─────────────────────────────────────────────────────────────────
            await set_job_status(task_id, {"status": "trend_matching"})
            creator_ctx = await memory_service.get_creator_context(user_id, db)
            trend_data = await ai_service.match_trends(
                transcript=transcript,
                niche=creator_ctx.get("primary_niche"),
                visual_context=visual_ctx,
            )
            analysis.trend_alignment = trend_data.get("trend_alignment")
            analysis.relevant_trends = trend_data.get("relevant_trends", [])
            analysis.missing_trends  = trend_data.get("missing_trends", [])
            analysis.niche_alignment = trend_data.get("niche_alignment")
            await db.commit()

            # ─────────────────────────────────────────────────────────────────
            # STAGE 9: Caption Generation
            # ─────────────────────────────────────────────────────────────────
            await set_job_status(task_id, {"status": "caption_generation"})
            caption_data = await ai_service.generate_captions(
                transcript=transcript,
                title=title,
                niche=analysis.niche_alignment,
                visual_context=visual_ctx,
            )
            analysis.captions = caption_data.get("captions", {})
            analysis.hashtags = caption_data.get("hashtags", {})
            analysis.titles   = caption_data.get("titles", {})
            await db.commit()

            # ─────────────────────────────────────────────────────────────────
            # STAGE 10: Retention Analysis
            # Now uses visual energy drops and pacing signals
            # ─────────────────────────────────────────────────────────────────
            await set_job_status(task_id, {"status": "retention_analysis"})
            retention_data = await ai_service.analyze_retention(
                segments=segments_raw,
                scenes=analysis.scenes or [],
                transcript=transcript,
                duration=duration,
                visual_context=visual_ctx,
            )
            analysis.pacing_score = retention_data.get("pacing_score")
            analysis.improvements = retention_data.get("improvements", [])
            await db.commit()

            # ─────────────────────────────────────────────────────────────────
            # STAGE 11: Thumbnail Selection
            # Uses ACTUAL analyzed frame descriptions, not timestamp guesses
            # ─────────────────────────────────────────────────────────────────
            await set_job_status(task_id, {"status": "thumbnail_selection"})
            thumbnails = await ai_service.suggest_thumbnails(
                transcript=transcript,
                scene_timestamps=[sc.get("timestamp", 0) for sc in (analysis.scenes or [])],
                duration=duration,
                visual_context=visual_ctx,
                thumbnail_candidates=vision_summary.thumbnail_candidates,
            )
            analysis.thumbnail_suggestions = thumbnails
            await db.commit()

            # ─────────────────────────────────────────────────────────────────
            # STAGE 12: Benchmark
            # ─────────────────────────────────────────────────────────────────
            await set_job_status(task_id, {"status": "benchmarking"})
            benchmark = await ai_service.generate_benchmark(
                analysis_data={
                    "hook_score":    analysis.hook_score,
                    "virality_score": analysis.virality_score,
                    "pacing_score":  analysis.pacing_score,
                    "value_density": analysis.value_density,
                    "face_presence": vision_summary.face_presence_ratio,
                    "pacing_type":   vision_summary.pacing_assessment,
                    "has_visual_hooks": len(vision_summary.visual_hook_moments) > 0,
                },
                creator_profile=creator_ctx,
            )
            analysis.benchmark = benchmark
            await db.commit()

            # ─────────────────────────────────────────────────────────────────
            # STAGE 13: Finalise, deduct credits, update memory
            # ─────────────────────────────────────────────────────────────────
            elapsed = round(time.time() - start_time, 2)
            analysis.processing_time_sec = elapsed
            analysis.status       = AnalysisStatus.COMPLETE
            analysis.credits_used = settings.CREDITS_PER_ANALYSIS
            media.status          = MediaStatus.READY
            await db.commit()

            user_result = await db.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
            if user:
                user.credits = round(max(0, user.credits - settings.CREDITS_PER_ANALYSIS), 1)
                await db.commit()

            # Update creator memory
            await memory_service.update_profile_from_analysis(user_id, analysis, db)

            # Cache complete result
            result_dict = analysis.to_response_dict()
            result_dict["vision_summary"] = {
                "frames_analyzed":    vision_summary.total_frames_analyzed,
                "face_presence":      vision_summary.face_presence_ratio,
                "pacing":             vision_summary.pacing_assessment,
                "dominant_scene":     vision_summary.dominant_scene_type,
                "hook_visual":        vision_summary.hook_visual_description,
                "hook_emotion":       vision_summary.hook_dominant_emotion,
                "thumbnail_candidates": vision_summary.thumbnail_candidates,
                "visual_hook_moments": vision_summary.visual_hook_moments,
            }
            await cache_set(
                CacheKeys.ANALYSIS_RESULT.format(video_id=media_id),
                result_dict,
                ttl=86400,
            )

            analysis_jobs_total.labels(status="complete").inc()
            analysis_duration_seconds.observe(elapsed)

            await set_job_status(task_id, {
                "status": "complete",
                "media_id":           media_id,
                "analysis_id":        str(analysis.id),
                "processing_time_sec": elapsed,
                "frames_analyzed":    vision_summary.total_frames_analyzed,
                "vision_api_calls":   vision_summary.api_calls_made,
            })
            logger.info(
                "[analysis_worker] ✅ Complete | media=%s | %.1fs | "
                "frames=%d | vision_calls=%d | virality=%s | hook=%.1f",
                media_id, elapsed,
                vision_summary.total_frames_analyzed,
                vision_summary.api_calls_made,
                analysis.virality_score,
                analysis.hook_score or 0,
            )

    try:
        _run(_pipeline())
    except Exception as exc:
        logger.exception("[analysis_worker] Pipeline failed for media_id=%s: %s", media_id, exc)
        _run(set_job_status(task_id, {"status": "failed", "error": str(exc)}))
        _run(_mark_analysis_failed(media_id, str(exc)))
        analysis_jobs_total.labels(status="failed").inc()
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass


async def _mark_analysis_failed(media_id: str, reason: str):
    from app.core.database import AsyncSessionLocal
    from app.models.analysis import Analysis, AnalysisStatus
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Analysis).where(Analysis.media_id == media_id))
        analysis = result.scalar_one_or_none()
        if analysis:
            analysis.status = AnalysisStatus.FAILED
            analysis.error_message = reason[:500]
            await db.commit()
