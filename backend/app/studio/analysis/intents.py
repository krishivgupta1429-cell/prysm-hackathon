"""Hackathon-built (Meta Global AI Developer Hackathon).

Edit intents: the input for the Stage 2 director. Fixes that are auto_applicable and not reshoot/none are
flattened into one ordered list (max 8), ranked by severity x the parameter's niche weight.

  severity  the highest severity among the parameter's issues that fall inside the fix's target (+/- 1 s),
            else the highest among all its issues, else 1
  weight    the niche weight of a scored parameter; advisory parameters (no weight) use half the smallest
            scored weight so they rank below scored ones of equal severity
A fix with no target borrows a 3-second window at its issue's time; otherwise the target stays null (whole video).
A "rotate" intent always applies to the whole video (target null) and carries the direction found in the fix's
steps ("cw90", "ccw90" or "180", else null) plus the matching FFmpeg filter.
"""
import re

from app.studio.analysis.niches import PARAMETERS, Niche
from app.studio.analysis.schema import NOT_AUTOMATABLE, EditIntent, Fix, Parameter, Target

MAX_INTENTS = 8
ORDER = {p[0]: i for i, p in enumerate(PARAMETERS)}


ROTATE_FILTERS = {"cw90": "transpose=1", "ccw90": "transpose=2", "180": "transpose=1,transpose=1"}


def rotate_direction(title: str, steps: list[str]) -> str | None:
    """"cw90", "ccw90" or "180" read from the fix text, or None when the direction is not stated."""
    text = " ".join([title, *steps]).lower()
    if re.search(r"counter[\s-]?clockwise|anti[\s-]?clockwise|\bccw\b|transpose\s*=\s*2", text):
        return "ccw90"
    if re.search(r"180|upside[\s-]?down", text):
        return "180"
    if re.search(r"clockwise|\bcw\b|transpose\s*=\s*1", text):
        return "cw90"
    return None


def _severity(p: Parameter, target: Target | None) -> int:
    if not p.issues:
        return 1
    if target:
        near = [i.severity for i in p.issues if i.at_s is not None and target.start_s - 1 <= i.at_s <= target.end_s + 1]
        if near:
            return max(near)
    return max(i.severity for i in p.issues)


def top_fixes(params: list[Parameter], niche: Niche, n: int = 3) -> list[tuple[float, Parameter, "Fix"]]:
    """The n highest-ranked fixes of any kind (including reshoot/none), ranked like the edit intents."""
    advisory_weight = min(niche.weights.values()) / 2
    ranked = []
    for p in params:
        weight = niche.weights.get(p.id, advisory_weight) if p.scored else advisory_weight
        for j, fix in enumerate(p.fixes):
            ranked.append((round(_severity(p, fix.target) * weight, 5), ORDER.get(p.id, 99), j, p, fix))
    ranked.sort(key=lambda r: (-r[0], r[1], r[2]))
    return [(r[0], r[3], r[4]) for r in ranked[:n]]


def build_edit_intents(params: list[Parameter], niche: Niche, max_intents: int = MAX_INTENTS) -> list[EditIntent]:
    weights = niche.weights
    advisory_weight = min(weights.values()) / 2
    ranked: list[tuple[float, int, int, EditIntent]] = []
    for p in params:
        weight = weights.get(p.id, advisory_weight) if p.scored else advisory_weight
        for j, fix in enumerate(p.fixes):
            if not fix.auto_applicable or fix.fixable_by in NOT_AUTOMATABLE:
                continue
            target = fix.target
            extra = {}
            if fix.fixable_by == "rotate":  # orientation is a whole-video fix
                target = None
                direction = rotate_direction(fix.title, fix.steps)
                extra = {"direction": direction, "ffmpeg_filter": ROTATE_FILTERS.get(direction)}
            elif target is None:
                at = next((i.at_s for i in sorted(p.issues, key=lambda i: -i.severity) if i.at_s is not None), None)
                if at is not None:
                    target = Target(start_s=max(0.0, at), end_s=max(0.0, at) + 3.0)
            rank = round(_severity(p, fix.target) * weight, 5)
            ranked.append((rank, ORDER.get(p.id, 99), j, EditIntent(
                intent=fix.fixable_by, target=target, parameters={"title": fix.title, "steps": fix.steps, **extra},
                source_parameter=p.id, rationale=fix.expected_effect or fix.title,
                evidence=list(dict.fromkeys(e.id for e in p.evidence)), rank_score=rank)))  # one id per item
    ranked.sort(key=lambda r: (-r[0], r[1], r[2]))
    return [r[3] for r in ranked[:max_intents]]
