"""Built during the Meta Global AI Developer Hackathon (new work).

Wonder vs Spark director probe (start of Stage 2; a probe only, there is no executor and no verify loop here).

Question: which Meta model should PLAN the automatic fix edits of a reel, muse-wonder-1.0 or muse-spark-1.3?
The director task is defined once, here: input = the analysis's edit intents (given ids I1..In) plus the evidence
pack (plus keyframes, or the video itself, where the model accepts them); output = STRICT JSON, an edit plan that uses
only the op vocabulary trim, reorder, caption, text_overlay, audio_balance, music_swap, speed_ramp, zoom_crop,
hook_rewrite, rotate. Each op has start/end seconds, params, a reason and the intent id it addresses. A rotate
direction may only be stated if the evidence or keyframes show it, else it is "unknown".

Automatic checks per run: valid JSON, schema valid, all timestamps inside the video, no conflicting overlapping ops,
every intent covered or explicitly declined with a reason, plus latency, tokens and cost in USD from the published
prices. Quality (specific? sensible? timestamp-accurate?) is judged by reading the raw outputs: see docs/director-probe.md.

Usage (from backend/):  python -m app.studio.spikes.director_probe --budget 1.9
Reuses the cached analyses under samples/studio_runs/<reel>/ (analysis.json, report.json, media/); nothing is re-run.
Raw outputs go to samples/studio_runs/director_probe/ (gitignored).
"""
import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError, field_validator

from app.studio import config
from app.studio.meta_client import MetaClient, MetaError
from app.studio.stages.analysis import keyframes
from app.studio.stages.watch import parse_watch_json

OPS = ("trim", "reorder", "caption", "text_overlay", "audio_balance", "music_swap", "speed_ramp", "zoom_crop", "hook_rewrite", "rotate")
# Ground truth for the probe's reels, checked by looking at the frames: Video-86506 is stored sideways with the head
# pointing right, so it must be turned counter-clockwise (ffmpeg transpose=2) to stand upright.
ROTATE_TRUTH = {"Video-86506": "ccw90"}
EXCLUSIVE = {"trim", "reorder", "speed_ramp", "zoom_crop", "hook_rewrite"}  # these cannot act on the same seconds twice
LAYERED = {"caption", "text_overlay", "audio_balance", "music_swap"}  # two of the same kind on the same seconds collide
ROTATE_DIRECTIONS = ("cw90", "ccw90", "180", "unknown")
OVERLAP_TOLERANCE_S = 0.05
MODES = ("text", "images", "video")
SPARK = config.ANALYSIS_MODEL
WONDER = config.WONDER_MODEL


# ---------------------------------------------------------------- the plan schema

class Op(BaseModel):
    op: str
    start_s: float | None = None
    end_s: float | None = None
    params: dict = {}
    reason: str
    intent_id: str

    @field_validator("op")
    @classmethod
    def known_op(cls, v):
        if v not in OPS:
            raise ValueError(f"op {v!r} is not in the vocabulary")
        return v

    @field_validator("reason")
    @classmethod
    def has_reason(cls, v):
        if not str(v).strip():
            raise ValueError("an op needs a reason")
        return v


class Declined(BaseModel):
    intent_id: str
    reason: str


class Plan(BaseModel):
    ops: list[Op]
    declined: list[Declined] = []


# ---------------------------------------------------------------- the case (one reel's cached analysis)

@dataclass
class Case:
    reel: str
    duration_s: float
    width: int
    height: int
    intents: list[dict]  # each with "id" (I1..In)
    evidence_lines: list[str]
    evidence_text: str
    frames_dir: Path | None = None
    proxy: Path | None = None
    meta: dict = field(default_factory=dict)

    @property
    def intent_ids(self) -> list[str]:
        return [i["id"] for i in self.intents]


