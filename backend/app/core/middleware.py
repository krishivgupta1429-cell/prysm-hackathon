"""
app/core/middleware.py
Production-grade middleware stack:
  1. CorrelationIDMiddleware  – attaches X-Request-ID to every request
  2. RequestLoggingMiddleware – structured access log with timing
  3. RateLimitMiddleware      – per-user / per-IP sliding window via Redis
  4. MetricsMiddleware        – Prometheus counter + histogram per route
  5. AbuseProtectionMiddleware – bot detection + IP blocking
"""

import time
import hashlib
from typing import Callable, Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core.logging import (
    get_logger, new_request_id,
    request_id_var, user_id_var, endpoint_var,
)
from app.core.metrics import (
    http_requests_total,
    http_request_duration_seconds,
)

logger = get_logger("middleware")


# ── 1. Correlation ID ─────────────────────────────────────────────────────────

class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """
    Reads X-Request-ID from incoming headers or generates one.
    Stores it in ContextVar for structured logging and echoes it in the response.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rid = request.headers.get("X-Request-ID") or new_request_id()
        token = request_id_var.set(rid)
        ep_token = endpoint_var.set(f"{request.method} {request.url.path}")
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_var.reset(token)
            endpoint_var.reset(ep_token)


# ── 2. Request Logging ────────────────────────────────────────────────────────

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Emits a structured log entry for every HTTP request."""

    SKIP_PATHS = {"/health", "/metrics", "/favicon.ico"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)

        logger.info(
            "HTTP request",
            extra={
                "x_method":      request.method,
                "x_path":        request.url.path,
                "x_status":      response.status_code,
                "duration_ms":   duration_ms,
                "x_ip":          request.client.host if request.client else "unknown",
                "x_ua":          request.headers.get("user-agent", "")[:80],
            },
        )
        response.headers["X-Response-Time-Ms"] = str(duration_ms)
        return response


