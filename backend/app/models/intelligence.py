"""
app/models/intelligence.py
New ORM models for v2 features:
  • ViralPattern       – database of known viral video traits
  • CreatorIntelligence – scraped handle-based profile
  • CompetitorAnalysis  – competitor account reports
  • ClipJob            – async clip generation job
  • TimelineFeedback   – per-video timestamp-level feedback
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from sqlalchemy import (
    Column, String, Integer, Float, DateTime, ForeignKey,
    Boolean, Text, JSON, BigInteger, Enum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


# ── Viral Pattern Engine ──────────────────────────────────────────────────────

class HookType(str, PyEnum):
    QUESTION     = "question"
    BOLD_CLAIM   = "bold_claim"
    STORY        = "story"
    CONTRAST     = "contrast"
    CONTROVERSY  = "controversy"
    HOW_TO       = "how_to"
    LIST         = "list"
    SHOCK        = "shock"


class EmotionalTone(str, PyEnum):
    INSPIRING    = "inspiring"
    FUNNY        = "funny"
    SHOCKING     = "shocking"
    EDUCATIONAL  = "educational"
    RELATABLE    = "relatable"
    CONTROVERSIAL = "controversial"
    HEARTWARMING = "heartwarming"
    ANGRY        = "angry"


class ViralPattern(Base):
    """
    Each row captures measured characteristics of a video that went viral.
    The Viral Pattern Engine queries this table to improve predictions.
    """
    __tablename__ = "viral_patterns"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_id          = Column(UUID(as_uuid=True), ForeignKey("analyses.id", ondelete="SET NULL"), nullable=True)

    # Video traits
    hook_type            = Column(Enum(HookType))
    hook_duration_sec    = Column(Float)           # seconds before main content
    video_duration_sec   = Column(Float)
    emotional_tone       = Column(Enum(EmotionalTone))
    topic_category       = Column(String(100))
    scene_change_rate    = Column(Float)           # changes per second
    speech_pace_wpm      = Column(Integer)         # words per minute
    has_captions         = Column(Boolean)
    has_music            = Column(Boolean)
    has_face             = Column(Boolean)
    face_emotion         = Column(String(50))
    platform             = Column(String(30))      # instagram | tiktok | youtube

    # Outcome metrics
    virality_score       = Column(Integer)         # 0-100
    engagement_ratio     = Column(Float)           # likes+comments / views
    share_rate           = Column(Float)           # shares / views
    completion_rate      = Column(Float)           # avg watch time / duration

    # Feature vector (for ML, stored as JSON list[float])
    feature_vector       = Column(JSON)

    source               = Column(String(50))      # user_upload | scraped | imported
    created_at           = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# ── Creator Handle Intelligence ───────────────────────────────────────────────

class ScrapingStatus(str, PyEnum):
    PENDING    = "pending"
    RUNNING    = "running"
    COMPLETE   = "complete"
    FAILED     = "failed"


class CreatorIntelligence(Base):
    """Scraped profile analysis for a social handle."""
    __tablename__ = "creator_intelligence"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id        = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    handle         = Column(String(100), nullable=False)   # @username
    platform       = Column(String(30), nullable=False)    # instagram | tiktok | youtube
    status         = Column(Enum(ScrapingStatus), default=ScrapingStatus.PENDING)
    error_message  = Column(Text)

    # Raw scrape data
    posts_scraped  = Column(Integer, default=0)
    raw_posts      = Column(JSON)                          # list of post metadata

    # Derived insights
    best_posting_times     = Column(JSON)    # [{day, hour, avg_engagement}]
    best_video_length_sec  = Column(Float)
    top_hooks              = Column(JSON)    # list[str]
    topic_clusters         = Column(JSON)    # [{cluster, keywords, avg_virality}]
    engagement_patterns    = Column(JSON)    # {by_day, by_hour, by_length}
    avg_virality_score     = Column(Float)
    avg_engagement_rate    = Column(Float)
    follower_count         = Column(BigInteger)
    posting_frequency_days = Column(Float)   # avg days between posts
    niche_summary          = Column(Text)    # AI-generated niche description
    strategic_insights     = Column(JSON)    # list[str]

    celery_task_id = Column(String(255))
    created_at     = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at     = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User")


# ── Competitor Analysis ───────────────────────────────────────────────────────

class CompetitorAnalysis(Base):
    __tablename__ = "competitor_analyses"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    competitor_handle = Column(String(100), nullable=False)
    platform          = Column(String(30), nullable=False)
    status            = Column(Enum(ScrapingStatus), default=ScrapingStatus.PENDING)
    error_message     = Column(Text)

    # Results
    top_performing_content = Column(JSON)   # list[{title, hook, virality_score, engagement}]
    viral_patterns         = Column(JSON)   # {hook_types, durations, tones, ...}
    engagement_strategies  = Column(JSON)   # list[{strategy, frequency, effectiveness}]
    posting_schedule       = Column(JSON)   # {best_days, best_hours}
    content_pillars        = Column(JSON)   # list[str]
    gap_opportunities      = Column(JSON)   # topics competitor misses
    threat_level           = Column(String(20))   # low | medium | high
    strategic_recommendations = Column(JSON)

    celery_task_id = Column(String(255))
    created_at     = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at     = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User")


# ── Clip Generation Job ───────────────────────────────────────────────────────

class ClipStatus(str, PyEnum):
    QUEUED     = "queued"
    PROCESSING = "processing"
    COMPLETE   = "complete"
    FAILED     = "failed"


class ClipJob(Base):
    __tablename__ = "clip_jobs"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id        = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    media_id       = Column(UUID(as_uuid=True), ForeignKey("media.id",  ondelete="CASCADE"), nullable=False)
    status         = Column(Enum(ClipStatus), default=ClipStatus.QUEUED)
    error_message  = Column(Text)

    # Configuration
    max_clips      = Column(Integer, default=5)
    min_clip_sec   = Column(Float, default=15.0)
    max_clip_sec   = Column(Float, default=60.0)
    add_captions   = Column(Boolean, default=True)
    caption_style  = Column(String(50), default="tiktok")  # tiktok | minimal | bold

    # Results
    clips          = Column(JSON)    # list[{start, end, score, storage_url, caption}]
    total_clips    = Column(Integer, default=0)

    celery_task_id = Column(String(255))
    created_at     = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at     = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user  = relationship("User")
    media = relationship("Media")


# ── Trending Audio ────────────────────────────────────────────────────────────

class TrendingAudio(Base):
    """
    Weekly-scraped trending audio data per niche + platform.
    Written by the weekly cron scraper; read at analysis time to give Claude
    real, current audio and hashtag recommendations instead of static guesses.
    """
    __tablename__ = "trending_audio"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    niche         = Column(String(100), nullable=False, index=True)
    platform      = Column(String(30), nullable=False, index=True)   # instagram | tiktok | youtube

    # Audio trends — ranked by usage count across scraped posts
    audio_title   = Column(String(255), nullable=False)
    artist_name   = Column(String(255))
    usage_count   = Column(Integer, default=0)    # times seen in top posts this week
    avg_likes     = Column(Float, default=0.0)    # avg likes on posts using this audio
    genre         = Column(String(100))
    mood          = Column(String(100))           # upbeat, calm, hype, etc.

    # Hashtags seen alongside this audio in top posts
    co_hashtags   = Column(JSON)                  # list[str] — hashtags frequently paired

    # Weekly snapshot — what week was this scraped?
    week_start    = Column(DateTime(timezone=True), nullable=False, index=True)

    # Raw Apify post sample for this audio (up to 3 posts)
    sample_posts  = Column(JSON)                  # [{likes, comments, caption_preview}]

    created_at    = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class NicheHashtag(Base):
    """
    Weekly-scraped hashtag frequency table per niche.
    Stores the top 30 hashtags per niche per week, ranked by frequency across viral posts.
    Gives Claude real hashtags to recommend instead of generic ones.
    """
    __tablename__ = "niche_hashtags"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    niche         = Column(String(100), nullable=False, index=True)
    platform      = Column(String(30), nullable=False, index=True)
    hashtag       = Column(String(150), nullable=False)
    frequency     = Column(Integer, default=0)    # how many viral posts used this hashtag
    avg_likes     = Column(Float, default=0.0)    # avg likes on posts using this hashtag
    week_start    = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at    = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class NicheFormat(Base):
    """
    Weekly-scraped format trend data extracted from top creator profiles per niche.
    Each row is one observed format pattern (e.g. "POV", "GRWM", "transformation").
    Scraped from high-performing posts on top creator accounts — NOT hashtag posts.
    This gives Claude real, viral format signals instead of generic hashtag data.
    """
    __tablename__ = "niche_formats"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    niche          = Column(String(100), nullable=False, index=True)
    platform       = Column(String(30), nullable=False, index=True)

    # Format signal
    format_label   = Column(String(100), nullable=False)   # e.g. "POV", "GRWM", "Day in my life"
    format_type    = Column(String(50))                    # "hook_style" | "content_structure" | "caption_style"
    frequency      = Column(Integer, default=0)            # posts using this format this week
    avg_likes      = Column(Float, default=0.0)            # avg likes on posts using this format
    avg_comments   = Column(Float, default=0.0)
    example_hooks  = Column(JSON)                          # list[str] — real hook lines from top posts
    source_creators = Column(JSON)                         # list[str] — which creator handles this came from

    # Weekly snapshot
    week_start     = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at     = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# ── Timeline Feedback ─────────────────────────────────────────────────────────

class TimelineFeedback(Base):
    __tablename__ = "timeline_feedback"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_id = Column(UUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, unique=True)
    user_id     = Column(UUID(as_uuid=True), ForeignKey("users.id",    ondelete="CASCADE"), nullable=False)

    # Ordered list of timestamped feedback events
    events      = Column(JSON, nullable=False)
    # Format: [{"timestamp": 1.4, "label": "weak hook", "type": "warning",
    #            "suggestion": "Lead with a question instead", "score_impact": -8}]

    summary     = Column(Text)    # AI-generated 2-sentence summary
    created_at  = Column(DateTime(timezone=True), default=utcnow, nullable=False)
