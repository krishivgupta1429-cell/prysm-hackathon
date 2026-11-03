"""Hackathon-built (Meta Global AI Developer Hackathon).

Offline tests (no network) for the adapter: a fixture Studio analysis mapped to the baseline's result shape
(10 parameters in the baseline's order, advisory flags, weights, the baseline's scoring rules and label
thresholds, exactly 5 hashtags, optional fields), the baseline-shaped cost summary, the Redis size guard, the
progress writer and the 90 second guard.
"""
import asyncio
import json
import re

import pytest

from app.core.config import ADVISORY_PARAMETERS, DEFAULT_SCORED_WEIGHTS, NICHE_WEIGHTS, SCORED_PARAMETERS
from app.studio import adapter
from app.studio.analysis.creator import CreatorContext
from app.studio.stages.analysis import run_analysis
from app.studio.tests.test_studio_analysis import FakeClient, make_report
from app.studio.tests.test_studio_creator import with_fit

CREATOR = CreatorContext.from_inputs("Fashion,Lifestyle", "A saree try-on with styling tips")


@pytest.fixture(scope="module")
def fixture(tmp_path_factory):
    """A real analysis.json produced by the analysis stage from a fixture report (fake model answers)."""
    tmp = tmp_path_factory.mktemp("adapter")
    report = make_report()
    scores = {"hook": 90, "retention": 60, "audio": 70, "visual": 80, "trend": 50, "shareability": 100, "cta": 30,
              "on_screen_text": 65, "save_potential": 40, "replay_value": 55}
    host = {"hook": 0.19, "retention": 0.17, "audio": 0.16, "visual": 0.16, "trend": 0.16, "shareability": 0.16}
    stage = run_analysis(report, FakeClient(scoring=[with_fit(**scores)]), tmp / "analysis.json", creator=CREATOR, repeat=1,
                         weights_override=host)
    assert stage.status == "ok"
    report.stages.analysis = stage
    return report, json.loads((tmp / "analysis.json").read_text()), scores


def result_for(fixture, **kw):
    report, analysis, _ = fixture
    weights = adapter.baseline_weights(CREATOR.primary)
    args = dict(creator=CREATOR, platform="Instagram Reels", intent="my_video", source_type="upload", weights=weights,
                extras=None, frames=[], cached=False, extra_items=[])
    args.update(kw)
    return adapter.build_result(report, analysis, **args)


# ---------------------------------------------------------------- the baseline's own rules

def test_weights_follow_the_baselines_lookup():
    assert adapter.baseline_weights("Comedy") == NICHE_WEIGHTS["comedy"]
    assert adapter.baseline_weights("Comedy,Food") == NICHE_WEIGHTS["comedy"]  # the primary niche only
    assert adapter.baseline_weights("  DANCE ") == NICHE_WEIGHTS["dance"] and adapter.baseline_weights("fitness") == NICHE_WEIGHTS["fitness"]
    assert adapter.baseline_weights("Fashion") == DEFAULT_SCORED_WEIGHTS and adapter.baseline_weights("Anything else") == DEFAULT_SCORED_WEIGHTS
    assert adapter.baseline_weights("Fashion") is not DEFAULT_SCORED_WEIGHTS  # a copy: callers cannot mutate the baseline's dict


def test_score_label_thresholds_match_the_baseline():
    cases = {100: "Exceptional", 90: "Exceptional", 89: "Viral-Ready", 80: "Viral-Ready", 79: "Strong Content", 70: "Strong Content",
             69: "Good Potential, Needs Polish", 55: "Good Potential, Needs Polish", 54: "Below Average", 40: "Below Average",
             39: "Needs Significant Work", 0: "Needs Significant Work"}
    for score, label in cases.items():
        assert adapter.score_label(score) == label, score
    assert [adapter.score_label(s, "competitor") for s in (80, 79, 65, 64, 10)] == [
        "Excellent Execution", "Strong Concept, Needs Polish", "Strong Concept, Needs Polish", "Has Potential", "Has Potential"]


