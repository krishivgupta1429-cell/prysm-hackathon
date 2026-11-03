"""Built during the Meta Global AI Developer Hackathon (new work).

Watch stage: Muse Spark watches the native video and returns a structured JSON read of it
(hook, timeline, weak moments, on-screen text, format, niche, CTA).
"""
import json
import re
import time
from pathlib import Path

from pydantic import ValidationError

from app.studio import config
from app.studio.meta_client import MetaClient, MetaError
from app.studio.report import TokenUsage, WatchOutput, WatchStage

PROMPT = """You are analysing a short vertical social video ({duration:.0f} seconds long) for a creator tool.
Watch the whole video, then reply with ONE JSON object and nothing else: no prose, no markdown, no code fences.

All times are in seconds from the start of the video (numbers, not strings). Use exactly this schema:
{{
  "hook": {{"what_happens": "what the viewer sees and hears in the first 3 seconds",
            "attention_rating": <integer 0-10, how strongly the first 3 seconds stop a scroll>,
            "rating_reason": "one sentence"}},
  "timeline": [{{"start_s": 0.0, "end_s": 2.5, "what_happens": "..."}}],
  "weak_moments": [{{"start_s": 0.0, "end_s": 0.0, "problem": "why viewers may drop off here"}}],
  "on_screen_text": [{{"text": "exact text shown", "start_s": 0.0, "end_s": 0.0}}],
  "setting": "where it takes place",
  "subject_description": "who or what is the main subject",
  "format_type": one of "talking head", "dance", "skit", "voiceover", "montage", "other",
  "niche_guess": {{"niche": "e.g. fashion, fitness, comedy", "confidence": <0.0-1.0>}},
  "cta_present": <true or false>,
  "cta_text": "the call to action, or null",
  "visual_quality_notes": "lighting, framing, stability, resolution"
}}
The timeline must cover the whole video in order. Use [] for empty lists. Output the JSON object only."""

RETRY_SUFFIX = "\n\nYour previous reply was not valid JSON for this schema. Reply with the JSON object only."


def parse_watch_json(text: str) -> dict:
    """Parse model output into a dict: strips ``` fences and surrounding prose. Raises ValueError."""
    cleaned = re.sub(r"^\s*```(?:json|JSON)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```\s*$", "", cleaned).strip()
    try:
        value = json.loads(cleaned)
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in model output") from None
        value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("model output is not a JSON object")
    return value


def parse_watch_output(text: str) -> WatchOutput:
    """JSON text -> validated WatchOutput. Raises ValueError (ValidationError is one) on any problem."""
    out = WatchOutput.model_validate(parse_watch_json(text))
    if out.hook is None or not out.timeline:
        raise ValueError("JSON parsed but hook or timeline is missing")
    return out


def run_watch(client: MetaClient, proxy: Path, duration_s: float, model: str, raw_dir: Path) -> WatchStage:
    start = time.time()
    stage = WatchStage(model=model)
    prompt = PROMPT.format(duration=duration_s)
    tokens_in = tokens_out = 0
    raw = ""
    try:
        for attempt in (1, 2):
            call = client.watch_video(proxy, prompt if attempt == 1 else prompt + RETRY_SUFFIX, model)
            raw = call.text
            tokens_in += call.tokens_in or 0
            tokens_out += call.tokens_out or 0
            stage.cost_usd = round(stage.cost_usd + call.cost_usd, 6)
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"watch_{model}_attempt{attempt}.txt").write_text(config.redact(raw))
            try:
                parsed = parse_watch_output(raw)
            except (ValueError, ValidationError) as e:
                stage.error = f"attempt {attempt}: could not parse model output: {str(e)[:200]}"
                continue
            for name in WatchOutput.model_fields:
                setattr(stage, name, getattr(parsed, name))
            stage.status, stage.error, stage.raw_text = "ok", None, None
            break
        else:
            stage.status, stage.raw_text = "error", config.redact(raw)  # stage.error holds the last parse failure
    except MetaError as e:
        stage.status, stage.error = "error", str(e)
    except Exception as e:  # never let a stage raise into the pipeline
        stage.status, stage.error = "error", config.redact(f"{type(e).__name__}: {e}")
    stage.usage = TokenUsage(input_tokens=tokens_in, output_tokens=tokens_out)
    stage.seconds = round(time.time() - start, 2)
    return stage
