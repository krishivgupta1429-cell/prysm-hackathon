"""Built during the Meta Global AI Developer Hackathon (new work).

The edit decision list (EDL): schema, hard validation and timestamp snapping.

All times are SOURCE-video seconds (the executor maps them to the new timeline) except an overlay with anchor "output".
The same `validate_edl` checks planner output, revisions and toggles. Per-op problems drop that op and turn it into a manual tip;
problems with the plan as a whole (too many ops, too much removed, result too long) are returned as `errors` so the planner can
be asked once more.
"""
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

from app.studio.autoedit import config
from app.studio.autoedit.inputs import Inputs, norm_token, FILLERS_EN, FILLERS_HI, snap_to_beat
from app.studio.autoedit.presets import resolve_style

GROUPS = ("hook", "pacing", "zoom", "text", "audio")  # also the order groups are rendered and verified in
OP_TYPES = ("trim", "cut_segment", "remove_filler", "cold_open", "text_overlay", "caption_burn", "zoom_punch", "loudnorm", "music_duck", "sfx")
TYPE_GROUPS = {
    "trim": {"pacing"}, "cut_segment": {"pacing"}, "remove_filler": {"pacing"}, "cold_open": {"hook"},
    "text_overlay": {"hook", "text"}, "caption_burn": {"text"}, "zoom_punch": {"zoom"}, "loudnorm": {"audio"}, "music_duck": {"audio"}, "sfx": {"audio"},
}
DEFAULT_TARGET = {"hook": "Hook & Opening", "pacing": "Retention & Pacing", "zoom": "Visual Quality & Originality",
                  "text": "On-Screen Text", "audio": "Audio & Music"}
TEXT_LIMITS = {"hook": 42, "label": 36, "cta": 48}
MAX_HOOK_WORDS = 8
REMOVING = ("trim", "cut_segment", "remove_filler")


class Op(BaseModel):
    id: str
    type: str
    group: str
    source_step_id: str | None = None
    target_parameter: str = ""
    reason: str = ""
    params: dict = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("reason")
    @classmethod
    def _short(cls, v: str) -> str:
        return (v or "").strip()[:200]


class Tip(BaseModel):
    step_id: str | None = None
    parameter: str = ""
    tip: str
    why_manual: str = ""


class EDL(BaseModel):
    version: int = 1
    ops: list[Op] = Field(default_factory=list)
    style: dict = Field(default_factory=dict)
    manual_tips: list[Tip] = Field(default_factory=list)


@dataclass
class Validation:
    edl: EDL
    dropped: list[dict] = field(default_factory=list)   # {"op", "reason"}
    errors: list[str] = field(default_factory=list)     # plan-level problems
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ------------------------------------------------------------------ timestamps

def allowed_times(inp: Inputs) -> list[float]:
    """Every timestamp that exists in the analysis text, the transcript, the detected cuts, the silence map or the energy peaks."""
    ts = {0.0, round(inp.duration, 3)}
    ts.update(a["t"] for a in inp.analysis_times)
    for w in inp.words:
        ts.update((w["start"], w["end"]))
    for s in inp.segments:
        ts.update((s["start"], s["end"]))
    ts.update(inp.scene_cuts)
    for s in inp.silences:
        ts.add(s["start"])
        if s.get("end") is not None:
            ts.add(s["end"])
    ts.update(p["t"] for p in inp.energy_peaks)
    return sorted(ts)


def snap(t: float, allowed: list[float], tol: float = config.SNAP_TOLERANCE_S) -> float | None:
    """The nearest allowed timestamp within `tol`, else None (the time is not in the analysis, so it is not used)."""
    best = min(allowed, key=lambda a: abs(a - t))
    return best if abs(best - t) <= tol else None


