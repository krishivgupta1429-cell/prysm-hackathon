"""
app/core/metrics.py
Prometheus metrics — wrapped in try/except so server starts even without prometheus.
"""

import contextlib
import time
import logging

logger = logging.getLogger(__name__)


class _Stub:
    def labels(self, **kwargs): return self
    def inc(self, *a, **kw): pass
    def observe(self, *a, **kw): pass
    def set(self, *a, **kw): pass
    def time(self): return _StubCtx()


class _StubCtx:
    def __enter__(self): return self
    def __exit__(self, *a): pass


def _make_counter(name, doc):
    try:
        from prometheus_client import Counter
        return Counter(name, doc)
    except Exception:
        return _Stub()


def _make_histogram(name, doc, buckets=None):
    try:
        from prometheus_client import Histogram
        if buckets:
            return Histogram(name, doc, buckets=buckets)
        return Histogram(name, doc)
    except Exception:
        return _Stub()


def _make_gauge(name, doc):
    try:
        from prometheus_client import Gauge
        return Gauge(name, doc)
    except Exception:
        return _Stub()


http_requests_total = _make_counter("creator_ai_http_requests_total", "Total HTTP requests")
http_request_duration_seconds = _make_histogram("creator_ai_http_request_duration_seconds", "HTTP request duration")
analysis_jobs_total = _make_counter("creator_ai_analysis_jobs_total", "Total analysis jobs")
analysis_duration_seconds = _make_histogram("creator_ai_analysis_duration_seconds", "Analysis job duration")
analysis_stage_duration = _make_histogram("creator_ai_analysis_stage_duration_seconds", "Per-stage analysis duration")
vision_frames_processed = _make_counter("creator_ai_vision_frames_processed_total", "Vision frames processed")
vision_faces_detected = _make_counter("creator_ai_vision_faces_detected_total", "Faces detected in vision")
celery_tasks_active = _make_gauge("creator_ai_celery_tasks_active", "Active Celery tasks")
celery_tasks_total = _make_counter("creator_ai_celery_tasks_total", "Total Celery tasks")
credits_consumed_total = _make_counter("creator_ai_credits_consumed_total", "Credits consumed")
active_users = _make_gauge("creator_ai_active_users", "Active users")
cache_hits_total = _make_counter("creator_ai_cache_hits_total", "Cache hits")
cache_misses_total = _make_counter("creator_ai_cache_misses_total", "Cache misses")
viral_patterns_stored = _make_counter("creator_ai_viral_patterns_stored_total", "Viral patterns stored")
clips_generated_total = _make_counter("creator_ai_clips_generated_total", "Clips generated")
scraping_jobs_total = _make_counter("creator_ai_scraping_jobs_total", "Scraping jobs")
competitor_analyses_total = _make_counter("creator_ai_competitor_analyses_total", "Competitor analyses")
content_ideas_generated = _make_counter("creator_ai_content_ideas_generated_total", "Content ideas generated")


@contextlib.contextmanager
def track_stage(stage_name: str):
    start = time.time()
    try:
        yield
    finally:
        try:
            analysis_stage_duration.observe(time.time() - start)
        except Exception:
            pass


def get_metrics_response():
    try:
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
        return generate_latest(), CONTENT_TYPE_LATEST
    except Exception:
        return b"metrics unavailable", "text/plain"
