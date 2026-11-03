"""Hackathon-built (Meta Global AI Developer Hackathon).

The adapter: Prysm as the analysis engine of the existing Prysm website and backend.

`run_studio_analysis` has the signature and return shape of `run_full_analysis` in
app/services/prysm_analyse_service.py, so the baseline flow (queue, credits, database, Redis cache, results
page) works unchanged when `ANALYSIS_ENGINE=studio`. This is the only Studio file that imports baseline modules.

How a Studio analysis becomes the baseline's result
  - the 10 parameters come back in the baseline's order with the baseline's field names; the weights are the
    baseline's own lookup (NICHE_WEIGHTS for the primary niche, else DEFAULT_SCORED_WEIGHTS)
  - overallScore, scoreLabel and predictedScoreAfterFixes use the baseline's rules (weighted sum of the scored
    parameters, its label thresholds, and "every scored parameter below 75 is fixed to 75")
  - one extra muse-spark-1.3 call, grounded in the evidence, writes the summary, biggest problem, quick fix,
    top priorities, caption and exactly five hashtags
  - `_cost` has the baseline CostTracker shape with the real stage costs, so the credit deduction is unchanged
  - Studio's own extras (confidence, range, stable, evidence, fixable_by, edit intents, flags, ...) are additive
"""
import asyncio
import base64
import json
import logging
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.core.config import ADVISORY_PARAMETERS, DEFAULT_SCORED_WEIGHTS, NICHE_WEIGHTS, settings  # baseline: allowed here only
from app.studio import config as studio_config
from app.studio import trends as trends_mod
from app.studio.analysis.creator import CreatorContext, weights_by_id
from app.studio.analysis.niches import PARAM_NAMES
from app.studio.meta_client import MetaClient, MetaError
from app.studio.pipeline import analyze_video
from app.studio.report import Report
from app.studio.safety import Limits
from app.studio.stages.watch import parse_watch_json

log = logging.getLogger("prysm.studio")

# The order the baseline returns its 10 parameters in.
BASELINE_ORDER = ["Hook & Opening", "Retention & Pacing", "Shareability", "Audio & Music", "Trend Alignment",
                  "Visual Quality & Originality", "On-Screen Text", "Save Potential", "Replay Value", "Call to Action"]
NAME_TO_ID = {name: pid for pid, name in PARAM_NAMES.items()}
FRAME_COUNT = 12  # frames sent to the page (the baseline sends up to 18)
FRAME_WIDTH = 768
CACHE_LIMIT_BYTES = 900_000  # the baseline keeps Redis values under 1 MB; stay clear of it
HASHTAG_COUNT = 5
PROGRESS_TTL = 7200
STAGE_LABELS = {"watch": "Watching the video", "speech": "Transcribing speech", "subjects": "Tracking people and faces",
                "audio": "Listening to the audio", "signals": "Measuring visual and audio signals", "consensus": "Checking the transcript",
                "captions": "Drafting captions", "music_id": "Identifying the music", "analysis": "Scoring the 10 parameters",
                "summary": "Writing the summary"}


class StudioVideoTooLong(ValueError):
    """The video is longer than Prysm supports; the message is shown to the user as is."""

    def __init__(self, limit: int = 90):
        super().__init__(f"Prysm supports videos up to {limit} seconds.")


# ---------------------------------------------------------------- the baseline's own scoring rules

def baseline_weights(niche: str, niche_weights: dict | None = None, default_weights: dict | None = None) -> dict[str, float]:
    """The baseline's lookup: the primary niche (first of the comma list), lower-cased, in NICHE_WEIGHTS, else the default."""
    niche_weights = NICHE_WEIGHTS if niche_weights is None else niche_weights
    default_weights = DEFAULT_SCORED_WEIGHTS if default_weights is None else default_weights
    primary = (niche.split(",")[0] if "," in niche else niche).strip()
    return dict(niche_weights.get(primary.lower(), default_weights))


