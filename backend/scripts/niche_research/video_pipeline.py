"""Video acquisition + media extraction.

Re-implements the parts of the live backend's pipeline that we need for
research, but standalone — no FastAPI / Celery / DB dependencies.

Pipeline per video:
  1. Resolve metadata via Apify (Instagram) or yt-dlp (everything else)
  2. Download .mp4 to WORK_DIR
  3. Extract frames with FFmpeg (hook frames + evenly-spaced body)
  4. Extract audio to mp3 + transcribe (Groq, local whisper.cpp, or skip)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .config import SETTINGS

# Cookies file lives alongside this script (same directory)
_COOKIES_FILE = Path(__file__).resolve().parent / "instagram_cookies.txt"

# Lazy instaloader import — only used for Instagram URLs
_instaloader = None

def _load_instaloader_cookies(IL) -> None:
    """Inject Netscape cookie file into instaloader's requests session."""
    if not _COOKIES_FILE.exists():
        return
    try:
        with open(_COOKIES_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, _flag, path_, _secure, _expiry, name, value = parts[:7]
                IL.context._session.cookies.set(name, value, domain=domain, path=path_)
        log.info("Loaded Instagram session cookies into instaloader from %s", _COOKIES_FILE)
    except Exception as e:
        log.warning("Failed to load instaloader cookies: %s", e)

def _get_instaloader():
    global _instaloader
    if _instaloader is None:
        try:
            import instaloader
            _instaloader = instaloader.Instaloader(
                download_videos=False,
                download_video_thumbnails=False,
                download_geotags=False,
                download_comments=False,
                save_metadata=False,
                compress_json=False,
                post_metadata_txt_pattern="",
                quiet=True,
            )
            _load_instaloader_cookies(_instaloader)
        except ImportError:
            log.warning("instaloader not installed — Instagram downloads may fail")
    return _instaloader

log = logging.getLogger(__name__)


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class VideoAssets:
    url: str
    niche: str
    cache_key: str
    work_dir: Path
    video_path: Optional[Path] = None
    audio_path: Optional[Path] = None
    frames: List[Dict[str, Any]] = field(default_factory=list)  # {"path": Path, "timestamp": float}
    metadata: Dict[str, Any] = field(default_factory=dict)
    transcript: str = ""
    duration_sec: float = 0.0
    error: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _cache_key(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _is_instagram(url: str) -> bool:
    return "instagram.com" in url.lower()


def _run(cmd: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


# ── 1. Metadata via instaloader (Instagram) or yt-dlp ───────────────────────

def fetch_metadata(url: str, prefilled: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Fetch metadata for a video URL.

    For Instagram URLs, uses instaloader to get the CDN video_url directly
    (no Apify credits needed). If `prefilled` is provided (e.g. from CSV),
    those values are merged in — the CDN URL is always fetched fresh.
    """
    if _is_instagram(url):
        meta = _instaloader_instagram(url)
        if meta:
            if prefilled:
                # For numeric fields: use prefilled (CSV) value when instaloader returns 0/empty.
                # Keep instaloader's _videoUrl since it's a fresh CDN link.
                for field in ("uploader", "viewCount", "likeCount", "commentCount", "duration"):
                    if not meta.get(field) and prefilled.get(field):
                        meta[field] = prefilled[field]
            return meta
        if prefilled:
            log.warning("instaloader failed for %s — using prefilled metadata only", url)
            return prefilled
        log.warning("instaloader failed for %s", url)
        return {}

    # yt-dlp for non-Instagram
    try:
        cmd = [SETTINGS.yt_dlp_bin, "--dump-json", "--no-download", "--no-playlist", url]
        proc = _run(cmd, timeout=60)
        if proc.returncode != 0:
            log.warning("yt-dlp metadata failed: %s", proc.stderr.decode()[:200])
            return prefilled or {}
        data = json.loads(proc.stdout.decode())
        caption = (data.get("description") or "")[:2000]
        return {
            "uploader": data.get("uploader") or data.get("uploader_id") or "",
            "caption": caption,
            "hashtags": re.findall(r"#(\w+)", caption),
            "viewCount": data.get("view_count") or 0,
            "likeCount": data.get("like_count") or 0,
            "commentCount": data.get("comment_count") or 0,
            "duration": float(data.get("duration") or 0),
            "uploadDate": data.get("upload_date", ""),
        }
    except Exception as e:
        log.warning("yt-dlp metadata exception: %s", e)
        return prefilled or {}


def _instaloader_instagram(url: str) -> Dict[str, Any]:
    """Use instaloader to get Instagram post metadata + CDN video_url."""
    try:
        import instaloader
        IL = _get_instaloader()
        if IL is None:
            return {}

        # Extract shortcode from URL  (/p/XXXXX/, /reel/XXXXX/, or /reels/XXXXX/)
        m = re.search(r"/(?:p|reels?|tv)/([A-Za-z0-9_-]+)", url)
        if not m:
            log.warning("Can't extract shortcode from: %s", url)
            return {}
        shortcode = m.group(1)

        post = instaloader.Post.from_shortcode(IL.context, shortcode)
        caption = (post.caption or "")[:2000]
        video_url = post.video_url or ""
        return {
            "uploader": post.owner_username or "",
            "caption": caption,
            "hashtags": re.findall(r"#(\w+)", caption),
            "viewCount": post.video_view_count or 0,
            "likeCount": post.likes or 0,
            "commentCount": post.comments or 0,
            "duration": 0.0,  # instaloader doesn't expose duration easily
            "uploadDate": post.date_utc.strftime("%Y%m%d") if post.date_utc else "",
            "_videoUrl": video_url,
        }
    except Exception as e:
        log.warning("instaloader exception for %s: %s", url, e)
        return {}


# ── 2. Download video ────────────────────────────────────────────────────────

def download_video(assets: VideoAssets) -> Optional[Path]:
    out = assets.work_dir / f"{assets.cache_key}.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out

    direct_url = assets.metadata.get("_videoUrl")
    if direct_url:
        try:
            with requests.get(direct_url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(out, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        f.write(chunk)
            if out.stat().st_size > 0:
                return out
        except Exception as e:
            log.warning("Direct video download failed (%s); trying yt-dlp", e)

    # yt-dlp fallback
    try:
        cmd = [SETTINGS.yt_dlp_bin, "-f", "best[ext=mp4]/best", "--no-playlist"]
        if _COOKIES_FILE.exists():
            cmd += ["--cookies", str(_COOKIES_FILE)]
        cmd += ["-o", str(out), assets.url]
        proc = _run(cmd, timeout=300)
        if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return out
        log.warning("yt-dlp download failed: %s", proc.stderr.decode()[:200])
    except Exception as e:
        log.warning("yt-dlp download exception: %s", e)
    return None


# ── 3. FFmpeg: probe + frames + audio ────────────────────────────────────────

def probe_duration(video_path: Path) -> float:
    try:
        proc = _run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)
        ], timeout=20)
        return float(proc.stdout.decode().strip() or 0)
    except Exception:
        return 0.0


def extract_frames(assets: VideoAssets) -> List[Dict[str, Any]]:
    if not assets.video_path:
        return []
    duration = assets.duration_sec or probe_duration(assets.video_path)
    if duration <= 0:
        return []

    max_f = SETTINGS.max_frames

    # Always allocate at least 1 body slot so we cover the full video.
    # Hook zone: up to min(3, max_f-1) frames at 0s, 1.5s, 3s.
    # Body zone: remaining slots evenly spread from 4s to near-end.
    if max_f <= 1:
        timestamps = [0.0]
    elif max_f == 2:
        timestamps = [0.0, round(duration * 0.7, 2)]
    else:
        n_hook = min(3, max_f - 1)
        n_body = max_f - n_hook
        hook_ts = [t for t in (0.0, 1.5, 3.0)[:n_hook] if t < duration]
        body_start = min(4.0, duration * 0.25)
        body_end = max(body_start + 0.5, duration - 0.5)
        step = (body_end - body_start) / max(n_body, 1)
        body_ts = [round(body_start + step * i, 2) for i in range(n_body)]
        timestamps = sorted(set(t for t in hook_ts + body_ts if 0 <= t < duration))

    timestamps = timestamps[:max_f]

    frames_dir = assets.work_dir / f"frames_{assets.cache_key}"
    frames_dir.mkdir(exist_ok=True)

    out: List[Dict[str, Any]] = []
    for i, ts in enumerate(timestamps):
        frame_path = frames_dir / f"frame_{i:02d}.jpg"
        if not frame_path.exists():
            try:
                _run([
                    "ffmpeg", "-y", "-ss", str(ts), "-i", str(assets.video_path),
                    "-frames:v", "1", "-vf", f"scale={SETTINGS.frame_width}:-1",
                    "-q:v", "3", str(frame_path),
                ], timeout=20)
            except Exception as e:
                log.warning("Frame extract failed at %ss: %s", ts, e)
                continue
        if frame_path.exists() and frame_path.stat().st_size > 0:
            out.append({"path": frame_path, "timestamp": ts})
    log.info("Extracted %d/%d frames for %s", len(out), len(timestamps), assets.cache_key)
    return out


def extract_audio(assets: VideoAssets) -> Optional[Path]:
    if not assets.video_path:
        return None
    out = assets.work_dir / f"{assets.cache_key}.mp3"
    if out.exists() and out.stat().st_size > 0:
        return out
    try:
        proc = _run([
            "ffmpeg", "-y", "-i", str(assets.video_path), "-vn",
            "-acodec", "libmp3lame", "-ab", "96k", "-ar", "16000", "-ac", "1", str(out),
        ], timeout=60)
        if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return out
    except Exception as e:
        log.warning("Audio extract failed: %s", e)
    return None


# ── 4. Transcription ─────────────────────────────────────────────────────────

def transcribe(audio_path: Path) -> str:
    backend = SETTINGS.transcribe_backend
    if backend == "skip" or not audio_path or not audio_path.exists():
        return ""

    if backend == "groq":
        if not SETTINGS.groq_api_key:
            log.warning("Groq selected but no GROQ_API_KEY set — skipping transcript")
            return ""
        try:
            from groq import Groq
            client = Groq(api_key=SETTINGS.groq_api_key)
            with open(audio_path, "rb") as f:
                resp = client.audio.transcriptions.create(
                    file=(audio_path.name, f.read()),
                    model="whisper-large-v3-turbo",
                )
            return getattr(resp, "text", "") or ""
        except Exception as e:
            log.warning("Groq transcribe failed: %s", e)
            return ""

    if backend == "local_whisper":
        bin_ = SETTINGS.whisper_cpp_bin
        model = SETTINGS.whisper_cpp_model
        if not model:
            log.warning("local_whisper selected but WHISPER_CPP_MODEL not set")
            return ""
        try:
            proc = _run([bin_, "-m", model, "-f", str(audio_path), "-otxt", "-of",
                         str(audio_path.with_suffix(""))], timeout=180)
            txt_path = audio_path.with_suffix(".txt")
            if txt_path.exists():
                return txt_path.read_text().strip()
            log.warning("whisper.cpp produced no output: %s", proc.stderr.decode()[:200])
        except Exception as e:
            log.warning("whisper.cpp failed: %s", e)
    return ""


# ── Orchestrator ─────────────────────────────────────────────────────────────

def prepare_video(url: str, niche: str,
                  prefilled_metadata: Optional[Dict[str, Any]] = None) -> VideoAssets:
    """Run the whole pipeline for a single URL — returns assembled assets.

    prefilled_metadata: values from the CSV row (uploader, view_count, etc.)
    that are merged into the fetched metadata so we skip redundant API calls.
    """
    cache_key = _cache_key(url)
    work_dir = SETTINGS.work_dir / cache_key
    work_dir.mkdir(parents=True, exist_ok=True)

    assets = VideoAssets(url=url, niche=niche, cache_key=cache_key, work_dir=work_dir)

    log.info("[%s] fetching metadata …", cache_key)
    assets.metadata = fetch_metadata(url, prefilled=prefilled_metadata)
    assets.duration_sec = float(assets.metadata.get("duration") or 0)

    log.info("[%s] downloading video …", cache_key)
    assets.video_path = download_video(assets)
    if not assets.video_path:
        assets.error = "video_download_failed"
        return assets

    if assets.duration_sec == 0:
        assets.duration_sec = probe_duration(assets.video_path)

    log.info("[%s] extracting frames + audio …", cache_key)
    assets.frames = extract_frames(assets)
    assets.audio_path = extract_audio(assets)

    if assets.audio_path and SETTINGS.transcribe_backend != "skip":
        log.info("[%s] transcribing …", cache_key)
        assets.transcript = transcribe(assets.audio_path)

    return assets
