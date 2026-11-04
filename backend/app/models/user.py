"""
app/models/user.py
User, Subscription, and credit tracking models.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from sqlalchemy import (
    Column, String, Boolean, Integer, DateTime,
    ForeignKey, Enum, Text, Float, JSON, BigInteger
)
from app.studio.instagram.tokencrypt import EncryptedToken  # hackathon: encrypted Instagram token column
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class PlanTier(str, PyEnum):
    FREE    = "free"
    PRO     = "pro"
    PLUS    = "plus"
    CREATOR = "creator"


class User(Base):
    __tablename__ = "users"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email       = Column(String(255), unique=True, nullable=False, index=True)
    username    = Column(String(100), unique=True, nullable=False, index=True)
    hashed_pw   = Column(String(255), nullable=False)
    full_name   = Column(String(255))
    avatar_url  = Column(String(512))
    is_active   = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    credits     = Column(Float, default=20.0, nullable=False)
    plan        = Column(Enum(PlanTier), default=PlanTier.FREE, nullable=False)

    # Razorpay
    razorpay_customer_id     = Column(String(100))
    razorpay_subscription_id = Column(String(100))
    billing_cycle            = Column(String(10), default="monthly")  # "monthly" or "annual"
    plan_started_at          = Column(DateTime(timezone=True))
    plan_expires_at          = Column(DateTime(timezone=True))

    # Legacy Stripe (kept for backwards compat)
    stripe_customer_id    = Column(String(100))
    stripe_subscription_id = Column(String(100))

    # Credit rollover tracking
    rollover_credits = Column(Float, default=0.0, nullable=False)

    auth_provider = Column(String(20), default="email", nullable=False)  # "email" or "google"

    # Instagram OAuth
    instagram_access_token = Column(EncryptedToken, nullable=True)  # hackathon: Fernet-encrypted at rest (TOKEN_ENCRYPTION_KEY)
    instagram_user_id      = Column(String(50), nullable=True)
    instagram_username     = Column(String(100), nullable=True)

    created_at  = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at  = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Relationships
    media        = relationship("Media",        back_populates="user", cascade="all, delete-orphan")
    analyses     = relationship("Analysis",     back_populates="user", cascade="all, delete-orphan")
    chat_sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")
    creator_profile = relationship("CreatorProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.email} plan={self.plan} credits={self.credits}>"
