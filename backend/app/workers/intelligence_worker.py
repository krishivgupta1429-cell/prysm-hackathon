"""
app/workers/intelligence_worker.py
Background Celery tasks for all v2 intelligence features:
  • scrape_creator_handle    – Creator Profile Intelligence
  • run_competitor_analysis  – Competitor Analysis
  • generate_video_clips     – Automatic Clip Generation
  • store_viral_pattern      – Viral Pattern Engine (post-analysis hook)
  • generate_timeline_fb     – Timeline Feedback generation
"""

import asyncio
import logging
import time
from typing import Optional

from app.workers.celery_app import celery_app
from app.core.cache import set_job_status, cache_set, CacheKeys
from app.core.metrics import (
    scraping_jobs_total, competitor_analyses_total,
    clips_generated_total, celery_tasks_total,
)

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


# ── Task 1: Scrape creator handle ─────────────────────────────────────────────

@celery_app.task(
    name="app.workers.intelligence_worker.scrape_creator_handle",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="intelligence",
)
def scrape_creator_handle(self, intelligence_id: str, handle: str, platform: str, user_id: str):
    """
    Scrapes a creator handle and stores derived insights in CreatorIntelligence.
    """
    task_id = self.request.id
    celery_tasks_total.labels(queue="intelligence", status="started").inc()
    logger.info("[intel_worker] Scraping @%s on %s (job=%s)", handle, platform, intelligence_id)

    async def _run_scrape():
        from app.core.database import AsyncSessionLocal
        from app.models.intelligence import CreatorIntelligence, ScrapingStatus
        from app.services.creator_scraper_service import scrape_creator, analyze_creator_patterns
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(CreatorIntelligence).where(CreatorIntelligence.id == intelligence_id)
            )
            intel = result.scalar_one_or_none()
            if not intel:
                raise ValueError(f"CreatorIntelligence {intelligence_id} not found.")

            await set_job_status(task_id, {"status": "scraping", "handle": handle})
            intel.status = ScrapingStatus.RUNNING
            await db.commit()

            # Scrape posts
            scrape_data = await scrape_creator(handle, platform)
            posts = scrape_data.get("posts", [])
            intel.posts_scraped = len(posts)
            intel.raw_posts = posts[:50]   # store raw data (capped at 50)
            await db.commit()

            await set_job_status(task_id, {"status": "analysing", "posts": len(posts)})

            # AI pattern analysis
            insights = await analyze_creator_patterns(posts, handle, platform)

            intel.best_posting_times     = insights.get("best_posting_times", [])
            intel.best_video_length_sec  = insights.get("best_video_length_sec")
            intel.top_hooks              = insights.get("top_hooks", [])
            intel.topic_clusters         = insights.get("topic_clusters", [])
            intel.engagement_patterns    = insights.get("engagement_patterns", {})
            intel.avg_virality_score     = insights.get("avg_virality_estimate")
            intel.niche_summary          = insights.get("niche_summary")
            intel.strategic_insights     = insights.get("strategic_insights", [])
            intel.status                 = ScrapingStatus.COMPLETE
            intel.celery_task_id         = task_id
            await db.commit()

            await set_job_status(task_id, {
                "status": "complete",
                "intelligence_id": intelligence_id,
                "posts_scraped": len(posts),
            })
            celery_tasks_total.labels(queue="intelligence", status="complete").inc()
            logger.info("[intel_worker] Scrape complete for @%s (%d posts)", handle, len(posts))

    try:
        _run(_run_scrape())
    except Exception as exc:
        logger.exception("[intel_worker] scrape_creator_handle failed: %s", exc)
        _run(set_job_status(task_id, {"status": "failed", "error": str(exc)}))
        _run(_mark_intelligence_failed(intelligence_id, str(exc)))
        celery_tasks_total.labels(queue="intelligence", status="failed").inc()
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass


# ── Task 2: Competitor Analysis ───────────────────────────────────────────────

