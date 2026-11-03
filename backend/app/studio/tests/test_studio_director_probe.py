"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests (fake Meta client, no network) for the director probe's task definition and automatic checks: the op
vocabulary and strict schema, timestamps inside the video, overlapping ops, intent coverage, the rotate-direction rule,
the budget guard, Wonder's published prices and the report table.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.studio import config
from app.studio.meta_client import MetaClient, MetaError
from app.studio.spikes import director_probe as dp


def make_case(duration=20.0, evidence="E1 [0-3s] watch: a woman faces the camera\nE2 [0s] video: 720x1280"):
    intents = [{"id": f"I{n}", "intent": kind, "target": target, "title": f"t{n}", "steps": ["do it"], "source_parameter": "hook",
                "rationale": "because", "evidence": ["E1"]}
               for n, (kind, target) in enumerate([("text_overlay", {"start_s": 0, "end_s": 3}), ("rotate", None), ("zoom_crop", {"start_s": 0, "end_s": 20}),
                                                    ("trim", {"start_s": 0, "end_s": 6})], 1)]
    return dp.Case(reel="Video-0", duration_s=duration, width=720, height=1280, intents=intents, evidence_lines=evidence.split("\n"), evidence_text=evidence)


def op(kind, start=0.0, end=3.0, intent="I1", reason="because E1", **params):
    return {"op": kind, "start_s": start, "end_s": end, "params": params, "reason": reason, "intent_id": intent}


def plan_json(ops, declined=()):
    return json.dumps({"ops": ops, "declined": [{"intent_id": i, "reason": "cannot be done automatically"} for i in declined]})


GOOD = plan_json([op("text_overlay", 0, 3, "I1", text="hi"), op("rotate", None, None, "I2", direction="unknown"),
                  op("zoom_crop", 0, 3, "I3", zoom=1.1), op("trim", 5, 8, "I4", remove=True)])


# ---------------------------------------------------------------- vocabulary and schema

def test_the_op_vocabulary_is_exactly_the_ten_allowed_ops():
    assert dp.OPS == ("trim", "reorder", "caption", "text_overlay", "audio_balance", "music_swap", "speed_ramp", "zoom_crop", "hook_rewrite", "rotate")


def test_a_good_plan_passes_every_check():
    checks = dp.run_checks(GOOD, make_case())
    assert [k for k, v in checks.items() if not k.startswith("_") and not v["pass"]] == []
    assert len(checks["_plan"]["ops"]) == 4


def test_a_reply_wrapped_in_a_code_fence_is_still_valid_json():
    assert dp.run_checks("```json\n" + GOOD + "\n```", make_case())["valid_json"]["pass"]


def test_prose_or_broken_json_fails_the_json_check_and_every_later_one():
    for text in ("Sure! Here is the plan.", '{"ops": ['):
        checks = dp.run_checks(text, make_case())
        assert not checks["valid_json"]["pass"] and not checks["schema"]["pass"] and not checks["timestamps"]["pass"]


def test_an_op_outside_the_vocabulary_or_without_a_reason_fails_the_schema():
    bad_op = plan_json([op("blur", 0, 3, "I1")])
    assert not dp.run_checks(bad_op, make_case())["schema"]["pass"]
    no_reason = plan_json([op("trim", 0, 3, "I4", reason="  ")])
    assert not dp.run_checks(no_reason, make_case())["schema"]["pass"]
    assert not dp.run_checks('{"nope": 1}', make_case())["schema"]["pass"]


# ---------------------------------------------------------------- timestamps

def test_timestamps_must_be_inside_the_video_and_ordered():
    case = make_case(duration=20)
    too_late = dp.run_checks(plan_json([op("trim", 18, 25, "I4")], declined=["I1", "I2", "I3"]), case)
    assert not too_late["timestamps"]["pass"] and "outside 0-20s" in too_late["timestamps"]["detail"]
    backwards = dp.run_checks(plan_json([op("trim", 8, 3, "I4")], declined=["I1", "I2", "I3"]), case)
    assert not backwards["timestamps"]["pass"]
    half = dp.run_checks(plan_json([{"op": "trim", "start_s": 2, "end_s": None, "params": {}, "reason": "r", "intent_id": "I4"}], declined=["I1", "I2", "I3"]), case)
    assert not half["timestamps"]["pass"]


