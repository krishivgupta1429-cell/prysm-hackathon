"""
app/services/prysm_analyse_service.py
Unified Prysm analysis pipeline — single service that produces the 10-parameter output.

Pipeline:
  1. Download video (if URL) or use uploaded file
  2. Extract frames with FFmpeg (5 frames at strategic points)
  3. Extract audio with FFmpeg
  4. Transcribe audio with Groq Whisper (Hindi, English, all languages)
  5. Send frames to Claude Vision for visual analysis
  6. Gemini listens to audio (music, voice quality, mood, background sounds)
  7. Combine vision + audio + creator brief → Claude generates 10-parameter breakdown
  8. Return structured result for frontend
"""

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.studio.llm_switch import anthropic  # the anthropic package, or Muse Spark behind the same interface (AI_PROVIDER=meta)
from app.studio.score_math import normalise_scored_weights, overall_and_predicted  # hackathon: overall over the six scored params, weights normalised to 1

from app.core.config import settings, NICHE_WEIGHTS, DEFAULT_SCORED_WEIGHTS, ADVISORY_PARAMETERS, SCORED_PARAMETERS
from app.services.trend_service import get_trend_data, build_trend_context
from app.services.encodec_service import compute_audio_energy_curve, build_energy_context
from app.services.aesthetics_service import score_frames, build_aesthetics_context
from app.services.engagement_service import predict_engagement, build_engagement_context

logger = logging.getLogger(__name__)


def _validate_suggestions(result: dict, whisper_data: dict, vision_data: dict, intent: str) -> dict:
    """
    Post-processing validation layer: removes or rewrites howToDoIt steps that are
    unimplementable because the video already does what is being suggested.

    Three checks run deterministically (no LLM):
      1. Transcript check — step suggests something the voiceover already does
         (CTA, re-hook, specific phrase). Rewrite to acknowledge + suggest upgrade.
      2. Vision check — step suggests adding on-screen text/element already visible
         in a frame. Rewrite to acknowledge + suggest style upgrade.
      3. Timestamp deduplication — two parameters suggest a fix at the same timestamp.
         Keep the one whose parameter owns that domain; strip the duplicate.
    """
    if intent == "competitor":
        # Competitor mode: suggestions are for user's NEXT video, not THIS one — skip validation
        return result

    transcript_full = (whisper_data.get("transcript") or "").lower()
    segments = whisper_data.get("segments") or []
    frames = (vision_data or {}).get("frames") or []

    # Build lookup: timestamp (int seconds) → on-screen text seen in that frame
    frame_text_by_ts: dict[int, str] = {}
    for f in frames:
        ts = int(f.get("timestamp", 0))
        txt = (f.get("onScreenText") or "").strip().lower()
        if txt:
            frame_text_by_ts[ts] = txt

    # Build set of all on-screen text tokens already visible in any frame
    all_onscreen_tokens: set[str] = set()
    for txt in frame_text_by_ts.values():
        all_onscreen_tokens.update(tok for tok in re.findall(r'\w+', txt) if len(tok) > 3)

    # Build set of all spoken tokens from transcript
    transcript_tokens: set[str] = set(tok for tok in re.findall(r'\w+', transcript_full) if len(tok) > 3)

    # CTA keywords — if any appear in transcript, verbal CTA already exists
    _CTA_WORDS = {"follow", "subscribe", "like", "save", "share", "comment", "swipe", "tag",
                  "link", "bio", "click", "check", "download", "watch", "send", "karo", "dabao"}

    # Re-hook / mid-video hook keywords
    _REHOOK_WORDS = {"wait", "stay", "before", "watch", "listen", "important", "but", "actually",
                     "ruko", "suno", "dekho", "lekin", "magar"}

    # Track which timestamps have already been claimed by a parameter (for dedup)
    # Maps timestamp_string → parameter_name that claimed it
    claimed_timestamps: dict[str, str] = {}

    # Parameter domain ownership for timestamp dedup
    _PARAM_DOMAIN = {
        "Hook & Opening": {"hook", "first", "opening", "start", "0s", "1s", "2s", "3s"},
        "Retention & Pacing": {"rehook", "re-hook", "drop", "middle", "mid", "8s", "9s", "10s", "11s", "12s"},
        "On-Screen Text": {"caption", "subtitle", "text overlay", "overlay", "on-screen"},
        "Call to Action": {"cta", "call to action", "follow", "save", "share", "comment", "end", "final"},
        "Audio & Music": {"audio", "music", "sound", "song", "track", "voice"},
    }

    for param in result.get("parameters", []):
        param_name = param.get("name", "")
        how = param.get("howToDoIt") or ""
        if not how:
            continue

        # Split into individual steps — handle "Step N —", numbered lists, sentences
        step_parts = re.split(r'(?:^|\s)Step\s+\d+\s*[—\-:]\s*|(?<=[.!?])\s+(?=[A-Z])', how, flags=re.IGNORECASE)
        step_parts = [s.strip() for s in step_parts if s.strip() and len(s.strip()) > 15]

        validated_steps = []
        for step in step_parts:
            step_lower = step.lower()
            step_tokens = set(tok for tok in re.findall(r'\w+', step_lower) if len(tok) > 3)

            # ── Check 1: CTA already exists verbally ─────────────────────────
            suggests_cta = bool(_CTA_WORDS & step_tokens) and any(
                kw in step_lower for kw in ("add", "include", "say", "tell", "put", "place", "write")
            )
            cta_in_transcript = bool(_CTA_WORDS & transcript_tokens)
            if suggests_cta and cta_in_transcript and param_name == "Call to Action":
                verbal_cta = whisper_data.get("verbalCTA", "")
                if verbal_cta and verbal_cta != "None detected":
                    # Rewrite: acknowledge it exists, suggest upgrade
                    step = (
                        f"Your voiceover already includes a CTA ({verbal_cta.split(' at ')[0]}). "
                        f"Instead of adding another, strengthen the existing one: make it more specific "
                        f"(e.g. 'Comment your biggest hook mistake' beats 'Comment below') or "
                        f"add a matching on-screen text version so muted viewers see it too."
                    )
                    logger.info(f"[Validate] Rewrote redundant CTA step in {param_name}")

            # ── Check 2: Re-hook already exists in transcript ─────────────────
            suggests_rehook = bool(_REHOOK_WORDS & step_tokens) and any(
                kw in step_lower for kw in ("add", "insert", "include", "place", "put")
            )
            rehook_in_transcript = bool(_REHOOK_WORDS & transcript_tokens)
            if suggests_rehook and rehook_in_transcript and param_name == "Retention & Pacing":
                # Find which segment has the re-hook word
                rehook_seg = next(
                    (seg for seg in segments
                     if any(w in seg.get("text", "").lower() for w in _REHOOK_WORDS)),
                    None
                )
                if rehook_seg:
                    step = (
                        f"A mid-video re-hook already exists in your voiceover at "
                        f"{rehook_seg['start']:.0f}s ('{rehook_seg['text'].strip()[:60]}'). "
                        f"Rather than adding another, strengthen this moment visually: "
                        f"cut to a new angle, add a quick zoom, or flash a text beat at exactly "
                        f"{rehook_seg['start']:.0f}s to amplify the re-hook that's already there."
                    )
                    logger.info(f"[Validate] Rewrote redundant re-hook step in {param_name}")

            # ── Check 3: On-screen text already exists at suggested timestamp ──
            # Extract any timestamp mentioned in this step (e.g. "at 14s", "14.5s")
            ts_mentions = re.findall(r'(\d+(?:\.\d+)?)\s*s\b', step_lower)
            for ts_str in ts_mentions:
                ts_int = int(float(ts_str))
                existing_text = frame_text_by_ts.get(ts_int) or frame_text_by_ts.get(ts_int - 1) or frame_text_by_ts.get(ts_int + 1)
                suggests_text_add = any(kw in step_lower for kw in
                    ("add text", "add a text", "text overlay", "add overlay", "add caption",
                     "place text", "put text", "write", "add the text", "add on-screen"))
                if suggests_text_add and existing_text:
                    step = (
                        f"On-screen text already exists at {ts_str}s ('{existing_text[:50]}'). "
                        f"Instead of adding another overlay, improve what's already there: "
                        f"increase the font size for mobile readability, reposition it to the "
                        f"upper third away from Instagram's UI, or make the wording punchier."
                    )
                    logger.info(f"[Validate] Rewrote duplicate text overlay step at {ts_str}s in {param_name}")
                    break

            # ── Check 4: Timestamp deduplication across parameters ────────────
            for ts_str in ts_mentions:
                ts_key = f"{int(float(ts_str))}s"
                if ts_key in claimed_timestamps:
                    prior_param = claimed_timestamps[ts_key]
                    if prior_param != param_name:
                        # Check if this param owns this type of suggestion
                        param_domain = _PARAM_DOMAIN.get(param_name, set())
                        step_owned = bool(param_domain & step_tokens)
                        if not step_owned:
                            # This param doesn't own this timestamp — soft-remove the step
                            logger.info(f"[Validate] Skipped duplicate timestamp {ts_key} in {param_name} (claimed by {prior_param})")
                            step = ""  # mark for removal
                            break
                else:
                    claimed_timestamps[ts_key] = param_name

            if step:
                validated_steps.append(step)

        if validated_steps:
            # Reconstruct howToDoIt with explicit Step N — labels
            param["howToDoIt"] = " ".join(
                f"Step {i + 1} — {s}" for i, s in enumerate(validated_steps)
            )

    return result


async def _claude_with_retry(client, max_retries: int = 4, **kwargs):
    """Call client.messages.create with exponential backoff on rate limit, timeout, and connection errors."""
    for attempt in range(max_retries):
        try:
            return await client.messages.create(**kwargs)
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt  # 1s, 2s, 4s, 8s
            logger.warning(f"[Prysm] Anthropic rate limit hit — retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
            await asyncio.sleep(wait)
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
            if attempt == max_retries - 1:
                raise
            wait = 10 * (attempt + 1)  # 10s, 20s, 30s — transient stall, short wait
            logger.warning(f"[Prysm] Anthropic timeout/connection error ({type(e).__name__}) — retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
            await asyncio.sleep(wait)
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < max_retries - 1:  # overloaded
                wait = 2 ** attempt
                logger.warning(f"[Prysm] Anthropic overloaded — retrying in {wait}s")
                await asyncio.sleep(wait)
            else:
                raise


# Dedicated thread pool for blocking subprocess calls (FFmpeg, yt-dlp).
# Default pool is too small (5-6 threads) — with 20 concurrent analyses each
# needing 3-4 threads for FFmpeg+audio+aesthetics, jobs queue behind each other.
# 10 threads per worker × 4 workers = 40 total, handles 10 simultaneous analyses.
_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="prysm-ffmpeg")


class CreditExhaustedError(Exception):
    """Raised mid-pipeline when the user runs out of credits after Stage 1."""
    def __init__(self, stage1_cost_usd: float):
        self.stage1_cost_usd = stage1_cost_usd
        super().__init__("Credits exhausted after Stage 1 — scoring call skipped")


# ── Cost Tracking ────────────────────────────────────────────────────────────
# Pricing per million tokens (USD) — from provider dashboards
PRICING = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-6":   {"input": 15.0, "output": 75.0},
    "gemini-2.5-pro":    {"input": 1.25, "output": 10.0},
    # Muse Spark (AI_PROVIDER=meta): published prices per million tokens
    "muse-spark-1.3":    {"input": 1.25, "output": 4.25},
    "muse-spark-1.2":    {"input": 1.25, "output": 4.25},
}


def _resp_model(response, default: str) -> str:
    """The model name to price a response with: the model that really answered when AI_PROVIDER=meta, else the original name."""
    return (getattr(response, "model", None) or default) if settings.META_MODE else default
# RunPod RTX A5000 serverless rate ($/second of GPU compute including delay)
RUNPOD_A40_COST_PER_SEC = 0.00078


class CostTracker:
    """Tracks real API costs across an analysis pipeline. All values in USD."""

    def __init__(self):
        self.items: List[Dict[str, Any]] = []
        self.total_usd = 0.0

    def add_claude(self, model: str, input_tokens: int, output_tokens: int, label: str = ""):
        rates = PRICING.get(model, PRICING["claude-sonnet-4-6"])
        cost_usd = (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
        self.total_usd += cost_usd
        self.items.append({
            "label": label or model, "model": model,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 6),
        })
        logger.info(f"[Cost] {label or model}: {input_tokens} in + {output_tokens} out = ${cost_usd:.4f}")

    def add_gemini(self, model: str, input_tokens: int, output_tokens: int, label: str = ""):
        rates = PRICING.get(model, {"input": 1.25, "output": 10.0})
        cost_usd = (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
        self.total_usd += cost_usd
        self.items.append({
            "label": label or model, "model": model,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 6),
        })
        logger.info(f"[Cost] {label or model}: {input_tokens} in + {output_tokens} out = ${cost_usd:.4f}")

    def add_runpod(self, execution_time_ms: int, label: str = "RunPod engagement (A5000)"):
        cost_usd = (execution_time_ms / 1000) * RUNPOD_A40_COST_PER_SEC
        self.total_usd += cost_usd
        self.items.append({
            "label": label, "model": "runpod-a40",
            "execution_time_ms": execution_time_ms,
            "cost_usd": round(cost_usd, 6),
        })
        logger.info(f"[Cost] {label}: {execution_time_ms}ms = ${cost_usd:.4f}")

    def add_apify(self, cost_usd: float, label: str = "Instagram scrape (Apify)"):
        self.total_usd += cost_usd
        self.items.append({"label": label, "model": "apify", "cost_usd": round(cost_usd, 6)})
        logger.info(f"[Cost] {label}: ${cost_usd:.4f}")

    def add_other(self, label: str, model: str, cost_usd: float):
        """A cost measured elsewhere (the hackathon's extra-evidence models)."""
        self.total_usd += cost_usd
        self.items.append({"label": label, "model": model, "cost_usd": round(cost_usd, 6)})
        logger.info(f"[Cost] {label}: ${cost_usd:.4f}")

    def summary(self) -> Dict[str, Any]:
        return {"items": self.items, "total_usd": round(self.total_usd, 6)}


_anthropic: Optional[anthropic.AsyncAnthropic] = None
_groq = None
_gemini = None


def _get_anthropic() -> anthropic.AsyncAnthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=360.0,
            max_retries=0,
        )
    return _anthropic


def _get_groq():
    global _groq
    if _groq is None:
        from groq import Groq
        _groq = Groq(api_key=settings.GROQ_API_KEY)
    return _groq


def _get_gemini():
    global _gemini
    if _gemini is None:
        from google import genai
        _gemini = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _gemini


NICHE_CONTEXT = {
    "Finance": "Finance education: Hook must establish credibility or a shocking money stat in 2 seconds. Audiences are sceptical — trust signals (charts, numbers, proof) in the first frame outperform personality hooks.",
    "Fitness": "Fitness: Hook must show a visible transformation, a challenging exercise, or a shocking before/after in 1.5 seconds. Results speak louder than tips.",
    "Comedy": "Comedy: Hook must create confusion, surprise, or an absurd setup in 1 second. The punchline drives saves and shares — protect it. Never give away the joke in the first frame.",
    "Education": "Education: Hook must promise a specific surprising outcome or counterintuitive fact immediately. Curiosity gap ('You're doing X wrong') outperforms explanation hooks.",
    "Lifestyle": "Lifestyle: Hook must create an aspirational or relatable feeling in 2 seconds. Aesthetic consistency and a clear 'this could be your life' moment drive follows.",
    "Business": "Business: Hook must present a recognisable pain point or a polarising opinion immediately. Controversy and counterintuitive business takes drive saves and shares.",
    "Food": "Food: Hook must show the finished dish or the most visually satisfying moment in the first 1 second. Colour, steam, texture — make them hungry instantly.",
    "Tech": "Tech: Hook must demonstrate a problem being solved or a jaw-dropping capability in 2 seconds. Before/after demos and 'I didn't know this existed' hooks dominate.",
    "Fashion": "Fashion: Hook must show the final outfit or the most striking transformation frame immediately. Colour contrast, unexpected styling, and the 'wow' frame before the reveal are critical.",
    "Travel": "Travel: Hook must show the single most visually stunning moment of the entire trip in the first frame. B-roll quality and colour grade matter more in travel than any other niche.",
    "Dance": "Dance: Hook must show the most impressive move or the exact drop within 1 second. Original choreography on trending audio wins. Song choice is the single biggest Trend Alignment signal — an old song revived by a viral choreo move is as powerful as a current chart topper.",
    "Bollywood": "Bollywood/Indian dance: Hook must show the key expression or first beat within 1 second. Indian audiences value emotional connection to the song, clean footwork, and expressive face. Trend alignment is song-driven — viral choreography on classic or trending Bollywood/Punjabi songs wins.",
    "IndianCreator": "Indian creator content: Hook must establish personality or a relatable Indian moment in 1.5 seconds. Indian audiences respond strongly to relatability, Hinglish dialogue, cultural references, and emotional storytelling.",
    "Motivation": "Motivation/Self-help: Hook must land an emotionally charged statement or a shocking personal truth in 1.5 seconds. Vulnerability and specificity ('I lost everything at 24') outperform generic inspiration.",
    "Gaming": "Gaming: Hook must show the most hype clip — the clutch moment, the insane play, or the funniest fail — in the first second. Reactions and facecam energy drive engagement more than gameplay alone.",
    "Beauty": "Beauty: Hook must show the most dramatic transformation result or the most striking before/after immediately. Tutorials must tease the final look in the first frame — never start with a bare face and build slowly.",
    "Music": "Music: Hook must hit the most emotionally resonant or melodically distinctive moment of the performance immediately. Original songs need the catchiest bar first; covers need to show the singer's unique take within 2 seconds.",
    "Sports": "Sports: Hook must show the most exciting moment — the goal, the trick, the fail, the record — in the first second. Reaction moments and slow-motion replays of peak performance drive rewatches.",
    "Health & Wellness": "Health & Wellness: Hook must present a surprising health fact, a common myth being busted, or a visible result in 1.5 seconds. Authority signals (credentials, data, personal results) drive trust and saves.",
    "DIY & Crafts": "DIY & Crafts: Hook must show the finished result first, then take the viewer on the creation journey. The 'you can make this' moment drives saves more than any other signal — make the end product irresistible.",
    "Photography": "Photography: Hook must show the most visually striking image or the most dramatic before/after edit immediately. Technique reveals ('how I shot this') and gear-free results ('taken on iPhone') are the top formats.",
    "Entertainment": "Entertainment: Hook must open on the most dramatic, funny, or unexpected moment of the story. Reaction-style content and 'you won't believe this' setups work best — get to the payoff fast.",
    "Marketing": "Marketing: Hook must present a counterintuitive marketing truth or a shocking result ('$0 ad spend, 100K views') in 2 seconds. Data-backed claims and case studies drive saves and shares among business audiences.",
    "Vlogs": "Vlogs: Hook must open on the most cinematic or emotionally loaded moment of the day — not a slow morning routine. Story-driven structure with a clear emotional arc outperforms diary-style content.",
    "Self Improvement": "Self Improvement: Hook must open with a specific, relatable failure or a counterintuitive insight in 1.5 seconds. Practical systems and numbered tips ('3 habits that changed my mornings') drive saves. Avoid generic motivation — specificity is everything.",
    "Memes": "Memes: Hook must land the setup or the punchline format immediately — viewers have seen every meme template and skip in under 0.5 seconds if it's not instantly funny or relatable. Audio selection is the single biggest viral signal.",
    "News & Politics": "News & Politics: Hook must state the headline or the most surprising fact in the first 1 second. Opinionated takes and 'here's what they're not telling you' angles drive comments and shares. Balanced takes underperform — a clear perspective wins.",
    "Other": "Content creator: Apply strong hook principles — establish what the video is about and why the viewer should keep watching within the first 2 seconds.",
}

