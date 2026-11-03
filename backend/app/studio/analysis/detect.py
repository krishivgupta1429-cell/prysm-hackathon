"""Hackathon-built (Meta Global AI Developer Hackathon).

Niche detection: muse-spark-1.3 (text only, through meta_client) reads the evidence pack and returns
{niche, confidence 0-1, runner_up, reasons: [evidence ids]}. Below 0.5 confidence the niche falls back to
"other". A niche_override (CLI --niche) skips the model entirely. One retry on an unparseable answer; if the
call fails the niche is "other" and the error is returned for the caller to flag.
"""
from dataclasses import dataclass

from pydantic import ValidationError

from app.studio import config
from app.studio.analysis.evidence import EvidencePack
from app.studio.analysis.niches import NICHE_IDS, NICHES
from app.studio.analysis.schema import NicheDetection
from app.studio.meta_client import MetaClient, MetaError
from app.studio.stages.watch import parse_watch_json

MIN_CONFIDENCE = 0.5

PROMPT = """Classify this short vertical social video into exactly one niche. Use ONLY the evidence below.

NICHES:
{niches}

EVIDENCE (each line starts with its id):
{evidence}

Reply with ONE JSON object and nothing else (no prose, no code fences):
{{"niche": "<one niche id>", "confidence": <0.0-1.0>, "runner_up": "<another niche id>", "reasons": ["E3", "E7"]}}
"reasons" are the evidence ids that decided it. Use "other" with low confidence if nothing fits clearly."""


@dataclass
class DetectOutcome:
    detection: NicheDetection
    cost_usd: float = 0.0
    error: str | None = None


def validate_override(niche_id: str) -> str:
    if niche_id not in NICHES:
        raise ValueError(f"unknown niche {niche_id!r}; choose one of: {', '.join(NICHE_IDS)}")
    return niche_id


def interpret(raw: dict, valid_ids: set[str]) -> NicheDetection:
    """A parsed model answer -> NicheDetection, applying the confidence fallback and dropping bad ids."""
    niche = str(raw.get("niche", "")).strip().lower()
    if niche not in NICHES:
        niche = "other"
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    runner = str(raw.get("runner_up") or "").strip().lower()
    reasons = [str(r).strip().strip("[]").upper() for r in (raw.get("reasons") or []) if str(r).strip().strip("[]").upper() in valid_ids]
    if confidence < MIN_CONFIDENCE and niche != "other":
        return NicheDetection(niche="other", confidence=confidence, runner_up=niche, reasons=reasons, detected=niche,
                              source="fallback")
    return NicheDetection(niche=niche, confidence=confidence, runner_up=runner if runner in NICHES and runner != niche else None,
                          reasons=reasons, detected=niche, source="model")


def detect_niche(pack: EvidencePack, client: MetaClient, niche_override: str | None = None,
                 model: str = config.ANALYSIS_MODEL) -> DetectOutcome:
    if niche_override:
        return DetectOutcome(NicheDetection(niche=validate_override(niche_override), confidence=1.0, source="override"))
    prompt = PROMPT.format(niches="\n".join(f"- {n.id}: {n.description}" for n in NICHES.values()), evidence=pack.render())
    cost, error = 0.0, None
    for attempt in (1, 2):
        try:
            call = client.complete(prompt if attempt == 1 else prompt + "\n\nReply with the JSON object only.", model,
                                   temperature=config.ANALYSIS_TEMPERATURE)
        except MetaError as e:
            return DetectOutcome(NicheDetection(niche="other", source="fallback"), cost, f"niche detection call failed: {e}")
        cost += call.cost_usd
        try:
            return DetectOutcome(interpret(parse_watch_json(call.text), pack.ids()), cost)
        except (ValueError, ValidationError) as e:
            error = f"niche detection answer unparseable: {str(e)[:100]}"
    return DetectOutcome(NicheDetection(niche="other", source="fallback"), cost, error)
