"""Hackathon-built (Meta Global AI Developer Hackathon).

Offline tests (no network) for Stage 1D: the evidence pack and its size cap, the niche weights and band
mapping, the validator, edit-intent ranking, the JSON repair path with a mocked client, the niche override and
the low-confidence fallback to "other", plus an end-to-end run of the analysis stage with a fake client.
"""
import json
import re

import pytest

from app.studio import config
from app.studio.analysis import niches
from app.studio.analysis.detect import detect_niche, interpret, validate_override
from app.studio.analysis.evidence import build_evidence_pack, estimate_tokens
from app.studio.analysis.intents import build_edit_intents, top_fixes
from app.studio.analysis.overall import band_for, compute_overall
from app.studio.analysis.schema import EvidenceRef, Fix, Issue, Parameter, Target
from app.studio.analysis.score import score_once
from app.studio.analysis.validate import validate_parameters
from app.studio.meta_client import MetaError, WatchCall
from app.studio.report import (AudioStage, ConsensusSegment, ConsensusStage, DemucsResult, GeminiAudioResult, Hook,
                               ModelSignal, MusicIdStage, MusicInfo, MusicTrack, OnScreenText, Report, SceneCuts,
                               SignalsStage, SparkAudioResult, SpeechStage, SubjectsStage, SubjectTrack, Beat, Box,
                               VideoMeta, VoiceQuality, WatchStage, AudioFeatures, WhisperResult, LowConfidenceWord)
from app.studio.stages.analysis import keyframes, run_analysis, stability


# ---------------------------------------------------------------- fixture report

def make_report(n_text: int = 3, n_cuts: int = 5, consensus: str = "This is exactly what I mean by stop wearing obvious designer") -> Report:
    watch = WatchStage(
        status="ok", model="muse-spark-1.3",
        hook=Hook(what_happens="A woman in an orange saree speaks to camera", attention_rating=9, rating_reason="SECRET-OPINION"),
        timeline=[Beat(start_s=0, end_s=3.5, what_happens="Intro to camera"), Beat(start_s=3.5, end_s=9, what_happens="Shows the drape")],
        on_screen_text=[OnScreenText(text=f"caption number {i}", start_s=i, end_s=i + 1) for i in range(n_text)],
        setting="bedroom", subject_description="young woman in saree", format_type="talking head", cta_present=True,
        cta_text="Comment for the designer", visual_quality_notes="bright, stable")
    watch.weak_moments = []
    from app.studio.report import WeakMoment, NicheGuess
    watch.weak_moments = [WeakMoment(start_s=5, end_s=6, problem="OPINION-WEAK-MOMENT")]
    watch.niche_guess = NicheGuess(niche="OPINION-NICHE-GUESS", confidence=0.9)
    cons = ConsensusStage(status="ok", consensus_text=consensus, primary="whisper",
                          segments=[ConsensusSegment(start_s=0, end_s=2.5, text="This is exactly what I mean", confidence=1, timing="whisper"),
                                    ConsensusSegment(start_s=5, end_s=7, text="later words", confidence=1, timing="whisper")],
                          low_confidence_words=[LowConfidenceWord(word="obvious", index=9, start_s=2.3, confidence=0.4)])
    speech = SpeechStage(status="ok", primary="whisper", whisper=WhisperResult(status="ok", language="English", text="x"))
    audio = AudioStage(status="ok",
                       gemini=GeminiAudioResult(status="ok", music=MusicInfo(present=True, description="upbeat pop", mood="happy", energy_0_10=6),
                                                voice_quality=VoiceQuality(clarity_0_10=8, noise="none"), hook_audio="speech over silence",
                                                loudness_issues=["peaks clip at 12 s"]),
                       spark=SparkAudioResult(status="ok", music_present=True, voice_music_balance="voice dominates"))
    subjects = SubjectsStage(status="ok", person=SubjectTrack(prompt="person", first_seen_s=0.0, visible_in_first_3s=True,
                                                               screen_time_ratio=1.0, safe_area=Box(x1=0.1, y1=0.2, x2=0.9, y2=1.0)))
    signals = SignalsStage(status="ok", cuts=SceneCuts(status="ok", cut_times_s=[float(i) for i in range(n_cuts)], cuts_per_10s=4.2,
                                                       avg_shot_length_s=2.1),
                           audio_features=AudioFeatures(status="ok", tempo_bpm=90.0, integrated_loudness_lufs=-17.5, silence_ratio=0.01),
                           demucs=DemucsResult(status="ok", voice_to_music_db=2.8),
                           models={"encodec": ModelSignal(status="ok", dims=128, meta={"codebook_usage_ratio": 0.46})})
    music = MusicIdStage(status="ok", matched=True, source="rest_stem", track=MusicTrack(title="Majboor", artists=["A", "B"], play_offset_ms=63000))
    r = Report(video=VideoMeta(path="x.mp4", duration_s=36.6, width=720, height=1280, fps=30))
    r.stages.watch, r.stages.consensus, r.stages.speech, r.stages.audio = watch, cons, speech, audio
    r.stages.subjects, r.stages.signals, r.stages.music_id = subjects, signals, music
    return r