def load_case(reel_dir: Path) -> Case:
    """Build a case from samples/studio_runs/<reel>/ (analysis.json + report.json + media/)."""
    analysis = json.loads((reel_dir / "analysis.json").read_text())
    report = json.loads((reel_dir / "report.json").read_text())
    intents = []
    for n, raw in enumerate(analysis.get("edit_intents") or [], 1):
        params = raw.get("parameters") or {}
        intents.append({"id": f"I{n}", "intent": raw.get("intent"), "target": raw.get("target"), "title": params.get("title"),
                        "steps": params.get("steps") or [], "source_parameter": raw.get("source_parameter"),
                        "rationale": raw.get("rationale"), "evidence": raw.get("evidence") or []})
    lines = []
    for item in (analysis.get("evidence_pack") or {}).get("items") or []:
        when = ""
        if item.get("t_start") is not None:
            t0, t1 = item["t_start"], item.get("t_end")
            when = f"[{t0:g}s]" if t1 is None or abs(t1 - t0) < 0.05 else f"[{t0:g}-{t1:g}s]"
        lines.append(f"{item['id']} {when} {item['source']}: {item['text']}".replace("  ", " "))
    video = report["video"]
    media = reel_dir / "media"
    return Case(reel=reel_dir.name, duration_s=float(video["duration_s"]), width=int(video["width"]), height=int(video["height"]),
                intents=intents, evidence_lines=lines, evidence_text="\n".join(lines),
                frames_dir=media / "frames" if (media / "frames").exists() else None,
                proxy=media / "proxy.720p.mp4" if (media / "proxy.720p.mp4").exists() else None)


# ---------------------------------------------------------------- the director task

PROMPT = """You are the director who plans the automatic edit of one short vertical reel ({duration:g} seconds, {width}x{height}). An analysis already found what to fix; you turn its edit intents into a concrete edit plan that a program will execute with FFmpeg.

EDIT INTENTS (id, type, where, what to do):
{intents}

EVIDENCE (facts about the video; ids and timestamps in seconds):
{evidence}

Reply with ONE JSON object only (no prose, no code fences):
{{"ops": [{{"op": "text_overlay", "start_s": 0.0, "end_s": 3.0, "params": {{"text": "..."}}, "reason": "one sentence tied to the evidence", "intent_id": "I1"}}],
  "declined": [{{"intent_id": "I5", "reason": "why this intent cannot or should not be done automatically"}}]}}

RULES
1. "op" must be one of: {ops}. No other operations exist.
2. Every op has start_s and end_s (seconds, 0 <= start_s <= end_s <= {duration:g}); only "rotate" and "music_swap" may use null for both (they apply to the whole video).
3. Every op names the intent it addresses in "intent_id" and gives a one-sentence "reason" that points at the evidence.
4. Every intent id must either be addressed by at least one op or appear in "declined" with a reason. Do not invent intents.
5. No two ops of these kinds may overlap in time: trim, reorder, speed_ramp, zoom_crop, hook_rewrite. Two ops of the same kind among caption, text_overlay, audio_balance, music_swap must not overlap either.
6. Suggested params: trim {{"remove": true}}; reorder {{"move_to_s": 0.0}}; caption {{"text": "...", "position": "bottom"}}; text_overlay {{"text": "...", "position": "top_safe", "style": "..."}}; audio_balance {{"voice_db": 0, "music_db": -18}}; music_swap {{"suggestion": "..."}}; speed_ramp {{"speed": 1.1}}; zoom_crop {{"zoom": 1.15, "focus": "face"}}; hook_rewrite {{"new_hook_text": "...", "spoken_line": "..."}}; rotate {{"direction": "cw90|ccw90|180|unknown"}}.
7. rotate: you may state a direction (cw90, ccw90 or 180) ONLY if the evidence lines or the attached frames/video clearly show which way the video is rotated. Otherwise the direction must be "unknown". Never guess.
8. Use only facts from the evidence{visual_note}. Keep text short."""

VISUAL_NOTES = {"text": "", "images": " and the attached keyframes", "video": " and the attached video"}


def render_intents(case: Case) -> str:
    out = []
    for i in case.intents:
        t = i["target"]
        where = "whole video" if not t else f"{t['start_s']:g}-{t['end_s']:g}s"
        steps = " | ".join(i["steps"])[:300]
        out.append(f"{i['id']} {i['intent']} ({where}) from {i['source_parameter']}: {i['title']}. {steps} Why: {i['rationale']}. Evidence: {', '.join(i['evidence'])}")
    return "\n".join(out)


