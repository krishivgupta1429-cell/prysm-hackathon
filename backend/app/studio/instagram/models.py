"""Built during the Meta Global AI Developer Hackathon (new work).

Tables of the Instagram / Creator DNA feature. New tables only; no baseline table is altered. The access token itself stays
where the baseline stored it (users.instagram_access_token); everything here is derived data and is deleted on disconnect.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class IgAccount(Base):
    __tablename__ = "ig_account"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    ig_user_id = Column(String(50), nullable=False)
    username = Column(String(100))
    profile_picture_url = Column(Text)                # a link Instagram serves (may expire); no image is stored
    account_type = Column(String(30))                 # BUSINESS / MEDIA_CREATOR when Instagram says so
    is_fixture = Column(Boolean, nullable=False, default=False)   # dev-only fake data: must never pass for real
    scopes = Column(String(300))
    token_expires_at = Column(DateTime(timezone=True))
    token_refreshed_at = Column(DateTime(timezone=True))
    connected_at = Column(DateTime(timezone=True), default=utcnow)
    last_import_at = Column(DateTime(timezone=True))
    last_import_requested = Column(Integer)
    last_import_summary = Column(JSON)                # {"imported": n, "missing_metrics": [...], "rate_limited": bool, ...}


class IgReel(Base):
    __tablename__ = "ig_reel"
    __table_args__ = (UniqueConstraint("account_id", "media_id", name="uq_ig_reel_account_media"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("ig_account.id", ondelete="CASCADE"), nullable=False, index=True)
    media_id = Column(String(60), nullable=False)
    permalink = Column(Text)
    thumbnail_url = Column(Text)                      # a link Instagram serves (may expire); no image is stored
    posted_at = Column(DateTime(timezone=True))
    caption = Column(Text)
    duration_s = Column(Float)                        # measured from the downloaded file; NULL when the video was not available
    like_count = Column(Integer)
    comments_count = Column(Integer)
    analysis_level = Column(String(20))               # full (video frames) | thumbnail_caption | caption_only (no video link from Instagram)
    frames_used = Column(Integer, default=0)          # frames sent to the model for the fingerprint (0 = caption only)
    performance_index = Column(Float)                 # reach (else views) / account median across the imported reels
    performance_basis = Column(String(20))            # "reach" or "views"
    watch_through = Column(Float)                     # avg watch time / duration, 0..1+, NULL when either is unknown
    fingerprint = Column(JSON)                        # {"hook_type", "format", "pacing", "audio_type", "on_screen_text", "topic"}
    note = Column(Text)                               # why something is missing, in plain words


class IgReelInsight(Base):
    __tablename__ = "ig_reel_insight"
    __table_args__ = (UniqueConstraint("reel_id", "metric", name="uq_ig_reel_insight_metric"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reel_id = Column(UUID(as_uuid=True), ForeignKey("ig_reel.id", ondelete="CASCADE"), nullable=False, index=True)
    metric = Column(String(60), nullable=False)
    value = Column(Float)                             # NULL = Instagram returned nothing (not zero)
    available = Column(Boolean, nullable=False, default=False)
    fetched_at = Column(DateTime(timezone=True), default=utcnow)


class CreatorDna(Base):
    __tablename__ = "creator_dna"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("ig_account.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    dna = Column(JSON)                                # structured DNA (keep / change / shared traits, each with cited reels)
    summary = Column(Text)                            # plain-language summary
    sample_size = Column(Integer)
    metrics_available = Column(JSON)                  # {"reach": 9, "ig_reels_avg_watch_time": 0, ...} count of reels with the metric
    medians = Column(JSON)                            # account medians and measured averages used on the results page
    model = Column(String(60))
    cost_usd = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), default=utcnow)