def score_label(score: int, intent: str = "my_video") -> str:
    """The baseline's thresholds (and its positive wording for competitor analyses)."""
    if intent == "competitor":
        return "Excellent Execution" if score >= 80 else ("Strong Concept, Needs Polish" if score >= 65 else "Has Potential")
    if score >= 90:
        return "Exceptional"
    if score >= 80:
        return "Viral-Ready"
    if score >= 70:
        return "Strong Content"
    if score >= 55:
        return "Good Potential, Needs Polish"
    if score >= 40:
        return "Below Average"
    return "Needs Significant Work"


def baseline_scores(parameters: list[dict], weights: dict[str, float], advisory: set[str] | None = None) -> tuple[int, int]:
    """(overallScore, predictedScoreAfterFixes) exactly as the baseline computes them: only scored parameters
    count, overall = sum(score x weight), predicted = sum(max(score, 75) x weight), both rounded."""
    advisory = ADVISORY_PARAMETERS if advisory is None else advisory
    overall = predicted = 0.0
    for p in parameters:
        if p.get("name") in advisory:
            continue
        w = weights.get(p.get("name", ""), 0.0)
        s = p.get("score", 50)
        overall += s * w
        predicted += max(s, 75) * w
    return round(overall), round(predicted)


# ---------------------------------------------------------------- parameters

def _when(item: dict) -> str:
    a, b = item.get("t_start"), item.get("t_end")
    if a is None:
        return ""
    return f"{a:g}s" if b is None or abs(b - a) < 0.05 else f"{a:g}-{b:g}s"


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def numbered_steps(steps: list[str]) -> str:
    """The baseline's format: "Step 1 — ... Step 2 — ..." in one string."""
    return " ".join(f"Step {i} — {s.strip()}" for i, s in enumerate([s for s in steps if s and s.strip()], 1))


def build_parameters(analysis: dict, weights: dict[str, float], advisory: set[str] | None = None) -> list[dict]:
    """The 10 parameters in the baseline's order and shape, plus Studio's extras as additive fields."""
    advisory = ADVISORY_PARAMETERS if advisory is None else advisory
    items = {i["id"]: i for i in analysis.get("evidence_pack", {}).get("items", [])}
    by_name = {p["name"]: p for p in analysis.get("parameters", [])}
    out = []
    for name in BASELINE_ORDER:
        p = by_name.get(name)
        if p is None:
            continue
        is_advisory = name in advisory
        issues = p.get("issues") or []
        fixes = p.get("fixes") or []
        worst = max(enumerate(issues), key=lambda t: (t[1].get("severity", 1), -t[0]))[1] if issues else None
        problem = ""
        if worst:
            problem = worst["what"] + (f" (at {worst['at_s']:g}s)" if worst.get("at_s") is not None else "")
        seen, happening = set(), []
        for ref in p.get("evidence") or []:
            item = items.get(ref["id"])
            if item and ref["id"] not in seen:
                seen.add(ref["id"])
                when = _when(item)
                happening.append(f"[{when}] " if when else "")
                happening[-1] += _clip(item["text"], 180)
        what_is = " | ".join(happening[:4]) or "; ".join(p.get("strengths") or [])
        first = fixes[0] if fixes else {}
        change = "; ".join(f"{f['title']}" + (f" ({f['target']['start_s']:g}-{f['target']['end_s']:g}s)" if f.get("target") else "")
                           for f in fixes) or ("Keep: " + "; ".join(p.get("strengths") or []) if p.get("strengths") else "")
        score = p.get("score")
        param = {
            "name": name, "weight": 0 if is_advisory else round(weights.get(name, 0.0) * 100, 1),
            "score": 50 if score is None else int(score),  # the baseline's default for a missing score
            "advisory": is_advisory, "problem": problem, "fix": first.get("title", ""),
            "whatIsHappening": what_is, "whyItMatters": p.get("why_it_matters", ""), "whatToChange": change,
            "howToDoIt": numbered_steps(first.get("steps") or []), "whatToExpect": first.get("expected_effect", ""),
            # Studio's extras (additive; the page ignores what it does not know)
            "id": p["id"], "confidence": p.get("confidence"), "unsupported": bool(p.get("unsupported")),
            "evidence": [{"id": r["id"], "quote": r.get("quote", ""), "when": _when(items.get(r["id"], {}))} for r in (p.get("evidence") or [])],
            "fixable_by": first.get("fixable_by"), "evidence_limited": bool(p.get("evidence_limited")), "label": p.get("label"),
            "stable": p.get("stable", True), "range": p.get("range"), "min": p.get("min"), "max": p.get("max"),
            "strengths": p.get("strengths") or [],
        }
        out.append(param)
    return out


