"""
app/models/media.py  – Media (video) model
"""
import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Enum, BigInteger, Float, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class MediaSource(str, PyEnum):
    UPLOAD    = "upload"
    INSTAGRAM = "instagram"
    TIKTOK    = "tiktok"
    YOUTUBE   = "youtube"


class MediaStatus(str, PyEnum):
    PENDING    = "pending"
    PROCESSING = "processing"
    READY      = "ready"
    FAILED     = "failed"


class Media(Base):
    __tablename__ = "media"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id      = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    title        = Column(String(512))
    original_url = Column(Text)                   # source URL if downloaded
    storage_path = Column(Text)                   # S3 key or local path
    storage_url  = Column(Text)                   # public URL / CDN
    thumbnail_url = Column(Text)

    source       = Column(Enum(MediaSource), default=MediaSource.UPLOAD, nullable=False)
    status       = Column(Enum(MediaStatus), default=MediaStatus.PENDING, nullable=False)
    duration_sec = Column(Float)
    file_size_bytes = Column(BigInteger)
    mime_type    = Column(String(100))
    width        = Column(Integer)
    height       = Column(Integer)
    fps          = Column(Float)

    transcript   = Column(Text)                   # raw Whisper transcript
    audio_path   = Column(Text)

    celery_task_id = Column(String(255))          # background job id

    created_at   = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at   = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user     = relationship("User",     back_populates="media")
    analysis = relationship("Analysis", back_populates="media", uselist=False, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Media {self.id} source={self.source} status={self.status}>"
