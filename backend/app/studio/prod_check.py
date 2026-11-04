"""Built during the Meta Global AI Developer Hackathon (new work).

Startup check for a PUBLIC deployment. With APP_ENV=production the server refuses to start, with one clear message that names
every problem (variable names and reasons only, never values), when:

  * a required variable is missing, still a placeholder or localhost (database, Redis, secrets, website URL, model keys, storage);
  * a cost cap is not set on purpose (daily spend cap, per-user hourly and daily limits);
  * a dev-only switch is on (DEV_BAKEOFF, DEV_DINO, STUDIO_EXPOSE_COST, DEBUG_SAVE_INPUTS, INSTAGRAM_FIXTURE, DEBUG);
  * nobody could ever verify an account (neither DEMO_AUTO_VERIFY nor an email key).

Outside production it does nothing, so local work is unchanged. `problems()` is pure (settings and an env lookup in, two lists out) so the
tests need no server. The list of variables is documented in docs/deploy-checklist.md.
"""
from __future__ import annotations

from typing import Callable

# Cost caps a public deployment must choose on purpose: setting them is required even though code defaults exist.
COST_CAPS = ("STUDIO_DAILY_SPEND_CAP_USD", "STUDIO_USER_HOURLY_LIMIT", "STUDIO_USER_DAILY_LIMIT")
# Dev-only switches that must be off (setting objects name -> variable name is the same).
MUST_BE_OFF = ("DEV_BAKEOFF", "DEV_DINO", "STUDIO_EXPOSE_COST", "DEBUG_SAVE_INPUTS", "DEBUG")
MIN_SECRET_LEN = 32
PLACEHOLDER_HINTS = ("change-me", "change-in-production", "your-", "your_", "example.com", "xxx", "placeholder")


def _bad_secret(value: str) -> bool:
    v = (value or "").strip()
    return len(v) < MIN_SECRET_LEN or any(h in v.lower() for h in PLACEHOLDER_HINTS)


def _local(url: str) -> bool:
    u = (url or "").lower()
    return not u or "localhost" in u or "127.0.0.1" in u or "0.0.0.0" in u


def cors_origins(s) -> list[str]:
    """Allowed CORS origins: ALLOWED_ORIGINS (local dev defaults) + CORS_ORIGINS + FRONTEND_URL. In production the localhost entries are dropped."""
    origins = list(dict.fromkeys(
        [o.rstrip("/") for o in s.ALLOWED_ORIGINS]
        + [o.strip().rstrip("/") for o in s.CORS_ORIGINS.split(",") if o.strip()]
        + ([s.FRONTEND_URL.rstrip("/")] if s.FRONTEND_URL else [])
    ))
    if s.APP_ENV == "production":
        origins = [o for o in origins if "localhost" not in o and "127.0.0.1" not in o]
    return origins


