"""Built during the Meta Global AI Developer Hackathon (new work).

The keep-or-revert rule, the cost cap, objective checks and the revision parsing (no network)."""
import pytest

from app.studio.autoedit import config, revise, verify
from app.studio.autoedit import edl as E
from app.studio.autoedit.llm import CostCapExceeded, CostMeter, EditLLM
from app.studio.tests.autoedit_helpers import make_inputs

OK_CRITIC = {"verdict": "approve", "reasons": ["better"], "flags": [], "mode": "video"}


def scores(before, after):
    base = {k: 70 for k in verify.RESCORE_PARAMS}
    return {"before": {**base, **before}, "after": {**base, **after}}


def critic(**bundles):
    return {"verdict": "ok", "mode": "video", "notes": "", "bundles": bundles}


APPROVE = {"verdict": "approve", "defects": [], "reasons": []}
MAJOR = {"verdict": "reject", "defects": [{"flag": "cut_mid_word", "severity": "major", "detail": "at 3.2 s"}], "reasons": ["cut inside a word"]}
MINOR = {"verdict": "approve", "defects": [{"flag": "tone_mismatch", "severity": "minor", "detail": "slightly loud"}], "reasons": []}


def test_a_safe_bundle_is_kept_when_objective_checks_pass_and_the_critic_reports_no_defect():
    d = verify.decide_bundle("pacing", True, [], critic(pacing=APPROVE))
    assert d["keep"] and d["risk"] == "safe" and "objective checks pass" in d["reasons"][0]
    assert verify.decide_bundle("captions", True, [], critic(captions={"verdict": "unknown", "defects": [], "reasons": []}))["keep"]  # silence from the critic is not a defect


def test_a_safe_bundle_with_a_minor_note_is_still_kept_and_the_note_is_shown():
    d = verify.decide_bundle("audio", True, [], critic(audio=MINOR))
    assert d["keep"] and any("minor notes" in r for r in d["reasons"])


def test_a_safe_bundle_is_reverted_for_a_major_defect_or_a_failed_objective_check():
    d = verify.decide_bundle("pacing", True, [], critic(pacing=MAJOR))
    assert not d["keep"] and "cut_mid_word" in " ".join(d["reasons"])
    d = verify.decide_bundle("captions", False, ["captions in sync (400 ms)"], critic(captions=APPROVE))
    assert not d["keep"] and "objective checks failed" in " ".join(d["reasons"])


def test_a_safe_bundle_is_kept_on_the_objective_checks_alone_when_the_critic_is_unreachable():
    d = verify.decide_bundle("audio", True, [], {"verdict": "unavailable", "mode": "none", "bundles": {}})
    assert d["keep"] and "objective checks alone" in " ".join(d["reasons"])


def test_a_risky_bundle_needs_an_explicit_critic_approval():
    assert verify.decide_bundle("zoom", True, [], critic(zoom=APPROVE))["keep"]
    d = verify.decide_bundle("zoom", True, [], critic(zoom={"verdict": "unknown", "defects": [], "reasons": []}))
    assert not d["keep"] and "RISKY" in " ".join(d["reasons"])
    d = verify.decide_bundle("zoom", True, [], {"verdict": "unavailable", "mode": "none", "bundles": {}})
    assert not d["keep"] and "could not verify" in " ".join(d["reasons"])
    assert not verify.decide_bundle("rhythm", False, ["x"], critic(rhythm=APPROVE))["keep"]
    assert not verify.decide_bundle("polish", True, [], critic(polish=MAJOR))["keep"]


def test_the_ai_rescore_plays_no_part_in_the_decision():
    import inspect
    assert "score" not in inspect.signature(verify.decide_bundle).parameters


class FakeCriticLLM:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def json_call(self, prompt, model, label, **kw):
        self.calls.append((label, bool(kw.get("videos")), bool(kw.get("images"))))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


BUNDLES = [{"id": "pacing", "risk": "safe", "ops": [{"type": "cut_segment", "params": {"start": 1.0, "end": 1.4}}]},
           {"id": "zoom", "risk": "risky", "ops": [{"type": "zoom_punch", "params": {"at": 2.0, "scale": 1.1}}]}]


