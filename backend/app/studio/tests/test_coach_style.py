"""Built during the Meta Global AI Developer Hackathon (new work).

COACH_STYLE addendum, DEBUG_SAVE_INPUTS and the scoring replay (scripts/rescore.py), all offline.
"""
import asyncio
import json

import pytest

from app.core.config import NICHE_WEIGHTS, Settings
from app.studio import coach_style, meta_llm_shim as shim, rescore


def build(**extra):
    from app.services.prysm_analyse_service import build_scoring_prompts
    kw = dict(niche="Lifestyle", platform="Instagram Reels", source_type="upload", intent="my_video", brief="", content_goal="",
              scored_weights=NICHE_WEIGHTS["lifestyle"], niche_ctx="", hook_retention_ctx="", niche_trend_lens="",
              vision_data=None, whisper_data={"transcript": "hello there", "segments": []}, gemini_audio={}, audio_measurements={},
              energy_data={}, aesthetics_data={}, engagement_data={}, trend_data={}, song_trend_match={}, saved_frames=[],
              post_metadata=None, audio_path=None, has_vision=False, has_audio=False)
    kw.update(extra)
    return build_scoring_prompts(**kw)


def test_the_style_is_on_by_default_in_meta_mode_and_never_in_baseline_mode(monkeypatch):
    from app.core.config import settings
    assert Settings.model_fields["COACH_STYLE"].default is True and Settings.model_fields["DEBUG_SAVE_INPUTS"].default is False
    assert coach_style.addendum(False) == ""
    monkeypatch.setattr(settings, "AI_PROVIDER", "meta")
    assert coach_style.addendum() == coach_style.addendum(True) != ""
    monkeypatch.setattr(settings, "COACH_STYLE", False)
    assert coach_style.addendum() == ""                      # COACH_STYLE=0 turns it off
    monkeypatch.setattr(settings, "COACH_STYLE", True)
    monkeypatch.setattr(settings, "AI_PROVIDER", "")
    assert coach_style.addendum() == ""                      # without AI_PROVIDER=meta the prompts are the baseline's


def test_without_the_addendum_the_prompts_are_the_baseline_prompts():
    a0, b0 = build()
    a1, b1 = build(style_addendum="")
    assert (a0, b0) == (a1, b1)


def test_the_addendum_is_appended_to_both_prompts_and_nothing_above_it_changes():
    a0, b0 = build()
    add = coach_style.addendum(True)
    a1, b1 = build(style_addendum=add)
    assert a1 == a0 + add and b1 == b0 + add and add.startswith("\n\n")


def test_the_style_guide_is_a_de_identified_pattern_guide():
    t = coach_style.style_text()
    for must in ["AT LEAST 3 steps", "READY-TO-USE WORDING", "fallback", "Step 1 —", "[exact words]"]:
        assert must in t, must
    for banned in ["theprysm", "krishiv", "Mitran", "saree", "Jazzy"]:
        assert banned.lower() not in t.lower()


# ---------------------------------------------------------------- replay

A = {"parameters": [{"name": n, "score": 70, "problem": "p.", "fix": "f.", "whatIsHappening": "At 3s she says 'hello there' to camera.",
                     "whyItMatters": "w.", "whatToChange": "c.", "howToDoIt": "Step 1 — Do one thing here now. Step 2 — Do two things here now. Step 3 — Do three things now.", "whatToExpect": "e."}
                    for n in ["Hook & Opening", "Retention & Pacing", "Shareability", "Audio & Music", "Trend Alignment"]], "summary": "s"}
B = {"parameters": [{"name": n, "score": 70, "problem": "p.", "fix": "f.", "whatIsHappening": "h.", "whyItMatters": "w.", "whatToChange": "c.",
                     "howToDoIt": "Step 1 — Say 'follow me for more' near the end. Step 2 — Put it on screen too now. Step 3 — Pin it as a comment now.", "whatToExpect": "e."}
                    for n in ["Visual Quality & Originality", "On-Screen Text", "Save Potential", "Replay Value", "Call to Action"]]}


def saved_inputs(addendum=""):
    return {"call_a_prompt": "PROMPT A" + addendum, "call_b_prompt": "PROMPT B" + addendum, "style_addendum": addendum,
            "max_tokens_a": 7000, "max_tokens_b": 6000, "intent": "my_video", "niche": "Lifestyle", "platform": "Instagram Reels",
            "source_type": "upload", "scored_weights": NICHE_WEIGHTS["lifestyle"],
            "whisper_data": {"transcript": "hello there everyone", "segments": []}, "vision_data": {"frames": []}}


@pytest.fixture
def fake_post(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "meta")
    seen = []

    def post(self, body):
        seen.append(body)
        payload = A if "PROMPT A" in body["messages"][-1]["content"] else B
        return shim.Message(content=[shim.TextBlock("```json\n" + json.dumps(payload) + "\n```")], usage=shim.Usage(1000, 2000),
                            model=body["model"], cost_usd=0.01, reasoning_tokens=500)

    monkeypatch.setattr(shim.AsyncAnthropic, "_post", post)
    from app.core.config import settings
    monkeypatch.setattr(settings, "AI_PROVIDER", "meta")
    return seen


def test_replay_runs_only_the_two_scoring_calls_with_the_chosen_model(fake_post):
    out = asyncio.run(rescore.rescore(saved_inputs(), model="muse-wonder-1.0", style=False, regen=False))
    assert len(fake_post) == 2 and {b["model"] for b in fake_post} == {"muse-wonder-1.0"}
    assert all(b["messages"][-1]["content"] in ("PROMPT A", "PROMPT B") for b in fake_post)
    st = out["stats"]
    assert st["tokens_in"] == 2000 and st["tokens_out"] == 4000 and st["reasoning_tokens"] == 1000 and st["cost_usd"] == 0.02
    assert len(out["result"]["parameters"]) == 10 and out["result"]["overallScore"] == 70


def test_replay_with_style_appends_the_addendum_and_strips_the_one_the_saved_run_used(fake_post):
    add = coach_style.addendum(True)
    asyncio.run(rescore.rescore(saved_inputs(), model="muse-spark-1.3", style=True, regen=False))
    assert all(b["messages"][-1]["content"].endswith(add) for b in fake_post)
    fake_post.clear()
    asyncio.run(rescore.rescore(saved_inputs(addendum=add), model="muse-spark-1.3", style=False, regen=False))   # saved WITH style, replayed without
    assert [b["messages"][-1]["content"] for b in fake_post] == ["PROMPT A", "PROMPT B"] or \
        sorted(b["messages"][-1]["content"] for b in fake_post) == ["PROMPT A", "PROMPT B"]


def test_replay_metrics(fake_post):
    m = asyncio.run(rescore.rescore(saved_inputs(), model="muse-spark-1.3", style=False, regen=False))["stats"]["metrics"]
    assert m["valid_json"] is True and m["truncated_fields"] == 0 and m["scored_with_fewer_than_3_steps"] == 0
    assert m["transcript_quotes"] == 5 and m["advisory_with_ready_wording"] == "4/4" and m["overall"] == 70


def test_save_and_load_inputs_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUG_SAVE_INPUTS_DIR", str(tmp_path))
    p = rescore.save_inputs(saved_inputs())
    assert p.parent == tmp_path and rescore.load_inputs(p)["call_a_prompt"] == "PROMPT A"
