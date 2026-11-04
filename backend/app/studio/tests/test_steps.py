"""Built during the Meta Global AI Developer Hackathon (new work).

Defects seen in the bake-off screenshots: (a) a quoted phrase cut off mid-sentence by a character clip, (b) steps merged into one item and
an unnumbered extra line in a step list, (c) a quote split across two steps at '..' or a period inside it, (d) scored parameters
with only 1-2 steps and a Shareability card with only the fallback line.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.studio import meta_llm_shim as shim, step_quality, steps


# ---------------------------------------------------------------- (c) never split inside quotes or at an ellipsis

def test_a_quote_with_a_double_dot_or_a_period_inside_is_not_split():
    t = ("Step 1 — At 12s say: 'the lyrics said salwar suit.. wait for it' and point. Step 2 — Put 'Wait for it. Then drop.' on screen at 3s. "
         "Step 3 — Hold the pose for 0.4 seconds.")
    got = steps.split_steps(t)
    assert got == ["At 12s say: 'the lyrics said salwar suit.. wait for it' and point.", "Put 'Wait for it. Then drop.' on screen at 3s.",
                   "Hold the pose for 0.4 seconds."]


def test_sentences_are_not_split_at_ellipses_decimals_abbreviations_or_lower_case_words():
    assert steps.split_sentences("Trim at 12.5s... then wait. Use e.g. a zoom. Done.") == ["Trim at 12.5s... then wait.", "Use e.g. a zoom.", "Done."]
    assert steps.split_sentences("Say “stop. look.” now. Then cut.") == ["Say “stop. look.” now.", "Then cut."]
    assert steps.split_sentences("It's fine, don't worry. Next one.") == ["It's fine, don't worry.", "Next one."]


# ---------------------------------------------------------------- (b) one numbered item per step

def test_merged_steps_become_separate_numbered_items_and_the_format_is_canonical():
    assert steps.normalise("Step 1: Do a Step 2 - Do b. Step 3) Do c.") == "Step 1 — Do a Step 2 — Do b. Step 3 — Do c."
    assert steps.split_steps("Step 1 — Do a. Step 2 — Do b.") == ["Do a.", "Do b."]


def test_an_unnumbered_line_inside_a_step_list_is_folded_into_its_step_and_a_fallback_becomes_its_own_step():
    t = "Step 1 — A mid-video re-hook exists at 0s. Rather than adding another, strengthen it. Step 2 — Do b. Fallback: If that feels forced, do c."
    got = steps.split_steps(t)
    assert got == ["A mid-video re-hook exists at 0s. Rather than adding another, strengthen it.", "Do b.", "Fallback: If that feels forced, do c."]
    assert steps.normalise(t).count("Step ") == 3


def test_text_without_markers_is_split_into_numbered_steps_and_list_items_work():
    assert steps.split_steps("Trim the end. Add a caption. Export it.") == ["Trim the end.", "Add a caption.", "Export it."]
    assert steps.split_steps("1. Trim the end\n2) Add a caption\n- Export it") == ["Trim the end", "Add a caption", "Export it"]
    assert steps.split_steps(["Step 1 — a one.", "Step 2 — b two."]) == ["a one.", "b two."]


# ---------------------------------------------------------------- (d) the main line comes first

def test_a_fallback_line_never_comes_first():
    got = steps.split_steps("Fallback: If voicing feels awkward, mime it. Step 1 — Say 'tag a friend' at 12s. Step 2 — Hold the pose.")
    assert not steps.is_fallback(got[0]) and any(steps.is_fallback(s) for s in got)
    assert "tag a friend" in got[0]


def test_fallback_detection_does_not_catch_ordinary_if_steps():
    assert steps.is_fallback("Fallback: do x.") and steps.is_fallback("If that feels forced, do x instead.")
    assert not steps.is_fallback("If yes, lock it before you post.") and not steps.is_fallback("Check it twice.")


# ---------------------------------------------------------------- (a) no clipped quote

def test_shorten_quote_ends_at_a_word_boundary_with_an_ellipsis():
    long = "I knew it was not easy but I still signed up for this and breaking down is part of the story " * 3
    out = steps.shorten_quote(long, 60)
    assert out.endswith("…") and len(out) <= 61 and not out.rstrip("…").endswith("br") and out.rstrip("…") in long
    assert steps.shorten_quote("short text") == "short text"


def test_the_validator_rewrite_does_not_clip_a_transcript_quote_mid_word():
    from app.services.prysm_analyse_service import _validate_suggestions
    seg_text = "I knew it was not easy but I still signed up for this and breaking down is a real part of my story right now"
    result = {"parameters": [{"name": "Retention & Pacing",
                              "howToDoIt": "Step 1 — Add a re-hook before the middle at 10s so viewers stay. Step 2 — Trim the end. Step 3 — Check the loop."}]}
    out = _validate_suggestions(result, {"transcript": "wait before this " + seg_text, "segments": [{"start": 0.0, "text": seg_text}]},
                                {"frames": []}, "my_video")
    text = out["parameters"][0]["howToDoIt"]
    assert "already exists in your voiceover" in text and "and br'" not in text and seg_text[:100] in text


# ---------------------------------------------------------------- (d) minimum steps survive the validator

def test_the_validator_never_leaves_fewer_than_three_steps_because_of_timestamp_dedup():
    from app.services.prysm_analyse_service import _validate_suggestions
    r = {"parameters": [
        {"name": "Hook & Opening", "howToDoIt": "Step 1 — At 10s do x. Step 2 — At 12s do y. Step 3 — At 14s do z."},
        {"name": "Visual Quality & Originality", "howToDoIt": "Step 1 — At 10s check the lighting here. Step 2 — At 12s check the framing now. Step 3 — At 14s check the edges."}]}
    out = _validate_suggestions(r, {"transcript": "", "segments": []}, {"frames": []}, "my_video")
    assert len(steps.split_steps(out["parameters"][1]["howToDoIt"])) == 3
    assert out["parameters"][1]["howToDoIt"].startswith("Step 1 — At 10s check the lighting")   # original order kept


def test_the_validator_keeps_a_step_with_two_sentences_as_one_step():
    from app.services.prysm_analyse_service import _validate_suggestions
    how = "Step 1 — Open the clip. Then trim it. Step 2 — Add text 'wait for it. then drop' at 3s. Step 3 — Export it here."
    out = _validate_suggestions({"parameters": [{"name": "Shareability", "howToDoIt": how}]}, {"transcript": "", "segments": []}, {"frames": []}, "my_video")
    assert len(steps.split_steps(out["parameters"][0]["howToDoIt"])) == 3


# ---------------------------------------------------------------- the step check and the regeneration retry

def param(name, how, advisory=False):
    return {"name": name, "score": 60, "advisory": advisory, "problem": "p.", "fix": "f.", "whatIsHappening": "h.", "whyItMatters": "w.",
            "whatToChange": "c.", "howToDoIt": how, "whatToExpect": "e."}


GOOD = "Step 1 — Say 'send this to your friend' at 12s. Step 2 — Hold the pose for a beat. Step 3 — Check it plays well. Fallback: If that feels forced, mime it."


def test_the_check_flags_too_few_steps_fallback_first_no_ready_line_and_cut_off_steps():
    assert step_quality.problems(param("Shareability", GOOD)) == []
    assert any("only 2" in p for p in step_quality.problems(param("Hook & Opening", "Step 1 — Do a thing. Step 2 — Do more.")))
    # a fallback written first is moved behind the main step before the check, so the card never opens with the fallback line
    assert step_quality.problems(param("Hook & Opening", "Step 1 — Fallback: if that fails, do x. Step 2 — Do b. Step 3 — Do c.")) == []
    assert steps.split_steps("Step 1 — Fallback: if that fails, do x. Step 2 — Do b. Step 3 — Do c.")[0] == "Do b."
    assert any("ready-to-say" in p for p in step_quality.problems(param("Shareability", "Step 1 — Do a. Step 2 — Do b. Step 3 — Do c.")))
    assert not any("ready-to-say" in p for p in step_quality.problems(param("Hook & Opening", "Step 1 — Do a. Step 2 — Do b. Step 3 — Do c.")))
    assert any("cut off" in p for p in step_quality.problems(param("Hook & Opening", "Step 1 — Do a. Step 2 — Do b. Step 3 — Mark it from 'd")))


def fake_reply(text):
    return shim.Message(content=[shim.TextBlock(text)], usage=shim.Usage(500, 700), model="muse-spark-1.3", cost_usd=0.004, reasoning_tokens=300)


def test_a_failing_parameter_is_regenerated_once_and_the_others_are_left_alone():
    result = {"parameters": [param("Hook & Opening", "Step 1 — Do a thing. Step 2 — Do more."), param("Retention & Pacing", "Step 1 — a one. Step 2 — b two. Step 3 — c three.")]}
    calls = []

    async def call(prompt, max_tokens):
        calls.append(prompt)
        return fake_reply(json.dumps({"howToDoIt": "Step 1 — At 3s say 'hi there'. Step 2 — Cut the pause. Step 3 — Watch it back."}))

    stats = asyncio.run(step_quality.regenerate_failing(result, call=call, transcript="hi there friends", niche="Lifestyle", platform="Instagram Reels", intent="my_video"))
    assert len(calls) == 1 and stats.regenerated == ["Hook & Opening"] and stats.still_failing == []
    assert "hi there" in result["parameters"][0]["howToDoIt"] and result["parameters"][1]["howToDoIt"].startswith("Step 1 — a one.")
    assert stats.calls == 1 and stats.tokens_out == 700 and stats.reasoning_tokens == 300 and stats.cost_usd == 0.004
    assert "ready-to-say" in calls[0] and "hi there friends" in calls[0] and "only 2 step(s)" in calls[0]


def test_a_regeneration_that_fails_or_is_worse_leaves_the_original(monkeypatch):
    result = {"parameters": [param("Hook & Opening", "Step 1 — Do a thing. Step 2 — Do more.")]}

    async def broken(prompt, max_tokens):
        raise RuntimeError("down")

    stats = asyncio.run(step_quality.regenerate_failing(result, call=broken, transcript="", niche="x", platform="y", intent="my_video"))
    assert stats.still_failing == ["Hook & Opening"] and result["parameters"][0]["howToDoIt"] == "Step 1 — Do a thing. Step 2 — Do more."

    async def worse(prompt, max_tokens):
        return fake_reply(json.dumps({"howToDoIt": "Step 1 — Only one."}))

    asyncio.run(step_quality.regenerate_failing(result, call=worse, transcript="", niche="x", platform="y", intent="my_video"))
    assert result["parameters"][0]["howToDoIt"] == "Step 1 — Do a thing. Step 2 — Do more."


def test_the_pipeline_normalises_and_checks_in_meta_mode_only():
    import inspect
    from app.services import prysm_analyse_service as svc
    src = inspect.getsource(svc)
    assert "if settings.META_MODE:\n            # Hackathon: one canonical" in src and "settings.STEP_REGENERATE" in src


def test_the_web_step_parser_splits_only_at_markers():
    import re
    src = (__import__("pathlib").Path(__file__).resolve().parents[4] / "web" / "src" / "studio" / "steps.ts").read_text()
    assert "Step\\s*\\d+" in src and "parseMarkedSteps" in src


def test_an_unclosed_quote_is_detected_and_fails_the_check():
    assert steps.has_unclosed_quote("Say 'the lyrics said salwar suit.. wait for") and not steps.has_unclosed_quote("Say 'it's fine' now.")
    assert not steps.has_unclosed_quote("Don't stop; it’s “fine” now.")
    assert any("never closed" in p for p in step_quality.problems(param("Hook & Opening", "Step 1 — Do a. Step 2 — Do b. Step 3 — Say 'wait for it")))


def test_empty_values_as_in_env_example_mean_not_set(monkeypatch):
    from app.core.config import Settings
    for name in ("COACH_STYLE", "STEP_REGENERATE", "DEV_BAKEOFF", "STUDIO_UNLIMITED"):
        monkeypatch.setenv(name, "")
    s = Settings(_env_file=None)    # not the developer's own .env
    assert s.COACH_STYLE is True and s.STEP_REGENERATE is True and s.DEV_BAKEOFF is False and s.STUDIO_UNLIMITED is False


# ---- no made-up figure about Prysm's own data (hackathon fix)

def test_a_count_of_reels_presented_as_prysms_own_data_is_a_problem():
    from app.studio import step_quality as q
    for bad in ["Say: 'Based on 1,000 reels we analysed, hooks under 2 seconds win.'", "Prysm data from 1000 videos shows this works.", "Our analysis of 2k creators says so."]:
        assert q.claims_own_data(bad), bad
    for ok in ["Cut the first 3 videos clips you filmed down to one.", "Trim the intro to 2 seconds.", "Post 3 reels this week and compare the saves."]:
        assert not q.claims_own_data(ok), ok
    p = {"name": "Hook & Opening", "howToDoIt": "Step 1 — Say 'Based on 1,000 reels we analysed, this hook works.' Step 2 — Trim the pause at 0:02. Step 3 — Watch the first 3 seconds."}
    assert any("own data" in w for w in q.problems(p))