def test_one_critic_call_judges_every_bundle(tmp_path):
    llm = FakeCriticLLM([{"bundles": {"pacing": {"verdict": "approve", "defects": []},
                                      "zoom": {"verdict": "reject", "reasons": ["crops the face"], "defects": [{"flag": "zoom_crops_face", "severity": "major", "detail": "2.1 s"}]}},
                         "notes": "fine overall"}])
    out = verify.critique_all(tmp_path / "a.mp4", tmp_path / "b.mp4", BUNDLES, llm, tmp_path, "edit")
    assert len(llm.calls) == 1 and llm.calls[0][1] is True
    assert out["verdict"] == "ok" and out["mode"] == "video" and out["bundles"]["zoom"]["defects"][0]["flag"] == "zoom_crops_face"
    assert out["bundles"]["pacing"]["verdict"] == "approve"


def test_a_bundle_the_critic_skipped_is_unknown(tmp_path):
    out = verify.critique_all(tmp_path / "a.mp4", tmp_path / "b.mp4", BUNDLES, FakeCriticLLM([{"bundles": {"pacing": {"verdict": "approve"}}}]), tmp_path, "edit")
    assert out["bundles"]["zoom"]["verdict"] == "unknown"


def test_the_critic_falls_back_to_frames_and_then_to_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "_jpegs", lambda *a, **k: [b"x"])
    llm = FakeCriticLLM([RuntimeError("video refused"), {"bundles": {"pacing": {"verdict": "approve"}, "zoom": {"verdict": "approve"}}}])
    out = verify.critique_all(tmp_path / "a.mp4", tmp_path / "b.mp4", BUNDLES, llm, tmp_path, "edit")
    assert out["mode"] == "frames" and [c[1:] for c in llm.calls] == [(True, False), (False, True)]
    out = verify.critique_all(tmp_path / "a.mp4", tmp_path / "b.mp4", BUNDLES, FakeCriticLLM([RuntimeError("no"), RuntimeError("no")]), tmp_path, "edit")
    assert out["verdict"] == "unavailable"


def test_caption_sync_reports_the_median_offset():
    cap = [{"word": w, "start": 1.0 + i * 0.5} for i, w in enumerate(["this", "is", "a", "test", "of", "sync"])]
    asr = [{"word": w, "start": 1.05 + i * 0.5} for i, w in enumerate(["This", "is", "a", "test.", "of", "sync"])]
    r = verify.caption_sync(cap, asr)
    assert r["matched_words"] == 6 and r["median_offset_ms"] == -50


# ---- cost cap

def test_the_cost_meter_totals_by_service_and_refuses_past_the_cap():
    m = CostMeter(cap=1.50)
    m.add("spark", "plan", 0.40)
    m.add("wonder", "critic", 0.55)
    m.add("modal", "demucs", 0.01)
    assert m.total == pytest.approx(0.96) and m.by_service()["wonder"]["usd"] == 0.55
    m.check(0.5)  # 1.46 <= 1.5
    with pytest.raises(CostCapExceeded):
        m.check(0.6)


def test_a_call_that_could_exceed_the_cap_is_refused_before_it_is_made(monkeypatch):
    m = CostMeter(cap=1.50)
    m.add("spark", "so far", 1.499)

    class NoNetwork:
        def _headers(self):
            raise AssertionError("no request may be made once the cap is reached")

    llm = EditLLM(m, NoNetwork())
    with pytest.raises(CostCapExceeded):
        llm.chat([{"type": "text", "text": "hi"}], "muse-wonder-1.0", 6000, "critic")


def test_the_cap_default_and_env_override(monkeypatch):
    monkeypatch.delenv("AUTO_EDIT_MAX_COST_USD", raising=False)
    monkeypatch.setattr("app.studio.config.get_env", lambda n: None)
    assert config.max_cost_usd() == 1.50
    monkeypatch.setattr("app.studio.config.get_env", lambda n: "0.75" if n == "AUTO_EDIT_MAX_COST_USD" else None)
    assert config.max_cost_usd() == 0.75


# ---- revision parsing

