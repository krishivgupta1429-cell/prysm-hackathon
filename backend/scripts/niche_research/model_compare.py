"""
model_compare.py — Claude Sonnet 4.6 vs Qwen2.5-VL-32B scoring comparison

20 reels, identical merged vision+scoring prompt, both models.
Results saved to results/model_compare/<code>_{model}.json
Report written to results/model_compare/report.md

Usage:
    # Run both models (needs QWEN32B_ENDPOINT env var set)
    python -m scripts.niche_research.model_compare

    # Run only Sonnet (Qwen endpoint not yet deployed)
    python -m scripts.niche_research.model_compare --model sonnet

    # Run only Qwen (Sonnet already done)
    python -m scripts.niche_research.model_compare --model qwen

    # Just build report from existing raw outputs
    python -m scripts.niche_research.model_compare --report-only
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Reels under test ─────────────────────────────────────────────────────────

REELS: List[Dict[str, str]] = [
    # Beauty (7)
    {"code": "DblADp8SKGI", "niche": "beauty", "creator": "skincareinvogue"},
    {"code": "DAlOO7iSgmz", "niche": "beauty", "creator": "skincareinvogue"},
    {"code": "DbqX8rzJhB9", "niche": "beauty", "creator": "meghnapahwa"},
    {"code": "Da0FKEOyJtE", "niche": "beauty", "creator": "meghnapahwa"},
    {"code": "DblNWIXNjdJ", "niche": "beauty", "creator": "aashiadani"},
    {"code": "DK10ITsBxsE", "niche": "beauty", "creator": "aashiadani"},
    {"code": "DJLn27ovUVH", "niche": "beauty", "creator": "chaoswithkhush"},
    # Fitness (7)
    {"code": "C97CAxPSypW", "niche": "fitness", "creator": "shameekhh"},
    {"code": "DMXXzCjsi04", "niche": "fitness", "creator": "shameekhh"},
    {"code": "DbnEAuTB1Nw", "niche": "fitness", "creator": "justchand_"},
    {"code": "DbszCTeh6DK", "niche": "fitness", "creator": "justchand_"},
    {"code": "DW8npdiDbvW", "niche": "fitness", "creator": "arrlifts"},
    {"code": "DWyjEBgDSwh", "niche": "fitness", "creator": "arrlifts"},
    {"code": "DblLZvLTF0J", "niche": "fitness", "creator": "sushant_rajput.27"},
    # Business (6)
    {"code": "DFP1WMOS0C8", "niche": "business", "creator": "hariramrinwaofficial"},
    {"code": "DY9pPjdIIOd", "niche": "business", "creator": "hariramrinwaofficial"},
    {"code": "DbBcipeq90J", "niche": "business", "creator": "siddheshs"},
    {"code": "Da2pazZq81P", "niche": "business", "creator": "siddheshs"},
    {"code": "DaVFiCaKvvg", "niche": "business", "creator": "harsh.solankii"},
    {"code": "DXReorNjcRG", "niche": "business", "creator": "harsh.solankii"},
]

# ── Scored parameters ─────────────────────────────────────────────────────────

SCORED_PARAMS = [
    "Hook & Opening",
    "Retention & Pacing",
    "Shareability",
    "Audio & Music",
    "Trend Alignment",
    "Visual Quality & Originality",
]
ALL_PARAMS = SCORED_PARAMS + [
    "On-Screen Text",
    "Save Potential",
    "Replay Value",
    "Call to Action",
]

# ── Paths ─────────────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results" / "model_compare"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("model_compare")


# ── Shared rubric prompt ──────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are an Instagram Reels expert analyst. You will see frames from a reel "
    "in chronological order. You must both observe what you see AND score the video "
    "against ten well-defined parameters.\n\n"
    "Scoring calibration: 50 = average for the niche, 80+ = exceptional, sub-40 = poor. "
    "Use the full 0-100 range honestly — most videos are NOT 70+. "
    "Your evidence sentence must reference something concrete you actually see or hear."
)

_PARAM_SCHEMA = "\n".join(
    f'    {{"name": "{p}", "score": <0-100>, "evidence": "one concrete sentence"}}'
    for p in ALL_PARAMS
)

_OUTPUT_SCHEMA = (
    '{\n'
    '  "vision": {\n'
    '    "narrative": "1-2 sentences: what happens across the frames",\n'
    '    "hookType": "curiosity|shock|face-first|context-first|question|statement|reveal",\n'
    '    "hookMoment": <seconds as float>,\n'
    '    "facePresent": true/false,\n'
    '    "textOverlayPresent": true/false,\n'
    '    "primaryEmotion": "string"\n'
    '  },\n'
    '  "parameters": [\n'
    + _PARAM_SCHEMA + '\n'
    '  ],\n'
    '  "dominantPatterns": ["tag1", "tag2"],\n'
    '  "weakSpots": ["tag1", "tag2"]\n'
    '}'
)


def build_user_prompt(niche: str, duration: float, meta: Dict, transcript: str) -> str:
    views = meta.get("viewCount", "unknown")
    likes = meta.get("likeCount", "unknown")
    comments = meta.get("commentCount", "unknown")
    caption = (meta.get("caption") or "")[:300]
    hashtags = ", ".join(meta.get("hashtags") or [])
    transcript_text = (transcript or "").strip()[:1500] or "(no transcript)"

    lines = [
        f"NICHE: {niche}",
        f"DURATION: {duration:.1f}s",
        f"VIEWS: {views} | LIKES: {likes} | COMMENTS: {comments}",
        f"CAPTION: {caption}",
        f"HASHTAGS: {hashtags}",
        "",
        "AUDIO TRANSCRIPT:",
        transcript_text,
        "",
        "The frames above are labeled with timestamps in chronological order.",
        "Observe them, then score all 10 parameters.",
        "",
        "Return ONLY valid JSON, no markdown fences, no prose:",
        _OUTPUT_SCHEMA,
        "",
        "Output ALL ten parameters. Do not skip any.",
    ]
    return "\n".join(lines)


# ── Frame loading ─────────────────────────────────────────────────────────────

def load_frames_b64(frame_paths: List[Path]) -> List[Tuple[str, float]]:
    """Returns list of (base64_str, timestamp_float)."""
    result = []
    for fp in frame_paths:
        if fp.exists():
            b64 = base64.b64encode(fp.read_bytes()).decode()
            # Extract timestamp from filename frame_00.jpg → 0.0 (approx)
            result.append((b64, 0.0))
    return result


def load_frame_data(assets) -> List[Dict[str, Any]]:
    """Returns [{path, timestamp, b64}] from VideoAssets."""
    out = []
    for f in assets.frames:
        p = Path(f["path"]) if isinstance(f["path"], str) else f["path"]
        if p.exists():
            b64 = base64.b64encode(p.read_bytes()).decode()
            out.append({"path": p, "timestamp": f["timestamp"], "b64": b64})
    return out


# ── JSON parsing ──────────────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
# Matches adjacent JSON objects in an array that are missing the separating comma.
_MISSING_COMMA_RE = re.compile(r"}\s*\n(\s*{)", re.MULTILINE)


def _fix_missing_commas(s: str) -> str:
    """Insert commas between adjacent JSON objects in arrays (common LLM output quirk)."""
    return _MISSING_COMMA_RE.sub(r"},\n\1", s)


def parse_response(raw: str) -> Tuple[Optional[Dict], bool]:
    """Returns (parsed_dict_or_None, parsed_clean_on_first_try)."""
    raw = (raw or "").strip()
    if not raw:
        return None, False

    # Try direct parse
    try:
        return json.loads(raw), True
    except json.JSONDecodeError:
        pass

    # Strip markdown fences
    m = _FENCE_RE.search(raw)
    if m:
        candidate = m.group(1).strip()
        try:
            return json.loads(candidate), False
        except json.JSONDecodeError:
            try:
                return json.loads(_fix_missing_commas(candidate)), False
            except json.JSONDecodeError:
                pass

    # Extract outermost {...}
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidate = raw[start:end + 1]
        try:
            return json.loads(candidate), False
        except json.JSONDecodeError:
            try:
                return json.loads(_fix_missing_commas(candidate)), False
            except json.JSONDecodeError:
                pass

    return None, False


def extract_scores(parsed: Dict) -> Dict[str, Optional[int]]:
    """Extract per-parameter scores from parsed response."""
    if not parsed:
        return {p: None for p in ALL_PARAMS}
    params = parsed.get("parameters") or []
    indexed = {}
    for item in params:
        if isinstance(item, dict):
            name = item.get("name", "")
            score = item.get("score")
            if name and score is not None:
                try:
                    indexed[name] = int(score)
                except (TypeError, ValueError):
                    pass
    return {p: indexed.get(p) for p in ALL_PARAMS}


def extract_evidence(parsed: Dict) -> Dict[str, str]:
    if not parsed:
        return {}
    params = parsed.get("parameters") or []
    return {
        item.get("name", ""): item.get("evidence", "")
        for item in params
        if isinstance(item, dict)
    }


# ── Sonnet runner ─────────────────────────────────────────────────────────────

def run_sonnet(
    code: str, niche: str, assets, frame_data: List[Dict], anthropic_key: str
) -> Dict[str, Any]:
    import anthropic

    client = anthropic.Anthropic(api_key=anthropic_key)
    user_text = build_user_prompt(niche, assets.duration_sec, assets.metadata or {}, assets.transcript or "")

    content: List[Dict] = []
    for fd in frame_data:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": fd["b64"]},
        })
        content.append({"type": "text", "text": f"[t={fd['timestamp']:.1f}s]"})
    content.append({"type": "text", "text": user_text})

    t0 = time.time()
    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        raw = resp.content[0].text if resp.content else ""
        elapsed = round(time.time() - t0, 1)
        input_tokens = resp.usage.input_tokens
        output_tokens = resp.usage.output_tokens
        # Sonnet 4.6 pricing: $3/M input, $15/M output
        cost_usd = (input_tokens * 3 + output_tokens * 15) / 1_000_000
        parsed, clean = parse_response(raw)
        return {
            "code": code,
            "niche": niche,
            "model": "claude-sonnet-4-6",
            "raw": raw,
            "parsed": parsed,
            "clean_parse": clean,
            "scores": extract_scores(parsed),
            "evidence": extract_evidence(parsed),
            "vision": (parsed or {}).get("vision"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 6),
            "elapsed_sec": elapsed,
            "error": None,
        }
    except Exception as e:
        return {
            "code": code, "niche": niche, "model": "claude-sonnet-4-6",
            "raw": "", "parsed": None, "clean_parse": False,
            "scores": {p: None for p in ALL_PARAMS}, "evidence": {},
            "vision": None, "input_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0, "elapsed_sec": round(time.time() - t0, 1),
            "error": str(e),
        }


# ── Qwen runner (RunPod vLLM via native queue API) ────────────────────────────

def _qwen_openai_input(model: str, frame_data: List[Dict], user_text: str) -> Dict:
    """Build OpenAI-format messages for vLLM / RunPod openai_route."""
    content: List[Dict] = []
    for fd in frame_data:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{fd['b64']}"},
        })
        content.append({"type": "text", "text": f"[t={fd['timestamp']:.1f}s]"})
    content.append({"type": "text", "text": user_text})

    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "temperature": 0.2,
        "max_tokens": 2000,
    }


def run_qwen(
    code: str, niche: str, assets, frame_data: List[Dict],
    endpoint_id: str, api_key: str, model_name: str,
) -> Dict[str, Any]:
    import requests

    base = f"https://api.runpod.ai/v2/{endpoint_id}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    user_text = build_user_prompt(niche, assets.duration_sec, assets.metadata or {}, assets.transcript or "")
    openai_input = _qwen_openai_input(model_name, frame_data, user_text)
    payload = {"input": {"openai_route": "/v1/chat/completions", "openai_input": openai_input}}

    t0 = time.time()
    try:
        r = requests.post(f"{base}/runsync", headers=headers, json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
        job_id = data.get("id")
        status = data.get("status")

        # If not completed inline, poll
        if status not in ("COMPLETED", "FAILED") and job_id:
            deadline = time.time() + 600
            interval = 5
            while time.time() < deadline:
                time.sleep(interval)
                interval = min(interval * 1.5, 30)
                pr = requests.get(f"{base}/status/{job_id}", headers=headers, timeout=30)
                pr.raise_for_status()
                data = pr.json()
                status = data.get("status")
                if status in ("COMPLETED", "FAILED", "CANCELLED"):
                    break

        if status == "FAILED":
            raise RuntimeError(f"RunPod job failed: {data.get('error')}")

        # Extract text from vLLM OpenAI response
        raw = ""
        output = data.get("output")
        if isinstance(output, list) and output:
            output = output[0]
        if isinstance(output, dict):
            choices = output.get("choices", [])
            if choices:
                raw = choices[0].get("message", {}).get("content", "")

        elapsed = round(time.time() - t0, 1)

        # Qwen32B on RunPod: billed at serverless GPU time
        # A40 48GB ~$0.00059/s → tracked separately in report
        execution_ms = data.get("executionTime", 0)
        billed_ms = execution_ms + data.get("delayTime", 0)

        parsed, clean = parse_response(raw)
        return {
            "code": code,
            "niche": niche,
            "model": model_name,
            "raw": raw,
            "parsed": parsed,
            "clean_parse": clean,
            "scores": extract_scores(parsed),
            "evidence": extract_evidence(parsed),
            "vision": (parsed or {}).get("vision"),
            "input_tokens": None,   # vLLM doesn't expose token counts via openai_route
            "output_tokens": None,
            "cost_usd": None,       # computed separately from RunPod billed_ms
            "billed_ms": billed_ms,
            "elapsed_sec": elapsed,
            "error": None,
        }
    except Exception as e:
        return {
            "code": code, "niche": niche, "model": model_name,
            "raw": "", "parsed": None, "clean_parse": False,
            "scores": {p: None for p in ALL_PARAMS}, "evidence": {},
            "vision": None, "input_tokens": None, "output_tokens": None,
            "cost_usd": None, "billed_ms": 0, "elapsed_sec": round(time.time() - t0, 1),
            "error": str(e),
        }


# ── DeepInfra runner (OpenAI-compatible, per-token billing, no idle GPU cost) ─

# Pricing constants ($/M tokens) — verified 2026-09
DI_30B_INPUT_PER_M  = 0.15
DI_30B_OUTPUT_PER_M = 0.60
DI_235B_INPUT_PER_M  = 0.20
DI_235B_OUTPUT_PER_M = 0.88


def run_deepinfra(
    code: str, niche: str, assets, frame_data: List[Dict],
    api_key: str, model_name: str,
    input_price_per_m: float = 0.15,
    output_price_per_m: float = 0.60,
) -> Dict[str, Any]:
    """Run a DeepInfra-hosted vision model via their OpenAI-compatible endpoint."""
    import openai

    client = openai.OpenAI(
        api_key=api_key,
        base_url="https://api.deepinfra.com/v1/openai",
    )
    user_text = build_user_prompt(
        niche, assets.duration_sec, assets.metadata or {}, assets.transcript or ""
    )

    content: List[Dict] = []
    for fd in frame_data:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{fd['b64']}"},
        })
        content.append({"type": "text", "text": f"[t={fd['timestamp']:.1f}s]"})
    content.append({"type": "text", "text": user_text})

    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            max_tokens=2000,
            temperature=0.2,
        )
        raw = resp.choices[0].message.content if resp.choices else ""
        elapsed = round(time.time() - t0, 1)
        input_tokens = resp.usage.prompt_tokens if resp.usage else 0
        output_tokens = resp.usage.completion_tokens if resp.usage else 0
        cost_usd = (input_tokens * input_price_per_m + output_tokens * output_price_per_m) / 1_000_000
        parsed, clean = parse_response(raw)
        return {
            "code": code,
            "niche": niche,
            "model": model_name,
            "raw": raw,
            "parsed": parsed,
            "clean_parse": clean,
            "scores": extract_scores(parsed),
            "evidence": extract_evidence(parsed),
            "vision": (parsed or {}).get("vision"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 6),
            "elapsed_sec": elapsed,
            "error": None,
        }
    except Exception as e:
        return {
            "code": code, "niche": niche, "model": model_name,
            "raw": "", "parsed": None, "clean_parse": False,
            "scores": {p: None for p in ALL_PARAMS}, "evidence": {},
            "vision": None, "input_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0, "elapsed_sec": round(time.time() - t0, 1),
            "error": str(e),
        }


# ── Report builder ────────────────────────────────────────────────────────────

def load_result(code: str, model_tag: str) -> Optional[Dict]:
    p = RESULTS_DIR / f"{code}_{model_tag}.json"
    if p.exists():
        return json.loads(p.read_text())
    return None


def _is_complete(code: str, model_tag: str) -> bool:
    """True only if result exists, has no error, and has at least one parsed score."""
    r = load_result(code, model_tag)
    if r is None:
        return False
    if r.get("error"):
        return False
    scores = r.get("scores") or {}
    return any(v is not None for v in scores.values())


def flag_internal_inconsistency(result: Dict) -> List[str]:
    """
    Detect cases where evidence text contradicts the score.
    Simple heuristic: look for negative language in evidence for high scores (>=70)
    or positive language for very low scores (<=30).
    """
    flags = []
    scores = result.get("scores") or {}
    evidence = result.get("evidence") or {}
    negatives = ["weak", "absent", "missing", "no ", "none", "poor", "lacks", "doesn't", "not visible", "unclear"]
    positives = ["strong", "excellent", "clear", "great", "engaging", "effective", "high-energy"]

    for param in SCORED_PARAMS:
        sc = scores.get(param)
        ev = (evidence.get(param) or "").lower()
        if sc is None or not ev:
            continue
        if sc >= 70 and any(neg in ev for neg in negatives):
            flags.append(f"{param}: score={sc} but evidence contains negative language (\"{ev[:80]}...\")")
        if sc <= 30 and any(pos in ev for pos in positives):
            flags.append(f"{param}: score={sc} but evidence contains positive language (\"{ev[:80]}...\")")
    return flags


def build_report(sonnet_results: List[Dict], qwen_results: List[Dict]) -> str:
    sonnet_by_code = {r["code"]: r for r in sonnet_results}
    qwen_by_code = {r["code"]: r for r in qwen_results}

    lines = [
        "# Model Comparison: Claude Sonnet 4.6 vs Qwen2.5-VL-32B",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Reels: {len(REELS)} | Parameters compared: {len(SCORED_PARAMS)} scored",
        "",
    ]

    # ── Per-reel score table ──────────────────────────────────────────────────
    lines.append("## Per-Reel Scores (6 Scored Parameters)")
    lines.append("")

    header = "| Reel | Niche | Creator | Param | Sonnet | Qwen32B | Δ |"
    sep =    "|------|-------|---------|-------|--------|---------|---|"
    lines += [header, sep]

    all_deltas: Dict[str, List[float]] = {p: [] for p in SCORED_PARAMS}
    sonnet_totals: Dict[str, List[int]] = {p: [] for p in SCORED_PARAMS}
    qwen_totals: Dict[str, List[int]] = {p: [] for p in SCORED_PARAMS}

    for reel in REELS:
        code = reel["code"]
        niche = reel["niche"]
        creator = reel["creator"]
        sr = sonnet_by_code.get(code)
        qr = qwen_by_code.get(code)

        for i, param in enumerate(SCORED_PARAMS):
            ss = (sr.get("scores") or {}).get(param) if sr else None
            qs = (qr.get("scores") or {}).get(param) if qr else None
            delta = ""
            if ss is not None and qs is not None:
                d = qs - ss
                delta = f"{d:+d}"
                all_deltas[param].append(abs(d))
                sonnet_totals[param].append(ss)
                qwen_totals[param].append(qs)
            code_col = code if i == 0 else ""
            niche_col = niche if i == 0 else ""
            creator_col = creator if i == 0 else ""
            lines.append(
                f"| {code_col} | {niche_col} | {creator_col} | {param} "
                f"| {ss if ss is not None else 'ERR'} "
                f"| {qs if qs is not None else 'ERR'} "
                f"| {delta} |"
            )

    lines.append("")

    # ── Summary stats ─────────────────────────────────────────────────────────
    lines.append("## Summary Statistics")
    lines.append("")
    lines.append("| Parameter | Avg Δ (abs) | Sonnet avg | Qwen avg | Bias direction |")
    lines.append("|-----------|-------------|------------|----------|----------------|")

    total_abs_delta = []
    for param in SCORED_PARAMS:
        deltas = all_deltas[param]
        s_vals = sonnet_totals[param]
        q_vals = qwen_totals[param]
        if not deltas:
            lines.append(f"| {param} | — | — | — | — |")
            continue
        avg_abs = round(sum(deltas) / len(deltas), 1)
        s_avg = round(sum(s_vals) / len(s_vals), 1)
        q_avg = round(sum(q_vals) / len(q_vals), 1)
        bias = q_avg - s_avg
        direction = f"Qwen {'higher' if bias > 0 else 'lower'} by {abs(bias):.1f}"
        total_abs_delta.extend(deltas)
        lines.append(f"| {param} | {avg_abs} | {s_avg} | {q_avg} | {direction} |")

    if total_abs_delta:
        overall = round(sum(total_abs_delta) / len(total_abs_delta), 1)
        lines.append(f"| **OVERALL** | **{overall}** | — | — | — |")

    lines.append("")

    # ── Bias analysis ─────────────────────────────────────────────────────────
    lines.append("## Bias Analysis")
    lines.append("")
    all_raw_deltas = []
    for reel in REELS:
        code = reel["code"]
        sr = sonnet_by_code.get(code)
        qr = qwen_by_code.get(code)
        if not sr or not qr:
            continue
        for param in SCORED_PARAMS:
            ss = (sr.get("scores") or {}).get(param)
            qs = (qr.get("scores") or {}).get(param)
            if ss is not None and qs is not None:
                all_raw_deltas.append(qs - ss)

    if all_raw_deltas:
        mean_delta = sum(all_raw_deltas) / len(all_raw_deltas)
        # Variance: systematic if |mean| > 5 points, random if scattered
        n = len(all_raw_deltas)
        variance = sum((d - mean_delta) ** 2 for d in all_raw_deltas) / n
        std = variance ** 0.5

        lines.append(f"- Mean delta (Qwen − Sonnet) across all {n} param×reel pairs: **{mean_delta:+.1f} points**")
        lines.append(f"- Std deviation of deltas: **{std:.1f} points**")
        if abs(mean_delta) >= 5 and std < 15:
            lines.append(f"- Assessment: **Systematic bias** — Qwen scores consistently {'higher' if mean_delta > 0 else 'lower'}. A calibration offset of {-mean_delta:+.1f} points could correct this.")
        elif std >= 15:
            lines.append("- Assessment: **Random scatter** — high std deviation means the bias is not consistent. A simple offset cannot correct this; the model is not reliably aligned with Sonnet's rubric.")
        else:
            lines.append("- Assessment: **Near-aligned** — mean delta is small enough that scores are comparable without calibration.")

    lines.append("")

    # ── Internal inconsistency flags ──────────────────────────────────────────
    lines.append("## Internal Inconsistency Flags")
    lines.append("")
    any_flag = False
    for reel in REELS:
        code = reel["code"]
        for label, result in [("Sonnet", sonnet_by_code.get(code)), ("Qwen32B", qwen_by_code.get(code))]:
            if not result:
                continue
            flags = flag_internal_inconsistency(result)
            if flags:
                any_flag = True
                lines.append(f"**{code} ({reel['niche']}) — {label}:**")
                for f in flags:
                    lines.append(f"  - {f}")
    if not any_flag:
        lines.append("No internal inconsistencies detected.")

    lines.append("")

    # ── JSON reliability ──────────────────────────────────────────────────────
    lines.append("## JSON Reliability")
    lines.append("")

    for label, results in [("Claude Sonnet 4.6", sonnet_results), ("Qwen2.5-VL-32B", qwen_results)]:
        if not results:
            lines.append(f"**{label}**: no results yet")
            continue
        total = len(results)
        errors = sum(1 for r in results if r.get("error"))
        clean = sum(1 for r in results if r.get("clean_parse") and not r.get("error"))
        parsed = sum(1 for r in results if r.get("parsed") and not r.get("error"))
        lines.append(f"**{label}:** {total} calls — {errors} errors — {clean}/{total} clean parse — {parsed}/{total} parsed (after cleanup)")

    lines.append("")

    # ── Cost ─────────────────────────────────────────────────────────────────
    lines.append("## Cost")
    lines.append("")

    sonnet_cost = sum(r.get("cost_usd") or 0 for r in sonnet_results)
    sonnet_input = sum(r.get("input_tokens") or 0 for r in sonnet_results)
    sonnet_output = sum(r.get("output_tokens") or 0 for r in sonnet_results)
    lines.append(f"**Claude Sonnet 4.6:** ${sonnet_cost:.4f} total ({sonnet_input:,} input tokens, {sonnet_output:,} output tokens, @$3/$15 per M)")

    qwen_billed_ms = sum(r.get("billed_ms") or 0 for r in qwen_results)
    qwen_billed_s = qwen_billed_ms / 1000
    # 80GB serverless GPU (unquantized Qwen2.5-VL-32B): ~$0.000640/s ≈ A100 SXM 80GB rate; verify in RunPod billing
    GPU80_RATE_PER_SEC = 0.000640
    qwen_cost = qwen_billed_s * GPU80_RATE_PER_SEC
    lines.append(f"**Qwen2.5-VL-32B (RunPod 80GB, unquantized):** ${qwen_cost:.4f} total ({qwen_billed_s:.0f}s billed GPU time @ ~${GPU80_RATE_PER_SEC}/s est.)")
    lines.append(f"**Total test cost:** ${sonnet_cost + qwen_cost:.4f}")

    lines.append("")

    # ── Raw evidence spot-check ───────────────────────────────────────────────
    lines.append("## Evidence Spot-Check (Hook & Opening — first 5 reels)")
    lines.append("")
    for reel in REELS[:5]:
        code = reel["code"]
        sr = sonnet_by_code.get(code)
        qr = qwen_by_code.get(code)
        ss = (sr.get("scores") or {}).get("Hook & Opening") if sr else None
        qs = (qr.get("scores") or {}).get("Hook & Opening") if qr else None
        se = (sr.get("evidence") or {}).get("Hook & Opening", "") if sr else ""
        qe = (qr.get("evidence") or {}).get("Hook & Opening", "") if qr else ""
        lines.append(f"**{code} ({reel['niche']}/{reel['creator']}):**")
        lines.append(f"  Sonnet score={ss}: {se[:150]}")
        lines.append(f"  Qwen score={qs}: {qe[:150]}")

    return "\n".join(lines)


# ── Four-way report builder ───────────────────────────────────────────────────

def build_four_way_report(
    sonnet_results: List[Dict],
    runpod_results: List[Dict],
    di_30b_results: List[Dict],
    di_235b_results: List[Dict],
) -> str:
    """Build a four-model comparison: Sonnet / RunPod-Qwen2.5-VL-32B / DI-Qwen3-VL-30B / DI-Qwen3-VL-235B."""
    by_code = {
        "sonnet": {r["code"]: r for r in sonnet_results},
        "runpod": {r["code"]: r for r in runpod_results},
        "di_30b": {r["code"]: r for r in di_30b_results},
        "di_235b": {r["code"]: r for r in di_235b_results},
    }

    # Reels present in all four
    all_codes = set(by_code["sonnet"]) & set(by_code["runpod"]) & set(by_code["di_30b"]) & set(by_code["di_235b"])
    paired_reels = [r for r in REELS if r["code"] in all_codes]

    lines = [
        "# Four-Way Model Comparison",
        "## Sonnet 4.6 / RunPod Qwen2.5-VL-32B / DeepInfra Qwen3-VL-30B / DeepInfra Qwen3-VL-235B",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Reels with all four models scored: {len(paired_reels)}",
        "",
        "> **Note — model mismatch**: RunPod ran Qwen2.5-VL-32B (instruction-tuned, unquantized, MAX_MODEL_LEN=4096, ENFORCE_EAGER=true).",
        "> DeepInfra runs Qwen3-VL-30B and Qwen3-VL-235B (different generation, hosted shared infra).",
        "> Score/reliability differences reflect both model generation AND infrastructure changes — not isolatable.",
        "",
    ]

    # ── 1. Per-reel score table ───────────────────────────────────────────────
    lines.append("## 1. Per-Reel Scores (6 Scored Parameters)")
    lines.append("")
    lines.append("| Code | Niche | Parameter | Sonnet | RunPod-Q2.5-32B | Δ | DI-Q3-30B | Δ | DI-Q3-235B | Δ |")
    lines.append("|------|-------|-----------|--------|-----------------|---|-----------|---|------------|---|")

    deltas: Dict[str, Dict[str, List]] = {
        tag: {p: [] for p in SCORED_PARAMS} for tag in ("runpod", "di_30b", "di_235b")
    }
    avgs: Dict[str, Dict[str, List]] = {
        tag: {p: [] for p in SCORED_PARAMS} for tag in ("sonnet", "runpod", "di_30b", "di_235b")
    }
    raw_deltas: Dict[str, List] = {"runpod": [], "di_30b": [], "di_235b": []}

    def sc(result, param):
        return (result.get("scores") or {}).get(param) if result else None

    for reel in paired_reels:
        code = reel["code"]
        niche = reel["niche"]
        sr = by_code["sonnet"].get(code)
        rr = by_code["runpod"].get(code)
        ar = by_code["di_30b"].get(code)
        br = by_code["di_235b"].get(code)

        for i, param in enumerate(SCORED_PARAMS):
            ss = sc(sr, param)
            rs = sc(rr, param)
            as_ = sc(ar, param)
            bs = sc(br, param)

            def delta_str(s, base):
                if s is not None and base is not None:
                    return f"{s - base:+d}"
                return "—"

            for tag, val in [("runpod", rs), ("di_30b", as_), ("di_235b", bs)]:
                if val is not None and ss is not None:
                    deltas[tag][param].append(abs(val - ss))
                    raw_deltas[tag].append(val - ss)
                if val is not None:
                    avgs[tag][param].append(val)
            if ss is not None:
                avgs["sonnet"][param].append(ss)

            code_col = code if i == 0 else ""
            niche_col = niche if i == 0 else ""
            lines.append(
                f"| {code_col} | {niche_col} | {param} "
                f"| {ss if ss is not None else '—'} "
                f"| {rs if rs is not None else '—'} | {delta_str(rs, ss)} "
                f"| {as_ if as_ is not None else '—'} | {delta_str(as_, ss)} "
                f"| {bs if bs is not None else '—'} | {delta_str(bs, ss)} |"
            )

    lines.append("")

    # ── 2. Summary stats ──────────────────────────────────────────────────────
    lines.append("## 2. Average |Δ| vs Sonnet, Per Parameter")
    lines.append("")
    lines.append("| Parameter | RunPod-Q2.5-32B avg|Δ| | DI-Q3-30B avg|Δ| | DI-Q3-235B avg|Δ| |")
    lines.append("|-----------|------------------------|-------------------|-------------------|")

    for param in SCORED_PARAMS:
        def avg_abs(tag):
            ds = deltas[tag][param]
            return f"{sum(ds)/len(ds):.1f}" if ds else "—"
        lines.append(f"| {param} | {avg_abs('runpod')} | {avg_abs('di_30b')} | {avg_abs('di_235b')} |")

    lines.append("")

    # ── 3. Bias analysis ──────────────────────────────────────────────────────
    lines.append("## 3. Bias vs Sonnet")
    lines.append("")
    lines.append("| Model | Mean Δ (signed) | Std dev | Bias type |")
    lines.append("|-------|-----------------|---------|-----------|")

    for tag, label in [("runpod", "RunPod Qwen2.5-VL-32B"), ("di_30b", "DI Qwen3-VL-30B"), ("di_235b", "DI Qwen3-VL-235B")]:
        ds = raw_deltas[tag]
        if not ds:
            lines.append(f"| {label} | — | — | — |")
            continue
        mean = sum(ds) / len(ds)
        std = (sum((d - mean) ** 2 for d in ds) / len(ds)) ** 0.5
        if abs(mean) >= 5 and std < 15:
            bias = f"Systematic ({'higher' if mean > 0 else 'lower'} by {abs(mean):.1f}pt; offset of {-mean:+.1f} would align)"
        elif std >= 15:
            bias = "Scattered (high std — no simple offset correction)"
        else:
            bias = f"Near-aligned (mean Δ < 5pt)"
        lines.append(f"| {label} | {mean:+.1f} | {std:.1f} | {bias} |")

    lines.append("")

    # ── 4. JSON reliability ───────────────────────────────────────────────────
    lines.append("## 4. JSON Reliability (9 attempted reels each)")
    lines.append("")
    lines.append("| Model | Clean parse (first try) | After rescue | Failures |")
    lines.append("|-------|------------------------|--------------|----------|")

    def reliability_row(results, label):
        if not results:
            return f"| {label} | — | — | — |"
        n = len(results)
        errors = sum(1 for r in results if r.get("error"))
        clean = sum(1 for r in results if r.get("clean_parse") and not r.get("error"))
        parsed = sum(1 for r in results if r.get("parsed") and not r.get("error"))
        fail_codes = [r["code"] for r in results if not r.get("parsed") and not r.get("error")]
        fail_str = ", ".join(fail_codes) if fail_codes else "none"
        return f"| {label} | {clean}/{n} ({100*clean//n}%) | {parsed}/{n} ({100*parsed//n}%) | {fail_str} |"

    lines.append(reliability_row(sonnet_results, "Sonnet 4.6"))
    lines.append(reliability_row(runpod_results, "RunPod Qwen2.5-VL-32B"))
    lines.append(reliability_row(di_30b_results, "DI Qwen3-VL-30B"))
    lines.append(reliability_row(di_235b_results, "DI Qwen3-VL-235B"))
    lines.append("")

    # ── 5. Cost breakdown ─────────────────────────────────────────────────────
    lines.append("## 5. Cost")
    lines.append("")
    lines.append("| Model | Reels | Input tok | Output tok | Total $ | $/reel |")
    lines.append("|-------|-------|-----------|------------|---------|--------|")

    def cost_row(results, label, rate_note=""):
        if not results:
            return f"| {label} | 0 | — | — | — | — |"
        n = len([r for r in results if not r.get("error")])
        ti = sum(r.get("input_tokens") or 0 for r in results)
        to_ = sum(r.get("output_tokens") or 0 for r in results)
        tc = sum(r.get("cost_usd") or 0 for r in results)
        per = tc / n if n else 0
        tok_in = f"{ti:,}" if ti else "—"
        tok_out = f"{to_:,}" if to_ else "—"
        return f"| {label} | {n} | {tok_in} | {tok_out} | ${tc:.4f} | ${per:.4f} |"

    lines.append(cost_row(sonnet_results, "Sonnet 4.6 (API)"))
    lines.append(f"| RunPod Qwen2.5-VL-32B | 9 | — | — | ~$3.51–4.15 (wall-clock GPU) | ~$0.39–0.46 |")
    lines.append(cost_row(di_30b_results, "DI Qwen3-VL-30B ($0.15/$0.60/M)"))
    lines.append(cost_row(di_235b_results, "DI Qwen3-VL-235B ($0.20/$0.88/M)"))
    lines.append("")

    # ── 6. 4,900-reel extrapolation ───────────────────────────────────────────
    lines.append("## 6. Extrapolation to 4,900 Reels")
    lines.append("")
    lines.append("| Model | $/reel (measured) | 4,900 reels | vs Sonnet |")
    lines.append("|-------|-------------------|-------------|-----------|")

    def extrap_row(results, label, per_reel_override=None):
        if per_reel_override is not None:
            per = per_reel_override
        elif results:
            n = len([r for r in results if not r.get("error")])
            tc = sum(r.get("cost_usd") or 0 for r in results)
            per = tc / n if n else 0
        else:
            return f"| {label} | — | — | — |"
        total = per * 4900
        return f"| {label} | ${per:.4f} | ${total:.2f} | — |"

    # Sonnet baseline
    sn = len([r for r in sonnet_results if not r.get("error")])
    sc_total = sum(r.get("cost_usd") or 0 for r in sonnet_results)
    sonnet_per = sc_total / sn if sn else 0
    sonnet_4900 = sonnet_per * 4900
    lines.append(f"| Sonnet 4.6 | ${sonnet_per:.4f} | ${sonnet_4900:.2f} | 1× (baseline) |")
    lines.append(f"| RunPod Qwen2.5-VL-32B | ~$0.42 | ~$2,058 | ~113× |")

    for results, label in [(di_30b_results, "DI Qwen3-VL-30B"), (di_235b_results, "DI Qwen3-VL-235B")]:
        if not results:
            lines.append(f"| {label} | — | — | — |")
            continue
        n = len([r for r in results if not r.get("error")])
        tc = sum(r.get("cost_usd") or 0 for r in results)
        per = tc / n if n else 0
        total_4900 = per * 4900
        ratio = per / sonnet_per if sonnet_per else 0
        sign = "×" if ratio >= 1 else "×"
        lines.append(f"| {label} | ${per:.4f} | ${total_4900:.2f} | {ratio:.1f}× vs Sonnet |")

    lines.append("")

    return "\n".join(lines)


# ── Orchestrator ──────────────────────────────────────────────────────────────

def save_result(result: Dict, model_tag: str) -> None:
    code = result["code"]
    p = RESULTS_DIR / f"{code}_{model_tag}.json"
    p.write_text(json.dumps(result, indent=2, default=str))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["sonnet", "qwen", "both", "deepinfra"], default="both")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--qwen-endpoint", default=os.environ.get("QWEN32B_ENDPOINT", ""))
    ap.add_argument("--qwen-model", default=os.environ.get("QWEN32B_MODEL", "Qwen/Qwen2.5-VL-32B-Instruct"))
    ap.add_argument("--runpod-key", default=os.environ.get("RUNPOD_API_KEY_32B") or os.environ.get("LLM_API_KEY", ""))
    ap.add_argument("--anthropic-key", default=os.environ.get("ANTHROPIC_API_KEY", ""))
    ap.add_argument("--deepinfra-key", default=os.environ.get("DEEPINFRA_API_KEY", ""))
    ap.add_argument("--deepinfra-model-30b", default=os.environ.get("DEEPINFRA_MODEL_30B", "Qwen/Qwen3-VL-30B-A3B-Instruct"))
    ap.add_argument("--deepinfra-model-235b", default=os.environ.get("DEEPINFRA_MODEL_235B", "Qwen/Qwen3-VL-235B-A22B-Instruct"))
    ap.add_argument("--sleep-sec", type=float, default=20.0,
                    help="Seconds to sleep between reels to avoid Instagram rate limiting (default: 20)")
    args = ap.parse_args()

    # ── Report-only mode ──────────────────────────────────────────────────────
    if args.report_only:
        sonnet_results = [r for code in [r["code"] for r in REELS]
                         if (r := load_result(code, "sonnet")) is not None]
        qwen_results   = [r for code in [r["code"] for r in REELS]
                         if (r := load_result(code, "qwen32b")) is not None]
        di_30b_results  = [r for code in [r["code"] for r in REELS]
                           if (r := load_result(code, "qwen3_30b_di")) is not None]
        di_235b_results = [r for code in [r["code"] for r in REELS]
                           if (r := load_result(code, "qwen3_235b_di")) is not None]
        if di_30b_results or di_235b_results:
            report = build_four_way_report(sonnet_results, qwen_results, di_30b_results, di_235b_results)
            report_path = RESULTS_DIR / "report_four_way.md"
        else:
            report = build_report(sonnet_results, qwen_results)
            report_path = RESULTS_DIR / "report.md"
        report_path.write_text(report)
        print(report)
        log.info("Report saved to %s", report_path)
        return 0

    # ── DeepInfra mode — runs only on reels already paired (Sonnet + RunPod) ──
    if args.model == "deepinfra":
        if not args.deepinfra_key:
            log.error("DEEPINFRA_API_KEY not set. Pass --deepinfra-key or set env var.")
            return 2

        sys.path.insert(0, str(HERE.parent.parent))
        from scripts.niche_research.video_pipeline import prepare_video  # noqa: E402

        paired_codes = {
            r["code"] for r in REELS
            if _is_complete(r["code"], "sonnet") and _is_complete(r["code"], "qwen32b")
        }
        log.info("DeepInfra mode: %d paired reels found", len(paired_codes))

        BUDGET_LIMIT = 1.0  # USD — stop if total DI cost exceeds this
        di_30b_results: List[Dict] = []
        di_235b_results: List[Dict] = []
        running_cost = 0.0

        for reel in REELS:
            code = reel["code"]
            if code not in paired_codes:
                continue

            done_30b  = _is_complete(code, "qwen3_30b_di")
            done_235b = _is_complete(code, "qwen3_235b_di")
            if done_30b and done_235b:
                log.info("[%s] DeepInfra already complete — loading", code)
                di_30b_results.append(load_result(code, "qwen3_30b_di"))
                di_235b_results.append(load_result(code, "qwen3_235b_di"))
                continue

            # Load cached assets — will not hit Instagram since /tmp is populated
            assets = prepare_video(f"https://www.instagram.com/p/{code}/", reel["niche"])
            if assets.error:
                log.error("[%s] asset load failed: %s", code, assets.error)
                continue

            frame_data = load_frame_data(assets)
            log.info("[%s] %d frames, %.1fs duration", code, len(frame_data), assets.duration_sec)

            if not done_30b:
                log.info("[%s] → DeepInfra Qwen3-VL-30B …", code)
                r30 = run_deepinfra(
                    code, reel["niche"], assets, frame_data,
                    args.deepinfra_key, args.deepinfra_model_30b,
                    input_price_per_m=DI_30B_INPUT_PER_M,
                    output_price_per_m=DI_30B_OUTPUT_PER_M,
                )
                save_result(r30, "qwen3_30b_di")
                di_30b_results.append(r30)
                running_cost += r30.get("cost_usd") or 0
                scores_30b = {k: v for k, v in r30["scores"].items() if k in SCORED_PARAMS}
                log.info("[%s] 30B scores: %s | cost=$%.5f | err=%s",
                         code, scores_30b, r30.get("cost_usd", 0), r30.get("error"))

                if running_cost > BUDGET_LIMIT:
                    log.error("BUDGET GUARDRAIL: DeepInfra cost $%.4f exceeded $%.2f limit — stopping.",
                              running_cost, BUDGET_LIMIT)
                    break

            if not done_235b:
                log.info("[%s] → DeepInfra Qwen3-VL-235B …", code)
                r235 = run_deepinfra(
                    code, reel["niche"], assets, frame_data,
                    args.deepinfra_key, args.deepinfra_model_235b,
                    input_price_per_m=DI_235B_INPUT_PER_M,
                    output_price_per_m=DI_235B_OUTPUT_PER_M,
                )
                save_result(r235, "qwen3_235b_di")
                di_235b_results.append(r235)
                running_cost += r235.get("cost_usd") or 0
                scores_235b = {k: v for k, v in r235["scores"].items() if k in SCORED_PARAMS}
                log.info("[%s] 235B scores: %s | cost=$%.5f | err=%s",
                         code, scores_235b, r235.get("cost_usd", 0), r235.get("error"))

                if running_cost > BUDGET_LIMIT:
                    log.error("BUDGET GUARDRAIL: DeepInfra cost $%.4f exceeded $%.2f limit — stopping.",
                              running_cost, BUDGET_LIMIT)
                    break

        log.info("DeepInfra run complete. Total cost: $%.4f", running_cost)

        # Build four-way report
        sonnet_results = [r for code in [r["code"] for r in REELS]
                         if (r := load_result(code, "sonnet")) is not None]
        qwen_results   = [r for code in [r["code"] for r in REELS]
                         if (r := load_result(code, "qwen32b")) is not None]
        report = build_four_way_report(sonnet_results, qwen_results, di_30b_results, di_235b_results)
        report_path = RESULTS_DIR / "report_four_way.md"
        report_path.write_text(report)
        log.info("Four-way report saved to %s", report_path)
        print("\n" + report)
        return 0

    # ── Load per-reel assets ──────────────────────────────────────────────────
    # Import here to avoid config loading at module import time
    import sys as _sys
    _sys.path.insert(0, str(HERE.parent.parent))  # prysm-backend root
    from scripts.niche_research.video_pipeline import prepare_video, _cache_key  # noqa: E402

    run_sonnet_flag = args.model in ("sonnet", "both")
    run_qwen_flag = args.model in ("qwen", "both")

    if run_qwen_flag and not args.qwen_endpoint:
        log.error(
            "QWEN32B_ENDPOINT not set. Set env var or pass --qwen-endpoint.\n"
            "See deployment instructions at scripts/niche_research/RUNPOD_32B_DEPLOY.md"
        )
        if args.model == "qwen":
            return 2
        log.warning("Skipping Qwen32B — running Sonnet only.")
        run_qwen_flag = False

    sonnet_results = []
    qwen_results = []

    for reel_idx, reel in enumerate(REELS):
        code = reel["code"]
        niche = reel["niche"]
        url = f"https://www.instagram.com/p/{code}/"

        # Skip only if already complete (no error, has scores) — error stubs are retried
        sonnet_done = _is_complete(code, "sonnet")
        qwen_done = _is_complete(code, "qwen32b")

        if (not run_sonnet_flag or sonnet_done) and (not run_qwen_flag or qwen_done):
            log.info("[%s] already complete — loading from cache", code)
            if sonnet_done:
                sonnet_results.append(load_result(code, "sonnet"))
            if qwen_done:
                qwen_results.append(load_result(code, "qwen32b"))
            continue

        # Sleep between downloads to avoid Instagram rate limiting (skip before first reel)
        if reel_idx > 0 and args.sleep_sec > 0:
            log.info("Sleeping %.0fs before next reel …", args.sleep_sec)
            time.sleep(args.sleep_sec)

        log.info("[%s] preparing video assets …", code)
        assets = prepare_video(url, niche)
        if assets.error:
            log.error("[%s] video prep failed: %s", code, assets.error)
            stub = {
                "code": code, "niche": niche, "error": assets.error,
                "scores": {p: None for p in ALL_PARAMS}, "evidence": {},
                "vision": None, "clean_parse": False,
                "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "billed_ms": 0,
            }
            if run_sonnet_flag and not sonnet_done:
                stub["model"] = "claude-sonnet-4-6"
                save_result(stub, "sonnet")
                sonnet_results.append(stub)
            if run_qwen_flag and not qwen_done:
                stub["model"] = args.qwen_model
                save_result(stub, "qwen32b")
                qwen_results.append(stub)
            continue

        frame_data = load_frame_data(assets)
        log.info("[%s] %d frames loaded, duration=%.1fs", code, len(frame_data), assets.duration_sec)

        if run_sonnet_flag and not sonnet_done:
            log.info("[%s] → Sonnet 4.6 …", code)
            sr = run_sonnet(code, niche, assets, frame_data, args.anthropic_key)
            save_result(sr, "sonnet")
            sonnet_results.append(sr)
            scores_summary = {k: v for k, v in sr["scores"].items() if k in SCORED_PARAMS}
            log.info("[%s] Sonnet scores: %s | cost=$%.5f | err=%s",
                     code, scores_summary, sr.get("cost_usd", 0), sr.get("error"))

        if run_qwen_flag and not qwen_done:
            log.info("[%s] → Qwen32B …", code)
            qr = run_qwen(code, niche, assets, frame_data,
                          args.qwen_endpoint, args.runpod_key, args.qwen_model)
            save_result(qr, "qwen32b")
            qwen_results.append(qr)
            scores_summary = {k: v for k, v in qr["scores"].items() if k in SCORED_PARAMS}
            log.info("[%s] Qwen32B scores: %s | billed=%dms | err=%s",
                     code, scores_summary, qr.get("billed_ms", 0), qr.get("error"))

        # Load existing if we only ran one side
        if run_sonnet_flag and sonnet_done and not any(r["code"] == code for r in sonnet_results):
            sonnet_results.append(load_result(code, "sonnet"))
        if run_qwen_flag and qwen_done and not any(r["code"] == code for r in qwen_results):
            qwen_results.append(load_result(code, "qwen32b"))

    # ── Build report ──────────────────────────────────────────────────────────
    report = build_report(sonnet_results, qwen_results)
    report_path = RESULTS_DIR / "report.md"
    report_path.write_text(report)
    log.info("Report saved to %s", report_path)
    print("\n" + report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
