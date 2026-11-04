"""
app/core/config.py
Prysm configuration — multi-model: Claude (vision/reasoning), Groq (transcription),
Gemini (audio analysis/trends), Apify (Instagram scraping).
"""

from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # the Meta layer's own variables (META_API_KEY, ...) live in the same .env
        env_ignore_empty=True,  # hackathon: a name with no value in .env (as in .env.example) means "not set", not a parse error
    )

    # ── App ───────────────────────────────────────────────────────────────────
    APP_NAME: str = "Prysm"
    APP_ENV: str = "development"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:5173"]
    CORS_ORIGINS: str = ""         # comma-separated extra origins allowed by CORS (the deployed website), e.g. https://my-app.example
    TRUSTED_HOSTS: str = ""        # comma-separated hostnames the API answers to in production (empty: no host check)
    CSP_CONNECT_SRC: str = ""      # space-separated extra connect-src entries for the production CSP (e.g. the backend URL)
    PROMOTE_EMAILS: str = ""       # comma-separated emails promoted to the Creator plan at startup (empty: nobody)

    # ── Hackathon switches (Meta Global AI Developer Hackathon; every default keeps the baseline behaviour) ──
    AI_PROVIDER: str = ""                      # "meta": every Claude call is served by Muse Spark through app/studio/meta_llm_shim.py
    STUDIO_EXPOSE_COST: bool = False           # local testing: the owner may fetch the per-analysis cost breakdown (the founder email always may); never set in a public deployment
    DEV_BAKEOFF: bool = False                  # local only: mount /dev/bakeoff (saved blind bake-off variants for the founder's review page); never set in a deployment
    DEV_DINO: bool = False                     # dev only: mount /dino/* (DINOv2 visual-change curve, render check, own-reel matching; needs login). DINO_BACKEND / DINO_MODAL_URL / DINO_MODAL_TOKEN are read from .env by app/studio/dino/client.py
    COACH_STYLE: bool = True                   # hackathon: append app/studio/coach_style.md to the two scoring prompts as an addendum (default on; only used with AI_PROVIDER=meta; COACH_STYLE=0 turns it off)
    STEP_REGENERATE: bool = True               # hackathon: regenerate the steps of any parameter that fails the step check (fewer than 3 steps, fallback first, no ready-to-say line); STEP_REGENERATE=0 turns it off
    DEBUG_SAVE_INPUTS: bool = False            # hackathon: save the exact inputs of the scoring calls once per analysis (samples/_local/scoring_inputs) for scripts/rescore.py
    AUTO_EDIT_ENABLED: bool = True             # the baseline auto-edit + R2 upload step after an analysis; the hackathon env switches it off (AUTO_EDIT_ENABLED=0)
    STUDIO_MAX_CONCURRENT: int = 2             # analyses running at once across all processes (safety net)
    DEMO_AUTO_VERIFY: bool = False             # demo mode: new accounts are verified at once (no email provider needed)
    STUDIO_ALLOW_LINKS: bool = True            # reel links (the baseline link flow). Off: uploads only
    STUDIO_UNLIMITED: bool = False             # demo mode: no plans or credits; every user gets the full result (see studio/safety.py)
    STUDIO_DAILY_SPEND_CAP_USD: float = 10.0   # safety net: stop new analyses for the UTC day once their real cost adds up to this
    STUDIO_USER_MAX_CONCURRENT: int = 1        # safety net: analyses one user can have running at once
    STUDIO_USER_HOURLY_LIMIT: int = 10         # safety net: analyses one user can start per hour
    STUDIO_REGISTER_PER_IP_HOURLY: int = 5     # safety net: sign-ups per IP per hour

    @property
    def META_MODE(self) -> bool:
        """True when AI_PROVIDER=meta: the Claude call sites are served by Muse Spark (and the Anthropic package is never imported)."""
        return (self.AI_PROVIDER or "").strip().lower() == "meta"

    # ── Admin / Security ──────────────────────────────────────────────────────
    FOUNDER_EMAIL: str = ""        # Set in Railway env — never hardcode
    DEBUG_TOKEN: str = ""          # Token required to access /debug/* endpoints

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/prysm"

    # ── Redis / Celery ────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # ── Google OAuth ─────────────────────────────────────────────────────────
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:3000/oauth/google"

    # ── JWT ───────────────────────────────────────────────────────────────────
    JWT_SECRET: str = "jwt-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30       # 30 minutes
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7          # 7 days

    # ── Storage ───────────────────────────────────────────────────────────────
    STORAGE_BACKEND: str = "local"
    LOCAL_MEDIA_PATH: str = "/tmp/prysm/media"
    AWS_BUCKET_NAME: str = ""
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    CDN_BASE_URL: str = ""

    # ── Cloudflare R2 ─────────────────────────────────────────────────────────
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = "prysm-videos"

    # ── AI — Multi-model ──────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""

    # Claude: vision + reasoning + chat
    CLAUDE_ANALYSIS_MODEL: str = "claude-opus-4-6"
    CLAUDE_CHAT_MODEL: str = "claude-sonnet-4-6"
    CLAUDE_VISION_MODEL: str = "claude-opus-4-6"

    # ── Vision (Claude Opus) ─────────────────────────────────────────────────
    VISION_SAMPLE_FPS: float = 1.0
    VISION_HOOK_FPS: float = 2.0
    VISION_MAX_FRAMES: int = 80
    VISION_FRAME_WIDTH: int = 768
    VISION_ENABLED: bool = True
    VISION_MODEL: str = "claude-opus-4-6"     # used by vision_service
    VISION_FRAMES_PER_BATCH: int = 8
    VISION_HOOK_DEDICATED_PASS: bool = True

    # ── Transcription (Groq Whisper) ─────────────────────────────────────────
    WHISPER_MODEL: str = "whisper-large-v3-turbo"
    WHISPER_DEVICE: str = "cpu"               # unused, kept for compat

    # ── Vector DB ─────────────────────────────────────────────────────────────
    VECTOR_BACKEND: str = "pgvector"
    PINECONE_API_KEY: str = ""
    PINECONE_ENV: str = ""
    PINECONE_INDEX: str = "prysm"

    # ── Email (Brevo) ────────────────────────────────────────────────────────
    BREVO_API_KEY: str = ""
    BREVO_FROM_EMAIL: str = ""        # sender address; email is only sent when BREVO_API_KEY is set
    BREVO_FROM_NAME: str = "Prysm"
    FRONTEND_URL: str = "http://localhost:3000"  # the website's URL, used in emails and OAuth redirects
    VERIFICATION_TOKEN_EXPIRE_HOURS: int = 24
    RESET_TOKEN_EXPIRE_MINUTES: int = 60

    # ── Instagram OAuth ──────────────────────────────────────────────────────
    INSTAGRAM_APP_ID: str = ""        # Instagram App ID (not Meta App ID)
    INSTAGRAM_APP_SECRET: str = ""    # Instagram App Secret
    INSTAGRAM_REDIRECT_URI: str = "https://YOUR-BACKEND-URL.example.com/auth/instagram/callback"
    INSTAGRAM_FRONTEND_URL: str = ""  # Override FRONTEND_URL for OAuth redirects (set to http://localhost:3000 locally)

    # ── Payments (Razorpay) ──────────────────────────────────────────────────
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""

    # ── Stripe (future) ──────────────────────────────────────────────────────
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # ── External AI + Scraping ───────────────────────────────────────────────
    GROQ_API_KEY: str = ""                    # Groq Whisper — transcription
    GEMINI_API_KEY: str = ""                  # Gemini — audio analysis + trend search
    APIFY_API_TOKEN: str = ""                 # Apify — Instagram URL download only
    YT_DLP_RATE_LIMIT: str = "2M"
    RAPID_API_KEY: str = ""

    # ── ACRCloud — Song fingerprinting ───────────────────────────────────────
    ACRCLOUD_HOST: str = ""                   # e.g. identify-ap-southeast-1.acrcloud.com
    ACRCLOUD_ACCESS_KEY: str = ""
    ACRCLOUD_ACCESS_SECRET: str = ""

    # ── RunPod (engagement prediction) ───────────────────────────────────────
    RUNPOD_ENGAGEMENT_ENDPOINT: str = ""      # e.g. https://api.runpod.ai/v2/<id>/run
    RUNPOD_API_KEY: str = ""                  # RunPod API key

    # ── Credits ───────────────────────────────────────────────────────────────
    # Cost per feature (in credits)
    CREDITS_PER_ANALYSIS: float = 10.0
    CREDITS_PER_ARIA_CHAT: float = 1.0
    CREDITS_PER_CREATOR_SCRAPE: float = 25.0
    CREDITS_PER_COMPETITOR_ANALYSIS: float = 30.0
    CREDITS_PER_CLIP_GENERATION: float = 20.0
    CREDITS_PER_CONTENT_IDEAS: float = 5.0

    # Credits per plan (monthly allocation)
    FREE_TIER_CREDITS: float = 20.0       # one-time, not monthly
    PLAN_FREE_MONTHLY_CREDITS: float = 0.0   # free plan doesn't refresh
    PLAN_PRO_MONTHLY_CREDITS: float = 100.0   # Pro tier removed — kept for DB compat
    PLAN_PLUS_MONTHLY_CREDITS: float = 100.0
    PLAN_CREATOR_MONTHLY_CREDITS: float = 400.0

    # Credit rollover caps per plan
    ROLLOVER_CAP_PRO: float = 0.0
    ROLLOVER_CAP_PLUS: float = 20.0
    ROLLOVER_CAP_CREATOR: float = 50.0

    # ── Pricing (INR) ──────────────────────────────────────────────────────
    PRICE_PRO_MONTHLY_INR: int = 29900        # ₹299 in paise
    PRICE_PRO_ANNUAL_INR: int = 249900        # ₹2,499 in paise
    PRICE_PLUS_MONTHLY_INR: int = 79900       # ₹799 in paise
    PRICE_PLUS_ANNUAL_INR: int = 719900       # ₹7,199 in paise (~25% off ₹799×12)
    PRICE_CREATOR_MONTHLY_INR: int = 249900   # ₹2,499 in paise
    PRICE_CREATOR_ANNUAL_INR: int = 1999900   # ₹19,999 in paise (~33% off ₹2,499×12)

    # ── Pricing (USD — cents) ──────────────────────────────────────────────
    PRICE_PRO_MONTHLY_USD: int = 499          # $4.99
    PRICE_PRO_ANNUAL_USD: int = 4788          # $47.88
    PRICE_PLUS_MONTHLY_USD: int = 999         # $9.99
    PRICE_PLUS_ANNUAL_USD: int = 9588         # $95.88
    PRICE_CREATOR_MONTHLY_USD: int = 2999     # $29.99
    PRICE_CREATOR_ANNUAL_USD: int = 29988     # $299.88

    # ── Top-up packs (INR paise) ───────────────────────────────────────────
    TOPUP_STARTER_INR: int = 14900            # ₹149 → 30 credits
    TOPUP_POPULAR_INR: int = 34900            # ₹349 → 80 credits
    TOPUP_POWER_INR: int = 79900              # ₹799 → 200 credits
    TOPUP_STARTER_CREDITS: float = 30.0
    TOPUP_POPULAR_CREDITS: float = 80.0
    TOPUP_POWER_CREDITS: float = 200.0

    # ── Rate limiting ─────────────────────────────────────────────────────────
    RATE_LIMIT_ANALYZE_PER_MIN: int = 20
    RATE_LIMIT_CHAT_PER_MIN: int = 60
    RATE_LIMIT_GLOBAL_PER_MIN: int = 300

    # ── Content Strategist ────────────────────────────────────────────────────
    STRATEGIST_IDEAS_DEFAULT: int = 10

    # ── Clip generation ───────────────────────────────────────────────────────
    CLIP_DEFAULT_MAX: int = 5
    CLIP_MIN_DURATION_SEC: float = 15.0
    CLIP_MAX_DURATION_SEC: float = 60.0
    CLIP_CAPTION_FONT: str = "Arial"
    CLIP_CAPTION_FONT_SIZE: int = 36

    # ── Viral Pattern Engine ──────────────────────────────────────────────────
    VIRAL_PATTERN_MIN_SCORE: int = 65
    VIRAL_PATTERN_LOOKBACK_DAYS: int = 90

    # ── Scraper ───────────────────────────────────────────────────────────────
    SCRAPER_MAX_POSTS: int = 50
    SCRAPER_TIMEOUT_SEC: int = 120


