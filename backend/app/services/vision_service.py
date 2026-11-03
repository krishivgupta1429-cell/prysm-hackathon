"""
app/services/vision_service.py
Prysm Vision — Claude-only multimodal pipeline.

Architecture:
  1. FFmpeg extracts frames at 1fps (hook window at 2fps)
  2. Frames resized to 768px, encoded as base64 JPEG
  3. Claude Vision analyses frames directly (no GPT-4o)
  4. Returns VideoFrameSummary — same interface as original
  5. Claude reasoning layer consumes summary unchanged

Claude sees the actual pixels. One model. No OpenAI dependency.
"""

import asyncio
import base64
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.studio.llm_switch import anthropic  # the anthropic package, or Muse Spark behind the same interface (AI_PROVIDER=meta)

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import vision_frames_processed, track_stage

logger = get_logger("vision_service")

_client: Optional[anthropic.AsyncAnthropic] = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=60.0, max_retries=1)
    return _client


# ── Data structures (unchanged from original) ────────────────────────────────

@dataclass
class FrameAnalysis:
    """Claude's understanding of a single video frame."""
    timestamp: float
    description: str
    faces_detected: bool
    face_emotion: str
    face_count: int
    on_screen_text: List[str]
    scene_type: str
    energy_level: str
    visual_hook_strength: float
    composition_notes: str
    is_hook_frame: bool = False


@dataclass
class VideoFrameSummary:
    total_frames_analyzed: int
    duration_sec: float
    sample_fps: float
    hook_frames: List[FrameAnalysis]
    hook_visual_description: str
    hook_has_face: bool
    hook_dominant_emotion: str
    all_frames: List[FrameAnalysis]
    dominant_scene_type: str
    energy_timeline: List[Dict]
    emotion_arc: List[Dict]
    on_screen_texts: List[Dict]
    face_presence_ratio: float
    thumbnail_candidates: List[Dict]
    scene_changes: List[float]
    avg_scene_duration_sec: float
    pacing_assessment: str
    visual_hook_moments: List[Dict]
    api_calls_made: int
    frames_per_call: int


def empty_summary(duration: float) -> VideoFrameSummary:
    return VideoFrameSummary(
        total_frames_analyzed=0, duration_sec=duration, sample_fps=0,
        hook_frames=[], hook_visual_description="Vision analysis not available.",
        hook_has_face=False, hook_dominant_emotion="unknown",
        all_frames=[], dominant_scene_type="unknown",
        energy_timeline=[], emotion_arc=[], on_screen_texts=[],
        face_presence_ratio=0.0, thumbnail_candidates=[],
        scene_changes=[], avg_scene_duration_sec=duration,
        pacing_assessment="unknown", visual_hook_moments=[],
        api_calls_made=0, frames_per_call=0,
    )


# ── Main entry point ──────────────────────────────────────────────────────────

async def analyze_video_frames(
    video_path: str,
    duration_sec: float,
) -> VideoFrameSummary:
    if not settings.VISION_ENABLED:
        logger.info("Vision disabled — skipping frame analysis.")
        return empty_summary(duration_sec)

    if not os.path.exists(video_path):
        logger.warning("Video not found at %s", video_path)
        return empty_summary(duration_sec)

    with track_stage("vision_analysis"):
        frames_dir, hook_dir = await _extract_frames(video_path, duration_sec)

        hook_frame_files = _sorted_frames(hook_dir)
        body_frame_files = _sorted_frames(frames_dir)

        if not hook_frame_files and not body_frame_files:
            return empty_summary(duration_sec)

        all_analyses: List[FrameAnalysis] = []
        api_calls = 0

        # Hook pass — first 3 seconds, analysed separately
        hook_analyses = []
        if hook_frame_files and settings.VISION_HOOK_DEDICATED_PASS:
            hook_analyses, calls = await _analyze_frame_batch(
                hook_frame_files,
                context_hint="These are the HOOK WINDOW frames — the critical first 3 seconds of the video.",
            )
            api_calls += calls
            all_analyses.extend(hook_analyses)
            for fa in hook_analyses:
                fa.is_hook_frame = True

        # Body frames
        if body_frame_files:
            body_analyses, calls = await _analyze_frame_batch(body_frame_files)
            api_calls += calls
            body_only = [fa for fa in body_analyses if fa.timestamp > 3.0]
            all_analyses.extend(body_only)

        all_analyses.sort(key=lambda x: x.timestamp)
        vision_frames_processed.inc(len(all_analyses))

        summary = _build_summary(all_analyses, hook_analyses, duration_sec, api_calls)
        _cleanup([frames_dir, hook_dir])

        logger.info(
            "Vision complete: %d frames, %d API calls, pacing=%s",
            len(all_analyses), api_calls, summary.pacing_assessment,
        )
        return summary