# ── Per-niche Hook + Retention scoring criteria ───────────────────────────────
# Three archetypes: visual-first, verbal-first, hybrid.
# Each entry defines: best hook formulas, what kills the hook, retention mechanism,
# re-hook style, common retention killers, and energy curve interpretation.
# Injected directly into the Hook & Opening and Retention & Pacing scoring blocks.
NICHE_HOOK_RETENTION: dict = {
    # ── VISUAL-FIRST NICHES ───────────────────────────────────────────────────
    # Hook = first frame. Verbal hook is secondary. Retention = visual momentum.
    "Dance": {
        "archetype": "visual-first",
        "hook_formulas": "Cold open (show the most impressive move or drop FIRST, then context). Pattern interrupt (unexpected transition or freeze-frame). The verbal hook barely matters — the first frame IS the hook.",
        "hook_killers": "Starting with a slow walk-in, camera setup shot, or any frame that isn't the peak moment. Delayed music entry. Any 'intro' before the choreo starts.",
        "compound_trigger": "Combine visual impact (impressive move) + audio sync (drop exactly on beat 1) for maximum scroll-stop power. Both firing together outperforms either alone by 3-5x.",
        "retention_mechanism": "Beat-sync cuts — every cut must land on a beat. Visual momentum: the video should feel like it's accelerating, not maintaining pace. Each 4-8 second segment needs a new impressive element.",
        "mid_rehook": "At 8-12s, introduce a new move, angle change, or the climax sequence. If the video plateaus visually, viewers drop. The energy curve must slope upward through the middle.",
        "retention_killers": "Holding a static pose for more than 2 seconds mid-video. Off-beat cuts. Repeating the same move twice before the payoff. Slow sections without clear build-up purpose.",
        "energy_curve_rule": "Hook energy should be high. Mid energy must not drop more than 20% below hook — if it does, flag the exact timestamp. Closing energy should match or exceed hook energy (strong finish drives replays).",
        "loop_value": "Seamless loop (last frame matches first frame energy/position) is powerful for dance — triggers auto-replay. Check if the video ends in a way that could loop back.",
    },
    "Bollywood": {
        "archetype": "visual-first",
        "hook_formulas": "Cold open on the key expression or first beat of the song. Show the most emotionally resonant frame first. Pattern interrupt via facial expression change or costume reveal.",
        "hook_killers": "Starting before the song drop. Static standing pose with no energy. Any frame that doesn't show the dancer's face or expression.",
        "compound_trigger": "Visual impact (expression/move) + emotional audio (song recognition) — Indian audiences emotionally connect to Bollywood songs. If the song is recognisable within 0.5s, retention is naturally higher.",
        "retention_mechanism": "Emotional arc — the video should build feeling, not just show moves. Face + footwork balance. Song familiarity carries retention — viewers stay to hear the full verse/chorus.",
        "mid_rehook": "Costume change, location change, or the climax step at 8-12s. Transition on the antara (verse) keeps energy alive.",
        "retention_killers": "Too many similar angles without a change. Face not visible during the emotional peak. Audio cut-off before the natural song end.",
        "energy_curve_rule": "Energy should peak at the mukhda (chorus). Mid energy dip during antara is acceptable if it builds back. Flag any drop that doesn't recover.",
        "loop_value": "Less critical than Western dance — emotional resolution matters more than seamless loop.",
    },
    "Food": {
        "archetype": "visual-first",
        "hook_formulas": "Cold open on the finished dish, the most visually satisfying moment (pour, cut, stretch, steam). Never build up to it — show it first. 'What if' format works: show the surprising final result before the process.",
        "hook_killers": "Starting with raw ingredients, mise en place, or hands prepping with no context. Any frame that doesn't make the viewer hungry immediately. Text-heavy openers.",
        "compound_trigger": "Visual appetite trigger (colour/texture/steam) + ASMR audio (sizzle, crunch, pour) firing together = maximum scroll-stop. If the audio doesn't match the visual satisfaction, the hook underperforms.",
        "retention_mechanism": "Satisfying process reveals — each step should feel like a reward, not a chore. Fast-paced cuts between steps. The 'how did they do that' curiosity keeps viewers watching.",
        "mid_rehook": "The most surprising step or technique at 8-12s. An unexpected ingredient or method that viewers didn't see coming.",
        "retention_killers": "Steps that look too similar back-to-back. Narration that explains what viewers can already see. Long static shots of simmering or waiting.",
        "energy_curve_rule": "Energy should peak at the reveal moment. Flag any static mid-section. Closing shot of the finished dish should match opening hook energy.",
        "loop_value": "Loop from finished dish back to first ingredient works well — triggers 'I want to make this' re-watch.",
    },
    "Fashion": {
        "archetype": "visual-first",
        "hook_formulas": "Cold open on the final outfit or the most striking transformation frame. Colour contrast interrupt. 'Before/after' implied in the first 2 seconds without spelling it out.",
        "hook_killers": "Starting in plain clothes, messy room, or without the 'wow' frame visible. Slow zoom-in to reveal. Text-heavy intro before showing the look.",
        "compound_trigger": "Visual contrast (colour pop or dramatic outfit) + trending audio = compound hook. Outfit reveal on beat = maximum impact.",
        "retention_mechanism": "Transformation arc — the journey from before to after. Each styling step should visually change the look enough to justify the cut. Outfit details reveal (close-up of shoes, accessories) at mid-point.",
        "mid_rehook": "The 'walking out' or 'mirror reveal' moment at 8-12s. A second outfit or styling variation.",
        "retention_killers": "Too many shots of the same outfit from the same angle. Transition that doesn't show a clear visual change. Ending without a clear 'final look' moment.",
        "energy_curve_rule": "Hook energy high. Mid energy can dip slightly during process — must recover sharply at the reveal. Closing energy must be the highest point.",
        "loop_value": "Before/after loop (final outfit → back to beginning) is highly effective.",
    },
    "Travel": {
        "archetype": "visual-first",
        "hook_formulas": "Cold open on the single most visually stunning shot of the entire trip. Pattern interrupt via unexpected location or angle. No verbal hook needed — the visual must do all the work.",
        "hook_killers": "Starting with airport, packing, or travel logistics. Any shot that could be from anywhere. Slow fade-in. Text overlays that compete with the visual.",
        "compound_trigger": "Stunning visual + aspirational audio (cinematic music that matches the mood of the location). Both together create 'I want to be there' response.",
        "retention_mechanism": "Visual journey — each new location or moment should feel like a reward. Colour grade consistency. Storytelling arc: arrival → exploration → best moment → reflection.",
        "mid_rehook": "The second-best shot or a reveal of where they actually are (location text or context) at 8-12s.",
        "retention_killers": "Too many similar landscape shots without a human element. Jump cuts that disorient rather than energise. Missing a clear location context mid-video.",
        "energy_curve_rule": "Opening and closing energy should be the two highest points. Mid energy can dip for 'journey' sections but must not flatline.",
        "loop_value": "Final cinematic shot looping back to opening is effective for travel.",
    },
    "Beauty": {
        "archetype": "visual-first",
        "hook_formulas": "Cold open on the most dramatic transformation result or the most striking final look. Tease the before/after without showing the full before yet. 'Wait for it' structure — but only if the payoff is genuinely dramatic.",
        "hook_killers": "Starting with a bare face and building slowly. Flat lighting in the opening shot. Too much product placement before showing a result.",
        "compound_trigger": "Dramatic visual before/after + relatable verbal hook ('I can't believe this took 10 minutes'). The verbal hook validates that the result is achievable.",
        "retention_mechanism": "Step-by-step transformation — each step should show visible progress. Close-up shots of technique. Before/after comparison at mid-point to remind viewers of the journey.",
        "mid_rehook": "Reveal an unexpected product or technique at 8-12s. The 'this is the secret step' moment.",
        "retention_killers": "Steps where the visual difference is too subtle. Talking about a product without showing the effect on skin/hair. Long blending sections without visible change.",
        "energy_curve_rule": "Energy should escalate through the video, peaking at the final reveal. Flag any mid-section plateau.",
        "loop_value": "Final look → bare face loop is very effective if the transformation is dramatic.",
    },
    "Sports": {
        "archetype": "visual-first",
        "hook_formulas": "Cold open on the peak moment — the goal, the trick, the record, the fail. No build-up. Slow-motion interrupt on the most impressive frame.",
        "hook_killers": "Showing the wind-up or preparation before the moment. Any frame that doesn't immediately communicate athletic achievement or drama.",
        "compound_trigger": "Peak visual moment + crowd reaction audio or impact sound = compound hook. The audio amplifies the visual impact.",
        "retention_mechanism": "Multi-angle replays, slow-motion reveals, reaction shots. The 'how did that happen' curiosity. Countdown structures ('3 tricks in 60 seconds') create completion pressure.",
        "mid_rehook": "Slow-motion replay or a second peak moment at 8-12s.",
        "retention_killers": "Too much standard-speed footage between peak moments. Missing reaction or emotion after the key moment.",
        "energy_curve_rule": "Multiple energy peaks expected. Each peak should be higher or equal to the previous. Flag any section where energy drops between peaks for more than 5 seconds.",
        "loop_value": "Replay loop of the peak moment is extremely effective.",
    },
    "DIY & Crafts": {
        "archetype": "visual-first",
        "hook_formulas": "Cold open on the finished creation — show what viewers will learn to make before showing how. 'You can make this' implied by showing the result first.",
        "hook_killers": "Starting with raw materials that don't hint at the final product. Too much setup before any visible creation begins.",
        "compound_trigger": "Impressive finished product + 'this only costs ₹X / took X minutes' verbal hook. The result + accessibility combination drives saves.",
        "retention_mechanism": "Progressive reveal — each step should visually change the creation enough to justify staying. Satisfying transformation moments. Step numbering ('step 3 of 5') creates completion pressure.",
        "mid_rehook": "The most surprising or difficult step at 8-12s. A close-up of a satisfying detail.",
        "retention_killers": "Steps that look too similar. Slow sections without visible progress. Missing a 'before you think it's hard, watch this' moment.",
        "energy_curve_rule": "Steady energy through process steps, sharp peak at the reveal. Flag any extended plateau in the middle.",
        "loop_value": "Finished product → raw materials loop drives saves ('I need to remember how to make this').",
    },
    "Photography": {
        "archetype": "visual-first",
        "hook_formulas": "Cold open on the most striking final image or the most dramatic edit reveal. Before/after edit in first 2 seconds. 'How I shot this on [device]' structure — show the result first.",
        "hook_killers": "Starting with camera gear or settings without showing the result. Slow zoom into a mediocre shot.",
        "compound_trigger": "Dramatic edit before/after + surprising context ('this is shot on an iPhone 12') creates compound curiosity + social proof.",
        "retention_mechanism": "Side-by-side comparisons, step-by-step edit reveals, settings breakdown. The 'I can do this too' motivation.",
        "mid_rehook": "Reveal of the original unedited shot vs. current state at 8-12s.",
        "retention_killers": "Too many similar shots without clear progression. Edit steps that look identical.",
        "energy_curve_rule": "Peak at the before/after reveal. Flag any section where no visible change occurs.",
        "loop_value": "Before/after toggle loop is highly effective.",
    },
    # ── VERBAL-FIRST NICHES ───────────────────────────────────────────────────
    # Hook = the sentence structure. Visual is supporting. Retention = narrative tension.
    "Education": {
        "archetype": "verbal-first",
        "hook_formulas": "Contrarian ('Everyone teaches X wrong — here's what actually works'). Mistake hook ('I failed 3 exams before I learned this one method'). Curiosity gap ('The one study technique nobody talks about'). Numbered list ('3 things top rankers do that you don't'). Avoid time-based hooks — education audiences respond more to knowledge gaps than efficiency promises.",
        "hook_killers": "Starting with 'Hey guys, today we're going to learn about...' or any intro/pleasantry. Explaining what you're about to explain before explaining it. Any verbal preamble before the hook lands.",
        "compound_trigger": "Curiosity gap + social proof ('The study technique that 90% of toppers use but nobody teaches') scores highest. Count triggers: 1 trigger = weak, 2 = good, 3 = compound and near-unscrollable.",
        "retention_mechanism": "Open loop — create an unresolved question in the hook that only gets answered at the end. Numbered structure ('5 things') creates completion pressure — viewers stay to hear all 5. Each point should resolve one tension and create a new one.",
        "mid_rehook": "At 8-12s, introduce the most surprising or counterintuitive point. A 'and here's the one most people get wrong' moment re-earns attention from viewers who might be drifting.",
        "retention_killers": "Resolving the hook's promise too early (no reason to stay). Explaining things the viewer already knows without a twist. Monotone delivery without energy variation. Long transitions between points.",
        "promise_payoff": "The hook's promise MUST be delivered in full. If the hook says '5 mistakes', all 5 must appear. If the hook promises a counterintuitive insight, it must be genuinely surprising — not a restatement of common knowledge.",
        "energy_curve_rule": "Verbal energy matters more than visual energy here. Flag if the transcript shows long uninterrupted explanation segments (>15 seconds without a new point, question, or transition) — these are drop-off zones.",
        "loop_value": "Less important. A strong 'now you know X — and here's how to apply it today' ending drives saves over replays.",
    },
    "Finance": {
        "archetype": "verbal-first",
        "hook_formulas": "Shocking stat hook ('₹10,000 invested 10 years ago in X would be ₹4.2 lakh today'). Mistake hook ('I lost ₹50,000 because I didn't know this rule'). Contrarian ('Everyone tells you to save 20% — that advice is keeping you poor'). Credibility hook ('After studying 100 annual reports, here's what I found').",
        "hook_killers": "Generic advice tone without specificity. No numbers in the first 3 seconds. 'Today we're going to talk about investing...' style openers.",
        "compound_trigger": "Shocking number + personal stake ('I lost X') + authority ('after X years') = 3-trigger compound hook. Specificity of numbers is the single biggest trust signal in finance content.",
        "retention_mechanism": "Stakes-driven narrative — what could the viewer gain or lose? Numbered structure. Each financial insight should be tied to a specific rupee/dollar amount to stay concrete.",
        "mid_rehook": "'And here's the part most financial advisors won't tell you' at 8-12s. A second shocking number or comparison.",
        "retention_killers": "Abstract advice without numbers. Theoretical explanations without real examples. Missing the 'what this means for YOU' personalisation at each point.",
        "promise_payoff": "Financial hooks that promise a specific result ('how I made X') must deliver the actual method — not a teaser for a course or a generic tip.",
        "energy_curve_rule": "Flag any section longer than 20 seconds without a new data point, comparison, or concrete example.",
        "loop_value": "Saves-driven content — end with a 'screenshot this' or 'save this formula' moment.",
    },
    "Business": {
        "archetype": "verbal-first",
        "hook_formulas": "Contrarian ('Hustle culture is destroying your productivity — here's what actually works'). Mistake hook ('I wasted 2 years doing X before learning this'). Pain point hook ('If your business isn't growing, it's because of this one mistake'). Polarising opinion as hook.",
        "hook_killers": "Vague business advice tone. Missing a specific pain point or bold claim in the first 3 seconds. 'In today's video I'll share...' openers.",
        "compound_trigger": "Contrarian claim + personal proof ('I used to believe X until my business hit ₹1Cr') = 2-trigger compound. Adding a third trigger (curiosity gap — 'and the reason surprised me') makes it near-unscrollable.",
        "retention_mechanism": "Problem-solution arc with specific examples. Each claim backed by a result or number. The 'here's exactly how' structure keeps viewers waiting for the actionable step.",
        "mid_rehook": "The most counterintuitive insight or the 'I wish I knew this earlier' moment at 8-12s.",
        "retention_killers": "Theory without application. Missing social proof (numbers, results, client examples). Long segments without a new idea or pivot.",
        "promise_payoff": "Business content must deliver the tactical 'how' — not just the 'what'. A hook promising a system must show the actual system.",
        "energy_curve_rule": "Flag segments longer than 15 seconds without a new insight, example, or pivot in delivery energy.",
        "loop_value": "Saves-driven — end with a framework, checklist, or principle worth saving.",
    },
    "Motivation": {
        "archetype": "verbal-first",
        "hook_formulas": "Vulnerability hook ('I failed for 3 years before this happened'). Stakes hook ('At 24 I had ₹0 in my account and no job'). Contrarian ('Generic motivation is making you lazy — here's why'). Shocking personal truth — specific, not generic.",
        "hook_killers": "Generic inspiration ('You can do it', 'Believe in yourself'). Missing specificity — 'I struggled' is weak, 'I failed 17 job interviews in 6 months' is strong. Background music louder than voice in the first 3 seconds.",
        "compound_trigger": "Vulnerability + specific stakes + implied payoff ('I failed 17 interviews — here's the one thing that finally worked') = 3-trigger compound. Specificity is the multiplier in motivation content.",
        "retention_mechanism": "Emotional arc — tension (the failure/struggle) → turning point → resolution (the lesson). Viewers stay for the transformation. Each story beat must escalate emotional stakes.",
        "mid_rehook": "The turning point moment at 8-12s. The 'and then everything changed when...' beat. This is the highest-retention moment if delivered with genuine emotion.",
        "retention_killers": "Plateau in emotional energy — if the video stays at the same emotional register throughout, viewers drift. Generic advice after a strong personal story. Missing the specific lesson from the experience.",
        "promise_payoff": "Personal story hook must lead to a specific, transferable lesson — not just 'keep going'. The viewer's question is 'what did you actually do differently?'",
        "energy_curve_rule": "Emotional energy should build in a clear arc. Flag any section where the delivery becomes monotone or the content becomes generic after a strong personal opening.",
        "loop_value": "Less important. Strong 'here's your one takeaway' ending drives shares over replays.",
    },
    "Self Improvement": {
        "archetype": "verbal-first",
        "hook_formulas": "Numbered list ('3 habits that changed my mornings in 30 days'). Time-based ('What 90 days of cold showers taught me'). Contrarian ('The productivity advice you follow is making you worse'). Mistake hook ('I spent 5 years doing morning routines wrong').",
        "hook_killers": "Generic opener without a specific claim. 'So today I want to talk about productivity...' style. Any preamble before the hook.",
        "compound_trigger": "Numbered list + time-based + personal proof ('3 habits I did for 90 days that actually worked') = 3-trigger compound. The combination of specificity, timeline, and personal validation is near-unscrollable.",
        "retention_mechanism": "Numbered structure creates completion pressure — viewers stay to hear all items. Each item should be specific and surprising, not generic. 'And the third one shocked me' teasers work.",
        "mid_rehook": "The most surprising habit or tip in the list — save it for 8-12s, not the beginning. Tease it early ('the last one is controversial') to hold viewers.",
        "retention_killers": "Listing items that viewers already know. Missing the 'why this works' for each item. Ending before delivering the most surprising item.",
        "promise_payoff": "Numbered hook must deliver all promised numbers. Time-based hook must include the actual result achieved.",
        "energy_curve_rule": "Energy should build item by item. Flag if the last item has less energy than the first — the most powerful point should be the most energetically delivered.",
        "loop_value": "Saves-driven — end with a summary slide or 'screenshot this checklist' moment.",
    },
    "Health & Wellness": {
        "archetype": "verbal-first",
        "hook_formulas": "Myth-bust hook ('The diet advice your doctor gives you is outdated'). Surprising fact hook ('Your gut bacteria affect your mood more than your brain does'). Mistake hook ('I damaged my health for 3 years doing this'). Contrarian + authority ('As someone who studied nutrition for 8 years...').",
        "hook_killers": "Generic wellness advice without a specific surprising claim. Visual-only opener without a strong verbal hook. Missing the 'you don't know this yet' element.",
        "compound_trigger": "Myth-bust + personal authority + immediate value ('The one supplement 90% of Indians are deficient in that affects everything') = 3-trigger compound.",
        "retention_mechanism": "Each claim backed by a mechanism ('here's WHY this happens in your body'). Practical application after each insight. Visual proof (charts, before/after, studies on screen) in mid-video.",
        "mid_rehook": "The most surprising health fact or the 'this affects you right now' personalisation at 8-12s.",
        "retention_killers": "Claims without mechanisms or proof. Advice that feels like it could be from any generic wellness account. Missing personalisation ('if you do X, then you're at risk').",
        "promise_payoff": "Health content must deliver the specific actionable — not just the problem. 'You're deficient in X' must be followed by 'here's how to fix it'.",
        "energy_curve_rule": "Flag any segment longer than 20s without a new fact, mechanism, or application.",
        "loop_value": "Saves-driven — end with a specific protocol or checklist.",
    },
    "Marketing": {
        "archetype": "verbal-first",
        "hook_formulas": "Result hook ('$0 ad spend, 100K views in 48 hours — here's exactly what I did'). Contrarian ('Everything you've been taught about content marketing is wrong'). Mistake hook ('I burned ₹2 lakh on ads before learning this'). Case study hook ('How a 23-year-old got 10K customers without a single paid ad').",
        "hook_killers": "Generic marketing tips without proof. Missing a specific result or number. 'In this video I'll teach you about...' openers.",
        "compound_trigger": "Specific result + low barrier + counterintuitive method ('I got 10K followers in 30 days without posting daily — here's the system') = 3-trigger compound.",
        "retention_mechanism": "Case study structure — show proof, then explain the method. Each tactic backed by a specific result. 'Steal this exact template' structures drive saves.",
        "mid_rehook": "Reveal the most counterintuitive or surprising tactic at 8-12s.",
        "retention_killers": "Tactics without proof. Generic advice that works for any niche. Missing the 'how to implement this today' step.",
        "promise_payoff": "Marketing hooks promising a system must show the actual system — not a teaser.",
        "energy_curve_rule": "Flag any abstract segment longer than 15s without a concrete example or number.",
        "loop_value": "Saves-driven — frameworks, templates, and checklists drive saves over replays.",
    },
    "News & Politics": {
        "archetype": "verbal-first",
        "hook_formulas": "Headline hook ('India just made a decision that will affect every family — here's what they're not telling you'). Contrarian angle ('The news coverage on X is completely missing the point'). Stakes hook ('This policy change affects your salary — most people have no idea').",
        "hook_killers": "Balanced, neutral openers — opinionated framing outperforms balanced framing. Missing a specific stake for the viewer. Too much context before the headline.",
        "compound_trigger": "Surprising fact + personal stake ('This new law will affect YOUR taxes starting next month — and most people have no idea') = 2-trigger compound. Urgency amplifies both.",
        "retention_mechanism": "What it means for YOU framing throughout. 'And here's the part they buried in paragraph 47' structure. Progressive reveal of information — most important first, context second.",
        "mid_rehook": "The most surprising buried fact or the 'this is what mainstream media isn't saying' reveal at 8-12s.",
        "retention_killers": "Too much background before getting to the point. Missing the viewer's personal stake. Balanced analysis that never takes a position.",
        "promise_payoff": "Must deliver the 'what this means for you' — not just the news itself.",
        "energy_curve_rule": "Opening should be the highest energy point. Flag any segment that loses urgency.",
        "loop_value": "Shares-driven content — end with a strong opinion or call-to-comment.",
    },
    # ── HYBRID NICHES ─────────────────────────────────────────────────────────
    # Hook = visual interrupt AND verbal hook working together.
    # Retention = audio-visual sync + narrative tension combined.
    "Comedy": {
        "archetype": "hybrid",
        "hook_formulas": "Setup-punchline in 3 seconds (protect the punchline). Absurd visual + relatable verbal setup simultaneously. Ironic title card as the hook (text on screen creates the curiosity gap, visual creates the pattern interrupt). 'Expectation vs reality' — establish the expectation visually in 2 seconds, subvert it with text or audio.",
        "hook_killers": "Giving away the punchline in the first frame. Slow build-up to the joke with no pattern interrupt. A funny video with a bad first frame — viewers never get to the punchline.",
        "compound_trigger": "Pattern interrupt visual (unexpected/absurd frame) + curiosity gap text ('When she says she's almost ready') + relatable setup = 3-trigger compound. The text and visual must work together, not duplicate each other.",
        "retention_mechanism": "Punchline protection — everything in the mid-section should escalate tension toward the payoff without giving it away. Each beat adds a layer. Loop structure: end frame flows back to beginning to trigger re-watch for the joke's setup.",
        "mid_rehook": "A secondary comedic beat or escalation at 8-12s — a reaction shot, an unexpected turn, or a second layer to the joke.",
        "retention_killers": "Mid-video explanation of the joke (never explain comedy). Static shot that holds too long before the punchline. Punchline arriving too early (nothing to stay for after).",
        "promise_payoff": "Comedy hook implies a payoff (the punchline or the twist). If the payoff doesn't land, viewers feel cheated. The bigger the setup, the more surprising the punchline must be.",
        "energy_curve_rule": "Energy should build through the video and peak at the punchline. Flag any energy drop between setup and payoff. Closing energy must be the highest point.",
        "loop_value": "Extremely important — comedy loops drive the highest rewatch rates. Check if the last frame creates a seamless loop back to the first.",
    },
    "IndianCreator": {
        "archetype": "hybrid",
        "hook_formulas": "Relatable Indian moment as visual + Hinglish verbal hook ('Ye toh sabke sath hota hai'). Personality-first hook — face + expression + one punchy line in 1.5s. Contrarian Indian culture take ('Gen Z ki problem kya hai? Main bata ta hoon'). POV format with an instantly relatable Indian situation.",
        "hook_killers": "Generic 'Hey guys' opener. Missing cultural specificity — hooks that could work for any global creator underperform with Indian audiences. Slow build-up before the relatable moment lands.",
        "compound_trigger": "Visual relatability (recognisable Indian setting/situation) + Hinglish verbal hook + emotional truth = 3-trigger compound. Indian audiences respond to cultural specificity above all else.",
        "retention_mechanism": "Emotional or comedic arc with cultural resonance. Each beat should feel specific to the Indian experience. Hinglish pacing — the mix of Hindi and English should feel natural, not performative.",
        "mid_rehook": "A cultural reference, twist, or the most relatable moment at 8-12s. 'Aur ye toh sirf shuru hai' type escalation.",
        "retention_killers": "Generic advice that loses cultural specificity mid-video. Slowing down after a strong opening personality moment. Missing the 'this is SO Indian' element that drives DM shares.",
        "promise_payoff": "Personality-led content must maintain the same energy and personality throughout — viewers stay for the person, not just the information.",
        "energy_curve_rule": "Personality energy must stay consistent or build. Flag any section where the creator's energy drops or becomes generic.",
        "loop_value": "Shares-driven — 'Send this to your [relationship]' ending drives DM sends more than loops.",
    },
    "Gaming": {
        "archetype": "hybrid",
        "hook_formulas": "Cold open on the peak gaming moment (clutch play, fail, world record attempt). Reaction facecam simultaneously with the peak moment — the human reaction amplifies the game moment. 'You won't believe this happened' structure with the clip starting immediately.",
        "hook_killers": "Starting with lobby screen, loading, or any non-action game footage. Missing facecam reaction on the peak moment. Over-explaining the setup before showing the clip.",
        "compound_trigger": "Peak game moment + genuine facecam reaction + audio (crowd sound, game audio, or hype music) = 3-trigger compound. The human reaction is what separates gaming content that goes viral from content that doesn't.",
        "retention_mechanism": "Multi-angle replays of the peak moment. Build-up to the next peak (tease the next clip). Commentary that adds context without over-explaining. Emotional journey — the viewer lives the experience through the creator.",
        "mid_rehook": "Slow-motion replay or the next peak clip at 8-12s.",
        "retention_killers": "Standard gameplay footage between peak moments. Commentary that narrates what viewers can already see. Missing the emotional reaction at key moments.",
        "energy_curve_rule": "Must have multiple energy peaks. Flag any section longer than 8 seconds without a new peak, reaction, or significant game event.",
        "loop_value": "Peak moment loop is effective for gaming highlights.",
    },
    "Entertainment": {
        "archetype": "hybrid",
        "hook_formulas": "Dramatic/shocking opening moment first (cold open). 'You won't believe' implied by starting at the most unexpected moment. Story hook — open at the most dramatic point in the narrative, then rewind.",
        "hook_killers": "Starting at the beginning of the story chronologically — always start at the peak. Slow build to the drama. Missing the emotional or surprise element in the first frame.",
        "compound_trigger": "Visual drama/surprise + verbal context ('So this actually happened to me') + implied resolution ('and what I did next changed everything') = 3-trigger compound.",
        "retention_mechanism": "Narrative tension — the hook opens at the peak, then rewinds to explain how we got there. Viewers stay to understand the full story. Cliffhanger beats throughout.",
        "mid_rehook": "A secondary revelation or escalation at 8-12s. 'And then it got worse' moment.",
        "retention_killers": "Resolving the tension too early. Slow narration sections without new information. Missing the emotional payoff.",
        "promise_payoff": "Story hooks must deliver the full story — not cut off before the resolution.",
        "energy_curve_rule": "Non-linear energy is acceptable — peak at open, dip for context, peak again at resolution. Flag if mid-section dip lasts more than 15 seconds.",
        "loop_value": "Shares-driven — 'I can't believe this happened' endings drive DMs.",
    },
    "Vlogs": {
        "archetype": "hybrid",
        "hook_formulas": "Cold open on the most cinematic or emotionally loaded moment of the day — NOT a morning routine. Story hook ('This is the day everything went wrong — and right'). Visual storytelling with a verbal context line simultaneously.",
        "hook_killers": "Starting with waking up, making coffee, or any routine moment. Slow build-up before the 'event' of the day is established. Diary-style opener without a clear story promise.",
        "compound_trigger": "Cinematic visual (best shot of the day) + verbal story promise ('I didn't expect this to happen') + location/context = 3-trigger compound.",
        "retention_mechanism": "Narrative arc — the day should have a clear story structure with a beginning, middle, and an emotionally satisfying or surprising end. Each segment should advance the story.",
        "mid_rehook": "The turning point of the day's story at 8-12s. The 'and then this happened' beat.",
        "retention_killers": "Diary-style meandering without a clear arc. Multiple similar-looking locations without clear transitions. Missing the emotional payoff at the end.",
        "promise_payoff": "Story hook must resolve — if the opening promises drama, the video must deliver the resolution.",
        "energy_curve_rule": "Story arc energy — can start high, dip for context, must resolve at a high emotional point. Flag any plateau longer than 20s.",
        "loop_value": "Less important — strong emotional ending drives shares over replays.",
    },
    "Lifestyle": {
        "archetype": "hybrid",
        "hook_formulas": "Aspirational visual + relatable verbal hook ('This is what my mornings look like now vs 2 years ago'). Before/after implied without spelling it out. 'This could be your life' framing — show the aspirational moment, then invite the viewer in.",
        "hook_killers": "Generic lifestyle shot without context. Missing the aspirational or relatable element that makes it specific to the viewer.",
        "compound_trigger": "Aspirational visual + relatable verbal hook + personal transformation story = 3-trigger compound.",
        "retention_mechanism": "Aspiration arc — show the lifestyle, explain how to get there, make it feel achievable. Each moment should either inspire or teach.",
        "mid_rehook": "The 'here's how I actually got here' reality check at 8-12s — vulnerability within aspiration drives trust.",
        "retention_killers": "Pure aspiration without the 'how' — viewers drift if they feel the lifestyle is unattainable. Missing personal story.",
        "energy_curve_rule": "Consistent aspirational energy with one vulnerability dip and one peak. Flag if the video stays at the same register throughout.",
        "loop_value": "Aspirational loop (final aesthetic moment → beginning) works well.",
    },
    "Music": {
        "archetype": "hybrid",
        "hook_formulas": "Start at the catchiest or most emotionally resonant bar — not the intro. Live performance: open on the most expressive moment. Cover: show the unique interpretation in the first 2 seconds.",
        "hook_killers": "Starting with the intro or build-up before the best part. Generic performance setup shots without immediately showing vocal/instrumental skill.",
        "compound_trigger": "Peak musical moment + visible emotion from performer + the specific musical choice that makes this version unique = 3-trigger compound.",
        "retention_mechanism": "Musical arc — if it's a complete song, it has its own retention structure. If it's a clip, it must feel complete. Emotional build to the highest note or most powerful moment.",
        "mid_rehook": "The bridge or the most technically impressive moment at 8-12s.",
        "retention_killers": "Holding a note or section too long without dynamic change. Missing the climax or the big moment.",
        "energy_curve_rule": "Musical energy should follow the song's natural arc. Flag if the video cuts before the emotional peak of the song.",
        "loop_value": "Hook of the song looping back to beginning is very effective if the hook is strong.",
    },
    "Memes": {
        "archetype": "hybrid",
        "hook_formulas": "Template recognition + subversion in the first 0.5 seconds. Audio recognition — trending sound used in an unexpected way. The setup must be instantly recognisable to the meme-literate audience.",
        "hook_killers": "Using a meme template that's past its peak. Any delay before the recognisable element appears. Explaining the meme.",
        "compound_trigger": "Recognisable template + unexpected subversion + relatable situation = 3-trigger compound. The subversion IS the joke.",
        "retention_mechanism": "The payoff must arrive within 5-8 seconds. Meme content has the shortest acceptable retention window — if the joke hasn't landed by 8s, viewers are gone.",
        "mid_rehook": "A second layer to the joke or an unexpected extension at 5-8s.",
        "retention_killers": "Payoff arriving too late. Over-explaining. Using a format that peaked 2+ weeks ago.",
        "promise_payoff": "Template sets an expectation — the subversion must be genuinely surprising or relatable to earn the laugh.",
        "energy_curve_rule": "Peak must arrive early. Flag if the highest energy moment is after the 8-second mark.",
        "loop_value": "Extremely high — meme loops are designed to be rewatched. Check for seamless loop structure.",
    },
    "Fitness": {
        "archetype": "hybrid",
        "hook_formulas": "Visual transformation first (before/after) + verbal hook ('I did this every day for 90 days'). Technique reveal ('Most people do this exercise wrong'). Challenge hook ('Can you do this in 30 days?').",
        "hook_killers": "Starting with gym setup or warm-up. Generic 'let's get it' energy without a specific hook. Missing either the visual proof or the verbal promise.",
        "compound_trigger": "Visual result + time-based claim + contrarian element ('I never went to the gym and still got this result') = 3-trigger compound.",
        "retention_mechanism": "Workout structure creates natural retention — sets, reps, and exercises create mini completion pressures. Transformation reveal at the end of a workout series.",
        "mid_rehook": "The most impressive exercise or the surprising technique at 8-12s.",
        "retention_killers": "Too much rest footage. Missing the form correction or the 'here's why this works' moment.",
        "energy_curve_rule": "Energy should match the workout intensity. Flag any visual or audio plateau during high-intensity segments.",
        "loop_value": "Challenge hooks ('try this') drive saves over loops.",
    },
    "Tech": {
        "archetype": "hybrid",
        "hook_formulas": "Problem demonstration first ('Watch what happens when I do this'). 'I didn't know this existed' hook — show the capability immediately. Before/after demo in 3 seconds.",
        "hook_killers": "Starting with a verbal explanation before showing the tech in action. Over-explaining the problem before demonstrating the solution.",
        "compound_trigger": "Visual demonstration + 'I didn't know this was possible' implication + immediate value ('this saves 2 hours every day') = 3-trigger compound.",
        "retention_mechanism": "Demo-first structure — show the result, then explain how. 'Stay until the end for the best part' tease works well in tech content.",
        "mid_rehook": "The most jaw-dropping capability or the 'and it also does this' reveal at 8-12s.",
        "retention_killers": "Too much verbal explanation without showing the tech doing something. Missing the 'wow' demo moment.",
        "energy_curve_rule": "Demonstration peaks should drive the energy curve. Flag any extended verbal-only segment without a new visual demonstration.",
        "loop_value": "Demo loop (showing the result again) drives saves.",
    },
    "Other": {
        "archetype": "hybrid",
        "hook_formulas": "Apply the most relevant formula for your content type: contrarian, mistake, numbered list, time-based, or question. The first 3 seconds must establish WHAT the video is about AND why the viewer should care — simultaneously.",
        "hook_killers": "Any intro, pleasantry, or context-setting before the hook. 'Hey guys' or 'Welcome back' openers. Starting with a slow build-up.",
        "compound_trigger": "Aim to fire at least 2 psychological triggers in the first sentence: curiosity + social proof, or stakes + immediacy, or controversy + personal proof.",
        "retention_mechanism": "Open loop — create an unresolved question or promise early that only gets resolved at the end. Check that the video has a clear reason for viewers to stay beyond the hook.",
        "mid_rehook": "At 8-12s, introduce the most surprising or valuable element. Don't save the best for the very end — tease it at the midpoint.",
        "retention_killers": "No clear narrative arc. Resolving the tension too early. Missing a mid-video surprise or escalation.",
        "promise_payoff": "Whatever the hook promises, the video must deliver in full. Unfulfilled hooks cause viewers to bounce and damage future content reach.",
        "energy_curve_rule": "Flag any section longer than 15 seconds without a new idea, visual change, or energy shift.",
        "loop_value": "Depends on content type — save-worthy endings drive more algorithmic value than loops for most content.",
    },
}