# ---------------------------------------------------------------- extras: energy curve, frames, hashtags

def build_energy_curve(rms_db: list[float]) -> dict | None:
    """The baseline's energyCurve shape (energy normalised to 0-1) from Studio's per-second RMS in dBFS."""
    if not rms_db:
        return None
    amp = [10 ** (db / 20) for db in rms_db]
    lo, hi = min(amp), max(amp)
    energy = [round((a - lo) / (hi - lo), 4) if hi > lo else 0.5 for a in amp]
    curve = [{"t": float(i), "energy": e} for i, e in enumerate(energy)]
    n = len(curve)
    avg = lambda xs, default: round(sum(xs) / len(xs), 4) if xs else default  # noqa: E731
    mean = avg(energy, 0.0)
    end = max(0.0, n - 3.0)
    low, start = [], None
    for p in curve + [{"t": float(n), "energy": 1.0}]:
        if p["energy"] < 0.4 and start is None:
            start = p["t"]
        elif p["energy"] >= 0.4 and start is not None:
            if p["t"] - start >= 2.0:
                seg = [x["energy"] for x in curve if start <= x["t"] < p["t"]]
                low.append({"start": start, "end": p["t"], "avg_energy": avg(seg, 0.0)})
            start = None
    return {"curve": curve, "low_energy_segments": low, "avg_energy": mean,
            "hook_energy": avg([p["energy"] for p in curve if p["t"] <= 3.0], mean),
            "mid_energy": avg([p["energy"] for p in curve if 3.0 < p["t"] < end], mean),
            "closing_energy": avg([p["energy"] for p in curve if p["t"] >= end], mean),
            "drop_detected": bool(low), "drop_at_seconds": low[0]["start"] if low else None, "duration": float(n)}


def extract_frames(video_path: str, duration: float, count: int = FRAME_COUNT) -> list[dict]:
    """Evenly spaced frames as {"timestamp", "base64"} at 768 px wide (blocking: run in a thread)."""
    frames = []
    n = max(1, min(count, int(duration) or 1))
    with tempfile.TemporaryDirectory(prefix="studio_frames_") as d:
        for i in range(n):
            t = round(min(duration - 0.1, duration * (i + 0.5) / n), 2) if duration > 0.2 else 0.0
            out = Path(d) / f"f{i:02d}.jpg"
            try:
                subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", str(t), "-i", video_path, "-frames:v", "1",
                                "-vf", f"scale={FRAME_WIDTH}:-1", "-q:v", "3", str(out)], check=True, timeout=30)
                frames.append({"timestamp": t, "base64": base64.b64encode(out.read_bytes()).decode()})
            except (subprocess.SubprocessError, OSError):
                continue
    return frames


def clean_hashtags(raw: Any, fallback: list[str]) -> list[str]:
    """Exactly five hashtags without '#': the model's, de-duplicated, topped up from the niches and platform if short."""
    tags, seen = [], set()
    for source in (raw if isinstance(raw, list) else [], fallback):
        for h in source:
            tag = re.sub(r"[^\w]", "", str(h).lstrip("#").replace(" ", ""), flags=re.UNICODE)
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                tags.append(tag)
            if len(tags) == HASHTAG_COUNT:
                return tags
    return tags


# ---------------------------------------------------------------- the extra text call