# ── Frame extraction (FFmpeg only) ───────────────────────────────────────────

async def _extract_frames(video_path: str, duration_sec: float):
    tmp = tempfile.mkdtemp(prefix="prysm_frames_")
    body_dir = os.path.join(tmp, "body")
    hook_dir = os.path.join(tmp, "hook")
    os.makedirs(body_dir, exist_ok=True)
    os.makedirs(hook_dir, exist_ok=True)

    fps = min(settings.VISION_SAMPLE_FPS, 1.0)
    hfps = settings.VISION_HOOK_FPS
    w = settings.VISION_FRAME_WIDTH
    max_duration = min(duration_sec, settings.VISION_MAX_FRAMES / fps)
    hook_end = min(3.0, duration_sec)

    body_cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"fps={fps},scale={w}:-1",
        "-t", str(max_duration),
        "-frame_pts", "1", "-q:v", "3",
        os.path.join(body_dir, "frame_%04d.jpg"),
    ]
    hook_cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"fps={hfps},scale={w}:-1",
        "-t", str(hook_end),
        "-q:v", "2",
        os.path.join(hook_dir, "frame_%04d.jpg"),
    ]

    await asyncio.gather(_run_ffmpeg(body_cmd), _run_ffmpeg(hook_cmd))
    return body_dir, hook_dir


async def _run_ffmpeg(cmd: List[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning("FFmpeg warning: %s", stderr.decode()[:200])


def _sorted_frames(directory: str) -> List[str]:
    if not os.path.exists(directory):
        return []
    files = sorted([
        os.path.join(directory, f)
        for f in os.listdir(directory) if f.endswith(".jpg")
    ])
    return files[:settings.VISION_MAX_FRAMES]


# ── Claude Vision API call (replaces GPT-4o) ─────────────────────────────────

async def _analyze_frame_batch(
    frame_files: List[str],
    context_hint: str = "",
) -> tuple[List[FrameAnalysis], int]:
    """
    Send frames to Claude Vision in batches.
    Claude directly analyses the images — no GPT-4o intermediary.
    """
    batch_size = settings.VISION_FRAMES_PER_BATCH
    all_results: List[FrameAnalysis] = []
    api_calls = 0

    batches = [frame_files[i:i+batch_size] for i in range(0, len(frame_files), batch_size)]

    for batch in batches:
        result, calls = await _call_claude_vision(batch, context_hint)
        all_results.extend(result)
        api_calls += calls
        if len(batches) > 1:
            await asyncio.sleep(0.3)

    return all_results, api_calls


async def _call_claude_vision(
    frame_files: List[str],
    context_hint: str,
) -> tuple[List[FrameAnalysis], int]:
    """
    Single Claude Vision API call with N frames.
    Claude returns structured JSON describing each frame.
    """
    content = []

    if context_hint:
        content.append({"type": "text", "text": context_hint})

    for i, fpath in enumerate(frame_files):
        frame_num = _extract_frame_num(fpath)
        content.append({
            "type": "text",
            "text": f"Frame {i+1} (approx {frame_num:.1f}s into video):",
        })
        b64 = _encode_image(fpath)
        if b64:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": b64,
                },
            })

    content.append({"type": "text", "text": _vision_prompt()})

    try:
        client = _get_client()
        response = await client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": content}],
        )
        raw = response.content[0].text
        # Strip markdown fences if present
        raw = re.sub(r"```json\n?", "", raw)
        raw = re.sub(r"```\n?", "", raw)
        parsed = json.loads(raw.strip())
        analyses = _parse_frame_analyses(parsed, frame_files)
        return analyses, 1

    except Exception as exc:
        logger.warning("Claude vision call failed: %s", exc)
        placeholders = [_placeholder_frame(_extract_frame_num(f)) for f in frame_files]
        return placeholders, 0


def _vision_prompt() -> str:
    return """Analyse each video frame above carefully and in sequence.

For each frame, return a JSON object in the "frames" array:
{
  "frame_index": <int, 1-based>,
  "timestamp_estimate": <float, seconds>,
  "description": "<one clear sentence: who, what, where, what's happening>",
  "faces_detected": <bool>,
  "face_count": <int>,
  "face_emotion": "<neutral|happy|surprised|angry|sad|excited|unknown>",
  "on_screen_text": ["<any visible text overlays, captions, or subtitles>"],
  "scene_type": "<talking_head|b_roll|text_only|product|outdoor|indoor|graphic>",
  "energy_level": "<low|medium|high>",
  "visual_hook_strength": <float 0-10, how attention-grabbing is this frame>,
  "composition_notes": "<e.g. direct eye contact, product held up, gesturing, looking away>"
}

Be specific and accurate. Describe exactly what you see.
Return ONLY: {"frames": [...]}"""


