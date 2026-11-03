"""Built during the Meta Global AI Developer Hackathon (new work).

Replay of ONLY the scoring/writing calls (call A and call B) from inputs saved by a full analysis run
(DEBUG_SAVE_INPUTS=1), with a chosen model and style setting, so scoring models can be compared without re-running the
perception stages. The baseline's own helpers are reused (call retry, suggestion validator); the merge, the overall
score and the checks mirror what the pipeline does after the two calls.
"""
import asyncio
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DIR = REPO_ROOT / "samples" / "_local" / "scoring_inputs"

CANONICAL_ORDER = ["Hook & Opening", "Retention & Pacing", "Shareability", "Audio & Music", "Trend Alignment",
                   "Visual Quality & Originality", "On-Screen Text", "Save Potential", "Replay Value", "Call to Action"]
TEXT_FIELDS = ("problem", "fix", "whatIsHappening", "whyItMatters", "whatToChange", "howToDoIt", "whatToExpect")


def inputs_dir() -> Path:
    return Path(os.environ.get("DEBUG_SAVE_INPUTS_DIR") or DEFAULT_DIR)


def save_inputs(inp: dict) -> Path:
    """Write the scoring inputs of one analysis (called once per analysis when DEBUG_SAVE_INPUTS=1). Returns the file path."""
    d = inputs_dir()
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    path = d / f"scoring_inputs_{stamp}_{abs(hash(inp.get('call_a_prompt', ''))) % 10**6:06d}.json"
    path.write_text(json.dumps(inp, ensure_ascii=False))
    return path


def load_inputs(path) -> dict:
    return json.loads(Path(path).read_text())


def base_prompt(prompt: str, addendum: str) -> str:
    """The prompt as the baseline builds it (any style addendum the saved run used is cut off the end)."""
    return prompt[: -len(addendum)] if addendum and prompt.endswith(addendum) else prompt


def _parse(raw: str, label: str) -> tuple:
    """(parsed dict, strict) - strict is True when the reply was valid JSON as written (fences and surrounding text aside)."""
    from app.studio.json_tolerant import extract_json_object
    text = extract_json_object(raw)
    text = re.sub(r"```json\n?", "", text)
    text = re.sub(r"```\n?", "", text).strip()
    text = re.sub(r'"overallScore"\s*:\s*\d+\s*,?\s*', "", text)
    text = re.sub(r'"scoreLabel"\s*:\s*"[^"]*"\s*,?\s*', "", text)
    text = re.sub(r'"predictedScoreAfterFixes"\s*:\s*\d+\s*,?\s*', "", text)
    try:
        return json.loads(text), True
    except json.JSONDecodeError:
        from json_repair import repair_json
        return json.loads(repair_json(text, return_objects=False)), False