EXTRAS_PROMPT = """You write the creator-facing summary of a short-video analysis. Use ONLY the facts below; invent nothing.

CREATOR: niches {niches}; platform {platform}. Description (creator-provided, unverified): {description}
OVERALL: {overall}/100 ({label}). The score is already computed; do not restate a different number.

PARAMETERS (name | score | main problem | first fix):
{params}

EVIDENCE (ids and timestamps from the video itself):
{evidence}

SPOKEN OR SUNG WORDS (consensus transcript, may be empty): {transcript}
{trend_block}
Reply with ONE JSON object only (no prose, no code fences):
{{"summary": "2-3 sentences on what this video does well and what holds it back",
  "biggestProblem": "one sentence naming the single biggest thing to improve",
  "quickFix": "one sentence: the most impactful change doable in 5 minutes",
  "topPriorities": ["most impactful change", "second", "third"],
  "suggestedCaption": "3 to 5 sentences in the creator's voice, grounded in the transcript and the description; NO hashtags in it",
  "suggestedHashtags": ["tag1", "tag2", "tag3", "tag4", "tag5"]}}
Exactly 3 priorities and exactly 5 hashtags (no # sign). Every sentence must be supported by the facts above."""


def _fallback_extras(parameters: list[dict], creator: CreatorContext, overall: int, label: str) -> dict:
    """Used when the extra call fails: plain statements built from the analysis itself."""
    scored = sorted((p for p in parameters if not p["advisory"]), key=lambda p: p["score"])
    weakest = scored[0] if scored else None
    best = scored[-1] if scored else None
    fixes = [p["fix"] for p in scored if p["fix"]][:3]
    return {
        "summary": f"Scored {overall}/100 ({label})."
                   + (f" Strongest: {best['name']} ({best['score']})." if best else "")
                   + (f" Weakest: {weakest['name']} ({weakest['score']})." if weakest else ""),
        "biggestProblem": (weakest["problem"] or weakest["name"]) if weakest else "",
        "quickFix": fixes[0] if fixes else "", "topPriorities": fixes,
        "suggestedCaption": creator.description if creator.description else "", "suggestedHashtags": []}


def trend_block(trends) -> str:
    """The live trending hashtags for the summary prompt (empty unless the trends stage returned something)."""
    if trends is None or getattr(trends, "status", "") != "ok" or not trends.hashtags:
        return ""
    return ("\nLIVE TRENDING HASHTAGS (from a Google search today for the creator's niches): " + " ".join(trends.hashtags[:8]) +
            "\nUse the ones that genuinely fit this video among suggestedHashtags; never use a trending hashtag that does not fit it.\n")


def generate_extras(client: MetaClient, parameters: list[dict], analysis: dict, transcript: str, creator: CreatorContext,
                    platform: str, overall: int, label: str, trends=None) -> tuple[dict, float, str | None]:
    """One muse-spark-1.3 text call (blocking). Returns (extras, cost_usd, error)."""
    prompt = EXTRAS_PROMPT.format(trend_block=trend_block(trends),
        niches=", ".join(creator.niches) or "not given", platform=platform, description=creator.description or "not given",
        overall=overall, label=label,
        params="\n".join(f"- {p['name']} | {p['score']} | {_clip(p['problem'], 140) or 'none'} | {_clip(p['fix'], 100) or 'none'}" for p in parameters),
        evidence="\n".join(f"{i['id']} [{_when(i)}] {i['source']}: {_clip(i['text'], 200)}"
                           for i in analysis.get("evidence_pack", {}).get("items", []) if i["source"] != "creator context")[:4500],
        transcript=_clip(transcript, 1200) or "(none)")
    cost, error = 0.0, None
    for attempt in (1, 2):
        try:
            call = client.complete(prompt if attempt == 1 else prompt + "\n\nReply with the JSON object only.",
                                   studio_config.ANALYSIS_MODEL, temperature=0.3)
        except MetaError as e:
            return _fallback_extras(parameters, creator, overall, label), cost, f"summary call failed: {e}"
        cost += call.cost_usd
        try:
            data = parse_watch_json(call.text)
            return data, cost, None
        except ValueError as e:
            error = f"summary answer unparseable: {str(e)[:100]}"
    return _fallback_extras(parameters, creator, overall, label), cost, error


