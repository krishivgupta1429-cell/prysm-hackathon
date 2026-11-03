"""
app/services/clip_generation_service.py
Automatic Clip Generation – Feature #8

Accepts a long-form video (5min – 2hr).
Pipeline:
  1. Transcribe with Whisper
  2. Segment into candidate clips (15–60s)
  3. Score each candidate (engagement potential)
  4. Select top N clips
  5. Export via FFmpeg with optional caption burn-in
  6. Return clip URLs + metadata

Caption styles:
  tiktok  – large white text, black outline, bottom third
  minimal – small white text, top left
  bold    – yellow/black contrasting, centre
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import clips_generated_total
from app.services.ai_service import _chat, _parse_json
from app.services.video_service import extract_audio, extract_video_metadata
from app.services.transcription_service import transcribe, build_timed_segments

logger = get_logger("clip_generation")


# ── Caption style presets ─────────────────────────────────────────────────────

CAPTION_STYLES = {
    "tiktok": {
        "fontsize":  48,
        "fontcolor": "white",
        "borderw":   3,
        "bordercolor": "black",
        "box":       0,
        "y_pos":     "h*0.75",
        "font":      "Arial-Bold",
    },
    "minimal": {
        "fontsize":  28,
        "fontcolor": "white@0.9",
        "borderw":   2,
        "bordercolor": "black@0.7",
        "box":       0,
        "y_pos":     "h*0.85",
        "font":      "Arial",
    },
    "bold": {
        "fontsize":  52,
        "fontcolor": "yellow",
        "borderw":   4,
        "bordercolor": "black",
        "box":       1,
        "box_color": "black@0.4",
        "y_pos":     "h/2",
        "font":      "Arial-Bold",
    },
}


# ── Main entry point ──────────────────────────────────────────────────────────

async def generate_clips(
    media_id: str,
    video_path: str,
    user_id: str,
    max_clips: int = settings.CLIP_DEFAULT_MAX,
    min_sec: float = settings.CLIP_MIN_DURATION_SEC,
    max_sec: float = settings.CLIP_MAX_DURATION_SEC,
    add_captions: bool = True,
    caption_style: str = "tiktok",
) -> List[Dict[str, Any]]:
    """
    Full clip generation pipeline.
    Returns list of clip metadata dicts with storage paths and scores.
    """
    logger.info("Starting clip generation for media_id=%s", media_id)

    # Step 1: Get video metadata
    meta = await extract_video_metadata(video_path)
    total_duration = meta.get("duration_sec") or 0

    if total_duration < min_sec * 2:
        raise ValueError(f"Video too short ({total_duration:.0f}s) for clip generation.")

    # Step 2: Extract audio and transcribe
    audio_path = await extract_audio(video_path)
    whisper_result = await transcribe(audio_path, settings.WHISPER_MODEL, settings.WHISPER_DEVICE)
    transcript = whisper_result.get("text", "")
    segments   = build_timed_segments(whisper_result.get("segments", []))

    # Step 3: Identify candidate clip windows using AI
    candidates = await _identify_clip_candidates(
        transcript, segments, total_duration, min_sec, max_sec
    )

    if not candidates:
        # Fallback: evenly spaced clips
        candidates = _generate_fallback_candidates(total_duration, max_clips, min_sec, max_sec)

    # Step 4: Score and rank candidates
    candidates = _rank_candidates(candidates)
    top_clips = candidates[:max_clips]

    # Step 5: Export each clip
    output_dir = Path(video_path).parent / "clips" / media_id
    output_dir.mkdir(parents=True, exist_ok=True)

    exported = []
    for i, clip in enumerate(top_clips):
        clip_path = await _export_clip(
            video_path=video_path,
            start=clip["start"],
            end=clip["end"],
            output_path=str(output_dir / f"clip_{i+1:02d}.mp4"),
            caption=clip.get("caption") if add_captions else None,
            style=caption_style,
        )
        if clip_path:
            clips_generated_total.inc()
            exported.append({
                "clip_number":  i + 1,
                "start":        clip["start"],
                "end":          clip["end"],
                "duration_sec": round(clip["end"] - clip["start"], 1),
                "score":        clip.get("score", 0),
                "caption":      clip.get("caption"),
                "title":        clip.get("title"),
                "hook_type":    clip.get("hook_type"),
                "storage_path": clip_path,
                "storage_url":  _path_to_url(clip_path),
            })

    logger.info("Generated %d clips for media_id=%s", len(exported), media_id)
    return exported


# ── AI candidate identification ───────────────────────────────────────────────

async def _identify_clip_candidates(
    transcript: str,
    segments: List[Dict],
    duration: float,
    min_sec: float,
    max_sec: float,
) -> List[Dict[str, Any]]:
    """
    Ask Claude to identify the best clip windows from the transcript.
    Returns list of {start, end, score, reason, caption, title, hook_type}.
    """
    # Build transcript with timestamps
    seg_text = "\n".join(
        f"[{s['start']:.1f}s] {s['text']}"
        for s in segments[:60]   # first 60 segments / ~3 min
    )

    system = (
        "You are a video editor specialised in creating viral short-form clips from "
        "long-form content. Identify the most engaging segments. Respond ONLY with valid JSON."
    )
    user = f"""
