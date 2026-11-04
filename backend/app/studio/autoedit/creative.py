"""Built during the Meta Global AI Developer Hackathon (new work).

The "creative director" call (Spark, one call, runs in parallel with the planner):
  * picks a style pack and suggests 3 fonts (headline / caption) from niche, tone and language, with a one-line reason each;
  * singles out emphasis words (at most ~15 % of the words) for a bigger size / the accent colour;
  * labels the moments that matter for retention (hook, curiosity/reveal, shock, funny fail, transition, emphasis) and chooses ONE fix
    for each drop point from the analysis' retention findings: sound accent, zoom, caption emphasis, jump cut or trim.
Everything the model returns is validated in code: packs and fonts must exist, fonts must cover the video's script, times must be real
timestamps (snapped), emphasis is capped, and density limits are enforced by edl.validate_edl. A failed call falls back to a deterministic
pack and no sound effects; nothing in the edit depends on this call succeeding.
"""
import json
import math
from dataclasses import dataclass, field

from app.studio.autoedit import config, fonts, packs, sfxlib
from app.studio.autoedit.edl import Op, allowed_times, snap
from app.studio.autoedit.inputs import Inputs
from app.studio.autoedit.llm import CostCapExceeded, EditLLM

MAX_EMPHASIS_SHARE = 0.15
FIXES = ("sfx", "zoom", "caption_emphasis", "jump_cut", "trim")


@dataclass
class Creative:
    pack: str
    pack_reason: str
    font_suggestions: list[dict] = field(default_factory=list)   # [{family, role, reason}] up to 3
    headline: str | None = None
    caption: str | None = None
    emphasis_times: list[float] = field(default_factory=list)    # source start times of the emphasised words
    moments: list[dict] = field(default_factory=list)            # [{t, kind, fix, finding, why}]
    notes: list[str] = field(default_factory=list)
    from_model: bool = False

    def to_json(self) -> dict:
        return {"pack": self.pack, "pack_reason": self.pack_reason, "fonts": self.font_suggestions, "headline": self.headline, "caption": self.caption,
                "emphasis_count": len(self.emphasis_times), "moments": self.moments, "notes": self.notes, "from_model": self.from_model}


def retention_findings(analysis: dict) -> dict:
    """What the analysis says about where viewers drop: the retention / hook fix text, the energy curve's low stretches and drop point."""
    out = {"retention": {}, "hook": {}, "energy": {}}
    for p in (analysis.get("parameters") or []):
        if p.get("name") in ("Retention & Pacing", "Hook & Opening"):
            key = "retention" if p["name"].startswith("Retention") else "hook"
            out[key] = {"score": p.get("score"), "problem": (p.get("problem") or "")[:420], "fix": (p.get("fix") or "")[:420]}
    ec = analysis.get("energyCurve") or {}
    out["energy"] = {"low_energy_segments": ec.get("low_energy_segments"), "drop_at_seconds": ec.get("drop_at_seconds"), "peak_energy_time": ec.get("peak_energy_time")}
    ep = analysis.get("engagementPrediction") or {}
    out["risk"] = {"hook_risk": ep.get("hook_risk"), "retention_risk": ep.get("retention_risk")}
    return out


def _allowed(inp: Inputs) -> list[float]:
    ts = {a["t"] for a in inp.analysis_times}
    ts.update(inp.scene_cuts[:30])
    ts.update(p["t"] for p in inp.energy_peaks)
    for i, w in enumerate(inp.words):
        if i == 0 or w["start"] - inp.words[i - 1]["end"] > 0.3 or inp.words[i - 1]["word"].rstrip().endswith((".", "?", "!", "।")):
            ts.add(w["start"])
    return sorted(round(t, 2) for t in ts if 0.5 <= t <= inp.duration - 0.5)