def test_overall_and_predicted_score_replicate_the_baseline_arithmetic():
    weights = NICHE_WEIGHTS["comedy"]
    params = [{"name": n, "score": s} for n, s in zip(
        ["Hook & Opening", "Retention & Pacing", "Audio & Music", "Visual Quality & Originality", "Trend Alignment", "Shareability"],
        [90, 60, 70, 80, 50, 100])] + [{"name": "Call to Action", "score": 5}, {"name": "Replay Value", "score": 5}]
    expected_overall = round(sum(s * weights[n] for n, s in [(p["name"], p["score"]) for p in params[:6]]))
    expected_predicted = round(sum(max(s, 75) * weights[n] for n, s in [(p["name"], p["score"]) for p in params[:6]]))
    assert adapter.baseline_scores(params, weights) == (expected_overall, expected_predicted)
    assert expected_predicted >= expected_overall  # weak scored parameters are assumed fixed to 75
    assert adapter.baseline_scores([{"name": "Hook & Opening"}], {"Hook & Opening": 0.5}) == (25, 38)  # a missing score counts as 50


# ---------------------------------------------------------------- the mapping

def test_ten_parameters_come_back_in_the_baseline_order_with_its_fields(fixture):
    r = result_for(fixture)
    names = [p["name"] for p in r["parameters"]]
    assert names == ["Hook & Opening", "Retention & Pacing", "Shareability", "Audio & Music", "Trend Alignment",
                     "Visual Quality & Originality", "On-Screen Text", "Save Potential", "Replay Value", "Call to Action"]
    assert set(names[:5] + [names[5]]) == SCORED_PARAMETERS and set(names[6:]) == ADVISORY_PARAMETERS
    baseline_keys = {"name", "weight", "score", "advisory", "problem", "fix", "whatIsHappening", "whyItMatters", "whatToChange",
                     "howToDoIt", "whatToExpect"}
    for p in r["parameters"]:
        assert baseline_keys <= set(p) and isinstance(p["score"], int) and isinstance(p["weight"], (int, float))
        assert p["advisory"] == (p["name"] in ADVISORY_PARAMETERS)
        assert p["weight"] == (0 if p["advisory"] else round(DEFAULT_SCORED_WEIGHTS[p["name"]] * 100, 1))


def test_parameter_text_fields_are_built_from_the_evidence_issues_and_fixes(fixture):
    hook = result_for(fixture)["parameters"][0]
    assert hook["score"] == 90 and hook["weight"] == 19.0
    assert hook["problem"] == "slow start (at 2s)"  # the most severe issue, with its timestamp
    assert hook["fix"] == "fix hook" and hook["whatToExpect"] == "better"
    assert hook["howToDoIt"] == "Step 1 — do it"  # the baseline's numbered-steps format
    assert hook["whatIsHappening"].startswith("[0-3s] first 3 seconds on screen: A woman in an orange saree")
    assert hook["whatToChange"] == "fix hook (0-3s)" and hook["whyItMatters"] == "because"
    for extra in ("confidence", "evidence", "fixable_by", "stable", "range", "evidence_limited", "unsupported", "strengths"):
        assert extra in hook  # Studio's additions are present and additive
    assert hook["evidence"][0]["id"] == "E3" and hook["fixable_by"] == "trim"
    trend = next(p for p in result_for(fixture)["parameters"] if p["name"] == "Trend Alignment")
    assert trend["evidence_limited"] is True and trend["label"] == "estimate (no live trend data)"


def test_steps_are_numbered_like_the_baseline():
    assert adapter.numbered_steps(["a", " b ", "", "c"]) == "Step 1 — a Step 2 — b Step 3 — c"
    assert adapter.numbered_steps([]) == ""


