"""Built during the Meta Global AI Developer Hackathon (new work).

Plain-English revisions ("bigger captions", "less zoom", "keep the intro"). A deterministic parser handles the common requests
at no cost; anything else goes to one Spark call that returns a diff. Either way the result is a `Diff` that is applied to the
EDL and re-validated with the same rules as the planner's output (a revision that would break a rule is refused with the reason).
"""
import re
from dataclasses import dataclass, field

from app.studio.autoedit import config
from app.studio.autoedit.edl import EDL, Op, validate_edl, REMOVING
from app.studio.autoedit.inputs import Inputs
from app.studio.autoedit.llm import EditLLM
from app.studio.autoedit.presets import PRESETS, resolve_style, LIMITS

INTRO_SECONDS = 6.0


@dataclass
class Diff:
    style: dict = field(default_factory=dict)
    preset: str | None = None
    disable_ids: list[str] = field(default_factory=list)
    enable_ids: list[str] = field(default_factory=list)
    disable_types: list[str] = field(default_factory=list)
    enable_types: list[str] = field(default_factory=list)
    add_ops: list[dict] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    resolved: bool = True
    source: str = "rules"

    def empty(self) -> bool:
        return not (self.style or self.preset or self.disable_ids or self.enable_ids or self.disable_types or self.enable_types or self.add_ops)


