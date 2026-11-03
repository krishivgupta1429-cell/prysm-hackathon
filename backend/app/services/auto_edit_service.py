"""
app/services/auto_edit_service.py
Reads analysis scores → builds a fixed-toolbox edit plan → renders via FFmpeg.

Automatable parameters:
  Hook & Opening     → text overlay at 0-3.5s with the fix text
  Call to Action     → text overlay at last 3s with the fix text
  Audio & Music      → loudnorm audio normalisation
  On-Screen Text     → caption burn-in from transcript (if speech detected)
  Retention & Pacing → trim silent/dead tail beyond last meaningful frame

Non-automatable (content decisions):
  Shareability, Trend Alignment, Visual Quality, Save Potential, Replay Value
  → shown to user as "manual action needed" in the UI
"""
import json
import logging
import os
import re
import subprocess
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

# Preferred font path on Ubuntu/Debian (Railway). Falls back to no fontfile arg.
_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
FONT_PATH = next((f for f in _FONTS if os.path.exists(f)), None)

def _has_drawtext() -> bool:
    """Return True if this FFmpeg build includes the drawtext filter."""
    try:
        r = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True, timeout=5)
        return "drawtext" in r.stdout
    except Exception:
        return False

_DRAWTEXT_AVAILABLE = _has_drawtext()

AUTOMATABLE = {
    "Hook & Opening",
    "Call to Action",
    "Audio & Music",
    "On-Screen Text",
    "Retention & Pacing",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _escape(text: str) -> str:
    """Escape text for FFmpeg drawtext filter."""
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "’")   # replace straight apostrophe with curly
    text = text.replace(":", "\\:")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace(",", "\\,")
    return text


def _shorten(text: str, max_chars: int = 40) -> str:
    """Trim to the first sentence or first N chars, whichever is shorter."""
    for sep in [". ", "! ", "? ", " — ", " – "]:
        idx = text.find(sep)
        if 0 < idx < max_chars:
            text = text[:idx]
            break
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0]
    return text.strip().rstrip(".,;:")


_NICHE_HOOK_DEFAULTS: dict[str, str] = {
    "dance":         "Watch this move 🔥",
    "fashion":       "Outfit check! ✨",
    "fitness":       "Try this today 💪",
    "food":          "You have to try this!",
    "comedy":        "Wait for it... 😂",
    "indiancreator": "Desi vibes only 🔥",
    "bollywood":     "Desi queen era 👑",
    "travel":        "This place is unreal!",
    "beauty":        "Get ready with me ✨",
    "gaming":        "This clip is insane!",
}

_NICHE_CTA_DEFAULTS: dict[str, str] = {
    "dance":         "Follow for more! 💃",
    "fashion":       "Save for inspo! 💫",
    "fitness":       "Follow for tips! 💪",
    "food":          "Save this recipe! 🍽️",
    "comedy":        "Follow for laughs! 😂",
    "indiancreator": "Follow for more! 🔥",
    "bollywood":     "Follow for more! ✨",
    "travel":        "Follow for travel! 🌍",
    "beauty":        "Follow for more! 💄",
    "gaming":        "Follow for clips! 🎮",
}


def _hook_text_for(result: dict) -> str:
    """
    Generate short on-screen hook text from the transcript's opening words.
    Falls back to a niche-appropriate default if no transcript.
    """
    transcript = (result.get("transcript") or "").strip()
    if transcript:
        words = transcript.split()
        # Skip common filler openings
        fillers = {"uh", "um", "okay", "ok", "so", "like", "right", "hey", "hi",
                   "hello", "alright", "well", "yeah", "yes", "no", "wait"}
        while words and words[0].lower().strip(",.!?") in fillers:
            words = words[1:]
        if len(words) >= 3:
            snippet = " ".join(words[:min(7, len(words))]).rstrip(".,;:")
            if len(snippet) >= 8:
                return snippet

    niche = (result.get("niche") or "").lower()
    return _NICHE_HOOK_DEFAULTS.get(niche, "Watch till the end! 👀")


def _cta_text_for(result: dict) -> str:
    """Return a niche + platform appropriate call-to-action string."""
    niche = (result.get("niche") or "").lower()
    return _NICHE_CTA_DEFAULTS.get(niche, "Follow for more!")


