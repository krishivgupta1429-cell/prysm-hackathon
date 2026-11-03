"""Built during the Meta Global AI Developer Hackathon (new work).

The whole Auto-edit run on a generated clip with scripted model answers: groups are rendered, verified, kept or reverted with the
reason recorded; the cost cap stops the run but keeps what was verified; a toggle re-renders the chosen subset. Also the planner."""
import json
from pathlib import Path

import pytest

from app.studio.autoedit import edl as E, ffx, orchestrator as O, planner, storage as ST, verify
from app.studio.autoedit.llm import CostCapExceeded, CostMeter
from app.studio.tests.autoedit_helpers import WORDS, make_clip, make_inputs

OPS = [
    {"id": "o1", "type": "remove_filler", "group": "pacing", "source_step_id": "pacing#1", "target_parameter": "Retention & Pacing", "reason": "um", "params": {"start": 1.0, "end": 1.3, "word": "um"}},
    {"id": "o2", "type": "zoom_punch", "group": "zoom", "source_step_id": "visual#1", "target_parameter": "Visual Quality & Originality", "reason": "punch", "params": {"at": 2.0, "scale": 1.1}},
    {"id": "o3", "type": "caption_burn", "group": "text", "source_step_id": "text#1", "target_parameter": "On-Screen Text", "reason": "captions", "params": {}},
    {"id": "o4", "type": "loudnorm", "group": "audio", "source_step_id": "audio#1", "target_parameter": "Audio & Music", "reason": "loud", "params": {}},
]


class FakeClient:
    """A recogniser that hears what is in the file: words after the filler move earlier by however much audio was cut out."""

    def whisper(self, wav):
        import soundfile as sf
        shift = max(0.0, 6.0 - sf.info(str(wav)).duration)
        shift = shift if shift < 2.0 else 0.0  # (a cold open would make the file longer, not shorter)

        class R:
            words = [{"word": w, "start": s - (shift if s > 1.3 else 0.0), "end": e - (shift if s > 1.3 else 0.0)} for w, s, e in WORDS if w != "um" or shift == 0.0]
            segments = []
            language = "Hinglish"
            cost_usd = 0.0004
        return R()


def scripted(plan):
    """plan = {group: (target gain, critic verdict)}; the re-score and critic are replaced by these answers."""
    def rescore(before_v, after_v, bm, am, bw, aw, analysis, llm, work, label):
        gain = plan.get(label, (0, "approve"))[0]
        base = {k: 70 for k in verify.RESCORE_PARAMS}
        tgt = {"pacing": "Retention & Pacing", "zoom": "Visual Quality & Originality", "text": "On-Screen Text", "audio": "Audio & Music", "overall": "Retention & Pacing"}[label]
        llm.meter.add("spark", f"rescore:{label}", 0.03)
        return {"before": base, "after": {**base, tgt: 70 + gain}, "notes": "", "label": verify.LABEL}

    def critique(before_v, after_v, group, ops, llm, work, label):
        llm.meter.add("wonder", f"critic:{label}", 0.06)
        v = plan.get(group, (0, "approve"))[1]
        return {"verdict": v, "reasons": ["scripted"], "flags": [], "mode": "video"}
    return rescore, critique


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = ST.LocalStorage(tmp_path / "store")
    clip = make_clip(tmp_path / "src.mp4")
    uid, aid = "u1", "a1"
    store.put_file(ST.upload_key(uid, aid), clip)
    edl = E.validate_edl({"ops": OPS}, make_inputs()).edl
    monkeypatch.setattr(planner, "plan", lambda analysis, inp, llm, preset="clean", steps=None: planner.PlanResult(edl, E.Validation(edl), 1))
    return store, uid, aid


def run(env, plan, cap=1.5, tmp_path=None):
    store, uid, aid = env
    state = O.new_state(aid, uid)
    meter = CostMeter(cap=cap)
    r = O.Runner(state, {"niche": "Fashion", "parameters": []}, store, client=FakeClient(), meter=meter)
    return r.run(), store, meter