# ── Niche-specific parameter weights (empirically derived) ────────────────────
# Scored parameters only — these 6 determine the overall score.
# Weights are normalised so each niche sums to 1.0.
# Source: RunPod A40 scoring of 115 real Instagram reels (May 2026).

ADVISORY_PARAMETERS = {
    "Call to Action",
    "On-Screen Text",
    "Save Potential",
    "Replay Value",
}

SCORED_PARAMETERS = {
    "Hook & Opening",
    "Retention & Pacing",
    "Audio & Music",
    "Visual Quality & Originality",
    "Trend Alignment",
    "Shareability",
}

# Raw empirical weights from niche_weights.md, re-normalised across scored params only.
# Empirical weights from 7,647 scored reels across 25 niches (11,590 total scraped).
# Methodology: within-creator normalization + Pearson correlation vs log2(views/creator_median).
# Weight blend: 40% mean score + 60% normalized correlation. Each row sums to ~100; divided by 100.
# Last updated: 2026-08-20 from batch 1-5 aggregation.
NICHE_WEIGHTS: dict = {
    "beauty": {
        "Hook & Opening":               round(10.9 / 100, 4),
        "Retention & Pacing":           round(9.7  / 100, 4),
        "Shareability":                 round(10.1 / 100, 4),
        "Audio & Music":                round(8.7  / 100, 4),
        "On-Screen Text":               round(10.1 / 100, 4),
        "Save Potential":               round(10.5 / 100, 4),
        "Trend Alignment":              round(10.8 / 100, 4),
        "Replay Value":                 round(9.1  / 100, 4),
        "Visual Quality & Originality": round(10.6 / 100, 4),
        "Call to Action":               round(9.4  / 100, 4),
    },
    "business": {
        "Hook & Opening":               round(10.9 / 100, 4),
        "Retention & Pacing":           round(9.8  / 100, 4),
        "Shareability":                 round(10.5 / 100, 4),
        "Audio & Music":                round(9.5  / 100, 4),
        "On-Screen Text":               round(9.9  / 100, 4),
        "Save Potential":               round(10.7 / 100, 4),
        "Trend Alignment":              round(10.6 / 100, 4),
        "Replay Value":                 round(9.4  / 100, 4),
        "Visual Quality & Originality": round(9.6  / 100, 4),
        "Call to Action":               round(9.0  / 100, 4),
    },
    "comedy": {
        "Hook & Opening":               round(11.0 / 100, 4),
        "Retention & Pacing":           round(9.8  / 100, 4),
        "Shareability":                 round(11.0 / 100, 4),
        "Audio & Music":                round(9.0  / 100, 4),
        "On-Screen Text":               round(10.0 / 100, 4),
        "Save Potential":               round(10.4 / 100, 4),
        "Trend Alignment":              round(11.1 / 100, 4),
        "Replay Value":                 round(9.4  / 100, 4),
        "Visual Quality & Originality": round(9.7  / 100, 4),
        "Call to Action":               round(8.5  / 100, 4),
    },
    "dance": {
        "Hook & Opening":               round(11.2 / 100, 4),
        "Retention & Pacing":           round(10.3 / 100, 4),
        "Shareability":                 round(10.9 / 100, 4),
        "Audio & Music":                round(9.9  / 100, 4),
        "On-Screen Text":               round(7.8  / 100, 4),
        "Save Potential":               round(10.2 / 100, 4),
        "Trend Alignment":              round(11.5 / 100, 4),
        "Replay Value":                 round(9.6  / 100, 4),
        "Visual Quality & Originality": round(10.8 / 100, 4),
        "Call to Action":               round(7.8  / 100, 4),
    },
    "diy_crafts": {
        "Hook & Opening":               round(10.9 / 100, 4),
        "Retention & Pacing":           round(9.8  / 100, 4),
        "Shareability":                 round(10.5 / 100, 4),
        "Audio & Music":                round(8.4  / 100, 4),
        "On-Screen Text":               round(10.1 / 100, 4),
        "Save Potential":               round(10.4 / 100, 4),
        "Trend Alignment":              round(11.0 / 100, 4),
        "Replay Value":                 round(9.3  / 100, 4),
        "Visual Quality & Originality": round(10.4 / 100, 4),
        "Call to Action":               round(9.2  / 100, 4),
    },
    "education": {
        "Hook & Opening":               round(10.8 / 100, 4),
        "Retention & Pacing":           round(9.8  / 100, 4),
        "Shareability":                 round(10.4 / 100, 4),
        "Audio & Music":                round(9.4  / 100, 4),
        "On-Screen Text":               round(10.7 / 100, 4),
        "Save Potential":               round(10.8 / 100, 4),
        "Trend Alignment":              round(10.2 / 100, 4),
        "Replay Value":                 round(9.2  / 100, 4),
        "Visual Quality & Originality": round(9.5  / 100, 4),
        "Call to Action":               round(9.2  / 100, 4),
    },
    "entertainment": {
        "Hook & Opening":               round(11.2 / 100, 4),
        "Retention & Pacing":           round(10.2 / 100, 4),
        "Shareability":                 round(10.9 / 100, 4),
        "Audio & Music":                round(8.5  / 100, 4),
        "On-Screen Text":               round(10.2 / 100, 4),
        "Save Potential":               round(10.6 / 100, 4),
        "Trend Alignment":              round(10.2 / 100, 4),
        "Replay Value":                 round(9.5  / 100, 4),
        "Visual Quality & Originality": round(10.6 / 100, 4),
        "Call to Action":               round(8.2  / 100, 4),
    },
    "fashion": {
        "Hook & Opening":               round(11.0 / 100, 4),
        "Retention & Pacing":           round(9.7  / 100, 4),
        "Shareability":                 round(10.4 / 100, 4),
        "Audio & Music":                round(8.1  / 100, 4),
        "On-Screen Text":               round(10.3 / 100, 4),
        "Save Potential":               round(10.5 / 100, 4),
        "Trend Alignment":              round(10.8 / 100, 4),
        "Replay Value":                 round(9.2  / 100, 4),
        "Visual Quality & Originality": round(10.7 / 100, 4),
        "Call to Action":               round(9.2  / 100, 4),
    },
    "finance": {
        "Hook & Opening":               round(11.0 / 100, 4),
        "Retention & Pacing":           round(9.8  / 100, 4),
        "Shareability":                 round(10.4 / 100, 4),
        "Audio & Music":                round(8.5  / 100, 4),
        "On-Screen Text":               round(10.9 / 100, 4),
        "Save Potential":               round(10.9 / 100, 4),
        "Trend Alignment":              round(10.4 / 100, 4),
        "Replay Value":                 round(9.1  / 100, 4),
        "Visual Quality & Originality": round(9.7  / 100, 4),
        "Call to Action":               round(9.4  / 100, 4),
    },
    "fitness": {
        "Hook & Opening":               round(11.1 / 100, 4),
        "Retention & Pacing":           round(9.9  / 100, 4),
        "Shareability":                 round(10.7 / 100, 4),
        "Audio & Music":                round(8.7  / 100, 4),
        "On-Screen Text":               round(10.1 / 100, 4),
        "Save Potential":               round(10.6 / 100, 4),
        "Trend Alignment":              round(10.8 / 100, 4),
        "Replay Value":                 round(9.2  / 100, 4),
        "Visual Quality & Originality": round(10.4 / 100, 4),
        "Call to Action":               round(8.5  / 100, 4),
    },
    "food": {
        "Hook & Opening":               round(10.6 / 100, 4),
        "Retention & Pacing":           round(9.7  / 100, 4),
        "Shareability":                 round(10.6 / 100, 4),
        "Audio & Music":                round(8.8  / 100, 4),
        "On-Screen Text":               round(10.2 / 100, 4),
        "Save Potential":               round(10.7 / 100, 4),
        "Trend Alignment":              round(10.4 / 100, 4),
        "Replay Value":                 round(9.3  / 100, 4),
        "Visual Quality & Originality": round(10.4 / 100, 4),
        "Call to Action":               round(9.2  / 100, 4),
    },
    "gaming": {
        "Hook & Opening":               round(10.8 / 100, 4),
        "Retention & Pacing":           round(10.0 / 100, 4),
        "Shareability":                 round(10.8 / 100, 4),
        "Audio & Music":                round(8.7  / 100, 4),
        "On-Screen Text":               round(10.0 / 100, 4),
        "Save Potential":               round(10.7 / 100, 4),
        "Trend Alignment":              round(10.9 / 100, 4),
        "Replay Value":                 round(9.3  / 100, 4),
        "Visual Quality & Originality": round(10.4 / 100, 4),
        "Call to Action":               round(8.5  / 100, 4),
    },
    "health_wellness": {
        "Hook & Opening":               round(11.0 / 100, 4),
        "Retention & Pacing":           round(9.8  / 100, 4),
        "Shareability":                 round(10.6 / 100, 4),
        "Audio & Music":                round(8.3  / 100, 4),
        "On-Screen Text":               round(10.0 / 100, 4),
        "Save Potential":               round(10.6 / 100, 4),
        "Trend Alignment":              round(10.7 / 100, 4),
        "Replay Value":                 round(9.3  / 100, 4),
        "Visual Quality & Originality": round(10.6 / 100, 4),
        "Call to Action":               round(9.1  / 100, 4),
    },
    "lifestyle": {
        "Hook & Opening":               round(11.0 / 100, 4),
        "Retention & Pacing":           round(10.0 / 100, 4),
        "Shareability":                 round(10.4 / 100, 4),
        "Audio & Music":                round(8.7  / 100, 4),
        "On-Screen Text":               round(10.3 / 100, 4),
        "Save Potential":               round(10.8 / 100, 4),
        "Trend Alignment":              round(10.2 / 100, 4),
        "Replay Value":                 round(9.4  / 100, 4),
        "Visual Quality & Originality": round(10.6 / 100, 4),
        "Call to Action":               round(8.5  / 100, 4),
    },
    "marketing": {
        "Hook & Opening":               round(11.0 / 100, 4),
        "Retention & Pacing":           round(9.9  / 100, 4),
        "Shareability":                 round(10.4 / 100, 4),
        "Audio & Music":                round(8.3  / 100, 4),
        "On-Screen Text":               round(10.5 / 100, 4),
        "Save Potential":               round(10.8 / 100, 4),
        "Trend Alignment":              round(10.3 / 100, 4),
        "Replay Value":                 round(9.4  / 100, 4),
        "Visual Quality & Originality": round(10.5 / 100, 4),
        "Call to Action":               round(8.9  / 100, 4),
    },
    "memes": {
        "Hook & Opening":               round(11.2 / 100, 4),
        "Retention & Pacing":           round(10.1 / 100, 4),
        "Shareability":                 round(10.7 / 100, 4),
        "Audio & Music":                round(8.1  / 100, 4),
        "On-Screen Text":               round(10.7 / 100, 4),
        "Save Potential":               round(10.3 / 100, 4),
        "Trend Alignment":              round(11.1 / 100, 4),
        "Replay Value":                 round(9.4  / 100, 4),
        "Visual Quality & Originality": round(10.2 / 100, 4),
        "Call to Action":               round(8.2  / 100, 4),
    },
    "motivation": {
        "Hook & Opening":               round(11.2 / 100, 4),
        "Retention & Pacing":           round(10.1 / 100, 4),
        "Shareability":                 round(10.3 / 100, 4),
        "Audio & Music":                round(9.1  / 100, 4),
        "On-Screen Text":               round(10.3 / 100, 4),
        "Save Potential":               round(10.6 / 100, 4),
        "Trend Alignment":              round(10.2 / 100, 4),
        "Replay Value":                 round(9.4  / 100, 4),
        "Visual Quality & Originality": round(9.7  / 100, 4),
        "Call to Action":               round(8.9  / 100, 4),
    },
    "music": {
        "Hook & Opening":               round(11.0 / 100, 4),
        "Retention & Pacing":           round(10.2 / 100, 4),
        "Shareability":                 round(10.4 / 100, 4),
        "Audio & Music":                round(10.1 / 100, 4),
        "On-Screen Text":               round(9.2  / 100, 4),
        "Save Potential":               round(10.5 / 100, 4),
        "Trend Alignment":              round(10.6 / 100, 4),
        "Replay Value":                 round(9.5  / 100, 4),
        "Visual Quality & Originality": round(10.3 / 100, 4),
        "Call to Action":               round(8.3  / 100, 4),
    },
    "news_politics": {
        "Hook & Opening":               round(10.9 / 100, 4),
        "Retention & Pacing":           round(10.2 / 100, 4),
        "Shareability":                 round(11.1 / 100, 4),
        "Audio & Music":                round(8.4  / 100, 4),
        "On-Screen Text":               round(9.6  / 100, 4),
        "Save Potential":               round(10.7 / 100, 4),
        "Trend Alignment":              round(11.3 / 100, 4),
        "Replay Value":                 round(9.4  / 100, 4),
        "Visual Quality & Originality": round(10.1 / 100, 4),
        "Call to Action":               round(8.4  / 100, 4),
    },
    "photography": {
        "Hook & Opening":               round(11.0 / 100, 4),
        "Retention & Pacing":           round(10.1 / 100, 4),
        "Shareability":                 round(10.7 / 100, 4),
        "Audio & Music":                round(8.8  / 100, 4),
        "On-Screen Text":               round(9.7  / 100, 4),
        "Save Potential":               round(10.8 / 100, 4),
        "Trend Alignment":              round(10.2 / 100, 4),
        "Replay Value":                 round(9.6  / 100, 4),
        "Visual Quality & Originality": round(11.3 / 100, 4),
        "Call to Action":               round(7.9  / 100, 4),
    },
    "self_improvement": {
        "Hook & Opening":               round(10.8 / 100, 4),
        "Retention & Pacing":           round(9.8  / 100, 4),
        "Shareability":                 round(10.6 / 100, 4),
        "Audio & Music":                round(8.3  / 100, 4),
        "On-Screen Text":               round(10.6 / 100, 4),
        "Save Potential":               round(10.6 / 100, 4),
        "Trend Alignment":              round(10.5 / 100, 4),
        "Replay Value":                 round(9.3  / 100, 4),
        "Visual Quality & Originality": round(10.1 / 100, 4),
        "Call to Action":               round(9.3  / 100, 4),
    },
    "sports": {
        "Hook & Opening":               round(11.3 / 100, 4),
        "Retention & Pacing":           round(10.1 / 100, 4),
        "Shareability":                 round(10.7 / 100, 4),
        "Audio & Music":                round(8.1  / 100, 4),
        "On-Screen Text":               round(10.2 / 100, 4),
        "Save Potential":               round(10.6 / 100, 4),
        "Trend Alignment":              round(10.6 / 100, 4),
        "Replay Value":                 round(9.3  / 100, 4),
        "Visual Quality & Originality": round(10.6 / 100, 4),
        "Call to Action":               round(8.5  / 100, 4),
    },
    "tech": {
        "Hook & Opening":               round(10.7 / 100, 4),
        "Retention & Pacing":           round(9.8  / 100, 4),
        "Shareability":                 round(10.7 / 100, 4),
        "Audio & Music":                round(9.0  / 100, 4),
        "On-Screen Text":               round(9.7  / 100, 4),
        "Save Potential":               round(10.6 / 100, 4),
        "Trend Alignment":              round(10.9 / 100, 4),
        "Replay Value":                 round(9.3  / 100, 4),
        "Visual Quality & Originality": round(10.1 / 100, 4),
        "Call to Action":               round(9.1  / 100, 4),
    },
    "travel": {
        "Hook & Opening":               round(11.1 / 100, 4),
        "Retention & Pacing":           round(10.2 / 100, 4),
        "Shareability":                 round(10.6 / 100, 4),
        "Audio & Music":                round(8.8  / 100, 4),
        "On-Screen Text":               round(9.8  / 100, 4),
        "Save Potential":               round(10.7 / 100, 4),
        "Trend Alignment":              round(10.2 / 100, 4),
        "Replay Value":                 round(9.5  / 100, 4),
        "Visual Quality & Originality": round(10.9 / 100, 4),
        "Call to Action":               round(8.3  / 100, 4),
    },
    "vlogs": {
        "Hook & Opening":               round(11.2 / 100, 4),
        "Retention & Pacing":           round(10.1 / 100, 4),
        "Shareability":                 round(10.5 / 100, 4),
        "Audio & Music":                round(8.6  / 100, 4),
        "On-Screen Text":               round(10.1 / 100, 4),
        "Save Potential":               round(10.6 / 100, 4),
        "Trend Alignment":              round(10.8 / 100, 4),
        "Replay Value":                 round(9.2  / 100, 4),
        "Visual Quality & Originality": round(10.3 / 100, 4),
        "Call to Action":               round(8.6  / 100, 4),
    },
    # Bollywood/IndianCreator: proxied from dance profile (closest match for music+trend-driven Indian content)
    "bollywood": {
        "Hook & Opening":               round(11.2 / 100, 4),
        "Retention & Pacing":           round(10.3 / 100, 4),
        "Shareability":                 round(10.9 / 100, 4),
        "Audio & Music":                round(9.9  / 100, 4),
        "On-Screen Text":               round(7.8  / 100, 4),
        "Save Potential":               round(10.2 / 100, 4),
        "Trend Alignment":              round(11.5 / 100, 4),
        "Replay Value":                 round(9.6  / 100, 4),
        "Visual Quality & Originality": round(10.8 / 100, 4),
        "Call to Action":               round(7.8  / 100, 4),
    },
    "indiancreator": {
        "Hook & Opening":               round(11.2 / 100, 4),
        "Retention & Pacing":           round(10.3 / 100, 4),
        "Shareability":                 round(10.9 / 100, 4),
        "Audio & Music":                round(9.9  / 100, 4),
        "On-Screen Text":               round(7.8  / 100, 4),
        "Save Potential":               round(10.2 / 100, 4),
        "Trend Alignment":              round(11.5 / 100, 4),
        "Replay Value":                 round(9.6  / 100, 4),
        "Visual Quality & Originality": round(10.8 / 100, 4),
        "Call to Action":               round(7.8  / 100, 4),
    },
}

# Default weights when niche is unknown / not in NICHE_WEIGHTS.
# Equal weights across all 10 scored parameters.
DEFAULT_SCORED_WEIGHTS: dict = {
    "Hook & Opening":               0.1100,
    "Retention & Pacing":           0.1000,
    "Shareability":                 0.1050,
    "Audio & Music":                0.0880,
    "On-Screen Text":               0.1000,
    "Save Potential":               0.1060,
    "Trend Alignment":              0.1070,
    "Replay Value":                 0.0930,
    "Visual Quality & Originality": 0.1040,
    "Call to Action":               0.0870,
}


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # Block startup if production is using placeholder secrets
    if s.APP_ENV == "production":
        if s.SECRET_KEY == "change-me-in-production":
            raise RuntimeError("SECRET_KEY must be changed in production!")
        if s.JWT_SECRET == "jwt-secret-change-in-production":
            raise RuntimeError("JWT_SECRET must be changed in production!")
        if s.DEBUG:
            raise RuntimeError("DEBUG must be False in production!")
    return s


settings = get_settings()
