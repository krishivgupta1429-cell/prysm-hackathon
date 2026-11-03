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


def test_keep_when_the_target_gains_2_nothing_drops_over_3_objective_passes_and_critic_approves():
    d = verify.decide("pacing", "Retention & Pacing", scores({}, {"Retention & Pacing": 72, "Shareability": 67}), True, [], OK_CRITIC)
    assert d["keep"] and "Retention & Pacing +2" in d["reasons"][0]


@pytest.mark.parametrize("after,why", [
    ({"Retention & Pacing": 71}, "needs at least +2"),                               # +1 is not enough
    ({"Retention & Pacing": 75, "Hook & Opening": 66}, "dropped by more than 3"),    # a scored parameter fell by 4
])
def test_revert_when_the_score_rule_is_not_met(after, why):
    d = verify.decide("pacing", "Retention & Pacing", scores({}, after), True, [], OK_CRITIC)
    assert not d["keep"] and why in " ".join(d["reasons"])


def test_a_drop_of_exactly_3_is_allowed():
    assert verify.decide("pacing", "Retention & Pacing", scores({}, {"Retention & Pacing": 75, "Hook & Opening": 67}), True, [], OK_CRITIC)["keep"]


def test_revert_when_an_objective_check_fails_even_if_everything_else_is_great():
    d = verify.decide("text", "On-Screen Text", scores({}, {"On-Screen Text": 90}), False, ["text inside safe area (3 frames outside)"], OK_CRITIC)
    assert not d["keep"] and "objective checks failed" in d["reasons"][-1]


def test_revert_when_the_critic_rejects_or_raises_a_blocking_flag_or_is_missing():
    s = scores({}, {"Retention & Pacing": 80})
    rej = verify.decide("pacing", "Retention & Pacing", s, True, [], {"verdict": "reject", "reasons": ["cuts the CTA"], "flags": [], "mode": "video"})
    assert not rej["keep"] and "cuts the CTA" in " ".join(rej["reasons"])
    flag = verify.decide("pacing", "Retention & Pacing", s, True, [], {"verdict": "approve", "reasons": [], "flags": [{"flag": "cut_mid_word", "detail": "x"}], "mode": "video"})
    assert not flag["keep"] and "cut_mid_word" in " ".join(flag["reasons"])
    gone = verify.decide("pacing", "Retention & Pacing", s, True, [], {"verdict": "unavailable", "reasons": [], "flags": []})
    assert not gone["keep"] and "could not be verified" in " ".join(gone["reasons"])
    soft = verify.decide("pacing", "Retention & Pacing", s, True, [], {"verdict": "approve", "reasons": [], "flags": [{"flag": "caption_timing", "detail": "x"}], "mode": "video"})
    assert soft["keep"]  # a non-blocking flag is recorded, not a veto


def test_revert_when_there_is_no_score_for_the_target():
    assert not verify.decide("pacing", "Retention & Pacing", None, True, [], OK_CRITIC)["keep"]
    assert not verify.decide("pacing", "Retention & Pacing", {"before": {"Hook & Opening": 70}, "after": {"Hook & Opening": 80}}, True, [], OK_CRITIC)["keep"]


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
