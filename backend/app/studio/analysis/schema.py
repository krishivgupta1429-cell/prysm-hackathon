"""Hackathon-built (Meta Global AI Developer Hackathon).

Pydantic models for the model's scoring output and for analysis.json (schema_version "1.0").
The parsers are lenient about types (a model may return "3" or "0:03") and strict about the structure;
the content is checked afterwards in validate.py.
"""
from typing import Literal

from pydantic import BaseModel, field_validator

from app.studio.report import to_seconds

ANALYSIS_SCHEMA_VERSION = "1.0"
FIXABLE_BY = ("trim", "reorder", "caption", "text_overlay", "audio_balance", "music_swap", "speed_ramp", "zoom_crop",
              "rotate", "hook_rewrite", "reshoot", "none")  # rotate = an FFmpeg transpose fix for sideways / upside-down reels
NOT_AUTOMATABLE = ("reshoot", "none")  # an editor cannot do these
Confidence = Literal["low", "medium", "high"]


class EvidenceRef(BaseModel):
    id: str
    quote: str = ""

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, v):
        return str(v).strip().strip("[]").upper()


class Issue(BaseModel):
    what: str
    at_s: float | None = None
    severity: int = 1  # 1 minor .. 3 major

    @field_validator("at_s", mode="before")
    @classmethod
    def _at(cls, v):
        return None if v in (None, "", "null") else to_seconds(v)

    @field_validator("severity", mode="before")
    @classmethod
    def _sev(cls, v):
        return max(1, min(3, round(float(v)))) if v not in (None, "") else 1


class Target(BaseModel):
    start_s: float
    end_s: float

    _secs = field_validator("start_s", "end_s", mode="before")(lambda cls, v: to_seconds(v))


class Fix(BaseModel):
    title: str
    steps: list[str] = []
    expected_effect: str = ""
    fixable_by: str = "none"
    target: Target | None = None
    auto_applicable: bool = False

    @field_validator("steps", mode="before")
    @classmethod
    def _steps(cls, v):
        return [] if v is None else ([v] if isinstance(v, str) else v)

    @field_validator("target", mode="before")
    @classmethod
    def _target(cls, v):
        if isinstance(v, Target):
            return v
        return v if isinstance(v, dict) and v.get("start_s") is not None and v.get("end_s") is not None else None


class Parameter(BaseModel):
    id: str
    name: str = ""
    scored: bool = True
    score: int | None = None
    confidence: Confidence = "low"
    evidence: list[EvidenceRef] = []
    why_it_matters: str = ""
    strengths: list[str] = []
    issues: list[Issue] = []
    fixes: list[Fix] = []
    unsupported: bool = False  # set by validate.py: a scored parameter with no valid evidence
    evidence_limited: bool = False  # True for Trend Alignment: no live trend data in this version
    label: str | None = None  # "estimate (no live trend data)" for Trend Alignment
    # filled by aggregate.py when the scoring ran more than once
    min: int | None = None
    max: int | None = None
    spread: int | None = None
    stable: bool = True  # False when the runs differ by more than 10 points
    range: list[int] | None = None  # [min, max], only when not stable
    runs_scored: int = 0

    @field_validator("score", mode="before")
    @classmethod
    def _score(cls, v):
        return None if v in (None, "", "null") else round(float(v))

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        v = str(v or "low").strip().lower()
        return v if v in ("low", "medium", "high") else "low"

    @field_validator("strengths", "evidence", "issues", "fixes", mode="before")
    @classmethod
    def _lists(cls, v):
        return [] if v is None else v


class DescriptionFit(BaseModel):
    """Does the video match the description the creator gave? Judged from evidence, like every other claim."""
    verdict: Literal["matches", "partial", "off"] = "partial"
    note: str = ""
    evidence: list[EvidenceRef] = []
    unsupported: bool = False  # set by validate.py: no valid evidence behind the verdict

    @field_validator("verdict", mode="before")
    @classmethod
    def _verdict(cls, v):
        mapping = {"matches": "matches", "match": "matches", "matched": "matches", "partial": "partial",
                   "partially": "partial", "off": "off", "mismatch": "off", "no": "off"}
        return mapping.get(str(v or "").strip().lower(), "partial")

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence(cls, v):
        return [] if v is None else v


class ScoringOutput(BaseModel):
    parameters: list[Parameter]
    description_fit: DescriptionFit | None = None

    @field_validator("description_fit", mode="before")
    @classmethod
    def _fit(cls, v):
        return v if isinstance(v, dict) else None


class NicheDetection(BaseModel):
    niche: str  # the niche used downstream (after the confidence fallback or an override)
    confidence: float = 0.0
    runner_up: str | None = None
    reasons: list[str] = []  # evidence ids
    detected: str | None = None  # what the model said before a low-confidence fallback to "other"
    source: Literal["model", "override", "fallback", "creator"] = "model"  # creator = the niche the creator picked


class OverallScore(BaseModel):
    score: float | None = None
    band: str | None = None
    weights: dict[str, float] = {}  # normalised weights actually used
    excluded: list[str] = []  # scored parameters left out (no score or unsupported)
    coverage: float = 0.0  # share of the niche's weight that was used


class Stability(BaseModel):
    runs: int = 0
    per_parameter: dict[str, dict[str, float]] = {}  # id -> {min, max, spread}
    overall: dict[str, float] = {}
    unstable: list[str] = []  # spread over 10 points
    run_overalls: list[float | None] = []


class Flag(BaseModel):
    parameter: str | None = None
    code: str
    detail: str = ""

    def text(self) -> str:
        return f"{self.parameter or 'analysis'}: {self.code}" + (f" ({self.detail})" if self.detail else "")


class EditIntent(BaseModel):
    intent: str
    target: Target | None = None
    parameters: dict = {}
    source_parameter: str
    rationale: str = ""
    evidence: list[str] = []
    rank_score: float = 0.0  # severity x the parameter's niche weight