def build_prompt(analysis: dict, inp: Inputs, intensity: str) -> str:
    words = " ".join(f"{i}:{w['word']}" for i, w in enumerate(inp.words[:420]))
    pack_txt = "\n".join(f"- {k}: {v['label']} ({v['headline']} + {v['caption']}, {v['animation']}); for {v['for']}" for k, v in packs.PACKS.items())
    n_max = max(2, math.ceil(inp.duration / 3))
    ctx = {"niche": analysis.get("niche"), "language": inp.language, "duration_s": round(inp.duration, 1), "has_music": inp.has_music,
           "summary": (analysis.get("summary") or analysis.get("storyUnderstanding") or "")[:400], "hook_type": ((analysis.get("visionData") or {}).get("hookType")),
           "intensity": intensity}
    devanagari = any(fonts.is_devanagari(w["word"]) for w in inp.words)
    return f"""You are the creative director of an automatic editor for Instagram Reels. Return ONLY one JSON object.

VIDEO
{json.dumps(ctx, ensure_ascii=False)}
Speech contains Devanagari (Hindi): {devanagari}

STYLE PACKS
{pack_txt}

RETENTION FINDINGS FROM THE ANALYSIS (where viewers drop)
{json.dumps(retention_findings(analysis), ensure_ascii=False)}

TRANSCRIPT WORDS (index:word)
{words or '(no speech)'}

REAL TIMESTAMPS YOU MAY USE for moments (seconds): {json.dumps(_allowed(inp))}

TASKS
1. "pack": the best pack id for this niche, tone and language, with "pack_reason" (one line).
2. "fonts": exactly 3 suggestions from Google Fonts, each {{"family": "<exact Google Fonts family>", "role": "headline" or "caption", "reason": "<one line>"}}. At least one headline and one caption. If the speech has Devanagari the caption font MUST support Devanagari (e.g. Hind, Mukta, Baloo 2, Noto Sans Devanagari, Poppins). Readable at small sizes; no script/handwriting fonts for captions.
3. "emphasis": word INDICES (at most 15 % of the words, usually 6-12) that carry the meaning or a number or a punch word, to be shown bigger / in the accent colour. Never more than one per 4 words.
4. "moments": at most {n_max} moments, each {{"t": <a real timestamp from the list>, "kind": "hook|curiosity|shock|funny|transition|emphasis", "fix": "sfx|zoom|caption_emphasis|jump_cut|trim", "finding": "<the retention finding or drop point this answers, short>", "why": "<one line>"}}. Choose ONE fix per drop point. Spread moments out: at least 1.5 s apart. "sfx" = a short synthesised sound accent, only where it clearly helps (a reveal, a shock, a funny fail, a cut); never over speech that matters.
Output: {{"pack": "...", "pack_reason": "...", "fonts": [...], "emphasis": [...], "moments": [...]}}"""


def fallback(analysis: dict, inp: Inputs, note: str) -> Creative:
    pack, reason = packs.default_pack(inp.language, analysis.get("niche"))
    return Creative(pack, reason, notes=[note])


def decide(analysis: dict, inp: Inputs, llm: EditLLM, intensity: str = "balanced") -> Creative:
    try:
        raw = llm.json_call(build_prompt(analysis, inp, intensity), config.planner_model(), "creative", max_tokens=5000, est_in_tokens=4500,
                            reasoning_effort=config.planner_effort())
    except CostCapExceeded:
        raise
    except Exception as e:
        return fallback(analysis, inp, f"the style call failed ({type(e).__name__}: {str(e)[:80]}): a default pack was chosen without a model")
    return validate(raw, analysis, inp)


