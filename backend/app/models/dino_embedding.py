"""
app/models/dino_embedding.py
Built during the Meta Global AI Developer Hackathon (new work). DINOv2 visual embeddings (facebook/dinov2-base CLS
vectors, 768-d, L2-normalised mean over a reel's frames).

New tables only. reel_visual_embedding joins the Instagram reel tables by the media id STRING at query time and has no
foreign key into them (those tables live on another branch with their own lifecycle).
"""
import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base

DIM = 768


def _now():
    return datetime.now(timezone.utc)


class ReelVisualEmbedding(Base):
    __tablename__ = "reel_visual_embedding"
    __table_args__ = (UniqueConstraint("user_id", "media_id", name="uq_reel_visual_embedding_user_media"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    media_id = Column(String(60), nullable=False)          # the Instagram media id, as a string (no foreign key)
    mean_vector = Column(Vector(DIM), nullable=False)      # L2-normalised mean of the frame vectors
    frame_count = Column(Integer, nullable=False)          # how many frame vectors went into the mean
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)


class AnalysisVisualEmbedding(Base):
    __tablename__ = "analysis_visual_embedding"

    analysis_id = Column(UUID(as_uuid=True), ForeignKey("prysm_analyses.id", ondelete="CASCADE"), primary_key=True)
    mean_vector = Column(Vector(DIM), nullable=False)
    frame_count = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