# ── Summary builder (unchanged logic) ────────────────────────────────────────

def _build_summary(
    all_frames: List[FrameAnalysis],
    hook_frames: List[FrameAnalysis],
    duration_sec: float,
    api_calls: int,
) -> VideoFrameSummary:
    if not all_frames:
        return empty_summary(duration_sec)

    hook_has_face = any(f.faces_detected for f in hook_frames)
    hook_emotions = [f.face_emotion for f in hook_frames if f.face_emotion != "unknown"]
    hook_dominant_emotion = max(set(hook_emotions), key=hook_emotions.count) if hook_emotions else "neutral"
    hook_desc = " | ".join(f.description for f in hook_frames[:3]) if hook_frames else "No hook frames"

    face_frames = sum(1 for f in all_frames if f.faces_detected)
    face_ratio = face_frames / len(all_frames) if all_frames else 0.0

    scene_types = [f.scene_type for f in all_frames]
    dominant_scene = max(set(scene_types), key=scene_types.count) if scene_types else "unknown"

    energy_timeline = [
        {"timestamp": f.timestamp, "energy": f.energy_level, "description": f.description[:60]}
        for f in all_frames
    ]
    emotion_arc = [
        {"timestamp": f.timestamp, "emotion": f.face_emotion, "intensity": f.visual_hook_strength / 10}
        for f in all_frames if f.faces_detected
    ]
    text_events = [
        {"timestamp": f.timestamp, "texts": f.on_screen_text}
        for f in all_frames if f.on_screen_text
    ]

    scene_changes = _detect_scene_changes(all_frames)
    avg_scene_dur = duration_sec / (len(scene_changes) + 1) if scene_changes else duration_sec

    changes_per_sec = len(scene_changes) / duration_sec if duration_sec > 0 else 0
    pacing = "fast-cut" if changes_per_sec > 0.5 else "steady" if changes_per_sec > 0.2 else "slow-paced"

    visual_hooks = [
        {"timestamp": f.timestamp, "description": f.description, "score": f.visual_hook_strength}
        for f in all_frames if f.timestamp <= 10.0 and f.visual_hook_strength >= 6.0
    ]
    visual_hooks.sort(key=lambda x: x["score"], reverse=True)

    thumb_candidates = _select_thumbnail_candidates(all_frames, duration_sec)

    return VideoFrameSummary(
        total_frames_analyzed=len(all_frames),
        duration_sec=duration_sec,
        sample_fps=settings.VISION_SAMPLE_FPS,
        hook_frames=hook_frames,
        hook_visual_description=hook_desc,
        hook_has_face=hook_has_face,
        hook_dominant_emotion=hook_dominant_emotion,
        all_frames=all_frames,
        dominant_scene_type=dominant_scene,
        energy_timeline=energy_timeline,
        emotion_arc=emotion_arc,
        on_screen_texts=text_events,
        face_presence_ratio=round(face_ratio, 2),
        thumbnail_candidates=thumb_candidates,
        scene_changes=scene_changes,
        avg_scene_duration_sec=round(avg_scene_dur, 1),
        pacing_assessment=pacing,
        visual_hook_moments=visual_hooks[:5],
        api_calls_made=api_calls,
        frames_per_call=settings.VISION_FRAMES_PER_BATCH,
    )


def _detect_scene_changes(frames: List[FrameAnalysis]) -> List[float]:
    changes = []
    for i in range(1, len(frames)):
        prev, curr = frames[i-1], frames[i]
        if prev.scene_type != curr.scene_type or (
            prev.energy_level == "low" and curr.energy_level == "high"
        ):
            changes.append(curr.timestamp)
    return changes


