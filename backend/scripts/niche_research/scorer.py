"""Score one prepared video against the 10 Prysm parameters using the local LLM.

Scoring is broken into two passes so even a 7B vision model can do it well:

  Pass 1  (vision)  — describe what's on screen across the frames
  Pass 2  (text)    — score 10 parameters using vision summary + transcript +
                       post metadata as the context

This separation lets us use a vision-capable model only where vision is
actually needed, and use a fast text-only follow-up for the JSON scoring.
Both calls hit the same OpenAI-compatible endpoint, just with different
content types.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List

from .config import SETTINGS
from .llm_client import chat_text, chat_with_vision, extract_json
from .video_pipeline import VideoAssets

log = logging.getLogger(__name__)


# The canonical 10 parameters, with the *flat* default weights from the live backend.
# These weights are inputs to scoring (so the model can compute weighted averages
# itself if it wants), NOT outputs — the whole point of the research tool is to
# discover what the empirical weights *should* be per niche.
PARAMETERS = [
    {"name": "Hook & Opening",            "weight": 17},
    {"name": "Retention & Pacing",        "weight": 17},
    {"name": "Shareability",              "weight": 14},
    {"name": "Audio & Music",             "weight": 10},
    {"name": "On-Screen Text",            "weight": 9},
    {"name": "Save Potential",            "weight": 8},
    {"name": "Trend Alignment",           "weight": 7},
    {"name": "Replay Value",              "weight": 6},
    {"name": "Visual Quality & Originality", "weight": 6},
    {"name": "Call to Action",            "weight": 6},
]


# ── Pass 1: vision ───────────────────────────────────────────────────────────

VISION_SYSTEM = (
    "You are a careful observer of short-form videos. You will see frames "
    "extracted from a single Instagram Reel, in chronological order. "
    "Describe what you see — do not score, do not judge, just observe."
)


def _vision_prompt(assets: VideoAssets) -> str:
    parts = [
        f"You are looking at {len(assets.frames)} frames from a {assets.niche} reel.",
        f"Video duration: {assets.duration_sec:.1f} seconds.",
        "",
        "Each frame below is labeled with its timestamp. Watch them in order — they tell a story.",
        "",
        "Return ONLY valid JSON, no prose:",
        "{",
        '  "narrative": "1-2 sentences describing what happens across the video",',
        '  "hookType": "what kind of hook is used (curiosity / shock / face-first / context-first / question / statement / reveal)",',
        '  "hookMoment": <number — timestamp in seconds when the hook lands, e.g. 0.5>,',
        '  "framePresence": {',
        '    "facePresent": true/false,',
        '    "textOverlayPresent": true/false,',
        '    "primaryEmotion": "string"',
        '  },',
        '  "perFrame": [',
        '    {"timestamp": 0.0, "scene": "string", "onScreenText": "string or empty", "visualHookStrength": 1-10}',
        "  ]",
        "}",
    ]
    return "\n".join(parts)


def run_vision(assets: VideoAssets) -> Dict[str, Any]:
    if not assets.frames:
        return {}
    image_paths = [str(f["path"]) for f in assets.frames]
    raw = chat_with_vision(
        system_prompt=VISION_SYSTEM,
        user_prompt=_vision_prompt(assets),
        image_paths=image_paths,
    )
    parsed = extract_json(raw)
    if not parsed:
        log.warning("[%s] vision pass returned unparseable JSON", assets.cache_key)
    return parsed


# ── Pass 2: scoring ──────────────────────────────────────────────────────────

SCORE_SYSTEM = (
    "You are an Instagram Reels expert who scores videos against ten well-defined "
    "parameters used by short-form-video coaching tools. Your scores are calibrated "
    "(50 = average performance for the niche, 80+ = exceptional, sub-40 = poor) and "
    "your reasoning is concrete, not generic."
)


def _score_prompt(assets: VideoAssets, vision: Dict[str, Any]) -> str:
    meta = assets.metadata or {}
    transcript = (assets.transcript or "").strip()[:1500] or "(no transcript available)"
    vision_blob = json.dumps(vision, indent=2)[:2500] if vision else "(vision pass unavailable)"

    parts = [
        f"NICHE: {assets.niche}",
        f"DURATION: {assets.duration_sec:.1f}s",
        f"VIEWS: {meta.get('viewCount', 'unknown')} | LIKES: {meta.get('likeCount', 'unknown')} | COMMENTS: {meta.get('commentCount', 'unknown')}",
        f"CAPTION: {(meta.get('caption') or '')[:300]}",
        f"HASHTAGS: {', '.join(meta.get('hashtags') or [])}",
        "",
        "VISION OBSERVATION (from prior multimodal pass):",
        vision_blob,
        "",
        "AUDIO TRANSCRIPT:",
        transcript,
        "",
        "Score every one of the ten parameters below. Each score is 0-100. Be honest "
        "and use the full range — most parameters of most videos are NOT 70+.",
        "",
        "Parameters to score (use these EXACT names):",
        *[f"  - {p['name']}" for p in PARAMETERS],
        "",
        "Return ONLY valid JSON, no prose. Schema:",
        "{",
        '  "parameters": [',
        '    {"name": "Hook & Opening", "score": <0-100>, "evidence": "1 sentence — what in the video supports this score"}',
        "  ],",
        '  "dominantPatterns": ["short tags describing what the video DOES well — e.g. \\"trending audio\\", \\"face-first hook\\", \\"text overlay reveal\\""],',
        '  "weakSpots": ["short tags describing what is missing or weak"]',
        "}",
        "",
        "Output ALL ten parameters. Do not skip any.",
    ]
    return "\n".join(parts)


def run_scoring(assets: VideoAssets, vision: Dict[str, Any]) -> Dict[str, Any]:
    raw = chat_text(
        system_prompt=SCORE_SYSTEM,
        user_prompt=_score_prompt(assets, vision),
    )
    parsed = extract_json(raw)
    if not parsed:
        log.warning("[%s] scoring pass returned unparseable JSON", assets.cache_key)
    return parsed


# ── Combined ─────────────────────────────────────────────────────────────────

def score_video(assets: VideoAssets) -> Dict[str, Any]:
    """Returns a single record per video — ready to be appended to per_video_<niche>.json."""
    out: Dict[str, Any] = {
        "url": assets.url,
        "niche": assets.niche,
        "cache_key": assets.cache_key,
        "metadata": {
            k: v for k, v in (assets.metadata or {}).items()
            if not k.startswith("_")  # drop the raw video URL etc
        },
        "duration_sec": assets.duration_sec,
        "frames_used": len(assets.frames),
        "transcript_len": len(assets.transcript or ""),
        "error": assets.error,
    }
    if assets.error:
        return out

    vision = run_vision(assets)
    out["vision"] = vision

    scoring = run_scoring(assets, vision)
    out["scoring"] = scoring

    # Index params by name for easier downstream aggregation
    params = scoring.get("parameters") or []
    indexed: Dict[str, int] = {}
    for p in params:
        if isinstance(p, dict) and "name" in p and "score" in p:
            try:
                indexed[p["name"]] = int(p["score"])
            except (ValueError, TypeError):
                continue
    out["scores_by_param"] = indexed

    return out
