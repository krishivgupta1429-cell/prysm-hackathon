"""
app/models/analysis.py  – Analysis results model
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, Float, Integer, String, DateTime, ForeignKey, JSON, Text, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from enum import Enum as PyEnum
from app.core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class AnalysisStatus(str, PyEnum):
    QUEUED     = "queued"
    RUNNING    = "running"
    COMPLETE   = "complete"
    FAILED     = "failed"


class ViralityConfidence(str, PyEnum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


class Analysis(Base):
    __tablename__ = "analyses"

    id       = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id  = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    media_id = Column(UUID(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"), nullable=False, index=True, unique=True)

    status   = Column(Enum(AnalysisStatus), default=AnalysisStatus.QUEUED, nullable=False)
    error_message = Column(Text)

    # ── Hook Analysis ──────────────────────────────────────────────────────────
    hook_score           = Column(Float)          # 0-10
    hook_transcript      = Column(Text)           # first 3s text
    hook_retention_potential = Column(String(50)) # low | medium | high
    hook_suggestions     = Column(JSON)           # list[str]

    # ── Virality ───────────────────────────────────────────────────────────────
    virality_score       = Column(Integer)        # 0-100
    virality_confidence  = Column(Enum(ViralityConfidence))
    virality_reasons     = Column(JSON)           # list[str]

    # ── Content Breakdown ─────────────────────────────────────────────────────
    story_structure      = Column(JSON)           # {setup, conflict, resolution}
    value_density        = Column(Float)          # 0-10
    emotional_hooks      = Column(JSON)           # list[str]
    call_to_action       = Column(Text)

    # ── Trend Matching ────────────────────────────────────────────────────────
    trend_alignment      = Column(String(50))     # low | medium | high
    relevant_trends      = Column(JSON)           # list[str]
    missing_trends       = Column(JSON)           # list[str]
    niche_alignment      = Column(String(255))

    # ── Captions & Tags ───────────────────────────────────────────────────────
    captions             = Column(JSON)           # {instagram, tiktok, youtube}
    hashtags             = Column(JSON)           # {instagram, tiktok, youtube}
    titles               = Column(JSON)           # {instagram, tiktok, youtube}

    # ── Retention / Improvements ──────────────────────────────────────────────
    pacing_score         = Column(Float)          # 0-10
    improvements         = Column(JSON)           # list[{type, timestamp, suggestion}]

    # ── Thumbnail Suggestions ─────────────────────────────────────────────────
    thumbnail_suggestions = Column(JSON)          # list[{timestamp, overlay_text, reason}]

    # ── Benchmark ─────────────────────────────────────────────────────────────
    benchmark            = Column(JSON)           # vs creator avg & niche avg

    # ── Scene / Segment Data ──────────────────────────────────────────────────
    scenes               = Column(JSON)           # list[{start, end, description}]
    segments             = Column(JSON)           # list[{start, end, text, type}]

    credits_used         = Column(Integer, default=10)
    processing_time_sec  = Column(Float)

    created_at  = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at  = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user  = relationship("User",  back_populates="analyses")
    media = relationship("Media", back_populates="analysis")

    def to_response_dict(self) -> dict:
        return {
            "video_id":              str(self.media_id),
            "analysis_id":           str(self.id),
            "status":                self.status,
            "hook_score":            self.hook_score,
            "hook_retention":        self.hook_retention_potential,
            "hook_suggestions":      self.hook_suggestions,
            "virality_score":        self.virality_score,
            "virality_confidence":   self.virality_confidence,
            "virality_reasons":      self.virality_reasons,
            "story_structure":       self.story_structure,
            "value_density":         self.value_density,
            "emotional_hooks":       self.emotional_hooks,
            "call_to_action":        self.call_to_action,
            "trend_alignment":       self.trend_alignment,
            "relevant_trends":       self.relevant_trends,
            "missing_trends":        self.missing_trends,
            "niche_alignment":       self.niche_alignment,
            "captions":              self.captions,
            "hashtags":              self.hashtags,
            "titles":                self.titles,
            "pacing_score":          self.pacing_score,
            "improvements":          self.improvements,
            "thumbnail_suggestions": self.thumbnail_suggestions,
            "benchmark":             self.benchmark,
            "scenes":                self.scenes,
            "segments":              self.segments,
            "credits_used":          self.credits_used,
            "processing_time_sec":   self.processing_time_sec,
            "created_at":            self.created_at.isoformat() if self.created_at else None,
        }
