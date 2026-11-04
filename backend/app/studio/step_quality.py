"""Built during the Meta Global AI Developer Hackathon (new work).

The step-list check for each parameter of a finished analysis and a regeneration retry for any parameter that fails it.

A parameter fails when its steps are too few (fewer than 3), when a fallback line comes first (the main ready-to-say line must come
first), when Shareability or an advisory parameter has no quoted ready-to-say line at all, or when its last step is cut off. A failing
parameter's "howToDoIt" is regenerated once with a small targeted call (that parameter's own fields + the transcript + the style rules);
if that does not fix it, the better of the two versions is kept. Everything fails soft: an error leaves the text as it was.
"""
import asyncio
import re
from dataclasses import dataclass, field

from app.core.config import ADVISORY_PARAMETERS
from app.studio import coach_style, quality, steps
from app.studio.json_tolerant import extract_json_object

MIN_STEPS = 3
_READY = re.compile(r"[‘'“\"][^'’\"”]{4,200}[’'”\"]")
NEEDS_READY = {"Shareability"} | set(ADVISORY_PARAMETERS)
# A made-up figure about Prysm's own data ("from 1,000 reels we analysed ..."). Prysm has no such dataset to quote in a creator's line.
_COUNT = re.compile(r"\b\d[\d,.]*\s*(?:k|m|thousand|million)?\+?\s+(?:reels?|videos?|creators?|posts?|accounts?)\b", re.I)
_OWN_DATA_CUE = re.compile(r"\b(?:prysm|we|our|analy[sz]\w*|data|dataset|stud(?:y|ies)|benchmark\w*|research|sample|scanned|reviewed)\b", re.I)


def claims_own_data(text: str) -> bool:
    """True when `text` states a count of reels / videos / creators next to a cue that it is Prysm's own data or analysis."""
    for m in _COUNT.finditer(text or ""):
        around = (text or "")[max(0, m.start() - 70):m.end() + 70]
        if _OWN_DATA_CUE.search(around):
            return True
    return False


def problems(param: dict) -> list:
    """What is wrong with this parameter's step list (an empty list means it passes)."""
    items = steps.split_steps(param.get("howToDoIt"))
    out = []
    if len(items) < MIN_STEPS:
        out.append(f"only {len(items)} step(s); at least {MIN_STEPS} are needed")
    if items and steps.is_fallback(items[0]):
        out.append("the first step is a fallback line; the main ready-to-say line must come first")
    if param.get("name") in NEEDS_READY and items and not any(_READY.search(s) for s in items):
        out.append("no quoted ready-to-say line in the steps")
    if items and quality.looks_truncated(items[-1]):
        out.append("the last step is cut off")
    if any(claims_own_data(i) for i in items):
        out.append("a step quotes a figure about Prysm's own data (for example a number of reels analysed); no such figure may be stated")
    if any(steps.has_unclosed_quote(i) for i in items):
        out.append("a quoted phrase is opened and never closed (cut off or split across steps)")
    return out


def failing(result: dict) -> list:
    return [(p, problems(p)) for p in (result or {}).get("parameters", []) or [] if problems(p)]


def normalise_all(result: dict) -> dict:
    """Every parameter's howToDoIt as 'Step 1 — ... Step 2 — ...' (quote-aware); returns the result."""
    for p in (result or {}).get("parameters", []) or []:
        if p.get("howToDoIt"):
            p["howToDoIt"] = steps.normalise(p["howToDoIt"])
    return result


def build_prompt(param: dict, why: list, *, transcript: str, niche: str, platform: str, intent: str) -> str:
    who = ("The creator is editing this video right now: write steps for what to do before posting." if intent in ("my_video", "", None)
           else "Write the steps for the creator's own NEXT video, using this video as the example.")
    fields = "\n".join(f"- {k}: {param.get(k) or ''}" for k in ("problem", "fix", "whatIsHappening", "whyItMatters", "whatToChange", "howToDoIt"))
    return f"""You are Prysm AI, a friendly expert content coach for {platform} creators in the niche: {niche}.
Rewrite ONLY the "howToDoIt" field of one parameter of a reel analysis. {who}

PARAMETER: {param.get('name')} (score {param.get('score')}/100)
{fields}

TRANSCRIPT (for exact quotes; never invent one):
{(transcript or '')[:2500] or '(no speech)'}

WHAT IS WRONG WITH THE CURRENT STEPS: {'; '.join(why)}.

REQUIREMENTS
- Write 3 to 5 steps, formatted exactly "Step 1 — ... Step 2 — ... Step 3 — ..." (an em dash after the number, one action per step, every step a complete sentence that ends with a full stop).
- Step 1 is the MAIN action. If it involves something to say or put on screen, step 1 contains the exact ready-to-say line in single quotes. Any fallback ("Fallback: ...") comes AFTER the main steps, never first.
- Never state a figure about Prysm's own data or analysis (no "based on 1,000 reels", no counts of reels, creators or videos Prysm has seen). Numbers may only come from this video's own data.
- Never split a quoted phrase across steps, and never use "Step" inside a step.
- Keep the same advice direction as the fields above; anchor every step in a timestamp or an exact quote from this video.

{coach_style.style_text()}

Return ONLY this JSON object: {{"howToDoIt": "Step 1 — ... Step 2 — ... Step 3 — ..."}}"""


@dataclass
class RegenStats:
    regenerated: list = field(default_factory=list)       # parameter names whose steps were rewritten
    still_failing: list = field(default_factory=list)
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0


async def regenerate_failing(result: dict, *, call, transcript: str, niche: str, platform: str, intent: str,
                             max_params: int = 6) -> RegenStats:
    """Regenerate the steps of each failing parameter once. `call(prompt, max_tokens)` returns the model reply (text in reply.content[0].text,
    usage, cost_usd, reasoning_tokens). Mutates `result`; never raises."""
    stats = RegenStats()
    bad = failing(result)[:max_params]
    if not bad:
        return stats

    async def one(param, why):
        try:
            reply = await call(build_prompt(param, why, transcript=transcript, niche=niche, platform=platform, intent=intent), 1500)
        except Exception:
            return param, None, None
        return param, reply, why

    for param, reply, why in await asyncio.gather(*(one(p, w) for p, w in bad)):
        if reply is None:
            stats.still_failing.append(param.get("name"))
            continue
        stats.calls += 1
        stats.tokens_in += reply.usage.input_tokens
        stats.tokens_out += reply.usage.output_tokens
        stats.reasoning_tokens += getattr(reply, "reasoning_tokens", 0)
        stats.cost_usd += getattr(reply, "cost_usd", 0.0)
        try:
            import json
            new = json.loads(extract_json_object(reply.content[0].text)).get("howToDoIt")
            if isinstance(new, list):
                new = " ".join(str(x) for x in new)
            new = steps.normalise(new or "")
        except Exception:
            new = ""
        old_n = len(steps.split_steps(param.get("howToDoIt")))
        candidate = {**param, "howToDoIt": new}
        if new and (not problems(candidate) or len(steps.split_steps(new)) > old_n):
            param["howToDoIt"] = new
            stats.regenerated.append(param.get("name"))
        if problems(param):
            stats.still_failing.append(param.get("name"))
    stats.cost_usd = round(stats.cost_usd, 6)
    return stats