def _build_hook_retention_context(niche: str) -> str:
    """Build niche-specific Hook & Retention scoring context to inject into the Claude prompt."""
    entry = NICHE_HOOK_RETENTION.get(niche) or NICHE_HOOK_RETENTION.get("Other")
    archetype = entry["archetype"]

    archetype_label = {
        "visual-first": "VISUAL-FIRST NICHE — the first frame IS the hook. Verbal hook is secondary.",
        "verbal-first": "VERBAL-FIRST NICHE — the sentence structure IS the hook. What you say in the first 3 seconds matters more than what you show.",
        "hybrid": "HYBRID NICHE — the visual interrupt AND the verbal hook must work TOGETHER. Neither alone is enough.",
    }.get(archetype, "")

    ctx = f"""
NICHE-SPECIFIC HOOK & RETENTION RULES FOR {niche.upper()} ({archetype_label}):

HOOK SCORING:
- Best formulas for this niche: {entry['hook_formulas']}
- Hook killers (auto-flag these as critical failures): {entry['hook_killers']}
- Compound trigger scoring: {entry['compound_trigger']}
  Count how many of these 5 triggers fire in the first 3 seconds: (1) curiosity gap, (2) social proof/authority, (3) FOMO/urgency, (4) controversy/contrarian, (5) immediate value/efficiency.
  0 triggers = critical failure (score 20-35). 1 trigger = weak (36-55). 2 triggers = good (56-72). 3+ triggers = compound hook, near-unscrollable (73-90+).
- Slow build-up detection: If the first 3 seconds contain ANY pleasantry, intro, or context-setting before the hook lands, flag it as a critical failure — 'Hey guys', 'Welcome back', 'So today we're going to...' are automatic score penalties.
- Hook formula identification: Name the specific formula used (contrarian / mistake / numbered list / time-based / question / cold open / pattern interrupt / none). A video with no identifiable formula scores lower than one using any formula correctly.

RETENTION & PACING SCORING:
- Primary retention mechanism for this niche: {entry['retention_mechanism']}
- Mid-video re-hook requirement: {entry['mid_rehook']}
- Retention killers to flag: {entry['retention_killers']}
- Energy curve interpretation: {entry['energy_curve_rule']}
- Loop/replay value: {entry.get('loop_value', 'Assess based on whether the ending creates a reason to rewatch.')}
{f"- Promise-payoff check: {entry['promise_payoff']}" if 'promise_payoff' in entry else ""}

HOOK ↔ RETENTION CROSS-CHECK (mandatory):
- Identify the hook's core promise (what does the first 3 seconds implicitly or explicitly promise the viewer?).
- Verify the video delivers that promise in full. If the hook promises X but the video delivers Y, flag this as a retention failure — not a hook failure.
- If the hook fires 2+ triggers but the mid-section has a visible energy/momentum drop, the Hook score stays high but Retention score must reflect the drop with a specific timestamp.
"""
    return ctx


# Per-niche Trend Alignment scoring lens — tells Claude WHAT to look for
# beyond just hashtags. Each niche has different trend signals.
NICHE_TREND_LENS = {
    "Comedy": """TREND ALIGNMENT — COMEDY LENS:
Score based on FORMAT trendiness, NOT hashtags. Hashtags are irrelevant in comedy.
What actually drives viral distribution in comedy:
1. FORMAT: Is this using a currently viral comedy format? (POV, 'me explaining to my mom', reaction overlay, text-as-punchline, lip-sync + twist, expectation vs reality). Score high if yes.
2. AUDIO: Is the audio a trending sound being used ironically or creatively? Comedy creators who use trending audio with an unexpected twist get massive distribution. Score high if audio is recognisable or trending.
3. RELATABILITY TIMING: Is the topic something that is culturally relevant RIGHT NOW? A meme that was funny 6 months ago scores low. A fresh take on a current moment scores high.
4. LOOP STRUCTURE: Does the video end in a way that makes viewers want to replay? Comedy loops drive rewatch rate which is a top algorithm signal.
Fix suggestions must be about FORMAT and AUDIO, not hashtags.""",

    "Fitness": """TREND ALIGNMENT — FITNESS LENS:
What drives viral distribution in fitness:
1. FORMAT: Is this using trending fitness formats? (transformation reveal, 'day in my life' workout, debunking fitness myths, 'I tried X for 30 days', POV gym content). Score high if yes.
2. AUDIO: Fitness content goes viral when paired with high-energy trending audio. Is the audio matching the intensity of the workout? Is it a trending gym audio?
3. TRAINING STYLE TREND: Is the training style currently trending? (Pilates, 75 Hard, zone 2 cardio, calisthenics are currently popular. Old-school bodybuilding content is declining). Score based on whether the training approach is timely.
4. AESTHETIC: Clean, minimal gym aesthetic with good lighting is currently the dominant trend. Dark, old-school bodybuilding aesthetic is declining.
Fix suggestions: Suggest specific format tweaks (e.g. 'Show your starting weight/body at the beginning — transformation reveal format is the top-performing format in fitness right now').""",

    "Food": """TREND ALIGNMENT — FOOD LENS:
What drives viral distribution in food:
1. FORMAT: Is this using a trending food format? (ASMR cooking, aesthetic overhead shots, '3-ingredient recipe', speed cooking with transitions, 'what I eat in a day', street food discovery). Score high if yes.
2. INGREDIENT/DISH TREND: Is this dish or ingredient currently having a cultural moment? (Viral ingredients cycle — if this dish is having a moment, score high. If it's a staple that's been done 1M times, score lower).
3. VISUAL STYLE: Current dominant aesthetic is clean, minimal, natural light, overhead or 45-degree angle. Dark moody food photography is declining on Reels.
4. AUDIO: Food ASMR (sizzling, crunching, chopping) with no music is trending. Upbeat background music works for speed-cook formats.
Fix suggestions: Reference specific trending food formats, not hashtags. E.g. 'The overhead shot with the finished dish as the hook is currently the highest-performing food format — restructure your opening to show the finished dish first.'""",

    "Fashion": """TREND ALIGNMENT — FASHION LENS:
What drives viral distribution in fashion:
1. STYLE TREND: Is the outfit/style currently trending? (Specific aesthetics cycle — check if this fits current micro-trends: quiet luxury, Y2K, mob wife, coastal grandmother, etc.). Score based on whether the style is timely or dated.
2. FORMAT: Is this using trending fashion formats? (GRWM, outfit of the day, 'styling the same piece 5 ways', thrift haul, dupe finds). Score high if format is currently popular.
3. AUDIO: Fashion transitions MUST be synced to trending audio. Off-beat transitions with non-trending audio score very low.
4. COLOUR PALETTE: Current trending colour palettes on Reels vs outdated ones matters. If the outfit colours match current Pantone/seasonal trends, mention it.
Fix suggestions: Identify the exact micro-trend the video is or isn't hitting. Name the aesthetic. Tell them which trending format to try next.""",

    "Travel": """TREND ALIGNMENT — TRAVEL LENS:
What drives viral distribution in travel:
1. DESTINATION TREND: Is this destination currently having a viral moment? Some destinations cycle through virality. If this is a trending destination, score high. If it's oversaturated (Bali, Santorini done generically), score lower.
2. FORMAT: Is this using trending travel formats? ('Things nobody tells you about X', 'solo female travel', 'budget travel to X', 'hidden gems in X', cinematic vlog style). Score based on format trendiness.
3. CINEMATIC QUALITY: Travel content is increasingly cinematic. Shaky phone footage with no colour grade performs poorly. Smooth, colour-graded clips with transitions score higher for trend alignment.
4. AUDIO: Trending ambient or cinematic music dramatically boosts travel content distribution. Is the audio choice matching current travel content aesthetics?
Fix suggestions: Reference the format and audio approach, not hashtags. Suggest specific format angles that are currently trending for this type of destination.""",

    "Tech": """TREND ALIGNMENT — TECH LENS:
What drives viral distribution in tech:
1. TOPIC TIMELINESS: Is this about something that just launched, was just announced, or is currently in the news cycle? Tech content has a very short timeliness window — a review posted 2 weeks after launch underperforms one posted at launch.
2. FORMAT: Is this using trending tech formats? ('Hidden features of X', 'I tried X for 30 days', 'This $X gadget changed my life', 'AI tool that does X', comparison videos). Score based on format.
3. SPECIFICITY: Generic tech tips underperform. 'This specific ChatGPT prompt saved me 3 hours' outperforms 'AI is changing productivity'. Score higher for specificity.
4. PLATFORM RELEVANCE: Is this topic something the Reels algorithm is currently boosting? AI tools, iPhone tips, budget tech, and 'hidden features' formats are currently dominant.
Fix suggestions: Tell them if the topic is past its timeliness window. Suggest the specific trending tech format that would suit this content better.""",

    "Education": """TREND ALIGNMENT — EDUCATION LENS:
What drives viral distribution in education:
1. FORMAT: Is this using trending edu formats? ('Did you know X', numbered lists ('3 things they don't teach in school'), myth-busting, 'I read 100 books so you don't have to', whiteboard explainer). Score high if yes.
2. TOPIC TIMELINESS: Is this topic currently being searched or discussed? Education content tied to current events (new laws, current economic conditions, recent discoveries) outperforms evergreen content in the short term.
3. HOOK FORMAT: The 'curiosity gap' hook is the dominant education trend. 'Here's why X happens' underperforms 'You've been doing X wrong your whole life'. Score based on how strong the curiosity gap is.
4. LENGTH: Edu content is trending shorter — 15-30 second fact drops outperform 60+ second lectures on Reels.
Fix suggestions: Focus on hook format and content structure, not hashtags.""",

    "Finance": """TREND ALIGNMENT — FINANCE LENS:
What drives viral distribution in finance:
1. TOPIC TIMELINESS: Finance content is highly news-cycle dependent. Interest rates, market events, tax deadlines, viral money stories — is this tied to something currently in the news? If yes, score high.
2. FORMAT: Trending finance formats: 'How I saved X in Y months', 'Why the rich don't pay taxes', 'Things your bank doesn't tell you', 'I invested X and here's what happened'. Score based on format.
3. HOOK TYPE: Anger/outrage hooks ('The government doesn't want you to know this') and aspirational hooks ('How I retired at 32') dominate finance. Does the hook use either approach?
4. VISUAL: Talking head with text overlays showing numbers/charts is the dominant format. Aesthetic finance content (showing wealth) is declining — authenticity and relatability are trending up.
Fix suggestions: Reference format and hook type. If the topic is tied to a current financial event, call that out as a strength or missed opportunity.""",

    "Business": """TREND ALIGNMENT — BUSINESS LENS:
What drives viral distribution in business:
1. FORMAT: Trending business formats: 'Day in my life as a [job]', 'How I built X to $X/month', 'Things nobody tells you about entrepreneurship', 'I failed at X — here's what I learned', 'Business idea breakdown'. Score based on format.
2. CONTRARIAN ANGLE: Business content that challenges mainstream advice outperforms consensus advice. 'Why I quit my 6-figure job' outperforms 'How to be productive'. Does this video take a contrarian position?
3. SPECIFICITY OF RESULTS: Vague success stories underperform specific ones. '$0 to $10K in 3 months selling X' outperforms 'You can make money online'. Score based on specificity.
4. VULNERABILITY: Authentic failure and struggle content is trending strongly in business. Polished 'hustle porn' is declining.
Fix suggestions: Identify whether the video uses contrarian framing. Suggest specific format angles.""",

    "Lifestyle": """TREND ALIGNMENT — LIFESTYLE LENS:
What drives viral distribution in lifestyle:
1. AESTHETIC TREND: Lifestyle content is aesthetic-driven. Is the visual style matching current dominant aesthetics? (Clean minimal, 'that girl', coastal aesthetic, dark academia, cottagecore — aesthetics cycle fast).
2. FORMAT: Trending lifestyle formats: 'Day in my life', 'morning routine', 'what I eat in a day', 'apartment/room tour', 'realistic vs aesthetic' comparisons. Score based on format.
3. AUDIO: Lifestyle content HEAVILY relies on trending audio for distribution. The same video with a trending audio vs a non-trending one has dramatically different reach.
4. ASPIRATIONAL vs RELATABLE: The trend has shifted from purely aspirational (perfect life) toward authentic + aspirational (real life but elevated). Pure perfection content is declining.
Fix suggestions: Identify the specific aesthetic the video is targeting. Assess whether the audio choice is aligned with current trends for that aesthetic.""",

    "Beauty": """TREND ALIGNMENT — BEAUTY LENS:
What drives viral distribution in beauty:
1. TREND PRODUCT/TECHNIQUE: Is this featuring a currently viral product, technique, or look? Beauty trends cycle extremely fast — a foundation tutorial about a product that blew up last month but is now mainstream scores lower than a tutorial about something that just went viral this week.
2. FORMAT: Trending beauty formats: dramatic transformation, 'I tried X viral product', 'dupe for X', GRWM, 'honest review of X', 'doing my makeup in X minutes'. Score based on format.
3. AUDIO: Beauty tutorials pair best with trending, upbeat, or aesthetically matching audio. Silent ASMR beauty is trending for certain sub-niches.
4. SKIN/MAKEUP TREND: Is the makeup look/skincare approach currently trending? (Glass skin, clean girl, no-makeup makeup, bold liner — trends shift seasonally). Score based on whether the look is timely.
Fix suggestions: Name the specific beauty trend the video is targeting and whether it's rising, peak, or declining.""",

    "Music": """TREND ALIGNMENT — MUSIC LENS:
What drives viral distribution in music:
1. SONG TIMELINESS: If this is a cover — is the original song currently trending? Covering a song at its viral peak gets massive distribution. Covering something 2 months after its peak gets very little. If this is an original, is the genre/style currently trending on Reels?
2. FORMAT: Trending music formats: 'cover with a unique twist', 'I wrote a song about X', 'process of making a beat', 'reacting to my first song', 'genre mashup'. Score based on format.
3. AUDIO QUALITY PRESENTATION: Music content where the production quality is immediately obvious in the first 2 seconds scores higher. Compressed, low-quality audio kills music content even if the performance is strong.
4. HOOK MOMENT: For music, the hook IS the musical hook. Did the best part of the song start in the first 3 seconds?
Fix suggestions: Be specific about song timing and whether the cover is riding the right moment in the song's viral lifecycle.""",

    "Dance": """TREND ALIGNMENT — DANCE LENS:
What drives viral distribution in dance:
1. AUDIO CHOICE: This is the #1 signal. Is the song currently being used for dance content? A trending audio with original choreography gets massive algorithm distribution even without viral quality dancing. Check song trend match data carefully.
2. CHOREOGRAPHY ORIGINALITY: Is the choreography creator-driven and unique, or is it following an existing viral dance template? Both work but differently — original choreo on trending audio is the highest-scoring combination.
3. CHALLENGE POTENTIAL: Is this choreography simple enough that others would want to recreate it? Highly technical dances get views but low duets/recreations. Easy-to-follow choreo with a distinctive move or two gets challenges.
4. SYNC QUALITY: Beat sync and drop sync are the most shared moments in dance content. If the most impressive move doesn't land exactly on the musical peak, it's a major trend alignment miss.
Fix suggestions: Lead with audio assessment. If the song isn't trending, that's the primary fix regardless of how good the dancing is.""",

    "Bollywood": """TREND ALIGNMENT — BOLLYWOOD/INDIAN DANCE LENS:
What drives viral distribution in Bollywood content:
1. SONG TREND: Is this a currently trending Bollywood/Punjabi/regional song? Or a classic being revived? Song selection is the ENTIRE trend alignment score for Bollywood content. A classic song getting a revival moment (Tauba Tauba, Gallan Goodiyaan, etc.) scores as high as current chart toppers.
2. DANCE STYLE ALIGNMENT: Is the choreography style matching what's trending in Indian dance content? (Wedding choreography trends, Bharatnatyam fusion, simple trending steps that anyone can do). Classical styles score differently from commercial Bollywood.
3. CULTURAL MOMENT: Is this tied to a film release, a festival season, a wedding season? Timing to Indian cultural moments dramatically boosts Bollywood content.
4. COSTUME/AESTHETIC: Traditional vs fusion wear trends shift. Is the costume choice aligned with current Indian aesthetic trends?
Fix suggestions: Focus on song choice and cultural moment. Always reference whether the song is rising, at peak, or declining in usage across Indian creator content.""",

    "Gaming": """TREND ALIGNMENT — GAMING LENS:
What drives viral distribution in gaming:
1. GAME TIMELINESS: Is this a currently popular game? Gaming content is brutally trend-dependent — a clip from a game at its viral peak gets 10x the distribution of the same quality clip from a game that peaked 3 months ago.
2. CLIP TYPE: Trending gaming formats: clutch moments, insane plays, funny fails, 'I tried the hardest challenge in X', speedruns, 'day 1 vs day 100', beginner vs expert comparison. Score based on clip type.
3. REACTION/PERSONALITY: Pure gameplay underperforms gameplay + personality reaction. Facecam energy, commentary, and emotional reactions (rage, disbelief, hype) are the primary engagement drivers.
4. PLATFORM META: Each gaming platform (mobile vs console vs PC) has different trending content. Is this appropriate for the gaming sub-audience?
Fix suggestions: Lead with game timeliness assessment. If the game is past its peak, suggest pivoting to trending titles or adding cross-game commentary format.""",

    "Motivation": """TREND ALIGNMENT — MOTIVATION LENS:
What drives viral distribution in motivation content:
1. FORMAT: Trending motivation formats: cinematic quote video with b-roll, 'my story' vulnerability arc, '5 habits that changed my life', 'stop doing X' contrarian advice, 'nobody talks about this'. Score based on format.
2. AUDIO: Motivation content is HEAVILY audio-driven. Epic cinematic music, trending motivational speeches as audio, or emotionally resonant music dramatically impacts distribution. Is the audio choice matching content intensity?
3. SPECIFICITY: Generic 'you can do it' content is completely saturated. Specific personal stories with named outcomes ('I went from broke to X in Y months by doing Z') massively outperform generic inspiration.
4. HOOK VULNERABILITY: The trend has shifted hard toward authentic vulnerability as the hook. 'I failed for 3 years before this happened' outperforms 'Here's how to be successful'.
Fix suggestions: Assess specificity of the story and audio alignment. Vague motivation content is the most oversaturated space on Instagram — specificity is the only path to differentiation.""",

    "Health & Wellness": """TREND ALIGNMENT — HEALTH & WELLNESS LENS:
What drives viral distribution in health & wellness:
1. TOPIC TIMELINESS: Is this health topic currently trending? (Gut health, cortisol, nervous system regulation, seed cycling, cycle syncing, red light therapy — wellness trends cycle fast). Score high if the topic is in its trending window.
2. FORMAT: Trending wellness formats: myth-busting ('doctors won't tell you this'), personal protocol sharing ('my morning routine for X'), 'I tried X for 30 days', before/after health transformation.
3. AUTHORITY SIGNALS: Wellness audiences are increasingly sceptical. Citing specific studies, personal tracked results (blood work, sleep scores), or professional credentials scores higher than anecdotal claims.
4. VISUAL AESTHETIC: Clean, natural-light, minimal aesthetic is dominant in wellness. Overly produced content underperforms authentic-feeling content in this niche.
Fix suggestions: Identify whether the specific wellness topic is trending or declining. Wellness trend windows are often 3-6 months.""",

    "DIY & Crafts": """TREND ALIGNMENT — DIY & CRAFTS LENS:
What drives viral distribution in DIY content:
1. PROJECT TIMELINESS: Is this craft/project currently trending? (Specific craft trends cycle — crochet, candle making, resin art, thrift flips, room transformations all have viral moments). Score based on whether the craft type is in its trending window.
2. FORMAT: The 'result first' format dominates DIY. Showing the finished product in the first frame, then the process, then the result again drives saves. Process-only videos without a compelling reveal underperform.
3. SAVE POTENTIAL: DIY content thrives on saves ('I'll do this later'). Is the project achievable enough that viewers would save it? Extremely complex projects get admiration but few saves.
4. BUDGET SIGNAL: 'Budget DIY' and 'thrift flip' formats are trending strongly. 'Expensive materials, professional result' is declining vs 'cheap materials, impressive result'.
Fix suggestions: Focus on the result-first format and the save potential of the project.""",

    "Photography": """TREND ALIGNMENT — PHOTOGRAPHY LENS:
What drives viral distribution in photography content:
1. TECHNIQUE TREND: Is the photography technique currently trending? (Film photography revival, iPhone photography, cinematic colour grades, specific lighting setups like golden hour, neon photography). Score based on technique timeliness.
2. FORMAT: Trending photography formats: 'shoot with me', before/after edit reveal, 'how I got this shot' breakdown, iPhone vs DSLR comparison, 'recreating viral photos'. Score based on format.
3. EDITING STYLE TREND: Current trending edit styles vs outdated ones matter. Overly filtered/presets that were viral in 2021 score low. Natural film-grain, subtle colour grading scores high currently.
4. GEAR RELEVANCE: 'No gear needed' content (iPhone photography tips) has massive reach. Expensive gear content has a smaller but engaged audience.
Fix suggestions: Identify the specific photography technique and whether it's trending or declining. Suggest which trending photography format would suit this content type.""",

    "Entertainment": """TREND ALIGNMENT — ENTERTAINMENT LENS:
What drives viral distribution in entertainment content:
1. CULTURAL MOMENT: Is this tied to something currently happening in pop culture — a show, a celebrity moment, a viral event, a meme cycle? Entertainment content that rides a current moment gets massive algorithmic boost.
2. FORMAT: Trending entertainment formats: 'hot take on X', reaction to viral moment, 'ranking X', 'unpopular opinion about X', 'things that happened in X show/film that nobody talks about'.
3. HOOK CONTROVERSY: Entertainment content that opens with a controversial or surprising take gets significantly more comments (which boosts distribution). Is the hook polarising enough?
4. MEME SHELF LIFE: If this references a meme or trend, is the meme still in its viral window? Memes have a 2-4 week window — content referencing a 2-month-old meme scores very low.
Fix suggestions: Assess the cultural timeliness of the content. If a meme or trend is referenced, flag whether it's still within its viral window.""",

    "Marketing": """TREND ALIGNMENT — MARKETING LENS:
What drives viral distribution in marketing content:
1. FORMAT: Trending marketing formats: 'I spent $X on ads and here's what happened', 'marketing strategy breakdown', 'why X brand went viral', 'steal this marketing idea', 'ad that made me stop scrolling'. Score based on format.
2. CASE STUDY SPECIFICITY: Marketing audiences are highly analytical. Specific numbers, named brands, and verifiable results drive saves. Vague 'content is king' advice scores very low for trend alignment.
3. CONTRARIAN ANGLE: Marketing content that challenges mainstream advice ('cold email is dead', 'SEO doesn't matter anymore') massively outperforms consensus advice. Does this video take a provocative position?
4. CURRENT PLATFORM RELEVANCE: Is the marketing advice about platforms/strategies that are currently relevant? Advice about declining platforms or outdated tactics scores low.
Fix suggestions: Focus on specificity and contrarian framing. Reference which marketing format is currently driving the most engagement in this space.""",

    "Vlogs": """TREND ALIGNMENT — VLOG LENS:
What drives viral distribution in vlog content:
1. FORMAT: Trending vlog formats: 'day in my life' with a specific interesting job/location, 'week in my life', cinematic travel vlog, 'realistic day in my life' (authentic, unglamorous), 'productive day with me'. The specific vlog angle matters more than 'vlog' as a format.
2. AUDIO: Vlogs live or die by audio selection. Trending audio with strong emotional resonance used in the right moment of a vlog can go viral independently of the vlog quality. Is the audio choice intentional and trending?
3. PACING: Slow, diary-style vlogging is declining. Fast-paced, music-driven, cinematic vlogging with clear narrative structure is the current dominant style.
4. HOOK STORY: The most viral vlogs have a clear story hook — not just 'here's my day', but 'the day I [specific interesting thing]'. Is there a compelling story hook in the first 3 seconds?
Fix suggestions: Assess audio and pacing as the primary trend signals. A well-shot, well-edited vlog with poor audio selection and slow pacing will underperform consistently.""",

    "Self Improvement": """TREND ALIGNMENT — SELF IMPROVEMENT LENS:
What drives viral distribution in self improvement content:
1. SYSTEM SPECIFICITY: Generic self-improvement advice is the most saturated content on Instagram. Specific, actionable systems ('I wake up at 5am and do these 3 things in this order') massively outperform vague tips. Score based on specificity.
2. FORMAT: Trending self improvement formats: 'habits that changed my life', 'things I stopped doing', 'I tried X for 30 days', 'why you're not making progress' (diagnosis format), 'the 1% rule' type frameworks.
3. IDENTITY SHIFT ANGLE: Content framed around identity ('I became a morning person by doing this') currently outperforms habit/productivity content that's purely tactical.
4. AUDIO: Motivational speech audio overlaid on b-roll is trending. Clean, calm music with talking-head delivery also works. High-energy audio on self-improvement content feels mismatched.
Fix suggestions: Lead with specificity assessment. If the advice is too generic, that's the primary fix regardless of production quality.""",

    "Memes": """TREND ALIGNMENT — MEMES LENS:
What drives viral distribution in meme content:
1. MEME FRESHNESS: This is the ONLY thing that matters for meme trend alignment. Is this meme format/audio still in its viral window (0-2 weeks from peak)? Or is it already oversaturated (>3 weeks)? Or is it a dead meme (>6 weeks)? Score ruthlessly based on this.
2. AUDIO SELECTION: Trending audio is the primary distribution mechanism for memes. Even a mediocre meme with perfectly-timed trending audio will outperform a brilliant meme with non-trending audio. Is the audio at its peak, climbing, or declining?
3. FORMAT ORIGINALITY: The best meme content takes a trending format and applies an unexpected or niche-specific twist. A straight copy of a meme template scores lower than a creative subversion of it.
4. COMMENT BAIT: Memes go viral when they trigger comments. Does this meme have a polarising element, a 'tag someone who' moment, or a 'this is so me' identification trigger?
Fix suggestions: Be brutally honest about meme timeliness. If the audio or format is past its window, no amount of quality improvement will save the distribution. Suggest the freshest trending audio in this niche.""",

    "News & Politics": """TREND ALIGNMENT — NEWS & POLITICS LENS:
What drives viral distribution in news & politics content:
1. TIMELINESS: This is everything. News content has a 24-72 hour window. Is this video about something that happened in the last 24 hours? Or is it a week-old story? Score based on how current the content is.
2. ANGLE DISTINCTIVENESS: 'Reporting' news that everyone already knows gets no distribution. The viral angle is the unique take — 'here's what this ACTUALLY means', 'here's what they're not covering', 'unpopular opinion on X'. Does this video have a distinct POV?
3. EMOTIONAL RESONANCE: News/politics content that triggers strong emotions (outrage, fear, hope, vindication) gets significantly more shares than neutral reporting. Is the emotional angle intentional and strong?
4. COMMENT BAIT: News & politics content thrives on debate in comments. A clear position that people will argue about drives comment volume which boosts distribution.
Fix suggestions: Assess timeliness first. If the story is older than 72 hours, the fix is the angle — 'what does this mean now?' or 'here's the follow-up nobody is covering' pivots.""",

    "Other": """TREND ALIGNMENT — GENERAL LENS:
What drives viral distribution regardless of niche:
1. FORMAT TRENDINESS: Is this video using a format that's currently going viral across niches? (POV format, 'nobody talks about this', before/after, day in my life, reaction, etc.)
2. AUDIO: Trending audio is the single biggest cross-niche distribution signal. Is the audio choice a trending sound or a niche-relevant popular track?
3. HOOK PATTERN: The curiosity gap hook ('you've been doing X wrong'), the controversy hook ('unpopular opinion'), and the stakes hook ('I lost everything') work across all niches. Is the hook following a proven high-performing pattern?
4. WATCH TIME STRUCTURE: Algorithm rewards completion rate. Does the video have clear reasons to keep watching throughout, not just a strong hook?
Fix suggestions: Focus on audio and format — these are the two levers that work regardless of niche.""",
}