def finalize_extras(data: dict, parameters: list[dict], creator: CreatorContext, platform: str, overall: int, label: str) -> dict:
    """Coerce the model's extras to the baseline's types, falling back field by field."""
    fb = _fallback_extras(parameters, creator, overall, label)
    text = lambda k: str(data.get(k) or fb[k]).strip()  # noqa: E731
    priorities = [str(x).strip() for x in (data.get("topPriorities") or []) if str(x).strip()][:3] or fb["topPriorities"]
    caption = re.sub(r"\s*#\w+", "", text("suggestedCaption")).strip()  # no hashtags in the body
    pool = [re.sub(r"\W", "", n) for n in creator.niches] + [re.sub(r"\W", "", platform.split()[0]) if platform else "", "reels", "creator", "viral"]
    return {"summary": text("summary"), "biggestProblem": text("biggestProblem"), "quickFix": text("quickFix"),
            "topPriorities": priorities, "suggestedCaption": caption,
            "suggestedHashtags": clean_hashtags(data.get("suggestedHashtags"), pool)}


# ---------------------------------------------------------------- cost (the baseline CostTracker shape)

def cost_summary(report: Report, cached: bool, extra_items: list[dict]) -> dict:
    """{"items": [...], "total_usd": ...} from the real stage costs. A cached perception report cost nothing this run."""
    s = report.stages
    entries = [] if cached else [
        ("Watch (Spark)", s.watch.model or "muse-spark", s.watch.cost_usd),
        ("Speech (Voice Transcribe + Whisper)", "muse-voice-transcribe + whisper", s.speech.cost_usd),
        ("Subjects (SAM 3.1)", "sam-3.1", s.subjects.cost_usd),
        ("Audio listeners (Spark + Gemini)", "muse-spark + gemini", s.audio.cost_usd),
        ("Captions (Spark)", "muse-spark-1.3", s.captions.cost_usd),
        ("Signals (Modal GPU, approximate)", "modal", s.signals.cost_usd),
        ("Music ID (ACRCloud)", "acrcloud", s.music_id.cost_usd)]
    entries.append(("Live trends (Gemini Flash + Search, approximate)", trends_mod.MODEL, s.trends.cost_usd))  # fetched every run
    entries.append(("Analysis (scoring)", studio_config.ANALYSIS_MODEL, s.analysis.cost_usd))
    items = [{"label": label, "model": model, "cost_usd": round(cost, 6)} for label, model, cost in entries if cost]
    items += extra_items
    return {"items": items, "total_usd": round(sum(i["cost_usd"] for i in items), 6)}


def perception_cost(report: Report, cached: bool) -> float:
    """What this run spent on the perception stages (everything before the analysis)."""
    if cached:
        return 0.0
    s = report.stages
    return round(s.watch.cost_usd + s.speech.cost_usd + s.subjects.cost_usd + s.audio.cost_usd + s.captions.cost_usd
                 + s.signals.cost_usd + s.music_id.cost_usd, 6)


# ---------------------------------------------------------------- assembling the result