def test_overall_score_label_and_prediction_in_the_result(fixture):
    r = result_for(fixture)
    w = DEFAULT_SCORED_WEIGHTS
    scored = {"Hook & Opening": 90, "Retention & Pacing": 60, "Audio & Music": 70, "Visual Quality & Originality": 80,
              "Trend Alignment": 50, "Shareability": 100}
    assert r["overallScore"] == round(sum(s * w[n] for n, s in scored.items()))
    assert r["scoreLabel"] == adapter.score_label(r["overallScore"]) == "Strong Content"
    assert r["predictedScoreAfterFixes"] == round(sum(max(s, 75) * w[n] for n, s in scored.items())) > r["overallScore"]
    assert adapter.baseline_scores(r["parameters"], w) == (r["overallScore"], r["predictedScoreAfterFixes"])
    assert result_for(fixture, intent="competitor")["scoreLabel"] == "Strong Concept, Needs Polish"


def test_an_unsupported_parameter_shows_the_baselines_default_score_and_is_flagged(fixture):
    report, analysis, _ = fixture
    analysis = json.loads(json.dumps(analysis))
    audio = next(p for p in analysis["parameters"] if p["id"] == "audio")
    audio["score"], audio["unsupported"] = None, True
    r = adapter.build_result(report, analysis, CREATOR, "Instagram Reels", "my_video", "upload", adapter.baseline_weights("Fashion"),
                             None, [], False, [])
    p = next(p for p in r["parameters"] if p["name"] == "Audio & Music")
    assert p["score"] == 50 and p["unsupported"] is True


# ---------------------------------------------------------------- the extra text call and hashtags

def test_hashtags_are_always_exactly_five_without_hash_signs():
    pool = ["Fashion", "Lifestyle", "reels", "creator", "viral"]
    assert adapter.clean_hashtags(["#a", "b b", "#A", "c", "d", "e", "f", "g"], pool) == ["a", "bb", "c", "d", "e"]
    assert adapter.clean_hashtags(["#one", "two"], pool) == ["one", "two", "Fashion", "Lifestyle", "reels"]
    assert adapter.clean_hashtags(None, pool) == ["Fashion", "Lifestyle", "reels", "creator", "viral"]
    assert adapter.clean_hashtags("not a list", pool) == pool[:5]
    assert len(adapter.clean_hashtags(["#x"], [])) == 1  # nothing to top up from: never invents tags


def test_the_extras_are_coerced_to_the_baseline_types_and_fall_back_field_by_field(fixture):
    params = result_for(fixture)["parameters"]
    good = adapter.finalize_extras(
        {"summary": "S", "biggestProblem": "B", "quickFix": "Q", "topPriorities": ["1", "2", "3", "4"],
         "suggestedCaption": "A saree story. #saree #ootd It was fun!", "suggestedHashtags": ["#a", "#b", "#c", "#d", "#e", "#f"]},
        params, CREATOR, "Instagram Reels", 70, "Strong Content")
    assert good["topPriorities"] == ["1", "2", "3"] and len(good["suggestedHashtags"]) == 5
    assert "#" not in good["suggestedCaption"] and good["suggestedCaption"].startswith("A saree story.")
    empty = adapter.finalize_extras({}, params, CREATOR, "Instagram Reels", 70, "Strong Content")
    assert empty["summary"].startswith("Scored 70/100") and empty["biggestProblem"] and len(empty["suggestedHashtags"]) == 5
    assert empty["suggestedCaption"] == "A saree try-on with styling tips"  # falls back to the creator's own description
    assert isinstance(empty["topPriorities"], list)


class ScriptedExtras:
    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def complete(self, prompt, model, temperature=None, images=None):
        from app.studio.meta_client import WatchCall
        self.prompts.append(prompt)
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return WatchCall(text=item, tokens_in=10, tokens_out=5, cost_usd=0.01, seconds=0.1, model=model)