def _num(v, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{name} must be a number")
    return float(v)


def _snap_param(p: dict, key: str, allowed: list[float], inp: Inputs) -> None:
    t = _num(p.get(key), key)
    if not (-1e-6 <= t <= inp.duration + 1e-6):
        raise ValueError(f"{key}={t:g}s is outside the video (0-{inp.duration:.1f}s)")
    s = snap(t, allowed)
    if s is None:
        raise ValueError(f"{key}={t:g}s is not a timestamp from the analysis, the transcript or the detected cuts")
    p[key] = round(s, 3)


# ------------------------------------------------------------------ word-boundary alignment

def align_removal(a: float, b: float, words: list[dict], duration: float) -> tuple[float, float, list[dict]]:
    """Turn a requested removal into one that never cuts through a word: a word is removed when most of it lies inside [a,b],
    otherwise kept whole; the region is then widened to the gap edges (end of the last kept word before, start of the first
    kept word after). Returns (a', b', removed words). A removal with no words in it spans the whole gap it sits in."""
    removed = [w for w in words
               if max(0.0, min(b, w["end"]) - max(a, w["start"])) / max(w["end"] - w["start"], 1e-6) > 0.5]
    kept = [w for w in words if w not in removed]
    mid = (a + b) / 2
    before = [w["end"] for w in kept if w["end"] <= mid + 1e-9]
    after = [w["start"] for w in kept if w["start"] >= mid - 1e-9]
    a2 = max(before) if before else 0.0
    b2 = min(after) if after else duration
    return round(a2, 3), round(b2, 3), removed


def pad_for(a: float, b: float) -> float | None:
    """Padding kept on each side of a removal region: 60-120 ms, shrinking so the cut is still worth making; None = too small to cut."""
    room = (b - a - 0.08) / 2
    if room < config.CUT_PAD_MIN:
        return None
    return round(min(config.CUT_PAD_MAX, room), 3)


# ------------------------------------------------------------------ per-op validation

def _check_text(text, role: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text is empty")
    text = " ".join(text.split())
    limit = TEXT_LIMITS.get(role, 36)
    if len(text) > limit:
        raise ValueError(f"text is {len(text)} characters (limit {limit} for a {role})")
    if role == "hook" and len(text.split()) > MAX_HOOK_WORDS:
        raise ValueError(f"hook text has more than {MAX_HOOK_WORDS} words")
    return text


def _validate_op(op: Op, inp: Inputs, allowed: list[float]) -> None:
    """Normalises op.params in place (snapping times) or raises ValueError with the reason."""
    if op.type not in OP_TYPES:
        raise ValueError(f"unknown operation type {op.type!r}")
    if op.group not in TYPE_GROUPS[op.type]:
        raise ValueError(f"a {op.type} belongs to group {sorted(TYPE_GROUPS[op.type])}, not {op.group!r}")
    p = op.params
    dur = inp.duration
    if op.type in ("trim", "cut_segment"):
        _snap_param(p, "start", allowed, inp)
        _snap_param(p, "end", allowed, inp)
        if inp.music_driven:  # a music-driven reel cuts on the beat (within 120 ms), never at the very start / end of the file
            for k in ("start", "end"):
                if 0.3 < p[k] < dur - 0.3:
                    p[k] = round(snap_to_beat(p[k], inp.beats), 3)
        if p["end"] - p["start"] < 0.3:
            raise ValueError("range is shorter than 0.3 s")
        if op.type == "trim" and not (p["start"] <= 0.5 or p["end"] >= dur - 0.5):
            raise ValueError("a trim must keep the start or the end of the video; use cut_segment for the middle")
    elif op.type == "remove_filler":
        _snap_param(p, "start", allowed, inp)
        _snap_param(p, "end", allowed, inp)
        match = [w for w in inp.words if abs(w["start"] - p["start"]) < 0.06 and abs(w["end"] - p["end"]) < 0.06]
        if not match:
            raise ValueError("no transcript word has these boundaries")
        tok = norm_token(match[0]["word"])
        if tok not in FILLERS_EN | FILLERS_HI:
            raise ValueError(f"{match[0]['word']!r} is not on the clear-filler list")
        if not any(abs(f["start"] - p["start"]) < 0.06 for f in inp.fillers):
            raise ValueError(f"{match[0]['word']!r} is not a filler here (ambiguous words count only when isolated by pauses)")
        p["word"] = match[0]["word"]
    elif op.type == "cold_open":
        _snap_param(p, "start", allowed, inp)
        _snap_param(p, "end", allowed, inp)
        length = p["end"] - p["start"]
        if not (0.9 <= length <= 3.2):
            raise ValueError(f"cold-open moment must be 1-3 s long, got {length:.1f} s")
        if p["start"] < 3.0:
            raise ValueError("the moment is already in the first 3 s; no cold open needed")
        beat = p.get("beat_s", 0.3)
        p["beat_s"] = round(min(max(_num(beat, "beat_s"), 0.1), 0.5), 2)
    elif op.type == "text_overlay":
        role = p.get("role", "label")
        if role not in TEXT_LIMITS:
            raise ValueError(f"role must be one of {sorted(TEXT_LIMITS)}")
        p["role"] = role
        p["text"] = _check_text(p.get("text"), role)
        anchor = p.get("anchor", "source")
        if anchor not in ("source", "output"):
            raise ValueError("anchor must be 'source' or 'output'")
        p["anchor"] = anchor
        if anchor == "output":
            p["start"] = 0.0
            end = _num(p.get("end", 2.8), "end")
            p["end"] = round(min(max(end, 1.0), 4.0), 2)
        else:
            _snap_param(p, "start", allowed, inp)
            _snap_param(p, "end", allowed, inp)
            if not (0.8 <= p["end"] - p["start"] <= 6.0):
                raise ValueError("an overlay must stay on screen 0.8-6 s")
        p["position"] = p.get("position") if p.get("position") in ("top", "center", "lower") else "top"
    elif op.type == "caption_burn":
        if not inp.words:
            raise ValueError("there is no speech transcript to caption")
    elif op.type == "zoom_punch":
        _snap_param(p, "at", allowed, inp)
        p["duration"] = round(min(max(_num(p.get("duration", 0.25), "duration"), 0.15), 0.5), 2)
        p["scale"] = round(min(max(_num(p.get("scale", 1.1), "scale"), 1.05), 1.15), 3)
        if p["at"] > dur - 0.4:
            raise ValueError("too close to the end for a zoom punch")
    elif op.type == "loudnorm":
        if not inp.has_audio:
            raise ValueError("the video has no audio")
        p["target_lufs"] = round(min(max(_num(p.get("target_lufs", -14), "target_lufs"), -16), -12), 1)
        p["true_peak"] = round(min(max(_num(p.get("true_peak", -1.5), "true_peak"), -3), -1), 1)
        p["denoise"] = bool(p.get("denoise", True))
    elif op.type == "sfx":
        p["level_db"] = round(min(max(_num(p.get("level_db", -22), "level_db"), -30), -12), 1)  # always mixed low
    elif op.type == "music_duck":
        if not (inp.has_music and inp.has_speech):
            raise ValueError("music ducking needs detected music and speech")
        p["duck_db"] = round(min(max(_num(p.get("duck_db", -9), "duck_db"), -18), -3), 1)


# ------------------------------------------------------------------ whole-plan validation

def removed_seconds(ops: list[Op], inp: Inputs) -> float:
    """Total seconds taken out by the enabled removing ops (after word alignment and padding; overlaps counted once)."""
    return sum(b - a for a, b in removal_ranges(ops, inp))


def removal_ranges(ops: list[Op], inp: Inputs) -> list[tuple[float, float]]:
    """The final removed ranges (source seconds), aligned to words and padded, merged."""
    ranges = []
    for op in ops:
        if not op.enabled or op.type not in REMOVING:
            continue
        p = op.params
        if op.type == "trim":
            for a, b in ((0.0, p["start"]), (p["end"], inp.duration)):
                if b - a > 0.05:
                    ranges.append((a, b))
        else:
            ranges.append((p["start"], p["end"]))
    out = []
    for a, b in ranges:
        a2, b2, _ = align_removal(a, b, inp.words, inp.duration) if inp.words else (a, b, [])
        pad = pad_for(a2, b2)
        if pad is None:
            continue
        # a removal that starts at 0 / ends at the end keeps its padding only on the inner side
        lo = a2 if a2 <= 0.001 else a2 + pad
        hi = b2 if b2 >= inp.duration - 0.001 else b2 - pad
        if hi - lo > 0.05:
            out.append((round(lo, 3), round(hi, 3)))
    out.sort()
    merged = []
    for a, b in out:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def result_seconds(ops: list[Op], inp: Inputs) -> float:
    total = inp.duration - removed_seconds(ops, inp)
    for op in ops:
        if op.enabled and op.type == "cold_open":
            total += (op.params["end"] - op.params["start"]) + op.params.get("beat_s", 0.3)
    return total


def validate_edl(raw: dict | EDL, inp: Inputs, style_overrides: dict | None = None) -> Validation:
    """Validate and normalise an EDL against the analysis inputs. Never raises for bad content: see Validation."""
    try:
        edl = raw if isinstance(raw, EDL) else EDL.model_validate(raw)
    except Exception as e:
        return Validation(EDL(), errors=[f"the EDL is not valid JSON of the right shape: {str(e)[:200]}"])
    allowed = allowed_times(inp)
    kept, dropped, tips = [], [], list(edl.manual_tips)
    seen_ids = set()
    for i, op in enumerate(edl.ops):
        if not op.id or op.id in seen_ids:
            op.id = f"op{i + 1}"
        seen_ids.add(op.id)
        if not op.target_parameter:
            op.target_parameter = DEFAULT_TARGET.get(op.group, "")
        try:
            _validate_op(op, inp, allowed)
        except (ValueError, TypeError, KeyError) as e:
            dropped.append({"op": op.model_dump(), "reason": str(e)})
            tips.append(Tip(step_id=op.source_step_id, parameter=op.target_parameter, tip=op.reason or f"{op.type} could not be applied",
                            why_manual=f"not applied automatically: {e}"))
            continue
        kept.append(op)
    errors, notes = [], []

    # one loudnorm / caption_burn / music_duck at most
    for t in ("loudnorm", "caption_burn", "music_duck", "cold_open", "sfx"):
        same = [o for o in kept if o.type == t]
        for extra in same[1:]:
            kept.remove(extra)
            dropped.append({"op": extra.model_dump(), "reason": f"only one {t} is allowed"})

    # zoom punches at least 2.5 s apart (keep the earlier ones)
    zooms = sorted((o for o in kept if o.type == "zoom_punch"), key=lambda o: o.params["at"])
    last = None
    for z in zooms:
        if last is not None and z.params["at"] - last < config.ZOOM_MIN_GAP_S:
            kept.remove(z)
            dropped.append({"op": z.model_dump(), "reason": f"zoom punches must be {config.ZOOM_MIN_GAP_S} s apart"})
        else:
            last = z.params["at"]

    # removals must not overlap each other (keep the first)
    removers = sorted((o for o in kept if o.type in REMOVING and o.type != "trim"), key=lambda o: o.params["start"])
    prev_end = -1.0
    for r in removers:
        if r.params["start"] < prev_end - 1e-6:
            kept.remove(r)
            dropped.append({"op": r.model_dump(), "reason": "overlaps another removal"})
        else:
            prev_end = max(prev_end, r.params["end"])

    if len(kept) > config.MAX_OPS:
        errors.append(f"{len(kept)} operations; the limit is {config.MAX_OPS}")
    removed = removed_seconds(kept, inp)
    if inp.duration and removed / inp.duration > config.MAX_REMOVED_FRACTION + 1e-9:
        errors.append(f"{removed / inp.duration:.0%} of the video is removed; the limit is {config.MAX_REMOVED_FRACTION:.0%}")
    if result_seconds(kept, inp) > config.MAX_RESULT_SECONDS:
        errors.append(f"the result would be {result_seconds(kept, inp):.0f} s; the limit is {config.MAX_RESULT_SECONDS} s")
    edl.ops = kept
    edl.manual_tips = tips
    edl.style = resolve_style(edl.style.get("preset") if edl.style else None, {**(edl.style or {}), **(style_overrides or {})})
    return Validation(edl=edl, dropped=dropped, errors=errors, notes=notes)


def ops_by_group(edl: EDL) -> dict[str, list[Op]]:
    out = {g: [] for g in GROUPS}
    for op in edl.ops:
        out.setdefault(op.group, []).append(op)
    return out