def parse_revision(text: str, edl: EDL, inp: Inputs | None = None) -> Diff:
    """Rules for the common requests. `resolved` is False when nothing matched (the caller then asks Spark)."""
    t = " ".join((text or "").lower().split())
    d = Diff()
    style = resolve_style(edl.style.get("preset"), edl.style)

    def has(*pats):
        return any(re.search(p, t) for p in pats)

    cap_words = r"(captions?|subtitles?|text)"
    if has(rf"\b(bigger|larger|increase|enlarge)\b.*{cap_words}", rf"{cap_words}.*\b(bigger|larger)\b", r"\bmake (the )?(captions?|subtitles?|text) bigger"):
        d.style["caption_size"] = round(style["caption_size"] * 1.2)
        d.style["hook_size"] = round(style["hook_size"] * 1.1)
        d.summary.append(f"captions bigger ({style['caption_size']} -> {d.style['caption_size']} px)")
    elif has(rf"\b(smaller|reduce|decrease|shrink)\b.*{cap_words}", rf"{cap_words}.*\bsmaller\b"):
        d.style["caption_size"] = round(style["caption_size"] * 0.85)
        d.style["hook_size"] = round(style["hook_size"] * 0.9)
        d.summary.append(f"captions smaller ({style['caption_size']} -> {d.style['caption_size']} px)")
    if has(r"\b(no|remove|without|turn off|drop|disable)\b.*\bzoom", r"\bzoom\b.*\b(off|away)\b"):
        d.disable_types.append("zoom_punch")
        d.summary.append("zoom punches off")
    elif has(r"\b(less|fewer|reduce|reduced|softer|gentler|subtle|tone down)\b.*\bzoom", r"\bzoom\w*\b.*\b(less|fewer|softer|subtle)\b"):
        d.style["zoom_scale"] = round(max(1.0, style["zoom_scale"] - 0.03), 3)
        d.style["zoom_max"] = max(0, style["zoom_max"] - 1)
        d.summary.append(f"less zoom (intensity {style['zoom_scale']:.2f} -> {d.style['zoom_scale']:.2f})")
        d.disable_ids.append("__fewer_zooms__")
    elif has(r"\b(more|stronger|bigger|extra)\b.*\bzoom", r"\bzoom\w*\b.*\b(more|stronger)\b"):
        d.style["zoom_scale"] = round(min(1.15, style["zoom_scale"] + 0.03), 3)
        d.summary.append(f"more zoom (intensity {style['zoom_scale']:.2f} -> {d.style['zoom_scale']:.2f})")
    if has(r"\bkeep (the |my )?(intro|opening|start|beginning)", r"\b(don'?t|do not|never) (cut|trim|touch|shorten) (the )?(intro|opening|start|beginning)", r"\bleave (the )?(intro|opening|start|beginning)"):
        ids = [o.id for o in edl.ops if o.type in REMOVING and _starts_in_intro(o, inp)]
        d.disable_ids += ids
        d.summary.append("intro kept as recorded" + (f" ({len(ids)} cut{'s' if len(ids) != 1 else ''} off)" if ids else " (nothing was cutting it)"))
    if has(r"\b(no|remove|without|turn off|disable)\b.*\b(captions?|subtitles?)\b"):
        d.disable_types.append("caption_burn")
        d.summary.append("captions off")
    elif has(r"\b(add|turn on|enable|show|burn)\b.*\b(captions?|subtitles?)\b"):
        d.enable_types.append("caption_burn")
        d.summary.append("captions on")
    pos = re.search(r"\b(?:captions?|subtitles?)\b.*\b(?:to the |in the |at the )?(top|upper|higher|up|middle|center|centre|lower|bottom|down)\b|\b(?:move|put)\b.*\b(top|upper|middle|center|centre|lower|bottom)\b", t)
    if pos and has(r"\b(captions?|subtitles?)\b"):
        w = next(g for g in pos.groups() if g)
        d.style["caption_position"] = {"top": "upper", "upper": "upper", "higher": "upper", "up": "upper", "middle": "middle", "center": "middle",
                                       "centre": "middle", "lower": "lower", "bottom": "lower", "down": "lower"}[w]
        d.summary.append(f"captions {d.style['caption_position']}")
    if has(r"\b(no|remove|without|turn off)\b.*\b(hook text|title text|headline)", r"\bremove the (hook|title)"):
        d.disable_ids += [o.id for o in edl.ops if o.type == "text_overlay" and o.params.get("role") == "hook"]
        d.summary.append("hook text off")
    if has(r"\b(no|remove|without|skip)\b.*\bcold open", r"\bdon'?t (move|show) (the )?(moment|teaser)"):
        d.disable_types.append("cold_open")
        d.summary.append("cold open off")
    for name in PRESETS:
        if has(rf"\b(make it|switch to|use|go|try)\b.*\b{name}\b", rf"^{name}\b") and name != edl.style.get("preset"):
            d.preset = name
            d.summary.append(f"style preset {name}")
            break
    if has(r"\blouder\b", r"\bboost (the )?(volume|loudness)"):
        d.style["__loudness__"] = -12.5
        d.summary.append("louder (-12.5 LUFS target)")
    elif has(r"\bquieter\b", r"\blower (the )?(volume|loudness)"):
        d.style["__loudness__"] = -16.0
        d.summary.append("quieter (-16 LUFS target)")
    d.resolved = not d.empty()
    return d


def _starts_in_intro(op: Op, inp: Inputs | None) -> bool:
    limit = INTRO_SECONDS if inp is None else max(INTRO_SECONDS, 0.12 * inp.duration)
    p = op.params
    if op.type == "trim":
        return p.get("start", 0) > 0.05 and p["start"] < limit or p.get("start", 0) <= 0.5
    return p.get("start", 99) < limit


@dataclass
class Applied:
    edl: EDL
    style_overrides: dict
    preset: str | None
    changes: list[str]
    refused: str | None = None