def get_video_duration(video_path: str) -> float:
    """Return video duration in seconds via ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path],
            capture_output=True, text=True, timeout=15,
        )
        return float(json.loads(r.stdout).get("format", {}).get("duration", 0))
    except Exception as e:
        logger.warning(f"[AutoEdit] ffprobe error: {e}")
        return 0.0


def _drawtext(text: str, start_s: float, end_s: float,
              y_expr: str, font_size: int = 48) -> str:
    """Build a single FFmpeg drawtext filter string."""
    t = _escape(text)
    font_arg = f"fontfile={FONT_PATH}:" if FONT_PATH else ""
    return (
        f"drawtext={font_arg}"
        f"text='{t}':"
        f"fontsize={font_size}:"
        f"fontcolor=white:"
        f"x=(w-text_w)/2:y={y_expr}:"
        f"box=1:boxcolor=black@0.65:boxborderw=14:"
        f"enable='between(t,{start_s:.2f},{end_s:.2f})'"
    )


# ── Edit-plan builder ─────────────────────────────────────────────────────────

def build_edit_plan(result: dict, video_duration: float) -> dict:
    """
    Map analysis scores to a concrete list of FFmpeg operations.
    Returns:
      {
        "operations": [...],
        "applied_params": ["Hook & Opening", ...],   # what was auto-applied
        "skipped_params": ["Shareability", ...],     # what needs manual action
      }
    """
    params = {p["name"]: p for p in result.get("parameters", [])}
    ops: list[dict] = []
    applied: list[str] = []
    skipped: list[str] = []

    audio_data = result.get("audioAnalysis") or {}

    # ── Hook text overlay ─────────────────────────────────────
    hook = params.get("Hook & Opening", {})
    if hook and hook.get("score", 100) < 70:
        hook_text = _hook_text_for(result)
        ops.append({
            "op": "text_overlay",
            "text": hook_text,
            "start_s": 0.0,
            "end_s": min(3.5, video_duration - 0.5),
            "position": "top",
            "font_size": 40,
            "source_param": "Hook & Opening",
        })
        applied.append("Hook & Opening")
    elif hook:
        skipped.append("Hook & Opening")

    # ── CTA text overlay ──────────────────────────────────────
    cta = params.get("Call to Action", {})
    if cta and cta.get("score", 100) < 70 and video_duration > 6:
        cta_text = _cta_text_for(result)
        cta_start = max(video_duration - 3.5, video_duration * 0.80)
        ops.append({
            "op": "text_overlay",
            "text": cta_text,
            "start_s": cta_start,
            "end_s": video_duration - 0.2,
            "position": "bottom",
            "font_size": 36,
            "source_param": "Call to Action",
        })
        applied.append("Call to Action")
    elif cta:
        skipped.append("Call to Action")

    # ── Audio normalisation ────────────────────────────────────
    audio_param = params.get("Audio & Music", {})
    if audio_param and audio_param.get("score", 100) < 75:
        ops.append({
            "op": "audio_normalize",
            "target_lufs": -14,
            "source_param": "Audio & Music",
        })
        applied.append("Audio & Music")

    # ── Caption burn-in from transcript ───────────────────────
    on_screen = params.get("On-Screen Text", {})
    has_speech = audio_data.get("hasSpeech", False)
    transcript_text = result.get("transcript") or ""
    if (on_screen and on_screen.get("score", 100) < 70
            and has_speech and transcript_text):
        srt_segments = _transcript_to_srt_segments(transcript_text, video_duration)
        if srt_segments:
            ops.append({
                "op": "caption_burn",
                "segments": srt_segments,
                "source_param": "On-Screen Text",
            })
            applied.append("On-Screen Text")
        else:
            skipped.append("On-Screen Text")
    elif on_screen:
        skipped.append("On-Screen Text")

    # ── Parameters that cannot be auto-applied ────────────────
    for name in ["Shareability", "Trend Alignment", "Visual Quality & Originality",
                 "Save Potential", "Replay Value", "Retention & Pacing"]:
        if name in params and name not in applied:
            skipped.append(name)

    logger.info(f"[AutoEdit] Plan: {len(ops)} ops | applied={applied} | skipped={skipped} | drawtext={_DRAWTEXT_AVAILABLE}")
    return {"operations": ops, "applied_params": applied, "skipped_params": skipped}


def _transcript_to_srt_segments(transcript: str, duration: float) -> list[dict]:
    """
    Try to parse timestamped segments from transcript.
    Claude returns transcripts in plain text without reliable timestamps,
    so we split evenly across the video duration as a fallback.
    Returns list of {start_s, end_s, text}.
    """
    if not transcript or duration < 2:
        return []

    # Try to parse [HH:MM:SS] or [MM:SS] format timestamps
    ts_pattern = re.compile(r'\[(\d+):(\d{2})(?::(\d{2}))?\]\s*(.+?)(?=\[|\Z)', re.DOTALL)
    matches = ts_pattern.findall(transcript)
    if matches:
        segments = []
        for m in matches:
            h, mins, secs_raw, text = m
            secs = int(secs_raw) if secs_raw else 0
            start = int(h) * 3600 + int(mins) * 60 + secs
            segments.append({"start_s": float(start), "text": text.strip()})
        # Assign end times
        for i, seg in enumerate(segments):
            seg["end_s"] = segments[i + 1]["start_s"] if i + 1 < len(segments) else duration
        return segments

    # Fallback: split transcript into ~5-word chunks, distribute evenly
    words = transcript.split()
    if len(words) < 3:
        return []
    chunk_size = max(4, len(words) // max(1, int(duration / 3)))
    chunks = [" ".join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size)]
    seg_dur = duration / len(chunks)
    return [
        {"start_s": i * seg_dur, "end_s": (i + 1) * seg_dur, "text": chunk}
        for i, chunk in enumerate(chunks)
    ]


# ── PIL text-overlay image builder (fallback when drawtext unavailable) ───────

def _get_video_dimensions(video_path: str) -> tuple[int, int]:
    """Return (width, height) of video via ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "v:0", video_path],
            capture_output=True, text=True, timeout=10,
        )
        s = json.loads(r.stdout).get("streams", [{}])[0]
        return int(s.get("width", 720)), int(s.get("height", 1280))
    except Exception:
        return 720, 1280