def build_result(report: Report, analysis: dict, creator: CreatorContext, platform: str, intent: str, source_type: str,
                 weights: dict[str, float], extras: dict | None, frames: list[dict], cached: bool, extra_items: list[dict],
                 post_metadata: dict | None = None, advisory: set[str] | None = None) -> dict:
    """The baseline-shaped result for the website (see the module docstring)."""
    parameters = build_parameters(analysis, weights, advisory)
    overall, predicted = baseline_scores(parameters, weights, advisory)
    label = score_label(overall, intent)
    fx = finalize_extras(extras or {}, parameters, creator, platform, overall, label)
    s = report.stages
    audio = {
        "transcript": s.consensus.consensus_text, "language": s.speech.whisper.language or s.speech.voice_transcribe.language_guess,
        "hasSpeech": bool(s.consensus.consensus_text), "segments": [q.model_dump() for q in s.consensus.segments],
        "primary_listener": s.speech.primary, "gemini": s.audio.gemini.model_dump(), "spark": s.audio.spark.model_dump(),
        "music": s.music_id.model_dump(), "loudness_lufs": s.signals.audio_features.integrated_loudness_lufs,
        "tempo_bpm": s.signals.audio_features.tempo_bpm, "voice_to_music_db": s.signals.demucs.voice_to_music_db,
        "silence_ratio": s.signals.audio_features.silence_ratio, "cuts_per_10s": s.signals.cuts.cuts_per_10s}
    fit = analysis.get("description_fit")
    result: dict[str, Any] = {
        "parameters": parameters, "overallScore": overall, "scoreLabel": label, "predictedScoreAfterFixes": predicted,
        **fx, "niche": ", ".join(creator.niches) or analysis["niche"]["niche"], "platform": platform,
        "intent": intent, "source_type": source_type,
        "transcript": s.consensus.consensus_text, "audioAnalysis": audio, "frames": frames,
        "first_frame_b64": frames[0]["base64"] if frames else None,
        "descriptionFit": ({"verdict": fit["verdict"], "note": fit["note"], "evidence": [e["id"] for e in fit["evidence"]],
                            "unsupported": fit["unsupported"]} if fit else None),
        "_cost": cost_summary(report, cached, extra_items),
        "studio": {
            "engine": "prysm-studio", "schema_version": analysis.get("schema_version"), "report_schema": report.schema_version,
            "niche": analysis["niche"], "niche_weights": analysis["niche_weights"], "creator_context": analysis.get("creator_context"),
            "overall_studio": analysis["overall"], "flags": analysis["flags"], "edit_intents": analysis["edit_intents"],
            "stability": analysis["stability"], "perception_cached": cached, "evidence_pack": analysis["evidence_pack"],
            "music": {"matched": s.music_id.matched, "track": s.music_id.track.model_dump() if s.music_id.track else None},
            "trends": s.trends.model_dump(mode="json"), "models_used": models_used(report),
            "stages": {n: getattr(s, n).status for n in ("watch", "speech", "subjects", "audio", "consensus", "captions",
                                                         "signals", "music_id", "trends", "analysis")}},
        "_debug": {"engine": "studio", "hasAudio": bool(s.consensus.consensus_text) or s.audio.status in ("ok", "partial"),
                   "framesSaved": len(frames), "language": audio["language"], "perceptionCached": cached},
    }
    curve = build_energy_curve(s.signals.audio_features.rms_curve_1hz)
    if curve:
        result["energyCurve"] = curve
    if post_metadata:
        result["postMetadata"] = post_metadata
    return result


def models_used(report: Report) -> list[dict]:
    """The models behind this analysis, one row per step, for the results page's "Models used" panel."""
    s = report.stages
    trend_note = {"ok": f"{len(s.trends.sources)} sources" + (", cached" if s.trends.cached else ""), "unavailable": "not available this time"}
    return [
        {"step": "Watching the video", "model": f"{s.watch.model or studio_config.watch_model()} (Meta)", "status": s.watch.status},
        {"step": "Speech", "model": "Voice Transcribe (Meta) + Whisper large-v3-turbo (Groq)", "status": s.speech.status},
        {"step": "People and objects", "model": "SAM 3.1 (Meta)", "status": s.subjects.status},
        {"step": "Audio", "model": f"{studio_config.SPARK_AUDIO_MODEL} (Meta) + Gemini", "status": s.audio.status},
        {"step": "Visual and audio signals", "model": "DINOv2, V-JEPA 2, LanguageBind, EnCodec, CLAP, Demucs (GPU)", "status": s.signals.status},
        {"step": "Music", "model": "ACRCloud", "status": s.music_id.status},
        {"step": "Live trends", "model": "Gemini 2.5 Flash + Google Search", "status": s.trends.status,
         "note": trend_note.get(s.trends.status, "")},
        {"step": "Captions", "model": f"{studio_config.CAPTION_MODEL} (Meta)", "status": s.captions.status},
        {"step": "Scoring and fixes", "model": f"{studio_config.ANALYSIS_MODEL} (Meta)", "status": s.analysis.status},
        {"step": "Summary and chat (ARIA)", "model": f"{studio_config.ARIA_MODEL} (Meta)", "status": "ok"},
    ]


