"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests for the "study any reel" (link) flow: the switch (default off), the plain failure messages and time limit, what a studied reel
is and is not (no coach-style or Creator DNA addendum, no regeneration, not offered to "Did my fix work?"), and the "how to adapt this to my
niche" block (grounded in the creator's imported reels, dropped when nothing is grounded, never built without a usable DNA).
"""
import asyncio
import json

import pytest

from app.core.config import Settings
from app.studio import links
from app.studio.instagram import dna as D
from app.studio.instagram import personalise
from app.studio.tests.test_instagram_hardening import data_for, mk, fp

run = asyncio.run


# ------------------------------------------------------------------ the switch and the settings

def test_links_are_off_by_default_and_the_limits_have_sane_defaults():
    s = Settings(_env_file=None)
    assert s.STUDIO_ALLOW_LINKS is False
    assert s.STUDIO_LINK_DAILY_LIMIT == 20 and 30 <= s.STUDIO_LINK_DOWNLOAD_TIMEOUT_S <= 300


def test_empty_env_value_keeps_links_off(monkeypatch):
    monkeypatch.setenv("STUDIO_ALLOW_LINKS", "")
    assert Settings(_env_file=None).STUDIO_ALLOW_LINKS is False
    monkeypatch.setenv("STUDIO_ALLOW_LINKS", "1")
    assert Settings(_env_file=None).STUDIO_ALLOW_LINKS is True


# ------------------------------------------------------------------ fetching a linked reel

def test_a_blocked_link_gives_the_plain_message_and_no_internal_detail(monkeypatch):
    def boom(url, work_dir):
        raise RuntimeError("secret token abc123 in a stack trace")
    monkeypatch.setattr(links, "_fetch", boom)
    with pytest.raises(links.LinkError) as e:
        run(links.fetch_linked_reel("https://www.instagram.com/reel/X/", "/tmp", 5))
    assert str(e.value) == links.BLOCKED_MESSAGE and "abc123" not in str(e.value) and "Upload the video file" in str(e.value)


def test_a_slow_download_stops_at_the_time_limit(monkeypatch):
    import time
    monkeypatch.setattr(links, "_fetch", lambda url, wd: time.sleep(1.5))
    with pytest.raises(links.LinkError) as e:
        run(links.fetch_linked_reel("https://www.instagram.com/reel/X/", "/tmp", 0.2))
    assert str(e.value) == links.TIMEOUT_MESSAGE


def test_a_good_download_returns_metadata_and_path(monkeypatch):
    monkeypatch.setattr(links, "_fetch", lambda url, wd: ({"likeCount": 5}, "/tmp/video.mp4"))
    assert run(links.fetch_linked_reel("u", "/tmp", 5)) == ({"likeCount": 5}, "/tmp/video.mp4")


# ------------------------------------------------------------------ what a studied reel is

def test_only_the_competitor_intent_is_a_studied_reel():
    assert links.is_studied("competitor") and not links.is_studied("my_video") and not links.is_studied(None)


def test_a_studied_reel_gets_neither_the_coach_style_nor_the_creator_dna_addendum():
    from app.services.prysm_analyse_service import _style_for
    assert _style_for("competitor", "\n\nCREATOR DNA ADDENDUM") == ""
    assert "CREATOR DNA ADDENDUM" in _style_for("my_video", "\n\nCREATOR DNA ADDENDUM")


def test_studied_reels_are_not_offered_to_did_my_fix_work():
    import inspect
    from app.studio.instagram import closeloop
    assert "IS DISTINCT FROM 'competitor'" in inspect.getsource(closeloop.list_analyses)


def test_the_worker_keeps_only_the_result_text_of_a_studied_reel():
    import inspect
    from app.routers import prysm
    src = inspect.getsource(prysm._run_analysis_task)
    assert 'first_frame, embeddings = None, {}' in src and 'result.pop("frames", None)' in src
    assert 'raise _NoDna()' in src                       # no personalisation, so no comparison block either
    assert 'adapt_block' in src


# ------------------------------------------------------------------ "how to adapt this to my niche"

def reels():
    return [mk(1, caption="plain", fingerprint=fp("question", "talking_head", "fast", audio="unknown"), index=2.0, wt=0.7),
            mk(2, caption="plain", fingerprint=fp("visual_only", "montage", "slow", audio="unknown"), index=0.5, wt=0.2)]


def studied_result():
    return {"summary": "A fast question hook then three quick proof shots.",
            "nextVideoTemplate": {"hook": "Open on a question", "structure": "Question, three proof shots, one line close", "audio": "Trending beat",
                                  "text": "Big number on screen", "cta": "Follow for part two"}}


def patch_ask(monkeypatch, bullets):
    async def ask(content, max_tokens, spend, *, system=None):
        spend.add(0.004)
        return json.dumps({"adapt": bullets})
    monkeypatch.setattr(D, "_ask", ask)


def patch_load(monkeypatch, data):
    async def load(db, user_id):
        return data
    monkeypatch.setattr(personalise, "load", load)


def test_adaptation_keeps_grounded_bullets_and_drops_ones_about_unknown_reels(monkeypatch):
    data = data_for(reels())
    data["dna"].sample_size = 2
    data["dna"].dna.update({"strongest": [{"claim": "open with a question", "reels": [1]}], "weakest": [], "keep": [], "change": []})
    patch_load(monkeypatch, data)
    patch_ask(monkeypatch, ["Open on a question in your niche, as your Reel 1 does, then show three quick proof shots.",
                            "Your Reel 77 already proves this works, so reuse its exact scene.",
                            "Close with one clear line asking for the follow, in your own words."])
    spend = D.Spend(0.6)
    out = run(personalise.adapt_block(None, "u", studied_result(), "Fitness", spend))
    assert out["note"] == "Borrow the structure, not the content."
    assert len(out["bullets"]) == 2 and not any("Reel 77" in b for b in out["bullets"])
    assert out["niche"] == "Fitness" and out["sampleSize"] == 2 and out["costUsd"] == pytest.approx(0.004)


def test_no_usable_dna_means_no_block_and_no_model_call(monkeypatch):
    called = []

    async def ask(*a, **k):
        called.append(1)
        return "{}"
    monkeypatch.setattr(D, "_ask", ask)
    patch_load(monkeypatch, None)
    assert run(personalise.adapt_block(None, "u", studied_result(), "Fitness", D.Spend(0.6))) is None
    data = data_for(reels()); data["dna"].dna["mode"] = "breakdown"
    patch_load(monkeypatch, data)
    assert run(personalise.adapt_block(None, "u", studied_result(), "Fitness", D.Spend(0.6))) is None
    assert called == []


def test_when_every_bullet_is_ungrounded_or_the_call_fails_there_is_no_block(monkeypatch):
    data = data_for(reels()); data["dna"].sample_size = 2
    patch_load(monkeypatch, data)
    patch_ask(monkeypatch, ["Like your Reel 9 does, copy its opening scene."])
    assert run(personalise.adapt_block(None, "u", studied_result(), "Fitness", D.Spend(0.6))) is None

    async def boom(*a, **k):
        raise RuntimeError("model down")
    monkeypatch.setattr(D, "_ask", boom)
    assert run(personalise.adapt_block(None, "u", studied_result(), "Fitness", D.Spend(0.6))) is None


def test_the_adaptation_prompt_says_borrow_the_structure_and_lists_only_known_facts():
    data = data_for(reels()); data["dna"].sample_size = 2
    p = personalise.adapt_prompt(studied_result(), data, "Fitness")
    assert "borrow the structure, not the content" in p and "STUDIED REEL" in p and "Reel 1" in p
    assert "never invent" in p


def test_personalisation_off_turns_the_block_off(monkeypatch):
    monkeypatch.setenv("COACH_PERSONALISE", "0")
    patch_load(monkeypatch, data_for(reels()))
    assert run(personalise.adapt_block(None, "u", studied_result(), "Fitness", D.Spend(0.6))) is None
