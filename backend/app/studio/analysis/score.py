"""Hackathon-built (Meta Global AI Developer Hackathon).

The scoring call: one muse-spark-1.3 text call (low temperature, JSON only) over the evidence pack and the
niche's judging notes, optionally with up to 6 keyframes (--with-frames). It returns 10 parameters, each with
a score, confidence, cited evidence, issues and fixes. The model never computes the overall score (overall.py
does) and nothing it says is trusted until validate.py has checked it. A parse failure is retried once with a
repair prompt; a second failure is an error outcome.
"""
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.studio import config
from app.studio.analysis.creator import CreatorContext
from app.studio.analysis.evidence import EvidencePack
from app.studio.analysis.niches import PARAMETERS, Niche, NOTES_LABEL, TREND_PREFIX
from app.studio.analysis.schema import FIXABLE_BY, DescriptionFit, Parameter, ScoringOutput
from app.studio.meta_client import MetaClient, MetaError
from app.studio.stages.watch import parse_watch_json

PROMPT = """You are Prysm's analyst for short-form video. Score one vertical reel for the niche "{niche}" using ONLY the evidence below.

EVIDENCE (each line starts with its id; times are seconds):
{evidence}

PARAMETERS AND JUDGING NOTES ({notes_label}):
{notes}
{creator_block}
RULES
1. Every claim must cite evidence ids from the list above. Do not use any fact that is not in the evidence. Put the cited ids in "evidence" with a short verbatim quote from that line.
2. Score each parameter 0-100 (integer). Bands: 0-49 Needs Work, 50-69 Developing, 70-84 Strong, 85-100 Viral-Ready. Use the full range; do not cluster everything between 60 and 80.
3. "confidence" is low, medium or high: how well the evidence supports the score.
4. Fixes must be specific and tied to timestamps in seconds. "fixable_by" is one of: {fixable}. Use "reshoot" or "none" for anything an editor cannot do with the existing footage (new takes, a different performance, a different location). Set "auto_applicable" true only for edits software can do from the existing footage.
{trend_rule}
6. The parameters marked advisory are scored the same way but are not part of the overall score ("scored": false).
7. If the evidence says the reel is presented sideways or upside down, give a "rotate" fix (auto_applicable true, no target needed) and put the direction in the steps, e.g. "rotate 90 degrees clockwise (ffmpeg transpose=1)", "rotate 90 degrees counterclockwise (transpose=2)" or "rotate 180 degrees". Only state a direction that the evidence or the attached keyframes actually show (the way the subject's head points tells you); if the direction is not shown, write "direction unknown: check a frame before applying" in the steps instead of guessing, because a wrong direction makes it worse. Use "zoom_crop" only for real crop or framing problems, never for orientation.
8. If an evidence line says an audio listener produced no result, say so in Audio & Music and keep its confidence "low".
9. Keep text short: why_it_matters 1-2 sentences, at most 3 strengths, at most 3 issues and 2 fixes per parameter.
{creator_rules}
Reply with ONE JSON object and nothing else (no prose, no code fences):
{{"parameters": [{{"id": "hook", "name": "Hook & Opening", "scored": true, "score": 72, "confidence": "medium",
  "evidence": [{{"id": "E4", "quote": "short quote"}}], "why_it_matters": "...", "strengths": ["..."],
  "issues": [{{"what": "...", "at_s": 2.5, "severity": 2}}],
  "fixes": [{{"title": "...", "steps": ["..."], "expected_effect": "...", "fixable_by": "trim",
             "target": {{"start_s": 0.0, "end_s": 3.0}}, "auto_applicable": true}}]}}]}}
Include exactly these 10 parameter ids, in this order: {ids}.{fit_schema}"""

TREND_RULE_ESTIMATE = """5. Trend Alignment: there is no live trend data in this version. It is an estimate: judge it only from the identified track and the format cues, cap its confidence at "medium", and its "why_it_matters" must say "no live trend data in this version"."""

TREND_RULE_GROUNDED = """5. Trend Alignment: the evidence includes live trend lines (source "trends", from a Google search made today for the creator's niches). Judge the video against those lines: say which trend (a format, a sound or a hashtag) the video matches or misses, and cite BOTH the trend line id and the video evidence id that shows the match or the miss. Use only trends that appear in the trend lines; never invent or assume a trend. If an identified track is in the evidence, compare it with the trending audio line. Give "high" confidence only when you cite both kinds of evidence, and its "why_it_matters" must name the matched or missed trend."""

