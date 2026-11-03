"""Aggregate per-video scores into niche-level statistics + empirical weights.

Methodology — per-creator Fisher z-weighted correlation:
  For each niche × parameter pair, we compute a per-creator Pearson r between
  that parameter's scores and log₂(views / creator_median) — restricted to
  creators with ≥ MIN_CREATOR_REELS usable (scored + MIN_VIEWS) reels.
  Individual r values are combined via Fisher z-transformation:

      z_i   = arctanh(r_i)
      w_i   = reel_count_i − 3
      z_avg = Σ(z_i · w_i) / Σ(w_i)
      r_agg = tanh(z_avg)

  Creators with more reels pull the aggregate r more heavily.  Creators below
  the reel threshold are excluded entirely (no partial signal).

  The resulting per-creator-weighted r is the *sole* basis for importance
  ranking.  Mean score is retained as a labeled informational statistic but
  does NOT influence parameter weights.

Output:
  results/aggregate_<niche>.json  — full statistics, machine-readable
  results/niche_weights.md        — comparison table across all niches (default)
  results/niche_weights_v2.md     — when aggregate_all(md_filename=...) is used
"""
from __future__ import annotations

import json
import logging
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any, Dict, List, Optional, Tuple

from .config import SETTINGS
from .scorer import PARAMETERS

log = logging.getLogger(__name__)

PARAM_NAMES = [p["name"] for p in PARAMETERS]
DEFAULT_WEIGHTS = {p["name"]: p["weight"] for p in PARAMETERS}

# Minimum absolute views to include a video in correlation.
MIN_VIEWS = 5_000

# Minimum usable (scored + MIN_VIEWS) reels for a creator to contribute
# a per-creator correlation.  Below this, the per-creator Pearson is too
# noisy to be meaningful.
MIN_CREATOR_REELS = 4


# ── Math helpers ──────────────────────────────────────────────────────────────

def _pearson(xs: List[float], ys: List[float], min_n: int = 5) -> Optional[float]:
    n = min(len(xs), len(ys))
    if n < min_n:
        return None
    mx, my = mean(xs), mean(ys)
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    dx  = sum((xs[i] - mx) ** 2 for i in range(n))
    dy  = sum((ys[i] - my) ** 2 for i in range(n))
    if dx <= 0 or dy <= 0:
        return None
    return num / math.sqrt(dx * dy)


def _get_views(r: Dict[str, Any]) -> Optional[float]:
    try:
        v = float((r.get("metadata") or {}).get("viewCount") or 0)
        return v if v >= MIN_VIEWS else None
    except (TypeError, ValueError):
        return None


def _get_uploader(r: Dict[str, Any]) -> str:
    return ((r.get("metadata") or {}).get("uploader") or "unknown").lower().strip()


# ── Per-creator Fisher z-weighted correlation ─────────────────────────────────

def _per_creator_fisher_correlation(
    valid: List[Dict[str, Any]],
    param_name: str,
) -> Tuple[Optional[float], int, int]:
    """
    Returns (fisher_weighted_r, n_contributing_creators, n_skipped_creators).

    fisher_weighted_r is None when no creator met the reel threshold.
    n_skipped counts creators who had usable reels but were excluded because
    their count was < MIN_CREATOR_REELS, their scores had zero variance, or
    their views had zero variance.
    """
    # Collect (score, views) pairs per creator
    creator_pairs: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for r in valid:
        score = (r.get("scores_by_param") or {}).get(param_name)
        if not isinstance(score, (int, float)):
            continue
        views = _get_views(r)
        if views is None:
            continue
        creator_pairs[_get_uploader(r)].append((float(score), views))

    z_values: List[float] = []
    weights:  List[float] = []
    n_contributing = 0
    n_skipped      = 0

    for uploader, pairs in creator_pairs.items():
        n = len(pairs)
        if n < MIN_CREATOR_REELS:
            n_skipped += 1
            continue

        scores_c = [p[0] for p in pairs]
        views_c  = [p[1] for p in pairs]

        med = median(views_c)
        if med <= 0:
            n_skipped += 1
            continue

        log2_views = [math.log2(v / med) for v in views_c]

        # Inline Pearson (min_n = MIN_CREATOR_REELS = 4)
        mx, my = mean(scores_c), mean(log2_views)
        num = sum((scores_c[i] - mx) * (log2_views[i] - my) for i in range(n))
        dx  = sum((scores_c[i] - mx) ** 2 for i in range(n))
        dy  = sum((log2_views[i] - my) ** 2 for i in range(n))
        if dx <= 0 or dy <= 0:
            n_skipped += 1
            continue

        r = num / math.sqrt(dx * dy)
        # Clamp to keep arctanh finite (|r|=1 → ±∞)
        r = max(-0.9999, min(0.9999, r))

        z_values.append(math.atanh(r))
        weights.append(float(n - 3))   # always ≥ 1 since n ≥ 4
        n_contributing += 1

    if not z_values:
        return None, 0, n_skipped

    total_w = sum(weights)
    z_avg   = sum(z * w for z, w in zip(z_values, weights)) / total_w
    return math.tanh(z_avg), n_contributing, n_skipped


