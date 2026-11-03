"""
app/services/ai_service.py
Central AI reasoning engine — Claude with multimodal context.

Every analysis function now accepts an optional `visual_context: str`
produced by vision_service.summary_to_context_string().

When present, Claude reasons over BOTH the transcript AND visual signals.
When absent (e.g. vision disabled), it falls back to transcript-only.

This is the Level 2 architecture:
  GPT-4o sees the pixels → produces descriptions
  Claude reads descriptions + transcript → produces strategy

Claude never sees raw pixels. Claude reasons. GPT-4o describes.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

import anthropic

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: Optional[anthropic.AsyncAnthropic] = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=60.0, max_retries=1)
    return _client


# ── Shared helpers ────────────────────────────────────────────────────────────

def _parse_json(text: str) -> Any:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON from AI response.")
        return {"raw": text}


async def _chat(
    system: str,
    user: str,
    max_tokens: int = 2048,
) -> str:
    client = _get_client()
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text


def _visual_section(visual_context: Optional[str]) -> str:
    """Inject visual context into a prompt, or explain it's unavailable."""
    if not visual_context or "[Visual analysis unavailable" in visual_context:
        return "\n[Note: Visual analysis unavailable — reasoning from transcript only]\n"
    return f"\n{visual_context}\n"


# ── Analysis functions — all now multimodal-aware ─────────────────────────────