# ── Apify helpers ─────────────────────────────────────────────────────────────

def _is_instagram_url(url: str) -> bool:
    return "instagram.com" in url.lower()


def _apify_scrape_instagram(url: str) -> Optional[Dict[str, Any]]:
    """
    Get Instagram post data + video URL via Apify.
    Actor 1: apify/instagram-scraper  — metadata + video URL (fast, may hit restrictions)
    Actor 2: apify/instagram-reel-scraper — dedicated reel downloader, bypasses restrictions
    """
    import requests as _requests

    token = settings.APIFY_API_TOKEN
    if not token:
        logger.warning("[Prysm] No APIFY_API_TOKEN set — skipping Apify")
        return None

    shortcode_match = re.search(r"/(?:p|reel|reels)/([A-Za-z0-9_-]+)", url)
    if not shortcode_match:
        logger.warning(f"[Prysm] Could not extract shortcode from URL: {url}")
        return None

    shortcode = shortcode_match.group(1)
    logger.info(f"[Prysm] Apify scraping Instagram shortcode: {shortcode}")

    # ── Actor 1: instagram-scraper (metadata + video URL) ─────────────────────
    post = None
    apify_run_id: Optional[str] = None
    try:
        resp = _requests.post(
            "https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items",
            params={"token": token},
            json={
                "directUrls": [url],
                "resultsLimit": 1,
                "resultsType": "posts",
                "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
            },
            timeout=90,
        )
        resp.raise_for_status()
        # Fetch the run ID from the last run so we can get exact cost later
        try:
            run_resp = _requests.get(
                "https://api.apify.com/v2/acts/apify~instagram-scraper/runs/last",
                params={"token": token, "status": "SUCCEEDED"},
                timeout=10,
            )
            if run_resp.status_code == 200:
                apify_run_id = run_resp.json().get("data", {}).get("id")
        except Exception:
            pass
        items = resp.json()
        if items:
            candidate = items[0]
            if not candidate.get("error"):
                post = candidate
                has_video = bool(candidate.get("videoUrl") or candidate.get("video_url"))
                logger.info(f"[Prysm] Apify scraped: {post.get('ownerUsername', '?')} | {post.get('likesCount', 0)} likes | videoUrl={'yes' if has_video else 'MISSING'} | keys={list(candidate.keys())[:10]}")
            else:
                logger.warning(f"[Prysm] Apify returned error: {candidate.get('error')} — trying reel scraper")
    except Exception as e:
        logger.warning(f"[Prysm] Apify instagram-scraper failed: {e}")

    # If we have metadata but no video URL, try the reel scraper for the download URL
    if post and not post.get("videoUrl"):
        logger.info("[Prysm] No videoUrl from instagram-scraper — trying reel scraper for download URL")
        post["_videoUrl"] = _apify_reel_video_url(url, token, _requests)
    elif not post:
        # Actor 1 failed entirely — try reel scraper for both metadata and video URL
        logger.info("[Prysm] Falling back to reel scraper entirely")
        reel_url = _apify_reel_video_url(url, token, _requests)
        if reel_url:
            post = {"_videoUrl": reel_url, "ownerUsername": "", "likesCount": 0}

    if post and apify_run_id:
        post["_apify_run_id"] = apify_run_id
    return post


def _apify_reel_video_url(url: str, token: str, _requests) -> Optional[str]:
    """Use apify/instagram-scraper with resultsType=details to get video download URL."""
    # Try three different actor input formats to maximize success rate
    attempts = [
        # Attempt 1: instagram-scraper with details type
        {
            "actor": "apify~instagram-scraper",
            "body": {"directUrls": [url], "resultsType": "details", "resultsLimit": 1},
        },
        # Attempt 2: instagram-scraper with posts type (different scraping path)
        {
            "actor": "apify~instagram-scraper",
            "body": {"directUrls": [url], "resultsType": "posts", "resultsLimit": 1, "proxy": {"useApifyProxy": True}},
        },
    ]

    for attempt in attempts:
        try:
            actor_url = f"https://api.apify.com/v2/acts/{attempt['actor']}/run-sync-get-dataset-items"
            resp = _requests.post(actor_url, params={"token": token}, json=attempt["body"], timeout=90)
            if resp.status_code != 200:
                logger.warning(f"[Prysm] Actor {attempt['actor']} returned {resp.status_code}")
                continue
            items = resp.json()
            if not items:
                continue
            item = items[0]
            # Check all possible video URL field names Apify uses
            video_url = (
                item.get("videoUrl") or
                item.get("video_url") or
                item.get("videoVersions", [{}])[0].get("url") if item.get("videoVersions") else None or
                item.get("displayUrl")  # image fallback
            )
            if video_url and video_url.startswith("http"):
                logger.info(f"[Prysm] Got video URL via {attempt['actor']}: {video_url[:80]}...")
                return video_url
            logger.warning(f"[Prysm] {attempt['actor']} returned item but no video URL. Keys: {list(item.keys())[:15]}")
        except Exception as e:
            logger.warning(f"[Prysm] {attempt['actor']} attempt failed: {e}")

    return None


def _fetch_apify_run_cost(run_id: str) -> float:
    """Fetch exact USD cost for an Apify run from the Apify runs API."""
    import requests as _requests
    token = settings.APIFY_API_TOKEN
    if not token or not run_id:
        return 0.0
    try:
        resp = _requests.get(
            f"https://api.apify.com/v2/actor-runs/{run_id}",
            params={"token": token},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            cost_usd = data.get("usageTotalUsd", 0.0) or 0.0
            logger.info(f"[Cost] Apify run {run_id}: ${cost_usd:.6f}")
            return float(cost_usd)
    except Exception as e:
        logger.warning(f"[Cost] Could not fetch Apify run cost for {run_id}: {e}")
    return 0.0


# ── Sanitization helper ───────────────────────────────────────────────────────

def _sanitize_text(value: str, max_length: int = 500) -> str:
    """Strip HTML tags and limit length to prevent XSS via scraped metadata."""
    import html as _html
    # Remove HTML tags
    cleaned = re.sub(r"<[^>]+>", "", value)
    # Decode HTML entities then re-escape (prevents double-encoding)
    cleaned = _html.escape(_html.unescape(cleaned))
    return cleaned[:max_length]


def _sanitize_username(value: str) -> str:
    """Restrict usernames to alphanumeric, underscores, periods (Instagram format)."""
    return re.sub(r"[^a-zA-Z0-9_.]+", "", value)[:60]


# ── Step 0: Extract Instagram metadata (caption, hashtags, likes) ─────────────

def extract_post_metadata(url: str) -> Dict[str, Any]:
    """Extract post metadata — Apify first for Instagram (faster), yt-dlp for everything else."""
    metadata = {}

    # Instagram: go straight to Apify (yt-dlp frequently fails/times out on Instagram)
    if _is_instagram_url(url):
        post = _apify_scrape_instagram(url)
        if post:
            caption = _sanitize_text(post.get("caption", "") or "", 2000)
            hashtags = re.findall(r"#(\w+)", caption)
            metadata = {
                "caption": caption,
                "title": _sanitize_text(caption[:100]) if caption else "",
                "uploader": _sanitize_username(post.get("ownerUsername", "") or ""),
                "hashtags": hashtags,
                "likeCount": post.get("likesCount", 0) or 0,
                "commentCount": post.get("commentsCount", 0) or 0,
                "viewCount": post.get("videoPlayCount", 0) or post.get("videoViewCount", 0) or 0,
                "duration": post.get("videoDuration", 0) or 0,
                "uploadDate": post.get("timestamp", "") or "",
                "musicInfo": post.get("musicInfo", None),
                "locationName": post.get("locationName", "") or "",
            }
            # Store video URL for download — reel scraper puts it in "_videoUrl"
            metadata["_videoUrl"] = post.get("videoUrl") or post.get("_videoUrl") or ""
            metadata["_displayUrl"] = post.get("displayUrl", "") or ""
            if post.get("_apify_run_id"):
                metadata["_apify_run_id"] = post["_apify_run_id"]
            logger.info(f"[Prysm] Apify metadata: @{metadata['uploader']} | {len(hashtags)} hashtags | {metadata['likeCount']} likes | {metadata['viewCount']} views")
            return metadata
        # If Apify fails, fall through to yt-dlp as backup
        logger.warning("[Prysm] Apify metadata failed for Instagram — trying yt-dlp")

    # Non-Instagram (YouTube, TikTok) or Instagram Apify fallback: use yt-dlp
    try:
        cmd = ["yt-dlp", "--dump-json", "--no-download", "--no-playlist"]
        cookie_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cookies.txt")
        if os.path.exists(cookie_path):
            cmd.extend(["--cookies", cookie_path])
        cmd.append(url)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            caption = _sanitize_text(data.get("description", "") or "", 2000)
            hashtags = re.findall(r"#(\w+)", caption)
            metadata = {
                "caption": caption,
                "title": _sanitize_text(data.get("title", "") or ""),
                "uploader": _sanitize_username(data.get("uploader", "") or data.get("channel", "") or ""),
                "hashtags": hashtags,
                "likeCount": data.get("like_count", 0) or 0,
                "commentCount": data.get("comment_count", 0) or 0,
                "viewCount": data.get("view_count", 0) or 0,
                "duration": data.get("duration", 0) or 0,
                "uploadDate": data.get("upload_date", "") or "",
            }
            logger.info(f"[Prysm] yt-dlp metadata: @{metadata['uploader']} | {len(hashtags)} hashtags")
            return metadata
    except Exception as e:
        logger.warning(f"[Prysm] yt-dlp metadata failed: {e}")

    if not metadata:
        logger.warning(f"[Prysm] Could not extract metadata from: {url}")
    return metadata


# ── Step 1: Download video ───────────────────────────────────────────────────

def download_video(url: str, output_dir: str, metadata: Optional[Dict] = None) -> str:
    """Download video — Apify first for Instagram (faster), yt-dlp for everything else."""
    import requests as _requests

    video_path = os.path.join(output_dir, "video.mp4")

    # Instagram: try Apify video URL first (already scraped during metadata extraction)
    if _is_instagram_url(url):
        # Use pre-scraped Apify video URL if available
        if metadata and metadata.get("_videoUrl"):
            try:
                logger.info("[Prysm] Downloading via Apify video URL...")
                resp = _requests.get(metadata["_videoUrl"], timeout=90, stream=True)
                resp.raise_for_status()
                with open(video_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
                if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
                    logger.info(f"[Prysm] Apify URL downloaded: {os.path.getsize(video_path) / 1024 / 1024:.1f} MB")
                    return video_path
            except Exception as e:
                logger.warning(f"[Prysm] Apify URL download failed: {e}")

        # Fresh Apify scrape if no pre-scraped URL
        if not metadata or not metadata.get("_videoUrl"):
            post = _apify_scrape_instagram(url)
            if post:
                fresh_url = post.get("videoUrl") or post.get("_videoUrl")
                if fresh_url:
                    try:
                        resp = _requests.get(fresh_url, timeout=90, stream=True)
                        resp.raise_for_status()
                        with open(video_path, "wb") as f:
                            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                                f.write(chunk)
                        if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
                            logger.info(f"[Prysm] Apify downloaded: {os.path.getsize(video_path) / 1024 / 1024:.1f} MB")
                            return video_path
                    except Exception as e:
                        logger.warning(f"[Prysm] Apify download failed: {e}")

        # Instagram yt-dlp fallback (only if Apify failed)
        logger.info("[Prysm] Apify download failed — trying yt-dlp for Instagram...")

    # yt-dlp (primary for non-Instagram, fallback for Instagram)
    try:
        cmd = ["yt-dlp", "-f", "best[ext=mp4]/best", "--no-playlist", "-o", video_path]
        cookie_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cookies.txt")
        if os.path.exists(cookie_path):
            cmd.extend(["--cookies", cookie_path])
        cmd.append(url)
        subprocess.run(cmd, timeout=90, capture_output=True, check=True)
        if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
            logger.info(f"[Prysm] yt-dlp downloaded: {os.path.getsize(video_path) / 1024 / 1024:.1f} MB")
            return video_path
    except Exception as e:
        logger.warning(f"[Prysm] yt-dlp failed: {e}")

    raise RuntimeError("Could not download this video — Instagram may be restricting access. Please save the reel to your device and use 'Upload Raw Video' instead.")


# ── Step 2: Extract frames ───────────────────────────────────────────────────

def _detect_scene_changes(video_path: str, duration: float) -> List[float]:
    """Use FFmpeg scene detection to find cut/transition timestamps."""
    try:
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", "select='gt(scene,0.25)',showinfo",
            "-vsync", "vfr", "-f", "null", "-"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        # Parse timestamps from showinfo output
        scene_times = []
        for line in result.stderr.split("\n"):
            if "pts_time:" in line:
                try:
                    pts = float(line.split("pts_time:")[1].split()[0])
                    if 0.5 < pts < duration - 0.5:  # Skip very start/end
                        scene_times.append(round(pts, 2))
                except (ValueError, IndexError):
                    pass
        logger.info(f"[Prysm] Detected {len(scene_times)} scene changes")
        return scene_times[:10]  # Cap at 10 to avoid too many
    except Exception as e:
        logger.warning(f"[Prysm] Scene detection failed: {e}")
        return []


def extract_frames(video_path: str, output_dir: str) -> List[Dict]:
    """
    Smart frame extraction — strategically placed frames where they matter most.

    Strategy:
      - Hook zone (0-3s): 5 frames — dense sampling where skip rate is decided
      - Scene changes: 1 frame after each detected cut — captures transitions
      - Mid-section: 1 frame every 3-4s for general pacing
      - Closing (last 2s): 2 frames — CTA and final moment

    Total: 12-18 frames, strategically placed > 30 dumb frames at fixed intervals
    """
    frames_dir = os.path.join(output_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    # Get duration
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path],
            capture_output=True, text=True, timeout=15,
        )
        duration = float(json.loads(probe.stdout).get("format", {}).get("duration", 30))
    except Exception:
        duration = 30.0

    timestamps = set()

    # 1. Hook zone — 5 frames in first 3 seconds (where 60-70% of viewers decide to stay or leave)
    hook_times = [0.0, 0.5, 1.0, 2.0, 3.0]
    for t in hook_times:
        if t < duration:
            timestamps.add(round(t, 2))

    # 2. Scene changes — frames at actual cut points
    scene_changes = _detect_scene_changes(video_path, duration)
    for sc in scene_changes:
        # Add frame just after the scene change (the new scene)
        timestamps.add(round(sc + 0.1, 2))

    # 3. Mid-section — evenly spaced every 3-4 seconds
    mid_start = 3.5
    mid_step = max(3.0, (duration - 5.0) / 6) if duration > 10 else 2.0
    t = mid_start
    while t < duration - 2.5:
        timestamps.add(round(t, 2))
        t += mid_step

    # 4. Closing — last 2 seconds (CTA moment + final frame)
    if duration > 4:
        timestamps.add(round(duration - 2.0, 2))
        timestamps.add(round(duration - 0.5, 2))

    # Sort and cap at 18 frames max
    all_timestamps = sorted(t for t in timestamps if 0 <= t < duration)[:18]

    # Extract each frame
    frames = []
    for i, ts in enumerate(all_timestamps):
        frame_path = os.path.join(frames_dir, f"frame_{i:02d}.jpg")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(ts), "-i", video_path,
                 "-frames:v", "1", "-vf", "scale=768:-1", "-q:v", "3", frame_path],
                capture_output=True, timeout=15,
            )
            if os.path.exists(frame_path) and os.path.getsize(frame_path) > 0:
                frames.append({"path": frame_path, "timestamp": ts})
        except Exception:
            pass

    logger.info(f"[Prysm] Smart extraction: {len(frames)} frames ({len(scene_changes)} scene changes detected) across {duration:.1f}s video")
    return frames


# ── Step 3: Extract audio ────────────────────────────────────────────────────

def extract_audio(video_path: str, output_dir: str) -> Optional[str]:
    """Extract audio as MP3 — 44.1kHz stereo 128k for better Whisper + Gemini results."""
    audio_path = os.path.join(output_dir, "audio.mp3")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame",
             "-ar", "44100", "-ac", "2", "-b:a", "128k", audio_path],
            capture_output=True, timeout=30,
        )
        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
            logger.info(f"[Prysm] Audio extracted: {os.path.getsize(audio_path) / 1024:.0f} KB")
            return audio_path
    except Exception as e:
        logger.warning(f"[Prysm] Audio extraction failed: {e}")
    return None


# ── Step 4: Transcribe with Groq Whisper ─────────────────────────────────────

def transcribe_audio(audio_path: str) -> Dict[str, Any]:
    """Transcribe audio using Groq Whisper — supports Hindi, English, all languages."""
    try:
        groq = _get_groq()

        # Log audio file details before sending
        audio_size = os.path.getsize(audio_path) if os.path.exists(audio_path) else 0
        logger.info(f"[Prysm Whisper] Sending audio: {audio_path}, size={audio_size/1024:.0f}KB")

        with open(audio_path, "rb") as f:
            result = groq.audio.transcriptions.create(
                file=f,
                model="whisper-large-v3-turbo",
                response_format="verbose_json",
            )

        transcript = getattr(result, "text", "") or ""
        language = getattr(result, "language", "unknown") or "unknown"
        logger.info(f"[Prysm Whisper] Raw response: text_len={len(transcript)}, lang={language}, type={type(result).__name__}")
        segments = []

        if hasattr(result, "segments") and result.segments:
            segments = [
                {"start": s.get("start", 0) if isinstance(s, dict) else getattr(s, "start", 0),
                 "end": s.get("end", 0) if isinstance(s, dict) else getattr(s, "end", 0),
                 "text": s.get("text", "") if isinstance(s, dict) else getattr(s, "text", "")}
                for s in result.segments
            ]

        # Detect verbal CTA (supports Hindi + English patterns)
        cta_patterns = [
            r"follow", r"subscribe", r"like", r"save", r"share", r"comment",
            r"follow karo", r"subscribe karo", r"dabao", r"press", r"tap",
            r"link in bio", r"bio mein", r"swipe", r"tag",
        ]
        verbal_cta = "None detected"
        for seg in segments:
            for pattern in cta_patterns:
                if re.search(pattern, seg.get("text", ""), re.IGNORECASE):
                    verbal_cta = f'"{seg["text"].strip()}" at {seg["start"]:.1f}s'
                    break
            if verbal_cta != "None detected":
                break

        hook_text = " ".join(s["text"] for s in segments if s.get("end", 99) <= 3).strip()

        logger.info(f"[Prysm] Whisper: {len(transcript)} chars, lang={language}, CTA={verbal_cta}")

        return {
            "transcript": transcript,
            "language": language,
            "hasSpeech": len(transcript) > 5,
            "segments": segments,
            "verbalCTA": verbal_cta,
            "hookText": hook_text or "No speech in first 3 seconds",
        }
    except Exception as e:
        import traceback
        logger.error(f"[Prysm] Whisper failed: {e}\n{traceback.format_exc()}")
        return {
            "transcript": "", "language": "unknown", "hasSpeech": False,
            "segments": [], "verbalCTA": "None detected", "hookText": "",
        }


# ── Step 4b: Gemini Audio Analysis ───────────────────────────────────────────

