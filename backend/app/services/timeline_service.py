"""
app/services/timeline_service.py
Timeline Feedback – Feature #5

Generates timestamp-level feedback for every analysed video.
Output example:
  00:01 ⚠  weak hook — try opening with a question
  00:04 🐢  pacing slow — cut 2s here
  00:07 🔥  strong emotional moment — keep this
  00:18 💡  CTA opportunity — add "follow for more"

Combines:
  - Whisper transcript segments (for text-based issues)
  - Scene detection events (for pacing / visual issues)
  - Emotion timeline from vision analysis (for peak moments)
  - AI interpretation of each segment
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger
from app.models.analysis import Analysis
from app.services.ai_service import _chat, _parse_json

logger = get_logger("timeline_service")


# ── Event type definitions ────────────────────────────────────────────────────

FEEDBACK_ICONS = {
    "hook":       "🎯",
    "pacing":     "🐢",
    "emotion":    "🔥",
    "cta":        "💡",
    "scene":      "✂️",
    "text":       "📝",
    "warning":    "⚠️",
    "positive":   "✅",
    "engagement": "💬",
}

FEEDBACK_TYPES = {
    "hook_weak":        ("warning", "weak hook"),
    "hook_strong":      ("positive", "strong hook"),
    "pacing_slow":      ("pacing",  "pacing slow"),
    "pacing_fast":      ("pacing",  "pacing rushed"),
    "emotion_peak":     ("emotion", "strong emotional moment"),
    "scene_change":     ("scene",   "scene cut"),
    "cta_opportunity":  ("cta",     "CTA opportunity"),
    "dead_air":         ("warning", "dead air / filler"),
    "value_moment":     ("positive", "high-value content"),
    "drop_off_risk":    ("warning", "drop-off risk"),
}


# ── Main entry point ──────────────────────────────────────────────────────────

async def generate_timeline_feedback(
    analysis: Analysis,
    vision_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Produces a sorted list of timestamped feedback events for a video.
    Combines rule-based detection with Claude for deeper interpretation.
    """
    events: List[Dict[str, Any]] = []

    # ── Rule-based events from analysis data ──────────────────────────────────
    segments = analysis.segments or []
    scenes   = analysis.scenes   or []
    improvements = analysis.improvements or []

    # Hook quality (first 3 seconds)
    hook_score = analysis.hook_score or 5.0
    if hook_score < 5.0:
        events.append(_make_event(0.5, "hook_weak", -10,
            f"Hook is weak (score {hook_score:.1f}/10). "
            f"Suggestion: {(analysis.hook_suggestions or ['rewrite opening'])[0]}"))
    elif hook_score >= 8.0:
        events.append(_make_event(0.5, "hook_strong", +15,
            f"Excellent hook (score {hook_score:.1f}/10) — high retention potential."))

    # Scene change events (every cut is a UI event)
    for sc in scenes[:20]:  # limit to first 20 cuts
        ts = sc.get("timestamp", 0)
        score = sc.get("score", 0)
        if score > 50:
            events.append(_make_event(ts, "scene_change", 0,
                f"Scene cut detected (intensity {score:.0f})"))

    # AI-generated improvement timestamps
    for imp in improvements:
        ts = imp.get("timestamp_start") or imp.get("timestamp", 0)
        if ts is None:
            continue
        suggestion = imp.get("suggestion", "")
        priority = imp.get("priority", "medium")
        imp_type = imp.get("type", "pacing")
        score_impact = -12 if priority == "high" else -6 if priority == "medium" else -3
        label = _map_improvement_type(imp_type)
        events.append(_make_event(float(ts), label, score_impact, suggestion))

    # Vision-derived emotion peaks
    if vision_data:
        for peak in (vision_data.get("peak_emotion_moments") or [])[:3]:
            ts = peak.get("timestamp", 0)
            emotion = peak.get("dominant", "")
            intensity = peak.get("intensity", 0)
            if intensity > 0.6:
                events.append(_make_event(ts, "emotion_peak", +8,
                    f"Peak emotion detected: {emotion} (intensity {intensity:.0%})"))

        for text_event in (vision_data.get("text_events") or [])[:5]:
            ts = text_event.get("timestamp", 0)
            texts = [t["text"] for t in text_event.get("texts", [])]
            if texts:
                events.append(_make_event(ts, "text",  0,
                    f"On-screen text: {', '.join(texts[:3])}"))

    # ── AI deep interpretation ─────────────────────────────────────────────────
    if segments:
        ai_events = await _ai_segment_feedback(segments, analysis)
        events.extend(ai_events)

    # ── Sort and deduplicate ──────────────────────────────────────────────────
    events.sort(key=lambda x: x["timestamp"])
    events = _deduplicate_events(events, min_gap=0.5)

    # ── Build summary ─────────────────────────────────────────────────────────
    summary = _build_summary(events, analysis)

    return {
        "events":   events,
        "summary":  summary,
        "total_events": len(events),
        "critical_count": sum(1 for e in events if e.get("score_impact", 0) < -8),
        "positive_count": sum(1 for e in events if e.get("score_impact", 0) > 5),
    }