# ---------------------------------------------------------------- evidence pack

def test_evidence_pack_has_sequential_ids_timestamps_and_only_facts():
    pack = build_evidence_pack(make_report())
    assert [i.id for i in pack.items] == [f"E{n}" for n in range(1, len(pack.items) + 1)]
    text = pack.render()
    assert "E1 video: duration 36.6s, 720x1280, 30 fps" in text
    assert "[0-3s] watch: first 3 seconds on screen: A woman in an orange saree speaks to camera" in text
    assert '[1-2s] watch: on-screen text: "caption number 1"' in text
    assert "[0-3s] speech: spoken or sung in the first 3 seconds: This is exactly what I mean" in text  # only the cue before 3 s
    assert "later words" not in next(i.text for i in pack.items if "first 3 seconds" in i.text and i.source == "speech")
    assert "obvious@2.3s" in text and "Majboor by A, B; matched on rest_stem" in text
    assert "first seen 0s; visible in first 3 s: True" in text and "safe area" in text
    assert "cuts per 10 s: 4.2" in text and "tempo 90 bpm" in text and "voice-to-music loudness ratio 2.8 dB" in text
    assert "EnCodec codebook usage 0.46" in text and "voice clarity 8/10" in text
    for opinion in ("SECRET-OPINION", "OPINION-WEAK-MOMENT", "OPINION-NICHE-GUESS"):  # the watch stage's opinions stay out
        assert opinion not in text


def test_evidence_pack_stays_under_the_token_cap_and_says_what_was_shortened():
    huge = make_report(n_text=600, n_cuts=900, consensus="long sentence about sarees " * 3000)
    pack = build_evidence_pack(huge)
    assert pack.tokens <= config.EVIDENCE_MAX_TOKENS == 6000
    assert pack.shortened and any("cut" in s or "dropped" in s for s in pack.shortened)
    assert len(pack.ids()) == len(pack.items)  # ids stay unique after shrinking
    small = build_evidence_pack(make_report())
    assert small.tokens < 2000 and small.shortened == []


def test_the_token_estimate_counts_non_ascii_scripts_heavier():
    assert estimate_tokens("abcd" * 100) == 100
    assert estimate_tokens("नमस्ते" * 100) > estimate_tokens("abcdef" * 100)


# ---------------------------------------------------------------- niches, weights, bands

def test_baseline_niches_carry_the_baseline_numbers_and_label_others_are_flat():
    n = niches.NICHES
    assert n["comedy"].weights["hook"] == round(10.9 / 62.0, 4) and n["comedy"].weights["trend"] == round(12.1 / 62.0, 4)
    assert n["fitness"].weights["shareability"] == round(12.6 / 65.3, 4)
    assert n["dance"].weights == n["bollywood_indian_creator"].weights
    for nid in ("comedy", "dance", "fitness", "bollywood_indian_creator"):
        assert n[nid].weights_label == niches.BASELINE_LABEL and sum(n[nid].weights.values()) == pytest.approx(1.0, abs=0.001)
    for nid in set(n) - {"comedy", "dance", "fitness", "bollywood_indian_creator"}:
        assert n[nid].weights == {pid: round(1 / 6, 4) for pid in niches.SCORED_IDS} and n[nid].weights_label == niches.FLAT_LABEL
    assert "not part of this repo" in niches.BASELINE_LABEL
    assert niches.NICHE_IDS == ["comedy", "dance", "fitness", "bollywood_indian_creator", "talking_head_education",
                                "fashion_beauty", "food", "travel", "music_performance", "other"]


