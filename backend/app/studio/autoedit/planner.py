"""Built during the Meta Global AI Developer Hackathon (new work).

The planner: Prysm's fix steps + transcript + cuts -> an EDL, by one Spark call (AUTO_EDIT_PLANNER_MODEL). The reply is validated
with edl.validate_edl; a plan-level problem sends the errors back once; whatever still breaks a global rule is trimmed in code so
a plan always comes out. Anything not executable becomes a manual tip.
"""
import json
import re
from dataclasses import dataclass, field

from app.studio import steps as steps_mod
from app.studio.autoedit import config
from app.studio.autoedit.edl import EDL, Tip, Validation, validate_edl
from app.studio.autoedit.inputs import Inputs
from app.studio.autoedit.llm import EditLLM, CostCapExceeded
from app.studio.autoedit.presets import PRESETS, resolve_style

SLUGS = {"Hook & Opening": "hook", "Retention & Pacing": "pacing", "Shareability": "share", "Audio & Music": "audio",
         "Trend Alignment": "trend", "Visual Quality & Originality": "visual", "On-Screen Text": "text",
         "Save Potential": "save", "Replay Value": "replay", "Call to Action": "cta"}
STEP_CHARS = 520


def extract_steps(analysis: dict) -> list[dict]:
    """The fix steps of every parameter as [{id, parameter, score, text}]; id = <slug>#<n> (the source_step_id of an op)."""
    out = []
    for p in (analysis.get("parameters") or []):
        name = p.get("name", "")
        slug = SLUGS.get(name, re.sub(r"\W+", "", name.lower())[:8] or "param")
        for i, s in enumerate(steps_mod.split_steps(p.get("howToDoIt") or ""), 1):
            out.append({"id": f"{slug}#{i}", "parameter": name, "score": p.get("score"), "text": s})
    return out


def existing_caption_share(analysis: dict) -> float:
    """Share of the analysed frames that already show on-screen text (burned-in captions in the upload)."""
    frames = ((analysis.get("visionData") or {}).get("frames")) or []
    if not frames:
        return 0.0
    return sum(1 for f in frames if (f.get("onScreenText") or "").strip()) / len(frames)


def _transcript_block(inp: Inputs) -> str:
    if not inp.words:
        return "(no speech detected)"
    lines, cur, start = [], [], None
    for w in inp.words:
        if start is None:
            start = w["start"]
        cur.append(f"{w['word']}@{w['start']:.2f}-{w['end']:.2f}")
        if len(cur) >= 10 or w["word"].endswith((".", "?", "!", "।")):
            lines.append(" ".join(cur))
            cur, start = [], None
    if cur:
        lines.append(" ".join(cur))
    return "\n".join(lines)


def build_prompt(analysis: dict, inp: Inputs, step_list: list[dict], preset: str, retry_errors: list[str] | None = None) -> str:
    style = resolve_style(preset)
    caps_share = existing_caption_share(analysis)
    steps_txt = "\n".join(f"[{s['id']}] ({s['parameter']}, score {s['score']}) {s['text'][:STEP_CHARS]}" for s in step_list)
    hook_moments = (analysis.get("visionData") or {}).get("hookMoment")
    ctx = {
        "duration_s": round(inp.duration, 2), "language": inp.language, "has_speech": inp.has_speech, "has_music": inp.has_music,
        "source_resolution": f"{inp.width}x{inp.height}", "existing_on_screen_text_share": round(caps_share, 2),
        "niche": analysis.get("niche"), "overall_score": analysis.get("overallScore"),
    }
    timing = {
        "analysis_timestamps": sorted({a["t"] for a in inp.analysis_times}),
        "scene_cuts": inp.scene_cuts[:40],
        "silences": [s for s in inp.silences if s.get("end") is not None][:20],
        "filler_candidates (the only words remove_filler may use)": inp.fillers[:12],
        "audio_energy_peaks": inp.energy_peaks[:6],
        "vision_hook_moment_s": hook_moments,
    }
    retry = ("\nYOUR PREVIOUS PLAN BROKE THESE RULES, FIX THEM:\n- " + "\n- ".join(retry_errors) + "\n") if retry_errors else ""
    return f"""You are the edit planner of Prysm's Auto-edit. Turn the creator's fix steps into an edit decision list (EDL) that an FFmpeg
renderer will execute on the ORIGINAL video. Output ONLY one JSON object.

VIDEO FACTS
{json.dumps(ctx, ensure_ascii=False)}

FIX STEPS FROM PRYSM'S ANALYSIS (each has an id)
{steps_txt}

TRANSCRIPT (word@start-end, seconds)
{_transcript_block(inp)}

TIMESTAMPS THAT EXIST (use ONLY times from this list, the transcript word edges above, or 0)
{json.dumps(timing, ensure_ascii=False)}

ALREADY HANDLED BY THE SYSTEM (do NOT plan these): burned-in captions, loudness / denoise, dead-air and filler-word removal. They are
measured and applied automatically. Plan only what needs judgement: the hook, zoom punches, labels, stumble cuts, effects.

OPERATION TYPES (times are seconds of the ORIGINAL video; the system assigns each op's group itself)
- cut_segment {{"start","end"}}: remove a stumble or a repeated take (not plain pauses or fillers); the renderer aligns it to word boundaries
- cold_open {{"start","end","beat_s"}}: a 1-3 s strongest moment (from the analysis timestamps / energy peaks) is shown first as a teaser, then the video starts. Only when that moment is after 3 s
- text_overlay {{"text","role":"hook|label|cta","anchor":"output|source","start","end","position":"top|center|lower"}}: hook text uses anchor "output" (shown 0-2.8 s of the NEW video) and at most 7 words / 42 chars; labels/cta use anchor "source" with times from the list (<= 36 / 48 chars). Never put a timestamp or the word 'seconds' in on-screen text, and never show two texts at the same time. Do not repeat what the creator already says or shows as text. Write the text in the language the creator speaks (Hindi / Hinglish stays Hindi / Hinglish).
- zoom_punch {{"at","scale":1.08-1.15,"duration":0.25}}: a quick punch-in at an emphasis moment (a word start / a cut / an analysis timestamp); at most one per 2.5 s, usually 2-4 per video
- sfx {{"level_db":-22}}: subtle synthesised whoosh at visible jump cuts and an impact at a cold open; only worth it for the energetic preset or fast music-driven reels, and only together with cuts
- music_duck {{"duck_db":-9}}: lowers the music under speech; only when has_music and has_speech and the music clearly competes with the voice

RULES
- At most {config.PLANNER_MAX_OPS} operations in total, each with: "id" ("o1"...), "type", "source_step_id" (the [id] of the step it implements), "target_parameter" (that step's parameter name), "reason" (one short line), "params".
- At most 25% of the video may be removed. Never cut a sentence the hook or payoff depends on.
- Never invent a timestamp. If a step needs something that is not in the lists (a new shot, re-recording, a different song, posting advice), do NOT make an operation: add it to "manual_tips" instead.
- Prefer few strong operations over many weak ones; an empty "ops" list is fine when no step needs one.
- Preset for style: "{preset}" ({json.dumps({k: v for k, v in style.items() if k in ('zoom_scale','zoom_max','cut_aggressiveness')})}); at most {style['zoom_max']} zoom punches.
{retry}
OUTPUT FORMAT
{{"ops":[{{"id":"o1","type":"...","source_step_id":"hook#1","target_parameter":"Hook & Opening","reason":"...","params":{{...}}}}],
 "manual_tips":[{{"step_id":"visual#2","parameter":"Visual Quality & Originality","tip":"...","why_manual":"needs a reshoot"}}]}}"""


