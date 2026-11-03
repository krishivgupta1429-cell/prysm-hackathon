"""
app/routers/auth.py
Authentication endpoints: register, login, token refresh, profile.
"""

import asyncio
import httpx
import random
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import (
    hash_password, verify_password, validate_password_strength,
    create_access_token, create_refresh_token,
    decode_token, get_current_user,
)
from app.models.user import User, PlanTier
from app.core.config import settings
from app.core.logging import get_logger
from app.core.cache import redis_client, CacheKeys
from app.services.email_service import send_verification_email, send_password_reset_email

router = APIRouter()
auth_logger = get_logger("auth")


# ── Schemas ───────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    username: str
    password: str
    full_name: str | None = None

    @field_validator("password")
    @classmethod
    def strong_password(cls, v):
        err = validate_password_strength(v)
        if err:
            raise ValueError(err)
        return v

    @field_validator("username")
    @classmethod
    def valid_username(cls, v):
        if not all(c.isalnum() or c == "_" for c in v):
            raise ValueError("Username may only contain letters, numbers, and underscores.")
        if len(v) < 3 or len(v) > 30:
            raise ValueError("Username must be between 3 and 30 characters.")
        return v.lower()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60


class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    full_name: str | None
    plan: str
    credits: float
    is_verified: bool
    auth_provider: str
    created_at: str
    instagram_user_id: str | None = None
    instagram_username: str | None = None


class VerifyEmailRequest(BaseModel):
    token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def strong_password(cls, v):
        err = validate_password_strength(v)
        if err:
            raise ValueError(err)
        return v


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class DeleteAccountRequest(BaseModel):
    password: str | None = None


class MessageResponse(BaseModel):
    message: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

def _normalize_email(email: str) -> str:
    """Normalize email to prevent alias-based account farming.
    Strips Gmail/Googlemail + aliases and dots: john+spam@gmail.com → john@gmail.com
    """
    local, domain = email.lower().split("@", 1)
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+")[0]      # strip +alias
        local = local.replace(".", "")   # strip dots (Gmail ignores them)
    return f"{local}@{domain}"


# Common disposable/temporary email domains — blocks throwaway account farming
_DISPOSABLE_DOMAINS: frozenset[str] = frozenset({
    "mailinator.com", "guerrillamail.com", "guerrillamail.info", "guerrillamail.biz",
    "guerrillamail.de", "guerrillamail.net", "guerrillamail.org", "grr.la",
    "sharklasers.com", "guerrillamailblock.com", "spam4.me", "trashmail.com",
    "trashmail.me", "trashmail.net", "trashmail.org", "trashmail.at",
    "trashmail.io", "trashmail.xyz", "tempmail.com", "tempmail.net",
    "temp-mail.org", "temp-mail.io", "temp-mail.ru", "dispostable.com",
    "yopmail.com", "yopmail.fr", "cool.fr.nf", "jetable.fr.nf", "nospam.ze.tc",
    "nomail.xl.cx", "mega.zik.dj", "speed.1s.fr", "courriel.fr.nf",
    "moncourrier.fr.nf", "monemail.fr.nf", "monmail.fr.nf",
    "throwam.com", "throwam.net", "throwam.org", "throwaway.email",
    "fakeinbox.com", "mailnull.com", "spamgourmet.com", "spamgourmet.net",
    "spamgourmet.org", "maildrop.cc", "mailnesia.com", "mailnull.com",
    "spamherelots.com", "spamhereplease.com", "spamthisplease.com",
    "spamfree24.org", "spamfree.eu", "spam.la", "spamoff.de",
    "despam.it", "antispam24.de", "antispammail.de",
    "discard.email", "discardmail.com", "discardmail.de",
    "throwam.com", "spamevader.com", "spamgob.com",
    "10minutemail.com", "10minutemail.net", "10minutemail.org",
    "10minemail.com", "20minutemail.com", "60minutemail.com",
    "minuteemails.com", "tempinbox.com", "tempinbox.co.uk",
    "33mail.com", "filzmail.com", "emlpro.com", "emlhub.com",
    "getairmail.com", "jetable.com", "jetable.net", "jetable.org",
    "nwldx.com", "spambox.us", "spambox.info", "spambox.irishspringrealty.com",
    "e4ward.com", "myfastmail.com", "spamgob.com", "spamgoes.in",
    "spamspot.com", "deadaddress.com", "sneakemail.com",
    "mailexpire.com", "spamify.com", "inoutmail.eu", "inoutmail.net",
    "inoutmail.info", "inoutmail.de", "dingbone.com", "fudgerub.com",
    "lookugly.com", "smellfear.com", "mohmal.com", "tempr.email",
    "discard.email", "spamwc.de", "spamwc.com", "spamwc.org",
    "spamwc.net", "haltospam.com", "spamobox.com", "bspamfree.org",
    "meltmail.com", "gishpuppy.com", "uggsrock.com", "emailsensei.com",
    "tempomail.fr", "mytempemail.com", "spamfighter.com", "spamfighter.net",
    "spamfighter.org", "spamfighter.de", "spamfighter.ru",
    "maildrop.cc", "spamgob.com", "easytrashmail.com",
    "trashmail.at", "trashmail.com", "trashmail.io", "trashmail.me",
    "trashmail.net", "trashmail.org", "trashmail.xyz",
    "wegwerfmail.de", "wegwerfmail.net", "wegwerfmail.org",
    "spamgob.com", "mailnull.com", "spamevader.com",
    "cuvox.de", "dayrep.com", "einrot.com", "fleckens.hu",
    "gustr.com", "jourrapide.com", "rhyta.com", "superrito.com",
    "teleworm.us", "armyspy.com", "cuvox.de", "dayrep.com",
    "einrot.com", "fleckens.hu", "gustr.com", "jourrapide.com",
    "spamgob.com", "fakemailgenerator.com", "spamgob.net",
    "getonemail.com", "filzmail.com", "emlhub.com",
})


