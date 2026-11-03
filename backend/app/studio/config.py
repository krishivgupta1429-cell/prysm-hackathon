"""Hackathon-built (Meta Global AI Developer Hackathon).

Prysm configuration: API endpoints, model ids, prices and paths.

Secrets are looked up by variable name in backend/.env (falling back to the process
environment) and are never printed or logged. Studio is standalone: nothing here or
elsewhere under app/studio imports from app.services, app.routers, Postgres or Redis.
"""
import os
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

STUDIO_DIR = Path(__file__).resolve().parent
BACKEND_DIR = STUDIO_DIR.parents[1]
REPO_DIR = BACKEND_DIR.parent
ENV_PATH = BACKEND_DIR / ".env"

# Meta API
META_BASE_URL = "https://api.meta.ai/v1"
SPARK_MODELS = ("muse-spark-1.1", "muse-spark-1.2", "muse-spark-1.3")  # plain ids, never -contributor
SPARK_USD_PER_M_IN = 1.25
SPARK_USD_PER_M_OUT = 4.25
WONDER_MODEL = "muse-wonder-1.0"  # found with GET /v1/models; used by the director probe only
WONDER_USD_PER_M_IN = 4.00
WONDER_USD_PER_M_OUT = 20.00
MODEL_USD_PER_M = {WONDER_MODEL: (WONDER_USD_PER_M_IN, WONDER_USD_PER_M_OUT)}  # models not listed here are priced as Spark


def price_per_m(model: str) -> tuple[float, float]:
    """(input, output) USD per 1M tokens for a Meta chat model: published prices, Spark's unless the model is listed."""
    return MODEL_USD_PER_M.get(model, (SPARK_USD_PER_M_IN, SPARK_USD_PER_M_OUT))
VOICE_TRANSCRIBE_MODEL = "muse-voice-transcribe-1.0"
VOICE_TRANSCRIBE_USD_PER_HOUR = 0.18
SAM_MODEL = "sam-3.1"
SAM_USD_PER_1000_FRAMES = 0.20
DEFAULT_SAM_FPS = 10  # override with STUDIO_SAM_FPS or analyze --sam-fps (SAM is billed per frame)

# Groq Whisper (cost is an estimate of Groq's published per-hour price)
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"
GROQ_USD_PER_HOUR = 0.04

# Stage 1B: audio listeners
SPARK_AUDIO_MODEL = "muse-spark-1.2"  # the model that heard audio most reliably in spark_audio_test
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODEL = "gemini-2.5-pro"
GEMINI_FALLBACK_MODEL = "gemini-2.5-flash"
GEMINI_MAX_VIDEO_BYTES = 18 * 1024 * 1024  # send the proxy video below this size, else the wav
# APPROXIMATE Gemini prices in USD per 1M tokens (input, output incl. thinking); check Google's price list.
GEMINI_USD_PER_M = {"gemini-2.5-pro": (1.25, 10.00), "gemini-2.5-flash": (0.30, 2.50)}
CAPTION_MODEL = "muse-spark-1.3"
ANALYSIS_MODEL = "muse-spark-1.3"  # niche detection and scoring (text, optionally keyframes)
ARIA_MODEL = "muse-spark-1.3"  # the ARIA chat assistant
ANALYSIS_TEMPERATURE = 0.2
EVIDENCE_MAX_TOKENS = 6000
CAPTION_MAX_LINES = 2
CAPTION_MAX_LINE_CHARS = 32
CAPTION_MIN_S = 1.0
CAPTION_MAX_S = 3.0