def _wrap_text_lines(text: str, draw, font, max_px: int) -> list[str]:
    """Break text into lines that each fit within max_px pixels wide."""
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = " ".join(current + [word])
        try:
            w = draw.textbbox((0, 0), trial, font=font)[2]
        except Exception:
            w = len(trial) * (font.size if hasattr(font, "size") else 10)
        if w <= max_px:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines or [text]


def _render_text_to_png(text: str, width: int, height: int,
                         position: str, font_size: int) -> Optional[str]:
    """
    Render text with semi-transparent background onto a transparent PNG.
    Word-wraps so text never overflows the frame.
    Returns temp file path, or None on failure.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        _font_candidates = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.load_default()
        for fp in _font_candidates:
            if os.path.exists(fp):
                try:
                    font = ImageFont.truetype(fp, font_size)
                    break
                except Exception:
                    continue

        max_text_w = int(width * 0.80)  # leave 10% margin each side
        lines = _wrap_text_lines(text, draw, font, max_text_w)

        pad = 14
        line_spacing = 6
        line_h = draw.textbbox((0, 0), "Ay", font=font)[3]  # ascender height
        block_h = len(lines) * line_h + (len(lines) - 1) * line_spacing

        # Vertical anchor: 7% from top or 82% from top
        block_top = (int(height * 0.07) if position == "top"
                     else int(height * 0.82) - block_h)

        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            lw = bbox[2] - bbox[0]
            lh = bbox[3] - bbox[1]
            x = (width - lw) // 2
            y = block_top + i * (line_h + line_spacing)
            draw.rounded_rectangle(
                [x - pad, y - pad, x + lw + pad, y + lh + pad],
                radius=8, fill=(0, 0, 0, 170),
            )
            draw.text((x, y), line, fill=(255, 255, 255, 255), font=font)

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(tmp.name, "PNG")
        tmp.close()
        return tmp.name
    except Exception as e:
        logger.warning(f"[AutoEdit] PIL text render failed: {e}")
        return None


# ── FFmpeg renderer ───────────────────────────────────────────────────────────

def render_edited_video(video_path: str, operations: list[dict], output_path: str) -> bool:
    """
    Apply operations to the original video in a single FFmpeg pass.
    Uses drawtext when available; falls back to PIL overlay PNG otherwise.
    Returns True on success.
    """
    if not operations:
        logger.info("[AutoEdit] No operations — nothing to render")
        return False

    audio_filters: list[str] = []
    text_overlay_ops = []
    caption_op = None
    temp_files: list[str] = []

    for op in operations:
        kind = op["op"]
        if kind == "text_overlay":
            text_overlay_ops.append(op)
        elif kind == "audio_normalize":
            audio_filters.append(f"loudnorm=I={op.get('target_lufs', -14)}:TP=-1.5:LRA=11")
        elif kind == "caption_burn":
            caption_op = op

    # ── Build the FFmpeg command ───────────────────────────────────────────
    # Strategy: one pass, compositing all overlays via filter_complex

    extra_inputs: list[str] = []        # additional -i args (PNG overlays)
    filter_complex_parts: list[str] = []
    last_video_label = "[0:v]"
    overlay_idx = 1                     # input index for PNG overlays

    # Text overlays
    if text_overlay_ops:
        if _DRAWTEXT_AVAILABLE:
            # Use drawtext directly in -vf (no extra inputs needed)
            vf_parts = []
            for op in text_overlay_ops:
                y = "h*0.07" if op.get("position") == "top" else "h*0.84"
                vf_parts.append(_drawtext(op["text"], op["start_s"], op["end_s"], y, op.get("font_size", 48)))
            filter_complex_parts.append(",".join(vf_parts))
        else:
            # PIL fallback: render each text to a PNG, overlay via filter_complex
            vid_w, vid_h = _get_video_dimensions(video_path)
            for op in text_overlay_ops:
                png = _render_text_to_png(
                    op["text"], vid_w, vid_h,
                    op.get("position", "top"), op.get("font_size", 48),
                )
                if not png:
                    continue
                temp_files.append(png)
                start_s = op["start_s"]
                end_s = op["end_s"]
                out_label = f"[v{overlay_idx}]"
                filter_complex_parts.append(
                    f"{last_video_label}[{overlay_idx}:v]"
                    f"overlay=x=0:y=0:enable='between(t,{start_s:.2f},{end_s:.2f})'"
                    f"{out_label}"
                )
                extra_inputs += ["-i", png]
                last_video_label = out_label
                overlay_idx += 1

    # Caption burn (subtitles filter — also needs freetype; skip if unavailable)
    if caption_op and _DRAWTEXT_AVAILABLE:
        srt_path = _write_srt(caption_op["segments"])
        if srt_path:
            temp_files.append(srt_path)
            escaped = srt_path.replace("\\", "/").replace(":", "\\:")
            sub_filter = (
                f"subtitles='{escaped}':force_style='"
                f"FontSize=18,PrimaryColour=&H00FFFFFF,"
                f"OutlineColour=&H00000000,BorderStyle=3,Outline=2,"
                f"Alignment=2,MarginV=30'"
            )
            filter_complex_parts.append(sub_filter)

    # Build command
    cmd = ["ffmpeg", "-y", "-i", video_path] + extra_inputs

    if filter_complex_parts and extra_inputs:
        # Use filter_complex when we have extra inputs (PIL overlays)
        cmd += ["-filter_complex", ";".join(filter_complex_parts),
                "-map", last_video_label, "-map", "0:a"]
    elif filter_complex_parts:
        # Plain -vf when no extra inputs (drawtext-only path)
        cmd += ["-vf", ",".join(filter_complex_parts)]

    if audio_filters:
        cmd += ["-af", ",".join(audio_filters)]

    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]

    logger.info(f"[AutoEdit] FFmpeg start | ops={len(operations)} | drawtext={_DRAWTEXT_AVAILABLE} | out={output_path}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        for tf in temp_files:
            try: os.unlink(tf)
            except Exception: pass
        if proc.returncode != 0:
            logger.error(f"[AutoEdit] FFmpeg stderr: {proc.stderr[-800:]}")
            return False
        size_kb = os.path.getsize(output_path) // 1024
        logger.info(f"[AutoEdit] Render OK — {size_kb} KB")
        return True
    except subprocess.TimeoutExpired:
        logger.error("[AutoEdit] FFmpeg timed out (300s)")
        return False
    except Exception as e:
        logger.error(f"[AutoEdit] FFmpeg exception: {e}")
        return False


def _write_srt(segments: list[dict]) -> str | None:
    """Write caption segments to a temp .srt file, return path."""
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".srt", delete=False, mode="w", encoding="utf-8")
        for i, seg in enumerate(segments, 1):
            start = _srt_time(seg["start_s"])
            end = _srt_time(seg["end_s"])
            tmp.write(f"{i}\n{start} --> {end}\n{seg['text']}\n\n")
        tmp.close()
        return tmp.name
    except Exception as e:
        logger.warning(f"[AutoEdit] SRT write failed: {e}")
        return None


def _srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