async def analyze_hook(
    hook_transcript: str,
    full_transcript: str,
    duration: float,
    visual_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Evaluate hook strength using BOTH the first-3s transcript AND
    visual descriptions of those frames.

    With visual context: knows if there's a face, what emotion is shown,
    whether text overlays exist, energy level of the opening shot.
    Without it: falls back to transcript-only scoring.
    """
    system = (
        "You are an expert short-form video content strategist who analyses both "
        "what creators SAY and what viewers SEE in the first 3 seconds. "
        "Respond ONLY with valid JSON."
    )
    user = f"""
TRANSCRIPT — first 3 seconds:
\"\"\"{hook_transcript}\"\"\"

TRANSCRIPT — full video (excerpt):
\"\"\"{full_transcript[:600]}\"\"\"

Video duration: {duration:.1f}s
{_visual_section(visual_context)}
Score the hook considering both verbal and visual elements.
A weak verbal hook can be rescued by a strong visual. A great line
delivered off-camera is weaker than the same line with eye contact.

Return JSON:
{{
  "hook_score": <float 0-10>,
  "retention_potential": "<low|medium|high>",
  "verbal_hook_score": <float 0-10>,
  "visual_hook_score": <float 0-10 or null if no visual data>,
  "hook_strengths": [<str>, ...],
  "hook_weaknesses": [<str>, ...],
  "suggested_hooks": [<str>, ...],
  "visual_improvements": [<str>, ...]
}}
"""
    return _parse_json(await _chat(system, user))


async def predict_virality(
    transcript: str,
    hook_score: float,
    duration: float,
    scenes: List[dict],
    visual_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Virality prediction using all signals:
    transcript content, hook score, scene pacing, AND visual energy/emotion data.
    """
    scene_count = len(scenes)
    avg_scene_dur = duration / scene_count if scene_count else duration

    system = (
        "You are a viral content prediction expert with deep knowledge of "
        "TikTok, Instagram Reels, and YouTube Shorts algorithms. "
        "You have access to both transcript analysis and visual frame data. "
        "Respond ONLY with valid JSON."
    )
    user = f"""
VIDEO SIGNALS:
- Duration: {duration:.1f}s
- Scene changes: {scene_count} (avg {avg_scene_dur:.1f}s each)
- Hook score: {hook_score}/10

TRANSCRIPT:
\"\"\"{transcript[:1000]}\"\"\"
{_visual_section(visual_context)}
Predict virality considering:
- Verbal hook strength and content quality (transcript)
- Visual energy, pacing, and emotional delivery (visual context)
- Platform-specific format fit
- Emotional trigger stacking (verbal AND visual)

Return JSON:
{{
  "virality_score": <int 0-100>,
  "confidence": "<low|medium|high>",
  "reasons": [<str>, ...],
  "verbal_signals": [<str>, ...],
  "visual_signals": [<str> or "unavailable", ...],
  "emotional_triggers_detected": [<str>, ...],
  "pacing_assessment": "<too slow|good|too fast>",
  "face_camera_bonus": <bool, did face+eye contact boost score?>,
  "storytelling_score": <float 0-10>
}}
"""
    return _parse_json(await _chat(system, user))


async def analyze_content_breakdown(
    transcript: str,
    visual_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Content structure analysis. Visual context reveals scene types and
    whether the format (talking head, b-roll, text) matches the narrative.
    """
    system = (
        "You are a content strategy expert in short-form video. "
        "Analyse both scripted content and visual execution. "
        "Respond ONLY with valid JSON."
    )
    user = f"""
TRANSCRIPT:
\"\"\"{transcript[:2000]}\"\"\"
{_visual_section(visual_context)}
Return JSON:
{{
  "story_structure": {{
    "setup": "<description or null>",
    "conflict": "<description or null>",
    "resolution": "<description or null>",
    "type": "<narrative|educational|entertaining|inspirational|promotional>"
  }},
  "value_density": <float 0-10>,
  "emotional_hooks": [<str>, ...],
  "call_to_action": "<detected CTA text or null>",
  "content_gaps": [<str>, ...],
  "visual_format_fit": "<how well does the visual style match the content type>",
  "audience_fit": "<broad|niche|very niche>"
}}
"""
    return _parse_json(await _chat(system, user))


async def match_trends(
    transcript: str,
    niche: Optional[str] = None,
    visual_context: Optional[str] = None,
) -> Dict[str, Any]:
    """Trend matching — visual context reveals format trends (talking head, POV, etc.)."""
    system = (
        "You are a social media trend analyst. Respond ONLY with valid JSON."
    )
    user = f"""
TRANSCRIPT:
\"\"\"{transcript[:1200]}\"\"\"

Creator niche: {niche or "unknown"}
{_visual_section(visual_context)}
Return JSON:
{{
  "trend_alignment": "<low|medium|high>",
  "relevant_trends": [<str>, ...],
  "missing_trends": [<str>, ...],
  "niche_alignment": "<description>",
  "format_trend_fit": "<how well the visual format aligns with current trends>",
  "trending_formats_to_consider": [<str>, ...]
}}
"""
    return _parse_json(await _chat(system, user))


async def generate_captions(
    transcript: str,
    title: Optional[str] = None,
    niche: Optional[str] = None,
    visual_context: Optional[str] = None,
) -> Dict[str, Any]:
    """Caption generation — visual thumbnail descriptions help write better hooks."""
    system = (
        "You are a viral social media copywriter. "
        "Use visual context to write captions that tease the best visual moments. "
        "Respond ONLY with valid JSON."
    )
    user = f"""
TRANSCRIPT (excerpt):
\"\"\"{transcript[:1000]}\"\"\"

Original title: {title or "none"}
Niche: {niche or "general"}
{_visual_section(visual_context)}
Return JSON:
{{
  "captions": {{
    "instagram": "<caption>",
    "tiktok": "<caption>",
    "youtube": "<caption>"
  }},
  "hashtags": {{
    "instagram": [<str>, ...],
    "tiktok": [<str>, ...],
    "youtube": [<str>, ...]
  }},
  "titles": {{
    "instagram": "<title>",
    "tiktok": "<title>",
    "youtube": "<title>"
  }},
  "cta_suggestions": [<str>, ...]
}}
"""
    return _parse_json(await _chat(system, user))


async def analyze_retention(
    segments: List[dict],
    scenes: List[dict],
    transcript: str,
    duration: float,
    visual_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Retention analysis using visual pacing signals + transcript.
    Visual context reveals energy drops, long static shots, scene change rate.
    """
    system = (
        "You are a video editor and retention specialist for short-form content. "
        "Use both script analysis and visual pacing signals. "
        "Respond ONLY with valid JSON."
    )
    segments_preview = segments[:15]

    user = f"""
VIDEO STRUCTURE:
- Duration: {duration:.1f}s
- Scene changes: {len(scenes)}
- Transcript segments: {len(segments)}

SEGMENTS (first 15):
{json.dumps(segments_preview, indent=2)}

TRANSCRIPT:
\"\"\"{transcript[:800]}\"\"\"
{_visual_section(visual_context)}
Return JSON:
{{
  "pacing_score": <float 0-10>,
  "improvements": [
    {{
      "type": "<cut|move|shorten|add_hook|reorder|add_broll>",
      "timestamp_start": <float or null>,
      "timestamp_end": <float or null>,
      "suggestion": "<actionable suggestion>",
      "priority": "<low|medium|high>"
    }}
  ],
  "visual_pacing_issues": [<str>, ...],
  "estimated_drop_off_points": [<float timestamps>, ...],
  "optimal_duration_sec": <float>
}}
"""
    return _parse_json(await _chat(system, user))


async def suggest_thumbnails(
    transcript: str,
    scene_timestamps: List[float],
    duration: float,
    visual_context: Optional[str] = None,
    thumbnail_candidates: Optional[List[Dict]] = None,
) -> List[Dict[str, Any]]:
    """
    Thumbnail suggestions now use ACTUAL analyzed frame descriptions
    from the vision pass — not just timestamp guesses.
    """
    system = (
        "You are a YouTube/TikTok thumbnail strategist. "
        "Select the best thumbnail frames based on actual visual descriptions. "
        "Respond ONLY with valid JSON."
    )

    # If we have real vision data, use it; otherwise fall back to timestamps
    if thumbnail_candidates:
        frame_info = json.dumps(thumbnail_candidates[:8], indent=2)
        frame_section = f"ANALYZED FRAME CANDIDATES (from vision pass):\n{frame_info}"
    else:
        frame_section = f"Available frame timestamps (no visual analysis): {scene_timestamps}"

    user = f"""
TRANSCRIPT:
\"\"\"{transcript[:600]}\"\"\"

Video duration: {duration:.1f}s
{_visual_section(visual_context)}
{frame_section}

Select up to 5 thumbnail suggestions. Prioritise frames with:
- Faces showing strong emotion (surprise, excitement, curiosity)
- Eye contact with camera
- Clear, legible on-screen text
- High visual energy
- Composition that creates curiosity

Return JSON array:
[
  {{
    "timestamp": <float>,
    "reason": "<why this frame wins>",
    "overlay_text": "<short punchy overlay text>",
    "emotion_target": "<curiosity|shock|inspiration|humour>",
    "has_face": <bool>,
    "description": "<what's in the frame>"
  }}
]
"""
    raw = await _chat(system, user)
    data = _parse_json(raw)
    return data if isinstance(data, list) else data.get("thumbnails", [])


async def generate_benchmark(
    analysis_data: Dict[str, Any],
    creator_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Benchmark analysis — unchanged, no visual context needed."""
    system = (
        "You are an analytics benchmarking expert for content creators. "
        "Respond ONLY with valid JSON."
    )
    profile_str = json.dumps(creator_profile or {}, indent=2)
    current_str = json.dumps(analysis_data, indent=2)

    user = f"""
CURRENT VIDEO:
{current_str}

CREATOR HISTORY:
{profile_str}

Return JSON:
{{
  "vs_creator_avg": {{
    "hook_score":     {{"current": <float>, "avg": <float or null>, "delta": <float or null>}},
    "virality_score": {{"current": <int>,   "avg": <float or null>, "delta": <float or null>}}
  }},
  "niche_avg_percentile": <int 0-100 or null>,
  "strengths": [<str>, ...],
  "areas_for_improvement": [<str>, ...],
  "overall_grade": "<A+|A|B+|B|C+|C|D|F>"
}}
"""
    return _parse_json(await _chat(system, user))