REPAIR = """Your previous reply could not be parsed ({error}).
Here it is:
{previous}

Reply again with ONLY the corrected JSON object, exactly the schema described before (a "parameters" list of 10 objects). No prose, no code fences."""


CREATOR_RULES = """10. The line in the evidence marked CREATOR-PROVIDED, UNVERIFIED is what the creator told us, not a fact about the video. Never cite it in a parameter's "evidence" and never use it to justify a score. Use it only to tailor advice to their goal and to fill "description_fit".
11. "description_fit": does the video match the description the creator gave? verdict "matches", "partial" or "off", a one-sentence note, and "evidence" citing the real evidence lines that show it (not the creator-provided line). If the description is empty, omit "description_fit"."""
FIT_SCHEMA = """
Also include a top-level "description_fit": {"verdict": "matches|partial|off", "note": "...", "evidence": [{"id": "E4", "quote": "short quote"}]}."""


def creator_block(creator: CreatorContext | None, niche: Niche) -> str:
    """The niche note and the creator's own context for the prompt (labelled as unverified context)."""
    if creator is None:
        return ""
    lines = [f"\nNICHE FOCUS: {niche.id}. {niche.description}"]
    if creator.extras:
        lines.append(f"ALSO THE CREATOR'S NICHES (context only, they do not change the scoring weights): {', '.join(creator.extras)}")
    if creator.description:
        lines.append(f"CREATOR DESCRIPTION (unverified, creator-provided, not evidence): {creator.description}")
    return "\n".join(lines) + "\n"


def build_prompt(pack: EvidencePack, niche: Niche, creator: CreatorContext | None = None) -> str:
    def note(pid: str) -> str:
        text = niche.judging_notes[pid]
        return text.replace(TREND_PREFIX, "") if pid == "trend" and pack.trends_ok else text  # the "no live trend data" prefix no longer applies

    notes = "\n".join(f"- {pid} ({name}, {'scored' if scored else 'advisory'}): {note(pid)}"
                      for pid, name, scored in PARAMETERS)
    has_context = creator is not None and bool(creator.niches or creator.description)
    return PROMPT.format(niche=niche.id, evidence=pack.render(), notes_label=NOTES_LABEL, notes=notes,
                         trend_rule=TREND_RULE_GROUNDED if pack.trends_ok else TREND_RULE_ESTIMATE,
                         fixable=", ".join(FIXABLE_BY), ids=", ".join(p[0] for p in PARAMETERS),
                         creator_block=creator_block(creator, niche) if has_context else "",
                         creator_rules="\n" + CREATOR_RULES if has_context else "",
                         fit_schema=FIT_SCHEMA if has_context and creator.description else "")


@dataclass
class ScoreOutcome:
    parameters: list[Parameter] | None = None
    description_fit: DescriptionFit | None = None
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    attempts: int = 0
    error: str | None = None
    raw: str = field(default="", repr=False)


def score_once(client: MetaClient, pack: EvidencePack, niche: Niche, model: str = config.ANALYSIS_MODEL,
               images: list[bytes] | None = None, creator: CreatorContext | None = None) -> ScoreOutcome:
    """One scoring run: the call plus one repair retry on a parse failure."""
    out = ScoreOutcome()
    prompt = build_prompt(pack, niche, creator)
    last_error = "no attempt"
    for attempt in (1, 2):
        out.attempts = attempt
        text = prompt if attempt == 1 else prompt + "\n\n" + REPAIR.format(error=last_error[:200], previous=out.raw[:6000])
        try:
            call = client.complete(text, model, temperature=config.ANALYSIS_TEMPERATURE, images=images)
        except MetaError as e:
            out.error = f"scoring call failed: {e}"
            return out
        out.cost_usd = round(out.cost_usd + call.cost_usd, 6)
        out.tokens_in += call.tokens_in or 0
        out.tokens_out += call.tokens_out or 0
        out.raw = call.text
        try:
            parsed = ScoringOutput.model_validate(parse_watch_json(call.text))
            if not parsed.parameters:
                raise ValueError("no parameters in the answer")
            out.parameters, out.error = parsed.parameters, None
            out.description_fit = parsed.description_fit
            return out
        except (ValueError, ValidationError) as e:
            last_error = str(e).replace("\n", " ")
    out.error = f"scoring answer unparseable after a repair retry: {last_error[:200]}"
    return out