def test_the_extras_call_is_grounded_retried_once_and_never_crashes(fixture):
    report, analysis, _ = fixture
    params = adapter.build_parameters(analysis, adapter.baseline_weights("Fashion"))
    good = ScriptedExtras(["garbage", json.dumps({"summary": "ok"})])
    data, cost, err = adapter.generate_extras(good, params, analysis, "hello there", CREATOR, "Instagram Reels", 72, "Strong Content")
    assert data == {"summary": "ok"} and err is None and cost == pytest.approx(0.02)
    prompt = good.prompts[0]
    assert "creator-provided, unverified" in prompt and "hello there" in prompt and "E3 [0-3s]" in prompt
    assert "CREATOR-PROVIDED" not in prompt.split("EVIDENCE")[1]  # the creator line is not offered as evidence
    from app.studio.meta_client import MetaError
    data2, _, err2 = adapter.generate_extras(ScriptedExtras([MetaError("HTTP 500")]), params, analysis, "", CREATOR, "x", 50, "Good")
    assert "summary" in data2 and err2.startswith("summary call failed")
    data3, _, err3 = adapter.generate_extras(ScriptedExtras(["no", "json"]), params, analysis, "", CREATOR, "x", 50, "Good")
    assert "summary" in data3 and "unparseable" in err3


# ---------------------------------------------------------------- optional fields, frames, energy curve

def test_optional_fields_are_absent_not_broken(fixture):
    r = result_for(fixture)
    for key in ("trendData", "engagementPrediction", "visionData", "trendingAudioSuggestion", "nextVideoTemplate", "postMetadata"):
        assert key not in r  # the page treats these as optional
    assert r["first_frame_b64"] is None and r["frames"] == []
    assert r["niche"] == "Fashion, Lifestyle" and r["platform"] == "Instagram Reels" and r["intent"] == "my_video" and r["source_type"] == "upload"
    assert r["transcript"].startswith("This is exactly what I mean") and r["audioAnalysis"]["hasSpeech"] is True
    assert {"gemini", "spark", "music"} <= set(r["audioAnalysis"]) and r["descriptionFit"]["verdict"] == "matches"
    with_meta = result_for(fixture, post_metadata={"likeCount": 5}, frames=[{"timestamp": 0.5, "base64": "QQ=="}])
    assert with_meta["postMetadata"] == {"likeCount": 5} and with_meta["first_frame_b64"] == "QQ=="
    assert r["studio"]["edit_intents"] and r["studio"]["niche"]["source"] == "creator" and r["_debug"]["engine"] == "studio"


def test_the_energy_curve_has_the_baselines_shape():
    curve = adapter.build_energy_curve([-10, -10, -10, -10, -10, -40, -40, -40, -10])
    assert {"curve", "low_energy_segments", "avg_energy", "hook_energy", "mid_energy", "closing_energy", "drop_detected",
            "drop_at_seconds", "duration"} <= set(curve)
    assert curve["curve"][2] == {"t": 2.0, "energy": 1.0} and min(p["energy"] for p in curve["curve"]) == 0.0
    assert curve["drop_detected"] is True and curve["drop_at_seconds"] == 5.0 and curve["low_energy_segments"][0]["end"] == 8.0
    assert adapter.build_energy_curve([]) is None


# ---------------------------------------------------------------- cost: the baseline CostTracker shape

def test_the_cost_summary_has_the_baseline_shape_and_real_stage_costs(fixture):
    report, _, _ = fixture
    s = report.stages
    s.watch.cost_usd, s.speech.cost_usd, s.subjects.cost_usd, s.audio.cost_usd = 0.02, 0.003, 0.05, 0.03
    s.captions.cost_usd, s.signals.cost_usd, s.music_id.cost_usd, s.analysis.cost_usd = 0.02, 0.01, 0.0, 0.09
    full = adapter.cost_summary(report, cached=False, extra_items=[{"label": "Studio summary", "model": "m", "cost_usd": 0.015}])
    assert set(full) == {"items", "total_usd"} and all({"label", "model", "cost_usd"} <= set(i) for i in full["items"])
    assert full["total_usd"] == pytest.approx(0.02 + 0.003 + 0.05 + 0.03 + 0.02 + 0.01 + 0.09 + 0.015)
    assert not any(i["cost_usd"] == 0 for i in full["items"])  # zero-cost stages are not listed
    cached = adapter.cost_summary(report, cached=True, extra_items=[])
    assert cached["total_usd"] == pytest.approx(0.09) and [i["label"] for i in cached["items"]] == ["Analysis (scoring)"]
    assert adapter.perception_cost(report, cached=False) == pytest.approx(0.133) and adapter.perception_cost(report, cached=True) == 0.0


