"""Built during the Meta Global AI Developer Hackathon (new work).

The overall score and the "after fixes" projection, computed over the six scored parameters with their weights
normalised to sum to 1.

Why: the baseline niche weights are normalised across all ten parameters (they add to 1.0), but only six parameters
are scored, so those six add to about 0.6 and the weighted sum topped out near 61 (six parameters at 70 gave 42).
The original production results use weights that sum to 100 over the six scored parameters (their stored overall is the
weighted mean), so this restores that behaviour. This is a hackathon-repo change to baseline behaviour.
"""
from typing import Dict, Iterable

from app.core.config import SCORED_PARAMETERS

FIX_TARGET = 75  # the baseline's "after fixes" assumption: every weak scored parameter reaches 75


def normalise_scored_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """The six scored parameters' weights rescaled to sum to 1; advisory parameters are left out."""
    raw = {n: float(weights.get(n, 0.0) or 0.0) for n in SCORED_PARAMETERS}
    total = sum(raw.values())
    if total <= 0:
        return {n: 1.0 / len(SCORED_PARAMETERS) for n in SCORED_PARAMETERS}
    return {n: w / total for n, w in raw.items()}


def overall_and_predicted(params: Iterable[dict], weights: Dict[str, float]) -> tuple:
    """(overallScore, predictedScoreAfterFixes) as rounded ints, using the same normalised weights for both."""
    nw = normalise_scored_weights(weights)
    overall = predicted = 0.0
    for p in params:
        if p.get("advisory", False):
            continue
        w = nw.get(p.get("name", ""), 0.0)
        s = p.get("score", 50)
        overall += s * w
        predicted += max(s, FIX_TARGET) * w
    return round(overall), round(predicted)