def build_prompt(case: Case, mode: str) -> str:
    return PROMPT.format(duration=case.duration_s, width=case.width, height=case.height, intents=render_intents(case),
                         evidence=case.evidence_text, ops=", ".join(OPS), visual_note=VISUAL_NOTES[mode])


# ---------------------------------------------------------------- the automatic checks

def parse_plan(text: str) -> tuple[dict | None, str | None]:
    """(plan dict, None) or (None, why not): the reply must be one JSON object."""
    try:
        data = parse_watch_json(text or "")
    except ValueError as e:
        return None, f"not valid JSON: {str(e)[:120]}"
    if not isinstance(data, dict):
        return None, "JSON is not an object"
    return data, None


def schema_check(data: dict) -> tuple[Plan | None, str | None]:
    try:
        return Plan.model_validate(data), None
    except ValidationError as e:
        first = e.errors()[0]
        return None, f"{'.'.join(str(x) for x in first['loc'])}: {first['msg']}"[:160]


def timestamp_check(plan: Plan, duration_s: float) -> list[str]:
    problems = []
    for n, op in enumerate(plan.ops):
        both_null = op.start_s is None and op.end_s is None
        if both_null:
            if op.op not in ("rotate", "music_swap"):
                problems.append(f"ops[{n}] {op.op}: no start/end")
            continue
        if op.start_s is None or op.end_s is None:
            problems.append(f"ops[{n}] {op.op}: only one of start/end given")
        elif not (0 <= op.start_s <= op.end_s <= duration_s + 0.05):
            problems.append(f"ops[{n}] {op.op}: {op.start_s:g}-{op.end_s:g}s is outside 0-{duration_s:g}s")
    return problems


def _overlap(a: Op, b: Op) -> bool:
    if a.start_s is None or b.start_s is None or a.end_s is None or b.end_s is None:
        return a.start_s is None and b.start_s is None  # two whole-video ops of the same kind
    return min(a.end_s, b.end_s) - max(a.start_s, b.start_s) > OVERLAP_TOLERANCE_S


def conflict_check(plan: Plan) -> list[str]:
    problems = []
    for i, a in enumerate(plan.ops):
        for b in plan.ops[i + 1:]:
            same_exclusive = a.op in EXCLUSIVE and b.op in EXCLUSIVE
            same_layer = a.op in LAYERED and a.op == b.op
            whole_rotate = a.op == "rotate" == b.op
            if (same_exclusive or same_layer or whole_rotate) and _overlap(a, b):
                problems.append(f"{a.op}@{a.start_s}-{a.end_s} overlaps {b.op}@{b.start_s}-{b.end_s}")
    return problems


def coverage_check(plan: Plan, intent_ids: list[str]) -> list[str]:
    covered = {op.intent_id for op in plan.ops}
    declined = {d.intent_id for d in plan.declined if d.reason.strip()}
    problems = [f"{i} neither addressed nor declined" for i in intent_ids if i not in covered | declined]
    problems += [f"{i} is not an intent of this video" for i in (covered | {d.intent_id for d in plan.declined}) if i not in intent_ids]
    return problems


DIRECTION_WORDS = {"cw90": ("clockwise", "cw"), "ccw90": ("counterclockwise", "counter-clockwise", "anticlockwise", "ccw"), "180": ("upside down", "180")}


def rotate_check(plan: Plan, evidence_text: str, visual: bool = False, truth: str | None = None) -> list[str]:
    """A rotate direction other than "unknown" needs the evidence to say which way: otherwise it is a guess.

    With frames or video (visual=True) the model can see the answer: a stated direction then passes only if it equals
    the known ground truth (if we have one); without it a visual claim cannot be verified automatically and fails."""
    problems = []
    text = evidence_text.lower()
    for n, op in enumerate(plan.ops):
        if op.op != "rotate":
            continue
        direction = str((op.params or {}).get("direction", "unknown")).lower()
        if direction not in ROTATE_DIRECTIONS:
            problems.append(f"ops[{n}] rotate: direction {direction!r} is not one of {ROTATE_DIRECTIONS}")
        elif direction != "unknown" and not any(w in text for w in DIRECTION_WORDS[direction]):
            if visual and truth == direction:
                continue  # read correctly from the frames
            if visual and truth:
                problems.append(f"ops[{n}] rotate: states {direction} from the frames but the video needs {truth}")
            else:
                problems.append(f"ops[{n}] rotate: states {direction} but the evidence does not show the direction")
    return problems