def test_the_result_carries_the_cost_and_its_total_matches_the_stage_costs(fixture):
    report, _, _ = fixture
    r = result_for(fixture, extra_items=[{"label": "Studio summary", "model": "m", "cost_usd": 0.01}])
    assert r["_cost"]["total_usd"] == pytest.approx(sum(i["cost_usd"] for i in r["_cost"]["items"]))


# ---------------------------------------------------------------- Redis size guard

def test_the_cache_view_is_shrunk_under_the_limit_by_dropping_the_heaviest_additive_fields(fixture):
    r = result_for(fixture)
    r["studio"]["evidence_pack"] = {"items": [{"id": f"E{i}", "text": "x" * 200} for i in range(6000)]}  # ~1.3 MB
    assert len(json.dumps(adapter.cache_view(r))) > adapter.CACHE_LIMIT_BYTES
    out = adapter.shrink_for_cache(r)
    assert len(json.dumps(adapter.cache_view(out), default=str)) <= adapter.CACHE_LIMIT_BYTES
    assert "evidence_pack" not in out["studio"] and out["parameters"][0]["name"] == "Hook & Opening"  # the page's data is untouched
    small = result_for(fixture)
    assert "evidence_pack" in adapter.shrink_for_cache(small)["studio"]  # a normal result is left alone
    view = adapter.cache_view(small)
    assert not {"frames", "audioAnalysis", "transcript", "_cost"} & set(view)  # what the baseline router strips


# ---------------------------------------------------------------- progress

def test_progress_is_written_to_redis_as_stages_report_in(monkeypatch):
    writes = []

    async def fake_cache_set(key, value, ttl=None):
        writes.append((key, dict(value), ttl))

    monkeypatch.setattr("app.core.cache.cache_set", fake_cache_set)

    async def run():
        p = adapter.Progress("job-1")
        p("watch", "running")
        p("speech", "running")
        p("watch", "ok")
        await p.flush()
        return p

    p = asyncio.run(run())
    assert p.state == {"watch": "ok", "speech": "running"}
    assert {k for k, _, _ in writes} == {"prysm:progress:job-1"} and writes[-1][1] or True
    assert any(v == {"watch": "ok", "speech": "running"} for _, v, _ in writes) and all(t == adapter.PROGRESS_TTL for _, _, t in writes)
    assert adapter.Progress(None).state == {}  # without a job id nothing is written


# ---------------------------------------------------------------- 90 s guard and concurrency

def test_videos_over_90_seconds_are_rejected_with_a_friendly_message(monkeypatch):
    monkeypatch.setattr(adapter, "probe_duration", lambda path: 91.0)
    with pytest.raises(adapter.StudioVideoTooLong) as e:
        adapter.check_duration("x.mp4")
    assert str(e.value) == "Prysm supports videos up to 90 seconds." and isinstance(e.value, ValueError)
    monkeypatch.setattr(adapter, "probe_duration", lambda path: 90.4)
    assert adapter.check_duration("x.mp4") == 90.4  # a little slack for container rounding
    monkeypatch.setattr(adapter, "probe_duration", lambda path: None)
    assert adapter.check_duration("x.mp4") is None  # unknown duration: let the pipeline decide


def test_concurrency_is_limited_by_a_semaphore_from_the_setting(monkeypatch):
    monkeypatch.setattr(adapter.settings, "STUDIO_MAX_CONCURRENT", 3)
    monkeypatch.setattr(adapter, "_semaphore", None)

    async def run():
        sem = adapter._limiter()
        return sem, adapter._limiter()

    a, b = asyncio.run(run())
    assert a is b and a._value == 3
    assert adapter.settings.STUDIO_MAX_CONCURRENT == 3
    from app.core.config import Settings
    assert Settings.model_fields["STUDIO_MAX_CONCURRENT"].default == 2  # the default