# ── Per-niche aggregation ─────────────────────────────────────────────────────

def aggregate_niche(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    valid = [r for r in records if not r.get("error") and r.get("scores_by_param")]

    summary: Dict[str, Any] = {
        "videos_total":  len(records),
        "videos_used":   len(valid),
        "videos_failed": len(records) - len(valid),
        "methodology": (
            "per-creator Fisher z-weighted correlation — "
            "log2(views/creator_median) vs parameter score, "
            f"min {MIN_CREATOR_REELS} reels per creator, weighted by (n-3)"
        ),
        "parameters": {},
        "patterns": {"dominant": {}, "weak": {}},
        "empirical_weights": {},
    }

    if not valid:
        return summary

    raw_scores: Dict[str, float] = {}

    for name in PARAM_NAMES:
        # ── Descriptive stats (all valid records, not filtered by views) ──────
        all_scores = [
            float(s)
            for r in valid
            if isinstance((s := (r.get("scores_by_param") or {}).get(name)), (int, float))
        ]

        if not all_scores:
            summary["parameters"][name] = {"n": 0}
            raw_scores[name] = 0.0
            continue

        m = mean(all_scores)
        param_stat: Dict[str, Any] = {
            "n":      len(all_scores),
            "mean":   round(m, 1),
            "median": round(median(all_scores), 1),
            "stddev": round(pstdev(all_scores), 1) if len(all_scores) > 1 else 0.0,
            "min":    round(min(all_scores), 1),
            "max":    round(max(all_scores), 1),
        }

        # ── Per-creator Fisher z-weighted correlation ─────────────────────────
        fisher_r, n_contrib, n_skipped = _per_creator_fisher_correlation(valid, name)

        param_stat["fisher_correlation"]        = round(fisher_r, 4) if fisher_r is not None else None
        param_stat["n_contributing_creators"]   = n_contrib
        param_stat["n_skipped_creators"]        = n_skipped

        summary["parameters"][name] = param_stat

        # Raw importance = floored Fisher r (None → 0; negative → 0)
        raw_scores[name] = max(0.0, fisher_r) if fisher_r is not None else 0.0

    # ── Normalize to sum = 100% ───────────────────────────────────────────────
    total = sum(raw_scores.values()) or 1.0
    summary["empirical_weights"] = {
        name: round((v / total) * 100, 1) for name, v in raw_scores.items()
    }

    # ── Creator coverage ──────────────────────────────────────────────────────
    creator_counts: Dict[str, int] = defaultdict(int)
    for r in valid:
        creator_counts[_get_uploader(r)] += 1
    summary["creator_coverage"] = {
        "unique_creators": len(creator_counts),
        "videos_per_creator": {
            k: v for k, v in sorted(creator_counts.items(), key=lambda x: -x[1])
        },
    }

    # ── Pattern frequencies ───────────────────────────────────────────────────
    dom  = Counter()
    weak = Counter()
    for r in valid:
        for tag in (r.get("scoring") or {}).get("dominantPatterns", []) or []:
            if isinstance(tag, str):
                dom[tag.strip().lower()] += 1
        for tag in (r.get("scoring") or {}).get("weakSpots", []) or []:
            if isinstance(tag, str):
                weak[tag.strip().lower()] += 1
    summary["patterns"]["dominant"] = dict(dom.most_common(20))
    summary["patterns"]["weak"]     = dict(weak.most_common(20))

    return summary


# ── Ranking helpers ───────────────────────────────────────────────────────────

def _rank_weights(weights: Dict[str, float]) -> Dict[str, int]:
    """Return param → rank (1 = highest weight) for a niche weight dict."""
    ordered = sorted(weights.items(), key=lambda x: -x[1])
    return {name: i + 1 for i, (name, _) in enumerate(ordered)}


def _rank_shift_summary(
    old_w: Dict[str, float],
    new_w: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Per-parameter old vs new comparison rows."""
    old_ranks = _rank_weights(old_w)
    new_ranks = _rank_weights(new_w)
    rows = []
    for name in PARAM_NAMES:
        ow = old_w.get(name, 0.0)
        nw = new_w.get(name, 0.0)
        or_ = old_ranks.get(name, 99)
        nr_ = new_ranks.get(name, 99)
        shift = or_ - nr_   # positive = moved up in ranking
        rows.append({"param": name, "old_w": ow, "new_w": nw,
                     "old_rank": or_, "new_rank": nr_, "rank_shift": shift})
    rows.sort(key=lambda x: abs(x["rank_shift"]), reverse=True)
    return rows


# ── Cross-niche markdown report ───────────────────────────────────────────────

def write_markdown_report(
    per_niche:   Dict[str, Dict[str, Any]],
    out_path:    Path,
    old_weights: Optional[Dict[str, Dict[str, float]]] = None,
) -> None:
    niches = sorted(per_niche.keys())

    lines: List[str] = [
        "# Niche-specific parameter weights — empirical (v2)",
        "",
        "**Methodology:** Per-creator Fisher z-weighted correlation.",
        "For each niche × parameter, Pearson r is computed between that parameter's",
        f"scores and log₂(views / creator_median) for each creator with ≥ {MIN_CREATOR_REELS} usable reels.",
        "Individual r values are combined via Fisher z-transformation, weighted by",
        "(reel_count − 3), so creators with more data pull the aggregate more heavily.",
        "",
        "**Weight derivation:** `raw_importance = max(0, Fisher_r)`, then normalized",
        "so each niche sums to exactly 100. Mean score is retained as a labeled",
        "informational stat only and does **not** influence rankings.",
        "",
        f"Niches analysed: **{len(niches)}**.",
        "",
        "## Sample sizes",
        "",
        "| Niche | Videos total | Used | Creators | Failed |",
        "|---|---|---|---|---|",
    ]
    for n in niches:
        s = per_niche[n]
        creators = s.get("creator_coverage", {}).get("unique_creators", "–")
        lines.append(
            f"| {n} | {s['videos_total']} | {s['videos_used']} "
            f"| {creators} | {s['videos_failed']} |"
        )
    lines.append("")

    # ── Empirical weights table ────────────────────────────────────────────────
    lines.extend([
        "## Empirical weights (rows sum to 100) — v2 formula",
        "",
        "| Niche | " + " | ".join(PARAM_NAMES) + " |",
        "|---" * (len(PARAM_NAMES) + 1) + "|",
    ])
    for n in niches:
        weights = per_niche[n].get("empirical_weights", {})
        cells   = [f"{weights.get(name, 0):.1f}" for name in PARAM_NAMES]
        lines.append(f"| {n} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "**Default (current production weights):** "
        + ", ".join(f"{name}={DEFAULT_WEIGHTS[name]}" for name in PARAM_NAMES)
    )
    lines.append("")

    # ── Before/after comparison for top-changed niches ────────────────────────
    if old_weights:
        # Score each niche by total absolute weight change
        change_scores: List[Tuple[float, str]] = []
        for n in niches:
            if n not in old_weights:
                continue
            new_w = per_niche[n].get("empirical_weights", {})
            old_w = old_weights[n]
            total_change = sum(
                abs(new_w.get(p, 0) - old_w.get(p, 0)) for p in PARAM_NAMES
            )
            change_scores.append((total_change, n))
        change_scores.sort(reverse=True)
        top_niches = [n for _, n in change_scores[:4]]

        lines.extend([
            "## Before / After comparison — top-4 most-changed niches",
            "",
            "> **Old formula:** 40% mean_score + 60% max(0, pooled_pearson)",
            "> **New formula:** Fisher z-weighted per-creator correlation only",
            "",
        ])

        for n in top_niches:
            old_w = old_weights[n]
            new_w = per_niche[n].get("empirical_weights", {})
            rows  = _rank_shift_summary(old_w, new_w)

            total_chg = sum(abs(r["new_w"] - r["old_w"]) for r in rows)
            lines.extend([
                f"### {n}  (total Δweight = {total_chg:.1f} pts)",
                "",
                "| Parameter | Old % | New % | Δ% | Old rank | New rank | Rank shift |",
                "|---|---|---|---|---|---|---|",
            ])
            for row in sorted(rows, key=lambda x: x["old_rank"]):
                delta = row["new_w"] - row["old_w"]
                sign  = "+" if delta >= 0 else ""
                shift = row["rank_shift"]
                shift_s = f"+{shift}" if shift > 0 else str(shift)
                lines.append(
                    f"| {row['param']} | {row['old_w']:.1f} | {row['new_w']:.1f} "
                    f"| {sign}{delta:.1f} | #{row['old_rank']} | #{row['new_rank']} "
                    f"| {shift_s} |"
                )
            lines.append("")

    # ── Data-reliability flags ────────────────────────────────────────────────
    lines.extend([
        "## Reliability flags — parameters with ≤ 3 contributing creators",
        "",
        "Parameters where fewer than 4 creators met the reel threshold; "
        "Fisher r is based on very thin data.",
        "",
        "| Niche | Parameter | n_contributing | n_skipped | Fisher r |",
        "|---|---|---|---|---|",
    ])
    flag_rows = []
    for n in niches:
        params = per_niche[n].get("parameters", {})
        for name in PARAM_NAMES:
            p = params.get(name, {})
            nc = p.get("n_contributing_creators", 0)
            if nc <= 3:
                ns    = p.get("n_skipped_creators", 0)
                fr    = p.get("fisher_correlation")
                fr_s  = f"{fr:.4f}" if isinstance(fr, float) else "–"
                flag_rows.append((n, name, nc, ns, fr_s))
    if flag_rows:
        for row in flag_rows:
            lines.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} |")
    else:
        lines.append("| — | — | — | — | — |")
    lines.append("")

    # ── Per-niche detail ──────────────────────────────────────────────────────
    for n in niches:
        s = per_niche[n]
        lines.extend([
            f"## {n}",
            "",
            f"Videos used: {s['videos_used']} (of {s['videos_total']}) "
            f"across {s.get('creator_coverage', {}).get('unique_creators', '?')} creators.",
            "",
            "### Parameter statistics",
            "",
            "| Parameter | n | mean (info only) | stddev | Fisher r | n_creators | weight |",
            "|---|---|---|---|---|---|---|",
        ])
        weights = s.get("empirical_weights", {})
        for name in PARAM_NAMES:
            p = s["parameters"].get(name, {})
            if not p or p.get("n", 0) == 0:
                lines.append(f"| {name} | 0 | – | – | – | – | 0.0 |")
                continue
            fr  = p.get("fisher_correlation")
            nc  = p.get("n_contributing_creators", 0)
            fr_s = f"{fr:+.4f}" if isinstance(fr, float) else "–"
            w    = weights.get(name, 0.0)
            lines.append(
                f"| {name} | {p['n']} | {p['mean']} | {p['stddev']} "
                f"| {fr_s} | {nc} | {w} |"
            )

        dom    = s["patterns"]["dominant"]
        weak_p = s["patterns"]["weak"]
        if dom:
            lines.extend(["", "### Top patterns in viral videos", ""])
            for k, v in list(dom.items())[:10]:
                lines.append(f"- `{k}` — {v} videos")
        if weak_p:
            lines.extend(["", "### Common weaknesses in underperforming videos", ""])
            for k, v in list(weak_p.items())[:10]:
                lines.append(f"- `{k}` — {v} videos")
        lines.append("")

    out_path.write_text("\n".join(lines))
    log.info("Wrote markdown report: %s", out_path)


# ── Public entry point ────────────────────────────────────────────────────────

def aggregate_all(
    results_dir: Optional[Path] = None,
    md_filename: str = "niche_weights.md",
) -> Dict[str, Dict[str, Any]]:
    results_dir = results_dir or SETTINGS.results_dir

    # Capture old empirical weights before overwriting aggregate_*.json
    old_weights: Optional[Dict[str, Dict[str, float]]] = None
    if md_filename != "niche_weights.md":
        old_weights = {}
        for path in sorted(results_dir.glob("aggregate_*.json")):
            niche = path.stem.replace("aggregate_", "")
            try:
                data = json.loads(path.read_text())
                if "empirical_weights" in data:
                    old_weights[niche] = data["empirical_weights"]
            except Exception:
                pass
        log.info("Captured old empirical weights for %d niches", len(old_weights))

    per_niche: Dict[str, Dict[str, Any]] = {}
    for path in sorted(results_dir.glob("per_video_*.json")):
        if ".bak" in path.name:
            continue
        niche = path.stem.replace("per_video_", "")
        try:
            records = json.loads(path.read_text())
        except json.JSONDecodeError:
            log.warning("Skipping unreadable %s", path)
            continue
        if not isinstance(records, list):
            continue
        agg = aggregate_niche(records)
        per_niche[niche] = agg
        out = results_dir / f"aggregate_{niche}.json"
        out.write_text(json.dumps(agg, indent=2))
        log.info("Aggregated %s → %s", niche, out)

    write_markdown_report(
        per_niche,
        results_dir / md_filename,
        old_weights=old_weights,
    )
    return per_niche