def _is_disposable_email(email: str) -> bool:
    domain = email.lower().split("@", 1)[-1]
    return domain in _DISPOSABLE_DOMAINS


@router.post("/register", status_code=201)
async def register(body: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip = _get_ip(request)

    if settings.META_MODE:
        from app.studio import safety
        from app.studio.safety import limits_from_settings
        try:
            await safety.check_register(redis_client, ip, limits_from_settings(settings))
        except safety.LimitExceeded as limit:
            raise HTTPException(status_code=limit.status_code, detail=limit.message)
        except Exception as limit_err:  # Redis trouble must not block sign-ups
            auth_logger.warning("Sign-up limit check failed", extra={"error": str(limit_err)})

    # Block disposable/temp email domains
    if _is_disposable_email(body.email):
        raise HTTPException(status_code=400, detail="Please use a permanent email address to register.")

    # Normalize email to prevent alias-based account farming
    normalized_email = _normalize_email(body.email)

    # Check uniqueness against normalized email
    existing = await db.execute(
        select(User).where((User.email == normalized_email) | (User.username == body.username))
    )
    if existing.scalar_one_or_none():
        auth_logger.warning("Registration failed — duplicate", extra={
            "x_ip": ip, "x_email": body.email, "x_reason": "duplicate",
        })
        raise HTTPException(status_code=409, detail="Email or username already registered.")

    user = User(
        email=normalized_email,
        username=body.username,
        hashed_pw=hash_password(body.password),
        full_name=body.full_name,
        credits=settings.FREE_TIER_CREDITS,
        plan=PlanTier.FREE,
    )
    db.add(user)
    await db.flush()

    auth_logger.info("User registered", extra={
        "x_ip": ip, "x_user_id": str(user.id), "x_email": body.email,
    })

    # Send verification email if Brevo is configured, otherwise auto-verify (DEMO_AUTO_VERIFY always verifies)
    if settings.DEMO_AUTO_VERIFY:
        user.is_verified = True
        auth_logger.info("DEMO_AUTO_VERIFY — verified user at registration", extra={"x_user_id": str(user.id)})
    elif settings.BREVO_API_KEY:
        token = secrets.token_urlsafe(32)
        try:
            await redis_client.setex(
                CacheKeys.EMAIL_VERIFY.format(token=token),
                settings.VERIFICATION_TOKEN_EXPIRE_HOURS * 3600,
                str(user.id),
            )
            sent = await send_verification_email(body.email, token)
            if sent:
                auth_logger.info("Verification email sent", extra={"x_user_id": str(user.id), "x_email": body.email})
            else:
                auth_logger.warning("Verification email failed to send — check Brevo logs", extra={"x_user_id": str(user.id)})
        except Exception as exc:
            auth_logger.warning("Failed to send verification email", extra={
                "x_user_id": str(user.id), "error": str(exc),
            })
    else:
        # No email service — auto-verify so users aren't locked out
        user.is_verified = True
        auth_logger.info("No BREVO_API_KEY — auto-verified user", extra={"x_user_id": str(user.id)})

    await db.commit()

    return {
        "access_token": create_access_token(str(user.id)),
        "refresh_token": create_refresh_token(str(user.id)),
        "token_type": "bearer",
        "expires_in": settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "is_verified": user.is_verified,
        "email": user.email,
    }


@router.post("/login")
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip = _get_ip(request)

    normalized_login_email = _normalize_email(body.email)
    result = await db.execute(select(User).where(User.email == normalized_login_email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_pw):
        auth_logger.warning("Login failed — invalid credentials", extra={
            "x_ip": ip, "x_email": body.email, "x_reason": "invalid_credentials",
        })
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    if not user.is_active:
        auth_logger.warning("Login failed — account suspended", extra={
            "x_ip": ip, "x_email": body.email, "x_user_id": str(user.id), "x_reason": "suspended",
        })
        raise HTTPException(status_code=403, detail="Account suspended.")

    auth_logger.info("Login successful", extra={
        "x_ip": ip, "x_user_id": str(user.id), "x_email": body.email,
    })

    return {
        "access_token": create_access_token(str(user.id)),
        "refresh_token": create_refresh_token(str(user.id)),
        "token_type": "bearer",
        "expires_in": settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "is_verified": user.is_verified,
        "email": user.email,
    }


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip = _get_ip(request)
    payload = decode_token(body.refresh_token)

    # Only accept refresh tokens
    if payload.get("type") != "refresh":
        auth_logger.warning("Invalid token type used for refresh", extra={
            "x_ip": ip, "x_reason": "wrong_token_type",
        })
        raise HTTPException(status_code=401, detail="Invalid token type.")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        auth_logger.warning("Refresh failed — user not found or inactive", extra={
            "x_ip": ip, "x_user_id": user_id or "unknown", "x_reason": "user_invalid",
        })
        raise HTTPException(status_code=401, detail="User not found.")

    auth_logger.info("Token refreshed", extra={
        "x_ip": ip, "x_user_id": str(user.id),
    })

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
    )


@router.get("/me", response_model=UserResponse)
async def get_profile(current_user: User = Depends(get_current_user)):
    from app.studio.safety import unlimited_view
    plan, credits = unlimited_view(settings.STUDIO_UNLIMITED, current_user.plan, round(current_user.credits, 1))
    return UserResponse(
        id=str(current_user.id),
        email=current_user.email,
        username=current_user.username,
        full_name=current_user.full_name,
        plan=plan,
        credits=credits,
        is_verified=current_user.is_verified,
        auth_provider=getattr(current_user, "auth_provider", None) or "email",
        created_at=current_user.created_at.isoformat(),
        instagram_user_id=getattr(current_user, "instagram_user_id", None) or None,
        instagram_username=getattr(current_user, "instagram_username", None) or None,
    )


class UpdateHandleRequest(BaseModel):
    username: str


@router.post("/update-handle")
async def update_handle(
    body: UpdateHandleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update creator handle (username). Must be unique."""
    handle = body.username.strip().lower()
    if len(handle) < 3:
        raise HTTPException(status_code=400, detail="Handle must be at least 3 characters")
    if not all(c.isalnum() or c in '._' for c in handle):
        raise HTTPException(status_code=400, detail="Only letters, numbers, dots, and underscores allowed")

    # Check uniqueness
    existing = await db.execute(select(User).where(User.username == handle, User.id != current_user.id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="This handle is already taken")

    current_user.username = handle
    await db.commit()

    return {"status": "updated", "username": handle}


# ── Email Verification & Password Reset ──────────────────────────────────────

@router.post("/verify-email", response_model=MessageResponse)
async def verify_email(body: VerifyEmailRequest, db: AsyncSession = Depends(get_db)):
    """Verify a user's email with the token from the verification link."""
    key = CacheKeys.EMAIL_VERIFY.format(token=body.token)
    user_id = await redis_client.get(key)

    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link.")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="User not found.")

    user.is_verified = True
    await db.commit()
    await redis_client.delete(key)

    auth_logger.info("Email verified", extra={"x_user_id": user_id, "x_email": user.email})
    return MessageResponse(message="Email verified successfully.")


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(body: ForgotPasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Send password reset email. Always returns success to prevent email enumeration."""
    ip = _get_ip(request)

    # Always return same message regardless of whether email exists
    success_msg = "If an account exists with that email, a reset link has been sent."

    normalized_fp_email = _normalize_email(body.email)
    result = await db.execute(select(User).where(User.email == normalized_fp_email))
    user = result.scalar_one_or_none()

    if not user or not settings.BREVO_API_KEY:
        # Normalize timing to prevent email enumeration
        await asyncio.sleep(random.uniform(0.1, 0.4))
        return MessageResponse(message=success_msg)

    # Rate limit: max 3 reset emails per hour per email
    rate_key = CacheKeys.EMAIL_RATE.format(email=body.email)
    try:
        pipe = redis_client.pipeline()
        pipe.incr(rate_key)
        pipe.expire(rate_key, 3600)
        results = await pipe.execute()
        if results[0] > 3:
            auth_logger.warning("Reset email rate limit hit", extra={"x_ip": ip, "x_email": body.email})
            return MessageResponse(message=success_msg)
    except Exception:
        pass

    token = secrets.token_urlsafe(32)
    try:
        await redis_client.setex(
            CacheKeys.PASSWORD_RESET.format(token=token),
            settings.RESET_TOKEN_EXPIRE_MINUTES * 60,
            str(user.id),
        )
        await send_password_reset_email(body.email, token)
        auth_logger.info("Password reset email sent", extra={"x_ip": ip, "x_email": body.email})
    except Exception as exc:
        auth_logger.error("Failed to send reset email", extra={"x_ip": ip, "error": str(exc)})

    return MessageResponse(message=success_msg)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Reset password using the token from the reset email."""
    key = CacheKeys.PASSWORD_RESET.format(token=body.token)
    user_id = await redis_client.get(key)

    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="User not found.")

    user.hashed_pw = hash_password(body.new_password)
    await db.commit()
    await redis_client.delete(key)

    auth_logger.info("Password reset", extra={"x_user_id": user_id, "x_email": user.email})
    return MessageResponse(message="Password reset successfully. You can now log in.")


@router.post("/resend-verification", response_model=MessageResponse)
async def resend_verification(body: ResendVerificationRequest, db: AsyncSession = Depends(get_db)):
    """Resend verification email."""
    if not settings.BREVO_API_KEY:
        raise HTTPException(status_code=500, detail="Email service not configured.")

    normalized_rv_email = _normalize_email(body.email)
    result = await db.execute(select(User).where(User.email == normalized_rv_email))
    user = result.scalar_one_or_none()

    if not user:
        # Don't reveal whether email exists
        return MessageResponse(message="If an account exists, a verification email has been sent.")

    if user.is_verified:
        return MessageResponse(message="Email is already verified.")

    # Rate limit: max 3 per hour
    rate_key = CacheKeys.EMAIL_RATE.format(email=normalized_rv_email)
    try:
        pipe = redis_client.pipeline()
        pipe.incr(rate_key)
        pipe.expire(rate_key, 3600)
        results = await pipe.execute()
        if results[0] > 3:
            return MessageResponse(message="Too many requests. Please try again later.")
    except Exception:
        pass

    token = secrets.token_urlsafe(32)
    await redis_client.setex(
        CacheKeys.EMAIL_VERIFY.format(token=token),
        settings.VERIFICATION_TOKEN_EXPIRE_HOURS * 3600,
        str(user.id),
    )
    await send_verification_email(normalized_rv_email, token)

    return MessageResponse(message="Verification email sent. Check your inbox.")


# ── Delete Account ────────────────────────────────────────────────────────────

@router.delete("/account", response_model=MessageResponse)
async def delete_account(
    body: DeleteAccountRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete the authenticated user's account after password confirmation."""
    ip = _get_ip(request)

    is_google_user = current_user.auth_provider == "google"

    if not is_google_user:
        if not body.password:
            raise HTTPException(status_code=400, detail="Password is required to delete your account.")
        if not verify_password(body.password, current_user.hashed_pw):
            auth_logger.warning("Account deletion failed — wrong password", extra={
                "x_ip": ip, "x_user_id": str(current_user.id),
            })
            raise HTTPException(status_code=403, detail="Incorrect password.")

    auth_logger.info("Account deleted", extra={
        "x_ip": ip, "x_user_id": str(current_user.id), "x_email": current_user.email,
    })

    await db.delete(current_user)
    await db.commit()

    return MessageResponse(message="Account deleted successfully.")


# ── Google OAuth ──────────────────────────────────────────────────────────────

class GoogleCallbackRequest(BaseModel):
    code: str
    state: str  # Required — CSRF protection


@router.get("/google/url")
async def google_auth_url():
    """Return the Google OAuth URL with CSRF state token."""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google OAuth not configured")

    # Generate CSRF state token — stored in Redis for verification
    state = secrets.token_urlsafe(32)
    try:
        from app.core.cache import redis_client
        await redis_client.setex(f"oauth_state:{state}", 600, "valid")  # 10 min expiry
    except Exception:
        raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return {"url": f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"}


@router.post("/google/callback")
async def google_callback(body: GoogleCallbackRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Exchange Google auth code for tokens, create/login user."""
    ip = _get_ip(request)

    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Google OAuth not configured")

    # Verify CSRF state token — reject if invalid
    try:
        from app.core.cache import redis_client
        state_key = f"oauth_state:{body.state}"
        valid = await redis_client.get(state_key)
        if not valid:
            auth_logger.warning("Google OAuth invalid state", extra={"x_ip": ip})
            raise HTTPException(status_code=400, detail="Invalid or expired OAuth state. Please try again.")
        await redis_client.delete(state_key)  # Single-use
    except HTTPException:
        raise
    except Exception:
        auth_logger.warning("Google OAuth state check failed (Redis)", extra={"x_ip": ip})
        raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")

    # Exchange code for Google tokens
    async with httpx.AsyncClient(timeout=10.0) as client:
        token_res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": body.code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )

    if token_res.status_code != 200:
        auth_logger.warning("Google OAuth token exchange failed", extra={
            "x_ip": ip, "x_reason": "token_exchange_failed",
            "x_redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "x_google_error": token_res.text[:200],
        })
        raise HTTPException(status_code=400, detail=f"Google sign-in failed: {token_res.json().get('error_description', 'Please try again.')}")

    token_data = token_res.json()
    access_token = token_data.get("access_token")

    # Get user info from Google
    async with httpx.AsyncClient(timeout=10.0) as client:
        userinfo_res = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if userinfo_res.status_code != 200:
        auth_logger.warning("Google userinfo fetch failed", extra={
            "x_ip": ip, "x_reason": "userinfo_failed",
        })
        raise HTTPException(status_code=400, detail="Failed to get Google user info")

    google_user = userinfo_res.json()
    email = google_user.get("email")
    name = google_user.get("name", "")

    if not email:
        raise HTTPException(status_code=400, detail="Google account has no email")

    # Check if user exists
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    is_new_user = False

    if user:
        if not user.is_active:
            auth_logger.warning("Google login failed — suspended", extra={
                "x_ip": ip, "x_email": email, "x_reason": "suspended",
            })
            raise HTTPException(status_code=403, detail="Account suspended.")
        auth_logger.info("Google login successful", extra={
            "x_ip": ip, "x_user_id": str(user.id), "x_email": email,
        })
    else:
        base_username = email.split("@")[0].lower().replace(".", "_")[:20]
        existing = await db.execute(select(User).where(User.username == base_username))
        if existing.scalar_one_or_none():
            base_username = base_username[:15] + "_" + secrets.token_hex(2)

        user = User(
            email=email,
            username=base_username,
            hashed_pw=hash_password(secrets.token_hex(32)),
            full_name=name,
            credits=settings.FREE_TIER_CREDITS,
            plan=PlanTier.FREE,
            is_verified=True,
            auth_provider="google",
        )
        db.add(user)
        await db.flush()
        await db.commit()
        is_new_user = True
        auth_logger.info("Google user registered", extra={
            "x_ip": ip, "x_user_id": str(user.id), "x_email": email,
        })

    return {
        "access_token": create_access_token(str(user.id)),
        "refresh_token": create_refresh_token(str(user.id)),
        "token_type": "bearer",
        "expires_in": settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "is_new": is_new_user,
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── Instagram OAuth ───────────────────────────────────────────────────────────

@router.get("/instagram")
async def instagram_oauth_start(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Redirect user to Instagram OAuth consent screen."""
    from fastapi.responses import RedirectResponse
    from app.core.security import decode_token
    if not settings.INSTAGRAM_APP_ID:
        raise HTTPException(status_code=503, detail="Instagram integration not configured")

    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Generate a CSRF state token and map it to the user_id in Redis (10 min TTL)
    csrf_state = secrets.token_urlsafe(32)
    await redis_client.setex(f"ig_oauth_state:{csrf_state}", 600, user_id)

    params = urlencode({
        "client_id": settings.INSTAGRAM_APP_ID,
        "redirect_uri": settings.INSTAGRAM_REDIRECT_URI,
        "scope": "instagram_business_basic,instagram_business_manage_messages,instagram_business_manage_comments,instagram_business_content_publish,instagram_business_manage_insights",
        "response_type": "code",
        "state": csrf_state,
    })
    return RedirectResponse(f"https://www.instagram.com/oauth/authorize?{params}")


@router.get("/instagram/callback")
async def instagram_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Handle Instagram OAuth callback, exchange code for token, store on user."""
    from fastapi.responses import RedirectResponse

    # Use INSTAGRAM_FRONTEND_URL if set (local dev), otherwise fall back to FRONTEND_URL (production)
    _frontend = settings.INSTAGRAM_FRONTEND_URL or settings.FRONTEND_URL

    if error or not code:
        return RedirectResponse(f"{_frontend}/settings?instagram=error")

    # Exchange code for short-lived token
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://api.instagram.com/oauth/access_token",
            data={
                "client_id": settings.INSTAGRAM_APP_ID,
                "client_secret": settings.INSTAGRAM_APP_SECRET,
                "grant_type": "authorization_code",
                "redirect_uri": settings.INSTAGRAM_REDIRECT_URI,
                "code": code,
            },
        )
    if token_res.status_code != 200:
        return RedirectResponse(f"{_frontend}/settings?instagram=error")

    token_data = token_res.json()
    short_token = token_data.get("access_token")
    ig_user_id = str(token_data.get("user_id", ""))

    # Exchange for long-lived token (60-day expiry)
    async with httpx.AsyncClient() as client:
        ll_res = await client.get(
            "https://graph.instagram.com/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": settings.INSTAGRAM_APP_SECRET,
                "access_token": short_token,
            },
        )
    long_token = short_token
    if ll_res.status_code == 200:
        long_token = ll_res.json().get("access_token", short_token)

    # Fetch Instagram username from Graph API
    ig_username = ""
    async with httpx.AsyncClient() as client:
        me_res = await client.get(
            "https://graph.instagram.com/me",
            params={"fields": "id,username", "access_token": long_token},
        )
    if me_res.status_code == 200:
        ig_username = me_res.json().get("username", "")

    # Resolve CSRF state token → user_id (single-use)
    if not state:
        return RedirectResponse(f"{_frontend}/settings?instagram=error")
    state_key = f"ig_oauth_state:{state}"
    resolved_user_id = await redis_client.get(state_key)
    if not resolved_user_id:
        return RedirectResponse(f"{_frontend}/settings?instagram=error")
    await redis_client.delete(state_key)

    from sqlalchemy import text
    result = await db.execute(select(User).where(User.id == resolved_user_id))
    user = result.scalar_one_or_none()
    if user:
        await db.execute(
            text("UPDATE users SET instagram_access_token = :tok, instagram_user_id = :uid, instagram_username = :uname WHERE id = :id"),
            {"tok": long_token, "uid": ig_user_id, "uname": ig_username, "id": str(user.id)},
        )
        await db.commit()

    return RedirectResponse(f"{_frontend}/settings?instagram=connected")


@router.delete("/instagram")
async def instagram_disconnect(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Clear the user's stored Instagram token and user_id."""
    from sqlalchemy import text
    await db.execute(
        text("UPDATE users SET instagram_access_token = NULL, instagram_user_id = NULL WHERE id = :id"),
        {"id": str(current_user.id)},
    )
    await db.commit()
    return {"detail": "Instagram disconnected"}
