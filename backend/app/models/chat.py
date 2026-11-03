"""
app/models/chat.py  – ARIA chat session & message models
app/models/outcomes.py – CreatorProfile (memory & benchmarks)
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, JSON, Text, Float, Integer, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id      = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title        = Column(String(255), default="New Chat")
    is_active    = Column(Boolean, default=True)
    context      = Column(JSON)           # optional attached analysis / video ids
    session_type = Column(String(20), default="general", nullable=False)  # "analysis" | "general"
    analysis_id  = Column(UUID(as_uuid=True), ForeignKey("prysm_analyses.id", ondelete="SET NULL"), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user     = relationship("User",        back_populates="chat_sessions")
    messages = relationship("ChatMessage", back_populates="session",
                            cascade="all, delete-orphan", order_by="ChatMessage.created_at")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role       = Column(String(20), nullable=False)   # user | assistant | system
    content    = Column(Text, nullable=False)
    tokens_used = Column(Integer)
    cost_usd   = Column(Float, nullable=True)          # actual API cost for this message
    extra_data = Column(JSON)                          # tool_calls, citations, etc.

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    session = relationship("ChatSession", back_populates="messages")


# ── Creator Profile ───────────────────────────────────────────────────────────

class CreatorProfile(Base):
    """
    Aggregated intelligence about a creator built over time.
    Acts as the 'memory' layer for personalised recommendations.
    """
    __tablename__ = "creator_profiles"

    id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, unique=True, index=True)

    # ── Aggregate metrics ──────────────────────────────────────────────────────
    total_videos_analyzed    = Column(Integer, default=0)
    avg_virality_score       = Column(Float)
    avg_hook_score           = Column(Float)
    avg_value_density        = Column(Float)
    avg_pacing_score         = Column(Float)

    # ── Trend / niche data ────────────────────────────────────────────────────
    primary_niche            = Column(String(255))
    secondary_niches         = Column(JSON)           # list[str]
    top_performing_topics    = Column(JSON)           # list[{topic, avg_virality}]
    best_performing_hooks    = Column(JSON)           # list[str]
    recurring_ctas           = Column(JSON)           # list[str]

    # ── Time-series snapshots (last N analyses) ───────────────────────────────
    virality_trend           = Column(JSON)           # list[{date, score}]
    hook_score_trend         = Column(JSON)           # list[{date, score}]

    # ── Platform performance ──────────────────────────────────────────────────
    platform_performance     = Column(JSON)           # {instagram: {}, tiktok: {}, youtube: {}}

    # ── Vector embedding id (pgvector / Pinecone) ────────────────────────────
    embedding_id             = Column(String(255))

    # ── Personalization context ───────────────────────────────────────────────
    content_style_summary    = Column(Text)           # AI-generated style description
    recommendations          = Column(JSON)           # latest AI recs for the creator

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="creator_profile")