# ── 3. Rate Limiting ──────────────────────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Granular rate limiter stored in Redis.

    Limits per IP (sliding window):
      - /auth/login         → 5 req/min   (brute-force protection)
      - /auth/register      → 3 req/min   (account spam prevention)
      - /auth/google/*      → 10 req/min  (OAuth abuse)
      - /prysm/analyse      → 5 req/min   (AI generation — expensive)
      - /prysm/chat         → 20 req/min  (AI chat)
      - /analyze/*          → 10 req/min  (analysis endpoints)
      - /chat/*             → 30 req/min  (chat endpoints)
      - /intelligence/*     → 10 req/min  (scraper endpoints)
      - /creator/*          → 10 req/min  (creator endpoints)
      - Global              → 120 req/min per IP
    """

    RULES = [
        # (path_prefix, window_seconds, max_requests, key_type)
        ("/auth/register",      60,   3,  "ip"),
        ("/auth/login",         60,   5,  "ip"),
        ("/auth/verify-email",  60,   5,  "ip"),
        ("/auth/forgot-password",60,  3,  "ip"),
        ("/auth/reset-password",60,   5,  "ip"),
        ("/auth/resend-verification",60,3,"ip"),
        ("/auth/account",       60,  10,  "user"),
        ("/auth/google",        60,  30,  "ip"),
        ("/prysm/analyse/status", 60, 120, "user"),  # status polling — 1 req/5s = 12/min, allow headroom
        ("/prysm/analyse",      60,   5,  "user"),
        ("/prysm/chat",         60,  20,  "user"),
        ("/analyze",            60,  10,  "user"),
        ("/chat",               60,  30,  "user"),
        ("/intelligence",       60,  10,  "user"),
        ("/creator",            60,  10,  "user"),
        ("/clips",              60,  10,  "user"),
    ]
    GLOBAL_WINDOW  = 60
    GLOBAL_LIMIT   = 120
    SKIP_PATHS     = {"/health", "/metrics", "/docs", "/redoc", "/openapi.json"}
    GLOBAL_EXEMPT_PREFIXES = ("/prysm/analyse/status",)  # hackathon: polled every few seconds for minutes

    @staticmethod
    async def _hit(redis_client, key: str, window: int) -> int:
        """Count one request in a fixed window: the expiry is set when the window opens, never pushed back by later requests."""
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, window)
        return count

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method == "OPTIONS" or request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        ip = self._get_real_ip(request)

        try:
            from app.core.cache import redis_client

            # Global IP limit. Hackathon change: the analysis status poll is exempt (it has its own, generous per-user
            # limit below), and a window is fixed from its FIRST request: the baseline reset the expiry on every request,
            # so a client that kept polling inside the window never saw the counter expire and was blocked for good.
            if not request.url.path.startswith(self.GLOBAL_EXEMPT_PREFIXES):
                global_key = f"rl:global:{ip}"
                count = await self._hit(redis_client, global_key, self.GLOBAL_WINDOW)

                if count > self.GLOBAL_LIMIT:
                    logger.warning("Global rate limit hit", extra={"x_ip": ip, "count": count})
                    return self._rate_limit_response("Too many requests. Please slow down.")

            # Route-specific limits
            for prefix, window, limit, key_type in self.RULES:
                if request.url.path.startswith(prefix):
                    if key_type == "user":
                        auth = request.headers.get("authorization", "")
                        identifier = hashlib.sha256(auth.encode()).hexdigest()[:16] if auth else ip
                    else:
                        identifier = ip

                    route_key = f"rl:{prefix}:{identifier}"
                    route_count = await self._hit(redis_client, route_key, window)

                    if route_count > limit:
                        logger.warning(
                            "Route rate limit hit",
                            extra={"x_ip": ip, "prefix": prefix, "count": route_count, "limit": limit},
                        )
                        return self._rate_limit_response(
                            f"Rate limit exceeded. Max {limit} requests per minute for this endpoint."
                        )
                    break

        except Exception:
            pass

        return await call_next(request)

    @staticmethod
    def _get_real_ip(request: Request) -> str:
        """Extract real IP from proxy headers."""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip
        return request.client.host if request.client else "0.0.0.0"

    @staticmethod
    def _rate_limit_response(detail: str) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": detail, "error": "rate_limit_exceeded"},
            headers={"Retry-After": "60"},
        )


# ── 4. Prometheus Metrics ─────────────────────────────────────────────────────

class MetricsMiddleware(BaseHTTPMiddleware):
    """Records Prometheus counter and histogram for every request."""

    SKIP_PATHS = {"/health", "/metrics"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        # Normalise path to avoid high-cardinality label (e.g. /media/UUID → /media/{id})
        path = _normalise_path(request.url.path)

        http_requests_total.labels(
            method=request.method,
            endpoint=path,
            status_code=str(response.status_code),
        ).inc()
        http_request_duration_seconds.labels(
            method=request.method,
            endpoint=path,
        ).observe(duration)

        return response


# ── 5. Abuse Protection ──────────────────────────────────────────────────────

class AbuseProtectionMiddleware(BaseHTTPMiddleware):
    """
    Blocks obvious bot/scraper traffic based on:
      - Missing or suspicious User-Agent
      - Rapid-fire failed login attempts (auto-ban IP for 15 min after 10 failures)
      - Suspicious request patterns
    """

    SKIP_PATHS = {"/health", "/metrics", "/docs", "/redoc", "/openapi.json",
                  "/auth/google/callback", "/auth/google/url", "/auth/refresh",
                  "/billing/webhook"}

    # Known bot UA substrings to block
    BOT_SIGNATURES = [
        "python-requests", "scrapy", "wget", "curl",
        "httpclient", "java/", "go-http-client",
        "php/", "perl", "ruby", "mechanize",
    ]

    # Paths that don't require User-Agent (health checks, etc)
    UA_EXEMPT_PATHS = {"/health", "/ready", "/metrics"}

    # Paths that are JWT-protected — no need for UA/bot checks, auth is the gate
    JWT_PROTECTED_PREFIXES = ("/prysm/", "/billing/", "/analyze/", "/chat/",
                              "/creator/", "/intelligence/", "/clips/", "/media/")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method == "OPTIONS" or request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        # JWT-protected endpoints: skip UA/bot checks entirely.
        # These routes require a valid Bearer token — that's sufficient protection.
        # Vercel's edge proxy strips or rewrites User-Agent headers, so UA-based
        # bot detection incorrectly blocks legitimate status polls from the frontend.
        if any(request.url.path.startswith(p) for p in self.JWT_PROTECTED_PREFIXES):
            return await call_next(request)

        ip = self._get_real_ip(request)
        ua = request.headers.get("user-agent", "").lower()

        try:
            from app.core.cache import redis_client

            # Check if IP is banned
            ban_key = f"ban:{ip}"
            if await redis_client.exists(ban_key):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Access temporarily blocked due to suspicious activity."},
                )

            # Block requests with no User-Agent on non-exempt paths
            if not ua and request.url.path not in self.UA_EXEMPT_PATHS:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Request blocked."},
                )

            # Block known bot User-Agents on auth endpoints only
            if any(bot in ua for bot in self.BOT_SIGNATURES):
                if request.url.path.startswith("/auth"):
                    logger.warning("Bot UA blocked", extra={"x_ip": ip, "ua": ua[:60]})
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Automated access is not permitted."},
                    )

            # Track failed login attempts — auto-ban after 10 failures in 5 minutes
            response = await call_next(request)

            if request.url.path in ("/auth/login", "/auth/register") and response.status_code in (401, 409):
                fail_key = f"auth_fail:{ip}"
                pipe = redis_client.pipeline()
                pipe.incr(fail_key)
                pipe.expire(fail_key, 300)  # 5 minute window
                res = await pipe.execute()
                fail_count = res[0]

                if fail_count >= 10:
                    await redis_client.setex(ban_key, 900, "banned")  # 15 min ban
                    logger.warning("IP auto-banned for auth abuse", extra={"x_ip": ip, "failures": fail_count})

            return response

        except Exception:
            return await call_next(request)

    @staticmethod
    def _get_real_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip
        return request.client.host if request.client else "0.0.0.0"


def _normalise_path(path: str) -> str:
    """Replace UUID segments with {id} to limit Prometheus label cardinality."""
    import re
    return re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "{id}",
        path,
    )
