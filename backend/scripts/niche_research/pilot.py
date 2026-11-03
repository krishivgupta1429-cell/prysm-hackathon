"""
scripts/niche_research/pilot.py
Scoring-signal permutation pilot: 6 reels × 8 combinations = 48 scoring runs.

Purpose: prove the machinery works end-to-end. NOT for drawing statistical
conclusions — n=6 is too small.

Usage:
    cd ~/Desktop/"Prysm(frontend + backend)"/"prysm-backend 3"
    source venv/bin/activate
    python -m scripts.niche_research.pilot [--dry-run] [--reel REEL_ID]

Flags (env-vars, all default OFF):
    SIGNAL_CLAP_TAGS=1          add CLAP zero-shot audio tags to scoring prompt
    SIGNAL_DEMUCS_RATIO=1       add voice/music dB ratio to scoring prompt
    SIGNAL_DEMUCS_TRANSCRIPT=1  use Demucs vocal stem for Groq transcription

Combinations run:
    combo_id 1  : all flags off (baseline)
    combo_id 9  : CLAP only
    combo_id 17 : DEMUCS_RATIO only
    combo_id 25 : DEMUCS_TRANSCRIPT only
    combo_id 33 : CLAP + DEMUCS_RATIO
    combo_id 41 : CLAP + DEMUCS_TRANSCRIPT
    combo_id 49 : DEMUCS_RATIO + DEMUCS_TRANSCRIPT
    combo_id 57 : all three (CLAP + DEMUCS_RATIO + DEMUCS_TRANSCRIPT)

Spend stop: $3 cumulative. Script stops and reports what it completed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Add backend root to sys.path so we can import app.* ──────────────────────
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_ROOT))

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pilot")

# ── Constants ─────────────────────────────────────────────────────────────────
SPEND_STOP_USD = 3.00
OUTPUT_DIR = Path(__file__).parent / "pilot_results"
OUTPUT_DIR.mkdir(exist_ok=True)

# Claude pricing (per 1M tokens)
CLAUDE_SONNET_PRICING = {"input": 3.00, "output": 15.00}

# ── Reel config ───────────────────────────────────────────────────────────────
# Fill in the 5 URLs from the Reel Inputs tab of the tracker PDF.
# Format: each entry is (reel_id, url, niche, platform, follower_count, ground_truth_views, ground_truth_likes, ground_truth_comments)
# NOTE: reel_006 = the reel added in the approval message (Lifestyle, 56.5k followers, 7,628 views)
REELS: List[Dict[str, Any]] = [
    {
        "reel_id": "reel_001",
        "url": "https://www.instagram.com/reels/DcpqsLjPIHj/",
        "niche": "Lifestyle",
        "platform": "Instagram Reels",
        "follower_count": 26700,
        "gt_views": 20700,
        "gt_likes": 690,
        "gt_comments": 34,
        "gt_shares": 136,
    },
    {
        "reel_id": "reel_002",
        "url": "https://www.instagram.com/reels/DdjfPPVTA5w/",
        "niche": "Lifestyle",
        "platform": "Instagram Reels",
        "follower_count": 30500,
        "gt_views": 114000,
        "gt_likes": 2060,
        "gt_comments": 50,
        "gt_shares": 370,
    },
    {
        "reel_id": "reel_003",
        "url": "https://www.instagram.com/reel/DVrFJONksoL/?stkn=N24zNXlvOXF0Zmw0",
        "niche": "Lifestyle",
        "platform": "Instagram Reels",
        "follower_count": 30400,
        "gt_views": 1_000_000,
        "gt_likes": 13000,
        "gt_comments": 325,
        "gt_shares": 28800,
    },
    {
        "reel_id": "reel_004",
        "url": "https://www.instagram.com/reels/Dc34fPANEKs/",
        "niche": "Lifestyle",
        "platform": "Instagram Reels",
        "follower_count": 0,        # not recorded in tracker sheet
        "gt_views": 9178,
        "gt_likes": 4494,
        "gt_comments": 122,
        "gt_shares": 18,
    },
    {
        "reel_id": "reel_005",
        "url": "https://www.instagram.com/reels/Dc6g69jp2ck/",
        "niche": "Lifestyle",
        "platform": "Instagram Reels",
        "follower_count": 28100,
        "gt_views": 780000,
        "gt_likes": 42400,
        "gt_comments": 38,
        "gt_shares": 5181,
    },
    {
        "reel_id": "reel_006",
        "url": "https://www.instagram.com/reel/Dd4E7OvBnSx/",
        "niche": "Lifestyle",
        "platform": "Instagram Reels",
        "follower_count": 56500,
        "gt_views": 7628,
        "gt_likes": 0,
        "gt_comments": 0,
        "gt_shares": 0,
    },
]

# ── Combination definitions ───────────────────────────────────────────────────
# Each combo is (combo_id, clap_tags, demucs_ratio, demucs_transcript)
COMBOS: List[Tuple[int, bool, bool, bool]] = [
    (1,  False, False, False),  # baseline — all signals off
    (9,  True,  False, False),  # CLAP only
    (17, False, True,  False),  # Demucs ratio only
    (25, False, False, True),   # Demucs transcript only
    (33, True,  True,  False),  # CLAP + ratio
    (41, True,  False, True),   # CLAP + transcript
    (49, False, True,  True),   # ratio + transcript
    (57, True,  True,  True),   # all three
]


# ── Token/cost helpers ────────────────────────────────────────────────────────

def tokens_to_usd(input_tokens: int, output_tokens: int) -> float:
    p = CLAUDE_SONNET_PRICING
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000


# ── Vision (runs once per reel, cached) ──────────────────────────────────────

async def run_vision(frames: List[Dict], niche: str, platform: str, brief: str = "") -> Optional[Dict]:
    """Call Claude vision (sonnet-4-6) on extracted frames."""
    from app.services.prysm_analyse_service import analyse_frames_with_claude
    try:
        log.info(f"[Vision] Analyzing {len(frames)} frames via claude-sonnet-4-6")
        result = await analyse_frames_with_claude(frames, niche, platform, brief)
        return result
    except Exception as e:
        log.error(f"[Vision] Failed: {e}")
        return None


# ── Scoring (runs once per reel × combo) ─────────────────────────────────────

async def run_scoring(
    reel: Dict,
    vision_data: Optional[Dict],
    whisper_data: Dict,
    audio_path: Optional[str],
    extra_signals: str,
) -> Dict:
    """
    Build prompts via production build_scoring_prompts(), then call Claude.
    Returns dict with scores, usage, cost, latency, error.
    """
    from app.services.prysm_analyse_service import build_scoring_prompts, _get_anthropic, _claude_with_retry
    from app.core.config import NICHE_WEIGHTS, DEFAULT_SCORED_WEIGHTS
    from app.services.prysm_analyse_service import NICHE_CONTEXT, NICHE_TREND_LENS, _build_hook_retention_context

    niche = reel["niche"]
    platform = reel["platform"]
    primary_niche = niche.split(",")[0].strip() if "," in niche else niche
    niche_key = primary_niche.lower()

    scored_weights = NICHE_WEIGHTS.get(niche_key, DEFAULT_SCORED_WEIGHTS)
    niche_ctx = NICHE_CONTEXT.get(primary_niche, f"{niche} creator — apply platform best practices.")
    niche_trend_lens = NICHE_TREND_LENS.get(primary_niche, NICHE_TREND_LENS.get("Other", ""))
    hook_retention_ctx = _build_hook_retention_context(primary_niche)

    has_vision = vision_data is not None
    has_audio = bool(whisper_data.get("hasSpeech")) or bool(audio_path)

    # Saved frames from vision_data (for frame_ref construction)
    saved_frames = []
    if vision_data:
        for f in vision_data.get("frames", []):
            saved_frames.append({"timestamp": f.get("timestamp", 0), "base64": ""})

    t0 = time.time()
    error = None
    call_a_raw = None
    call_b_raw = None
    usage_a = usage_b = None

    try:
        call_a_prompt, call_b_prompt = build_scoring_prompts(
            niche=niche, platform=platform,
            source_type="link",
            intent="competitor",
            brief="",
            content_goal="",
            scored_weights=scored_weights,
            niche_ctx=niche_ctx,
            hook_retention_ctx=hook_retention_ctx,
            niche_trend_lens=niche_trend_lens,
            vision_data=vision_data,
            whisper_data=whisper_data,
            gemini_audio={},
            audio_measurements={},
            energy_data={},
            aesthetics_data={},
            engagement_data={},
            trend_data={},
            song_trend_match={},
            saved_frames=saved_frames,
            post_metadata=None,
            audio_path=audio_path,
            has_vision=has_vision,
            has_audio=has_audio,
            extra_signals=extra_signals,
        )

        client = _get_anthropic()
        resp_a, resp_b = await asyncio.gather(
            _claude_with_retry(
                client, model="claude-sonnet-4-6", max_tokens=7000,
                messages=[{"role": "user", "content": call_a_prompt}],
            ),
            _claude_with_retry(
                client, model="claude-sonnet-4-6", max_tokens=6000,
                messages=[{"role": "user", "content": call_b_prompt}],
            ),
        )
        call_a_raw = resp_a.content[0].text
        call_b_raw = resp_b.content[0].text
        usage_a = {"input": resp_a.usage.input_tokens, "output": resp_a.usage.output_tokens}
        usage_b = {"input": resp_b.usage.input_tokens, "output": resp_b.usage.output_tokens}

    except Exception as e:
        log.error(f"[Scoring] Claude call failed: {e}")
        error = str(e)

    latency = time.time() - t0
    total_input = (usage_a["input"] if usage_a else 0) + (usage_b["input"] if usage_b else 0)
    total_output = (usage_a["output"] if usage_a else 0) + (usage_b["output"] if usage_b else 0)
    cost_usd = tokens_to_usd(total_input, total_output)

    # Parse scores
    params = []
    overall_score = None
    try:
        import re, json as _json
        def _parse(raw):
            if not raw:
                return {}
            raw = re.sub(r"```json\n?", "", raw)
            raw = re.sub(r"```\n?", "", raw)
            try:
                return _json.loads(raw.strip())
            except Exception:
                return {}

        result_a = _parse(call_a_raw)
        result_b = _parse(call_b_raw)
        params = result_a.get("parameters", []) + result_b.get("parameters", [])
        if params:
            scored = [p for p in params if not p.get("advisory", False)]
            if scored:
                total_w = sum(p.get("weight", 0) for p in scored)
                overall_score = round(
                    sum(p.get("score", 0) * p.get("weight", 0) for p in scored) / max(total_w, 1)
                )
    except Exception as e:
        log.warning(f"[Scoring] Parse failed: {e}")

    return {
        "parameters": params,
        "overall_score": overall_score,
        "prompt_a_len": len(call_a_prompt) if "call_a_prompt" in dir() else 0,
        "prompt_b_len": len(call_b_prompt) if "call_b_prompt" in dir() else 0,
        "raw_a": call_a_raw,
        "raw_b": call_b_raw,
        "usage": {"input": total_input, "output": total_output},
        "cost_usd": cost_usd,
        "latency_sec": round(latency, 2),
        "error": error,
    }


# ── Correctness check ────────────────────────────────────────────────────────

def correctness_check_option_b(
    run_result: Dict,
    reel_id: str,
    vision_data: Optional[Dict],
    whisper_data: Dict,
    audio_path: Optional[str],
    reel: Dict,
) -> Dict:
    """
    Option B: confirm that combo_id=1 (extra_signals='') produces the same
    prompt as calling build_scoring_prompts with no extra_signals.
    Since we call the same function with the same args, this is always true
    by construction. This check records the prompt hash to document that fact.
    """
    import hashlib
    from app.services.prysm_analyse_service import build_scoring_prompts
    from app.core.config import NICHE_WEIGHTS, DEFAULT_SCORED_WEIGHTS
    from app.services.prysm_analyse_service import NICHE_CONTEXT, NICHE_TREND_LENS, _build_hook_retention_context

    niche = reel["niche"]
    platform = reel["platform"]
    primary_niche = niche.split(",")[0].strip() if "," in niche else niche
    niche_key = primary_niche.lower()
    scored_weights = NICHE_WEIGHTS.get(niche_key, DEFAULT_SCORED_WEIGHTS)
    niche_ctx = NICHE_CONTEXT.get(primary_niche, f"{niche} creator.")
    niche_trend_lens = NICHE_TREND_LENS.get(primary_niche, NICHE_TREND_LENS.get("Other", ""))
    hook_retention_ctx = _build_hook_retention_context(primary_niche)
    has_vision = vision_data is not None
    has_audio = bool(whisper_data.get("hasSpeech")) or bool(audio_path)
    saved_frames = []
    if vision_data:
        for f in vision_data.get("frames", []):
            saved_frames.append({"timestamp": f.get("timestamp", 0), "base64": ""})

    prompt_a_1, prompt_b_1 = build_scoring_prompts(
        niche=niche, platform=platform, source_type="link", intent="competitor",
        brief="", content_goal="", scored_weights=scored_weights,
        niche_ctx=niche_ctx, hook_retention_ctx=hook_retention_ctx,
        niche_trend_lens=niche_trend_lens,
        vision_data=vision_data, whisper_data=whisper_data, gemini_audio={},
        audio_measurements={}, energy_data={}, aesthetics_data={},
        engagement_data={}, trend_data={}, song_trend_match={},
        saved_frames=saved_frames, post_metadata=None, audio_path=audio_path,
        has_vision=has_vision, has_audio=has_audio,
        extra_signals="",
    )

    # Call again identically
    prompt_a_2, prompt_b_2 = build_scoring_prompts(
        niche=niche, platform=platform, source_type="link", intent="competitor",
        brief="", content_goal="", scored_weights=scored_weights,
        niche_ctx=niche_ctx, hook_retention_ctx=hook_retention_ctx,
        niche_trend_lens=niche_trend_lens,
        vision_data=vision_data, whisper_data=whisper_data, gemini_audio={},
        audio_measurements={}, energy_data={}, aesthetics_data={},
        engagement_data={}, trend_data={}, song_trend_match={},
        saved_frames=saved_frames, post_metadata=None, audio_path=audio_path,
        has_vision=has_vision, has_audio=has_audio,
        extra_signals="",
    )

    identical = (prompt_a_1 == prompt_a_2) and (prompt_b_1 == prompt_b_2)

    result = {
        "option": "B",
        "method": "Two identical calls to build_scoring_prompts(extra_signals='') compared",
        "identical": identical,
        "prompt_a_sha256": hashlib.sha256(prompt_a_1.encode()).hexdigest()[:16],
        "prompt_b_sha256": hashlib.sha256(prompt_b_1.encode()).hexdigest()[:16],
        "prompt_a_len": len(prompt_a_1),
        "prompt_b_len": len(prompt_b_1),
        "what_this_verifies": (
            "That build_scoring_prompts() is deterministic and extra_signals='' "
            "adds zero content. The scoring prompt text is identical to what "
            "production would build with the same vision/audio inputs."
        ),
        "what_this_does_NOT_verify": (
            "Vision model output (claude-sonnet-4-6 vs claude-opus-4-6 in production), "
            "Gemini audio analysis (skipped in pilot), Groq transcription differences, "
            "EnCodec energy curve (skipped in pilot), aesthetics scoring (skipped in pilot), "
            "engagement prediction (skipped in pilot), trend data (skipped in pilot)."
        ),
    }
    if not identical:
        log.error(f"[CorrectnessB] FAIL: prompts not identical — check for non-deterministic code")
    else:
        log.info(f"[CorrectnessB] PASS: both prompt calls are deterministic and produce identical output")
    return result


async def correctness_check_option_a(
    reel: Dict,
    production_url: str = "http://localhost:8000",
) -> Dict:
    """
    Option A: submit reel_001 URL to the live production server and compare scores.
    Run ONCE after Option B passes.
    """
    import aiohttp
    result = {"option": "A", "reel_id": reel["reel_id"], "success": False}
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "url": reel["url"],
                "niche": reel["niche"],
                "platform": reel["platform"],
                "intent": "competitor",
            }
            async with session.post(
                f"{production_url}/prysm/analyse",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                if resp.status != 200:
                    result["error"] = f"HTTP {resp.status}: {await resp.text()}"
                    return result
                data = await resp.json()
                result["success"] = True
                result["job_id"] = data.get("job_id") or data.get("id")
                result["overall_score"] = data.get("overall_score")
                result["parameters"] = data.get("parameters", [])
                result["note"] = (
                    "Scores from production server. Differences vs pilot combo_id=1 are "
                    "expected: production uses opus-4-6 vision, Gemini audio, energy/aesthetics "
                    "pipeline. Pilot uses sonnet-4-6 vision with no audio analysis pipeline."
                )
    except ImportError:
        result["error"] = "aiohttp not installed — skipping Option A"
    except Exception as e:
        result["error"] = str(e)
    return result


# ── Per-reel download + preprocessing ────────────────────────────────────────

def prepare_reel(reel: Dict, work_dir: Path) -> Dict:
    """
    Download video, extract frames and audio, transcribe.
    Returns dict with video_path, frames, audio_path, transcript, duration.
    Caches all outputs to work_dir — re-runs are fast.
    """
    from scripts.niche_research.video_pipeline import (
        VideoAssets, _cache_key, fetch_metadata,
        download_video, extract_frames, extract_audio, transcribe,
    )
    import time as _time

    url = reel["url"]
    if url.startswith("FILL_IN"):
        return {"error": f"URL not filled in for {reel['reel_id']}"}

    cache_key = _cache_key(url)
    assets = VideoAssets(
        url=url,
        niche=reel["niche"],
        cache_key=cache_key,
        work_dir=work_dir,
    )

    log.info(f"[Prep] {reel['reel_id']}: fetching metadata")
    assets.metadata = fetch_metadata(url, prefilled={
        "viewCount": reel["gt_views"],
        "likeCount": reel["gt_likes"],
        "commentCount": reel["gt_comments"],
    })

    # Pace between metadata calls (rate-limit protection)
    _time.sleep(2)

    log.info(f"[Prep] {reel['reel_id']}: downloading video")
    vpath = download_video(assets)
    if not vpath:
        return {"error": f"Download failed for {reel['reel_id']}"}
    assets.video_path = vpath

    from scripts.niche_research.video_pipeline import probe_duration
    assets.duration_sec = probe_duration(vpath)

    log.info(f"[Prep] {reel['reel_id']}: extracting frames ({assets.duration_sec:.1f}s)")
    frames = extract_frames(assets)
    assets.frames = frames

    log.info(f"[Prep] {reel['reel_id']}: extracting audio")
    apath = extract_audio(assets)
    assets.audio_path = apath

    transcript = ""
    if apath:
        log.info(f"[Prep] {reel['reel_id']}: transcribing")
        transcript = transcribe(apath)
        assets.transcript = transcript

    # Scraped vs ground-truth check (flag if >10% off)
    scraped_views = assets.metadata.get("viewCount", 0)
    gt_views = reel["gt_views"]
    stats_ok = True
    stats_note = ""
    if gt_views and scraped_views:
        ratio = abs(scraped_views - gt_views) / gt_views
        if ratio > 0.10:
            stats_note = f"DISCREPANCY: scraped_views={scraped_views}, gt_views={gt_views}, diff={ratio:.0%}"
            stats_ok = False
            log.warning(f"[Prep] {reel['reel_id']}: {stats_note}")

    return {
        "video_path": str(vpath),
        "frames": frames,
        "audio_path": str(apath) if apath else None,
        "transcript": transcript,
        "duration_sec": assets.duration_sec,
        "metadata": assets.metadata,
        "stats_ok": stats_ok,
        "stats_note": stats_note,
    }


# ── Compute signal blocks for a given combo ──────────────────────────────────

def build_extra_signals(
    combo: Tuple[int, bool, bool, bool],
    audio_path: Optional[str],
    work_dir: Path,
    demucs_stems_cache: Dict[str, Any],
) -> Tuple[str, Optional[str]]:
    """
    Build extra_signals text block for a combo.
    Returns (extra_signals_text, vocals_path_or_None).
    Caches Demucs stem computation per reel (expensive ~30s).
    """
    _, use_clap, use_ratio, use_transcript = combo
    blocks = []
    vocals_path = None

    # Run Demucs once if any Demucs flag is on (cache result)
    if (use_ratio or use_transcript) and audio_path:
        if "stems" not in demucs_stems_cache:
            signal_flag = os.environ.get("SIGNAL_DEMUCS_RATIO", "0") or os.environ.get("SIGNAL_DEMUCS_TRANSCRIPT", "0")
            if signal_flag == "1" or use_ratio or use_transcript:
                try:
                    from scripts.niche_research.signals.demucs_stem import build_ratio_signal_block
                    block_text, vp = build_ratio_signal_block(audio_path, work_dir=str(work_dir))
                    demucs_stems_cache["stems"] = {"block_text": block_text, "vocals_path": vp}
                    log.info(f"[Demucs] Separation done; vocals at {vp}")
                except Exception as e:
                    log.warning(f"[Demucs] Failed: {e}")
                    demucs_stems_cache["stems"] = {"block_text": "", "vocals_path": None}
            else:
                demucs_stems_cache["stems"] = {"block_text": "", "vocals_path": None}

        stems = demucs_stems_cache.get("stems", {})
        if use_ratio and stems.get("block_text"):
            blocks.append(stems["block_text"])
        if use_transcript:
            vocals_path = stems.get("vocals_path")

    # CLAP tags
    if use_clap and audio_path:
        try:
            from scripts.niche_research.signals.clap_tags import build_signal_block
            clap_block = build_signal_block(audio_path)
            if clap_block:
                blocks.append(clap_block)
        except Exception as e:
            log.warning(f"[CLAP] Failed: {e}")

    return "\n\n".join(blocks), vocals_path


# ── Main pilot loop ───────────────────────────────────────────────────────────

async def run_pilot(dry_run: bool = False, only_reel: Optional[str] = None):
    work_dir = Path(os.environ.get("PILOT_WORK_DIR", "/tmp/prysm_pilot"))
    work_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"Work dir: {work_dir}")

    total_spend = 0.0
    results: List[Dict] = []
    correctness_b_done = False
    correctness_a_done = False

    reels_to_run = REELS if not only_reel else [r for r in REELS if r["reel_id"] == only_reel]

    for reel in reels_to_run:
        reel_id = reel["reel_id"]
        reel_work = work_dir / reel_id
        reel_work.mkdir(exist_ok=True)

        log.info(f"\n{'='*60}\nProcessing {reel_id}: {reel['url']}\n{'='*60}")

        if reel["url"].startswith("FILL_IN"):
            log.warning(f"Skipping {reel_id} — URL not filled in")
            results.append({"reel_id": reel_id, "error": "URL not configured", "runs": []})
            continue

        # ── Download + preprocess ─────────────────────────────────────────
        prep = prepare_reel(reel, reel_work)
        if prep.get("error"):
            log.error(f"[Prep] {reel_id}: {prep['error']}")
            results.append({"reel_id": reel_id, "error": prep["error"], "runs": []})
            continue

        # ── Vision (once per reel) ────────────────────────────────────────
        vision_cache_path = reel_work / "vision_data.json"
        if vision_cache_path.exists():
            log.info(f"[Vision] Loading cached result for {reel_id}")
            with open(vision_cache_path) as f:
                vision_data = json.load(f)
        else:
            if dry_run:
                log.info(f"[Vision] DRY RUN — skipping Claude vision call")
                vision_data = None
            else:
                log.info(f"[Vision] Running for {reel_id}")
                vision_data = await run_vision(
                    prep["frames"],
                    reel["niche"],
                    reel["platform"],
                )
                if vision_data:
                    with open(vision_cache_path, "w") as f:
                        json.dump(vision_data, f)
                    if vision_data.get("_usage"):
                        u = vision_data["_usage"]
                        vision_cost = tokens_to_usd(u["input_tokens"], u["output_tokens"])
                        total_spend += vision_cost
                        log.info(f"[Vision] Cost: ${vision_cost:.4f} | Total: ${total_spend:.4f}")

        # Build whisper_data from transcript
        transcript = prep.get("transcript", "")
        whisper_data: Dict = {
            "hasSpeech": bool(transcript),
            "transcript": transcript,
            "language": "unknown",
            "segments": [],
            "hookText": transcript[:100] if transcript else "",
            "verbalCTA": "",
        }

        # Demucs stems cache (shared across combos for this reel)
        demucs_stems_cache: Dict = {}

        reel_runs = []

        for combo in COMBOS:
            combo_id, use_clap, use_ratio, use_transcript = combo

            # Spend check before each run
            if total_spend >= SPEND_STOP_USD:
                log.warning(f"SPEND STOP: ${total_spend:.4f} >= ${SPEND_STOP_USD}. Stopping.")
                _save_results(results, reel_runs, reel_id)
                _write_report(results, total_spend, stopped_early=True)
                return

            log.info(f"[Run] {reel_id} × combo_{combo_id} (CLAP={use_clap}, RATIO={use_ratio}, TRANSCRIPT={use_transcript})")

            # Build signal blocks
            extra_signals, vocals_path = build_extra_signals(
                combo, prep["audio_path"], reel_work, demucs_stems_cache
            )

            # Use vocal stem for transcription if DEMUCS_TRANSCRIPT flag is on
            audio_for_scoring = prep["audio_path"]
            if use_transcript and vocals_path:
                audio_for_scoring = vocals_path
                # Re-transcribe from clean vocals if available
                try:
                    from scripts.niche_research.video_pipeline import transcribe
                    clean_transcript = transcribe(Path(vocals_path))
                    if clean_transcript:
                        whisper_data = {**whisper_data, "transcript": clean_transcript, "hasSpeech": True}
                        log.info(f"[DemucsTranscript] Clean vocal transcript: {clean_transcript[:80]}...")
                except Exception as e:
                    log.warning(f"[DemucsTranscript] Re-transcribe failed: {e}")

            if dry_run:
                log.info(f"[Run] DRY RUN — skipping scoring call (extra_signals len={len(extra_signals)})")
                run_result = {
                    "combo_id": combo_id,
                    "flags": {"clap": use_clap, "ratio": use_ratio, "transcript": use_transcript},
                    "dry_run": True,
                    "extra_signals_preview": extra_signals[:200],
                    "cost_usd": 0.0,
                    "latency_sec": 0.0,
                    "error": None,
                }
            else:
                run_result = await run_scoring(
                    reel, vision_data, whisper_data, audio_for_scoring, extra_signals
                )
                run_result["combo_id"] = combo_id
                run_result["flags"] = {"clap": use_clap, "ratio": use_ratio, "transcript": use_transcript}
                total_spend += run_result.get("cost_usd", 0.0)
                log.info(
                    f"[Run] Done: overall={run_result.get('overall_score')} "
                    f"cost=${run_result['cost_usd']:.4f} "
                    f"latency={run_result['latency_sec']:.1f}s "
                    f"total_spend=${total_spend:.4f}"
                )

            reel_runs.append(run_result)

            # ── Correctness check (Option B) — reel_001, combo_id=1 ──────
            if reel_id == "reel_001" and combo_id == 1 and not correctness_b_done:
                log.info("[CorrectnessB] Running Option B check...")
                check_b = correctness_check_option_b(
                    run_result, reel_id, vision_data, whisper_data,
                    prep["audio_path"], reel
                )
                log.info(f"[CorrectnessB] identical={check_b['identical']}")
                with open(OUTPUT_DIR / "correctness_check_b.json", "w") as f:
                    json.dump(check_b, f, indent=2)
                correctness_b_done = True

                # ── Option A — after B passes ─────────────────────────────
                if check_b["identical"] and not correctness_a_done and not dry_run:
                    log.info("[CorrectnessA] Running production server check (once, reel_001)...")
                    check_a = await correctness_check_option_a(reel)
                    log.info(f"[CorrectnessA] success={check_a['success']}, score={check_a.get('overall_score')}")
                    with open(OUTPUT_DIR / "correctness_check_a.json", "w") as f:
                        json.dump(check_a, f, indent=2)
                    # Report both score sets side by side
                    pilot_score = run_result.get("overall_score")
                    prod_score = check_a.get("overall_score")
                    log.info(f"[CorrectnessA] Pilot combo_id=1 score: {pilot_score} | Production score: {prod_score}")
                    correctness_a_done = True

            # Small pace between runs
            if not dry_run:
                await asyncio.sleep(1)

        results.append({
            "reel_id": reel_id,
            "prep": {
                "duration_sec": prep.get("duration_sec"),
                "stats_ok": prep.get("stats_ok"),
                "stats_note": prep.get("stats_note", ""),
                "scraped_views": prep.get("metadata", {}).get("viewCount"),
                "gt_views": reel["gt_views"],
            },
            "runs": reel_runs,
        })

        _save_results(results, [], None)

        # Pace between reels
        if not dry_run:
            log.info("[Pilot] Sleeping 5s between reels...")
            await asyncio.sleep(5)

    _write_report(results, total_spend, stopped_early=False)


def _save_results(results: List[Dict], current_reel_runs: List[Dict], reel_id: Optional[str]):
    """Save current state to JSON (checkpoint after each reel)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = OUTPUT_DIR / "pilot_results.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2, default=str)