def test_groups_are_kept_or_reverted_with_reasons_and_the_final_has_only_the_kept_ones(env, monkeypatch):
    rescore, critique = scripted({"pacing": (3, "approve"), "zoom": (4, "reject"), "text": (0, "approve"), "audio": (5, "approve"), "overall": (6, "approve")})
    monkeypatch.setattr(verify, "rescore_pair", rescore)
    monkeypatch.setattr(verify, "critique", critique)
    st, store, meter = run(env, None)
    assert st["status"] == "done", st["error"]
    g = st["groups"]
    assert (g["pacing"]["status"], g["audio"]["status"]) == ("kept", "kept")
    assert g["zoom"]["status"] == "reverted" and "critic rejected" in " ".join(g["zoom"]["reasons"])
    assert g["text"]["status"] == "reverted" and "needs at least +2" in " ".join(g["text"]["reasons"])
    enabled = {o["id"]: o["enabled"] for o in st["edl"]["ops"]}
    assert enabled == {"o1": True, "o2": False, "o3": False, "o4": True}
    assert st["final"]["key"] == "edits/a1/final.mp4" and store.exists(st["final"]["key"])
    assert st["final"]["overall_scores"]["label"].startswith("AI-estimated")
    assert st["baseline"]["metrics"]["duration_s"] > st["final"]["metrics"]["duration_s"]        # the filler was cut
    assert store.exists("edits/a1/edl.json") and not store.exists("edits/a1/captions.srt")        # captions were reverted
    assert [t["kind"] for t in st["trace"] if t["kind"] in ("kept", "reverted")] == ["kept", "reverted", "reverted", "kept"]
    assert st["cost"]["total_usd"] == pytest.approx(meter.total) and st["cost"]["by_service"]["wonder"]["calls"] == 4


def test_a_failed_objective_check_reverts_without_spending_on_ai_calls(env, monkeypatch):
    rescore, critique = scripted({})
    called = []
    monkeypatch.setattr(verify, "rescore_pair", lambda *a, **k: called.append(a[-1]) or rescore(*a, **k))
    monkeypatch.setattr(verify, "critique", critique)
    real = verify.objective_checks

    def failing(group, *a, **k):
        out = real(group, *a, **k)
        if group == "audio":
            out["checks"].append({"name": "loudness near -14 LUFS", "ok": False, "detail": "forced"})
            out["ok"] = False
        return out
    monkeypatch.setattr(verify, "objective_checks", failing)
    st, _, _ = run(env, None)
    assert st["groups"]["audio"]["status"] == "reverted" and "objective checks failed" in " ".join(st["groups"]["audio"]["reasons"])
    assert "audio" not in called and "pacing" in called   # no AI call was spent on the group whose objective check failed


def test_the_cost_cap_stops_the_run_and_keeps_what_was_already_verified(env, monkeypatch):
    rescore, critique = scripted({"pacing": (3, "approve"), "zoom": (4, "approve")})

    def capped(before_v, after_v, bm, am, bw, aw, analysis, llm, work, label):
        if label == "zoom":
            raise CostCapExceeded(1.52, 1.50)
        return rescore(before_v, after_v, bm, am, bw, aw, analysis, llm, work, label)
    monkeypatch.setattr(verify, "rescore_pair", capped)
    monkeypatch.setattr(verify, "critique", critique)
    st, store, _ = run(env, None)
    assert st["status"] == "stopped" and "cost cap" in st["error"]
    assert st["groups"]["pacing"]["status"] == "kept" and st["groups"]["zoom"]["status"] == "skipped"
    assert st["final"]["partial"] is True and store.exists("edits/a1/final.mp4")
    assert {o["id"]: o["enabled"] for o in st["edl"]["ops"]} == {"o1": True, "o2": False, "o3": False, "o4": False}


def test_toggling_a_change_rerenders_with_that_subset_and_checks_objectively(env, monkeypatch):
    rescore, critique = scripted({"pacing": (3, "approve"), "audio": (5, "approve"), "overall": (6, "approve")})
    monkeypatch.setattr(verify, "rescore_pair", rescore)
    monkeypatch.setattr(verify, "critique", critique)
    st, store, meter = run(env, None)
    first = store.get_bytes(st["final"]["key"])
    spent = meter.total
    for o in st["edl"]["ops"]:
        if o["id"] == "o3":
            o["enabled"] = True                 # the creator turns the (reverted) captions on
    r = O.Runner(st, {"niche": "Fashion"}, store, client=FakeClient(), meter=CostMeter.from_json(st["cost"]))
    st2 = r.rerender("captions on")
    assert st2["status"] == "done" and st2["final"]["not_rescored"] is True and st2["final"]["has_captions"]
    assert store.get_bytes(st2["final"]["key"]) != first and store.exists("edits/a1/captions.srt") and store.exists("edits/a1/captions.ass")
    assert any(c["group"] == "text" and c["name"].startswith("text inside") for c in st2["final"]["objective_checks"])
    assert st2["cost"]["total_usd"] < spent + 0.01  # a re-render costs only the ~$0.0004 transcript