def cache_view(result: dict) -> dict:
    """What the baseline router writes to Redis: the result without frames, audioAnalysis, transcript and _cost."""
    view = {k: v for k, v in result.items() if k not in ("frames", "audioAnalysis", "transcript", "_cost")}
    return view


def shrink_for_cache(result: dict, limit: int = CACHE_LIMIT_BYTES) -> dict:
    """Keep the Redis value under the limit by dropping Studio's heaviest additive fields, least useful first."""
    def size() -> int:
        return len(json.dumps(cache_view(result), default=str))

    studio = result.get("studio", {})
    for step in (lambda: studio.pop("evidence_pack", None), lambda: studio.pop("stability", None),
                 lambda: [p.pop("evidence", None) for p in result["parameters"]],
                 lambda: [p.pop("strengths", None) for p in result["parameters"]],
                 lambda: result.pop("energyCurve", None), lambda: studio.pop("edit_intents", None)):
        if size() <= limit:
            break
        step()
    return result


# ---------------------------------------------------------------- trends cache (Redis, shared by every backend process)

class TrendsRedisCache:
    """The baseline's JSON cache helpers behind the get/set interface trends.py expects (it falls back to memory if Redis fails)."""

    async def get(self, key: str):
        from app.core.cache import cache_get
        return await cache_get(key)

    async def set(self, key: str, value: dict, ttl_s: int) -> None:
        from app.core.cache import cache_set
        await cache_set(key, value, ttl=ttl_s)


# ---------------------------------------------------------------- progress (Redis, additive to the status endpoint)

class Progress:
    """Collects {stage: status} and writes it to Redis under prysm:progress:{job_id} as stages report in."""

    def __init__(self, job_id: str | None):
        self.job_id = job_id
        self.state: dict[str, str] = {}
        self._writes: list[asyncio.Future] = []

    def __call__(self, stage: str, status: str) -> None:
        self.state[stage] = status
        if self.job_id:
            self._writes.append(asyncio.ensure_future(self._write(dict(self.state))))

    async def _write(self, snapshot: dict) -> None:
        try:
            from app.core.cache import cache_set
            await cache_set(f"prysm:progress:{self.job_id}", snapshot, ttl=PROGRESS_TTL)
        except Exception as e:  # progress is a nicety: never break the analysis for it
            log.debug("progress write failed: %s", e)

    async def flush(self) -> None:
        if self._writes:
            await asyncio.gather(*self._writes, return_exceptions=True)


# ---------------------------------------------------------------- the entry point

_semaphore: asyncio.Semaphore | None = None
_semaphore_size = 0


def _limiter() -> asyncio.Semaphore:
    """Studio analyses running at once in this process (STUDIO_MAX_CONCURRENT, default 2)."""
    global _semaphore, _semaphore_size
    size = max(1, int(settings.STUDIO_MAX_CONCURRENT))
    if _semaphore is None or _semaphore_size != size:
        _semaphore, _semaphore_size = asyncio.Semaphore(size), size
    return _semaphore


def studio_limits() -> Limits:
    """The safety-net limits from the settings (the only place they are read, so the routers pass one object around)."""
    return Limits(daily_spend_cap_usd=settings.STUDIO_DAILY_SPEND_CAP_USD, user_max_concurrent=settings.STUDIO_USER_MAX_CONCURRENT,
                  user_hourly_limit=settings.STUDIO_USER_HOURLY_LIMIT, register_per_ip_hourly=settings.STUDIO_REGISTER_PER_IP_HOURLY,
                  max_concurrent=settings.STUDIO_MAX_CONCURRENT)