def analyse_audio_with_gemini(audio_path: str, frames: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """Use Gemini to LISTEN to the audio AND see frames at synced timestamps — hears music, voice quality,
    background sounds, AND aligns audio events with what's on screen at each moment.

    When `frames` is provided (list of {"path", "timestamp"} dicts), Gemini receives the frames inline
    alongside the audio and is asked to produce frame-aware sync observations on top of the standard
    audio fields. This lets the audio analysis catch desync, mood mismatches, and beat-on-cut alignment
    that pure-audio analysis cannot."""
    if not settings.GEMINI_API_KEY:
        logger.warning("[Prysm Audio] No Gemini key — skipping deep audio analysis")
        return {}

    try:
        from google.genai import types as _gemini_types
        client = _get_gemini()

        audio_size = os.path.getsize(audio_path) if os.path.exists(audio_path) else 0
        logger.info(f"[Prysm Audio] Uploading to Gemini: {audio_path}, size={audio_size/1024:.0f}KB")
        audio_file = client.files.upload(file=audio_path)

        # Build multimodal contents — audio first, then frames tagged with their timestamps
        # so Gemini can align what it hears with what was on screen at that moment.
        usable_frames: List[Dict] = []
        if frames:
            # Cap at 12 frames to keep payload manageable — prefer hook + evenly spaced body
            usable_frames = frames[:12]

        frame_intro = (
            f"\n\nYou also have {len(usable_frames)} frames from the SAME video, each labeled with its timestamp. "
            "Use them to align what you HEAR with what is on SCREEN at that exact moment. "
            "This is critical: most short-form videos succeed or fail based on audio-visual synchrony "
            "(beat drops on cuts, line delivery on facial expressions, music mood matching visual energy)."
            if usable_frames else ""
        )

        prompt_text = f"""Listen to this audio from a short-form video carefully. Describe EVERYTHING you hear.
The audio may be in Hindi, English, Hinglish, or any language. Transcribe key phrases in the original language AND translate to English.{frame_intro}

Return ONLY valid JSON:
{{
  "hasSpeech": true/false,
  "hasMusic": true/false,
  "speechLanguage": "language name or none",
  "speechSummary": "what the person says, summarized in English. Include key phrases in original language if not English",
  "voiceQuality": "describe the voice: clear/muffled/distorted/echo, confident/hesitant, energetic/flat",
  "songTitle": "Your best guess at the song title — e.g. 'Tooh', 'Levitating', 'Savage Love'. Write 'unknown' if you genuinely cannot identify it. Note: audio fingerprinting runs separately and will override this if it gets a confirmed match.",
  "songArtist": "Your best guess at the artist/band name. 'unknown' if not identified.",
  "musicGenre": "genre of background music or 'none'",
  "musicMood": "upbeat/calm/intense/melancholic/hype/dramatic/playful/none",
  "musicEnergy": "low/medium/high/none",
  "soundEffects": "describe any sound effects heard",
  "overallAudioQuality": "professional/decent/amateur/poor — and why",
  "hookAudio": "what viewers hear in the first 2-3 seconds",
  "verbalCTA": "any call-to-action spoken by creator (translated to English if not English). 'None' if none",
  "audioAdvice": "one key suggestion to improve the audio"{''',
  "audioVisualSync": "describe how well the audio aligns with the visuals you see in the frames. Where does it reinforce the visual? Where does it fight the visual?",
  "syncMoments": [{"timestamp": 0.5, "audio": "what is heard near this frame", "visual": "what is on screen", "synced": true, "note": "1 sentence — does this moment work?"}],
  "moodMatch": "does the music/voice mood match the visual energy? Be specific — call out mismatches",
  "hookAVAlignment": "in the first 0-3s: does the audio hook (music + first words) reinforce the visual hook, or do they pull in different directions?"''' if usable_frames else ""}
}}"""

        # Assemble parts: audio file + (optional) frames with per-frame timestamp labels + the prompt
        contents: List[Any] = [audio_file]
        for frame in usable_frames:
            try:
                if not os.path.exists(frame.get("path", "")):
                    continue
                with open(frame["path"], "rb") as fh:
                    frame_bytes = fh.read()
                contents.append(f"=== FRAME AT {frame['timestamp']}s ===")
                contents.append(_gemini_types.Part.from_bytes(
                    data=frame_bytes,
                    mime_type="image/jpeg",
                ))
            except Exception as fe:
                logger.warning(f"[Prysm Audio] Skipped frame {frame.get('timestamp')}: {fe}")
        contents.append(prompt_text)

        logger.info(f"[Prysm Audio] Calling Gemini with audio + {len(usable_frames)} frames")
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=contents,
        )

        raw = response.text
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        logger.info(f"[Prysm Audio] Gemini: speech={result.get('hasSpeech')}, music={result.get('hasMusic')}, mood={result.get('musicMood')}, syncFields={'audioVisualSync' in result}")

        # Capture token usage for cost tracking
        usage_meta = getattr(response, 'usage_metadata', None)
        if usage_meta:
            result["_usage"] = {
                "input_tokens": getattr(usage_meta, 'prompt_token_count', 0) or 0,
                "output_tokens": getattr(usage_meta, 'candidates_token_count', 0) or 0,
                "model": "gemini-2.5-pro",
            }

        return result

    except Exception as e:
        import traceback
        logger.error(f"[Prysm Audio] Gemini failed: {e}\n{traceback.format_exc()}")
        return {}


# ── Step 5: FFmpeg audio measurements ────────────────────────────────────────

def measure_audio(audio_path: str) -> Dict[str, Any]:
    """Measure volume, silence, quality with FFmpeg."""
    avg_vol, peak_vol, silence_pct = -30.0, -10.0, 0

    try:
        vol_out = subprocess.run(
            ["ffmpeg", "-i", audio_path, "-af", "volumedetect", "-f", "null", "/dev/null"],
            capture_output=True, text=True, timeout=15,
        ).stderr
        m = re.search(r"mean_volume:\s*([-\d.]+)", vol_out)
        p = re.search(r"max_volume:\s*([-\d.]+)", vol_out)
        if m: avg_vol = float(m.group(1))
        if p: peak_vol = float(p.group(1))
    except Exception:
        pass

    vol_profile = "loud" if avg_vol > -15 else "good" if avg_vol > -22 else "quiet" if avg_vol > -30 else "very quiet"
    quality = "clipping" if peak_vol > -1 else "good" if -20 < avg_vol < -10 else "low volume" if avg_vol < -30 else "decent"

    return {
        "avgVolume": round(avg_vol, 1),
        "peakVolume": round(peak_vol, 1),
        "silencePercent": silence_pct,
        "volumeProfile": vol_profile,
        "audioQuality": quality,
    }


# ── Step 6: Claude Vision ────────────────────────────────────────────────────

async def analyse_frames_with_claude(frames: List[Dict], niche: str, platform: str, brief: str = "") -> Dict:
    """Send frames to Claude Vision — understands narrative arc, not just individual frames."""
    brief_note = f"\n\nCREATOR'S DESCRIPTION: \"{brief}\"" if brief else ""

    content = [{"type": "text", "text": f"""Watch these {len(frames)} frames from a {niche} video on {platform} IN ORDER — they tell a story. Your job is to understand the NARRATIVE ARC of this video, not just judge individual frames.{brief_note}

IMPORTANT: A dark opening frame is NOT automatically a bad hook. A face not being in frame 1 is NOT automatically bad. Understand the STORY the creator is telling before judging. Many viral formats use context-first, face-second structures intentionally."""}]

    for frame in frames:
        if not os.path.exists(frame["path"]):
            continue
        with open(frame["path"], "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        content.append({"type": "text", "text": f"=== FRAME AT {frame['timestamp']}s ==="})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})

    content.append({"type": "text", "text": """Describe what you see happening across the ENTIRE video — the story, the transitions, the pacing.

Return ONLY valid JSON:
{
  "frames": [{"timestamp": 1.2, "facePresent": true, "emotion": "confident", "sceneType": "talking_head", "onScreenText": "", "visualHookStrength": 7, "issues": [], "strengths": []}],
  "narrativeStructure": "describe the story arc: setup → development → payoff",
  "hookType": "what kind of hook is used: curiosity, shock, face-first, context-first, question, statement, etc",
  "bestThumbnailFrame": 2.4,
  "hookMoment": 0.8,
  "overallVisualScore": 72,
  "visualSummary": "2-3 sentence summary of what the VIDEO is about as a story, not just what individual frames show"
}"""})

    client = _get_anthropic()
    response = await _claude_with_retry(
        client, model="claude-sonnet-4-6", max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )

    # Capture token usage for cost tracking
    _vision_usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens, "model": _resp_model(response, "claude-sonnet-4-6")}

    raw = response.content[0].text
    if settings.META_MODE:
        from app.studio.json_tolerant import extract_json_object
        raw = extract_json_object(raw)  # code fences / text around the JSON (Muse Spark)
    raw = re.sub(r"```json\n?", "", raw)
    raw = re.sub(r"```\n?", "", raw)
    try:
        result = json.loads(raw.strip())
    except json.JSONDecodeError:
        # If JSON is truncated, try to salvage by closing it
        logger.warning(f"[Prysm] Vision JSON truncated ({len(raw)} chars), attempting repair")
        # Try adding closing braces
        repaired = raw.strip().rstrip(',')
        for close in [']}', ']}']:
            try:
                result = json.loads(repaired + close)
                break
            except json.JSONDecodeError:
                continue
        else:
            logger.error(f"[Prysm] Vision JSON unrecoverable: {raw[:200]}...")
            return None
    logger.info(f"[Prysm] Vision: score={result.get('overallVisualScore')}")
    result["_usage"] = _vision_usage
    return result


# ── Step 7: Full analysis ────────────────────────────────────────────────────


