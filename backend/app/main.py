"""
Creator AI v2 – Production-Grade AI SaaS Backend
Main FastAPI application with complete v2 middleware stack and router registration.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response

from app.core.config import settings
from app.core.database import engine, Base
from app.core.cache import redis_client
from app.core.logging import configure_logging, get_logger
from app.core.metrics import get_metrics_response
from app.core.middleware import (
    CorrelationIDMiddleware,
    RequestLoggingMiddleware,
    RateLimitMiddleware,
    MetricsMiddleware,
    AbuseProtectionMiddleware,
)
from app.routers import auth, media, analysis, chat, creator
from app.routers import intelligence, clips
from app.routers import prysm
from app.routers import billing
from app.routers import admin

configure_logging()
logger = get_logger("main")


class SSESafeGZipMiddleware(GZipMiddleware):
    """GZip middleware that skips SSE (text/event-stream) responses to preserve streaming."""
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            accept = headers.get(b"accept", b"").decode()
            if "text/event-stream" in accept:
                await self.app(scope, receive, send)
                return
        await super().__call__(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Creator AI v2 starting up", extra={"env": settings.APP_ENV})
    from app.studio import prod_check   # hackathon: with APP_ENV=production, refuse to start on missing/placeholder variables or dev switches (names only)
    from app.studio.config import get_env as _get_env
    prod_check.enforce(settings, _get_env, logger)
    from app.studio.instagram.tokencrypt import startup_check   # hackathon: production needs TOKEN_ENCRYPTION_KEY (fails loudly)
    startup_check()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Ensure columns added after initial table creation exist
        from sqlalchemy import text, inspect
        def _add_missing_columns(sync_conn):
            insp = inspect(sync_conn)
            if insp.has_table("prysm_analyses"):
                cols = [c["name"] for c in insp.get_columns("prysm_analyses")]
                if "intent" not in cols:
                    sync_conn.execute(text("ALTER TABLE prysm_analyses ADD COLUMN intent VARCHAR(20) DEFAULT 'my_video'"))
                if "first_frame_b64" not in cols:
                    sync_conn.execute(text("ALTER TABLE prysm_analyses ADD COLUMN first_frame_b64 TEXT"))
                if "cost_breakdown" not in cols:
                    sync_conn.execute(text("ALTER TABLE prysm_analyses ADD COLUMN cost_breakdown JSONB"))
                if "total_cost_usd" not in cols:
                    sync_conn.execute(text("ALTER TABLE prysm_analyses ADD COLUMN total_cost_usd FLOAT"))
                # Widen share_id from VARCHAR(12) to VARCHAR(32) — token_urlsafe(16) produces 22 chars
                share_col = next((c for c in insp.get_columns("prysm_analyses") if c["name"] == "share_id"), None)
                if share_col and share_col.get("type") and hasattr(share_col["type"], "length") and (share_col["type"].length or 0) < 32:
                    sync_conn.execute(text("ALTER TABLE prysm_analyses ALTER COLUMN share_id TYPE VARCHAR(32)"))
            if insp.has_table("users"):
                user_cols = [c["name"] for c in insp.get_columns("users")]
                if "auth_provider" not in user_cols:
                    sync_conn.execute(text("ALTER TABLE users ADD COLUMN auth_provider VARCHAR(20) NOT NULL DEFAULT 'email'"))
                if "instagram_access_token" not in user_cols:
                    sync_conn.execute(text("ALTER TABLE users ADD COLUMN instagram_access_token TEXT"))
                if "instagram_user_id" not in user_cols:
                    sync_conn.execute(text("ALTER TABLE users ADD COLUMN instagram_user_id VARCHAR(50)"))
                if "instagram_username" not in user_cols:
                    sync_conn.execute(text("ALTER TABLE users ADD COLUMN instagram_username VARCHAR(100)"))
            if insp.has_table("chat_sessions"):
                chat_cols = [c["name"] for c in insp.get_columns("chat_sessions")]
                if "session_type" not in chat_cols:
                    sync_conn.execute(text("ALTER TABLE chat_sessions ADD COLUMN session_type VARCHAR(20) NOT NULL DEFAULT 'general'"))
                if "analysis_id" not in chat_cols:
                    sync_conn.execute(text("ALTER TABLE chat_sessions ADD COLUMN analysis_id UUID REFERENCES prysm_analyses(id) ON DELETE SET NULL"))
                    sync_conn.execute(text("CREATE INDEX IF NOT EXISTS ix_chat_sessions_analysis_id ON chat_sessions(analysis_id)"))
            if insp.has_table("chat_messages"):
                msg_cols = [c["name"] for c in insp.get_columns("chat_messages")]
                if "cost_usd" not in msg_cols:
                    sync_conn.execute(text("ALTER TABLE chat_messages ADD COLUMN cost_usd FLOAT"))
        await conn.run_sync(_add_missing_columns)

        # Founder/owner: permanent Creator plan — email read from env, never hardcoded
        from sqlalchemy import text
        founder_email = settings.FOUNDER_EMAIL
        if founder_email:
            try:
                await conn.execute(text("""
                    UPDATE users
                    SET plan = 'CREATOR',
                        credits = GREATEST(credits, 300),
                        plan_started_at = COALESCE(plan_started_at, NOW()),
                        plan_expires_at = NOW() + INTERVAL '100 years'
                    WHERE email = :email
                """), {"email": founder_email})
            except Exception as e:
                logger.warning(f"Founder promotion skipped: {e}")

        # One-time manual promotions: emails come from PROMOTE_EMAILS (empty by default), never hardcoded
        _promote_emails = [e.strip().lower() for e in settings.PROMOTE_EMAILS.split(",") if e.strip()]
        for _email in _promote_emails:
            try:
                await conn.execute(text("""
                    UPDATE users
                    SET plan = 'CREATOR',
                        credits = GREATEST(credits, 300),
                        plan_started_at = COALESCE(plan_started_at, NOW()),
                        plan_expires_at = NOW() + INTERVAL '100 years'
                    WHERE email = :email
                """), {"email": _email})
                logger.info(f"Promoted {_email} to Creator plan")
            except Exception as e:
                logger.warning(f"Promotion skipped for {_email}: {e}")
    await redis_client.ping()
    logger.info("Database and Redis connected.")

    # Start the persistent analysis worker loop
    from app.routers.prysm import _worker_loop
    import asyncio as _asyncio
    _asyncio.create_task(_worker_loop())
    logger.info("Analysis worker loop started.")

    # Pre-warm embedding models in background thread so the first user request
    # doesn't pay the cold model-load cost (~30-60s for ~3.7GB of weights).
    try:
        from app.services.embedding_service import prewarm_all_models
        prewarm_all_models()
        logger.info("Embedding model pre-warm triggered.")
    except Exception as _e:
        logger.warning(f"Embedding pre-warm skipped: {_e}")

    yield
    logger.info("Creator AI v2 shutting down")
    await redis_client.aclose()


# ── Disable docs in production ────────────────────────────────────────────────
is_prod = settings.APP_ENV == "production"

app = FastAPI(
    title="Prysm API",
    description="AI-powered video analysis for creators.",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None if is_prod else "/docs",
    redoc_url=None if is_prod else "/redoc",
    openapi_url=None if is_prod else "/openapi.json",
)

# ── Middleware (outermost first) ───────────────────────────────────────────────

# Trusted hosts — in production, only answer to the hostnames listed in TRUSTED_HOSTS (comma-separated)
_trusted_hosts = [h.strip() for h in settings.TRUSTED_HOSTS.split(",") if h.strip()]
if is_prod and _trusted_hosts:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[*_trusted_hosts, "localhost"],
    )

# ── Middleware stack ──────────────────────────────────────────────────────────
# Order matters: last added = first to execute.
# CORSMiddleware MUST be last (outermost) so CORS headers are always present,
# even when inner middleware or route handlers crash.

# Allowed origins: ALLOWED_ORIGINS (a JSON list, local dev by default) plus CORS_ORIGINS (comma-separated) and FRONTEND_URL
from app.studio.prod_check import cors_origins as _cors_list   # hackathon: same list as before; in production the localhost defaults are dropped
_cors_origins = _cors_list(settings)

app.add_middleware(CorrelationIDMiddleware)
app.add_middleware(AbuseProtectionMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(SSESafeGZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    max_age=600,
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router,         prefix="/auth",         tags=["Authentication"])
app.include_router(media.router,        prefix="/media",        tags=["Media"])
app.include_router(analysis.router,     prefix="/analyze",      tags=["Analysis"])
app.include_router(chat.router,         prefix="/chat",         tags=["ARIA Chatbot"])
app.include_router(creator.router,      prefix="/creator",      tags=["Creator Intelligence"])
app.include_router(intelligence.router, prefix="/intelligence", tags=["Intelligence v2"])
app.include_router(clips.router,        prefix="/clips",        tags=["Clip Generation"])
app.include_router(prysm.router,       prefix="/prysm",        tags=["Prysm Analysis"])
app.include_router(billing.router,     prefix="/billing",      tags=["Billing"])
app.include_router(admin.router,       prefix="/admin",         tags=["Admin"])
from app.studio.instagram.router import router as instagram_router  # hackathon: Instagram connect + Creator DNA
app.include_router(instagram_router, prefix="/instagram", tags=["Instagram / Creator DNA"])

if settings.DEV_BAKEOFF:  # hackathon, local only: the founder's blind bake-off review page (never mounted unless the flag is on)
    from app.studio import dev_bakeoff
    app.include_router(dev_bakeoff.router)

if settings.DEV_DINO:  # hackathon, dev only: DINOv2 curve / render check / own-reel matching (never mounted unless the flag is on)
    from app.studio.dino import router as dino_router
    app.include_router(dino_router.router)


# ── Security headers ─────────────────────────────────────────────────────────

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if is_prod:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # Razorpay's hosts are only allowed when payments are configured; CSP_CONNECT_SRC adds the backend URL etc.
        _pay = bool(settings.RAZORPAY_KEY_ID)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' 'unsafe-inline'{' https://checkout.razorpay.com' if _pay else ''}; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; "
            f"connect-src 'self' {settings.CSP_CONNECT_SRC}{' https://api.razorpay.com https://lux-gateway.razorpay.com' if _pay else ''}; "
            f"frame-src {'https://api.razorpay.com https://checkout.razorpay.com' if _pay else chr(39) + 'none' + chr(39)}; "
            "font-src 'self' data:; "
            "object-src 'none'; "
            "base-uri 'self'"
        )
    return response


# ── System endpoints ───────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check():
    return JSONResponse({"status": "ok", "service": "Prysm", "version": "2.0.1-queue"})


@app.get("/debug/queue", tags=["System"])
async def debug_queue(x_debug_token: str = Header(default="")):
    """Show Redis queue length — protected by debug token."""
    debug_token = settings.DEBUG_TOKEN
    if not debug_token or x_debug_token != debug_token:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        from app.core.cache import redis_client as _rl
        queue_len = await _rl.llen("prysm:queue")
        job_keys = await _rl.keys("prysm:job:*")
        jobs = {}
        for k in job_keys[-5:]:
            val = await _rl.get(k)
            if val:
                import json as _json
                parsed = _json.loads(val)
                jobs[k] = parsed.get("status", "?")
        return {"queue_length": queue_len, "recent_jobs": jobs, "total_job_keys": len(job_keys)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/ready", tags=["System"])
async def readiness_check():
    checks = {}
    try:
        await redis_client.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"
    try:
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ready" if all_ok else "not_ready", "checks": checks},
        status_code=200 if all_ok else 503,
    )


@app.get("/metrics", include_in_schema=False, tags=["System"])
async def prometheus_metrics():
    if is_prod:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    data, content_type = get_metrics_response()
    return Response(content=data, media_type=content_type)


# ── Global exception handler with logging ────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
    logger.exception(
        "Unhandled exception",
        extra={
            "x_path": str(request.url.path),
            "x_method": request.method,
            "x_ip": ip.split(",")[0].strip() if ip else "unknown",
            "x_ua": request.headers.get("user-agent", "")[:80],
        },
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal error."},
    )