def problems(s, getenv: Callable[[str], str | None]) -> tuple[list[str], list[str]]:
    """(errors, warnings) for production. `s` is the Settings object; `getenv(name)` returns a variable's value or None."""
    if s.APP_ENV != "production":
        return [], []
    err: list[str] = []
    warn: list[str] = []
    set_ = lambda n: bool((getenv(n) or "").strip())

    # Secrets and connections
    for name in ("SECRET_KEY", "JWT_SECRET"):
        if _bad_secret(getattr(s, name)):
            err.append(f"{name}: missing, a placeholder or shorter than {MIN_SECRET_LEN} characters")
    if s.SECRET_KEY and s.SECRET_KEY == s.JWT_SECRET:
        err.append("JWT_SECRET: must differ from SECRET_KEY")
    if _local(s.DATABASE_URL):
        err.append("DATABASE_URL: not set to the production database (still the local default)")
    if _local(s.REDIS_URL):
        err.append("REDIS_URL: not set to the production Redis (still the local default)")

    # The website: CORS, emails and the Instagram return address all come from these
    if _local(s.FRONTEND_URL) or not s.FRONTEND_URL.startswith("https://"):
        err.append("FRONTEND_URL: must be the website's https URL (it is the allowed CORS origin and the link in emails)")
    if s.CORS_ORIGINS and any(_local(o) or not o.strip().startswith("https://") for o in s.CORS_ORIGINS.split(",") if o.strip()):
        err.append("CORS_ORIGINS: every entry must be an https URL, none may be localhost")

    # Models and signals
    if (s.AI_PROVIDER or "").lower() == "meta":
        from app.studio import config as _studio_config
        if _studio_config.via_relay():   # a relay holds the Meta key (docs/meta-relay.md); the relay secret is what this server needs
            if not set_("RELAY_SECRET"):
                err.append("RELAY_SECRET: required because META_BASE_URL points at a relay")
        elif not set_("META_API_KEY"):
            err.append("META_API_KEY: required because AI_PROVIDER=meta")
    elif not s.ANTHROPIC_API_KEY:
        err.append("ANTHROPIC_API_KEY: required when AI_PROVIDER is not meta")
    if not set_("GROQ_API_KEY"):
        err.append("GROQ_API_KEY: required (speech transcription)")
    if not set_("GEMINI_API_KEY"):
        warn.append("GEMINI_API_KEY: not set, so the audio and trend stages are skipped")

    # Storage: the edited video goes to R2 when auto-edit is on
    if s.AUTO_EDIT_ENABLED:
        for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"):
            if not set_(name):   # explicit even for the bucket name, which has a code default
                err.append(f"{name}: required while AUTO_EDIT_ENABLED is on (set AUTO_EDIT_ENABLED=0 to run without edited videos)")

    # Accounts
    if not s.DEMO_AUTO_VERIFY and not s.BREVO_API_KEY:
        err.append("DEMO_AUTO_VERIFY / BREVO_API_KEY: set DEMO_AUTO_VERIFY=1 or give an email key, otherwise nobody can verify an account")

    # Instagram (only when the feature is configured)
    if s.INSTAGRAM_APP_ID:
        if not s.INSTAGRAM_APP_SECRET:
            err.append("INSTAGRAM_APP_SECRET: required because INSTAGRAM_APP_ID is set")
        if _local(s.INSTAGRAM_REDIRECT_URI) or "YOUR-BACKEND-URL" in s.INSTAGRAM_REDIRECT_URI or not s.INSTAGRAM_REDIRECT_URI.startswith("https://"):
            err.append("INSTAGRAM_REDIRECT_URI: must be the deployed backend's https callback URL")
        if not (s.INSTAGRAM_FRONTEND_URL or s.FRONTEND_URL):
            err.append("INSTAGRAM_FRONTEND_URL: required")

    # Cost caps chosen on purpose
    for name in COST_CAPS:
        if not set_(name):
            err.append(f"{name}: not set (a public deployment must choose its cost caps; see docs/deploy-checklist.md)")
    for name in COST_CAPS:
        if set_(name) and float(getattr(s, name)) <= 0:
            err.append(f"{name}: must be greater than 0")
    if not set_("TOKEN_ENCRYPTION_KEY"):
        err.append("TOKEN_ENCRYPTION_KEY: required (encrypts the Instagram token; create one with: python -m app.studio.instagram.tokencrypt generate)")

    # Dev-only switches
    for name in MUST_BE_OFF:
        if getattr(s, name, False):
            err.append(f"{name}: must be off in production")
    if (getenv("INSTAGRAM_FIXTURE") or "") == "1":
        err.append("INSTAGRAM_FIXTURE: must be off in production (fake Instagram data)")
    if s.PROMOTE_EMAILS and not s.FOUNDER_EMAIL:
        warn.append("PROMOTE_EMAILS is set; check that those addresses are yours")
    return err, warn


def enforce(s, getenv: Callable[[str], str | None], log=None) -> None:
    """Raise one RuntimeError listing every production problem (names and reasons only); log the warnings."""
    errors, warnings = problems(s, getenv)
    if log:
        for w in warnings:
            log.warning("[Startup] %s", w)
    if errors:
        raise RuntimeError("Prysm cannot start in production: fix these environment variables first (docs/deploy-checklist.md):\n  - "
                           + "\n  - ".join(errors))