def build_scoring_prompts(
    *,
    niche: str,
    platform: str,
    source_type: str,
    intent: str,
    brief: str,
    content_goal: str,
    scored_weights: Dict[str, float],
    niche_ctx: str,
    hook_retention_ctx: str,
    niche_trend_lens: str,
    vision_data: Optional[Dict],
    whisper_data: Dict,
    gemini_audio: Dict,
    audio_measurements: Dict,
    energy_data: Dict,
    aesthetics_data: Dict,
    engagement_data: Dict,
    trend_data: Dict,
    song_trend_match: Dict,
    saved_frames: List,
    post_metadata: Optional[Dict],
    audio_path: Optional[str],
    has_vision: bool,
    has_audio: bool,
    extra_signals: str = "",
) -> tuple:
    """Build the two Claude scoring prompts (call A + call B).

    Returns (call_a_prompt, call_b_prompt) as strings ready to pass to Claude.
    extra_signals="" (default) produces prompts identical to pre-extraction
    production — the pilot uses this to verify correctness at combo_id=1.
    """
    # Build frame descriptions
    frame_desc = ""
    if vision_data:
        frame_desc = f"\nVISION:\nScore: {vision_data.get('overallVisualScore', '?')}/100 | {vision_data.get('visualSummary', '')}"
        if vision_data.get("narrativeStructure"):
            frame_desc += f"\nNarrative arc: {vision_data['narrativeStructure']}"
        if vision_data.get("hookType"):
            frame_desc += f"\nHook type used: {vision_data['hookType']}"
        frame_desc += "\n"
        for f in vision_data.get("frames", []):
            frame_desc += f"• {f.get('timestamp', '?')}s — {f.get('sceneType', '?')}, emotion: {f.get('emotion', '?')}, hook: {f.get('visualHookStrength', '?')}/10"
            if f.get("onScreenText"): frame_desc += f", text: \"{f['onScreenText']}\""
            frame_desc += "\n"

    # Build audio context — structured as a story, not fragments
    audio_ctx = ""
    if has_audio:
        audio_ctx = f"\nAUDIO:\n- Speech: {whisper_data.get('hasSpeech')} | Lang: {whisper_data.get('language')}"
        audio_ctx += f"\n- Volume: {audio_measurements.get('avgVolume')} ({audio_measurements.get('volumeProfile')}) | Quality: {audio_measurements.get('audioQuality')}"
        audio_ctx += f"\n- CTA: {whisper_data.get('verbalCTA')}"
        audio_ctx += f"\n- Hook audio (first 3s): {whisper_data.get('hookText')}"

        # Build timestamped script so Claude understands the story flow
        segments = whisper_data.get("segments", [])
        if segments:
            audio_ctx += f"\n\nFULL TIMESTAMPED SCRIPT ({whisper_data.get('language')}) — read this like a story, understand the narrative flow:"
            for seg in segments:
                audio_ctx += f"\n  [{seg.get('start', 0):.1f}s → {seg.get('end', 0):.1f}s] {seg.get('text', '').strip()}"
            audio_ctx += "\n\nREAD THE SCRIPT ABOVE CAREFULLY. Understand what the creator is saying, the buildup, the punchline, the story arc. Your suggestions MUST respect the narrative structure."
        elif whisper_data.get("transcript"):
            safe_transcript = whisper_data["transcript"].replace('"', "'").replace("\\", "")[:1500]
            audio_ctx += f"\n\nTranscript ({whisper_data.get('language')}):\n{safe_transcript}"

        # Gemini deep audio analysis (music, voice quality, mood — the rich stuff)
        if gemini_audio:
            audio_ctx += "\nDEEP AUDIO ANALYSIS (AI listened to the actual audio):"
            if gemini_audio.get("hasMusic"):
                _song_id = gemini_audio.get("songTitle", "unknown") or "unknown"
                _song_artist = gemini_audio.get("songArtist", "") or ""
                _song_confirmed = gemini_audio.get("songConfirmed", False)
                _confidence_note = f" (ACRCloud fingerprint, confidence={gemini_audio.get('songConfidence', 0)})" if _song_confirmed else " (AI estimate — may be inaccurate)"
                _song_label = (
                    f"'{_song_id}' by {_song_artist}{_confidence_note}"
                    if _song_id.lower() not in ("unknown", "none") and _song_artist and _song_artist.lower() not in ("unknown", "none")
                    else f"'{_song_id}'{_confidence_note}" if _song_id.lower() not in ("unknown", "none")
                    else "unknown"
                )
                audio_ctx += f"\n- Music: YES — song: {_song_label}, genre: {gemini_audio.get('musicGenre', 'unknown')}, mood: {gemini_audio.get('musicMood', 'unknown')}, energy: {gemini_audio.get('musicEnergy', 'unknown')}"
            else:
                audio_ctx += "\n- Music: No background music detected"
            if gemini_audio.get("hasSpeech"):
                audio_ctx += f"\n- Voice quality: {gemini_audio.get('voiceQuality', 'unknown')}"
                audio_ctx += f"\n- Speech summary: {gemini_audio.get('speechSummary', '')}"
            if gemini_audio.get("soundEffects") and gemini_audio["soundEffects"] != "none":
                audio_ctx += f"\n- Sound effects: {gemini_audio.get('soundEffects')}"
            audio_ctx += f"\n- What viewers hear first 2-3s: {gemini_audio.get('hookAudio', 'unknown')}"
            audio_ctx += f"\n- Overall audio quality: {gemini_audio.get('overallAudioQuality', 'unknown')}"
            if gemini_audio.get("audioAdvice"):
                audio_ctx += f"\n- Audio improvement suggestion: {gemini_audio.get('audioAdvice')}"
            if gemini_audio.get("verbalCTA") and gemini_audio["verbalCTA"] not in ("None", "", None):
                audio_ctx += f"\n- Verbal CTA detected by AI listener: {gemini_audio.get('verbalCTA')}"

            # Frame-aware sync observations (only present when Gemini received frames)
            if gemini_audio.get("audioVisualSync"):
                audio_ctx += f"\n\nAUDIO-VISUAL SYNC (Gemini saw frames + heard audio together):"
                audio_ctx += f"\n- Sync overall: {gemini_audio.get('audioVisualSync')}"
            if gemini_audio.get("hookAVAlignment"):
                audio_ctx += f"\n- Hook A/V alignment (0-3s): {gemini_audio.get('hookAVAlignment')}"
            if gemini_audio.get("moodMatch"):
                audio_ctx += f"\n- Mood match: {gemini_audio.get('moodMatch')}"
            sync_moments = gemini_audio.get("syncMoments") or []
            if isinstance(sync_moments, list) and sync_moments:
                audio_ctx += f"\n- Per-moment sync (audio+visual at the same timestamp):"
                for sm in sync_moments[:8]:
                    if not isinstance(sm, dict):
                        continue
                    ts = sm.get("timestamp", "?")
                    synced = "✓" if sm.get("synced") else "✗"
                    audio_ctx += f"\n    [{ts}s {synced}] audio: {str(sm.get('audio',''))[:80]} | visual: {str(sm.get('visual',''))[:80]} — {str(sm.get('note',''))[:120]}"


    # Frame references
    frame_ref = ""
    ff = ""
    if saved_frames:
        hook_frames = [f for f in saved_frames if f['timestamp'] <= 3.0]
        mid_frames = [f for f in saved_frames if 3.0 < f['timestamp'] < (saved_frames[-1]['timestamp'] - 2.0 if saved_frames else 999)]
        end_frames = [f for f in saved_frames if f['timestamp'] >= (saved_frames[-1]['timestamp'] - 2.0 if saved_frames else 999)]
        frame_ref = f"""
Frames strategically extracted:
- HOOK ZONE (0-3s): {', '.join(str(f['timestamp']) + 's' for f in hook_frames)} — these are the critical first seconds where viewers decide to stay or scroll
- MID-SECTION + SCENE CHANGES: {', '.join(str(f['timestamp']) + 's' for f in mid_frames)} — includes frames at detected scene transitions
- CLOSING: {', '.join(str(f['timestamp']) + 's' for f in end_frames)} — CTA moment and final frame
Pay special attention to the hook zone frames — this is where skip rate is determined. Include referenceFrame and frameAnnotation per parameter."""
        ff = ', "referenceFrame": 1.2, "frameAnnotation": "label"'

    # Brief context — this is CRITICAL, it tells us what the creator intended
    brief_ctx = ""
    if brief:
        brief_ctx = f"""
CREATOR'S OWN DESCRIPTION OF THIS VIDEO: "{brief}"
{f'Goal: {content_goal}' if content_goal else ''}

THIS IS THE MOST IMPORTANT CONTEXT. The creator has told you what their video is about and what they intended. You MUST:
1. UNDERSTAND the creative intent before judging anything
2. NEVER suggest changes that would break the narrative or punchline
3. If the video builds up to a reveal/punchline, do NOT suggest moving the punchline to the start
4. If the opening deliberately sets context (dark scene, environment shot, etc.), do NOT call it a "weak hook" — evaluate if it creates curiosity
5. Your suggestions should make THIS video's story land BETTER, not turn it into a different video
6. If the creator's structure is intentional, suggest ways to make that SAME structure more effective"""

    # Post metadata context (Instagram caption, hashtags, engagement)
    post_ctx = ""
    if post_metadata and post_metadata.get("caption"):
        post_ctx = f"\n\nORIGINAL POST DATA (scraped from {platform}):"
        post_ctx += f"\n- Caption: \"{post_metadata['caption'][:500]}\""
        if post_metadata.get("hashtags"):
            post_ctx += f"\n- Hashtags used: {', '.join('#' + h for h in post_metadata['hashtags'][:20])}"
        if post_metadata.get("viewCount"):
            post_ctx += f"\n- Views: {post_metadata['viewCount']:,}"
        if post_metadata.get("likeCount"):
            post_ctx += f"\n- Likes: {post_metadata['likeCount']:,}"
        if post_metadata.get("commentCount"):
            post_ctx += f"\n- Comments: {post_metadata['commentCount']:,}"
        if post_metadata.get("uploader"):
            post_ctx += f"\n- Creator handle: @{post_metadata['uploader']}"
        post_ctx += """

USE THIS DATA TO:
1. Evaluate if the caption is effective — does it hook readers? Is it too long/short?
2. Evaluate if the hashtags are relevant and trending — suggest better hashtags from the trend data if available
3. Compare engagement (views/likes/comments ratio) against niche benchmarks
4. In your analysis, include a specific recommendation for a better caption and better hashtags
5. If the caption has a CTA (like "link in bio"), evaluate its effectiveness"""

    # Build trend context from data already fetched in parallel
    trend_ctx = ""
    try:
        # Always inject the niche-specific trend lens first — this tells Claude
        # WHAT to look for in this niche beyond hashtags, even with no live data
        trend_ctx = f"\n\n{niche_trend_lens}"

        # Append real scraped data on top if available
        live_trend_ctx = build_trend_context(trend_data, gemini_audio=gemini_audio)
        if live_trend_ctx:
            trend_ctx += f"\n{live_trend_ctx}"

        # Append song-trend match if found — this is the highest-confidence signal
        if song_trend_match.get("found"):
            m = song_trend_match
            staleness = m.get("staleness", "recently")
            trend_ctx += f"\n\nSONG TREND MATCH — HIGH CONFIDENCE SIGNAL:"
            trend_ctx += f"\n- The song in this video ('{m['audio_title']}'"
            if m.get("artist_name"):
                trend_ctx += f" by {m['artist_name']}"
            trend_ctx += f") is CONFIRMED TRENDING in our database ({staleness})."
            trend_ctx += f"\n- It appeared in {m['usage_count']} top-performing reels across niches: {', '.join(m['niches'])}"
            trend_ctx += f"\n- Posts using this audio averaged {int(m['avg_likes'])} likes"
            if m.get("co_hashtags"):
                trend_ctx += f"\n- Hashtags frequently paired with this audio: {', '.join('#' + t for t in m['co_hashtags'][:8])}"
            trend_ctx += "\n- INSTRUCTION: Because this song is confirmed trending, the Trend Alignment score MUST be at least 75. Acknowledge the song is currently viral and explain why this is an advantage. Do NOT say 'no trending audio data was available'."

        _primary = niche.split(",")[0].strip() if "," in niche else niche
        logger.info(f"[Prysm] Trend context: niche_lens={_primary}, {len(trend_data.get('trendingHashtags', []))} hashtags, {len(trend_data.get('trendingAudios', []))} audios, song_match={song_trend_match.get('found', False)}")
    except Exception as e:
        logger.error(f"[Prysm] Trend context build failed: {e}")

    # Build new signal contexts
    energy_ctx = build_energy_context(energy_data)
    aesthetics_ctx = build_aesthetics_context(aesthetics_data)
    engagement_ctx = build_engagement_context(engagement_data)

    # Build redundancy context — timestamps where voiceover + on-screen captions BOTH exist.
    # At these timestamps, suggesting another text overlay is redundant (Mayer's Redundancy Principle).
    redundancy_ctx = ""
    try:
        has_speech = whisper_data.get("hasSpeech", False)
        frames_with_captions = []
        if vision_data and has_speech:
            segments = whisper_data.get("segments", [])
            for f in vision_data.get("frames", []):
                ts = f.get("timestamp", 0)
                on_screen = f.get("onScreenText", "")
                # Frame has on-screen text (captions/subtitles detected by vision)
                if on_screen and len(on_screen.strip()) > 3:
                    # Check if voiceover is also active at this timestamp
                    voiceover_active = any(
                        seg.get("start", 0) <= ts <= seg.get("end", 0)
                        for seg in segments
                    ) if segments else has_speech  # fallback: if no segments, assume speech throughout
                    if voiceover_active:
                        frames_with_captions.append(f"{ts}s (text: \"{on_screen.strip()[:60]}\")")

        if frames_with_captions:
            redundancy_ctx = f"""

REDUNDANCY MAP — CRITICAL DATA:
The following timestamps already have BOTH on-screen captions/text AND active voiceover narration detected:
{chr(10).join(f'  • {f}' for f in frames_with_captions)}

At these timestamps: voiceover serves audio viewers, captions serve muted viewers. Both channels are already covered. Suggesting an additional text overlay at any of these timestamps violates Mayer's Redundancy Principle — it creates cognitive overload (audio + captions + extra text = three simultaneous channels) and REDUCES retention. DO NOT suggest any new text overlay at these timestamps. Instead, for On-Screen Text, acknowledge that captions are already well-placed here and redirect suggestions to timestamps where captions are ABSENT."""
        elif has_speech and vision_data:
            # Speech exists but no captioned frames detected — note this for On-Screen Text parameter
            redundancy_ctx = "\n\nREDUNDANCY MAP: No on-screen captions detected. Voiceover exists but muted viewers have no text to read — On-Screen Text suggestions are valid and needed here."
    except Exception as _e:
        logger.warning(f"[Prysm] Redundancy context build failed: {_e}")

    # Final Claude analysis
    # Pilot signal injection — appended to redundancy_ctx; empty = zero diff vs production
    if extra_signals:
        redundancy_ctx = (redundancy_ctx or "") + "\n" + extra_signals

    # Determine tone based on source_type and intent
    is_upload = source_type == "upload"
    is_competitor = intent == "competitor"
    is_own_link = not is_upload and not is_competitor

    if is_upload:
        tone_instructions = """
TONE — EDITOR MODE (RAW VIDEO UPLOAD):
This creator is editing this video RIGHT NOW. They have NOT posted it yet. You are their editor sitting next to them.
- Use present tense: "Add text at 15s" NOT "Next time, add text"
- Use "before you post" language: "Fix this before you post" NOT "In your next video"
- Be direct and actionable: each suggestion should feel like a checklist item they can do NOW
- Frame everything as "do this now" not "you should have done this"
- Step-by-step guides should reference their editing app workflow
- The goal is to make THIS video as strong as possible before it goes live"""
    elif is_competitor:
        tone_instructions = """
TONE — VIRAL VIDEO BLUEPRINT MODE:
This video is ALREADY SUCCESSFUL with massive engagement. The user wants to understand the exact techniques that made it go viral so they can create similar content.

YOUR ENTIRE RESPONSE MUST BE POSITIVE AND INSPIRATIONAL. You are teaching, not critiquing.

BANNED PHRASES (NEVER USE ANY OF THESE):
- "room for improvement"
- "could be better"
- "could be stronger"
- "the only area where"
- "weakness"
- "lacks"
- "missing"
- "fails to"
- "should have"
- "next time"
- Any negative framing of any kind

FIELD MAPPING (CRITICAL — follow EXACTLY):

"problem" field = THE TECHNIQUE THEY USED. Describe what the creator DID, not what's wrong.
  GOOD: "They open with two friends eating at a street stall — the casual real-life setting creates instant relatability and curiosity about what happens next"
  BAD: "The only area where there is room for improvement is..."

"fix" field = HOW THE USER CAN DO THIS IN THEIR OWN CONTENT. Specific, actionable, niche-adapted.
  GOOD: "Film your next video at a real location with friends — the candid energy is what makes viewers feel like they're part of the group"
  BAD: "How to apply this — when you create your own version..."

"whatIsHappening" = DETAILED description of the specific technique, with timestamps and quotes. What did the creator do? How did they execute it?

"whyItMatters" = Connect this technique to WHY the video got massive views. Explain the algorithm/psychology behind it.

"whatToChange" = YOUR BLUEPRINT — exact steps for the user to apply this technique in their own niche. Be specific to their content style.

"howToDoIt" = Step-by-step guide the user can follow TODAY to create their version. Each step should be concrete and actionable. End with what results to expect.

"whatToExpect" = The expected outcome in plain terms — describe WHAT will improve (e.g. more viewers stay past 3 seconds, stronger first impression, more saves) NOT invented percentages. Never fabricate specific numbers like "your hold rate will go from 49% to 65%". Say things like "more non-followers will stop scrolling" or "viewers are more likely to watch through to the end".

TOP-LEVEL FIELDS:
- "summary" = "This video works because [2-3 specific techniques]. With [X likes/views], it proves that [key insight]. Apply these same techniques to your content to achieve similar reach."
- "biggestProblem" = REFRAME: "The #1 technique that drove this viral: [specific technique with detail]"
- "quickFix" = "The easiest thing you can apply from this video to your very next post: [specific actionable step]"
- "topPriorities" = 3 techniques to implement, ordered by impact on views

SCORING:
- Score based on how WELL the video executes each parameter (high = brilliant technique to learn)
- ALL parameters should be framed as "what they did" — even lower-scoring ones
- For scores 70+: "This is a masterclass in [parameter] — here's exactly how they did it"
- For scores 50-69: "They took a unique approach to [parameter] — here's what you can learn from it"
- For scores below 50: "Despite [parameter] being unconventional, the video still went viral because [other techniques] carried it — this tells you where to focus your energy"

CRITICAL — STEPS ARE FOR THE USER, NOT THE ORIGINAL VIDEO:
- The "howToDoIt" steps must be written for the USER'S NEXT video, NOT for editing the original creator's video.
- WRONG: "In your editing app, add a text layer at 0.0s that says 'Flashmob at Thapar University'"
- RIGHT: "In YOUR next performance video, add a context text overlay in the first 2 seconds — e.g., '[Your event] at [Your location]'"
- The user CANNOT edit this video. They are studying it. Every step must start with "In your next video..." or "For your version..." or "When you create similar content..."
- Keep steps to 3 maximum per parameter. These are key takeaways, not a full editing tutorial.
- Each step should be a transferable principle, not a specific instruction tied to THIS video.

Remember: The user is here to be INSPIRED and get a BLUEPRINT. Every word should make them think "I can do this too." """
    else:
        tone_instructions = """
TONE — COACH MODE (OWN POSTED VIDEO):
This creator already posted this video. They want to learn what to do differently next time.
- Use future tense: "In your next video, try X" NOT "Fix this now"
- Frame as lessons learned: "This worked well, keep doing it" or "Next time, consider X"
- Every suggestion should be actionable for the NEXT video they make
- Be encouraging about what worked — they can't change the posted video"""

    # Build caption parameter JSON conditionally (outside f-string to avoid backslash issues)
    # Caption & hashtags are no longer a scored parameter — they are generated as suggestions only
    caption_param_json = ""

    # ── Call A prompt: parameters 1-5 + all top-level fields ──
    _call_a_prompt = f"""You are Prysm AI — a friendly, expert content strategist who helps creators go viral on {platform}. You've studied thousands of viral reels from creators worldwide and you understand STORYTELLING — not just visual rules.
{tone_instructions}

STEP 1 — UNDERSTAND THE VIDEO FIRST:
Before scoring ANYTHING, read the creator's own description (below), watch the frames in order, and read the full transcript. Understand:
- What is the STORY being told? What did the creator intend?
- What is the narrative structure (setup → development → payoff)?
- What is the punchline or key moment?
- Is the opening intentionally setting context for a later payoff?
- Why did the creator structure it THIS way?
{brief_ctx}

STEP 2 — THEN EVALUATE:
Now score each parameter based on how well this SPECIFIC story is told, not based on generic rules.

STRICT RULES:
1. NEVER mention technical terms: no FFmpeg, dB, bitrate, codec, clipping. Use simple language.
2. NEVER recommend specific apps by name. Say "in your editing app" instead.
3. Describe audio in human terms: "your voice sounds distorted when you get excited" not technical jargon.
4. Make suggestions feel easy and doable — like a friend giving advice.
5. Talk like a supportive coach who has seen what actually works on {platform}.
6. Reference what you ACTUALLY observed in the video — quote specific dialogues, describe specific frames.
7. NEVER suggest rearranging the video in a way that breaks the story. If the video builds to a punchline, do NOT suggest putting the punchline first.
8. If the creator already does something well, ACKNOWLEDGE it — don't suggest they change it.
9. If the creator's video is in a specific language (Hindi, Spanish, French, etc.), quote their actual words in your analysis. Tailor suggestions to the creator's specific cultural and regional context — do not assume any single country or audience.
10. Your "whatIsHappening" MUST describe what ACTUALLY happens in the video — quote real dialogue, describe real scenes. Do NOT make up scenes that don't exist.
11. Your "whatToChange" must suggest improvements to THIS video's structure, not a completely different video.
12. VIRAL-READY THRESHOLD IS 85, NOT 70: A score of 70 is good but NOT viral-ready. Only give a parameter a score of 85+ if it is genuinely exceptional and needs zero improvement. For scores 70–84 ("good but not viral-ready"), frame suggestions as refinements not problems — use language like "this is working well — to push it to viral-ready, [specific tweak]" instead of "the problem is X". For scores below 70, use normal problem/fix framing. NEVER score a parameter 85+ unless it is truly outstanding — most parameters in an average video should score 55–78.
13. HOOK TEXT OVERLAYS ARE VALID HOOKS: A title card, sarcastic text, ironic quote, or contradictory phrasing on screen in the first 3 seconds IS a strong hook format — especially in comedy. Ironic labels or setup text that creates a curiosity gap must be scored as hook strengths, not weaknesses. Do NOT suggest adding text that achieves the same effect as text already on screen.
    TEXT OVERLAY CONFLICT RULE: The problem is NEVER "there are multiple text elements on screen." Multiple overlays are standard and expected across most niches — the issue is only when two overlays serve the SAME function and compete for primary attention. There are four overlay types that legitimately coexist: (1) Hook/Attention overlay — primary "why watch this" signal, large, center; (2) Context overlay — step number, location tag, price, date, progress label like "Week 1", small, corner; (3) Captions/subtitles — speech transcription at the bottom, processed passively alongside audio; (4) Reaction/Commentary overlay — ironic label, POV frame, "Meanwhile...", stat callout. These serve DIFFERENT cognitive roles and do NOT cause overload when combined. The ONLY problem case: two Hook/Attention-level overlays simultaneously demanding primary attention in the same frame. NICHE BASELINES — these multi-overlay patterns are completely NORMAL and must NOT be flagged as clutter: Comedy/Lifestyle: POV label + ironic commentary text together IS the format. Fitness: transformation labels ("Week 1"/"Week 8") + step cues + progress markers together is baseline. Travel: location + price + day marker all visible simultaneously is editorial standard. Beauty: product name + step number + shade callout together during application is expected. Finance/Education: hook claim + stat callout + source attribution together drives saves. Recipe: ingredient + step + time overlays simultaneously is the format. When you see multiple overlays on screen, identify each one's TYPE first — only flag when two overlays of the SAME type conflict with each other.
14. CONTRADICTION CHECK — before writing any fix or howToDoIt step: explicitly ask yourself "does the video already do what I am about to suggest?" If yes, do NOT suggest it. Instead, explain what the creator already did correctly and why it works. A suggestion that mirrors existing content is a scoring error — it means you missed something in the video.
15. TREND ALIGNMENT — RESTRICTED TO 3 DOMAINS ONLY: FORMAT, AUDIO, CONTENT ANGLE. Nothing else. Hashtags do not drive reach on Instagram Reels (confirmed by Adam Mosseri). Text overlays are NOT a trend signal — they belong to Hook & Opening or On-Screen Text. Captions are NOT a trend signal — they belong to a separate section. The ONLY valid Trend Alignment fixes are: (a) FORMAT: "Your format is [X] — the top-performing content right now uses [Y format] because [reason]." (b) AUDIO: "Your audio [matches/doesn't match] what's trending — switch to [specific song + artist] because [reason tied to this video's mood]." (c) CONTENT ANGLE: "This topic/angle is [trending/not trending] — adjusting to [specific angle] would give you algorithmic momentum because [reason]." If the creator's format, audio, and angle are all well-aligned, say so as a strength — a high score with a specific compliment is correct. NEVER suggest adding a text overlay in the Trend Alignment parameter. NEVER suggest hashtag changes in Trend Alignment.

CRITICAL — PARAMETER OWNERSHIP (EACH PARAMETER HAS EXCLUSIVE DOMAIN):
16. EVERY parameter has exclusive ownership of its fix type. NEVER let two parameters suggest the same TYPE of change. Ownership is:
    - Hook & Opening → owns: hook formula, first 1-3s text overlay, opening energy
    - Retention & Pacing → owns: mid-video re-hook, cut timing, energy drops at specific timestamps
    - Trend Alignment → owns: FORMAT (video structure type), AUDIO (trending song recommendation), CONTENT ANGLE (topic/timing). NEVER text overlays. NEVER caption changes. NEVER hashtags.
    - Audio & Music → owns: sound quality, voice clarity, background music choice, audio-visual sync
    - On-Screen Text → owns: caption/subtitle overlays throughout the video (except the hook text in the first 3s which belongs to Hook & Opening)
    - Call to Action → owns: the final CTA (verbal or text), placed after the payoff
    - Save Potential → owns: save-worthy value signals (lists, tips, reference moments)
    - Shareability → owns: surprise moments, emotional peaks, DM-share triggers
    - Replay Value → owns: loop design, twist endings, dense-info pacing
    - Visual Quality & Originality → owns: lighting, framing, resolution, watermarks
17. If a fix naturally belongs to another parameter's domain, DO NOT suggest it in your current parameter. Instead, score this parameter on what it CAN observe and leave the other fix for the correct parameter.
18. TEXT OVERLAY RULE: Only Hook & Opening and On-Screen Text may suggest specific text overlays with specific wording. All other parameters must use verbal CTAs, structural changes, or audio changes instead. NEVER suggest the same timestamp for text in two different parameters.
19. Each parameter's "howToDoIt" steps must be UNIQUE and non-overlapping. The creator should be able to follow ALL parameters' steps without any conflicts. Think of it as writing ONE coherent editing checklist across all parameters, not 10 separate checklists.
20. MID-VIDEO TEXT OVERLAY RULES — MANDATORY:
    TIMING: Pattern interrupt text overlays every 3–5 seconds (not every cut) deliver 40–60% higher retention vs. no mid-video text. Key-moments-only outperforms constant saturation — place overlays exactly when the viewer needs them (confusing step, punchline, key stat, reveal), then remove before visual noise sets in. Each overlay must stay on screen minimum 2 seconds.
    SIMULTANEOUS LIMIT: Maximum 2 text elements on screen simultaneously at any point mid-video. Each element must occupy a DISTINCT screen zone (e.g. lower-third captions + corner step number = fine; two center-frame overlays = conflict). Stacking text (header + body + footer) in the same zone = cognitive overload.
    OVERLOAD TRIGGERS — flag these: (a) 3+ text elements stacking in the same zone at once, (b) overlapping text forcing split-attention reading, (c) rapid text changes under 0.5 seconds, (d) multiple CTAs stacked at the end-screen. End-screen: one dominant CTA wins — multiple CTA text elements train viewers to ignore all of them.
    NICHE EXCEPTION: Fitness is the one niche where 3 simultaneous elements (timer + rep counter + motivational text) can work IF each occupies a fixed, predictable screen zone the viewer has already learned across multiple frames.
    MAYER'S REDUNDANCY — adding a NEW Hook/Attention-level overlay at a timestamp that already has captions + voiceover causes cognitive overload. NEVER suggest a Hook/Attention overlay at timestamps listed in the REDUNDANCY MAP. EXEMPT from this restriction: Context overlays (step numbers, location tags, progress labels like "Week 1", price callouts) and Reaction/Commentary overlays (ironic labels, POV frames, "Meanwhile..." cards) — these are processed peripherally and do not conflict with captions. Do NOT penalise a creator who has captions + a niche-appropriate context or reaction overlay simultaneously. For the On-Screen Text parameter: if captions already exist throughout the video, acknowledge this as a strength and redirect suggestions to timestamps where captions are absent or to caption STYLE improvements (font size, placement, contrast) — not new hook-level overlays on top of existing ones.

CONTEXT: Niche: {niche} | Platform: {platform} | {niche_ctx}
{hook_retention_ctx}
{frame_desc}
{frame_ref}
{audio_ctx}
{energy_ctx}
{aesthetics_ctx}
{engagement_ctx}
{post_ctx}
{trend_ctx}
{redundancy_ctx}

{'The creator speaks in ' + whisper_data.get('language', 'unknown') + '. READ THE FULL TRANSCRIPT ABOVE. Quote their actual dialogue in your analysis. Understand the story they are telling.' if whisper_data.get('hasSpeech') else ''}
{'A verbal CTA was detected: ' + (gemini_audio.get('verbalCTA', '') or whisper_data.get('verbalCTA', '')) + '. Give credit in CTA score.' if (gemini_audio.get('verbalCTA', 'None') not in ('None', '', None) or whisper_data.get('verbalCTA', 'None detected') != 'None detected') else ''}

{"IMPORTANT — THIS IS A RAW VIDEO UPLOAD (NOT YET POSTED):" + chr(10) + "The creator is editing this video RIGHT NOW. Your suggestions should be things they can do BEFORE posting." + chr(10) + "- Use present tense: 'Add text at 15s' NOT 'Next time, add text'" + chr(10) + "- Frame everything as a pre-post checklist" + chr(10) + "- DO NOT score 'Caption & Hashtag Strategy' — this video hasn't been posted yet so there's no caption. Instead, just provide suggestedCaption and suggestedHashtags as recommendations in the JSON response." + chr(10) + "- Only return 9 parameters (skip Caption & Hashtag Strategy entirely from the parameters array)." if is_upload else "IMPORTANT — VIRAL VIDEO BREAKDOWN:" + chr(10) + "This video has massive engagement. The user wants to understand WHY and replicate this success." + chr(10) + "- For EVERY parameter, focus on WHAT THE CREATOR DID that worked — not what could be better" + chr(10) + "- The 'problem' field should describe the TECHNIQUE used, not a problem" + chr(10) + "- The 'fix' field should tell the USER how to apply this technique in their own content" + chr(10) + "- End each howToDoIt with a motivating line about the results they can expect" + chr(10) + "- NEVER use 'steal', 'copy', 'fix', 'improve' — use 'learn from', 'apply', 'create your version'" if is_competitor else "IMPORTANT — SUGGESTIONS THAT RESPECT THE STORY:" + chr(10) + "- This is the user's OWN posted video. Frame suggestions as 'next time, do X' — not 'fix this now'." + chr(10) + "- Your suggestions must make the user's NEXT video better." + chr(10) + "- Every suggestion should feel actionable for the next video they make."}
- If trend data is provided above, use formats and audios as the primary Trend Alignment signals. Hashtags are context only — never the main recommendation.
{("- Help the user understand WHY this video works so they can build similar content." if intent == "competitor" else "- Every suggestion should make the creator think 'yes, that would make MY video better' — not 'that's a completely different video.'")}

HASHTAG RULES — MANDATORY, NO EXCEPTIONS:
- suggestedHashtags must contain EXACTLY 5 hashtags. No more, no less. Instagram only allows 5 hashtags in the dedicated hashtag field — give the creator their 5 best ones, not more.
- ALL hashtags MUST be in English only. NEVER suggest hashtags in Thai, Arabic, Hindi, Korean, Japanese, or any other non-Latin script. If you see non-English characters, that hashtag is BANNED.
- NO generic hashtags. The following are BANNED: #viral, #reels, #trending, #foryou, #fyp, #explore, #explorepage, #instagood, #instagram, #video, #love, #follow, #like, #share, #instadaily, #igreels, #reelsviral, #reelsindia, #reelsinstagram, #lifestyle, #content, #creator, #motivation, #inspiration, #style, #fashion, #beauty
- Hashtags MUST be specific to the EXACT content of this video. Ask yourself: "Could this hashtag describe 1 million other videos?" If yes, it's too generic — pick something more specific.
- MIX these 4 types across your 5 hashtags, prioritising the most impactful: (1) niche-specific action/topic (e.g. #hairTransformation #budgetTravel #glowUp), (2) community/audience (e.g. #TravelCreators #BeautyEnthusiast), (3) audio/song name if identifiable (e.g. #prettyWoman #espresso), (4) sub-niche or location tag relevant to the video's actual setting (e.g. #BaliDiaries #NYCCreator #LondonVlog)
- If the video uses a specific song: include the song name and/or artist as a hashtag
- If the video references a creator or trend: include that creator's name or the specific trend name
- Never hallucinate hashtags in foreign scripts — if you are uncertain, use a simpler English equivalent
- NEVER suggest a hashtag that is just the niche name alone (e.g. #lifestyle, #fitness, #comedy alone are banned — be specific)

CAPTION RULES — MANDATORY:
- The suggestedCaption must be 3-5 sentences. It should feel like something the creator would actually post — authentic, warm, and personal.
- DO NOT summarise the video or describe what happens in it. Viewers can see the video.
- Instead: open with a hook line that triggers curiosity, emotion, or a relatable feeling. Use the creator's actual story, location, or moment from the video.
- Include one specific detail from the video (a place, a moment, a feeling, a transformation) to make it feel genuine — not a template.
- End with a CTA that feels natural: a question for comments, a "tag someone" prompt, or a "save this" nudge — NOT "follow for more" or "link in bio."
- Match the energy of the video: if it's funny, be witty. If it's emotional, be heartfelt. If it's a transformation, build anticipation.
- NEVER start with "I never imagined" or clichéd openers. Be direct and specific.

Do NOT include overallScore, scoreLabel, or predictedScoreAfterFixes — the backend will compute these from your parameter scores.
IMPORTANT: The "parameters" array in your response must contain EXACTLY these 5 parameters in order: Hook & Opening, Retention & Pacing, Shareability, Audio & Music, Trend Alignment. Do NOT add On-Screen Text, Visual Quality & Originality, Call to Action, Save Potential, or Replay Value — those are scored in a separate call.

Respond ONLY with valid JSON:
{{
  "storyUnderstanding": "2-3 sentences — write this BEFORE you score anything. What is the story or intent of this video? What narrative structure did the creator use? What is the punchline or key moment? This proves you understood the video before judging it.",
  "summary": "2-3 sentences — {"explain WHY this video achieved massive engagement. Identify the 2-3 key techniques that drove its success. End with a motivating line like 'Apply these to your content and you can achieve similar results.'" if intent == "competitor" else "mention the biggest problem and what fixing it could do for their views"}",
  "biggestProblem": "One sentence — {"The #1 reason this video went viral — the single most powerful technique the user should learn and apply immediately" if intent == "competitor" else "the single biggest thing to improve in the next video"}",
  "quickFix": "One sentence — {"The easiest technique from this video you can apply to your very next video today to start seeing better results" if intent == "competitor" else "the most impactful change they can make in 5 minutes"}",
  "hasRealVideoData": {str(has_vision).lower()},
  "hasAudioData": {str(has_audio).lower()},
  "topPriorities": ["most impactful change — one sentence", "second priority — one sentence", "third priority — one sentence"],
  "suggestedHashtags": ["hashtag1", "hashtag2", "hashtag3", "hashtag4", "hashtag5", "hashtag6", "hashtag7", "hashtag8"],
  "suggestedCaption": "3-5 sentences. Opens with a hook tied to a specific moment/emotion from this video. Middle lines build story or feeling. Ends with a CTA question or tag-someone prompt. Sounds like the creator wrote it — not a template. NO hashtags inside the caption body.",
  "trendingAudioSuggestion": "Give a CONCRETE audio recommendation — a specific song name and artist that would work for this video right now. Use the trending audios list above as your primary source. If that list is empty or none fit, use your own knowledge of what is currently trending on {platform} for this niche and video mood — name the actual track, do not tell the user to 'go search'. Format: 'Switch to [Song Name] by [Artist] — [one sentence on why it fits this video's mood/energy and what algorithmic boost it gives]'. Exception: if the video's core content IS the audio (e.g. a song cover, dialogue-driven comedy, or the creator's own voice is the product), say why the current audio must stay and suggest one complementary background track instead.",
  "timelineFeedback": [],
  "parameters": [
    {{"name":"Hook & Opening","weight":{round(scored_weights.get("Hook & Opening",0.19)*100,1)},"score":45,"advisory":false,"problem":"One sentence — name the specific failure: is there NO hook formula used, a slow build-up (intro/pleasantry before the hook), a weak single-trigger hook, or a hook-content mismatch? If on-screen text or a cold open already creates a strong curiosity gap or pattern interrupt, acknowledge it — do NOT treat absence of a face as a problem.","fix":"One sentence — name the specific formula they should use or the specific trigger they should add. Never suggest adding text that duplicates what is already on screen.","whatIsHappening":"Describe EXACTLY what happens in the first 1-3 seconds — quote on-screen text word for word, describe face/expression/energy, name the hook formula used (contrarian / mistake / numbered list / time-based / question / cold open / pattern interrupt / none). Count how many psychological triggers fire: (1) curiosity gap, (2) social proof/authority, (3) FOMO/urgency, (4) controversy/contrarian, (5) immediate value. State the trigger count explicitly: e.g. '2 triggers fire: curiosity gap + immediate value'.","whyItMatters":"viewers decide within 1.7 seconds whether to keep watching. Reels with 60%+ 3-second hold rates outperform weak holds by 5-10x in total reach. A compound hook (3+ triggers) is near-impossible to scroll past. A slow build-up (intro before the hook) loses 40-60% of viewers before the content even starts.","whatToChange":"Name the exact upgrade: (a) which formula to switch to for this niche, (b) which additional trigger to add to make it compound, or (c) how to eliminate the slow build-up. If the hook already fires 3 triggers, say so and redirect to the next weakest parameter.","howToDoIt":"3 concrete steps. Step 1 must name the specific formula and how to open. Step 2 must name the second psychological trigger to add. Step 3 must describe the first frame visually. Never suggest a step the video already does.","whatToExpect":"Describe the specific improvement: e.g. 'adding a social proof trigger to your existing curiosity hook makes it compound — more non-followers will stop scrolling because the hook now answers both what and why at the same time'. NO invented percentages. SCORE CALIBRATION — MANDATORY: If engagement prediction data is available above (from the AI Engagement Prediction block), your Hook & Opening score MUST align with the ECR (hook hold rate) signal. ECR < 50% (HIGH hook risk) = Hook score must be ≤ 55. ECR 50-70% (MEDIUM hook risk) = Hook score must be in the 45-70 range. ECR ≥ 70% (LOW hook risk) = Hook score should be ≥ 65. You may deviate by up to 10 points from these ranges ONLY if your qualitative frame-by-frame analysis strongly contradicts the signal — if you deviate, you MUST justify in whatIsHappening with a specific reason tied to what you saw (e.g. 'RunPod ECR=42% but the on-screen text creates an unusually strong curiosity gap for this niche, scoring 60'). If the source is heuristic (not RunPod), treat the risk level as a soft ceiling/floor — your qualitative read takes priority, but do not ignore the signal entirely."{ff}}},
    {{"name":"Retention & Pacing","weight":{round(scored_weights.get("Retention & Pacing",0.17)*100,1)},"score":60,"advisory":false,"problem":"One sentence — name the specific failure: missing mid-video re-hook, energy drop at a specific timestamp, no open loop to pull viewers through, promise-payoff mismatch, or pacing issue (too slow / too fast / uneven cuts).","fix":"One sentence — the single most impactful retention fix with a specific timestamp if possible.","whatIsHappening":"Describe the full retention arc: (1) What is the hook's core PROMISE — what did the first 3 seconds implicitly or explicitly commit to deliver? (2) Is there an open loop (unresolved question or tension) pulling viewers through the middle? (3) Is there a mid-video re-hook at 8-12s? (4) Where is the most likely drop-off point and why? (5) Does the video deliver on the hook's promise — fully, partially, or not at all? Reference the energy curve data if available: flag the exact timestamp of any energy drop > 20% below hook energy.","whyItMatters":"watch time is Instagram's confirmed #1 ranking signal (Adam Mosseri, Jan 2025). Completion rate directly determines non-follower reach. A video that loses viewers at 8s reaches half as many people as one that holds through to the end — even if every other parameter is identical.","whatToChange":"MANDATORY — every fix in this field MUST include a specific timestamp (e.g. 'At 9s, the video loses momentum — add X here'). No timestamp = incomplete answer. Priorities: (1) If the hook's promise was not delivered, name that as the primary fix with the timestamp where the payoff should land. (2) If there's no mid-video re-hook, specify the exact technique to add at 8-12s. (3) If the energy curve shows a drop > 20% at a specific second, flag it with that timestamp and name the fix.","howToDoIt":"3 concrete steps. Step 1: fix the biggest drop-off risk at a specific timestamp with a technique for this niche. Step 2: add or strengthen the mid-video re-hook at 8-12s. Step 3: ensure the hook's promise is fully delivered before the video ends. Every step must reference a timestamp. Never suggest a step that conflicts with another parameter's fix.","whatToExpect":"Describe what concretely improves — e.g. 'adding a re-hook at 9s means viewers who would have dropped are now re-engaged and stay through the payoff, which improves completion rate and pushes this to more non-followers'. NO invented percentages. SCORE CALIBRATION — MANDATORY: If engagement prediction data is available above (from the AI Engagement Prediction block), your Retention & Pacing score MUST align with the NAWP (average watch percentage) signal. NAWP < 35% (HIGH retention risk) = Retention score must be ≤ 50. NAWP 35-55% (MEDIUM retention risk) = Retention score must be in the 45-68 range. NAWP ≥ 55% (LOW retention risk) = Retention score should be ≥ 65. You may deviate by up to 10 points ONLY if your qualitative analysis (open loop presence, mid-rehook quality, energy curve timestamps) strongly contradicts the signal — if you deviate, you MUST justify in whatIsHappening with a specific structural reason (e.g. 'RunPod NAWP=30% but a clear open loop and re-hook at 9s suggest stronger-than-predicted retention, scoring 52'). If the source is heuristic (not RunPod), treat the risk level as a soft ceiling/floor — your qualitative read takes priority, but do not ignore the signal entirely."{ff}}},
    {{"name":"Shareability","weight":{round(scored_weights.get("Shareability",0.16)*100,1)},"score":55,"advisory":false,"problem":"One sentence — why wouldn't someone DM this to a friend?","fix":"One sentence — what would make this shareable","whatIsHappening":"does this have a surprise moment, emotional peak, tag-your-friend element, controversial take, or relatable 'this is SO me' moment?","whyItMatters":"DM sends are weighted 3-5x more than likes for reaching new audiences (confirmed by Mosseri). Sends are THE viral mechanism — 694K reels are sent via DM every minute.","whatToChange":"specific shareability or emotional trigger to add","howToDoIt":"step-by-step","whatToExpect":"describe what concretely improves — e.g. 'more viewers will DM this to a friend', 'this becomes the kind of content people tag others in'. NO invented percentages."{ff}}},
    {{"name":"Audio & Music","weight":{round(scored_weights.get("Audio & Music",0.16)*100,1)},"score":70,"advisory":false,"problem":"One sentence","fix":"One sentence — reference trending audio if available","whatIsHappening":"voice clarity, music choice, background noise, trending audio usage — what do viewers hear?","whyItMatters":"trending audio gets ~80% more engagement. Users clicking to the audio page is an official ranking signal. Audio sets emotional tone and drives retention.","whatToChange":"specific audio improvement or trending audio suggestion","howToDoIt":"step-by-step","whatToExpect":"describe what concretely improves — e.g. 'the audio will feel intentional rather than incidental', 'trending audio increases discoverability'. NO invented percentages."{ff}}},
    {{"name":"Trend Alignment","weight":{round(scored_weights.get("Trend Alignment",0.16)*100,1)},"score":65,"advisory":false,"problem":"One sentence — state whether the video's FORMAT (structure/style type), AUDIO (trending song or sound), or CONTENT ANGLE (topic/timing) is aligned or misaligned with what is currently performing in this niche. NEVER mention text overlays here — that belongs to Hook or On-Screen Text, not Trend Alignment.","fix":"One sentence — name the specific FORMAT, AUDIO, or CONTENT ANGLE change that would give the biggest algorithmic boost. If the audio is already trending, acknowledge it as a strength. NEVER suggest 'add text overlay' or 'change caption' — those are other parameters' domains.","whatIsHappening":"Assess exactly 3 things: (1) FORMAT — what style/structure is this video using (POV, GRWM, talking-head, B-roll montage, tutorial, challenge, etc.) and is that format currently trending for this niche? Name the format explicitly. (2) AUDIO — is the audio/song in this video currently trending? Cross-reference the trending audios list above. State clearly: trending/not-trending/unknown. (3) CONTENT ANGLE — is the topic or moment of this video timely? Is it riding an active cultural wave or is it evergreen or behind-trend? Answer all 3 explicitly.","whyItMatters":"trending FORMAT gets 2-3x more algorithmic distribution because Instagram's explore surface favors familiar content patterns. Trending AUDIO gets ~80% more engagement and clicking the audio drives discovery. Trending CONTENT ANGLE catches active search and hashtag waves. These three signals — not hashtags, not text overlays — are the actual trend distribution levers.","whatToChange":"Name the single highest-impact change across FORMAT, AUDIO, or CONTENT ANGLE. Examples: 'Switch from B-roll montage to a talking-head POV format — that format is currently dominant in this niche' or 'Replace the background music with [Song Name by Artist] — it is trending right now and matches the energy of your video' or 'Reframe the content angle from generic travel to [specific trending topic] which is actively being searched this week'. DO NOT suggest text overlay changes here.","howToDoIt":"3 concrete steps focused only on FORMAT, AUDIO, or CONTENT ANGLE. Step 1: the format or structural change (if needed) with specific description of how the video type should change. Step 2: the audio recommendation — name a specific trending song/sound that fits this video's mood and energy, and say where to find it. Step 3: the content angle or timing recommendation. ZERO text overlay suggestions allowed in any step.","whatToExpect":"describe the specific distribution improvement — e.g. 'switching to this format means Instagram will show this in the explore feed alongside other viral [format] content in the niche', 'using a trending audio triggers the audio discovery page, which surfaces this to new non-followers passively'. NO invented percentages."{ff}}},
  ],
  {'"nextVideoTemplate": {{"hook": "How to open YOUR version of this video — specific technique to replicate", "structure": "The format/narrative structure to follow", "audio": "What audio to use — trending track name or style", "location": "Where and when to film for maximum impact", "text": "What text overlays to add and when", "cta": "The exact CTA to include", "caption": "A ready-to-use caption for YOUR version"}},' if is_competitor else ''}
  "niche":"{niche}",
  "platform":"{platform}"
}}"""

    # ── Call B prompt: parameters 6-10 only, no top-level fields ──
    _call_b_prompt = f"""You are Prysm AI — a friendly, expert content strategist who helps creators go viral on {platform}. You've studied thousands of viral reels from creators worldwide and you understand STORYTELLING — not just visual rules.
{tone_instructions}

STEP 1 — UNDERSTAND THE VIDEO FIRST:
Before scoring ANYTHING, read the creator's own description (below), watch the frames in order, and read the full transcript. Understand:
- What is the STORY being told? What did the creator intend?
- What is the narrative structure (setup → development → payoff)?
- What is the punchline or key moment?
- Is the opening intentionally setting context for a later payoff?
- Why did the creator structure it THIS way?
{brief_ctx}

STEP 2 — THEN EVALUATE:
Now score each parameter based on how well this SPECIFIC story is told, not based on generic rules.

STRICT RULES:
1. NEVER mention technical terms: no FFmpeg, dB, bitrate, codec, clipping. Use simple language.
2. NEVER recommend specific apps by name. Say "in your editing app" instead.
3. Describe audio in human terms: "your voice sounds distorted when you get excited" not technical jargon.
4. Make suggestions feel easy and doable — like a friend giving advice.
5. Talk like a supportive coach who has seen what actually works on {platform}.
6. Reference what you ACTUALLY observed in the video — quote specific dialogues, describe specific frames.
7. NEVER suggest rearranging the video in a way that breaks the story. If the video builds to a punchline, do NOT suggest putting the punchline first.
8. If the creator already does something well, ACKNOWLEDGE it — don't suggest they change it.
9. If the creator's video is in a specific language (Hindi, Spanish, French, etc.), quote their actual words in your analysis. Tailor suggestions to the creator's specific cultural and regional context — do not assume any single country or audience.
10. Your "whatIsHappening" MUST describe what ACTUALLY happens in the video — quote real dialogue, describe real scenes. Do NOT make up scenes that don't exist.
11. Your "whatToChange" must suggest improvements to THIS video's structure, not a completely different video.
12. VIRAL-READY THRESHOLD IS 85, NOT 70: A score of 70 is good but NOT viral-ready. Only give a parameter a score of 85+ if it is genuinely exceptional and needs zero improvement. For scores 70–84 ("good but not viral-ready"), frame suggestions as refinements not problems — use language like "this is working well — to push it to viral-ready, [specific tweak]" instead of "the problem is X". For scores below 70, use normal problem/fix framing. NEVER score a parameter 85+ unless it is truly outstanding — most parameters in an average video should score 55–78.
13. HOOK TEXT OVERLAYS ARE VALID HOOKS: A title card, sarcastic text, ironic quote, or contradictory phrasing on screen in the first 3 seconds IS a strong hook format — especially in comedy. Ironic labels or setup text that creates a curiosity gap must be scored as hook strengths, not weaknesses. Do NOT suggest adding text that achieves the same effect as text already on screen.
    TEXT OVERLAY CONFLICT RULE: The problem is NEVER "there are multiple text elements on screen." Multiple overlays are standard and expected across most niches — the issue is only when two overlays serve the SAME function and compete for primary attention. There are four overlay types that legitimately coexist: (1) Hook/Attention overlay — primary "why watch this" signal, large, center; (2) Context overlay — step number, location tag, price, date, progress label like "Week 1", small, corner; (3) Captions/subtitles — speech transcription at the bottom, processed passively alongside audio; (4) Reaction/Commentary overlay — ironic label, POV frame, "Meanwhile...", stat callout. These serve DIFFERENT cognitive roles and do NOT cause overload when combined. The ONLY problem case: two Hook/Attention-level overlays simultaneously demanding primary attention in the same frame. NICHE BASELINES — these multi-overlay patterns are completely NORMAL and must NOT be flagged as clutter: Comedy/Lifestyle: POV label + ironic commentary text together IS the format. Fitness: transformation labels ("Week 1"/"Week 8") + step cues + progress markers together is baseline. Travel: location + price + day marker all visible simultaneously is editorial standard. Beauty: product name + step number + shade callout together during application is expected. Finance/Education: hook claim + stat callout + source attribution together drives saves. Recipe: ingredient + step + time overlays simultaneously is the format. When you see multiple overlays on screen, identify each one's TYPE first — only flag when two overlays of the SAME type conflict with each other.
14. CONTRADICTION CHECK — before writing any fix or howToDoIt step: explicitly ask yourself "does the video already do what I am about to suggest?" If yes, do NOT suggest it. Instead, explain what the creator already did correctly and why it works. A suggestion that mirrors existing content is a scoring error — it means you missed something in the video.
15. TREND ALIGNMENT — RESTRICTED TO 3 DOMAINS ONLY: FORMAT, AUDIO, CONTENT ANGLE. Nothing else. Hashtags do not drive reach on Instagram Reels (confirmed by Adam Mosseri). Text overlays are NOT a trend signal — they belong to Hook & Opening or On-Screen Text. Captions are NOT a trend signal — they belong to a separate section. The ONLY valid Trend Alignment fixes are: (a) FORMAT: "Your format is [X] — the top-performing content right now uses [Y format] because [reason]." (b) AUDIO: "Your audio [matches/doesn't match] what's trending — switch to [specific song + artist] because [reason tied to this video's mood]." (c) CONTENT ANGLE: "This topic/angle is [trending/not trending] — adjusting to [specific angle] would give you algorithmic momentum because [reason]." If the creator's format, audio, and angle are all well-aligned, say so as a strength — a high score with a specific compliment is correct. NEVER suggest adding a text overlay in the Trend Alignment parameter. NEVER suggest hashtag changes in Trend Alignment.

CRITICAL — PARAMETER OWNERSHIP (EACH PARAMETER HAS EXCLUSIVE DOMAIN):
16. EVERY parameter has exclusive ownership of its fix type. NEVER let two parameters suggest the same TYPE of change. Ownership is:
    - Hook & Opening → owns: hook formula, first 1-3s text overlay, opening energy
    - Retention & Pacing → owns: mid-video re-hook, cut timing, energy drops at specific timestamps
    - Trend Alignment → owns: FORMAT (video structure type), AUDIO (trending song recommendation), CONTENT ANGLE (topic/timing). NEVER text overlays. NEVER caption changes. NEVER hashtags.
    - Audio & Music → owns: sound quality, voice clarity, background music choice, audio-visual sync
    - On-Screen Text → owns: caption/subtitle overlays throughout the video (except the hook text in the first 3s which belongs to Hook & Opening)
    - Call to Action → owns: the final CTA (verbal or text), placed after the payoff
    - Save Potential → owns: save-worthy value signals (lists, tips, reference moments)
    - Shareability → owns: surprise moments, emotional peaks, DM-share triggers
    - Replay Value → owns: loop design, twist endings, dense-info pacing
    - Visual Quality & Originality → owns: lighting, framing, resolution, watermarks
17. If a fix naturally belongs to another parameter's domain, DO NOT suggest it in your current parameter. Instead, score this parameter on what it CAN observe and leave the other fix for the correct parameter.
18. TEXT OVERLAY RULE: Only Hook & Opening and On-Screen Text may suggest specific text overlays with specific wording. All other parameters must use verbal CTAs, structural changes, or audio changes instead. NEVER suggest the same timestamp for text in two different parameters.
19. Each parameter's "howToDoIt" steps must be UNIQUE and non-overlapping. The creator should be able to follow ALL parameters' steps without any conflicts. Think of it as writing ONE coherent editing checklist across all parameters, not 10 separate checklists.
20. MID-VIDEO TEXT OVERLAY RULES — MANDATORY:
    TIMING: Pattern interrupt text overlays every 3–5 seconds (not every cut) deliver 40–60% higher retention vs. no mid-video text. Key-moments-only outperforms constant saturation — place overlays exactly when the viewer needs them (confusing step, punchline, key stat, reveal), then remove before visual noise sets in. Each overlay must stay on screen minimum 2 seconds.
    SIMULTANEOUS LIMIT: Maximum 2 text elements on screen simultaneously at any point mid-video. Each element must occupy a DISTINCT screen zone (e.g. lower-third captions + corner step number = fine; two center-frame overlays = conflict). Stacking text (header + body + footer) in the same zone = cognitive overload.
    OVERLOAD TRIGGERS — flag these: (a) 3+ text elements stacking in the same zone at once, (b) overlapping text forcing split-attention reading, (c) rapid text changes under 0.5 seconds, (d) multiple CTAs stacked at the end-screen. End-screen: one dominant CTA wins — multiple CTA text elements train viewers to ignore all of them.
    NICHE EXCEPTION: Fitness is the one niche where 3 simultaneous elements (timer + rep counter + motivational text) can work IF each occupies a fixed, predictable screen zone the viewer has already learned across multiple frames.
    MAYER'S REDUNDANCY — adding a NEW Hook/Attention-level overlay at a timestamp that already has captions + voiceover causes cognitive overload. NEVER suggest a Hook/Attention overlay at timestamps listed in the REDUNDANCY MAP. EXEMPT from this restriction: Context overlays (step numbers, location tags, progress labels like "Week 1", price callouts) and Reaction/Commentary overlays (ironic labels, POV frames, "Meanwhile..." cards) — these are processed peripherally and do not conflict with captions. Do NOT penalise a creator who has captions + a niche-appropriate context or reaction overlay simultaneously. For the On-Screen Text parameter: if captions already exist throughout the video, acknowledge this as a strength and redirect suggestions to timestamps where captions are absent or to caption STYLE improvements (font size, placement, contrast) — not new hook-level overlays on top of existing ones.

CONTEXT: Niche: {niche} | Platform: {platform} | {niche_ctx}
{hook_retention_ctx}
{frame_desc}
{frame_ref}
{audio_ctx}
{energy_ctx}
{aesthetics_ctx}
{engagement_ctx}
{post_ctx}
{trend_ctx}
{redundancy_ctx}

{'The creator speaks in ' + whisper_data.get('language', 'unknown') + '. READ THE FULL TRANSCRIPT ABOVE. Quote their actual dialogue in your analysis. Understand the story they are telling.' if whisper_data.get('hasSpeech') else ''}
{'A verbal CTA was detected: ' + (gemini_audio.get('verbalCTA', '') or whisper_data.get('verbalCTA', '')) + '. Give credit in CTA score.' if (gemini_audio.get('verbalCTA', 'None') not in ('None', '', None) or whisper_data.get('verbalCTA', 'None detected') != 'None detected') else ''}

{"IMPORTANT — THIS IS A RAW VIDEO UPLOAD (NOT YET POSTED):" + chr(10) + "The creator is editing this video RIGHT NOW. Your suggestions should be things they can do BEFORE posting." + chr(10) + "- Use present tense: 'Add text at 15s' NOT 'Next time, add text'" + chr(10) + "- Frame everything as a pre-post checklist" + chr(10) + "- Only return the 4 parameters listed below." if is_upload else "IMPORTANT — VIRAL VIDEO BREAKDOWN:" + chr(10) + "This video has massive engagement. The user wants to understand WHY and replicate this success." + chr(10) + "- For EVERY parameter, focus on WHAT THE CREATOR DID that worked — not what could be better" + chr(10) + "- The 'problem' field should describe the TECHNIQUE used, not a problem" + chr(10) + "- The 'fix' field should tell the USER how to apply this technique in their own content" + chr(10) + "- End each howToDoIt with a motivating line about the results they can expect" + chr(10) + "- NEVER use 'steal', 'copy', 'fix', 'improve' — use 'learn from', 'apply', 'create your version'" if is_competitor else "IMPORTANT — SUGGESTIONS THAT RESPECT THE STORY:" + chr(10) + "- This is the user's OWN posted video. Frame suggestions as 'next time, do X' — not 'fix this now'." + chr(10) + "- Your suggestions must make the user's NEXT video better." + chr(10) + "- Every suggestion should feel actionable for the next video they make."}
- If trend data is provided above, use formats and audios as the primary Trend Alignment signals. Hashtags are context only — never the main recommendation.
{("- Help the user understand WHY this video works so they can build similar content." if intent == "competitor" else "- Every suggestion should make the creator think 'yes, that would make MY video better' — not 'that's a completely different video.'")}

Respond ONLY with valid JSON containing just the "parameters" array for these 4 parameters:
{{
  "parameters": [
    {{"name":"Visual Quality & Originality","weight":{round(scored_weights.get("Visual Quality & Originality",0.16)*100,1)},"score":75,"advisory":false,"problem":"One sentence","fix":"One sentence","whatIsHappening":"resolution, lighting, framing, watermarks, borders, aspect ratio — is this original content or reposted?","whyItMatters":"Instagram actively penalizes low-res, watermarked, and bordered content. Reposted content with 70%+ visual similarity gets flagged. 10+ reposts in 30 days = excluded from recommendations entirely.","whatToChange":"specific visual or originality improvement","howToDoIt":"step-by-step","whatToExpect":"describe what concretely improves — e.g. 'the video will feel more professional', 'Instagram will stop flagging this as reposted content'. NO invented percentages."{ff}}},
    {{"name":"On-Screen Text","weight":0,"score":50,"advisory":true,"problem":"One sentence — diagnose the actual gap: (a) captions absent entirely for a speech-heavy video, (b) captions present but no pattern interrupt overlays at key mid-video moments where a bold text callout would re-hook viewers, (c) captions present but poor style/placement (too small, low contrast, obscured by UI), or (d) text overload — 3+ elements stacking in the same zone simultaneously. If captions already cover the whole video with good style, the score should be high and the problem minor or absent.","fix":"One sentence — the single most impactful text fix. Options: add captions where absent, add a pattern interrupt overlay at the key reveal/punchline timestamp, improve caption style, or reduce text clutter at a specific timestamp.","whatIsHappening":"First check the REDUNDANCY MAP. Then assess all four dimensions: (1) CAPTION COVERAGE — which parts of the video have captions and which are gaps for muted viewers? (2) PATTERN INTERRUPTS — are there bold text overlays at key mid-video moments (8–12s re-hook, punchline, key stat, reveal)? Pattern interrupts every 3–5s deliver 40–60% higher retention. (3) STYLE/READABILITY — are existing captions readable? Right font size, contrast, not hidden behind UI elements? (4) OVERLOAD CHECK — are there any timestamps with 3+ text elements stacking in the same screen zone? Describe what you found for each dimension.","whyItMatters":"50%+ of Reels are watched mute — captions are not optional for speech-heavy content. Pattern interrupt text overlays at key mid-video moments (stat callouts, punchlines, reveals) increase retention by 40–60% and saves by 40%. But text overload (3+ elements in one zone, or a new Hook/Attention overlay on top of existing captions + voiceover) causes cognitive overload and REDUCES retention. The goal is strategic placement, not maximum coverage.","whatToChange":"Prioritise in this order: (1) If captions are missing for speech, that is the primary fix — specify which timestamps need captions. (2) If captions exist but there are no pattern interrupt overlays at key moments, suggest adding one bold callout at the most impactful timestamp (punchline, key stat, reveal) — NOT a Hook/Attention overlay on top of existing captions+voiceover. (3) If captions exist and pattern interrupts exist, suggest style improvements. (4) If there is text overload at a specific timestamp, flag it and say which element to remove. NEVER suggest a Hook/Attention overlay at a timestamp in the REDUNDANCY MAP.","howToDoIt":"step-by-step. Step 1: address the biggest gap (missing captions OR missing pattern interrupt at a specific timestamp). Step 2: style/readability improvement for existing text (font size, contrast, placement zone). Step 3: if applicable, flag a specific overloaded timestamp and which element to remove. Each step must reference a specific timestamp. Maximum 2 simultaneous text elements in any zone — never stack 3.","whatToExpect":"describe the concrete improvement — e.g. 'mute viewers will now follow the key argument at 12s where no caption existed', 'the bold stat callout at 8s re-hooks viewers who were about to drop — this is the point where retention typically falls for this format', 'removing the third overlay at 15s makes the remaining two elements readable instead of competing'. NO invented percentages."{ff}}},
    {{"name":"Save Potential","weight":0,"score":50,"advisory":true,"problem":"One sentence — why wouldn't someone save this?","fix":"One sentence — what would make this save-worthy","whatIsHappening":"does this video teach something, provide a list, give a recipe, offer a tip, or have reference value worth coming back to?","whyItMatters":"saves are a confirmed 'heavy' interaction signal — 57% of viewers save content they find valuable. A post with 50 saves and 20 shares can outrank one with 200 likes but 0 shares.","whatToChange":"specific way to add save-worthy value","howToDoIt":"step-by-step","whatToExpect":"describe what concretely improves — e.g. 'viewers will save this to reference later', 'the save rate will increase because this has list/tip value'. NO invented percentages."{ff}}},
    {{"name":"Replay Value","weight":0,"score":55,"advisory":true,"problem":"One sentence","fix":"One sentence","whatIsHappening":"does this video have a twist ending, dense information, seamless loop, or a moment worth rewatching?","whyItMatters":"replays count as additional watch time — Instagram's #1 signal. 48% of users replay entertaining content. Loopable content multiplies total view time.","whatToChange":"specific way to add replay value","howToDoIt":"step-by-step","whatToExpect":"describe what concretely improves — e.g. 'viewers will rewatch to catch the detail they missed', 'the loop will feel seamless and trigger replays'. NO invented percentages."{ff}}},
    {{"name":"Call to Action","weight":0,"score":50,"advisory":true,"problem":"One sentence — is there a CTA and is it effective?","fix":"One sentence — what CTA to add or improve","whatIsHappening":"is there a verbal or on-screen CTA? Does the creator tell viewers what to do — follow, save, share, comment? Is it specific or generic? Is it placed after the payoff?","whyItMatters":"CTAs directly drive the engagement actions the algorithm measures. 'Comment your city' drives more comments than 'like and follow'. 'Send this to someone who...' triggers the #1 viral signal — DM shares.","whatToChange":"specific CTA to add — verbal, on-screen, or both — with exact wording","howToDoIt":"step-by-step — where to place it, what to say, how to display it","whatToExpect":"describe what concretely improves — e.g. 'more viewers will comment', 'the DM share rate will increase because people have a reason to send it'. NO invented percentages."{ff}}}
  ]
}}"""

    return (_call_a_prompt, _call_b_prompt)

