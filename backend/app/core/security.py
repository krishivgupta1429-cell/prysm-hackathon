"""
app/core/security.py
JWT token creation/verification, password hashing, and FastAPI auth dependencies.
"""

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.user import User

bearer_scheme = HTTPBearer()
security_logger = get_logger("security")

# Bcrypt cost factor — 12 rounds is ~250ms on modern hardware
_BCRYPT_ROUNDS = 12


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Hash password with bcrypt at explicit cost factor."""
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time password verification."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def validate_password_strength(password: str) -> str | None:
    """
    Returns an error message if password is weak, None if strong.
    Requirements: 8+ chars, at least one uppercase, one lowercase, one digit.
    """
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Z]", password):
        return "Password must contain at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return "Password must contain at least one lowercase letter."
    if not re.search(r"[0-9]", password):
        return "Password must contain at least one number."
    return None


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _get_jwt_secret() -> str:
    """Get JWT secret, refusing to use the default placeholder in production."""
    secret = settings.JWT_SECRET
    if settings.APP_ENV == "production" and secret == "jwt-secret-change-in-production":
        raise RuntimeError("JWT_SECRET must be changed in production!")
    return secret


def create_access_token(user_id: str, extra: Optional[dict] = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        "type": "access",
        "jti": uuid.uuid4().hex,
        **(extra or {}),
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
        "type": "refresh",
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            _get_jwt_secret(),
            algorithms=[settings.JWT_ALGORITHM],
            options={"require_exp": True, "require_sub": True},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ── FastAPI Dependencies ──────────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_token(credentials.credentials)

    # Only accept access tokens — reject refresh tokens used as access
    if payload.get("type") != "access":
        security_logger.warning("Refresh token used as access token", extra={
            "x_user_id": payload.get("sub", "unknown"),
        })
        raise HTTPException(status_code=401, detail="Invalid token type.")

    user_id: str = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing subject.")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive.")
    return user


async def get_current_user_optional(
    token: str,
    db: AsyncSession,
) -> Optional[User]:
    """Try to get current user from token — returns None if invalid (no exception)."""
    try:
        payload = jwt.decode(
            token, _get_jwt_secret(), algorithms=[settings.JWT_ALGORITHM],
        )
        if payload.get("type") != "access":
            return None
        user_id = payload.get("sub")
        if not user_id:
            return None
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        return user if user and user.is_active else None
    except Exception:
        return None


async def require_credits(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency that ensures the user has enough credits for an analysis."""
    if current_user.credits < settings.CREDITS_PER_ANALYSIS:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits. You have {round(current_user.credits, 1)} credits but need {settings.CREDITS_PER_ANALYSIS}.",
        )
    return current_user
