"""Built during the Meta Global AI Developer Hackathon (new work).

Analysis stage (Stage 1D): runs after consensus, captions, signals and music_id, on the finished report.

  evidence pack -> niche -> scoring run(s) -> validation -> overall score (computed in code from the niche
  weights) -> edit intents

The niche is the creator's: with a CreatorContext (niche list + description) the first niche is the primary
one, its weights follow the baseline lookup (weights_override from the adapter) and the model is NOT asked to
detect a niche. Without one (the CLI) the model detects it, unless a Studio niche id is given.
The description is judged against the evidence (description_fit) but is never evidence itself.

The scoring call runs --repeat times (default 2) in parallel. Each parameter is the mean of the runs (rounded)
with min/max/spread kept; a spread over 10 points sets "stable": false and a "range" and is flagged
(aggregate.py). Evidence, issues and fixes come from the run with the best evidence for that parameter, and
the overall score is computed from the aggregated scores.
With --with-frames up to 6 keyframes (0, 1, 2, 3 s, the middle and the last frame) are attached to the scoring
call. Writes analysis.json (schema_version "1.0") and a compact summary into the report.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.studio import config
from app.studio.analysis.aggregate import UNSTABLE_SPREAD, aggregate_runs
from app.studio.analysis.creator import CreatorContext, resolve_niche
from app.studio.analysis.detect import detect_niche
from app.studio.analysis.evidence import build_evidence_pack
from app.studio.analysis.intents import build_edit_intents
from app.studio.analysis.niches import get_niche
from app.studio.analysis.overall import compute_overall
from app.studio.analysis.schema import ANALYSIS_SCHEMA_VERSION, Flag, NicheDetection, Stability
from app.studio.analysis.score import score_once
from app.studio.analysis.validate import validate_description_fit, validate_parameters
from app.studio.meta_client import MetaClient
from app.studio.report import AnalysisStage, ParamScore, Report


def keyframes(frames_dir: Path | None, limit: int = 6) -> list[bytes]:
    """The 1 fps frames at 0, 1, 2, 3 s, the middle and the last one (deduplicated, at most `limit`)."""
    files = sorted(frames_dir.glob("*.jpg")) if frames_dir and frames_dir.exists() else []
    if not files:
        return []
    picks = list(dict.fromkeys(i for i in (0, 1, 2, 3, len(files) // 2, len(files) - 1) if i < len(files)))
    return [files[i].read_bytes() for i in picks[:limit]]


def stability(runs_scores: list[dict[str, int]], run_overalls: list[float | None]) -> Stability:
    """Per-parameter min/max/spread across runs, the overall spread, and the parameters spread over 10 points."""
    st = Stability(runs=len(runs_scores), run_overalls=run_overalls)
    for pid in sorted({k for r in runs_scores for k in r}):
        vals = [r[pid] for r in runs_scores if pid in r]
        st.per_parameter[pid] = {"min": min(vals), "max": max(vals), "spread": max(vals) - min(vals)}
        if len(vals) > 1 and max(vals) - min(vals) > UNSTABLE_SPREAD:
            st.unstable.append(pid)
    known = [o for o in run_overalls if o is not None]
    if known:
        st.overall = {"min": min(known), "max": max(known), "spread": round(max(known) - min(known), 1)}
    return st


def run_analysis(report: Report, client: MetaClient, analysis_path: Path, niche_override: str | None = None,
                 repeat: int = 2, with_frames: bool = False, frames_dir: Path | None = None,
                 creator: CreatorContext | None = None, weights_override: dict[str, float] | None = None) -> AnalysisStage:
    start = time.time()
    stage = AnalysisStage()
    try:
        pack = build_evidence_pack(report, creator)
        flags: list[Flag] = []
        if creator is not None and creator.niches:  # the creator's niche: no model detection on this path
            niche_detection = NicheDetection(niche=creator.primary, confidence=1.0, source="creator")
            niche = resolve_niche(creator, weights_override)
            cost = 0.0
        else:
            detect = detect_niche(pack, client, niche_override)
            niche_detection = detect.detection
            niche = get_niche(niche_detection.niche)
            cost = detect.cost_usd
            if detect.error:
                flags.append(Flag(code="niche_detection_failed", detail=detect.error + "; used 'other'"))
        if niche_detection.source == "fallback" and niche_detection.detected:
            flags.append(Flag(code="low_niche_confidence", detail=f"{niche_detection.detected} at "
                                                                 f"{niche_detection.confidence:.2f} < 0.5; used 'other'"))
        images = keyframes(frames_dir) if with_frames else None
        if with_frames and not images:
            flags.append(Flag(code="no_keyframes", detail="--with-frames but no frames were available"))

        repeat = max(1, repeat)
        with ThreadPoolExecutor(max_workers=min(repeat, 4)) as pool:
            outcomes = list(pool.map(lambda _: score_once(client, pack, niche, images=images, creator=creator), range(repeat)))
        cost += sum(o.cost_usd for o in outcomes)

        runs = []
        validated = []  # (parameters, flags, overall) per successful run
        fits = []  # (valid evidence count, run index, description_fit) per run that gave one
        for o in outcomes:
            if o.parameters is None:
                runs.append({"error": o.error, "cost_usd": o.cost_usd, "attempts": o.attempts})
                continue
            params, run_flags = validate_parameters(o.parameters, pack)
            if creator is not None and creator.description:
                fit, fit_flags = validate_description_fit(o.description_fit, pack)
                run_flags += fit_flags
                if fit is not None:
                    fits.append((len(fit.evidence), len(fits), fit))
            overall = compute_overall(params, niche)
            validated.append((params, run_flags, overall))
            runs.append({"overall": overall.score, "scores": {p.id: p.score for p in params}, "cost_usd": o.cost_usd,
                         "attempts": o.attempts, "tokens_in": o.tokens_in, "tokens_out": o.tokens_out})
        if not validated:
            stage.status, stage.error = "error", "; ".join(r["error"] for r in runs if r.get("error"))[:300]
            stage.cost_usd = round(cost, 6)
            stage.seconds = round(time.time() - start, 2)
            return stage

        params = aggregate_runs([v[0] for v in validated])  # mean score per parameter, evidence/fixes from the best run
        overall = compute_overall(params, niche)  # from the aggregated scores
        seen = set()
        for _, run_flags, _ in validated:  # every run's validator flags, each only once
            for f in run_flags:
                if (f.parameter, f.code, f.detail) not in seen:
                    seen.add((f.parameter, f.code, f.detail))
                    flags.append(f)
        intents = build_edit_intents(params, niche)
        stab = stability([{k: v for k, v in r["scores"].items() if v is not None} for r in runs if "scores" in r],
                         [r["overall"] for r in runs if "scores" in r])
        for pid in stab.unstable:
            s = stab.per_parameter[pid]
            flags.append(Flag(parameter=pid, code="unstable_score",
                              detail=f"{s['min']}-{s['max']} over {stab.runs} runs (spread {s['spread']} > {UNSTABLE_SPREAD})"))
        if stab.overall.get("spread", 0) > UNSTABLE_SPREAD:
            flags.append(Flag(code="unstable_overall", detail=f"overall spread {stab.overall['spread']} > {UNSTABLE_SPREAD}"))

        best_fit = max(fits, key=lambda t: (t[0], -t[1]))[2] if fits else None  # most valid evidence, earliest run
        if creator is not None and creator.description and best_fit is None:
            flags.append(Flag(parameter="description_fit", code="missing", detail="the model did not return a description_fit"))
        analysis = {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "video": report.video.model_dump(),
            "model": config.ANALYSIS_MODEL, "temperature": config.ANALYSIS_TEMPERATURE, "with_frames": bool(images),
            "niche": niche_detection.model_dump(),
            "creator_context": ({"niches": creator.niches, "primary": creator.primary, "extra_niches_context_only": creator.extras,
                                 "description": creator.description,
                                 "status": "creator-provided, unverified; never evidence for a score"} if creator else None),
            "description_fit": best_fit.model_dump() if best_fit else None,
            "niche_weights": {"weights": niche.weights, "label": niche.weights_label,
                              "judging_notes": "hand-written heuristics, not data-derived"},
            "evidence_pack": pack.as_dict(),
            "parameters": [p.model_dump() for p in params],
            "overall": overall.model_dump(),
            "flags": [f.model_dump() for f in flags],
            "edit_intents": [i.model_dump() for i in intents],
            "stability": stab.model_dump(),
            "runs": runs,
            "cost_usd": round(cost, 6),
        }
        analysis_path.parent.mkdir(parents=True, exist_ok=True)
        analysis_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False))

        stage.status = "ok"
        stage.niche, stage.niche_confidence, stage.niche_source = niche_detection.niche, niche_detection.confidence, niche_detection.source
        stage.niches = list(creator.niches) if creator else [niche_detection.niche]
        stage.description_fit = ({"verdict": best_fit.verdict, "note": best_fit.note, "evidence": [e.id for e in best_fit.evidence],
                                  "unsupported": best_fit.unsupported} if best_fit else None)
        stage.overall_score, stage.band = overall.score, overall.band
        stage.parameters = [ParamScore(id=p.id, name=p.name, scored=p.scored, score=p.score, confidence=p.confidence,
                                       unsupported=p.unsupported, evidence_limited=p.evidence_limited, label=p.label,
                                       min=p.min, max=p.max, spread=p.spread, stable=p.stable, range=p.range)
                            for p in params]
        stage.unstable_parameters = stab.unstable
        stage.overall_spread = stab.overall.get("spread")
        stage.runs, stage.intent_count = stab.runs, len(intents)
        stage.flags = [f.text() for f in flags]
        stage.analysis_file = str(analysis_path)
        stage.cost_usd = round(cost, 6)
    except Exception as e:  # never raise into the pipeline
        stage.status, stage.error = "error", config.redact(f"{type(e).__name__}: {e}")[:300]
    stage.seconds = round(time.time() - start, 2)
    return stage