def test_every_niche_has_short_notes_for_all_ten_parameters_and_trend_says_no_live_data():
    assert len(niches.PARAMETERS) == 10 and len(niches.SCORED_IDS) == 6 and len(niches.ADVISORY_IDS) == 4
    for nid, niche in niches.NICHES.items():
        assert set(niche.judging_notes) == {p[0] for p in niches.PARAMETERS}, nid
        for pid, note in niche.judging_notes.items():
            assert len(re.findall(r"[.!?](?:\s|$)", note)) <= 2, (nid, pid, note)  # max 2 sentences
        assert "no live trend data in this version" in niche.judging_notes["trend"].lower(), nid


def test_band_mapping_boundaries():
    cases = {0: "Needs Work", 49: "Needs Work", 49.9: "Needs Work", 50: "Developing", 69.9: "Developing", 70: "Strong",
             84.9: "Strong", 85: "Viral-Ready", 100: "Viral-Ready"}
    for score, band in cases.items():
        assert band_for(score) == band, score


def _params(scores: dict[str, int], **over):
    return [Parameter(id=pid, name=niches.PARAM_NAMES[pid], scored=pid in niches.SCORED_IDS, score=s,
                      confidence="medium", evidence=[EvidenceRef(id="E1")], **over) for pid, s in scores.items()]


def test_overall_is_the_niche_weighted_mean_rounded_to_one_decimal():
    comedy = niches.get_niche("comedy")
    scores = {"hook": 90, "retention": 60, "audio": 70, "visual": 80, "trend": 50, "shareability": 100,
              "cta": 10, "on_screen_text": 10, "save_potential": 10, "replay_value": 10}  # advisory scores must not count
    expected = sum(scores[p] * comedy.weights[p] for p in niches.SCORED_IDS) / sum(comedy.weights.values())
    o = compute_overall(_params(scores), comedy)
    assert o.score == round(expected, 1) and o.band == band_for(round(expected, 1)) and o.excluded == [] and o.coverage == 1.0
    flat = compute_overall(_params(scores), niches.get_niche("food"))
    assert flat.score == round(sum(scores[p] for p in niches.SCORED_IDS) / 6, 1)  # 75.0 with flat weights
    assert flat.score == 75.0 and flat.band == "Strong"
    assert compute_overall(_params({p: 85 for p in niches.SCORED_IDS}), comedy).score == 85.0


def test_unsupported_or_unscored_parameters_are_left_out_and_the_weights_renormalised():
    params = _params({p: 80 for p in niches.SCORED_IDS})
    params[4].unsupported = True  # trend
    params[1].score = None  # retention
    o = compute_overall(params, niches.get_niche("food"))
    assert set(o.excluded) == {"trend", "retention"} and o.score == 80.0 and o.coverage == pytest.approx(4 / 6, abs=0.001)
    assert sum(o.weights.values()) == pytest.approx(1.0, abs=0.001)
    assert compute_overall([], niches.get_niche("food")).score is None


# ---------------------------------------------------------------- validator

def _pack():
    return build_evidence_pack(make_report())


def test_validator_drops_unknown_evidence_ids_and_lowers_confidence():
    pack = _pack()
    p = Parameter(id="hook", score=80, confidence="high", evidence=[EvidenceRef(id="E2"), EvidenceRef(id="E999"), EvidenceRef(id="[e3]")])
    out, flags = validate_parameters([p], pack)
    hook = out[0]
    assert [e.id for e in hook.evidence] == ["E2", "E3"] and hook.confidence == "low" and not hook.unsupported
    assert any(f.parameter == "hook" and f.code == "unknown_evidence_id" and "E999" in f.detail for f in flags)


def test_a_scored_parameter_with_no_valid_evidence_is_unsupported_but_an_advisory_one_is_not():
    pack = _pack()
    params = [Parameter(id="audio", score=70, confidence="high", evidence=[EvidenceRef(id="E500")]),
              Parameter(id="cta", score=40, evidence=[]), Parameter(id="visual", score=75)]
    out, flags = validate_parameters(params, pack)
    by = {p.id: p for p in out}
    assert by["audio"].unsupported and by["audio"].confidence == "low" and by["visual"].unsupported
    assert not by["cta"].unsupported  # advisory parameters are shown, not scored
    unsupported = {f.parameter for f in flags if f.code == "unsupported"}
    assert {"audio", "visual"} <= unsupported
    assert unsupported == {"audio", "visual", "hook", "retention", "trend", "shareability"}  # missing scored ones too
    assert "cta" not in unsupported
    assert compute_overall(out, niches.get_niche("food")).excluded  # unsupported ones leave the overall


