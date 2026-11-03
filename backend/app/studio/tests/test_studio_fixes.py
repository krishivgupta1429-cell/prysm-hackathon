"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests (no network, no waiting) for the Stage 1D fixes: Gemini retry with exponential backoff, the
audio_listener_missing cap, the "rotate" fix and intent, the evidence-limited Trend Alignment label, and the
repeat aggregation (default 2 runs) including an unstable case.
"""
import json

import pytest

from app.studio import config
from app.studio.analysis import niches
from app.studio.analysis.aggregate import aggregate_runs
from app.studio.analysis.evidence import build_evidence_pack
from app.studio.analysis.intents import build_edit_intents, rotate_direction
from app.studio.analysis.overall import compute_overall
from app.studio.analysis.schema import FIXABLE_BY, EvidenceRef, Fix, Issue, Parameter, Target
from app.studio.analysis.score import build_prompt
from app.studio.analysis.validate import validate_parameters
from app.studio.meta_client import GeminiCall, MetaError
from app.studio.report import AnalysisStage, ParamScore
from app.studio.stages import audio as audio_stage
from app.studio.stages.analysis import run_analysis
from app.studio.tests.test_studio_analysis import FakeClient, make_report, scoring_json

GEMINI_OK = json.dumps({"transcript": "he knelt", "language": "English", "speech_ratio": 0.8,
                        "music": {"present": True, "description": "pop", "mood": "x", "energy_0_10": 5},
                        "voice_quality": {"clarity_0_10": 8, "noise": "none", "issues": []},
                        "av_sync": {"ok": True, "notes": "ok"}, "silence_segments": [], "loudness_issues": [],
                        "hook_audio": "sung line"})


class ScriptedGemini:
    """Gemini client whose answers per model are scripted; records every call and its `retries` argument."""
    def __init__(self, plan):
        self.plan = {m: list(v) for m, v in plan.items()}
        self.calls = []

    def gemini(self, model, prompt, path, mime, retries=None):
        self.calls.append((model, retries))
        item = self.plan[model].pop(0)
        if isinstance(item, Exception):
            raise item
        return GeminiCall(text=item, model=model, tokens_in=10, tokens_out=5, thinking_tokens=1, cost_usd=0.001, seconds=0.1)


def e503():
    return MetaError("gemini: gave up after 1 attempts: _Retryable: HTTP 503: overloaded", status=503)


@pytest.fixture
def slept(monkeypatch):
    waits = []
    monkeypatch.setattr(audio_stage, "_sleep", waits.append)  # record instead of waiting
    return waits


@pytest.fixture
def proxy(tmp_path):
    p = tmp_path / "proxy.mp4"
    p.write_bytes(b"x" * 100)
    return p


# ---------------------------------------------------------------- 1. Gemini backoff

def test_the_backoff_schedule_is_2_4_8_16_30_seconds_with_up_to_25_percent_jitter():
    class Fixed:
        def __init__(self, v):
            self.v = v

        def uniform(self, a, b):
            return self.v * b

    assert audio_stage.backoff_delays(Fixed(0)) == [2, 4, 8, 16, 30]
    assert audio_stage.backoff_delays(Fixed(1)) == [2.5, 5, 10, 20, 37.5]
    for _ in range(50):
        for d, base in zip(audio_stage.backoff_delays(), audio_stage.BACKOFF_SECONDS):
            assert base <= d <= base * 1.25


def test_which_failures_are_retried():
    t = audio_stage.is_transient
    assert t(MetaError("x", status=503)) and t(MetaError("x", status=429)) and t(MetaError("x", timeout=True))
    assert not t(MetaError("quota", status=429)) is False  # a plain 429 is retried
    assert not t(MetaError("HTTP 429: Quota exceeded ... limit: 0, model: gemini-2.5-pro", status=429))  # a zero quota never clears
    assert not t(MetaError("x", status=404)) and not t(MetaError("x", status=400)) and not t(MetaError("x"))


def test_five_503s_then_success_waits_through_the_whole_schedule_and_answers(slept, proxy):
    client = ScriptedGemini({"gemini-2.5-pro": [e503()] * 5 + [GEMINI_OK]})
    res = audio_stage.run_gemini_audio(client, proxy, None)
    assert res.status == "ok" and res.model_used == "gemini-2.5-pro" and res.fallback_used is False
    assert res.retries == 5 and len(client.calls) == 6 and all(r == 0 for _, r in client.calls)  # the client does not retry on its own
    assert len(slept) == 5
    for d, base in zip(slept, audio_stage.BACKOFF_SECONDS):
        assert base <= d <= base * 1.25


def test_exhausted_retries_fall_back_to_the_other_gemini_model(slept, proxy):
    client = ScriptedGemini({"gemini-2.5-pro": [e503()] * 6, "gemini-2.5-flash": [GEMINI_OK]})
    res = audio_stage.run_gemini_audio(client, proxy, None)
    assert res.status == "ok" and res.model_used == "gemini-2.5-flash" and res.fallback_used is True
    assert [m for m, _ in client.calls] == ["gemini-2.5-pro"] * 6 + ["gemini-2.5-flash"] and res.retries == 5 and len(slept) == 5


def test_a_zero_quota_429_is_not_retried_but_a_timeout_and_a_plain_429_are(slept, proxy):
    zero = MetaError("gemini: HTTP 429: Quota exceeded for metric ... limit: 0, model: gemini-2.5-pro", status=429)
    client = ScriptedGemini({"gemini-2.5-pro": [zero], "gemini-2.5-flash": [MetaError("t", timeout=True), MetaError("q", status=429), GEMINI_OK]})
    res = audio_stage.run_gemini_audio(client, proxy, None)
    assert res.status == "ok" and res.fallback_used and res.retries == 2 and len(slept) == 2
    assert [m for m, _ in client.calls] == ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash", "gemini-2.5-flash"]
    notfound = ScriptedGemini({"gemini-2.5-pro": [MetaError("HTTP 404", status=404)], "gemini-2.5-flash": [GEMINI_OK]})
    slept.clear()
    assert audio_stage.run_gemini_audio(notfound, proxy, None).status == "ok" and slept == []  # 404 is not transient


def test_when_gemini_never_answers_the_audio_stage_is_partial_and_says_so(slept, proxy):
    from app.studio.report import SparkAudioResult
    client = ScriptedGemini({"gemini-2.5-pro": [e503()] * 6, "gemini-2.5-flash": [e503()] * 6})
    res = audio_stage.run_gemini_audio(client, proxy, None)
    assert res.status == "error" and "503" in res.error and len(client.calls) == 12 and len(slept) == 10
    stage = audio_stage.build_audio_stage(SparkAudioResult(status="ok"), res)
    assert stage.status == "partial" and "Gemini audio facts missing" in stage.error and "503" in stage.error


# ---------------------------------------------------------------- 2. audio_listener_missing

def _report_without_gemini():
    r = make_report()
    r.stages.audio.gemini.status, r.stages.audio.gemini.error = "error", "gemini-2.5-flash: HTTP 503 after retries"
    r.stages.audio.status = "partial"
    return r


def test_missing_gemini_facts_become_an_explicit_evidence_item_and_cap_audio_confidence():
    pack = build_evidence_pack(_report_without_gemini())
    assert pack.audio_listener_missing is True
    item = next(i for i in pack.items if "UNAVAILABLE" in i.text)
    assert item.source == "audio/gemini" and "503" in item.text
    assert not any(i.source == "audio/gemini" and "music present" in i.text for i in pack.items)
    audio_ref = EvidenceRef(id=item.id)
    params = [Parameter(id=pid, score=70, confidence="high", evidence=[audio_ref]) for pid in niches.SCORED_IDS]
    out, flags = validate_parameters(params, pack)
    audio = next(p for p in out if p.id == "audio")
    assert audio.confidence == "low" and not audio.unsupported
    assert any(f.parameter == "audio" and f.code == "audio_listener_missing" for f in flags)
    assert next(p for p in out if p.id == "hook").confidence == "high"  # only Audio & Music is capped
    assert compute_overall(out, niches.get_niche("food")).score == 70.0  # the overall still computes


def test_with_gemini_present_there_is_no_cap_and_no_flag():
    pack = build_evidence_pack(make_report())
    assert pack.audio_listener_missing is False and not any("UNAVAILABLE" in i.text for i in pack.items)
    out, flags = validate_parameters([Parameter(id="audio", score=70, confidence="high", evidence=[EvidenceRef(id="E2")])], pack)
    assert next(p for p in out if p.id == "audio").confidence == "high" and not any(f.code == "audio_listener_missing" for f in flags)


def test_the_flag_reaches_the_stage_and_the_analysis_file(tmp_path):
    client = FakeClient(scoring=[scoring_json()])
    stage = run_analysis(_report_without_gemini(), client, tmp_path / "a.json", niche_override="food", repeat=1)
    assert stage.status == "ok" and stage.overall_score is not None
    assert any("audio_listener_missing" in f for f in stage.flags)
    assert next(p for p in stage.parameters if p.id == "audio").confidence == "low"
    assert json.loads((tmp_path / "a.json").read_text())["evidence_pack"]["audio_listener_missing"] is True
    assert "UNAVAILABLE" in client.calls[0]["prompt"]  # the model is told too


# ---------------------------------------------------------------- 3. rotate

def test_rotate_is_in_the_vocabulary_and_the_prompt_tells_the_model_how_to_use_it():
    assert "rotate" in FIXABLE_BY
    prompt = build_prompt(build_evidence_pack(make_report()), niches.get_niche("food"))
    assert "rotate" in prompt and "sideways or upside down" in prompt and "transpose=1" in prompt and "only for real crop" in prompt
    assert "direction unknown: check a frame before applying" in prompt and "instead of guessing" in prompt  # no guessed direction


def test_a_rotate_fix_is_auto_applicable_and_becomes_a_whole_video_intent_with_the_direction():
    pack = build_evidence_pack(make_report())
    rot = Fix(title="Rotate to upright", steps=["Rotate 90 degrees clockwise (ffmpeg transpose=1)"], fixable_by="Rotate",
              auto_applicable=False, target=Target(start_s=0, end_s=21.5))
    crop = Fix(title="Crop out the clutter", steps=["Zoom in on the subject"], fixable_by="zoom_crop", auto_applicable=True,
               target=Target(start_s=0, end_s=4))
    visual = Parameter(id="visual", score=40, confidence="high", evidence=[EvidenceRef(id="E2")], fixes=[rot, crop],
                       issues=[Issue(what="presented sideways", at_s=5, severity=3)])
    out, _ = validate_parameters([visual], pack)
    v = next(p for p in out if p.id == "visual")
    assert v.fixes[0].fixable_by == "rotate" and v.fixes[0].auto_applicable is True  # forced on
    intents = build_edit_intents(out, niches.get_niche("bollywood_indian_creator"))
    first = next(i for i in intents if i.intent == "rotate")
    assert first.target is None and first.source_parameter == "visual"
    assert first.parameters["direction"] == "cw90" and first.parameters["ffmpeg_filter"] == "transpose=1"
    assert "clockwise" in first.parameters["steps"][0]
    assert next(i for i in intents if i.intent == "zoom_crop").target.end_s == 4  # real crop problems stay zoom_crop


def test_the_rotation_direction_is_read_from_the_steps():
    assert rotate_direction("Rotate", ["rotate 90 degrees clockwise (transpose=1)"]) == "cw90"
    assert rotate_direction("Rotate", ["rotate 90 degrees counterclockwise"]) == "ccw90"
    assert rotate_direction("Rotate", ["turn it anti-clockwise"]) == "ccw90"
    assert rotate_direction("Flip the upside-down video", ["rotate 180 degrees"]) == "180"
    assert rotate_direction("Rotate to upright", ["fix the orientation"]) is None  # direction not stated: null, not guessed
    assert rotate_direction("Rotate", ["direction unknown: check a frame before applying"]) is None
    p = Parameter(id="visual", score=40, evidence=[EvidenceRef(id="E1")], fixes=[
        Fix(title="Rotate", steps=["fix the orientation"], fixable_by="rotate", auto_applicable=True)])
    unknown = build_edit_intents([p], niches.get_niche("food"))[0]
    assert unknown.parameters["direction"] is None and unknown.parameters["ffmpeg_filter"] is None


# ---------------------------------------------------------------- 4. Trend Alignment is an estimate

def test_trend_is_marked_evidence_limited_with_the_estimate_label_and_confidence_capped():
    pack = build_evidence_pack(make_report())
    out, flags = validate_parameters([Parameter(id="trend", score=60, confidence="high", evidence=[EvidenceRef(id="E1")])], pack)
    trend = next(p for p in out if p.id == "trend")
    assert trend.evidence_limited is True and trend.label == "estimate (no live trend data)" == niches.TREND_LABEL
    assert trend.confidence == "medium"
    assert all(not p.evidence_limited and p.label is None for p in out if p.id != "trend")
    assert niches.NICHES["comedy"].weights["trend"] == round(12.1 / 62.0, 4)  # the weights are untouched
    assert niches.NICHES["food"].weights["trend"] == round(1 / 6, 4)


def test_the_estimate_label_is_in_analysis_json_the_report_and_the_cli_table(tmp_path):
    from app.studio.analyze import _analysis_lines
    client = FakeClient(scoring=[scoring_json()])
    stage = run_analysis(make_report(), client, tmp_path / "a.json", niche_override="comedy", repeat=1)
    data = json.loads((tmp_path / "a.json").read_text())
    trend = next(p for p in data["parameters"] if p["id"] == "trend")
    assert trend["evidence_limited"] is True and trend["label"] == "estimate (no live trend data)"
    assert next(p for p in stage.parameters if p.id == "trend").label == "estimate (no live trend data)"
    report = make_report()
    report.stages.analysis = stage
    text = "\n".join(_analysis_lines(report))
    trend_line = next(line for line in text.splitlines() if line.strip().startswith("Trend Alignment"))
    assert "estimate (no live trend data)" in trend_line
    assert not any("estimate" in line for line in text.splitlines() if line.strip().startswith("Hook"))


# ---------------------------------------------------------------- 5. repeat aggregation

def _run(scores, confidences=None, evidence_counts=None, fixes=None):
    confidences = confidences or {}
    evidence_counts = evidence_counts or {}
    out = []
    for pid, name, scored in niches.PARAMETERS:
        out.append(Parameter(id=pid, name=name, scored=scored, score=scores.get(pid, 70), confidence=confidences.get(pid, "medium"),
                             evidence=[EvidenceRef(id="E1")] * evidence_counts.get(pid, 1), fixes=(fixes or {}).get(pid, [])))
    return out


def _fix(title):
    return Fix(title=title, fixable_by="trim", auto_applicable=True)


def test_each_parameter_is_the_rounded_mean_with_min_max_spread_and_unstable_ones_get_a_range():
    r1 = _run({"hook": 70, "shareability": 55, "audio": 80})
    r2 = _run({"hook": 74, "shareability": 74, "audio": 81})
    agg = {p.id: p for p in aggregate_runs([r1, r2])}
    assert agg["hook"].score == 72 and (agg["hook"].min, agg["hook"].max, agg["hook"].spread) == (70, 74, 4)
    assert agg["hook"].stable is True and agg["hook"].range is None and agg["hook"].runs_scored == 2
    assert agg["audio"].score == 81  # 80.5 rounds half up
    s = agg["shareability"]
    assert s.score == 65 and (s.min, s.max, s.spread) == (55, 74, 19) and s.stable is False and s.range == [55, 74]
    edge = {p.id: p for p in aggregate_runs([_run({"hook": 60}), _run({"hook": 70})])}["hook"]
    assert edge.spread == 10 and edge.stable is True  # exactly 10 is still stable; "more than 10" is unstable


def test_the_overall_uses_the_aggregated_scores():
    r1 = _run({"hook": 90, "retention": 70, "audio": 70, "visual": 70, "trend": 70, "shareability": 70})
    r2 = _run({"hook": 70, "retention": 70, "audio": 70, "visual": 70, "trend": 70, "shareability": 70})
    agg = aggregate_runs([r1, r2])
    flat = niches.get_niche("food")
    assert compute_overall(agg, flat).score == round((80 + 70 * 5) / 6, 1) == 71.7  # hook averages to 80
    assert compute_overall(r1, flat).score == round((90 + 70 * 5) / 6, 1) == 73.3  # the first run alone would differ


def test_fixes_come_from_the_run_with_the_highest_confidence_evidence_for_that_parameter():
    r1 = _run({"hook": 70, "audio": 70, "visual": 70}, confidences={"hook": "medium", "audio": "high", "visual": "medium"},
              fixes={"hook": [_fix("hook fix from run 1")], "audio": [_fix("audio fix from run 1")], "visual": [_fix("visual fix run 1")]})
    r2 = _run({"hook": 80, "audio": 60, "visual": 80}, confidences={"hook": "high", "audio": "medium", "visual": "medium"},
              evidence_counts={"visual": 3}, fixes={"hook": [_fix("hook fix from run 2")], "audio": [_fix("audio fix from run 2")],
                                                    "visual": [_fix("visual fix run 2")]})
    agg = {p.id: p for p in aggregate_runs([r1, r2])}
    assert agg["hook"].fixes[0].title == "hook fix from run 2" and agg["hook"].confidence == "high"  # run 2 is more confident
    assert agg["audio"].fixes[0].title == "audio fix from run 1" and agg["audio"].confidence == "high"
    assert agg["visual"].fixes[0].title == "visual fix run 2"  # equal confidence: more evidence wins
    tie = {p.id: p for p in aggregate_runs([_run({}, fixes={"hook": [_fix("first")]}), _run({}, fixes={"hook": [_fix("second")]})])}
    assert tie["hook"].fixes[0].title == "first"  # a full tie keeps the earliest run


def test_unsupported_runs_do_not_count_and_a_parameter_nobody_could_score_stays_unsupported():
    r1, r2 = _run({"hook": 80}), _run({"hook": 40})
    r2[0].unsupported = True  # run 2 had no evidence for hook
    h = next(p for p in aggregate_runs([r1, r2]) if p.id == "hook")
    assert h.score == 80 and h.runs_scored == 1 and h.stable is True
    r1[0].unsupported = True
    h2 = next(p for p in aggregate_runs([r1, r2]) if p.id == "hook")
    assert h2.score is None and h2.unsupported is True
    single = aggregate_runs([_run({"hook": 66})])
    assert single[0].score == 66 and single[0].min == single[0].max == 66 and single[0].stable


def test_repeat_defaults_to_two_and_an_unstable_parameter_is_flagged_and_given_a_range(tmp_path):
    run1 = scoring_json({"hook": 90, "retention": 70, "audio": 70, "visual": 70, "trend": 70, "shareability": 70})
    run2 = scoring_json({"hook": 66, "retention": 70, "audio": 70, "visual": 70, "trend": 70, "shareability": 70})
    client = FakeClient(niche=[json.dumps({"niche": "food", "confidence": 0.9})], scoring=[run1, run2])
    stage = run_analysis(make_report(), client, tmp_path / "a.json")  # no repeat argument: the default
    assert stage.runs == 2 and len(client.calls) == 3
    hook = next(p for p in stage.parameters if p.id == "hook")
    assert hook.score == 78 and hook.stable is False and hook.range == [66, 90] and hook.spread == 24
    assert stage.unstable_parameters == ["hook"] and any("unstable_score" in f for f in stage.flags)
    assert stage.overall_score == round((78 + 70 * 5) / 6, 1)
    data = json.loads((tmp_path / "a.json").read_text())
    h = next(p for p in data["parameters"] if p["id"] == "hook")
    assert (h["score"], h["min"], h["max"], h["stable"], h["range"]) == (78, 66, 90, False, [66, 90])
    from app.studio.analyze import main
    import argparse
    assert argparse.ArgumentParser  # the CLI default is checked through its help text below
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        main(["--help"])
    assert "default 2" in buf.getvalue().replace("\n", " ")
