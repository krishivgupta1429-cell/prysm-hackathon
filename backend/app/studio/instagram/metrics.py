"""Built during the Meta Global AI Developer Hackathon (new work).

Pure functions: the numbers derived from imported reels. No I/O, no invention: a missing input gives None.
"""
from statistics import median
from typing import Iterable, Optional

# Insight metrics requested for a reel (docs/instagram-login.md section 3)
METRICS = ("reach", "views", "likes", "comments", "shares", "saved", "total_interactions",
           "ig_reels_avg_watch_time", "ig_reels_video_view_total_time", "reels_skip_rate")
METRIC_LABEL = {
    "reach": "reach", "views": "views", "likes": "likes", "comments": "comments", "shares": "shares", "saved": "saves",
    "total_interactions": "total interactions", "ig_reels_avg_watch_time": "average watch time",
    "ig_reels_video_view_total_time": "total watch time", "reels_skip_rate": "skip rate",
}
MIN_FOR_DNA = 3                # below this: only the per-reel breakdown, no pattern claim of any kind
MIN_FOR_PATTERNS = 5          # below this the DNA says "too few reels to call anything a pattern"
MIN_FOR_SPLIT = 4             # top/bottom split needs at least this many scored reels


def _num(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def account_median(values: Iterable) -> Optional[float]:
    """Median of the usable (non-None, > 0) values; None if there are none. Median, so one viral reel cannot drag it."""
    vals = [f for f in (_num(v) for v in values) if f is not None and f > 0]
    return float(median(vals)) if vals else None


def pick_basis(reels: list[dict]) -> Optional[str]:
    """'reach' if at least half the reels have it (the reach estimate is the better 'how many people' number), else 'views', else None."""
    n = len(reels)
    for basis in ("reach", "views"):
        have = sum(1 for r in reels if (_num(r.get(basis)) or 0) > 0)
        if have and have * 2 >= n:
            return basis
    for basis in ("reach", "views"):  # sparse but present: still better than nothing
        if any((_num(r.get(basis)) or 0) > 0 for r in reels):
            return basis
    return None


def performance_indices(reels: list[dict]) -> tuple[list[Optional[float]], Optional[str], Optional[float]]:
    """performance_index per reel = reach (or views) / the median of that metric across the imported reels.
    1.0 = typical for this account, 2.0 = twice the typical reel. A reel without the metric gets None.
    Returns (indices in the order given, basis, median)."""
    basis = pick_basis(reels)
    if basis is None:
        return [None] * len(reels), None, None
    med = account_median(r.get(basis) for r in reels)
    if not med:
        return [None] * len(reels), basis, None
    out = []
    for r in reels:
        v = _num(r.get(basis))
        out.append(round(v / med, 3) if v is not None else None)
    return out, basis, med


def watch_through(avg_watch_ms, duration_s) -> Optional[float]:
    """Average watch time divided by the reel's duration (both known, duration > 0). Can exceed 1.0 for looping reels."""
    a, d = _num(avg_watch_ms), _num(duration_s)
    if a is None or d is None or d <= 0 or a < 0:
        return None
    return round((a / 1000.0) / d, 3)


def mean(values: Iterable) -> Optional[float]:
    vals = [f for f in (_num(v) for v in values) if f is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def availability(reels: list[dict]) -> dict[str, int]:
    """For every metric, on how many reels Instagram returned a value."""
    return {m: sum(1 for r in reels if _num(r.get(m)) is not None) for m in METRICS}


def missing_metrics(reels: list[dict]) -> list[str]:
    """Metrics that came back empty on every imported reel (so the UI can say which are missing)."""
    av = availability(reels)
    return [m for m in METRICS if av[m] == 0]


def split_top_bottom(reels: list[dict], score_key: str = "score") -> tuple[list[dict], list[dict], bool]:
    """(top, bottom, outlier_flag) from reels carrying a numeric `score`. Roughly the top and bottom third (at least 2 each,
    never overlapping). outlier_flag is True when the best reel is more than 3x the median score (medians keep the split honest)."""
    scored = [r for r in reels if _num(r.get(score_key)) is not None]
    if len(scored) < MIN_FOR_SPLIT:
        return [], [], False
    scored.sort(key=lambda r: r[score_key], reverse=True)
    k = max(2, round(len(scored) / 3))
    k = min(k, len(scored) // 2)
    med = account_median(r[score_key] for r in scored)
    outlier = bool(med and scored[0][score_key] > 3 * med)
    return scored[:k], scored[-k:], outlier


def blended_score(perf_index: Optional[float], wt: Optional[float], wt_median: Optional[float]) -> Optional[float]:
    """One number to rank reels by: the mean of whichever of performance_index and (watch_through / the account's median
    watch_through) exist. Both read 1.0 for a typical reel of this account, so they can be averaged."""
    parts = []
    if perf_index is not None:
        parts.append(perf_index)
    if wt is not None and wt_median:
        parts.append(wt / wt_median)
    return round(sum(parts) / len(parts), 3) if parts else None