@celery_app.task(
    name="app.workers.intelligence_worker.run_competitor_analysis",
    bind=True,
    max_retries=2,
    default_retry_delay=90,
    queue="intelligence",
)
def run_competitor_analysis(
    self,
    competitor_analysis_id: str,
    competitor_handle: str,
    platform: str,
    requesting_user_id: str,
):
    task_id = self.request.id
    celery_tasks_total.labels(queue="intelligence", status="started").inc()
    logger.info("[intel_worker] Competitor analysis @%s on %s", competitor_handle, platform)

    async def _run_analysis():
        from app.core.database import AsyncSessionLocal
        from app.models.intelligence import CompetitorAnalysis, ScrapingStatus
        from app.services.competitor_service import analyze_competitor
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(CompetitorAnalysis).where(CompetitorAnalysis.id == competitor_analysis_id)
            )
            record = result.scalar_one_or_none()
            if not record:
                raise ValueError(f"CompetitorAnalysis {competitor_analysis_id} not found.")

            record.status = ScrapingStatus.RUNNING
            await db.commit()
            await set_job_status(task_id, {"status": "running", "competitor": competitor_handle})

            result_data = await analyze_competitor(
                competitor_handle=competitor_handle,
                platform=platform,
                requesting_user_id=requesting_user_id,
                db=db,
            )

            record.top_performing_content    = result_data.get("top_performing_content", [])
            record.viral_patterns            = result_data.get("viral_patterns", {})
            record.engagement_strategies     = result_data.get("engagement_strategies", [])
            record.posting_schedule          = result_data.get("posting_schedule", {})
            record.content_pillars           = result_data.get("content_pillars", [])
            record.gap_opportunities         = result_data.get("gap_opportunities", [])
            record.threat_level              = result_data.get("threat_level", "medium")
            record.strategic_recommendations = result_data.get("strategic_recommendations", [])
            record.status                    = ScrapingStatus.COMPLETE
            record.celery_task_id            = task_id
            await db.commit()

            await set_job_status(task_id, {
                "status": "complete",
                "competitor_analysis_id": competitor_analysis_id,
            })
            celery_tasks_total.labels(queue="intelligence", status="complete").inc()

    try:
        _run(_run_analysis())
    except Exception as exc:
        logger.exception("[intel_worker] competitor analysis failed: %s", exc)
        _run(set_job_status(task_id, {"status": "failed", "error": str(exc)}))
        _run(_mark_competitor_failed(competitor_analysis_id, str(exc)))
        celery_tasks_total.labels(queue="intelligence", status="failed").inc()
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass


# ── Task 3: Clip Generation ───────────────────────────────────────────────────

@celery_app.task(
    name="app.workers.intelligence_worker.generate_video_clips",
    bind=True,
    max_retries=1,
    default_retry_delay=120,
    queue="clips",
)
def generate_video_clips(self, clip_job_id: str, media_id: str, user_id: str):
    task_id = self.request.id
    celery_tasks_total.labels(queue="clips", status="started").inc()
    logger.info("[intel_worker] Clip generation for media_id=%s", media_id)

    async def _run_clips():
        from app.core.database import AsyncSessionLocal
        from app.models.intelligence import ClipJob, ClipStatus
        from app.models.media import Media
        from app.services.clip_generation_service import generate_clips
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            job_result = await db.execute(select(ClipJob).where(ClipJob.id == clip_job_id))
            job = job_result.scalar_one_or_none()
            if not job:
                raise ValueError(f"ClipJob {clip_job_id} not found.")

            media_result = await db.execute(select(Media).where(Media.id == media_id))
            media = media_result.scalar_one_or_none()
            if not media or not media.storage_path:
                raise ValueError(f"Media {media_id} not ready for clip generation.")

            job.status = ClipStatus.PROCESSING
            await db.commit()
            await set_job_status(task_id, {"status": "processing", "media_id": media_id})

            clips = await generate_clips(
                media_id=str(media_id),
                video_path=media.storage_path,
                user_id=user_id,
                max_clips=job.max_clips,
                min_sec=job.min_clip_sec,
                max_sec=job.max_clip_sec,
                add_captions=job.add_captions,
                caption_style=job.caption_style,
            )

            job.clips       = clips
            job.total_clips = len(clips)
            job.status      = ClipStatus.COMPLETE
            job.celery_task_id = task_id
            await db.commit()

            await set_job_status(task_id, {
                "status": "complete",
                "clip_job_id": clip_job_id,
                "total_clips": len(clips),
            })
            celery_tasks_total.labels(queue="clips", status="complete").inc()
            logger.info("[intel_worker] Generated %d clips for media_id=%s", len(clips), media_id)

    try:
        _run(_run_clips())
    except Exception as exc:
        logger.exception("[intel_worker] clip generation failed: %s", exc)
        _run(set_job_status(task_id, {"status": "failed", "error": str(exc)}))
        _run(_mark_clip_failed(clip_job_id, str(exc)))
        celery_tasks_total.labels(queue="clips", status="failed").inc()
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass


# ── Task 4: Timeline feedback (called post-analysis) ─────────────────────────

@celery_app.task(
    name="app.workers.intelligence_worker.generate_timeline_feedback",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    queue="analysis",
)
def generate_timeline_feedback(self, analysis_id: str, media_id: str, user_id: str):
    task_id = self.request.id

    async def _run_timeline():
        from app.core.database import AsyncSessionLocal
        from app.models.analysis import Analysis
        from app.models.intelligence import TimelineFeedback
        from app.services.timeline_service import generate_timeline_feedback as gen_fb
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Analysis).where(Analysis.id == analysis_id))
            analysis = result.scalar_one_or_none()
            if not analysis:
                return

            fb_data = await gen_fb(analysis)

            # Upsert TimelineFeedback
            fb_result = await db.execute(
                select(TimelineFeedback).where(TimelineFeedback.analysis_id == analysis_id)
            )
            feedback = fb_result.scalar_one_or_none()
            if not feedback:
                feedback = TimelineFeedback(
                    analysis_id=analysis_id,
                    user_id=user_id,
                )
                db.add(feedback)

            feedback.events  = fb_data.get("events", [])
            feedback.summary = fb_data.get("summary", "")
            await db.commit()
            logger.info("[intel_worker] Timeline feedback generated for analysis %s", analysis_id)

    try:
        _run(_run_timeline())
    except Exception as exc:
        logger.warning("[intel_worker] timeline feedback failed (non-critical): %s", exc)


# ── Error helpers ─────────────────────────────────────────────────────────────

async def _mark_intelligence_failed(record_id: str, reason: str):
    from app.core.database import AsyncSessionLocal
    from app.models.intelligence import CreatorIntelligence, ScrapingStatus
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(CreatorIntelligence).where(CreatorIntelligence.id == record_id))
        obj = r.scalar_one_or_none()
        if obj:
            obj.status = ScrapingStatus.FAILED
            obj.error_message = reason
            await db.commit()


async def _mark_competitor_failed(record_id: str, reason: str):
    from app.core.database import AsyncSessionLocal
    from app.models.intelligence import CompetitorAnalysis, ScrapingStatus
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(CompetitorAnalysis).where(CompetitorAnalysis.id == record_id))
        obj = r.scalar_one_or_none()
        if obj:
            obj.status = ScrapingStatus.FAILED
            obj.error_message = reason
            await db.commit()


async def _mark_clip_failed(job_id: str, reason: str):
    from app.core.database import AsyncSessionLocal
    from app.models.intelligence import ClipJob, ClipStatus
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(ClipJob).where(ClipJob.id == job_id))
        obj = r.scalar_one_or_none()
        if obj:
            obj.status = ClipStatus.FAILED
            obj.error_message = reason
            await db.commit()