def run_checks(text: str, case: Case, mode: str = "text") -> dict:
    """All automatic checks for one reply: {check: {"pass": bool, "detail": ...}} plus the parsed plan (or None)."""
    checks: dict[str, dict] = {}
    data, err = parse_plan(text)
    checks["valid_json"] = {"pass": data is not None, "detail": err}
    plan, err = (schema_check(data) if data is not None else (None, "no JSON"))
    checks["schema"] = {"pass": plan is not None, "detail": err}
    for name, fn in (("timestamps", lambda: timestamp_check(plan, case.duration_s)), ("no_conflicts", lambda: conflict_check(plan)),
                     ("intents_covered", lambda: coverage_check(plan, case.intent_ids)), ("rotate_rule", lambda: rotate_check(plan, case.evidence_text, mode != "text", ROTATE_TRUTH.get(case.reel)))):
        problems = fn() if plan is not None else ["no valid plan"]
        checks[name] = {"pass": not problems, "detail": "; ".join(problems)[:300] or None}
    checks["_plan"] = plan.model_dump() if plan else None
    return checks


# ---------------------------------------------------------------- running a model

@dataclass
class RunResult:
    reel: str
    model: str
    mode: str
    status: str  # "ran" | "not_accepted" | "error" | "skipped_budget"
    seconds: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float = 0.0
    checks: dict = field(default_factory=dict)
    error: str | None = None
    raw_file: str | None = None
    ops: int = 0
    declined: int = 0

    def passed(self) -> int:
        return sum(1 for k, v in self.checks.items() if not k.startswith("_") and v["pass"])


def run_one(client: MetaClient, case: Case, model: str, mode: str, out_dir: Path) -> RunResult:
    result = RunResult(case.reel, model, mode, "ran")
    prompt = build_prompt(case, mode)
    stem = f"{case.reel}_{model}_{mode}"
    try:
        if mode == "text":
            call = client.complete(prompt, model, temperature=0.2)
        elif mode == "images":
            frames = keyframes(case.frames_dir)
            if not frames:
                result.status, result.error = "error", "no keyframes"
                return result
            call = client.complete(prompt, model, temperature=0.2, images=frames)
        else:
            if case.proxy is None:
                result.status, result.error = "error", "no proxy video"
                return result
            call = client.watch_video(case.proxy, prompt, model)
    except MetaError as e:
        result.status = "not_accepted" if e.status and 400 <= e.status < 500 else "error"
        result.error = str(e)[:300]
        return result
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.txt").write_text(config.redact(call.text))
    result.raw_file = f"{stem}.txt"
    result.seconds, result.tokens_in, result.tokens_out, result.cost_usd = call.seconds, call.tokens_in, call.tokens_out, call.cost_usd
    result.checks = run_checks(call.text, case, mode)
    plan = result.checks.get("_plan") or {}
    result.ops, result.declined = len(plan.get("ops", [])), len(plan.get("declined", []))
    return result


PLAN = [(SPARK, "text"), (SPARK, "images"), (WONDER, "text"), (WONDER, "images"), (WONDER, "video")]  # cheap runs first, the video run last