def edl_with(ops=None, style=None):
    inp = make_inputs()
    raw = {"ops": ops or [
        {"id": "o1", "type": "cut_segment", "group": "pacing", "params": {"start": 1.3, "end": 1.8}},
        {"id": "o2", "type": "zoom_punch", "group": "zoom", "params": {"at": 2.0, "scale": 1.1}},
        {"id": "o3", "type": "zoom_punch", "group": "zoom", "params": {"at": 4.9, "scale": 1.1}},
        {"id": "o4", "type": "caption_burn", "group": "text", "params": {}},
        {"id": "o5", "type": "loudnorm", "group": "audio", "params": {}}], "style": style or {"preset": "clean"}}
    return E.validate_edl(raw, inp).edl, inp


def test_bigger_captions_changes_the_size():
    e, inp = edl_with()
    d = revise.parse_revision("bigger captions", e, inp)
    out = revise.apply_diff(e, d, inp, {})
    assert out.edl.style["caption_size"] > e.style["caption_size"] and out.refused is None


def test_less_zoom_lowers_intensity_and_the_number_of_punches():
    e, inp = edl_with()
    out = revise.apply_diff(e, revise.parse_revision("less zoom", e, inp), inp, {})
    assert out.edl.style["zoom_scale"] < e.style["zoom_scale"]
    assert sum(1 for o in out.edl.ops if o.type == "zoom_punch" and o.enabled) <= out.edl.style["zoom_max"]


def test_no_zoom_switches_every_punch_off():
    e, inp = edl_with()
    out = revise.apply_diff(e, revise.parse_revision("remove the zoom effects", e, inp), inp, {})
    assert not any(o.enabled for o in out.edl.ops if o.type == "zoom_punch") and all(o.enabled for o in out.edl.ops if o.type != "zoom_punch")


def test_keep_the_intro_switches_off_cuts_in_the_opening_but_not_later_ones():
    e, inp = edl_with()
    inp = make_inputs(duration=40.0, silences=[], scene_cuts=[])
    early = E.Op(id="a", type="cut_segment", group="pacing", params={"start": 1.3, "end": 1.8})
    late = E.Op(id="b", type="cut_segment", group="pacing", params={"start": 20.0, "end": 21.0})
    head_trim = E.Op(id="c", type="trim", group="pacing", params={"start": 0.8, "end": 40.0})
    assert revise._starts_in_intro(early, inp) and not revise._starts_in_intro(late, inp) and revise._starts_in_intro(head_trim, inp)
    d = revise.parse_revision("please keep the intro", e, inp)
    assert d.resolved and d.disable_ids == ["o1"] and "intro kept" in d.summary[0]


def test_caption_position_preset_and_loudness_requests():
    e, inp = edl_with()
    assert revise.apply_diff(e, revise.parse_revision("move the captions to the top", e, inp), inp, {}).edl.style["caption_position"] == "upper"
    assert revise.apply_diff(e, revise.parse_revision("make it energetic", e, inp), inp, {}).edl.style["preset"] == "energetic"
    out = revise.apply_diff(e, revise.parse_revision("make it louder", e, inp), inp, {})
    assert next(o for o in out.edl.ops if o.type == "loudnorm").params["target_lufs"] == -12.5


def test_an_unrecognised_request_is_unresolved_for_spark_and_spark_diffs_are_validated():
    e, inp = edl_with()
    assert not revise.parse_revision("make it feel more cinematic", e, inp).resolved

    class FakeLLM:
        def json_call(self, *a, **k):
            return {"style": {"caption_size": 9999, "bogus": 1}, "disable_ids": ["o1", "nope"], "add_ops": [{"type": "zoom_punch", "group": "zoom", "params": {"at": 33.0}}],
                    "summary": ["did it"], "message": ""}
    d = revise.revise_with_spark("something odd", e, inp, FakeLLM())
    assert d.style == {"caption_size": 9999} and d.disable_ids == ["o1"]
    out = revise.apply_diff(e, d, inp, {})
    assert out.edl.style["caption_size"] == 120                      # clamped
    assert any("could not be applied" in c for c in out.changes)      # the zoom at 33 s is not a real timestamp (video is 6 s)


def test_a_revision_that_breaks_a_global_rule_is_refused():
    e, inp = edl_with()
    d = revise.Diff(add_ops=[{"type": "trim", "group": "pacing", "params": {"start": 3.0, "end": 6.0}}])
    out = revise.apply_diff(e, d, inp, {})
    assert out.refused and "removed" in out.refused and out.edl is e