def test_only_rotate_and_music_swap_may_have_no_times():
    case = make_case()
    ok = dp.run_checks(plan_json([op("music_swap", None, None, "I1"), op("rotate", None, None, "I2", direction="unknown")], declined=["I3", "I4"]), case)
    assert ok["timestamps"]["pass"]
    bad = dp.run_checks(plan_json([op("trim", None, None, "I4")], declined=["I1", "I2", "I3"]), case)
    assert not bad["timestamps"]["pass"]


# ---------------------------------------------------------------- conflicts

def test_two_exclusive_ops_on_the_same_seconds_conflict_even_if_they_are_different_kinds():
    case = make_case()
    plan = plan_json([op("zoom_crop", 0, 20, "I3"), op("trim", 0, 6, "I4")], declined=["I1", "I2"])
    c = dp.run_checks(plan, case)["no_conflicts"]
    assert not c["pass"] and "zoom_crop" in c["detail"] and "trim" in c["detail"]


def test_touching_or_separate_ops_do_not_conflict():
    case = make_case()
    plan = plan_json([op("zoom_crop", 0, 3, "I3"), op("trim", 3, 6, "I4"), op("trim", 6.02, 8, "I4")], declined=["I1", "I2"])
    assert dp.run_checks(plan, case)["no_conflicts"]["pass"]


def test_layered_ops_conflict_only_with_the_same_kind():
    case = make_case()
    same = plan_json([op("text_overlay", 0, 3, "I1"), op("text_overlay", 2, 5, "I1")], declined=["I2", "I3", "I4"])
    assert not dp.run_checks(same, case)["no_conflicts"]["pass"]
    mixed = plan_json([op("text_overlay", 0, 3, "I1"), op("caption", 0, 3, "I1"), op("audio_balance", 0, 3, "I1")], declined=["I2", "I3", "I4"])
    assert dp.run_checks(mixed, case)["no_conflicts"]["pass"]


def test_two_whole_video_rotates_conflict():
    plan = plan_json([op("rotate", None, None, "I2", direction="unknown"), op("rotate", None, None, "I2", direction="unknown")], declined=["I1", "I3", "I4"])
    assert not dp.run_checks(plan, make_case())["no_conflicts"]["pass"]


# ---------------------------------------------------------------- coverage

def test_every_intent_must_be_addressed_or_declined_with_a_reason():
    case = make_case()
    partial = plan_json([op("text_overlay", 0, 3, "I1")])
    c = dp.run_checks(partial, case)["intents_covered"]
    assert not c["pass"] and "I2" in c["detail"] and "I4" in c["detail"]
    assert dp.run_checks(plan_json([op("text_overlay", 0, 3, "I1")], declined=["I2", "I3", "I4"]), case)["intents_covered"]["pass"]
    blank = json.dumps({"ops": [op("text_overlay", 0, 3, "I1")], "declined": [{"intent_id": i, "reason": ""} for i in ("I2", "I3", "I4")]})
    assert not dp.run_checks(blank, case)["intents_covered"]["pass"]  # a decline needs a reason


def test_an_unknown_intent_id_fails_the_coverage_check():
    plan = plan_json([op("text_overlay", 0, 3, "I1"), op("trim", 5, 6, "I99")], declined=["I2", "I3", "I4"])
    c = dp.run_checks(plan, make_case())["intents_covered"]
    assert not c["pass"] and "I99" in c["detail"]


# ---------------------------------------------------------------- the rotate rule

def test_a_rotate_direction_is_a_guess_unless_the_evidence_shows_it():
    case = make_case()  # nothing about rotation in the evidence
    guess = plan_json([op("rotate", None, None, "I2", direction="cw90")], declined=["I1", "I3", "I4"])
    c = dp.run_checks(guess, case)["rotate_rule"]
    assert not c["pass"] and "does not show the direction" in c["detail"]
    assert dp.run_checks(plan_json([op("rotate", None, None, "I2", direction="unknown")], declined=["I1", "I3", "I4"]), case)["rotate_rule"]["pass"]


def test_a_direction_the_evidence_states_is_allowed_and_odd_values_are_rejected():
    shown = make_case(evidence="E1 [0s] watch: the video is rotated, the head points to the left; turn it clockwise to fix")
    assert dp.run_checks(plan_json([op("rotate", None, None, "I2", direction="cw90")], declined=["I1", "I3", "I4"]), shown)["rotate_rule"]["pass"]
    weird = plan_json([op("rotate", None, None, "I2", direction="sideways")], declined=["I1", "I3", "I4"])
    assert not dp.run_checks(weird, shown)["rotate_rule"]["pass"]


