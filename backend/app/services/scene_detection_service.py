"""
app/services/scene_detection_service.py
Detects scene cuts in a video using FFmpeg's 'select' filter.
Returns list of scene timestamps and optionally extracts keyframes.
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Scene detection threshold: 0.0 (very sensitive) → 1.0 (very insensitive)
DEFAULT_THRESHOLD = 0.3


async def detect_scenes(
    video_path: str,
    threshold: float = DEFAULT_THRESHOLD,
) -> List[dict]:
    """
    Run ffmpeg scene detection and return list of scene boundary dicts.
    Each dict: { "timestamp": float, "score": float }
    """
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-vsync", "vfr",
        "-f", "null",
        "-",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    scenes = _parse_showinfo(stderr.decode())
    logger.info("Detected %d scene changes in %s", len(scenes), video_path)
    return scenes


def _parse_showinfo(output: str) -> List[dict]:
    """
    Parse ffmpeg showinfo filter output for scene timestamps.
    Lines of interest contain 'pts_time:' followed by the timestamp.
    """
    scenes = []
    pattern = re.compile(r"pts_time:([\d.]+).*scene_score:([\d.]+)", re.IGNORECASE)

    for line in output.splitlines():
        if "pts_time" not in line:
            continue
        # Alternative simple parser
        pts_match = re.search(r"pts_time:([\d.]+)", line)
        score_match = re.search(r"scene_score:([\d.]+)", line)
        if pts_match:
            scenes.append({
                "timestamp": round(float(pts_match.group(1)), 3),
                "score":     round(float(score_match.group(1)), 3) if score_match else 0.0,
            })

    return scenes


async def extract_keyframes(
    video_path: str,
    timestamps: List[float],
    output_dir: Optional[str] = None,
) -> List[str]:
    """
    Extract frame images at given timestamps.
    Returns list of saved image paths.
    """
    if output_dir is None:
        output_dir = str(Path(video_path).parent / "frames")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    saved_paths = []
    for ts in timestamps:
        out_path = f"{output_dir}/frame_{ts:.3f}.jpg"
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(ts),
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            out_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if Path(out_path).exists():
            saved_paths.append(out_path)

    logger.info("Extracted %d keyframes from %s", len(saved_paths), video_path)
    return saved_paths


def suggest_thumbnail_timestamps(
    scenes: List[dict],
    duration: float,
    max_suggestions: int = 5,
) -> List[float]:
    """
    Select the most visually distinct timestamps for thumbnail suggestions.
    Prioritises high scene-change scores and avoids the first 1 second.
    """
    candidates = [s for s in scenes if s["timestamp"] > 1.0]
    candidates.sort(key=lambda x: x["score"], reverse=True)
    top = candidates[:max_suggestions]
    return sorted([t["timestamp"] for t in top])