def validate(raw: dict, analysis: dict, inp: Inputs) -> Creative:
    notes: list[str] = []
    pack = raw.get("pack") if raw.get("pack") in packs.PACKS else None
    if pack is None:
        pack, why = packs.default_pack(inp.language, analysis.get("niche"))
        notes.append(f"the model's pack was not one of ours; {pack} chosen: {why}")
        reason = why
    else:
        reason = str(raw.get("pack_reason") or "")[:160]
    needs_dv = any(fonts.is_devanagari(w["word"]) for w in inp.words)
    fams = fonts.families()
    sugg: list[dict] = []
    for f in (raw.get("fonts") or []):
        if not isinstance(f, dict) or f.get("family") not in fams or f.get("role") not in ("headline", "caption"):
            continue
        meta = fams[f["family"]]
        if f["role"] == "caption" and needs_dv and "devanagari" not in meta["s"]:
            notes.append(f"{f['family']} was suggested for captions but has no Devanagari; skipped")
            continue
        if f["role"] == "caption" and meta["c"] in ("handwriting",):
            notes.append(f"{f['family']} is a handwriting font: not used for captions")
            continue
        sugg.append({"family": f["family"], "role": f["role"], "reason": str(f.get("reason") or "")[:140], "category": meta["c"]})
    sugg = sugg[:3]
    head = next((s["family"] for s in sugg if s["role"] == "headline"), None)
    cap = next((s["family"] for s in sugg if s["role"] == "caption"), None)
    # emphasis: indices -> source start times, capped
    cap_n = int(len(inp.words) * MAX_EMPHASIS_SHARE)
    idx = []
    for i in (raw.get("emphasis") or []):
        if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(inp.words) and (not idx or i - idx[-1] >= 3):
            idx.append(i)
    idx = sorted(set(idx))[:cap_n]
    emph = [round(inp.words[i]["start"], 2) for i in idx]
    if len(raw.get("emphasis") or []) > len(idx):
        notes.append(f"emphasis was capped to {len(idx)} words (15 % of {len(inp.words)})")
    # moments: snapped to real timestamps, at least 1.5 s apart
    allowed = allowed_times(inp)
    moments, last = [], -9.0
    for m in sorted((m for m in (raw.get("moments") or []) if isinstance(m, dict) and isinstance(m.get("t"), (int, float)) and not isinstance(m.get("t"), bool)), key=lambda m: m["t"]):
        t = snap(float(m["t"]), allowed)
        if t is None or t - last < sfxlib.MIN_EFFECT_GAP_S or not (0.2 <= t <= inp.duration - 0.4):
            continue
        kind = m.get("kind") if m.get("kind") in sfxlib.MOMENTS else "emphasis"
        fix = m.get("fix") if m.get("fix") in FIXES else "sfx"
        moments.append({"t": round(t, 2), "kind": kind, "fix": fix, "finding": str(m.get("finding") or "")[:160], "why": str(m.get("why") or "")[:160]})
        last = t
    return Creative(pack, reason, sugg, head, cap, emph, moments, notes, from_model=True)


def moment_ops(c: Creative, inp: Inputs, existing_cut_times: list[float]) -> tuple[list[Op], list[dict]]:
    """The ops the director's moments turn into, and tips for fixes the system does not make itself. One fix per moment.
    sfx -> sfx_hit; zoom -> zoom_punch; caption_emphasis -> already in `emphasis`; jump_cut / trim -> answered by the pause-tightening ops
    when one lies within 1.5 s, else a manual tip."""
    ops: list[Op] = []
    tips: list[dict] = []
    for i, m in enumerate(c.moments, 1):
        base = dict(target_parameter="Retention & Pacing" if m["fix"] != "sfx" else "Audio & Music", finding=m["finding"], reason=m["why"] or f"{m['kind']} moment")
        if m["fix"] == "sfx":
            ops.append(Op(id=f"fx{i}", type="sfx_hit", **base, params={"at": m["t"], "kind": m["kind"]}))
        elif m["fix"] == "zoom":
            ops.append(Op(id=f"mz{i}", type="zoom_punch", **base, params={"at": m["t"], "scale": 1.1, "duration": 0.25}))
        elif m["fix"] in ("jump_cut", "trim"):
            if not any(abs(t - m["t"]) <= 1.5 for t in existing_cut_times):
                tips.append({"parameter": "Retention & Pacing", "tip": f"Around {m['t']:.0f} s: {m['finding'] or m['why']} ({m['fix'].replace('_', ' ')})",
                             "why_manual": "no silence or pause there to cut automatically"})
    return ops, tips
