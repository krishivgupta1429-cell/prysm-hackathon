"""Built during the Meta Global AI Developer Hackathon (new work).

Auto-edit settings. Read from backend/.env or the process environment at call time (so tests can monkeypatch), never logged.
"""
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from app.studio import config as studio_config

PKG_DIR = Path(__file__).resolve().parent
FONTS_DIR = PKG_DIR / "fonts"
FONT_NAME = "Poppins"  # OFL; covers Latin and Devanagari, so Hindi / Hinglish captions render and shape correctly

MAX_SOURCE_SECONDS = 90
MAX_OPS = 24  # the planner adds at most 8 (PLANNER_MAX_OPS); the rest are the system's own dead-air / filler / caption / loudness ops
PLANNER_MAX_OPS = 8
MAX_REMOVED_FRACTION = 0.25
MAX_RESULT_SECONDS = 90
MAX_REVISIONS = 5
OUT_W, OUT_H = 1080, 1920
# Instagram's UI covers the top ~14 % (header) and the bottom ~20 % (caption / handle) and a button column on the right.
SAFE = {"x0": 80, "x1": 960, "y0": 290, "y1": 1500}
CUT_PAD_MIN, CUT_PAD_MAX = 0.06, 0.12
XFADE_S = 0.04
ZOOM_MIN_GAP_S = 2.5
SNAP_TOLERANCE_S = 0.25
START_LIMIT_PER_HOUR = 6


def env(name: str, default: str | None = None) -> str | None:
    value = studio_config.get_env(name)
    return value if value is not None else default


def enabled() -> bool:
    return (env("AUTO_EDIT_ENABLED", "0") or "0").strip().lower() in ("1", "true", "yes", "on")


def planner_model() -> str:
    return env("AUTO_EDIT_PLANNER_MODEL", "muse-spark-1.3")


def critic_model() -> str:
    return env("AUTO_EDIT_CRITIC_MODEL", "muse-wonder-1.0")


def planner_effort() -> str | None:
    """reasoning_effort sent with the planner call (the Meta API accepts minimal | low | medium | high): `low` cut the plan from ~80 s to ~20 s."""
    return env("AUTO_EDIT_PLANNER_EFFORT", "low") or None


def critic_effort() -> str | None:
    """reasoning_effort for the critic: `low` took ~33 s on a 54 s video where the default took ~75 s, with the same verdicts."""
    return env("AUTO_EDIT_CRITIC_EFFORT", "low") or None


def max_cost_usd() -> float:
    try:
        return float(env("AUTO_EDIT_MAX_COST_USD", "1.50"))
    except ValueError:
        return 1.50


def retention_days() -> float:
    try:
        return float(env("AUTO_EDIT_RETENTION_DAYS", "7"))
    except ValueError:
        return 7.0


def critic_required() -> bool:
    return (env("AUTO_EDIT_CRITIC_REQUIRED", "1") or "1").strip().lower() in ("1", "true", "yes", "on")


def storage_dir() -> Path:
    d = env("AUTO_EDIT_STORAGE_DIR")
    return Path(d).expanduser().resolve() if d else studio_config.BACKEND_DIR / "data" / "auto_edit"


def _has_filter(binary: str, name: str) -> bool:
    try:
        out = subprocess.run([binary, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return False
    return any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    """An ffmpeg that has the libass `ass` filter: AUTO_EDIT_FFMPEG, then the one on PATH, then the static build from imageio-ffmpeg."""
    candidates = []
    if env("AUTO_EDIT_FFMPEG"):
        candidates.append(env("AUTO_EDIT_FFMPEG"))
    if shutil.which("ffmpeg"):
        candidates.append(shutil.which("ffmpeg"))
    try:
        import imageio_ffmpeg
        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    for c in candidates:
        if c and _has_filter(c, "ass"):
            return c
    raise RuntimeError("no ffmpeg with libass found: set AUTO_EDIT_FFMPEG or pip install imageio-ffmpeg")


@lru_cache(maxsize=1)
def ffprobe_bin() -> str:
    return shutil.which("ffprobe") or "ffprobe"
