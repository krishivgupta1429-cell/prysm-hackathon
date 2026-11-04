"""Built during the Meta Global AI Developer Hackathon (new work).

The whole Auto-edit run on a generated clip with scripted model answers: groups are rendered, verified, kept or reverted with the
reason recorded; the cost cap stops the run but keeps what was verified; a toggle re-renders the chosen subset. Also the planner."""
import json
from pathlib import Path

import pytest

from app.studio.autoedit import edl as E, ffx, orchestrator as O, planner, storage as ST, verify
from app.studio.autoedit.llm import CostCapExceeded, CostMeter
from app.studio.tests.autoedit_helpers import WORDS, make_clip, make_inputs

PLAN_OPS = [  # what the planner contributes (judgement ops); the system adds captions, loudness and the filler cut by itself
    {"id": "o2", "type": "zoom_punch", "source_step_id": "visual#1", "target_parameter": "Visual Quality & Originality", "reason": "punch", "params": {"at": 2.0, "scale": 1.1}},
    {"id": "o3", "type": "text_overlay", "source_step_id": "hook#1", "target_parameter": "Hook & Opening", "reason": "hook text",
     "params": {"text": "Wait for it", "role": "hook", "anchor": "output", "start": 0.0, "end": 2.8, "position": "top"}},
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


def bundle_entry(verdict="approve", defects=None):
    return {"verdict": verdict, "defects": defects or [], "reasons": ["scripted"]}


class Scripted:
    """Replaces the model calls: ONE critic call (per-bundle answers) and the informational overall re-score."""

    def __init__(self, monkeypatch, answers=None, unavailable=False, fail=None):
        self.answers, self.critic_calls, self.rescore_labels = answers or {}, 0, []
        self.unavailable = unavailable
        monkeypatch.setattr(verify, "critique_all", self.critique_all)
        monkeypatch.setattr(verify, "rescore_pair", self.rescore_pair)

    def critique_all(self, before_v, after_v, bundles, llm, work, label):
        self.critic_calls += 1
        llm.meter.add("wonder", f"critic:{label}", 0.06)
        if self.unavailable:
            return {"verdict": "unavailable", "mode": "none", "bundles": {}, "notes": "down"}
        return {"verdict": "ok", "mode": "video", "notes": "scripted", "bundles": {b["id"]: self.answers.get(b["id"], bundle_entry()) for b in bundles}}

    def rescore_pair(self, before_v, after_v, bm, am, bw, aw, analysis, llm, work, label):
        self.rescore_labels.append(label)
        llm.meter.add("spark", f"rescore:{label}", 0.03)
        base = {k: 70 for k in verify.RESCORE_PARAMS}
        return {"before": base, "after": {**base, "Audio & Music": 92}, "notes": "", "label": verify.LABEL}


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = ST.LocalStorage(tmp_path / "store")
    clip = make_clip(tmp_path / "src.mp4")
    uid, aid = "u1", "a1"
    store.put_file(ST.upload_key(uid, aid), clip)
    edl = E.validate_edl({"ops": PLAN_OPS}, make_inputs()).edl
    from app.studio.autoedit import fonts as fonts_mod

    def offline(url, headers):
        raise OSError("offline in tests")
    monkeypatch.setattr(fonts_mod, "_http_get", offline)
    monkeypatch.setattr(planner, "plan", lambda analysis, inp, llm, preset="clean", steps=None: planner.PlanResult(edl, E.Validation(edl), 1))
    return store, uid, aid


def run(env, analysis=None, cap=1.5):
    store, uid, aid = env
    state = O.new_state(aid, uid)
    meter = CostMeter(cap=cap)
    r = O.Runner(state, analysis or {"niche": "Fashion", "parameters": []}, store, client=FakeClient(), meter=meter)
    return r.run(), store, meter


def enabled_ids(st):
    return {o["id"]: o["enabled"] for o in st["edl"]["ops"]}


def test_safe_bundles_are_kept_and_a_risky_one_the_critic_rejects_is_reverted_with_its_reason(env, monkeypatch):
    sc = Scripted(monkeypatch, {"zoom": bundle_entry("reject", [{"flag": "zoom_crops_face", "severity": "major", "detail": "at 2.1 s"}])})
    st, store, meter = run(env)
    assert st["status"] == "done", st["error"]
    g = st["groups"]
    assert {k: v["status"] for k, v in g.items()} == {"hook": "kept", "pacing": "kept", "captions": "kept", "audio": "kept", "zoom": "reverted"}
    assert g["zoom"]["risk"] == "risky" and "zoom_crops_face" in " ".join(g["zoom"]["reasons"])
    assert g["pacing"]["risk"] == "safe" and g["pacing"]["reasons"][0] == "objective checks pass"
    on = enabled_ids(st)
    assert on["o2"] is False and on["auto_captions"] and on["auto_loudness"] and on["auto_filler1"] and on["o3"]
    assert sc.critic_calls == 1 and sc.rescore_labels == ["overall"]            # ONE critic call and ONE (informational) re-score per edit
    assert st["final"]["key"] == "edits/a1/final.mp4" and store.exists(st["final"]["key"])
    assert st["final"]["overall_scores"]["label"].startswith("AI-estimated") and st["final"]["has_captions"]
    assert st["baseline"]["metrics"]["duration_s"] > st["final"]["metrics"]["duration_s"] - 0.05      # the filler was cut
    assert st["final"]["metrics"]["width"] == 1080 and st["final"]["metrics"]["height"] == 1920        # delivered at full size
    assert store.exists("edits/a1/captions.srt") and store.exists("edits/a1/edl.json")
    assert st["cost"]["by_service"]["wonder"]["calls"] == 1
    stages = st["timing"]["stages"]
    assert {"inputs", "plan", "proxies", "critic", "final"} <= set(stages)
    assert st["critic"]["mode"] == "video" and any(d["area"] == "audio" for d in st["decisions"])


def test_a_risky_bundle_is_kept_when_the_critic_approves(env, monkeypatch):
    Scripted(monkeypatch)
    st, _, _ = run(env)
    assert st["groups"]["zoom"]["status"] == "kept" and enabled_ids(st)["o2"] is True


def test_when_the_critic_is_unreachable_safe_bundles_are_kept_and_risky_ones_are_off(env, monkeypatch):
    Scripted(monkeypatch, unavailable=True)
    st, _, _ = run(env)
    g = st["groups"]
    assert g["pacing"]["status"] == "kept" and "objective checks alone" in " ".join(g["pacing"]["reasons"])
    assert g["zoom"]["status"] == "reverted" and "RISKY" in " ".join(g["zoom"]["reasons"])


def test_a_safe_bundle_with_a_major_critic_defect_is_reverted(env, monkeypatch):
    Scripted(monkeypatch, {"captions": bundle_entry("reject", [{"flag": "caption_timing", "severity": "major", "detail": "late at 3 s"}])})
    st, store, _ = run(env)
    assert st["groups"]["captions"]["status"] == "reverted" and "caption_timing" in " ".join(st["groups"]["captions"]["reasons"])
    assert st["final"]["has_captions"] is False and not store.exists("edits/a1/captions.srt")


def test_a_failed_objective_check_reverts_a_safe_bundle_even_if_the_critic_approves(env, monkeypatch):
    Scripted(monkeypatch)
    real = verify.objective_checks

    def failing(group, *a, **k):
        out = real(group, *a, **k)
        if group == "audio":
            out["checks"].append({"name": "loudness near -14 LUFS", "ok": False, "detail": "forced"})
            out["ok"] = False
        return out
    monkeypatch.setattr(verify, "objective_checks", failing)
    st, _, _ = run(env)
    assert st["groups"]["audio"]["status"] == "reverted" and "objective checks failed" in " ".join(st["groups"]["audio"]["reasons"])
    assert st["groups"]["pacing"]["status"] == "kept"


def test_captions_are_planned_by_default_and_left_off_with_a_reason_when_the_creator_has_their_own(env, monkeypatch):
    Scripted(monkeypatch)
    analysis = {"niche": "Fashion", "parameters": [], "visionData": {"frames": [{"onScreenText": f"hello {i}"} for i in range(4)]}}
    st, _, _ = run(env, analysis)
    cap = st["groups"]["captions"]
    assert cap["status"] == "off" and "creator's own captions" in " ".join(cap["reasons"])
    assert enabled_ids(st)["auto_captions"] is False            # still in the EDL: one click turns them on
    assert any(d["area"] == "captions" and "left off" in d["text"] for d in st["decisions"])


def two_cut_plan(monkeypatch):
    edl = E.validate_edl({"ops": PLAN_OPS + [{"id": "o4", "type": "cut_segment", "reason": "stumble", "params": {"start": 4.4, "end": 4.9}}]}, make_inputs()).edl
    monkeypatch.setattr(planner, "plan", lambda analysis, inp, llm, preset="clean", steps=None: planner.PlanResult(edl, E.Validation(edl), 1))


def test_a_bundle_of_cuts_the_critic_faults_at_one_cut_only_loses_that_cut(env, monkeypatch):
    two_cut_plan(monkeypatch)
    Scripted(monkeypatch, {"pacing": bundle_entry("reject", [{"flag": "cut_mid_word", "severity": "major", "at_s": 4.0, "detail": "chopped 'let's'"}])})
    st, _, _ = run(env)
    on = enabled_ids(st)
    assert st["groups"]["pacing"]["status"] == "kept" and on["o4"] is False and on["auto_filler1"] is True
    assert "cut_mid_word" in st["op_reverts"]["o4"] and "partly kept" in " ".join(st["groups"]["pacing"]["reasons"])
    from app.studio.autoedit.api import public_state
    ch = {c["id"]: c for c in public_state(st)["changes"]}
    assert ch["o4"]["verdict"] == "reverted" and ch["auto_filler1"]["verdict"] == "kept"


def test_a_cut_defect_without_a_time_reverts_the_whole_bundle(env, monkeypatch):
    two_cut_plan(monkeypatch)
    Scripted(monkeypatch, {"pacing": bundle_entry("reject", [{"flag": "jarring_cut", "severity": "major", "detail": "somewhere"}])})
    st, _, _ = run(env)
    assert st["groups"]["pacing"]["status"] == "reverted" and not enabled_ids(st)["o4"] and not enabled_ids(st)["auto_filler1"]


def test_a_cut_that_would_land_inside_a_word_is_dropped_on_its_own_before_rendering(tmp_path):
    words = [{"word": "so", "start": 0.5, "end": 1.0}, {"word": "uh", "start": 1.0, "end": 1.5}, {"word": "then", "start": 1.5, "end": 2.2},
             {"word": "go", "start": 3.4, "end": 3.9}, {"word": "now", "start": 4.0, "end": 4.5}]
    inp = make_inputs(duration=6.0, words=words, segments=[], fillers=[{"word": "uh", "start": 1.0, "end": 1.5}], silences=[{"start": 2.3, "end": 3.3}], scene_cuts=[],
                      energy_peaks=[], analysis_times=[])
    ops = [{"id": "f", "type": "remove_filler", "params": {"start": 1.0, "end": 1.5, "word": "uh"}},
           {"id": "s", "type": "cut_segment", "params": {"start": 2.3, "end": 3.3, "silence": True}}]
    edl = E.validate_edl({"ops": ops}, inp).edl
    r = O.Runner(O.new_state("a", "u"), {}, ST.LocalStorage(tmp_path / "s"), meter=CostMeter(), work_dir=tmp_path / "w")
    r.inp, r.faces = inp, []
    dropped = r._prune_unclean_cuts(edl)
    assert dropped == ["f"] and {o.id: o.enabled for o in edl.ops} == {"f": False, "s": True}
    assert "inside the word" in r.state["op_reverts"]["f"]


def test_the_cost_cap_stops_the_run_and_delivers_only_the_baseline(env, monkeypatch):
    sc = Scripted(monkeypatch)

    def capped(*a, **k):
        raise CostCapExceeded(1.52, 1.50)
    monkeypatch.setattr(verify, "critique_all", capped)
    st, store, _ = run(env)
    assert st["status"] == "stopped" and "cost cap" in st["error"]
    assert st["final"]["partial"] is True and store.exists("edits/a1/final.mp4")
    assert not any(enabled_ids(st).values())


def test_toggling_a_reverted_change_rerenders_with_that_subset_and_checks_objectively(env, monkeypatch):
    Scripted(monkeypatch, {"zoom": bundle_entry("reject", [{"flag": "zoom_crops_face", "severity": "major", "detail": "x"}])})
    st, store, meter = run(env)
    first = store.get_bytes(st["final"]["key"])
    spent = meter.total
    for o in st["edl"]["ops"]:
        if o["id"] == "o2":
            o["enabled"] = True                 # the creator turns the (reverted, RISKY) zoom back on
    r = O.Runner(st, {"niche": "Fashion"}, store, client=FakeClient(), meter=CostMeter.from_json(st["cost"]))
    st2 = r.rerender("zoom on")
    assert st2["status"] == "done" and st2["final"]["not_rescored"] is True
    assert store.get_bytes(st2["final"]["key"]) != first
    assert any(c["group"] == "zoom" for c in st2["final"]["objective_checks"])
    assert st2["cost"]["total_usd"] < spent + 0.01  # a re-render costs only the ~$0.0004 transcript


def test_the_edit_log_line_reports_cost_and_time(env, monkeypatch, caplog):
    Scripted(monkeypatch)
    with caplog.at_level("INFO"):
        run(env)
    line = next(r.getMessage() for r in caplog.records if "[AutoEdit] edit analysis=a1" in r.getMessage())
    assert "cost=$" in line and "wonder=$" in line and "total_s=" in line and "ops_planned=5" in line


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
    too_many = {"ops": [{"id": f"o{i}", "type": "text_overlay", "group": "text", "params": {"text": f"t{i}", "role": "label", "anchor": "source", "start": float(3 * i + 2), "end": float(3 * i + 3), "position": "top"}} for i in range(26)]}
    fine = {"ops": [{"id": "o1", "type": "loudnorm", "group": "audio", "source_step_id": "audio#1", "reason": "x", "params": {}}],
            "manual_tips": [{"step_id": "hook#2", "parameter": "Hook & Opening", "tip": "reshoot", "why_manual": "needs a reshoot"}]}
    llm = FakeLLM([too_many, fine])
    res = planner.plan(ANALYSIS, BIG, llm, "clean")
    assert res.attempts == 2 and "26 operations; the limit is 24" in llm.prompts[1] and len(res.edl.ops) == 1 and res.edl.manual_tips[0].tip == "reshoot"


def test_after_two_bad_plans_the_extra_ops_are_trimmed_so_a_plan_always_comes_out():
    too_many = {"ops": [{"id": f"o{i}", "type": "text_overlay", "group": "text", "params": {"text": f"t{i}", "role": "label", "anchor": "source", "start": float(3 * i + 2), "end": float(3 * i + 3), "position": "top"}} for i in range(26)]}
    res = planner.plan(ANALYSIS, BIG, FakeLLM([too_many, too_many]), "clean")
    assert len(res.edl.ops) <= 24 and res.validation.ok and res.notes


def test_the_planner_prompt_leaves_captions_loudness_and_dead_air_to_the_system_and_lists_only_real_timestamps():
    analysis = {**ANALYSIS, "visionData": {"frames": [{"onScreenText": "hello"}, {"onScreenText": "world"}]}}
    llm = FakeLLM([{"ops": []}])
    planner.plan(analysis, make_inputs(), llm, "minimal")
    p = llm.prompts[0]
    assert '"existing_on_screen_text_share": 1.0' in p and "ALREADY HANDLED BY THE SYSTEM" in p and "captions, loudness / denoise, dead-air and filler-word removal" in p
    assert "caption_burn {" not in p and "loudnorm {" not in p
    assert "नमस्ते@3.30-3.80" in p and "filler_candidates" in p


def test_merging_keeps_the_system_ops_and_ignores_a_planner_loudnorm_or_caption():
    from app.studio.autoedit import defaults
    inp = make_inputs(lufs=-27.0, true_peak_db=-12.0)
    auto = defaults.build(inp, {}, {"cut_aggressiveness": 0.5})
    plan_edl = E.validate_edl({"ops": [{"id": "o1", "type": "loudnorm", "group": "audio", "params": {}}, {"id": "o2", "type": "caption_burn", "group": "text", "params": {}},
                                       {"id": "o3", "type": "zoom_punch", "group": "zoom", "params": {"at": 2.0, "scale": 1.1}}]}, inp).edl
    v, notes = planner.merge_auto(plan_edl, auto.ops, inp)
    ids = [o.id for o in v.edl.ops]
    assert "auto_captions" in ids and "auto_loudness" in ids and "o3" in ids and "o1" not in ids and "o2" not in ids
    assert len(notes) == 2 and v.ok


def test_a_bundle_whose_every_change_was_skipped_is_reported_as_skipped_not_kept(env, monkeypatch):
    Scripted(monkeypatch)
    real = O.executor.plan_pieces

    def no_zoom(edl, inp, groups, faces=None):
        pc = real(edl, inp, groups, faces)
        pc.applied = [i for i in pc.applied if i != "o2"]
        pc.notes.append("zoom o2 skipped: the face fills too much of the frame to zoom in without cropping it")
        return pc
    monkeypatch.setattr(O.executor, "plan_pieces", no_zoom)
    st, _, _ = run(env)
    z = st["groups"]["zoom"]
    assert z["status"] == "skipped" and "nothing was applied" in z["reasons"][0] and "face fills" in z["reasons"][0]
    assert enabled_ids(st)["o2"] is False