def test_the_edit_log_line_reports_cost_and_time(env, monkeypatch, caplog):
    rescore, critique = scripted({"audio": (5, "approve")})
    monkeypatch.setattr(verify, "rescore_pair", rescore)
    monkeypatch.setattr(verify, "critique", critique)
    with caplog.at_level("INFO"):
        run(env, None)
    line = next(r.getMessage() for r in caplog.records if "[AutoEdit] edit analysis=a1" in r.getMessage())
    assert "cost=$" in line and "wonder=$" in line and "total_s=" in line and "ops_planned=4" in line


# ---- planner

class FakeLLM:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []
        self.meter = CostMeter()

    def json_call(self, prompt, model, label, **kw):
        self.prompts.append(prompt)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


ANALYSIS = {"niche": "Fashion", "overallScore": 61, "parameters": [
    {"name": "Hook & Opening", "score": 60, "howToDoIt": "Step 1 — Add a hook text at 0.0s-2.8s that says 'Wait for it'. Step 2 — Reshoot the opening with better light."},
    {"name": "Audio & Music", "score": 50, "howToDoIt": "Step 1 — Normalise the voice loudness before posting."}]}


BIG = make_inputs(duration=80.0, scene_cuts=[float(t) for t in range(0, 80)], silences=[], words=[], segments=[], energy_peaks=[], analysis_times=[], fillers=[])


def test_steps_get_ids_and_timestamps_are_harvested():
    steps = planner.extract_steps(ANALYSIS)
    assert [s["id"] for s in steps] == ["hook#1", "hook#2", "audio#1"]
    from app.studio.autoedit.inputs import parse_times
    assert [t for t, _ in parse_times(steps[0]["text"], 6.0)] == [0.0, 2.8]


def test_the_planner_retries_once_with_the_errors_and_then_accepts():
    too_many = {"ops": [{"id": f"o{i}", "type": "text_overlay", "group": "text", "params": {"text": f"t{i}", "role": "label", "anchor": "source", "start": float(3 * i + 2), "end": float(3 * i + 3), "position": "top"}} for i in range(16)]}
    fine = {"ops": [{"id": "o1", "type": "loudnorm", "group": "audio", "source_step_id": "audio#1", "reason": "x", "params": {}}],
            "manual_tips": [{"step_id": "hook#2", "parameter": "Hook & Opening", "tip": "reshoot", "why_manual": "needs a reshoot"}]}
    llm = FakeLLM([too_many, fine])
    res = planner.plan(ANALYSIS, BIG, llm, "clean")
    assert res.attempts == 2 and "16 operations; the limit is 14" in llm.prompts[1] and len(res.edl.ops) == 1 and res.edl.manual_tips[0].tip == "reshoot"


def test_after_two_bad_plans_the_extra_ops_are_trimmed_so_a_plan_always_comes_out():
    too_many = {"ops": [{"id": f"o{i}", "type": "text_overlay", "group": "text", "params": {"text": f"t{i}", "role": "label", "anchor": "source", "start": float(3 * i + 2), "end": float(3 * i + 3), "position": "top"}} for i in range(16)]}
    res = planner.plan(ANALYSIS, BIG, FakeLLM([too_many, too_many]), "clean")
    assert len(res.edl.ops) <= 14 and res.validation.ok and res.notes


def test_the_planner_prompt_forbids_captions_on_a_video_that_already_has_them_and_lists_only_real_timestamps():
    analysis = {**ANALYSIS, "visionData": {"frames": [{"onScreenText": "hello"}, {"onScreenText": "world"}]}}
    llm = FakeLLM([{"ops": []}])
    planner.plan(analysis, make_inputs(), llm, "minimal")
    p = llm.prompts[0]
    assert '"existing_on_screen_text_share": 1.0' in p and "Do NOT add it when existing_on_screen_text_share >= 0.4" in p
    assert "नमस्ते@3.30-3.80" in p and "filler_candidates" in p