# Stage 1C: signal models (Modal GPU) and ACRCloud music ID
MODAL_APP_NAME = "prysm-studio-signals"
MODAL_CLASS_NAME = "Signals"
MODAL_LB_CLASS_NAME = "LanguageBindSignals"  # LanguageBind towers, in a second image (older transformers)
MODAL_VOLUME_NAME = "prysm-studio-hf-cache"
# Hugging Face model ids (verified against the hub). Demucs htdemucs is not a hub model: its weights are
# fetched by the `demucs` package from Meta's servers.
SIGNAL_MODEL_IDS = {
    "dinov2": "facebook/dinov2-base",
    "vjepa2": "facebook/vjepa2-vitl-fpc64-256",
    "languagebind_video": "LanguageBind/LanguageBind_Video_FT",
    "languagebind_audio": "LanguageBind/LanguageBind_Audio_FT",
    "languagebind_text": "LanguageBind/LanguageBind_Image",
    "clip": "openai/clip-vit-large-patch14",  # stand-in when the LanguageBind video tower cannot run; local fallback
    "encodec": "facebook/encodec_24khz",
    "clap": "laion/clap-htsat-fused",
}
DEMUCS_MODEL = "htdemucs"
LOCAL_SIGNAL_MODELS = ("dinov2", "clip", "clap")  # can run on CPU with --signals-local
# APPROXIMATE Modal GPU prices in USD per second (check modal.com/pricing); billed from container start.
MODAL_GPU_USD_PER_S = {"L4": 0.000222, "T4": 0.000164}
ACRCLOUD_USD_PER_REQUEST = 0.0  # plan-based, not metered here
ACR_WINDOW_S = 12  # seconds of audio per identify request
ACR_MIN_REEL_S = 6.0  # shorter reels are not identified
ACR_RETRIES = 1
ACR_REQUEST_TIMEOUT_S = 20.0

# Media prep
PROXY_HEIGHT = 720
MEDIA_MAX_SECONDS = 90

# Per-stage timeouts (seconds)
TIMEOUT_WATCH = 150
TIMEOUT_SPEECH = 120
TIMEOUT_SUBJECTS = 180
TIMEOUT_AUDIO = 300  # per audio listener (Gemini may back off up to ~60 s per model before falling back)
TIMEOUT_CAPTIONS = 90
TIMEOUT_SIGNALS = 240  # includes a possible Modal cold start
TIMEOUT_MUSIC_ID = 60
TIMEOUT_ANALYSIS = 420  # niche detection + the scoring runs (--repeat runs them in parallel)

# Which Spark model the "watch" stage uses by default
DEFAULT_WATCH_MODEL = "muse-spark-1.3"


@lru_cache(maxsize=1)
def _dotenv() -> dict[str, str | None]:
    return dict(dotenv_values(ENV_PATH)) if ENV_PATH.exists() else {}


def get_env(name: str) -> str | None:
    """Value of a variable from backend/.env, else the process environment. Never log the result."""
    value = (_dotenv().get(name) or os.environ.get(name) or "").strip()
    return value or None


def meta_key() -> str | None:
    return get_env("META_API_KEY")


def groq_key() -> str | None:
    return get_env("GROQ_API_KEY")


def gemini_key() -> str | None:
    return get_env("GEMINI_API_KEY")


def acr_credentials() -> tuple[str | None, str | None, str | None]:
    """(host, access key, access secret) for ACRCloud; never log the secret."""
    return get_env("ACRCLOUD_HOST"), get_env("ACRCLOUD_ACCESS_KEY"), get_env("ACRCLOUD_ACCESS_SECRET")


def watch_model() -> str:
    return get_env("STUDIO_WATCH_MODEL") or DEFAULT_WATCH_MODEL


def sam_fps(override: float | None = None) -> float:
    """Frame rate of the clip sent to SAM: explicit override, else STUDIO_SAM_FPS, else 10. Must be > 0."""
    raw = override if override is not None else get_env("STUDIO_SAM_FPS")
    try:
        fps = float(raw) if raw is not None else float(DEFAULT_SAM_FPS)
    except ValueError:
        raise ValueError(f"STUDIO_SAM_FPS must be a number, got {raw!r}") from None
    if not 0 < fps <= 60:
        raise ValueError(f"SAM fps must be between 0 and 60, got {fps:g}")
    return fps


def work_dir() -> Path:
    """Where run outputs go: ../samples/studio_runs relative to backend/ (gitignored via samples/)."""
    override = get_env("STUDIO_WORK_DIR")
    return Path(override).expanduser().resolve() if override else REPO_DIR / "samples" / "studio_runs"


def redact(text: str) -> str:
    """Remove any configured API key value from text before it is logged or stored."""
    for key in (meta_key(), groq_key(), gemini_key(), *acr_credentials()[1:]):
        if key:
            text = text.replace(key, "***")
    return text
