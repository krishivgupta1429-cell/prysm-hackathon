"""
app/services/engagement_service.py
Predicted engagement metrics via LMM-EVQA.

LMM-EVQA (Apache 2.0) is the winning solution from VQualA 2025 challenge.
It predicts two real metrics trained on 120,651 actual Snapchat short-form videos:

  NAWP — Normalized Average Watch Percentage (0–100): predicted % of video viewers watch
  ECR  — Engagement Continuation Rate (0–100): predicted % who watch past 5 seconds

Architecture: ensemble of VideoLLaMA2 + Qwen2.5-VL, requires 48GB VRAM.
Deployment: RunPod serverless endpoint (called via HTTP).

Fallback: if RunPod endpoint not configured, computes a heuristic score from
the signals we already have (vision score, audio energy, aesthetic score).

Hackathon change (see HACKATHON.md): ENGAGEMENT_BACKEND = modal | runpod | heuristic picks the backend
(app/studio/engagement_modal.py runs Qwen2.5-VL zero-shot and Q-Align on Modal). Every result now carries
"available"; a failure returns available=False with a reason and NEVER invented numbers.
"""

import asyncio
import base64
import json
import logging
import os
from typing import Dict, List, Any, Optional

import httpx

logger = logging.getLogger(__name__)


async def predict_engagement(
    frames: List[Dict],
    audio_path: Optional[str],
    vision_data: Optional[Dict],
    energy_data: Optional[Dict],
    aesthetics_data: Optional[Dict],
    niche: str,
    duration: float = 30.0,
) -> Dict[str, Any]:
    """
    Predict NAWP and ECR for a video.

    Tries RunPod endpoint first. Falls back to heuristic if not configured.

    Returns:
        {
            "nawp": 38.5,           # predicted avg watch percentage
            "ecr": 64.2,            # predicted 5-second hold rate
            "hook_risk": "high",    # low/medium/high based on ECR
            "retention_risk": "medium",
            "source": "runpod" | "heuristic",
            "interpretation": "human-readable summary for Claude",
        }
    """
    from app.core.config import settings
    from app.studio import engagement_modal

    choice = engagement_modal.backend_choice()
    if choice == "modal":
        return await _predict_modal(frames, niche, duration)
    if choice == "heuristic":
        return _heuristic_engagement(vision_data, energy_data, aesthetics_data, niche)

    runpod_endpoint = settings.RUNPOD_ENGAGEMENT_ENDPOINT
    runpod_key = settings.RUNPOD_API_KEY

    logger.info(f"[Engagement] endpoint={'SET' if runpod_endpoint else 'MISSING'} key={'SET' if runpod_key else 'MISSING'}")

    if runpod_endpoint and runpod_key:
        result = await _call_runpod(
            runpod_endpoint, runpod_key,
            frames, audio_path, niche, duration
        )
        if result:
            return result

    # Heuristic fallback — derives NAWP/ECR from signals already computed
    return _heuristic_engagement(vision_data, energy_data, aesthetics_data, niche)


def _unavailable(source: str, reason: str) -> Dict[str, Any]:
    """No prediction. Carries the reason; has no numbers at all (the old worker's 40/55 fallback is not reproduced)."""
    return {"available": False, "reason": reason, "source": source, "nawp": None, "ecr": None,
            "hook_risk": None, "retention_risk": None, "qalign": None, "interpretation": ""}


async def _predict_modal(frames: List[Dict], niche: str, duration: float) -> Dict[str, Any]:
    from app.studio import engagement_modal
    try:
        res = await asyncio.to_thread(engagement_modal.predict, frames, niche, duration)
    except engagement_modal.EngagementUnavailable as e:
        logger.warning(f"[Engagement] unavailable: {e}")
        return _unavailable("qwen_zero_shot", str(e))
    except Exception as e:
        logger.warning(f"[Engagement] Modal call failed: {type(e).__name__}")
        return _unavailable("qwen_zero_shot", f"the prediction service could not be reached ({type(e).__name__})")
    out = _build_result(res["nawp"], res["ecr"], source=res["source"])
    out["qalign"] = res.get("qalign")
    out["_modal_cost_usd"] = res["call"]["est_cost_usd"]  # popped by the caller for the cost breakdown
    return out