Full video duration: {duration:.0f} seconds
Minimum clip length: {min_sec:.0f}s
Maximum clip length: {max_sec:.0f}s

Transcript with timestamps:
{seg_text}

Identify up to 8 clip candidates. Prioritise:
1. Strong hooks (first few words are gripping)
2. High-value insights or reveals
3. Emotional peaks or stories
4. Self-contained moments (beginning + end make sense alone)

For each clip, generate a short caption optimised for TikTok/Reels.

Return JSON array:
[
  {{
    "start":     <float seconds>,
    "end":       <float seconds>,
    "score":     <int 0-100>,
    "title":     "<short clip title>",
    "caption":   "<TikTok-style caption with emojis>",
    "hook_type": "<question|bold_claim|story|insight|reveal>",
    "reason":    "<why this clip will perform well>"
  }}
]
"""
    raw = await _chat(system, user, max_tokens=1500)
    data = _parse_json(raw)
    if not isinstance(data, list):
        return []

    # Validate and clamp timestamps
    valid = []
    for c in data:
        start = max(0.0, float(c.get("start", 0)))
        end   = min(duration, float(c.get("end", start + max_sec)))
        clip_dur = end - start
        if min_sec <= clip_dur <= max_sec:
            valid.append({**c, "start": start, "end": end})
    return valid


def _generate_fallback_candidates(
    duration: float,
    count: int,
    min_sec: float,
    max_sec: float,
) -> List[Dict[str, Any]]:
    """Generate evenly-spaced clip windows as a fallback."""
    clip_dur = min(max_sec, max(min_sec, duration / (count + 1)))
    candidates = []
    for i in range(count):
        start = i * (duration / count)
        end   = min(duration, start + clip_dur)
        candidates.append({
            "start": round(start, 1),
            "end":   round(end, 1),
            "score": 50,
            "title": f"Highlight {i+1}",
            "caption": f"Part {i+1} 🎬",
            "hook_type": "story",
            "reason": "Auto-generated fallback clip",
        })
    return candidates


def _rank_candidates(candidates: List[Dict]) -> List[Dict]:
    """Sort by score descending, then by start time to avoid heavy overlap."""
    candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    selected = []
    for c in candidates:
        # Avoid clips that heavily overlap with already selected ones
        overlap = any(
            _overlap_ratio(c["start"], c["end"], s["start"], s["end"]) > 0.4
            for s in selected
        )
        if not overlap:
            selected.append(c)
    return selected


def _overlap_ratio(s1, e1, s2, e2) -> float:
    overlap = max(0, min(e1, e2) - max(s1, s2))
    union   = max(e1, e2) - min(s1, s2)
    return overlap / union if union > 0 else 0


# ── FFmpeg clip export ────────────────────────────────────────────────────────

async def _export_clip(
    video_path: str,
    start: float,
    end: float,
    output_path: str,
    caption: Optional[str],
    style: str,
) -> Optional[str]:
    """
    Cut a clip with FFmpeg and optionally burn in a caption.
    Returns output path or None on failure.
    """
    duration = end - start
    vf_filters = []

    # Caption burn-in using drawtext filter
    if caption:
        cfg = CAPTION_STYLES.get(style, CAPTION_STYLES["tiktok"])
        # Sanitise caption for FFmpeg (escape special chars)
        safe_caption = _sanitise_drawtext(caption[:80])
        vf_filters.append(
            f"drawtext=text='{safe_caption}'"
            f":fontsize={cfg['fontsize']}"
            f":fontcolor={cfg['fontcolor']}"
            f":borderw={cfg['borderw']}"
            f":bordercolor={cfg['bordercolor']}"
            f":x=(w-text_w)/2"
            f":y={cfg['y_pos']}"
            f":font={cfg['font']}"
        )

    vf_arg = ",".join(vf_filters) if vf_filters else "null"

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        "-vf", vf_arg,
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        logger.error("FFmpeg clip export failed: %s", stderr.decode()[:300])
        return None

    logger.info("Exported clip: %s (%.1fs – %.1fs)", output_path, start, end)
    return output_path


def _sanitise_drawtext(text: str) -> str:
    """Escape characters that break FFmpeg drawtext."""
    return (
        text
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _path_to_url(path: str) -> str:
    if settings.CDN_BASE_URL:
        rel = path.replace(settings.LOCAL_MEDIA_PATH, "").lstrip("/")
        return f"{settings.CDN_BASE_URL}/{rel}"
    return f"/media/files/{Path(path).name}"
