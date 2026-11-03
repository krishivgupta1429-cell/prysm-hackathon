"""Built during the Meta Global AI Developer Hackathon (new work).

Aggregation of repeated scoring runs (the analysis stage scores twice by default).

Per parameter: the score is the mean of the runs that gave a usable score (rounded half up), with min, max
and spread kept. A spread of more than 10 points sets "stable": false and a "range" [min, max]. Everything
else (evidence, issues, fixes, confidence, why_it_matters) comes from the run whose evidence for that
parameter was best (highest confidence, then most valid evidence refs, then the earliest run), so the fixes
stay consistent with the evidence they cite. The overall score is then computed from these aggregated scores.
"""
import math
from statistics import fmean

from app.studio.analysis.schema import Parameter

UNSTABLE_SPREAD = 10  # points
CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def _usable(p: Parameter) -> bool:
    return p.score is not None and not p.unsupported


def aggregate_runs(runs: list[list[Parameter]]) -> list[Parameter]:
    """Combine validated runs (each in registry order) into one list of parameters."""
    if len(runs) == 1:
        p_list = [p.model_copy(deep=True) for p in runs[0]]
        for p in p_list:
            if _usable(p):
                p.min = p.max = p.score
                p.spread, p.runs_scored = 0, 1
        return p_list
    out = []
    for i in range(len(runs[0])):
        candidates = [run[i] for run in runs]
        pool = [p for p in candidates if _usable(p)] or candidates
        best = max(enumerate(pool), key=lambda t: (CONFIDENCE_RANK.get(t[1].confidence, 0), len(t[1].evidence), -t[0]))[1]
        agg = best.model_copy(deep=True)
        scores = [p.score for p in candidates if _usable(p)]
        if scores:
            agg.score = int(math.floor(fmean(scores) + 0.5))
            agg.min, agg.max, agg.spread, agg.runs_scored = min(scores), max(scores), max(scores) - min(scores), len(scores)
            agg.stable = agg.spread <= UNSTABLE_SPREAD
            agg.range = None if agg.stable else [agg.min, agg.max]
        else:  # no run could score it: it stays unsupported
            agg.score, agg.unsupported = None, agg.scored
        out.append(agg)
    return out