async def run_full_analysis(
    video_path: str,
    niche: str,
    platform: str,
    brief: str = "",
    content_goal: str = "",
    post_metadata: Optional[Dict[str, Any]] = None,
    intent: str = "my_video",
    source_type: str = "upload",
    credit_checker: Optional[Callable[[float], Awaitable[bool]]] = None,
    progress_cb: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """Run the complete Prysm analysis pipeline.

    progress_cb: optional (stage, status) callback so the website can show the real stage of the job (hackathon shell;
        None changes nothing).

    credit_checker: optional async callable(stage1_cost_usd: float) -> bool
        Called after Stage 1 (Vision + Gemini + RunPod) completes.
        Should deduct stage1_cost_usd from the user's credits and return
        True if credits are now exhausted (can't afford Stage 2).
        If it returns True, raises CreditExhaustedError to stop the pipeline.
    """

    work_dir = tempfile.mkdtemp(prefix="prysm_")
    from app.studio.progress import tracked, StageGroup  # progress reporting only (hackathon shell)
    _pcb = progress_cb
    # For multi-niche, use first niche for context lookup
    primary_niche = niche.split(",")[0].strip() if "," in niche else niche
    niche_ctx = NICHE_CONTEXT.get(primary_niche, f"{niche} creator — apply platform best practices.")
    niche_trend_lens = NICHE_TREND_LENS.get(primary_niche, NICHE_TREND_LENS.get("Other", ""))
    hook_retention_ctx = _build_hook_retention_context(primary_niche)

    # Resolve niche-specific scoring weights up front so prompt + scoring use the same values
    niche_key = primary_niche.lower()
    scored_weights = NICHE_WEIGHTS.get(niche_key, DEFAULT_SCORED_WEIGHTS)

    try:
        # Step A: Extract frames + audio (run in parallel via threads)
        loop = asyncio.get_event_loop()
        frames_future = loop.run_in_executor(_executor, extract_frames, video_path, work_dir)
        audio_future = loop.run_in_executor(_executor, extract_audio, video_path, work_dir)
        if _pcb:
            _pcb("frames", "running")
        frames, audio_path = await asyncio.gather(frames_future, audio_future)
        if _pcb:
            _pcb("frames", "ok")

        logger.info(f"[Prysm] Extraction done: {len(frames)} frames, audio={'yes' if audio_path else 'no'}")

        # Save frames as base64
        saved_frames = []
        for f in frames:
            if os.path.exists(f["path"]):
                with open(f["path"], "rb") as fh:
                    saved_frames.append({
                        "timestamp": f["timestamp"],
                        "base64": base64.b64encode(fh.read()).decode(),
                    })

        # Start RunPod engagement prediction immediately — runs in parallel with Step B
        # It only needs frames + niche + duration, not vision/energy/aesthetics
        engagement_task = asyncio.ensure_future(
            asyncio.wait_for(
                predict_engagement(
                    frames=list(frames),
                    audio_path=audio_path,
                    vision_data=None,
                    energy_data=None,
                    aesthetics_data=None,
                    niche=primary_niche,
                    duration=30.0,  # will be updated below if energy_data available
                ),
                timeout=330.0,
            )
        )

        # Step B: Run vision + audio analysis + trends in parallel
        import time as _time
        _step_b_start = _time.time()
        vision_data = None
        whisper_data = {"transcript": "", "language": "unknown", "hasSpeech": False, "segments": [], "verbalCTA": "None detected", "hookText": ""}
        audio_measurements = {"avgVolume": -30, "peakVolume": -10, "volumeProfile": "unknown", "audioQuality": "unknown"}
        gemini_audio = {}

        # Build parallel tasks
        tasks = {}

        if frames:
            tasks["vision"] = tracked(_pcb, "vision", analyse_frames_with_claude(frames, niche, platform, brief))

        # Embedding extraction — runs in parallel with Sonnet Vision in Step B.
        # 45s timeout guards against cold model downloads blocking the analysis.
        # Failures/timeouts are caught and embeddings are simply skipped.
        _signals = StageGroup(_pcb, "signals")
        try:
            from app.services.embedding_service import compute_all_embeddings, run_demucs_background
            _emb_frames = list(frames)
            _emb_audio  = audio_path
            _emb_video  = video_path
            async def _embeddings_with_timeout():
                try:
                    return await asyncio.wait_for(
                        loop.run_in_executor(
                            _executor, compute_all_embeddings,
                            _emb_frames, _emb_audio, _emb_video
                        ),
                        timeout=90.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("[Prysm] Embedding extraction timed out after 90s — skipping")
                    return {}
            tasks["embeddings"] = asyncio.ensure_future(_signals.run(_embeddings_with_timeout()))
            # Demucs: fire-and-forget background task using original video file.
            # Does NOT block the response — stems saved to a temp dir and discarded after use.
            asyncio.ensure_future(
                loop.run_in_executor(_executor, run_demucs_background, video_path)
            )
        except ImportError:
            logger.warning("[Prysm] embedding_service not available — skipping embeddings")

        if audio_path:
            tasks["whisper"] = tracked(_pcb, "speech", loop.run_in_executor(_executor, transcribe_audio, audio_path))
            tasks["audio_measure"] = loop.run_in_executor(_executor, measure_audio, audio_path)
            tasks["energy_curve"] = _signals.run(loop.run_in_executor(_executor, compute_audio_energy_curve, audio_path))
            # ACRCloud — song fingerprinting (runs in parallel, free, 5000/month)
            if settings.ACRCLOUD_HOST and settings.ACRCLOUD_ACCESS_KEY:
                from app.services.acrcloud_service import identify_song_multi_sample
                async def _acrcloud_with_timeout():
                    try:
                        return await asyncio.wait_for(
                            loop.run_in_executor(_executor, identify_song_multi_sample, audio_path),
                            timeout=30.0,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("[Prysm] ACRCloud timed out after 30s — skipping")
                        return {}
                tasks["acrcloud"] = tracked(_pcb, "music_id", _acrcloud_with_timeout())
            if settings.GEMINI_API_KEY:
                gemini_frames = list(frames)[:12] if frames else None
                async def _gemini_with_timeout():
                    try:
                        return await asyncio.wait_for(
                            loop.run_in_executor(_executor, analyse_audio_with_gemini, audio_path, gemini_frames),
                            timeout=90.0,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("[Prysm Audio] Gemini timed out after 90s — skipping")
                        return {}
                tasks["gemini_audio"] = tracked(_pcb, "audio", _gemini_with_timeout())

        # LAION Aesthetics v2 — per-frame visual quality scores
        if frames:
            tasks["aesthetics"] = _signals.run(loop.run_in_executor(_executor, score_frames, list(frames)))

        # Extra evidence (hackathon, AI_PROVIDER=meta): consensus transcript, second audio listener, SAM subjects,
        # the Modal signal models. Each part has its own flag and fails soft; see app/studio/evidence.py.
        if settings.META_MODE:
            from app.studio import evidence as _evidence_mod
            if any(_evidence_mod.enabled_parts().values()):
                tasks["evidence"] = _evidence_mod.collect_evidence(video_path, work_dir, _pcb)

        # Generic trend fetch — runs in parallel with Vision using just niche+platform.
        # After Vision completes we do a quick vibe-enrichment pass if keywords are available.
        # This saves ~30s vs the old approach of waiting for Vision to finish first.
        async def _trend_generic_with_timeout():
            try:
                return await asyncio.wait_for(
                    get_trend_data(primary_niche, platform),
                    timeout=50.0,
                )
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"[Prysm] Generic trend fetch failed: {e}")
                return {}
        tasks["trend_generic"] = tracked(_pcb, "trends", _trend_generic_with_timeout())

        # Await all parallel tasks
        task_keys = list(tasks.keys())
        task_results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        results_map = dict(zip(task_keys, task_results))

        # Unpack results (handle exceptions gracefully)
        if "vision" in results_map and not isinstance(results_map["vision"], Exception):
            vision_data = results_map["vision"]
        elif "vision" in results_map:
            logger.error(f"[Prysm] Vision failed: {results_map['vision']}")

        if "whisper" in results_map and not isinstance(results_map["whisper"], Exception):
            whisper_data = results_map["whisper"]
        elif "whisper" in results_map:
            logger.error(f"[Prysm] Whisper failed: {results_map['whisper']}")

        if "audio_measure" in results_map and not isinstance(results_map["audio_measure"], Exception):
            audio_measurements = results_map["audio_measure"]

        if "gemini_audio" in results_map and not isinstance(results_map["gemini_audio"], Exception):
            gemini_audio = results_map["gemini_audio"]
        elif "gemini_audio" in results_map:
            logger.error(f"[Prysm] Gemini audio failed: {results_map['gemini_audio']}")

        # ACRCloud confirmed song — overrides Gemini's unreliable guess
        if "acrcloud" in results_map and not isinstance(results_map["acrcloud"], Exception):
            acr = results_map["acrcloud"]
            if acr and acr.get("found"):
                logger.info(
                    f"[Prysm] ACRCloud confirmed: '{acr['title']}' by '{acr['artist']}' "
                    f"(confidence={acr.get('confidence')})"
                )
                if gemini_audio is None:
                    gemini_audio = {}
                gemini_audio["songTitle"] = acr["title"]
                gemini_audio["songArtist"] = acr.get("artist", "")
                gemini_audio["songConfidence"] = acr.get("confidence", 0)
                gemini_audio["songConfirmed"] = True
                gemini_audio["hasMusic"] = True
            elif acr and not acr.get("found"):
                logger.info(f"[Prysm] ACRCloud: no song match ({acr.get('reason', 'unknown')})")

        # Extra evidence → the inputs the original prompts already read (whisper_data, gemini_audio, extra_signals, embeddings)
        _evidence = results_map.get("evidence") if not isinstance(results_map.get("evidence"), Exception) else None
        _gemini_ok = bool(gemini_audio) and any(k in gemini_audio for k in ("hasMusic", "voiceQuality", "speechSummary"))
        _extra_signals = ""
        if _evidence is not None:
            from app.studio import evidence as _ev
            whisper_data = _ev.merge_transcript(whisper_data, _evidence)
            gemini_audio, _second_opinion = _ev.merge_audio(gemini_audio, _evidence)
            gemini_audio = _ev.merge_music(gemini_audio, bool(gemini_audio.get("songConfirmed")), _evidence)
            _extra_signals = _ev.extra_signals_text(_evidence, _second_opinion)
        elif "evidence" in results_map:
            logger.warning(f"[Prysm] Extra evidence failed: {results_map['evidence']}")

        energy_data = {}
        if "energy_curve" in results_map and not isinstance(results_map["energy_curve"], Exception):
            energy_data = results_map["energy_curve"] or {}
        elif "energy_curve" in results_map:
            logger.error(f"[Prysm] EnCodec energy curve failed: {results_map['energy_curve']}")

        aesthetics_data = {}
        if "aesthetics" in results_map and not isinstance(results_map["aesthetics"], Exception):
            aesthetics_data = results_map["aesthetics"] or {}
        elif "aesthetics" in results_map:
            logger.error(f"[Prysm] Aesthetics scoring failed: {results_map['aesthetics']}")

        # ── Trend data — generic fetch already ran in parallel with Vision ──────
        # Now extract vibe signals from Vision + Gemini and do a quick enrichment
        # pass if keywords/mood/energy are available. This second call hits a
        # Unpack embedding results
        embeddings_result: Dict = {}
        if "embeddings" in results_map:
            raw_emb = results_map["embeddings"]
            if isinstance(raw_emb, Exception):
                logger.warning(f"[Prysm] Embedding extraction failed: {raw_emb}")
            elif isinstance(raw_emb, dict):
                embeddings_result = raw_emb
                dims = {k: len(v) if v else None for k, v in raw_emb.items()}
                logger.info(f"[Prysm] Embeddings ready: {dims}")

        if _evidence is not None and not embeddings_result:
            embeddings_result = _ev.embeddings_from_signals(_evidence)  # the same vectors, from the Modal models

        # 2-hour Redis cache keyed on niche+platform+keywords, so repeated analyses
        # of the same content type skip the Gemini search entirely (~40s savings).
        trend_data = results_map.get("trend_generic") or {}
        if isinstance(trend_data, Exception):
            logger.error(f"[Prysm] Generic trend fetch failed: {trend_data}")
            trend_data = {}

        try:
            vision_keywords = []
            trend_scene_type = None

            if vision_data:
                summary = vision_data.get("visualSummary", "") or ""
                frames_data = vision_data.get("frames", []) or []

                # Extract dominant scene type — most frequent across frames
                scene_type_counts: Dict[str, int] = {}
                for f in frames_data:
                    st = f.get("sceneType", "")
                    if st and st.lower() not in ("unknown", "none", ""):
                        scene_type_counts[st] = scene_type_counts.get(st, 0) + 1
                if scene_type_counts:
                    trend_scene_type = max(scene_type_counts, key=scene_type_counts.get)

                # Build keyword list from visual summary
                sentences = summary.split(".")[:2]
                raw_keywords = " ".join(sentences).lower()
                _stopwords = {
                    "the", "a", "an", "is", "are", "in", "on", "at", "of", "to", "and",
                    "with", "that", "this", "for", "from", "as", "it", "its", "she", "he",
                    "they", "her", "his", "their", "by", "be", "was", "were", "has", "have",
                    "does", "do", "which", "who", "what", "where", "when", "while", "video",
                    "creator", "person", "shows", "show", "using", "use", "can", "will",
                }
                words = [w.strip(".,!?") for w in raw_keywords.split() if len(w) > 3]
                content_words = [w for w in words if w not in _stopwords]
                seen: set = set()
                for w in content_words:
                    if w not in seen:
                        vision_keywords.append(w)
                        seen.add(w)
                    if len(vision_keywords) >= 6:
                        break

            # Extract mood + energy from Gemini audio analysis
            trend_mood = None
            trend_energy = None
            if gemini_audio and gemini_audio.get("hasMusic"):
                raw_mood = (gemini_audio.get("musicMood", "") or "").strip()
                raw_energy = (gemini_audio.get("musicEnergy", "") or "").strip()
                if raw_mood and raw_mood.lower() not in ("unknown", "none", ""):
                    trend_mood = raw_mood
                if raw_energy and raw_energy.lower() not in ("unknown", "none", ""):
                    trend_energy = raw_energy

            has_vibe_signals = any([vision_keywords, trend_mood, trend_energy, trend_scene_type])
            logger.info(
                f"[Prysm] Trend signals — keywords: {vision_keywords}, "
                f"scene: {trend_scene_type}, mood: {trend_mood}, energy: {trend_energy}"
            )

            if has_vibe_signals:
                # Enrichment pass — adds video-specific keywords/mood to the generic result.
                # Cached for 2h per niche+platform+keywords_hash so repeated analyses skip Gemini.
                import hashlib as _hashlib
                from app.core.cache import cache_get, cache_set
                vibe_key_raw = f"{primary_niche.lower()}:{platform.lower()}:{','.join(vision_keywords)}:{trend_mood or ''}:{trend_energy or ''}:{trend_scene_type or ''}"
                vibe_cache_key = f"trends_vibe:{_hashlib.md5(vibe_key_raw.encode()).hexdigest()}"

                vibe_cached = await cache_get(vibe_cache_key)
                if vibe_cached:
                    logger.info(f"[Prysm] Vibe-trend cache hit — skipping Gemini enrichment (~40s saved)")
                    trend_data = vibe_cached
                else:
                    enriched = await asyncio.wait_for(
                        get_trend_data(
                            primary_niche,
                            platform,
                            keywords=vision_keywords if vision_keywords else None,
                            mood=trend_mood,
                            energy=trend_energy,
                            scene_type=trend_scene_type,
                        ),
                        timeout=50.0,
                    )
                    if enriched:
                        trend_data = enriched
                        await cache_set(vibe_cache_key, enriched, ttl=7200)  # 2h cache
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[Prysm] Trend enrichment timed out/failed — using generic data: {e}")

        # ── Fix 3: Song-to-trend matching ────────────────────────────────────
        # Gemini identified the song — check if it's in our trending DB.
        # This catches creator-driven revival trends (e.g. old song + viral choreography)
        # that wouldn't appear in hashtag-based trend searches.
        song_trend_match = {}
        try:
            from app.services.trend_scraper_service import lookup_song_in_trends
            gemini_song = ""
            if gemini_audio:
                # 1. Best signal: Gemini's explicit songTitle field (added to prompt)
                song_title_field = (gemini_audio.get("songTitle", "") or "").strip()
                if song_title_field and song_title_field.lower() not in ("unknown", "none", ""):
                    gemini_song = song_title_field

                # 2. Fallback: parse musicGenre field (e.g. "Tooh - Bollywood pop")
                if not gemini_song:
                    raw_genre = gemini_audio.get("musicGenre", "") or ""
                    candidate = raw_genre.split(" - ")[0].split(",")[0].strip() if raw_genre else ""
                    _generic_terms = {
                        "pop", "hip hop", "rock", "edm", "trap", "jazz", "classical",
                        "bollywood", "punjabi", "folk", "indie", "lofi", "unknown", "none",
                        "music", "song", "audio", "background",
                    }
                    if (candidate and len(candidate) >= 3
                            and not any(g in candidate.lower() for g in _generic_terms)):
                        gemini_song = candidate

            if gemini_song:
                song_trend_match = await asyncio.wait_for(
                    lookup_song_in_trends(gemini_song, platform.lower()),
                    timeout=3.0,
                )
                if song_trend_match.get("found"):
                    logger.info(
                        f"[Prysm] Song trend match: '{gemini_song}' → "
                        f"{song_trend_match['usage_count']} uses, avg likes {song_trend_match['avg_likes']}"
                    )
                else:
                    logger.info(f"[Prysm] Song '{gemini_song}' not found in trend DB")
        except Exception as e:
            logger.warning(f"[Prysm] Song-trend lookup failed: {e}")

        _step_b_elapsed = _time.time() - _step_b_start
        logger.info(f"[Prysm] Parallel tasks done in {_step_b_elapsed:.1f}s: vision={'ok' if vision_data else 'fail'}, whisper={whisper_data.get('hasSpeech')}, gemini={bool(gemini_audio)}, energy={bool(energy_data)}, aesthetics={bool(aesthetics_data)}, trends={bool(trend_data)}")

        # Await RunPod engagement task (started in parallel with Step B above)
        # By this point it has had ~60-90s head start, so usually already done
        engagement_data = {}
        try:
            engagement_data = await engagement_task
            if not engagement_data:
                engagement_data = {}
            logger.info(f"[Prysm] Engagement: nawp={engagement_data.get('nawp')}, ecr={engagement_data.get('ecr')}, source={engagement_data.get('source')}")
        except asyncio.TimeoutError:
            logger.warning("[Prysm] Engagement prediction timed out — skipping")
        except Exception as e:
            logger.error(f"[Prysm] Engagement prediction failed: {e}")
            engagement_data = {}

        # ── Credit checkpoint — after Stage 1, before Stage 2 (Claude scoring) ──
        # Compute Stage 1 cost so far: Vision + Gemini. RunPod and Apify are cheap
        # enough that we don't gate on them — only the two large LLM calls matter.
        if credit_checker is not None:
            stage1_usd = 0.0
            if vision_data and vision_data.get("_usage"):
                u = vision_data["_usage"]
                rates = PRICING.get(u["model"], PRICING["claude-sonnet-4-6"])
                stage1_usd += (u["input_tokens"] * rates["input"] + u["output_tokens"] * rates["output"]) / 1_000_000
            if gemini_audio and gemini_audio.get("_usage"):
                u = gemini_audio["_usage"]
                rates = PRICING.get(u["model"], {"input": 1.25, "output": 10.0})
                stage1_usd += (u["input_tokens"] * rates["input"] + u["output_tokens"] * rates["output"]) / 1_000_000
            exhausted = await credit_checker(round(stage1_usd, 6))
            if exhausted:
                raise CreditExhaustedError(stage1_cost_usd=round(stage1_usd, 6))

        has_vision = vision_data is not None
        has_audio = (
            whisper_data.get("hasSpeech", False)
            or audio_measurements.get("avgVolume", -99) > -50
            or gemini_audio.get("hasSpeech", False)
            or gemini_audio.get("hasMusic", False)
            or bool(audio_path)
        )

        # Final Claude analysis — build prompts then call Claude
        _step_c_start = _time.time()
        client = _get_anthropic()
        _call_a_prompt, _call_b_prompt = build_scoring_prompts(
            niche=niche, platform=platform, source_type=source_type, intent=intent,
            brief=brief, content_goal=content_goal, scored_weights=scored_weights,
            niche_ctx=niche_ctx, hook_retention_ctx=hook_retention_ctx,
            niche_trend_lens=niche_trend_lens,
            vision_data=vision_data, whisper_data=whisper_data, gemini_audio=gemini_audio,
            audio_measurements=audio_measurements, energy_data=energy_data,
            aesthetics_data=aesthetics_data, engagement_data=engagement_data,
            trend_data=trend_data, song_trend_match=song_trend_match,
            saved_frames=saved_frames, post_metadata=post_metadata, audio_path=audio_path,
            has_vision=has_vision, has_audio=has_audio,
            extra_signals=_extra_signals,
        )
        is_competitor = intent == "competitor"

        # ── Run both scoring calls in parallel ──
        logger.info(f"[Prysm] Starting parallel Claude scoring (call A: params 1-5, call B: params 6-10)")
        if _pcb:
            _pcb("scoring", "running")
        _call_a_msg = [{"role": "user", "content": _call_a_prompt}]
        _call_b_msg = [{"role": "user", "content": _call_b_prompt}]

        resp_a, resp_b = await asyncio.gather(
            _claude_with_retry(client, model="claude-sonnet-4-6", max_tokens=9000 if is_competitor else 7000, messages=_call_a_msg),
            _claude_with_retry(client, model="claude-sonnet-4-6", max_tokens=6000, messages=_call_b_msg),
        )

        _step_c_elapsed = _time.time() - _step_c_start
        logger.info(f"[Prysm] Claude scoring done in {_step_c_elapsed:.1f}s (parallel A+B), A={len(resp_a.content[0].text)} chars, B={len(resp_b.content[0].text)} chars")

        # Track combined token usage
        _scoring_usage = {
            "input_tokens": resp_a.usage.input_tokens + resp_b.usage.input_tokens,
            "output_tokens": resp_a.usage.output_tokens + resp_b.usage.output_tokens,
            "model": _resp_model(resp_a, "claude-sonnet-4-6"),
        }

        def _clean_and_parse(raw: str, label: str) -> dict:
            if settings.META_MODE:
                from app.studio.json_tolerant import extract_json_object
                raw = extract_json_object(raw)  # code fences / text around the JSON (Muse Spark)
            raw = re.sub(r"```json\n?", "", raw)
            raw = re.sub(r"```\n?", "", raw)
            raw = raw.strip()
            raw = re.sub(r'"overallScore"\s*:\s*\d+\s*,?\s*', '', raw)
            raw = re.sub(r'"scoreLabel"\s*:\s*"[^"]*"\s*,?\s*', '', raw)
            raw = re.sub(r'"predictedScoreAfterFixes"\s*:\s*\d+\s*,?\s*', '', raw)
            from json_repair import repair_json
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                logger.warning(f"[Prysm] {label} JSON parse failed ({e}), using json_repair on {len(raw)} chars")
                try:
                    return json.loads(repair_json(raw, return_objects=False))
                except Exception as e2:
                    logger.error(f"[Prysm] {label} json_repair also failed ({e2}), dumping first 500 chars: {raw[:500]}")
                    raise ValueError(f"Could not parse or repair Claude {label} JSON response ({len(raw)} chars)")

        async def _parse_with_one_repair(resp, msgs, max_tok, label):
            """Parse a scoring reply; with AI_PROVIDER=meta an unparsable reply gets exactly one 'return only the JSON' retry."""
            try:
                return _clean_and_parse(resp.content[0].text, label)
            except ValueError:
                if not settings.META_MODE:
                    raise
                logger.warning(f"[Prysm] {label} reply was not valid JSON — one repair retry")
                retry = await _claude_with_retry(
                    client, model="claude-sonnet-4-6", max_tokens=max_tok,
                    messages=list(msgs) + [
                        {"role": "assistant", "content": resp.content[0].text},
                        {"role": "user", "content": "That reply was not valid JSON. Return ONLY the complete, corrected JSON object — no commentary, no code fences."},
                    ],
                )
                _scoring_usage["input_tokens"] += retry.usage.input_tokens
                _scoring_usage["output_tokens"] += retry.usage.output_tokens
                return _clean_and_parse(retry.content[0].text, label + "-repair")

        if _pcb:
            _pcb("scoring", "ok")
            _pcb("report", "running")
        result_a = await _parse_with_one_repair(resp_a, _call_a_msg, 9000 if is_competitor else 7000, "call-A")
        result_b = await _parse_with_one_repair(resp_b, _call_b_msg, 6000, "call-B")

        # Merge: top-level fields from A, parameters = A params + B params
        result = result_a
        params_b = result_b.get("parameters", [])

        # Deduplicate parameters: B wins on conflict (advisory params are B's domain)
        # Maintain canonical order so the UI always displays params in the right sequence
        _CANONICAL_ORDER = [
            "Hook & Opening", "Retention & Pacing", "Shareability", "Audio & Music",
            "Trend Alignment", "Visual Quality & Originality", "On-Screen Text",
            "Save Potential", "Replay Value", "Call to Action",
        ]
        _param_map: dict[str, dict] = {}
        for _p in result_a.get("parameters", []) + params_b:
            _param_map[_p.get("name", "")] = _p  # B overwrites A on duplicate names
        result["parameters"] = sorted(
            _param_map.values(),
            key=lambda _p: _CANONICAL_ORDER.index(_p.get("name", "")) if _p.get("name") in _CANONICAL_ORDER else len(_CANONICAL_ORDER),
        )

        # Always stamp platform/niche explicitly so the frontend never sees 'undefined'
        result["platform"] = platform
        result["niche"] = niche

        # ── Compute overallScore using niche-specific empirical weights ──
        # Only SCORED_PARAMETERS contribute to the overall score.
        # Advisory parameters (CTA, On-Screen Text, Save Potential, Replay Value)
        # are analysed and shown but do not affect the number.
        # (scored_weights already resolved at function start)

        # Coerce all string fields on each parameter — Opus occasionally returns
        # howToDoIt / whatToChange etc. as arrays instead of plain strings.
        _TEXT_FIELDS = ("problem","fix","whatIsHappening","whyItMatters","whatToChange","howToDoIt","whatToExpect")
        for param in result.get("parameters", []):
            for field in _TEXT_FIELDS:
                v = param.get(field)
                if isinstance(v, list):
                    param[field] = " ".join(str(x) for x in v)
                elif v is not None and not isinstance(v, str):
                    param[field] = str(v)

        # Post-processing validation: remove/rewrite steps that duplicate existing video content
        try:
            result = _validate_suggestions(result, whisper_data, vision_data, intent)
        except Exception as _ve:
            logger.warning(f"[Validate] Suggestion validation failed (non-fatal): {_ve}")

        # Tag each parameter as advisory or scored, and inject its weight
        _norm_weights = normalise_scored_weights(scored_weights)
        for param in result.get("parameters", []):
            name = param.get("name", "")
            is_advisory = name in ADVISORY_PARAMETERS
            param["advisory"] = is_advisory
            # Hackathon change: weights normalised over the six scored parameters (see app/studio/score_math.py)
            param["weight"] = round(_norm_weights.get(name, 0.0) * 100, 1) if not is_advisory else 0

        # Overall + "after fixes" projection use the same normalised weights (weak scored params fixed to 75)
        result["overallScore"], result["predictedScoreAfterFixes"] = overall_and_predicted(result.get("parameters", []), scored_weights)

        # Score label
        s = result["overallScore"]
        if s >= 90:
            result["scoreLabel"] = "Exceptional"
        elif s >= 80:
            result["scoreLabel"] = "Viral-Ready"
        elif s >= 70:
            result["scoreLabel"] = "Strong Content"
        elif s >= 55:
            result["scoreLabel"] = "Good Potential, Needs Polish"
        elif s >= 40:
            result["scoreLabel"] = "Below Average"
        else:
            result["scoreLabel"] = "Needs Significant Work"

        # For competitor analysis, override label to be positive
        if intent == "competitor":
            if s >= 80:
                result["scoreLabel"] = "Excellent Execution"
            elif s >= 65:
                result["scoreLabel"] = "Strong Concept, Needs Polish"
            else:
                result["scoreLabel"] = "Has Potential"

        # ── Clean up hashtags ──
        # json_repair can corrupt the hashtags array — extract from caption as fallback
        raw_hashtags = result.get("suggestedHashtags", [])
        # Clean: remove # prefixes, empty strings, duplicates
        cleaned_hashtags = []
        seen = set()
        for h in raw_hashtags:
            if isinstance(h, str):
                tag = h.lstrip('#').strip()
                if tag and tag.lower() not in seen:
                    seen.add(tag.lower())
                    cleaned_hashtags.append(tag)

        # If we have fewer than 3 hashtags, try extracting from suggestedCaption
        if len(cleaned_hashtags) < 3:
            caption = result.get("suggestedCaption", "")
            if caption:
                caption_tags = re.findall(r'#(\w+)', caption)
                for tag in caption_tags:
                    if tag.lower() not in seen and len(tag) > 1:
                        seen.add(tag.lower())
                        cleaned_hashtags.append(tag)
            logger.info(f"[Prysm] Recovered hashtags from caption: {cleaned_hashtags}")

        result["suggestedHashtags"] = cleaned_hashtags

        # Attach extra data
        if vision_data:
            result["visionData"] = vision_data
        if saved_frames:
            result["frames"] = saved_frames
        result["audioAnalysis"] = {**whisper_data, **audio_measurements, "gemini": gemini_audio}
        result["energyCurve"] = energy_data
        result["aestheticsData"] = aesthetics_data
        result["engagementPrediction"] = engagement_data
        result["trendData"] = trend_data
        if embeddings_result:
            result["_embeddings"] = embeddings_result
        if post_metadata:
            result["postMetadata"] = post_metadata
            if post_metadata.get("_apify_run_id"):
                result["_apify_run_id"] = post_metadata["_apify_run_id"]
        result["intent"] = intent
        result["source_type"] = source_type
        # ── Cost tracking — accumulate real API costs ──
        cost = CostTracker()

        # Vision call cost
        if vision_data and vision_data.get("_usage"):
            u = vision_data.pop("_usage")
            cost.add_claude(u["model"], u["input_tokens"], u["output_tokens"], label="Vision (frames)")

        # Scoring call cost
        if _scoring_usage:
            cost.add_claude(_scoring_usage["model"], _scoring_usage["input_tokens"], _scoring_usage["output_tokens"], label="Scoring (10 params)")

        # Gemini audio cost
        if gemini_audio and gemini_audio.get("_usage"):
            u = gemini_audio.pop("_usage")
            cost.add_gemini(u["model"], u["input_tokens"], u["output_tokens"], label="Audio analysis (Gemini)")

        # Groq is free tier — no cost

        # RunPod engagement cost (exact — from executionTime in response)
        if engagement_data and engagement_data.get("_runpod_execution_ms"):
            cost.add_runpod(engagement_data.pop("_runpod_execution_ms"))

        # Apify cost — fetch exact cost from Apify runs API
        if source_type == "link" and result.get("_apify_run_id"):
            apify_run_id = result.pop("_apify_run_id")
            apify_cost_usd = _fetch_apify_run_cost(apify_run_id)
            cost.add_apify(apify_cost_usd)

        if _evidence is not None and _evidence.cost_usd:
            cost.add_other("Extra evidence (Spark audio, Voice Transcribe, SAM 3.1, signal models)", "meta+modal", _evidence.cost_usd)

        logger.info(f"[Prysm] Total cost: ${cost.total_usd:.4f}")
        result["_cost"] = cost.summary()
        if settings.META_MODE:
            from app.studio import evidence as _ev2
            _acr_found = bool(gemini_audio and gemini_audio.get("songConfirmed"))
            _trends_ok = bool(trend_data and (trend_data.get("trendingFormats") or trend_data.get("trendingHashtags") or trend_data.get("trendingAudios")))
            result["modelsUsed"] = _ev2.models_used(_evidence, whisper_ok=bool(whisper_data.get("hasSpeech")), gemini_ok=_gemini_ok,
                                                    acr_found=_acr_found, trends_ok=_trends_ok, embeddings_ok=bool(embeddings_result))

        result["_debug"] = {
            "visionSucceeded": has_vision,
            "framesAnalyzed": len(vision_data.get("frames", [])) if vision_data else 0,
            "framesSaved": len(saved_frames),
            "hasAudio": has_audio,
            "speech": whisper_data.get("hasSpeech"),
            "language": whisper_data.get("language"),
            "verbalCTA": whisper_data.get("verbalCTA"),
        }
        if _evidence is not None:
            result["_debug"]["extraEvidence"] = {"parts": _evidence.parts, "seconds": _evidence.seconds, "notes": _evidence.notes}

        if _pcb:
            _pcb("report", "ok")
        logger.info(f"[Prysm] ✅ Done | score={result.get('overallScore')} | vision={has_vision} | speech={whisper_data.get('hasSpeech')} | lang={whisper_data.get('language')}")
        return result

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