def _select_thumbnail_candidates(
    frames: List[FrameAnalysis],
    duration_sec: float,
    max_candidates: int = 5,
) -> List[Dict]:
    scored = []
    for f in frames:
        score = f.visual_hook_strength
        if f.faces_detected:
            score += 2.0
        if f.face_emotion in ("surprised", "excited", "happy"):
            score += 1.5
        if f.on_screen_text:
            score += 1.0
        scored.append((score, f))
    scored.sort(key=lambda x: x[0], reverse=True)

    selected = []
    used_ts = []
    for score, frame in scored:
        if all(abs(frame.timestamp - t) > 5.0 for t in used_ts):
            selected.append({
                "timestamp": frame.timestamp,
                "score": round(score, 1),
                "description": frame.description,
                "emotion": frame.face_emotion,
                "has_face": frame.faces_detected,
                "composition_notes": frame.composition_notes,
                "reason": (
                    f"High energy frame ({frame.energy_level})"
                    + (", face detected" if frame.faces_detected else "")
                    + (f", {frame.face_emotion} expression" if frame.faces_detected else "")
                ),
            })
            used_ts.append(frame.timestamp)
        if len(selected) >= max_candidates:
            break
    return selected


def summary_to_context_string(summary: VideoFrameSummary) -> str:
    if summary.total_frames_analyzed == 0:
        return "[Visual analysis unavailable — transcript-only mode]"

    lines = [
        f"VISUAL CONTEXT ({summary.total_frames_analyzed} frames @ {summary.sample_fps}fps, analysed by Claude Vision):",
        f"• Scene type: {summary.dominant_scene_type} | Pacing: {summary.pacing_assessment}",
        f"• Face presence: {summary.face_presence_ratio:.0%} of frames | Hook emotion: {summary.hook_dominant_emotion}",
        f"• Avg scene duration: {summary.avg_scene_duration_sec:.1f}s | Scene changes: {len(summary.scene_changes)}",
        "",
        f"HOOK WINDOW (0–3s): {summary.hook_visual_description}",
    ]

    if summary.visual_hook_moments:
        lines.append("VISUAL HOOK MOMENTS:")
        for vh in summary.visual_hook_moments[:3]:
            lines.append(f"  {vh['timestamp']:.1f}s — {vh['description']} (score {vh['score']:.1f})")

    if summary.on_screen_texts:
        lines.append("ON-SCREEN TEXT DETECTED:")
        for te in summary.on_screen_texts[:5]:
            lines.append(f"  {te['timestamp']:.1f}s — {te['texts']}")

    if summary.emotion_arc:
        lines.append("EMOTION ARC (key moments):")
        for ea in summary.emotion_arc[::3][:5]:
            lines.append(f"  {ea['timestamp']:.1f}s — {ea['emotion']}")

    if summary.thumbnail_candidates:
        lines.append("BEST THUMBNAIL FRAMES:")
        for tc in summary.thumbnail_candidates[:3]:
            lines.append(f"  {tc['timestamp']:.1f}s — {tc['description']} (score {tc['score']})")

    return "\n".join(lines)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _encode_image(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception as exc:
        logger.warning("Failed to encode frame %s: %s", path, exc)
        return None


def _extract_frame_num(fpath: str) -> float:
    match = re.search(r"(\d+)", Path(fpath).stem)
    return float(match.group(1)) if match else 0.0


def _parse_frame_analyses(data: Dict, frame_files: List[str]) -> List[FrameAnalysis]:
    frames_data = data.get("frames", [])
    results = []
    for i, fd in enumerate(frames_data):
        try:
            results.append(FrameAnalysis(
                timestamp=float(fd.get("timestamp_estimate", _extract_frame_num(frame_files[i]) if i < len(frame_files) else 0)),
                description=str(fd.get("description", "")),
                faces_detected=bool(fd.get("faces_detected", False)),
                face_emotion=str(fd.get("face_emotion", "unknown")),
                face_count=int(fd.get("face_count", 0)),
                on_screen_text=fd.get("on_screen_text", []),
                scene_type=str(fd.get("scene_type", "unknown")),
                energy_level=str(fd.get("energy_level", "medium")),
                visual_hook_strength=float(fd.get("visual_hook_strength", 5.0)),
                composition_notes=str(fd.get("composition_notes", "")),
            ))
        except Exception as e:
            logger.debug("Frame parse error at index %d: %s", i, e)
    return results


def _placeholder_frame(timestamp: float) -> FrameAnalysis:
    return FrameAnalysis(
        timestamp=timestamp, description="[Frame analysis unavailable]",
        faces_detected=False, face_emotion="unknown", face_count=0,
        on_screen_text=[], scene_type="unknown", energy_level="medium",
        visual_hook_strength=5.0, composition_notes="",
    )


def _cleanup(dirs: List[str]) -> None:
    import shutil
    for d in dirs:
        if d and os.path.exists(d):
            try:
                shutil.rmtree(d)
            except Exception:
                pass
