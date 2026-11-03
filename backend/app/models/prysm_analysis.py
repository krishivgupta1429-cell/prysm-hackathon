"""
app/models/prysm_analysis.py
Stores Prysm 8-parameter analysis results + performance feedback for the prediction model.
"""

import secrets
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Text, DateTime, ForeignKey, JSON, Boolean,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from app.core.database import Base


class PrysmAnalysis(Base):
    """Stores each Prysm analysis result with the 8 parameters."""
    __tablename__ = "prysm_analyses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    # Video source
    source_type = Column(String(20), default="upload")  # "upload" or "link"
    source_url = Column(Text, nullable=True)  # Instagram URL if link
    video_filename = Column(String(512), nullable=True)

    # User inputs
    niche = Column(String(255), default="General")
    platform = Column(String(100), default="Instagram Reels")
    brief = Column(Text, default="")
    content_goal = Column(String(100), default="")
    intent = Column(String(20), default="my_video")  # "my_video" or "competitor"

    # Instagram metadata (scraped from post)
    post_caption = Column(Text, nullable=True)
    post_hashtags = Column(JSON, nullable=True)  # list[str]
    post_likes = Column(Integer, nullable=True)
    post_comments = Column(Integer, nullable=True)
    post_views = Column(Integer, nullable=True)
    post_uploader = Column(String(255), nullable=True)

    # Overall result
    overall_score = Column(Integer, default=0)
    score_label = Column(String(100), default="")
    summary = Column(Text, default="")
    top_priorities = Column(JSON, nullable=True)  # list[str]

    # 8 parameters stored as JSON array
    parameters = Column(JSON, nullable=True)  # list[{name, weight, score, whatIsHappening, ...}]

    # Frames saved for reference
    frames = Column(JSON, nullable=True)  # list[{timestamp, base64}]
    first_frame_b64 = Column(Text, nullable=True)  # thumbnail: base64 of first frame

    # Full raw result (for backwards compat)
    raw_result = Column(JSON, nullable=True)

    # Cost tracking (dedicated column for easy querying)
    cost_breakdown = Column(JSON, nullable=True)   # {items: [...], total_usd: 0.28}
    total_cost_usd = Column(Float, nullable=True)  # e.g. 0.2825

    # Processing metadata
    processing_time_sec = Column(Float, nullable=True)
    transcript = Column(Text, nullable=True)
    audio_analysis = Column(JSON, nullable=True)

    # Dense embeddings for ANN search / duplicate detection.
    # Dimensions: visual=768 (DINOv2-base), motion=1024 (V-JEPA2-ViT-L),
    #             audio=512 (CLAP htsat-fused), joint=768 (CLIP-ViT-L/14)
    visual_embedding = Column(Vector(768),  nullable=True)
    motion_embedding = Column(Vector(1024), nullable=True)
    audio_embedding  = Column(Vector(512),  nullable=True)
    joint_embedding  = Column(Vector(768),  nullable=True)

    # Auto-edited video (R2 object key — generate pre-signed URL on demand)
    edited_video_key = Column(String(512), nullable=True)
    edit_plan = Column(JSON, nullable=True)  # {applied_params, skipped_params, operations}

    # Sharing
    share_id = Column(String(32), unique=True, nullable=True, index=True, default=lambda: secrets.token_urlsafe(16))
    is_public = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", backref="prysm_analyses")
    feedback = relationship("PerformanceFeedback", back_populates="analysis", uselist=False, cascade="all, delete-orphan")


class PerformanceFeedback(Base):
    """
    User-reported actual performance — the feedback loop for the prediction model.
    Users report how their video performed after posting.
    """
    __tablename__ = "performance_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_id = Column(UUID(as_uuid=True), ForeignKey("prysm_analyses.id", ondelete="CASCADE"), unique=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    # Actual performance metrics (user-reported)
    actual_views = Column(Integer, nullable=True)
    actual_likes = Column(Integer, nullable=True)
    actual_comments = Column(Integer, nullable=True)
    actual_saves = Column(Integer, nullable=True)
    actual_shares = Column(Integer, nullable=True)
    actual_reach = Column(Integer, nullable=True)

    # When was this data reported (hours after posting)
    hours_after_posting = Column(Integer, default=48)

    # Did the user implement our suggestions?
    implemented_suggestions = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)

    reported_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    analysis = relationship("PrysmAnalysis", back_populates="feedback")
    user = relationship("User", backref="performance_feedback")
