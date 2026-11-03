"""
app/models/research_reel.py

Storage model for the Prysm niche-research reel dataset.
Covers both historical batches (batch1_5) and new batches (batch6+).
Never touched by customer-facing code — research pipeline only.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, Date, DateTime, Float, Index, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


def _uuid():
    return str(uuid.uuid4())


class ResearchReel(Base):
    __tablename__ = "research_reels"

    # ── Identity ──────────────────────────────────────────────────────────────
    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instagram_url   = Column(String(512), nullable=False, unique=True)
    batch_source    = Column(String(32), nullable=False, index=True)   # "batch1_5" | "batch6"
    pipeline_version = Column(String(128), nullable=False)

    # ── Creator / niche ───────────────────────────────────────────────────────
    niche           = Column(String(64), nullable=True, index=True)
    creator_handle  = Column(String(128), nullable=True, index=True)
    follower_count  = Column(Integer, nullable=True)
    follower_tier   = Column(String(16), nullable=True, index=True)
    # nano <10k | micro 10k-50k | mid 50k-150k | macro 150k-500k | mega 500k+
    region          = Column(String(32), nullable=True, index=True)
    content_subformat = Column(String(128), nullable=True)

    # ── Raw engagement metrics ────────────────────────────────────────────────
    view_count      = Column(Integer, nullable=True)
    like_count      = Column(Integer, nullable=True)
    comment_count   = Column(Integer, nullable=True)
    upload_date     = Column(Date, nullable=True)
    duration_sec    = Column(Float, nullable=True)

    # ── Derived engagement features ───────────────────────────────────────────
    days_since_upload    = Column(Integer, nullable=True)
    log_relative_views   = Column(Float, nullable=True)  # log2(vc / creator_median)
    views_per_follower   = Column(Float, nullable=True)
    engagement_rate      = Column(Float, nullable=True)  # (likes + comments) / views

    # ── Scored parameters (6) ─────────────────────────────────────────────────
    score_hook          = Column(Float, nullable=True)   # Hook & Opening
    score_retention     = Column(Float, nullable=True)   # Retention & Pacing
    score_audio         = Column(Float, nullable=True)   # Audio & Music
    score_visual        = Column(Float, nullable=True)   # Visual Quality & Originality
    score_trend         = Column(Float, nullable=True)   # Trend Alignment
    score_shareability  = Column(Float, nullable=True)   # Shareability

    # ── Advisory parameters (4) stored as JSONB ──────────────────────────────
    advisory_json   = Column(JSONB, nullable=True)
    # {"On-Screen Text": 70, "Save Potential": 60, "Replay Value": 55, "Call to Action": 60}

    # ── Raw model output ──────────────────────────────────────────────────────
    raw_pipeline_json = Column(JSONB, nullable=True)

    # ── R2 artifact pointers (batch6+) ────────────────────────────────────────
    r2_frames_prefix = Column(String(512), nullable=True)
    r2_audio_key     = Column(String(512), nullable=True)

    # ── Pipeline health ───────────────────────────────────────────────────────
    error           = Column(Text, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at      = Column(DateTime, nullable=False, server_default=func.now())

    # ── Table-level constraints and indexes ───────────────────────────────────
    __table_args__ = (
        UniqueConstraint("instagram_url", name="uq_research_reels_url"),
        Index("ix_research_reels_niche_creator", "niche", "creator_handle"),
    )