@dataclass
class PlanResult:
    edl: EDL
    validation: Validation
    attempts: int = 1
    notes: list[str] = field(default_factory=list)


GROUP_DROP_ORDER = ("polish", "zoom", "text", "rhythm", "pacing", "hook")  # which group loses ops first when the plan is over a hard limit


def force_valid(edl: EDL, inp: Inputs) -> Validation:
    """Last resort after the planner retry: drop ops (least important first) until the global rules hold."""
    v = validate_edl(edl, inp)
    guard = 0
    while v.errors and v.edl.ops and guard < 30:
        guard += 1
        removable = [o for g in GROUP_DROP_ORDER for o in reversed(v.edl.ops) if o.group == g and o.type not in ("loudnorm", "caption_burn") and not o.id.startswith("auto_")]
        if not removable:
            removable = list(reversed(v.edl.ops))
        victim = removable[0]
        if any("removed" in e for e in v.errors):
            rem = [o for o in v.edl.ops if o.type in ("cut_segment", "remove_filler", "trim")]
            victim = rem[-1] if rem else victim
        v.edl.ops.remove(victim)
        v.edl.manual_tips.append(Tip(step_id=victim.source_step_id, parameter=victim.target_parameter, tip=victim.reason or victim.type,
                                     why_manual="left out to keep the edit within the safety limits"))
        nv = validate_edl(v.edl, inp)
        nv.dropped = v.dropped + [{"op": victim.model_dump(), "reason": "dropped to meet the global limits"}] + nv.dropped
        v = nv
    return v


def plan(analysis: dict, inp: Inputs, llm: EditLLM, preset: str = "clean", step_list: list[dict] | None = None) -> PlanResult:
    steps = step_list if step_list is not None else extract_steps(analysis)
    errors = None
    last: Validation | None = None
    for attempt in (1, 2):
        prompt = build_prompt(analysis, inp, steps, preset, errors)
        try:
            raw = llm.json_call(prompt, config.planner_model(), f"plan#{attempt}", max_tokens=9000, est_in_tokens=5000, reasoning_effort=config.planner_effort())
        except CostCapExceeded:
            raise
        except ValueError as e:
            errors = [f"the reply was not usable JSON: {e}"]
            last = None
            continue
        raw["style"] = {"preset": preset}
        v = validate_edl(raw, inp)
        last = v
        if v.ok:
            return PlanResult(v.edl, v, attempt)
        errors = v.errors
    if last is not None:
        v = force_valid(last.edl, inp)
        return PlanResult(v.edl, v, 2, notes=["the plan broke a global limit twice; the extra operations were trimmed automatically"])
    raise ValueError("the planner returned no usable plan twice")


def merge_auto(edl: EDL, auto_ops: list, inp: Inputs) -> tuple[Validation, list[dict]]:
    """The system's own ops (defaults.build) first, then the planner's judgement ops. The planner's loudnorm / caption_burn are dropped:
    those are decided from measurements. Returns the validated union and the notes about what was dropped."""
    notes = []
    mine = [o.model_copy(deep=True) for o in auto_ops]
    theirs = []
    for o in edl.ops:
        if o.type in ("loudnorm", "caption_burn"):
            notes.append({"area": "planner", "text": f"The planner's {o.type} was ignored: the system decides it from measurements."})
        else:
            theirs.append(o)
    merged = EDL(ops=mine + theirs, style=edl.style, manual_tips=list(edl.manual_tips))
    v = validate_edl(merged, inp)
    if v.errors:
        v = force_valid(v.edl, inp)
    return v, notes