def studio_base_dir() -> Path:
    return Path(settings.STUDIO_WORK_DIR) if settings.STUDIO_WORK_DIR else Path(tempfile.gettempdir()) / "prysm_studio"


def probe_duration(video_path: str) -> float | None:
    try:
        p = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path],
                           capture_output=True, text=True, timeout=15)
        return float(json.loads(p.stdout).get("format", {}).get("duration", 0))
    except Exception:
        return None


def check_duration(video_path: str, limit: int | None = None) -> float | None:
    """The video's duration; raises StudioVideoTooLong over the limit (90 s by default)."""
    limit = limit or int(settings.STUDIO_MAX_VIDEO_SECONDS)
    duration = probe_duration(video_path)
    if duration is not None and duration > limit + 0.5:
        raise StudioVideoTooLong(limit)
    return duration


async def run_studio_analysis(
    video_path: str,
    niche: str,
    platform: str,
    brief: str = "",
    content_goal: str = "",
    post_metadata: dict | None = None,
    intent: str = "my_video",
    source_type: str = "upload",
    credit_checker: Callable[[float], Awaitable[bool]] | None = None,
    job_id: str | None = None,
) -> dict:
    """Run the Studio pipeline and return the baseline's result shape (same arguments as run_full_analysis).

    `niche` is the website's comma-joined niche list and `brief` the creator's description. credit_checker
    works as in the baseline: it is called once the perception stages are done (with their cost this run) and,
    if it returns True, CreditExhaustedError stops the analysis before the scoring.
    """
    from app.services.prysm_analyse_service import CreditExhaustedError  # baseline class: the router catches this one

    duration = await asyncio.to_thread(check_duration, video_path)  # raises StudioVideoTooLong
    creator = CreatorContext.from_inputs(niche, brief)
    weights = baseline_weights(creator.primary)
    progress = Progress(job_id)
    client = MetaClient()
    state: dict[str, Any] = {}

    async def after_perception(report: Report) -> None:
        if credit_checker is None:
            return
        cost = perception_cost(report, state.get("cached", False))
        if await credit_checker(cost):
            raise CreditExhaustedError(stage1_cost_usd=cost)

    async with _limiter():
        base = studio_base_dir()
        out_root = base / "jobs" / (job_id or uuid.uuid4().hex)
        out_root.mkdir(parents=True, exist_ok=True)

        def on_progress(stage: str, status: str) -> None:
            if status == "cached":
                state["cached"] = True
            progress(stage, status)

        result = await analyze_video(
            Path(video_path), out_root, [studio_config.watch_model()], client=client, creator=creator,
            weights_override=weights_by_id(weights), progress=on_progress, use_cache=True, cache_root=base,
            platform=platform, trends_cache=TrendsRedisCache(), after_perception=after_perception)
    report = result.reports[0]
    cached = result.cached
    stage = report.stages.analysis
    if stage.status != "ok" or not stage.analysis_file:
        await progress.flush()
        raise RuntimeError(f"Prysm analysis failed: {stage.error or 'no analysis produced'}")
    analysis = json.loads(Path(stage.analysis_file).read_text())

    progress("summary", "running")
    parameters = build_parameters(analysis, weights)
    overall, _ = baseline_scores(parameters, weights)
    label = score_label(overall, intent)
    transcript = report.stages.consensus.consensus_text
    extras, extras_cost, extras_error = await asyncio.to_thread(
        generate_extras, client, parameters, analysis, transcript, creator, platform, overall, label, report.stages.trends)
    progress("summary", "ok" if not extras_error else "error")
    frames = await asyncio.to_thread(extract_frames, video_path, duration or report.video.duration_s)
    extra_items = [{"label": "Summary, caption and hashtags", "model": studio_config.ANALYSIS_MODEL,
                    "cost_usd": round(extras_cost, 6)}] if extras_cost else []
    out = build_result(report, analysis, creator, platform, intent, source_type, weights, extras, frames, cached, extra_items,
                       post_metadata)
    if extras_error:
        out["_debug"]["summaryError"] = extras_error
    await progress.flush()
    return shrink_for_cache(out)