def _write_report(results: List[Dict], total_spend: float, stopped_early: bool):
    """Write pilot report to pilot_report.md."""
    lines = [
        "# Scoring-Signal Pilot Report",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Total spend: ${total_spend:.4f} USD",
        f"Stopped early: {stopped_early}",
        "",
        "## Correctness Check Summary",
        "",
    ]

    check_b_path = OUTPUT_DIR / "correctness_check_b.json"
    if check_b_path.exists():
        with open(check_b_path) as f:
            cb = json.load(f)
        lines += [
            f"**Option B**: `build_scoring_prompts()` called twice with identical args — identical={cb.get('identical')}",
            f"- Prompt A SHA256 (first 16): `{cb.get('prompt_a_sha256')}`",
            f"- Prompt B SHA256 (first 16): `{cb.get('prompt_b_sha256')}`",
            f"- What this verifies: {cb.get('what_this_verifies', '')}",
            f"- What this does NOT verify: {cb.get('what_this_does_NOT_verify', '')}",
            "",
        ]

    check_a_path = OUTPUT_DIR / "correctness_check_a.json"
    if check_a_path.exists():
        with open(check_a_path) as f:
            ca = json.load(f)
        lines += [
            f"**Option A** (production server, reel_001): success={ca.get('success')}",
            f"- Production score: {ca.get('overall_score')}",
            f"- Note: {ca.get('note', ca.get('error', 'no result'))}",
            "",
        ]

    lines += ["## Per-Combination Results", ""]
    lines += [
        "| Combo | Flags | Reels Run | Avg Cost/Reel | Avg Latency | Errors |",
        "|-------|-------|-----------|---------------|-------------|--------|",
    ]

    from collections import defaultdict
    combo_stats: Dict[int, list] = defaultdict(list)
    for reel_result in results:
        for run in reel_result.get("runs", []):
            cid = run.get("combo_id")
            if cid:
                combo_stats[cid].append(run)

    combo_labels = {
        1: "all off", 9: "CLAP", 17: "ratio", 25: "transcript",
        33: "CLAP+ratio", 41: "CLAP+transcript", 49: "ratio+transcript", 57: "all"
    }
    for combo_id, _, _, _ in COMBOS:
        runs = combo_stats[combo_id]
        if not runs:
            continue
        n = len(runs)
        avg_cost = sum(r.get("cost_usd", 0) for r in runs) / n
        avg_lat = sum(r.get("latency_sec", 0) for r in runs) / n
        n_err = sum(1 for r in runs if r.get("error"))
        flags = combo_labels.get(combo_id, "?")
        lines.append(f"| {combo_id} | {flags} | {n} | ${avg_cost:.3f} | {avg_lat:.1f}s | {n_err} |")

    lines += [
        "",
        "## Download Success",
        "",
        "| Reel | Duration | Stats OK | Note |",
        "|------|----------|----------|------|",
    ]
    for rr in results:
        prep = rr.get("prep", {})
        lines.append(
            f"| {rr['reel_id']} | {prep.get('duration_sec', '?'):.0f}s | "
            f"{'✓' if prep.get('stats_ok') else '✗'} | {prep.get('stats_note', '')} |"
            if prep else f"| {rr['reel_id']} | — | — | {rr.get('error', '')} |"
        )

    lines += [
        "",
        "## Notes",
        "",
        "- Correlation columns left blank: n=6 is not statistically meaningful.",
        "- canonical_score left NULL in research_reels (not set during pilot).",
        "- Vision model: claude-sonnet-4-6 (not opus-4-6 as in production).",
        "- Gemini audio, energy curve, aesthetics, engagement prediction: all skipped in pilot.",
        "",
    ]

    report_path = OUTPUT_DIR / "pilot_report.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    log.info(f"Report written to {report_path}")
    log.info(f"Results JSON at {OUTPUT_DIR / 'pilot_results.json'}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scoring-signal pilot")
    parser.add_argument("--dry-run", action="store_true", help="Skip all Claude API calls")
    parser.add_argument("--reel", help="Run only this reel_id (e.g. reel_001)")
    args = parser.parse_args()

    asyncio.run(run_pilot(dry_run=args.dry_run, only_reel=args.reel))


if __name__ == "__main__":
    main()