def test_with_frames_a_direction_passes_only_if_it_matches_the_known_ground_truth(monkeypatch):
    monkeypatch.setitem(dp.ROTATE_TRUTH, "Video-0", "ccw90")
    case = make_case()
    right = plan_json([op("rotate", None, None, "I2", direction="ccw90")], declined=["I1", "I3", "I4"])
    wrong = plan_json([op("rotate", None, None, "I2", direction="cw90")], declined=["I1", "I3", "I4"])
    assert dp.run_checks(right, case, "images")["rotate_rule"]["pass"] and dp.run_checks(right, case, "video")["rotate_rule"]["pass"]
    c = dp.run_checks(wrong, case, "images")["rotate_rule"]
    assert not c["pass"] and "needs ccw90" in c["detail"]
    assert not dp.run_checks(right, case, "text")["rotate_rule"]["pass"]  # a text-only reply cannot see the frames: a correct guess is still a guess


def test_rescore_recomputes_checks_from_the_saved_raw_output(tmp_path):
    case = make_case()
    (tmp_path / "raw.txt").write_text(GOOD)
    result = dp.RunResult("Video-0", config.ANALYSIS_MODEL, "text", "ran", raw_file="raw.txt", checks={"schema": {"pass": False, "detail": "old"}})
    out = dp.rescore([result, dp.RunResult("Video-0", "m", "video", "not_accepted")], {"Video-0": case}, tmp_path)
    assert out[0].passed() == 6 and out[0].ops == 4 and out[1].checks == {}


def test_the_prompt_states_the_vocabulary_the_rules_and_the_rotate_rule():
    prompt = dp.build_prompt(make_case(), "images")
    for op_name in dp.OPS:
        assert op_name in prompt
    assert "ONLY if the evidence lines or the attached frames/video clearly show" in prompt and '"unknown"' in prompt
    assert "I1 text_overlay (0-3s)" in prompt and "I2 rotate (whole video)" in prompt and "attached keyframes" in prompt
    assert "attached" not in dp.build_prompt(make_case(), "text").replace("attached frames/video", "")


# ---------------------------------------------------------------- pricing and running

def test_wonders_published_prices_are_used_for_cost_and_spark_stays_the_default():
    assert config.price_per_m("muse-wonder-1.0") == (4.0, 20.0) and config.price_per_m("muse-spark-1.3") == (1.25, 4.25)
    assert config.price_per_m("anything-else") == (1.25, 4.25)


def test_the_meta_client_charges_wonder_at_wonder_prices(monkeypatch):
    import httpx
    body = {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, json=body, request=httpx.Request("POST", "https://x")))
    client = MetaClient(meta_key="k")
    call = client.complete("hi", "muse-wonder-1.0")
    assert call.cost_usd == pytest.approx((1000 * 4 + 500 * 20) / 1e6)
    spark = client.complete("hi", "muse-spark-1.3")
    assert spark.cost_usd == pytest.approx((1000 * 1.25 + 500 * 4.25) / 1e6)
    assert [e.service for e in client.ledger] == ["wonder", "spark"]


class FakeClient:
    def __init__(self, text=GOOD, cost=0.5, fail_video=False, fail_status=None):
        self.text, self.cost, self.fail_video, self.fail_status, self.calls = text, cost, fail_video, fail_status, []

    def _call(self, kind, model):
        self.calls.append((kind, model))
        if self.fail_status:
            raise MetaError("HTTP 400 video not supported", status=self.fail_status)
        return SimpleNamespace(text=self.text, seconds=1.5, tokens_in=2000, tokens_out=800, cost_usd=self.cost)

    def complete(self, prompt, model, temperature=None, images=None):
        return self._call("images" if images else "text", model)

    def watch_video(self, path, prompt, model):
        if self.fail_video:
            raise MetaError("video input is not supported by this model", status=400)
        return self._call("video", model)


