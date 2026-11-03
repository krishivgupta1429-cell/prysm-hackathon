"""Built during the Meta Global AI Developer Hackathon (new work).

Validation of the model's scoring output, in plain code after the model call. Nothing the model says is
trusted until it passes here:
  - parameter ids are canonicalised; unknown ones are dropped and missing ones added as unsupported
  - every evidence id must exist in the pack; unknown ids are dropped and that parameter's confidence becomes low
  - a scored parameter left with no valid evidence is marked "unsupported" (excluded from the overall score)
  - scores are clamped to 0-100
  - a fix with a missing or unknown fixable_by becomes "none" with auto_applicable false; reshoot/none are
    never auto-applicable; fix targets are clamped to start <= end
  - Trend Alignment confidence is capped at medium and it is marked evidence_limited with the label
    "estimate (no live trend data)" (there is no live trend data in this version)
  - when the Gemini audio facts are missing from the pack, Audio & Music confidence is capped at low and the
    flag "audio_listener_missing" is added (the overall score still computes)
  - a "rotate" fix is always auto-applicable (an FFmpeg transpose)
A quote that does not appear in the cited evidence line is flagged (not dropped).
The creator-context item (what the creator typed about niche and description) is never valid evidence: a
citation of it is dropped like an unknown id. description_fit is held to the same rule (validate_description_fit).
Returns the cleaned parameters in registry order and a list of flags.
"""
import re

from app.studio.analysis.evidence import EvidencePack
from app.studio.analysis.niches import GROUNDED_LABEL, PARAM_NAMES, PARAMETERS, TREND_LABEL
from app.studio.analysis.schema import FIXABLE_BY, NOT_AUTOMATABLE, DescriptionFit, Flag, Parameter

_NAME_TO_ID = {re.sub(r"[^a-z0-9]", "", n.lower()): pid for pid, n in PARAM_NAMES.items()}
_NAME_TO_ID.update({re.sub(r"[^a-z0-9]", "", pid): pid for pid in PARAM_NAMES})


def canonical_id(raw_id: str, raw_name: str = "") -> str | None:
    for candidate in (raw_id, raw_name):
        key = re.sub(r"[^a-z0-9]", "", (candidate or "").lower())
        if key in _NAME_TO_ID:
            return _NAME_TO_ID[key]
    return None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def validate_description_fit(fit: DescriptionFit | None, pack: EvidencePack) -> tuple[DescriptionFit | None, list[Flag]]:
    """The same evidence rule as for parameters: unknown ids and the creator's own line are dropped, and a verdict
    left with no valid evidence is marked unsupported."""
    if fit is None:
        return None, []
    flags: list[Flag] = []
    kept, dropped = [], []
    for ref in fit.evidence:
        (kept if ref.id in pack.citable_ids() else dropped).append(ref)
    if dropped:
        flags.append(Flag(parameter="description_fit", code="unknown_evidence_id",
                          detail="dropped " + ", ".join(r.id for r in dropped) + " (unknown, or the creator-provided line)"))
    fit.evidence = kept
    fit.unsupported = not kept
    if fit.unsupported:
        flags.append(Flag(parameter="description_fit", code="unsupported", detail="verdict has no valid evidence"))
    return fit, flags


def validate_parameters(params: list[Parameter], pack: EvidencePack) -> tuple[list[Parameter], list[Flag]]:
    flags: list[Flag] = []
    items = pack.by_id()
    citable = pack.citable_ids()
    by_id: dict[str, Parameter] = {}
    for p in params:
        pid = canonical_id(p.id, p.name)
        if pid is None:
            flags.append(Flag(parameter=p.id, code="unknown_parameter", detail="dropped: not one of the 10 parameters"))
            continue
        if pid in by_id:
            flags.append(Flag(parameter=pid, code="duplicate_parameter", detail="kept the first"))
            continue
        p.id = pid
        by_id[pid] = p

    out: list[Parameter] = []
    for pid, name, scored in PARAMETERS:
        p = by_id.get(pid)
        if p is None:
            flags.append(Flag(parameter=pid, code="missing_parameter", detail="the model did not return it"))
            p = Parameter(id=pid, name=name, scored=scored, score=None, confidence="low")
        p.name, p.scored = name, scored  # the registry is authoritative, not the model

        if p.score is not None:
            clamped = max(0, min(100, p.score))
            if clamped != p.score:
                flags.append(Flag(parameter=pid, code="score_clamped", detail=f"{p.score} -> {clamped}"))
            p.score = clamped

        kept, dropped = [], []
        for ref in p.evidence:
            if ref.id == pack.creator_item_id:
                dropped.append(ref.id)
                flags.append(Flag(parameter=pid, code="creator_context_not_evidence",
                                  detail="the creator-provided line cannot be evidence for a score"))
            elif ref.id in items:
                kept.append(ref)
                if ref.quote and _norm(ref.quote) not in _norm(items[ref.id].text):
                    flags.append(Flag(parameter=pid, code="quote_not_in_evidence", detail=f"{ref.id}: {ref.quote[:50]!r}"))
            else:
                dropped.append(ref.id)
        if dropped:
            flags.append(Flag(parameter=pid, code="unknown_evidence_id", detail=f"dropped {', '.join(dropped)}"))
            p.confidence = "low"
        p.evidence = kept

        p.unsupported = False
        if scored and (not kept or p.score is None):
            p.unsupported = True
            flags.append(Flag(parameter=pid, code="unsupported", detail="scored parameter with no valid evidence or no score"
                                                                        " (left out of the overall score)"))
        if pid == "trend":
            trend_cited = [r for r in kept if r.id in pack.trend_ids()]
            video_cited = [r for r in kept if r.id not in pack.trend_ids()]
            if pack.trends_ok and trend_cited:  # judged against the live trend lines: grounded
                p.evidence_limited, p.label = False, GROUNDED_LABEL
                if p.confidence == "high" and not video_cited:  # a trend line alone says nothing about this video
                    p.confidence = "medium"
                    flags.append(Flag(parameter=pid, code="trend_confidence_capped", detail="no video evidence cited next to the trend lines"))
            else:
                p.evidence_limited, p.label = True, TREND_LABEL
                if pack.trends_ok:  # live trends were available but the score does not cite them: stays an estimate
                    flags.append(Flag(parameter=pid, code="trend_not_grounded", detail="live trends available but no trend line cited"))
                if p.confidence == "high":
                    p.confidence = "medium"
                    flags.append(Flag(parameter=pid, code="trend_confidence_capped",
                                      detail="live trends not cited" if pack.trends_ok else "no live trend data in this version"))
        if pid == "audio" and pack.audio_listener_missing:
            p.confidence = "low"
            flags.append(Flag(parameter=pid, code="audio_listener_missing",
                              detail="Gemini audio facts are missing from the evidence; confidence capped at low"))

        for fix in p.fixes:
            kind = (fix.fixable_by or "").strip().lower()
            if kind not in FIXABLE_BY:
                if fix.fixable_by:
                    flags.append(Flag(parameter=pid, code="unknown_fixable_by", detail=f"{fix.fixable_by!r} -> none"))
                kind = "none"
            fix.fixable_by = kind
            if kind in NOT_AUTOMATABLE:
                fix.auto_applicable = False
            elif kind == "rotate":
                fix.auto_applicable = True  # an FFmpeg transpose needs no new footage
            if fix.target:
                a, b = sorted((max(0.0, fix.target.start_s), max(0.0, fix.target.end_s)))
                fix.target.start_s, fix.target.end_s = a, b
        out.append(p)
    return out, flags
