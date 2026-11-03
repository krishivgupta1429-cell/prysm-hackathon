"""
app/workers/celery_app.py
Celery application factory – broker and backend are both Redis.
"""

from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "creator_ai",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.video_worker",
        "app.workers.analysis_worker",
        "app.workers.intelligence_worker",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,

    task_routes={
        "app.workers.video_worker.*":        {"queue": "video"},
        "app.workers.analysis_worker.*":     {"queue": "analysis"},
        "app.workers.intelligence_worker.*": {"queue": "intelligence"},
        # Clip jobs get their own high-resource queue
        "app.workers.intelligence_worker.generate_video_clips": {"queue": "clips"},
    },
    task_default_queue="default",
    task_queues={
        "video":        {"routing_key": "video"},
        "analysis":     {"routing_key": "analysis"},
        "intelligence": {"routing_key": "intelligence"},
        "clips":        {"routing_key": "clips"},
        "default":      {"routing_key": "default"},
    },

    # Retry policy defaults
    task_max_retries=3,
    task_retry_backoff=True,
    task_retry_backoff_max=600,
    task_retry_jitter=True,

    broker_connection_retry_on_startup=True,

    # Beat schedule for periodic tasks
    beat_schedule={
        "refresh-trending-topics": {
            "task":     "app.workers.analysis_worker.refresh_trending_topics",
            "schedule": 3600,   # every hour
        },
    },
)