def test_validator_clamps_scores_fixes_fixable_by_caps_trend_and_orders_the_parameters():
    pack = _pack()
    fixes = [Fix(title="a", fixable_by="magic", auto_applicable=True), Fix(title="b", fixable_by="", auto_applicable=True),
             Fix(title="c", fixable_by="reshoot", auto_applicable=True), Fix(title="d", fixable_by="TRIM", auto_applicable=True,
                                                                              target=Target(start_s=9, end_s=2))]
    p = Parameter(id="Trend Alignment", name="x", score=130, confidence="high", evidence=[EvidenceRef(id="E1")], fixes=fixes)
    out, flags = validate_parameters([Parameter(id="made_up", score=5), p], pack)
    assert [x.id for x in out] == [pid for pid, _, _ in niches.PARAMETERS]  # registry order, all 10 present
    trend = next(x for x in out if x.id == "trend")
    assert trend.score == 100 and trend.confidence == "medium" and trend.name == "Trend Alignment"
    assert [(f.fixable_by, f.auto_applicable) for f in trend.fixes] == [("none", False), ("none", False), ("reshoot", False), ("trim", True)]
    assert (trend.fixes[3].target.start_s, trend.fixes[3].target.end_s) == (2.0, 9.0)
    codes = {f.code for f in flags}
    assert {"unknown_parameter", "missing_parameter", "score_clamped", "unknown_fixable_by", "trend_confidence_capped"} <= codes


def test_validator_flags_quotes_that_are_not_in_the_cited_evidence():
    pack = _pack()
    ok = Parameter(id="hook", score=70, evidence=[EvidenceRef(id="E2", quote="speaks to camera")])
    bad = Parameter(id="audio", score=70, evidence=[EvidenceRef(id="E2", quote="a completely invented quote")])
    out, flags = validate_parameters([ok, bad], pack)
    assert [f.parameter for f in flags if f.code == "quote_not_in_evidence"] == ["audio"]


# ---------------------------------------------------------------- edit intents

def _p(pid, fixes, issues=()):
    return Parameter(id=pid, name=niches.PARAM_NAMES[pid], scored=pid in niches.SCORED_IDS, score=60, confidence="medium",
                     evidence=[EvidenceRef(id="E1"), EvidenceRef(id="E2")], issues=list(issues), fixes=fixes)


def _fix(kind, title, auto=True, target=None):
    return Fix(title=title, steps=["s1"], expected_effect=f"effect of {title}", fixable_by=kind, auto_applicable=auto, target=target)


def test_edit_intents_are_ranked_by_severity_times_niche_weight_and_exclude_non_automatable_fixes():
    comedy = niches.get_niche("comedy")  # trend .1952 > shareability .1919 > hook .1758 > visual .15 > retention .1468 > audio .1403
    params = [
        _p("hook", [_fix("trim", "trim the intro", target=Target(start_s=0, end_s=2))], [Issue(what="slow", at_s=1, severity=3)]),
        _p("trend", [_fix("music_swap", "swap the track")], [Issue(what="old sound", at_s=None, severity=2)]),
        _p("audio", [_fix("audio_balance", "duck the music", target=Target(start_s=0, end_s=36))], [Issue(what="voice buried", severity=3)]),
        _p("visual", [_fix("reshoot", "better light"), _fix("none", "n/a"), _fix("zoom_crop", "crop in", auto=False)], [Issue(what="dark", severity=3)]),
        _p("cta", [_fix("text_overlay", "add a CTA card")], [Issue(what="no cta", at_s=33, severity=3)]),
    ]
    intents = build_edit_intents(params, comedy)
    assert [i.source_parameter for i in intents] == ["hook", "audio", "trend", "cta"]  # 3*.1758 > 3*.1403 > 2*.1952 > 3*.0702
    assert [i.rank_score for i in intents] == sorted((i.rank_score for i in intents), reverse=True)
    assert intents[0].intent == "trim" and (intents[0].target.start_s, intents[0].target.end_s) == (0, 2)
    assert intents[0].evidence == ["E1", "E2"] and intents[0].parameters["title"] == "trim the intro" and intents[0].rationale == "effect of trim the intro"
    assert intents[3].target.start_s == 33 and intents[3].target.end_s == 36  # no target: borrows a window at the issue
    assert all(i.intent not in ("reshoot", "none") for i in intents)  # and the non-auto zoom_crop is out too