async def _ai_segment_feedback(
    segments: List[Dict],
    analysis: Analysis,
) -> List[Dict[str, Any]]:
    """
    Ask Claude to annotate key transcript segments with timestamped feedback.
    """
    # Use first 20 segments to stay within token limits
    seg_text = "\n".join(
        f"[{s.get('start', 0):.1f}s - {s.get('end', 0):.1f}s] {s.get('text', '')}"
        for s in segments[:20]
    )
    system = (
        "You are a short-form video editor giving timestamped feedback on a transcript. "
        "Be specific, concise, and actionable. Respond ONLY with valid JSON."
    )
    user = f"""
Video transcript segments (timestamp: text):
{seg_text}

Hook score: {analysis.hook_score}/10
Pacing score: {analysis.pacing_score}/10
Virality score: {analysis.virality_score}/100

Identify up to 8 specific moments in the transcript that are:
- weak (slow, filler, unclear)
- strong (engaging, emotional, valuable)
- opportunities (add CTA, cut, re-order)

Return JSON array:
[
  {{
    "timestamp": <float seconds>,
    "label": "<short 2-4 word description>",
    "type": "<hook|pacing|emotion|cta|warning|positive>",
    "suggestion": "<1 actionable sentence>",
    "score_impact": <int, negative for problems, positive for strengths>
  }}
]
"""
    raw = await _chat(system, user, max_tokens=1000)
    data = _parse_json(raw)
    if not isinstance(data, list):
        return []
    return [_make_event(
        float(e.get("timestamp", 0)),
        e.get("type", "warning"),
        int(e.get("score_impact", 0)),
        e.get("suggestion", ""),
        label=e.get("label"),
    ) for e in data]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_event(
    timestamp: float,
    event_type: str,
    score_impact: int,
    suggestion: str,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    type_info = FEEDBACK_TYPES.get(event_type, ("warning", event_type.replace("_", " ")))
    category, default_label = type_info
    icon = FEEDBACK_ICONS.get(category, "•")
    return {
        "timestamp":    round(timestamp, 2),
        "timestamp_fmt": _fmt_ts(timestamp),
        "type":         event_type,
        "category":     category,
        "label":        label or default_label,
        "icon":         icon,
        "suggestion":   suggestion,
        "score_impact": score_impact,
    }


def _fmt_ts(seconds: float) -> str:
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m:02d}:{s:02d}"


def _map_improvement_type(imp_type: str) -> str:
    mapping = {
        "cut":      "pacing_slow",
        "shorten":  "pacing_slow",
        "move":     "pacing_slow",
        "add_hook": "hook_weak",
        "reorder":  "pacing_slow",
    }
    return mapping.get(imp_type, "warning")


def _deduplicate_events(events: List[Dict], min_gap: float) -> List[Dict]:
    """Remove events that are too close together (keep highest impact)."""
    result = []
    for event in events:
        ts = event["timestamp"]
        too_close = any(abs(ts - r["timestamp"]) < min_gap for r in result)
        if not too_close:
            result.append(event)
    return result


def _build_summary(events: List[Dict], analysis: Analysis) -> str:
    problems  = [e for e in events if e.get("score_impact", 0) < -5]
    strengths = [e for e in events if e.get("score_impact", 0) > 5]
    return (
        f"Found {len(strengths)} strengths and {len(problems)} areas for improvement. "
        f"Priority fix: {problems[0]['suggestion'] if problems else 'None — great work!'}. "
        f"Strongest moment: {strengths[0]['timestamp_fmt']} — {strengths[0]['label'] if strengths else 'N/A'}."
    )
