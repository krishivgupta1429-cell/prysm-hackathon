"""Hackathon-built (Meta Global AI Developer Hackathon).

The overall score, computed in code and never by the model: the weighted mean of the 6 scored parameters
using the niche weights, rounded to one decimal, plus the band. The 4 advisory parameters are shown but
not scored. A scored parameter with no score or marked unsupported is left out and the remaining weights
are re-normalised (the result says what was excluded and how much of the niche weight was used).
"""
from app.studio.analysis.niches import Niche
from app.studio.analysis.schema import OverallScore, Parameter

BANDS = [(85, "Viral-Ready"), (70, "Strong"), (50, "Developing"), (0, "Needs Work")]


def band_for(score: float) -> str:
    """Needs Work 0-49, Developing 50-69, Strong 70-84, Viral-Ready 85-100."""
    for low, name in BANDS:
        if score >= low:
            return name
    return BANDS[-1][1]


def compute_overall(params: list[Parameter], niche: Niche) -> OverallScore:
    usable = {p.id: p for p in params if p.scored and p.score is not None and not p.unsupported and p.id in niche.weights}
    excluded = [pid for pid in niche.weights if pid not in usable]
    total = sum(niche.weights[pid] for pid in usable)
    if not usable or total <= 0:
        return OverallScore(excluded=excluded)
    score = sum(usable[pid].score * niche.weights[pid] for pid in usable) / total
    return OverallScore(score=round(score, 1), band=band_for(round(score, 1)),
                        weights={pid: round(niche.weights[pid] / total, 4) for pid in usable},
                        excluded=excluded, coverage=round(total / sum(niche.weights.values()), 4))