def test_edit_intents_are_capped_at_eight_and_top_fixes_include_non_automatable_ones():
    params = [_p(pid, [_fix("trim", f"{pid} fix {n}", target=Target(start_s=n, end_s=n + 1)) for n in range(2)], [Issue(what="x", severity=2)])
              for pid in niches.SCORED_IDS]
    assert len(build_edit_intents(params, niches.get_niche("food"))) == 8  # 12 candidates
    reshoot_only = [_p("hook", [_fix("reshoot", "reshoot the opening", auto=False)], [Issue(what="x", severity=3)])]
    assert build_edit_intents(reshoot_only, niches.get_niche("food")) == []
    assert top_fixes(reshoot_only, niches.get_niche("food"), 3)[0][2].title == "reshoot the opening"


# ---------------------------------------------------------------- the model calls (mocked client)

def scoring_json(scores: dict[str, int] | None = None, evidence_id: str = "E2") -> str:
    scores = scores or {pid: 70 for pid, _, _ in niches.PARAMETERS}
    params = []
    for pid, name, scored in niches.PARAMETERS:
        params.append({"id": pid, "name": name, "scored": scored, "score": scores.get(pid, 70), "confidence": "medium",
                       "evidence": [{"id": evidence_id, "quote": "speaks to camera"}], "why_it_matters": "because",
                       "strengths": ["clear"], "issues": [{"what": "slow start", "at_s": "0:02", "severity": 2}],
                       "fixes": [{"title": f"fix {pid}", "steps": ["do it"], "expected_effect": "better", "fixable_by": "trim",
                                  "target": {"start_s": 0, "end_s": 3}, "auto_applicable": True}]})
    return json.dumps({"parameters": params})


class FakeClient:
    """Answers the niche prompt and the scoring prompt from scripted queues; records every call."""
    total_cost = 0.0  # the real client's running spend

    def __init__(self, niche=None, scoring=None):
        self.niche = list(niche or [])
        self.scoring = list(scoring or [])
        self.calls = []

    def complete(self, prompt, model, temperature=None, images=None):
        self.calls.append({"prompt": prompt, "temperature": temperature, "images": images, "model": model})
        queue = self.niche if "Classify this short vertical" in prompt else self.scoring
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return WatchCall(text=item, tokens_in=1000, tokens_out=500, cost_usd=0.01, seconds=0.1, model=model)


def test_scoring_parses_a_good_answer_with_low_temperature_and_the_niche_notes():
    client = FakeClient(scoring=[scoring_json()])
    out = score_once(client, _pack(), niches.get_niche("comedy"))
    assert out.attempts == 1 and out.error is None and len(out.parameters) == 10
    assert out.parameters[0].issues[0].at_s == 2.0 and out.cost_usd == pytest.approx(0.01)
    call = client.calls[0]
    assert call["temperature"] == config.ANALYSIS_TEMPERATURE == 0.2 and call["model"] == "muse-spark-1.3"
    assert "hand-written heuristics, not data-derived" in call["prompt"] and "no live trend data in this version" in call["prompt"]
    assert "E2 [0-3s] watch" in call["prompt"] and "Bands: 0-49 Needs Work" in call["prompt"]
    assert "Include exactly these 10 parameter ids" in call["prompt"]