async def _call_runpod(
    endpoint: str,
    api_key: str,
    frames: List[Dict],
    audio_path: Optional[str],
    niche: str,
    duration: float,
) -> Optional[Dict[str, Any]]:
    """Call RunPod serverless endpoint running LMM-EVQA."""
    try:
        # Encode up to 8 hook + key frames as base64
        encoded_frames = []
        for frame in frames[:8]:
            path = frame.get("path", "")
            if path and os.path.exists(path):
                with open(path, "rb") as f:
                    encoded_frames.append({
                        "timestamp": frame["timestamp"],
                        "image_b64": base64.b64encode(f.read()).decode(),
                    })
            elif frame.get("base64"):
                # Frame already has base64 data (saved_frames format)
                encoded_frames.append({
                    "timestamp": frame["timestamp"],
                    "image_b64": frame["base64"],
                })

        logger.info(f"[Engagement] RunPod call: {len(encoded_frames)} frames encoded, niche={niche}, duration={duration}")

        if not encoded_frames:
            raise ValueError(f"No frames could be encoded — frame keys: {list(frames[0].keys()) if frames else 'no frames'}")

        payload = {
            "input": {
                "frames": encoded_frames,
                "niche": niche,
                "duration": duration,
                "task": "engagement_prediction",
            }
        }

        # Use /run (async) + poll — more reliable than /runsync for long jobs
        run_url = endpoint.replace("/runsync", "/run")
        base_url = run_url.rsplit("/run", 1)[0]

        logger.info(f"[Engagement] Submitting job to {run_url}")
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                run_url,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            job = resp.json()
            job_id = job.get("id")
            if not job_id:
                raise ValueError(f"No job ID returned: {job}")

            logger.info(f"[Engagement] Job submitted: {job_id}, polling...")

            # Poll for completion (max 5 minutes)
            status_url = f"{base_url}/status/{job_id}"
            for poll_n in range(60):  # 60 × 5s = 300s
                await asyncio.sleep(5)
                status_resp = await client.get(
                    status_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                status_data = status_resp.json()
                status = status_data.get("status", "")
                if poll_n % 6 == 0:  # log every 30s
                    logger.info(f"[Engagement] Job {job_id} status={status} (poll {poll_n})")
                if status == "COMPLETED":
                    output = status_data.get("output", {})
                    nawp = float(output.get("nawp", 0))
                    ecr = float(output.get("ecr", 0))
                    execution_time_ms = status_data.get("executionTime", 0)
                    delay_time_ms = status_data.get("delayTime", 0)
                    total_billed_ms = execution_time_ms + delay_time_ms
                    logger.info(f"[Engagement] RunPod completed: nawp={nawp}, ecr={ecr}, exec={execution_time_ms}ms, delay={delay_time_ms}ms, billed={total_billed_ms}ms")
                    if nawp > 0 and ecr > 0:
                        result = _build_result(nawp, ecr, source="runpod")
                        result["_runpod_execution_ms"] = total_billed_ms
                        return result
                    raise ValueError(f"Invalid output: {output}")
                elif status in ("FAILED", "CANCELLED", "TIMED_OUT"):
                    raise ValueError(f"Job {status}: {status_data.get('error', '')}")
                # IN_QUEUE or IN_PROGRESS — keep polling

            raise TimeoutError("RunPod job did not complete within 5 minutes")

    except Exception as e:
        logger.warning(f"[Engagement] RunPod call failed: {e} — using heuristic")
        return None


def _heuristic_engagement(
    vision_data: Optional[Dict],
    energy_data: Optional[Dict],
    aesthetics_data: Optional[Dict],
    niche: str,
) -> Dict[str, Any]:
    """
    Derive predicted engagement from existing signals.

    This is not a trained model — it's a weighted heuristic that approximates
    what LMM-EVQA would output, based on the same underlying signals.
    Labelled as "heuristic" so Claude knows the confidence level.
    """
    score_components = []

    # Vision: overall visual quality score (0–100 → 0–1)
    if vision_data:
        vis_score = vision_data.get("overallVisualScore", 60) / 100.0
        score_components.append(("visual", vis_score, 0.35))

    # Audio energy: hook energy is the strongest ECR predictor
    if energy_data:
        hook_e = energy_data.get("hook_energy", 0.5)
        drop = 0.2 if energy_data.get("drop_detected") else 0.0
        audio_score = max(0.0, hook_e - drop)
        score_components.append(("audio_hook", audio_score, 0.35))

    # Aesthetics: avg frame quality
    if aesthetics_data:
        aes_score = (aesthetics_data.get("avg_score", 5.0) - 1.0) / 9.0
        score_components.append(("aesthetics", aes_score, 0.30))

    if not score_components:
        # Nothing to go on — no invented neutral numbers (hackathon change; the baseline returned 45 / 55)
        return _unavailable("heuristic", "no signals were available to estimate from")

    total_weight = sum(w for _, _, w in score_components)
    composite = sum(v * w for _, v, w in score_components) / total_weight

    # Map composite (0–1) to NAWP range (15–75) and ECR range (30–85)
    # These ranges are derived from SnapUGC dataset statistics
    nawp = round(15.0 + composite * 60.0, 1)
    ecr = round(30.0 + composite * 55.0, 1)

    return _build_result(nawp, ecr, source="heuristic")


def _build_result(nawp: float, ecr: float, source: str) -> Dict[str, Any]:
    """Build standardised engagement result dict."""
    # Hook risk based on ECR
    if ecr >= 70:
        hook_risk = "low"
    elif ecr >= 50:
        hook_risk = "medium"
    else:
        hook_risk = "high"

    # Retention risk based on NAWP
    if nawp >= 55:
        retention_risk = "low"
    elif nawp >= 35:
        retention_risk = "medium"
    else:
        retention_risk = "high"

    lines = []
    if ecr < 50:
        lines.append(f"Hook signal is WEAK — predicted {ecr:.0f}% of viewers watch past 5 seconds.")
    elif ecr < 70:
        lines.append(f"Hook signal is MODERATE — predicted {ecr:.0f}% of viewers watch past 5 seconds.")
    else:
        lines.append(f"Hook signal is STRONG — predicted {ecr:.0f}% of viewers watch past 5 seconds.")

    if nawp < 35:
        lines.append(f"Retention is WEAK — predicted average watch percentage is {nawp:.0f}%.")
    elif nawp < 55:
        lines.append(f"Retention is MODERATE — predicted average watch percentage is {nawp:.0f}%.")
    else:
        lines.append(f"Retention is STRONG — predicted average watch percentage is {nawp:.0f}%.")

    return {
        "available": True,
        "qalign": None,
        "nawp": nawp,
        "ecr": ecr,
        "hook_risk": hook_risk,
        "retention_risk": retention_risk,
        "source": source,
        "interpretation": " ".join(lines),
    }


def build_engagement_context(engagement: Dict[str, Any]) -> str:
    """Format engagement signals into a Claude-readable context string."""
    if not engagement or engagement.get("available") is False:
        return ""

    nawp = engagement.get("nawp", 0)
    ecr = engagement.get("ecr", 0)
    hook_risk = engagement.get("hook_risk", "medium")
    retention_risk = engagement.get("retention_risk", "medium")
    source = engagement.get("source", "heuristic")

    if source == "runpod":
        label = "Qwen2.5-VL-7B model (AI prediction)"
        confidence_note = "These are AI-predicted estimates based on visual frame analysis — not measured viewer data, but significantly more reliable than heuristics."
        pct_instruction = "You MAY reference these percentages as AI-predicted estimates (e.g. 'the model predicts ~{ecr:.0f}% hook hold rate'). Always clarify they are predictions, not measured stats."
    elif source == "evqa":
        label = "LMM-EVQA (trained on short-form watch data)"
        confidence_note = "These are model-predicted estimates, not measured viewer data."
        pct_instruction = "You MAY reference these percentages as AI-predicted estimates. Always clarify they are predictions, not measured stats."
    elif source == "qwen_zero_shot":
        label = "a general vision-language model's rough guess from the frames (not trained on watch data)"
        confidence_note = "This is a rough guess from frame content, not measured and not trained on watch data — wide error margin, not reliable enough to quote as specific numbers."
        pct_instruction = "Do NOT quote the specific percentages — use only the risk level (low/medium/high) to inform your advice, as a soft hint."
    else:
        label = "heuristic signal analysis"
        confidence_note = "These are rough directional estimates from audio/visual signals — wide error margin, not reliable enough to quote as specific numbers."
        pct_instruction = "Do NOT quote the specific percentages — use only the risk level (low/medium/high) to inform your advice."

    ctx = f"\nENGAGEMENT PREDICTION (from {label}):"
    ctx += f"\n- Predicted hook hold rate (ECR): {ecr:.0f}% — {hook_risk.upper()} risk of early scroll-off"
    ctx += f"\n- Predicted avg watch percentage (NAWP): {nawp:.0f}% — {retention_risk.upper()} risk of mid-video drop-off"
    ctx += f"\n- Assessment: {engagement.get('interpretation', '')}"
    ctx += f"\n- Confidence: {confidence_note}"

    if hook_risk == "high":
        ctx += "\n- The first 3 seconds are the single most critical fix for this video."
    if retention_risk == "high":
        ctx += "\n- Pacing and mid-section energy need work to hold viewers through."

    ctx += f"\nINSTRUCTION: {pct_instruction.format(ecr=ecr, nawp=nawp)}"

    # Q-Align visual quality: evidence for scoring only behind a flag (default off). Never a watch percentage.
    qa = engagement.get("qalign")
    if qa:
        from app.studio import engagement_modal
        if engagement_modal.qalign_for_scoring_enabled():
            ctx += (f"\nVISUAL QUALITY SIGNAL (Q-Align, picture quality only, not viewer behaviour): "
                    f"quality {qa['quality']:.1f}/5 ({qa['band']}), aesthetics {qa['aesthetic']:.1f}/5.")

    return ctx