async def rescore(inp: dict, *, model: str, style: bool, regen: bool = True) -> dict:
    """Run call A and call B again. Returns {"result", "raw", "stats"}; `result` is shaped like the pipeline's parameters block."""
    from app.core.config import settings
    from app.services.prysm_analyse_service import _claude_with_retry, _validate_suggestions
    from app.studio import coach_style, meta_llm_shim as shim, quality, step_quality
    from app.studio.score_math import overall_and_predicted
    if not settings.META_MODE:
        raise RuntimeError("set AI_PROVIDER=meta before importing the app (the replay goes through the Muse shim)")

    add = coach_style.addendum(style)
    old = inp.get("style_addendum") or ""
    pa = base_prompt(inp["call_a_prompt"], old) + add
    pb = base_prompt(inp["call_b_prompt"], old) + add
    client = shim.AsyncAnthropic()
    t0 = time.time()

    async def one(prompt, max_tokens):
        t = time.time()
        r = await _claude_with_retry(client, model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
        return r, time.time() - t

    (ra, ta), (rb, tb) = await asyncio.gather(one(pa, inp["max_tokens_a"]), one(pb, inp["max_tokens_b"]))
    latency = time.time() - t0
    stats = {
        "model": getattr(ra, "model", model), "style": bool(style), "latency_s": round(latency, 1),
        "latency_a_s": round(ta, 1), "latency_b_s": round(tb, 1),
        "tokens_in": ra.usage.input_tokens + rb.usage.input_tokens,
        "tokens_out": ra.usage.output_tokens + rb.usage.output_tokens,
        "reasoning_tokens": ra.reasoning_tokens + rb.reasoning_tokens,
        "cost_usd": round(ra.cost_usd + rb.cost_usd, 4),
        "stop_reasons": [ra.stop_reason, rb.stop_reason],
        "prompt_chars": [len(pa), len(pb)],
    }
    raw = {"A": ra.content[0].text, "B": rb.content[0].text}
    valid = True
    parsed = {}
    for label, text in raw.items():
        try:
            parsed[label], strict = _parse(text, label)
            valid = valid and strict
        except Exception:
            parsed[label], valid = {"parameters": []}, False
    stats["valid_json"] = valid

    result = parsed["A"]
    by_name = {}
    for p in parsed["A"].get("parameters", []) + parsed["B"].get("parameters", []):
        by_name[p.get("name", "")] = p
    result["parameters"] = sorted(by_name.values(), key=lambda p: CANONICAL_ORDER.index(p["name"]) if p.get("name") in CANONICAL_ORDER else 99)
    for p in result["parameters"]:
        for f in TEXT_FIELDS:
            v = p.get(f)
            if isinstance(v, list):
                p[f] = " ".join(str(x) for x in v)
            elif v is not None and not isinstance(v, str):
                p[f] = str(v)
    try:
        result = _validate_suggestions(result, inp["whisper_data"], inp["vision_data"], inp["intent"])
    except Exception:
        pass
    from app.core.config import ADVISORY_PARAMETERS
    for p in result["parameters"]:
        p["advisory"] = p.get("name") in ADVISORY_PARAMETERS
    # the pipeline's step handling: one canonical format, then a regeneration retry for any parameter that fails the step check
    step_quality.normalise_all(result)
    stats["failing_before_regen"] = [p.get("name") for p, _ in step_quality.failing(result)]
    stats["regen"] = {"calls": 0, "regenerated": [], "still_failing": [], "cost_usd": 0.0}
    if regen:
        async def regen_call(prompt, max_tokens):
            return await _claude_with_retry(client, model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
        rs = await step_quality.regenerate_failing(result, call=regen_call, transcript=(inp.get("whisper_data") or {}).get("transcript") or "",
                                                   niche=inp.get("niche", ""), platform=inp.get("platform", ""), intent=inp.get("intent", "my_video"))
        stats["regen"] = {"calls": rs.calls, "regenerated": rs.regenerated, "still_failing": rs.still_failing, "cost_usd": rs.cost_usd}
        stats["tokens_in"] += rs.tokens_in
        stats["tokens_out"] += rs.tokens_out
        stats["reasoning_tokens"] += rs.reasoning_tokens
        stats["cost_usd"] = round(stats["cost_usd"] + rs.cost_usd, 4)
    result["overallScore"], result["predictedScoreAfterFixes"] = overall_and_predicted(result["parameters"], inp["scored_weights"])
    stats["metrics"] = metrics(result, inp, stats)
    return {"result": result, "raw": raw, "stats": stats}


def metrics(result: dict, inp: dict, stats: dict) -> dict:
    """The bake-off's automatic metrics for one variant."""
    from app.studio import quality as q
    steps = q.steps_per_scored(result)
    ready, adv = q.advisory_with_ready_wording(result)
    return {
        "valid_json": stats.get("valid_json"),
        "truncated_fields": len(q.find_truncated(result)) + sum(1 for s in stats["stop_reasons"] if s == "max_tokens"),
        "steps_per_scored": steps,
        "scored_with_fewer_than_3_steps": sum(1 for v in steps.values() if v < 3),
        "avg_words_per_problem": q.avg_words(result, "problem"),
        "transcript_quotes": q.transcript_quotes(result, (inp.get("whisper_data") or {}).get("transcript") or ""),
        "advisory_with_ready_wording": f"{ready}/{adv}",
        "repeated_closing_phrases": q.repeated_closings(result),
        "overall": result.get("overallScore"),
        "latency_s": stats["latency_s"], "tokens_in": stats["tokens_in"], "tokens_out": stats["tokens_out"],
        "reasoning_tokens": stats["reasoning_tokens"], "cost_usd": stats["cost_usd"],
    }