def test_a_json_parse_failure_is_repaired_once_and_a_second_failure_is_an_error():
    client = FakeClient(scoring=["Sure! Here is my analysis: not json at all", scoring_json()])
    out = score_once(client, _pack(), niches.get_niche("comedy"))
    assert out.attempts == 2 and out.error is None and len(out.parameters) == 10
    assert "could not be parsed" in client.calls[1]["prompt"] and "not json at all" in client.calls[1]["prompt"]
    assert out.cost_usd == pytest.approx(0.02) and out.tokens_in == 2000

    bad = FakeClient(scoring=["nope", '{"parameters": []}'])
    failed = score_once(bad, _pack(), niches.get_niche("comedy"))
    assert failed.parameters is None and failed.attempts == 2 and "unparseable" in failed.error and failed.cost_usd == pytest.approx(0.02)
    assert score_once(FakeClient(scoring=[MetaError("HTTP 500")]), _pack(), niches.get_niche("comedy")).error.startswith("scoring call failed")


def test_keyframes_are_attached_to_the_scoring_call_when_given():
    client = FakeClient(scoring=[scoring_json()])
    score_once(client, _pack(), niches.get_niche("comedy"), images=[b"jpg1", b"jpg2"])
    assert client.calls[0]["images"] == [b"jpg1", b"jpg2"]


def test_keyframe_selection_is_0_1_2_3_middle_last(tmp_path):
    for i in range(1, 15):
        (tmp_path / f"frame_{i:03d}.jpg").write_bytes(bytes([i]))
    assert [b[0] for b in keyframes(tmp_path)] == [1, 2, 3, 4, 8, 14]  # indexes 0,1,2,3,7,13
    for p in list(tmp_path.glob("*.jpg"))[3:]:
        p.unlink()  # 3 frames left: no duplicates, no overflow
    assert len(keyframes(tmp_path)) == 3 and keyframes(None) == []


# ---------------------------------------------------------------- niche detection

def test_niche_override_skips_the_model_and_is_validated():
    class Boom:
        def complete(self, *a, **k):
            raise AssertionError("the model must not be called with an override")
    out = detect_niche(_pack(), Boom(), niche_override="food")
    assert (out.detection.niche, out.detection.confidence, out.detection.source, out.cost_usd) == ("food", 1.0, "override", 0.0)
    with pytest.raises(ValueError, match="unknown niche"):
        validate_override("cooking")
    from app.studio.analyze import main
    with pytest.raises(SystemExit):
        main(["whatever.mp4", "--niche", "cooking"])  # the CLI rejects it before doing anything


def test_a_confident_detection_is_kept_with_its_runner_up_and_valid_reasons():
    answer = json.dumps({"niche": "bollywood_indian_creator", "confidence": 0.82, "runner_up": "fashion_beauty", "reasons": ["E2", "e3", "E777"]})
    client = FakeClient(niche=[answer])
    out = detect_niche(_pack(), client)
    d = out.detection
    assert (d.niche, d.confidence, d.runner_up, d.source) == ("bollywood_indian_creator", 0.82, "fashion_beauty", "model")
    assert d.reasons == ["E2", "E3"] and out.cost_usd == pytest.approx(0.01)
    assert client.calls[0]["temperature"] == 0.2 and "talking_head_education" in client.calls[0]["prompt"]


def test_low_confidence_falls_back_to_other_and_unknown_niches_become_other():
    low = detect_niche(_pack(), FakeClient(niche=[json.dumps({"niche": "dance", "confidence": 0.3, "runner_up": "comedy", "reasons": ["E2"]})]))
    assert (low.detection.niche, low.detection.source, low.detection.detected, low.detection.runner_up) == ("other", "fallback", "dance", "dance")
    edge = interpret({"niche": "dance", "confidence": 0.5}, {"E1"})
    assert edge.niche == "dance"  # exactly 0.5 is enough
    assert interpret({"niche": "cooking", "confidence": 0.9}, set()).niche == "other"
    assert interpret({"niche": "food", "confidence": "high?"}, set()).niche == "other"  # unusable confidence -> 0 -> other


def test_detection_retries_once_then_falls_back_and_reports_failures():
    twice = detect_niche(_pack(), FakeClient(niche=["garbage", "more garbage"]))
    assert twice.detection.niche == "other" and twice.detection.source == "fallback" and "unparseable" in twice.error
    ok = detect_niche(_pack(), FakeClient(niche=["garbage", json.dumps({"niche": "food", "confidence": 0.9})]))
    assert ok.detection.niche == "food" and ok.error is None
    dead = detect_niche(_pack(), FakeClient(niche=[MetaError("HTTP 400")]))
    assert dead.detection.niche == "other" and "call failed" in dead.error


# ---------------------------------------------------------------- stability + the whole stage