def run_probe(cases: list[Case], out_dir: Path, budget_usd: float, client: MetaClient | None = None, plan=PLAN) -> list[RunResult]:
    """Every (model, mode) on every reel, stopping before the budget is exceeded (the rest are marked skipped_budget)."""
    client = client or MetaClient()
    results, spent = [], 0.0
    for model, mode in plan:
        for case in cases:
            if spent >= budget_usd:
                results.append(RunResult(case.reel, model, mode, "skipped_budget", error=f"budget ${budget_usd:g} reached"))
                continue
            r = run_one(client, case, model, mode, out_dir)
            spent += r.cost_usd
            results.append(r)
            print(f"{case.reel:12} {model:18} {mode:6} {r.status:13} {r.seconds:6.1f}s ${r.cost_usd:.4f} "
                  f"checks {r.passed()}/6 total ${spent:.3f}", flush=True)
    return results


# ---------------------------------------------------------------- the report table

CHECKS = ("valid_json", "schema", "timestamps", "no_conflicts", "intents_covered", "rotate_rule")
SHORT = {"valid_json": "json", "schema": "schema", "timestamps": "times", "no_conflicts": "overlap", "intents_covered": "covered", "rotate_rule": "rotate"}


def render_table(results: list[RunResult]) -> str:
    head = "| Model | Input | Reel | Status | " + " | ".join(SHORT[c] for c in CHECKS) + " | Ops / declined | Latency | Tokens in/out | Cost |"
    rows = [head, "|" + "---|" * (len(CHECKS) + 8)]
    for r in results:
        marks = [("pass" if r.checks.get(c, {}).get("pass") else "FAIL") if r.checks else "-" for c in CHECKS]
        tokens = f"{r.tokens_in or 0:,}/{r.tokens_out or 0:,}" if r.status == "ran" else "-"
        rows.append(f"| {r.model} | {r.mode} | {r.reel} | {r.status} | " + " | ".join(marks) +
                    f" | {r.ops} / {r.declined} | {r.seconds:.1f} s | {tokens} | ${r.cost_usd:.3f} |")
    return "\n".join(rows)


def summarise(results: list[RunResult]) -> dict:
    """Per (model, mode): runs, runs with every check passing, mean latency and cost, total cost."""
    out: dict[str, dict] = {}
    for r in results:
        key = f"{r.model} / {r.mode}"
        s = out.setdefault(key, {"runs": 0, "ran": 0, "all_checks": 0, "seconds": 0.0, "cost": 0.0})
        s["runs"] += 1
        if r.status == "ran":
            s["ran"] += 1
            s["all_checks"] += int(r.passed() == len(CHECKS))
            s["seconds"] += r.seconds
        s["cost"] += r.cost_usd
    for s in out.values():
        s["mean_seconds"] = round(s["seconds"] / s["ran"], 1) if s["ran"] else None
        s["cost"] = round(s["cost"], 4)
    return out


def rescore(results: list[RunResult], cases: dict[str, Case], out_dir: Path) -> list[RunResult]:
    """Recompute the automatic checks from the saved raw outputs (no API calls), e.g. after refining a check."""
    for r in results:
        if r.status == "ran" and r.raw_file:
            r.checks = run_checks((out_dir / r.raw_file).read_text(), cases[r.reel], r.mode)
            plan = r.checks.get("_plan") or {}
            r.ops, r.declined = len(plan.get("ops", [])), len(plan.get("declined", []))
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs-dir", type=Path, default=Path("../samples/studio_runs"))
    ap.add_argument("--reels", nargs="+", default=["Video-54720", "Video-57419", "Video-86506"])
    ap.add_argument("--budget", type=float, default=1.9, help="stop starting new runs once this many USD were spent")
    ap.add_argument("--rescore", action="store_true", help="recompute the checks from the saved outputs; no API calls")
    args = ap.parse_args(argv)
    cases = [load_case(args.runs_dir / reel) for reel in args.reels]
    out_dir = args.runs_dir / "director_probe"
    t0 = time.time()
    if args.rescore:
        saved = [RunResult(**r) for r in json.loads((out_dir / "results.json").read_text())]
        results = rescore(saved, {c.reel: c for c in cases}, out_dir)
    else:
        results = run_probe(cases, out_dir, args.budget)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps([r.__dict__ for r in results], indent=1, default=str))
    print(render_table(results))
    print(json.dumps(summarise(results), indent=1))
    print(f"total ${sum(r.cost_usd for r in results):.3f} in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
