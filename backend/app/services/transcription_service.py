"""
app/services/transcription_service.py
Speech-to-text using Groq Whisper (fast, accurate, multilingual).

Architecture:
  - FFmpeg extracts audio from video as MP3
  - Audio sent to Groq Whisper for transcription
  - Returns timestamped segments compatible with pipeline
"""

import asyncio
import json
import logging
import tempfile
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("transcription_service")

_groq = None


def _get_groq():
    global _groq
    if _groq is None:
        from groq import Groq
        _groq = Groq(api_key=settings.GROQ_API_KEY)
    return _groq


async def extract_audio(video_path: str) -> str:
    """Extract audio from video as MP3 using FFmpeg."""
    tmp_dir = tempfile.mkdtemp(prefix="prysm_audio_")
    audio_path = os.path.join(tmp_dir, "audio.mp3")

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "libmp3lame",
        "-ar", "16000", "-ac", "1",
        "-b:a", "64k",
        audio_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed: {stderr.decode()[:200]}")

    return audio_path


async def transcribe(
    audio_path: str,
    model_name: str = "whisper-large-v3-turbo",
    device: str = "cpu",  # kept for API compatibility, unused
) -> Dict[str, Any]:
    """
    Transcribe audio using Groq Whisper.
    Returns dict with keys: text, segments, language — same interface as before.
    """
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    import asyncio
    loop = asyncio.get_event_loop()

    def _transcribe_sync():
        groq = _get_groq()
        with open(audio_path, "rb") as f:
            result = groq.audio.transcriptions.create(
                file=("audio.mp3", f),
                model="whisper-large-v3-turbo",
                response_format="verbose_json",
                timestamp_granularities=["segment", "word"],
            )
        return result

    try:
        result = await loop.run_in_executor(None, _transcribe_sync)

        text = result.text or ""
        language = getattr(result, "language", "unknown") or "unknown"
        raw_segments = getattr(result, "segments", []) or []
        raw_words = getattr(result, "words", []) or []

        segments = []
        for seg in raw_segments:
            seg_start = getattr(seg, "start", 0)
            seg_end = getattr(seg, "end", 0)
            seg_text = getattr(seg, "text", "").strip()
            seg_words = [
                {"word": getattr(w, "word", ""), "start": getattr(w, "start", 0), "end": getattr(w, "end", 0)}
                for w in raw_words
                if getattr(w, "start", 0) >= seg_start and getattr(w, "end", 0) <= seg_end
            ]
            segments.append({
                "start": seg_start,
                "end": seg_end,
                "text": seg_text,
                "words": seg_words,
            })

        logger.info(
            "Transcription complete: %d chars, %d segments, lang=%s",
            len(text), len(segments), language,
        )
        return {"text": text, "segments": segments, "language": language}

    except Exception as exc:
        logger.warning("Groq transcription failed: %s — returning empty transcript", exc)
        return {"text": "", "segments": [], "language": "unknown"}


def extract_hook_transcript(segments: List[dict], hook_duration: float = 3.0) -> str:
    """Extract transcript text from the first `hook_duration` seconds."""
    hook_words = []
    for seg in segments:
        if seg.get("start", 0) <= hook_duration:
            words = seg.get("words", [])
            if words:
                hook_words.extend(
                    w["word"] for w in words
                    if w.get("start", 0) <= hook_duration
                )
            else:
                hook_words.append(seg.get("text", ""))
    return " ".join(hook_words).strip()


def build_timed_segments(segments: List[dict]) -> List[dict]:
    """Convert segment dicts to clean list for storage."""
    return [
        {
            "start": round(s.get("start", 0), 2),
            "end": round(s.get("end", 0), 2),
            "text": s.get("text", "").strip(),
        }
        for s in segments
    ]