def test_stability_reports_min_max_spread_and_flags_over_ten_points():
    st = stability([{"hook": 70, "audio": 60}, {"hook": 85, "audio": 66}], [72.0, 80.5])
    assert st.per_parameter["hook"] == {"min": 70, "max": 85, "spread": 15} and st.unstable == ["hook"]
    assert st.overall == {"min": 72.0, "max": 80.5, "spread": 8.5} and st.runs == 2
    assert stability([{"hook": 70}], [70.0]).unstable == []  # one run cannot be unstable


def test_the_analysis_stage_end_to_end_with_a_fake_client(tmp_path):
    niche = json.dumps({"niche": "bollywood_indian_creator", "confidence": 0.8, "runner_up": "dance", "reasons": ["E2"]})
    run1 = scoring_json({"hook": 90, "retention": 60, "audio": 70, "visual": 80, "trend": 50, "shareability": 100})
    run2 = scoring_json({"hook": 70, "retention": 62, "audio": 70, "visual": 80, "trend": 50, "shareability": 100})
    client = FakeClient(niche=[niche], scoring=[run1, run2])
    path = tmp_path / "analysis.json"
    stage = run_analysis(make_report(), client, path, repeat=2)
    assert stage.status == "ok" and stage.niche == "bollywood_indian_creator" and stage.niche_source == "model" and stage.runs == 2
    w = niches.get_niche("bollywood_indian_creator").weights
    mean_scores = {"hook": 80, "retention": 61, "audio": 70, "visual": 80, "trend": 50, "shareability": 100}  # mean of the two runs
    aggregated = sum(s * w[p] for p, s in mean_scores.items()) / sum(w.values())
    assert stage.overall_score == round(aggregated, 1)  # computed in code from the aggregated scores
    assert stage.unstable_parameters == ["hook"] and stage.overall_spread > 0 and any("unstable_score" in f for f in stage.flags)
    assert stage.cost_usd == pytest.approx(0.03) and stage.intent_count == 8 and len(stage.parameters) == 10
    data = json.loads(path.read_text())
    assert data["schema_version"] == "1.0" and data["niche"]["niche"] == "bollywood_indian_creator"
    assert data["niche_weights"]["label"] == niches.BASELINE_LABEL and "hand-written heuristics" in data["niche_weights"]["judging_notes"]
    assert len(data["parameters"]) == 10 and len(data["edit_intents"]) == 8 and data["stability"]["unstable"] == ["hook"]
    assert data["evidence_pack"]["items"][0]["id"] == "E1" and data["overall"]["score"] == stage.overall_score
    assert len(client.calls) == 3  # one niche call + two scoring runs


def test_the_analysis_stage_reports_an_error_when_every_scoring_run_fails_and_never_raises(tmp_path):
    client = FakeClient(niche=[json.dumps({"niche": "food", "confidence": 0.9})], scoring=["garbage", "garbage"])
    stage = run_analysis(make_report(), client, tmp_path / "a.json", repeat=1)
    assert stage.status == "error" and "unparseable" in stage.error and not (tmp_path / "a.json").exists()
    assert stage.cost_usd == pytest.approx(0.03)  # the niche call and both attempts were paid for


def test_a_niche_override_and_unknown_evidence_end_up_as_flags_in_the_stage(tmp_path):
    client = FakeClient(scoring=[scoring_json(evidence_id="E999")])
    stage = run_analysis(make_report(), client, tmp_path / "a.json", niche_override="food", repeat=1)
    assert stage.niche == "food" and stage.niche_source == "override" and len(client.calls) == 1  # no niche call
    assert stage.overall_score is None  # every scored parameter is unsupported: nothing to average
    assert any("unsupported" in f for f in stage.flags) and any("unknown_evidence_id" in f for f in stage.flags)


def test_an_intent_lists_each_evidence_id_once_even_when_a_parameter_cites_it_with_several_quotes():
    p = _p("hook", [_fix("trim", "t", target=Target(start_s=0, end_s=2))], [Issue(what="x", severity=2)])
    p.evidence = [EvidenceRef(id="E8", quote="a"), EvidenceRef(id="E8", quote="b"), EvidenceRef(id="E3")]
    assert build_edit_intents([p], niches.get_niche("comedy"))[0].evidence == ["E8", "E3"]
