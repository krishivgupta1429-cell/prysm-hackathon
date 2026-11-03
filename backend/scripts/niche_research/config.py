"""Env-based configuration for the niche research tool.

Loads .env (next to this file or in CWD) and exposes a typed Settings object.
All values have sane defaults — the only thing strictly required is
APIFY_API_TOKEN (for Instagram scraping).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

try:
    from dotenv import load_dotenv
    _HAS_DOTENV = True
except ImportError:
    _HAS_DOTENV = False


def _load_env() -> None:
    if not _HAS_DOTENV:
        return
    here = Path(__file__).resolve().parent
    for candidate in (here / ".env", Path.cwd() / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)
            return


def _env(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    return v if v is not None and v != "" else default


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key, str(default)).lower().strip()
    return raw in ("1", "true", "yes", "y", "on")


@dataclass(frozen=True)
class Settings:
    # LLM
    llm_backend: Literal["ollama", "vllm"]
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    llm_temperature: float
    llm_max_tokens: int
    llm_timeout_seconds: int

    # Acquisition
    apify_api_token: str
    yt_dlp_bin: str
    work_dir: Path

    # Transcription
    transcribe_backend: Literal["groq", "local_whisper", "skip"]
    groq_api_key: str
    whisper_cpp_bin: str
    whisper_cpp_model: str

    # Frames
    max_frames: int
    frame_width: int

    # Pipeline
    max_parallel: int
    resume: bool
    results_dir: Path


def load_settings() -> Settings:
    _load_env()

    backend = _env("LLM_BACKEND", "ollama").lower()
    if backend not in ("ollama", "vllm"):
        backend = "ollama"

    transcribe = _env("TRANSCRIBE_BACKEND", "groq").lower()
    if transcribe not in ("groq", "local_whisper", "skip"):
        transcribe = "groq"

    here = Path(__file__).resolve().parent
    results_dir_raw = _env("RESULTS_DIR", "results")
    results_dir = Path(results_dir_raw)
    if not results_dir.is_absolute():
        results_dir = here / results_dir
    results_dir.mkdir(parents=True, exist_ok=True)

    work_dir = Path(_env("WORK_DIR", "/tmp/prysm_niche_research"))
    work_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        llm_backend=backend,  # type: ignore[arg-type]
        llm_base_url=_env("LLM_BASE_URL", "http://localhost:11434/v1"),
        llm_model=_env("LLM_MODEL", "qwen2.5vl:7b"),
        llm_api_key=_env("LLM_API_KEY", "ollama"),
        llm_temperature=_env_float("LLM_TEMPERATURE", 0.2),
        llm_max_tokens=_env_int("LLM_MAX_TOKENS", 4096),
        llm_timeout_seconds=_env_int("LLM_TIMEOUT_SECONDS", 180),
        apify_api_token=_env("APIFY_API_TOKEN", ""),
        yt_dlp_bin=_env("YT_DLP_BIN", "yt-dlp"),
        work_dir=work_dir,
        transcribe_backend=transcribe,  # type: ignore[arg-type]
        groq_api_key=_env("GROQ_API_KEY", ""),
        whisper_cpp_bin=_env("WHISPER_CPP_BIN", "whisper-cli"),
        whisper_cpp_model=_env("WHISPER_CPP_MODEL", ""),
        max_frames=_env_int("MAX_FRAMES", 10),
        frame_width=_env_int("FRAME_WIDTH", 512),
        max_parallel=_env_int("MAX_PARALLEL", 2),
        resume=_env_bool("RESUME", True),
        results_dir=results_dir,
    )


SETTINGS = load_settings()