def apply_diff(edl: EDL, diff: Diff, inp: Inputs, style_overrides: dict) -> Applied:
    """Apply a diff to a copy of the EDL and validate it; a result that breaks a global rule is refused."""
    new = EDL.model_validate(edl.model_dump())
    overrides = dict(style_overrides)
    changes = list(diff.summary)
    loud = diff.style.pop("__loudness__", None) if diff.style else None
    for k, v in (diff.style or {}).items():
        overrides[k] = v
    preset = diff.preset
    if preset:
        overrides = {}  # a new preset starts from its own values
    for op in new.ops:
        if op.id in diff.disable_ids or op.type in diff.disable_types:
            op.enabled = False
        if op.id in diff.enable_ids or op.type in diff.enable_types:
            op.enabled = True
        if loud is not None and op.type == "loudnorm":
            op.params["target_lufs"] = loud
            op.enabled = True
    if "__fewer_zooms__" in diff.disable_ids:
        zs = sorted((o for o in new.ops if o.type == "zoom_punch" and o.enabled), key=lambda o: o.params["at"])
        keep = max(0, resolve_style(preset or new.style.get("preset"), overrides)["zoom_max"])
        for z in zs[keep:]:
            z.enabled = False
    if diff.add_ops:
        raw = {"ops": [o.model_dump() for o in new.ops] + diff.add_ops, "manual_tips": [t.model_dump() for t in new.manual_tips]}
        for i, o in enumerate(raw["ops"]):
            if "id" not in o or not o["id"]:
                o["id"] = f"r{i + 1}"
        v = validate_edl(raw, inp)
        if v.errors:
            return Applied(edl, style_overrides, None, [], refused="; ".join(v.errors))
        if v.dropped:
            changes.append(f"{len(v.dropped)} requested change(s) could not be applied: " + "; ".join(x["reason"] for x in v.dropped[:2]))
        new = v.edl
    # whole-plan rules still hold for the enabled ops
    chk = validate_edl(EDL(ops=[o for o in new.ops if o.enabled], style={}), inp)
    if chk.errors:
        return Applied(edl, style_overrides, None, [], refused="; ".join(chk.errors))
    new.style = resolve_style(preset or new.style.get("preset"), overrides)
    return Applied(new, overrides, preset, changes)


def revise_with_spark(text: str, edl: EDL, inp: Inputs, llm: EditLLM) -> Diff:
    """One Spark call that maps a free-text request to a diff (style numbers, ops on/off, new ops from existing timestamps)."""
    from app.studio.autoedit.edl import allowed_times
    ops = [{"id": o.id, "type": o.type, "group": o.group, "enabled": o.enabled, "reason": o.reason, "params": o.params} for o in edl.ops]
    prompt = f"""A creator asked to change an automatic video edit. Map the request to a small JSON diff. Output ONLY JSON.

REQUEST: {text[:300]!r}
CURRENT STYLE: {edl.style}
CURRENT OPS: {ops}
VIDEO: {inp.duration:.1f} s. Times you may use for new ops: {allowed_times(inp)[:120]}
Style keys you may change: caption_size (52-120), hook_size, zoom_scale (1.0-1.15), zoom_max (0-8), caption_position (upper|middle|lower),
caption_color / highlight_color (RRGGBB), overlay_box (true|false), preset (clean|energetic|minimal).
You may switch ops on or off by id. You may add ops only of these types with the same params as the current ops show: cut_segment,
remove_filler, text_overlay, zoom_punch, caption_burn, loudnorm. Never invent timestamps. If the request cannot be done with these
tools, return empty lists and explain in "message".
Return {{"style": {{}}, "preset": null, "disable_ids": [], "enable_ids": [], "add_ops": [], "summary": ["what you changed, in plain words"], "message": ""}}"""
    r = llm.json_call(prompt, config.planner_model(), "revise", max_tokens=6000, est_in_tokens=3000)
    d = Diff(source="spark")
    st = r.get("style") if isinstance(r.get("style"), dict) else {}
    d.style = {k: v for k, v in st.items() if k in LIMITS or k in ("caption_position", "caption_color", "highlight_color", "overlay_box")}
    if r.get("preset") in PRESETS:
        d.preset = r["preset"]
    known = {o.id for o in edl.ops}
    d.disable_ids = [i for i in (r.get("disable_ids") or []) if i in known]
    d.enable_ids = [i for i in (r.get("enable_ids") or []) if i in known]
    d.add_ops = [o for o in (r.get("add_ops") or []) if isinstance(o, dict)][:4]
    d.summary = [str(s)[:160] for s in (r.get("summary") or [])][:4] or ([str(r.get("message"))[:160]] if r.get("message") else [])
    d.resolved = not d.empty()
    return d