def case_with_media(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir(exist_ok=True)
    for i in range(4):
        (frames / f"frame_{i:03d}.jpg").write_bytes(b"jpg")
    proxy = tmp_path / "proxy.720p.mp4"
    proxy.write_bytes(b"mp4")
    case = make_case()
    case.frames_dir, case.proxy = frames, proxy
    return case


def test_run_one_records_checks_cost_latency_tokens_and_the_raw_output(tmp_path):
    result = dp.run_one(FakeClient(), case_with_media(tmp_path), config.WONDER_MODEL, "images", tmp_path / "out")
    assert result.status == "ran" and result.passed() == 6 and result.ops == 4 and result.cost_usd == 0.5
    assert (result.seconds, result.tokens_in, result.tokens_out) == (1.5, 2000, 800)
    assert (tmp_path / "out" / result.raw_file).read_text() == GOOD


def test_a_model_that_refuses_video_is_recorded_as_not_accepted_not_as_a_failure(tmp_path):
    result = dp.run_one(FakeClient(fail_video=True), case_with_media(tmp_path), config.WONDER_MODEL, "video", tmp_path / "out")
    assert result.status == "not_accepted" and "not supported" in result.error and result.cost_usd == 0.0


def test_a_server_error_is_an_error_result(tmp_path):
    result = dp.run_one(FakeClient(fail_status=503), case_with_media(tmp_path), config.WONDER_MODEL, "text", tmp_path / "out")
    assert result.status == "error"


def test_the_budget_guard_stops_new_runs_and_marks_the_rest_skipped(tmp_path):
    cases = [case_with_media(tmp_path), case_with_media(tmp_path)]
    results = dp.run_probe(cases, tmp_path / "out", budget_usd=1.0, client=FakeClient(cost=0.6))  # two runs reach $1.2, then stop
    assert [r.status for r in results].count("ran") == 2
    assert all(r.status == "skipped_budget" for r in results[2:]) and len(results) == len(dp.PLAN) * 2
    assert sum(r.cost_usd for r in results) == pytest.approx(1.2)


def test_the_cheap_runs_go_first_and_the_video_run_is_last():
    assert dp.PLAN[0] == (config.ANALYSIS_MODEL, "text") and dp.PLAN[-1] == (config.WONDER_MODEL, "video")


def test_the_report_table_and_summary(tmp_path):
    case = case_with_media(tmp_path)
    ran = dp.run_one(FakeClient(), case, config.ANALYSIS_MODEL, "text", tmp_path / "out")
    bad = dp.run_one(FakeClient(text="no json"), case, config.WONDER_MODEL, "text", tmp_path / "out")
    refused = dp.run_one(FakeClient(fail_video=True), case, config.WONDER_MODEL, "video", tmp_path / "out")
    table = dp.render_table([ran, bad, refused])
    assert table.count("\n") == 4 and "| muse-spark-1.3 | text | Video-0 | ran |" in table and "FAIL" in table and "not_accepted" in table
    summary = dp.summarise([ran, bad, refused])
    assert summary["muse-spark-1.3 / text"]["all_checks"] == 1 and summary["muse-wonder-1.0 / text"]["all_checks"] == 0
    assert summary["muse-wonder-1.0 / video"]["ran"] == 0


def test_load_case_reads_the_cached_analysis_layout(tmp_path):
    reel = tmp_path / "Video-1"
    (reel / "media" / "frames").mkdir(parents=True)
    (reel / "media" / "proxy.720p.mp4").write_bytes(b"x")
    (reel / "analysis.json").write_text(json.dumps({
        "edit_intents": [{"intent": "trim", "target": {"start_s": 0, "end_s": 4}, "parameters": {"title": "Cut", "steps": ["trim it"]},
                          "source_parameter": "retention", "rationale": "faster", "evidence": ["E2"]}],
        "evidence_pack": {"items": [{"id": "E1", "source": "video", "t_start": None, "t_end": None, "text": "duration 9s"},
                                    {"id": "E2", "source": "watch", "t_start": 0, "t_end": 3, "text": "hook"}]}}))
    (reel / "report.json").write_text(json.dumps({"video": {"duration_s": 9.0, "width": 720, "height": 1280}}))
    case = dp.load_case(reel)
    assert case.intent_ids == ["I1"] and case.duration_s == 9.0 and case.proxy is not None and case.frames_dir is not None
    assert "E2 [0-3s] watch: hook" in case.evidence_text and "E1 video: duration 9s" in case.evidence_text
