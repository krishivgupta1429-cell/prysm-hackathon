"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests (Gemini mocked, no network) for live trend grounding: the Gemini Flash + Google Search call with its
fail-soft contract (malformed JSON, timeout, errors, no key), the niche-set/day cache, India context only for India-centric
niches, the trends block in the evidence pack, the grounded Trend Alignment label (and the estimate label when
unavailable), the pipeline stage, and trending hashtags in the summary prompt.
"""
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.studio import trends
from app.studio.analysis import score, validate
from app.studio.analysis.evidence import build_evidence_pack
from app.studio.analysis.niches import GROUNDED_LABEL, TREND_LABEL, TREND_PREFIX, get_niche
from app.studio.analysis.schema import EvidenceRef, Parameter
from app.studio.pipeline import _trends_stage
from app.studio.report import TrendsStage
from app.studio.tests.test_studio_analysis import make_report

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
GOOD = {"formats": ["outfit reveal", "get ready with me"], "audio": ["Song A - Artist A", "Song B - Artist B"],
        "hashtags": ["#grwm", "fashion", "#ootd"], "whats_working": {"Fashion": "Fast reveals with a clear styling hook."}}


def run(coro):
    return asyncio.run(coro)


def fake_response(payload=None, text=None, sources=("example.com", "other.org"), tin=300, tout=900):
    chunks = [SimpleNamespace(web=SimpleNamespace(uri=f"https://redirect.example/{i}", title=t)) for i, t in enumerate(sources)]
    cand = SimpleNamespace(grounding_metadata=SimpleNamespace(grounding_chunks=chunks), content=SimpleNamespace(parts=[SimpleNamespace(text=text)]))
    body = text if text is not None else json.dumps(payload if payload is not None else GOOD)
    return SimpleNamespace(text=body, candidates=[cand], usage_metadata=SimpleNamespace(prompt_token_count=tin, candidates_token_count=tout, thoughts_token_count=0))


class FakeCall:
    """A fake blocking Gemini call: records its arguments and returns/raises what it is told to."""
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def __call__(self, api_key, prompt, timeout_s):
        self.calls.append({"api_key": api_key, "prompt": prompt, "timeout_s": timeout_s})
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return outcome()
        return outcome


def fetch(call, niches=("Fashion",), **kw):
    kw.setdefault("api_key", "test-key-123")
    kw.setdefault("now", NOW)
    return run(trends.fetch_trends(list(niches), kw.pop("platform", "Instagram Reels"), kw.pop("description", "a brunch outfit reel"),
                                   kw.pop("keywords", ["outfit"]), call=call, **kw))


# ---------------------------------------------------------------- the good path

def test_a_good_answer_becomes_an_ok_stage_with_sources_and_a_cost():
    call = FakeCall(fake_response())
    stage = fetch(call)
    assert stage.status == "ok" and stage.model == "gemini-2.5-flash" and stage.geo == "global" and not stage.cached
    assert stage.formats == ["outfit reveal", "get ready with me"] and stage.audio[0] == "Song A - Artist A"
    assert stage.hashtags == ["#grwm", "#fashion", "#ootd"]  # a missing # is added
    assert stage.whats_working == {"Fashion": "Fast reveals with a clear styling hook."}
    assert [s.title for s in stage.sources] == ["example.com", "other.org"] and stage.sources[0].url.startswith("https://redirect.example/")
    assert stage.cost_usd == pytest.approx(300 * 0.30 / 1e6 + 900 * 2.50 / 1e6 + trends.GROUNDING_USD_PER_PROMPT)
    assert stage.fetched_at == "2026-11-03T12:00:00+00:00" and stage.keywords_used == ["outfit"]
    assert call.calls[0]["api_key"] == "test-key-123" and call.calls[0]["timeout_s"] <= 20


def test_the_prompt_carries_niches_platform_description_and_keywords_and_forbids_inventing():
    call = FakeCall(fake_response())
    fetch(call, niches=("Fashion", "Lifestyle"), keywords=["saree", "bedroom"], description="my brunch look")
    prompt = call.calls[0]["prompt"]
    assert "Fashion, Lifestyle" in prompt and "Instagram Reels" in prompt and "my brunch look" in prompt and "saree, bedroom" in prompt
    assert "Do not invent" in prompt and '"Fashion": "one line' in prompt and '"Lifestyle": "one line' in prompt


def test_a_text_less_response_uses_the_candidate_parts():
    response = fake_response(text=json.dumps(GOOD))
    response.text = None
    assert fetch(FakeCall(response)).status == "ok"


# ---------------------------------------------------------------- global vs India context

def test_india_context_is_added_only_for_an_india_centric_niche_the_creator_picked():
    plain = FakeCall(fake_response())
    assert fetch(plain, niches=("Comedy",)).geo == "global" and "India" not in plain.calls[0]["prompt"]
    for niche in ("Dance", "Bollywood", "IndianCreator", "Food"):
        call = FakeCall(fake_response())
        stage = fetch(call, niches=(niche,))
        assert stage.geo == "India" and "Focus on India" in call.calls[0]["prompt"], niche
    assert trends.wants_india(["Fashion", "dance"]) and not trends.wants_india(["Fitness", "Travel"])


def test_niches_are_deduplicated_and_capped_at_three():
    assert trends.normalise_niches(["Comedy", "comedy", "Fitness", "Food", "Travel", "Tech"]) == ["Comedy", "Fitness", "Food"]


# ---------------------------------------------------------------- fail-soft

@pytest.mark.parametrize("text", ["this is not json at all", "{\"formats\": [", "[]", json.dumps({"formats": [], "audio": [], "hashtags": []}), ""])
def test_malformed_or_empty_answers_are_unavailable_not_errors(text):
    stage = fetch(FakeCall(fake_response(text=text)))
    assert stage.status == "unavailable" and stage.error and stage.formats == [] and stage.cost_usd >= 0


def test_a_timeout_is_unavailable_and_not_an_exception():
    call = FakeCall(lambda: time.sleep(1.5))  # (asyncio.run waits for this thread at exit, so time the stage itself, not the test)
    stage = fetch(call, timeout_s=0.1)
    assert stage.status == "unavailable" and "timed out" in stage.error and stage.seconds < 1.4


def test_any_api_error_is_unavailable_and_the_key_never_appears_in_it():
    stage = fetch(FakeCall(RuntimeError("401 bad key test-key-123 rejected")))
    assert stage.status == "unavailable" and "test-key-123" not in stage.error and "***" in stage.error


def test_no_api_key_is_unavailable_without_a_call():
    call = FakeCall(fake_response())
    stage = fetch(call, api_key=None)
    assert stage.status == "unavailable" and call.calls == []


def test_no_niche_is_skipped_without_a_call():
    call = FakeCall(fake_response())
    stage = run(trends.fetch_trends([], "Instagram Reels", "", [], api_key="k", call=call))
    assert stage.status == "skipped" and call.calls == []


def test_an_overloaded_503_is_retried_once(monkeypatch):
    monkeypatch.setattr(trends, "RETRY_DELAY_S", 0)
    call = FakeCall(RuntimeError("503 UNAVAILABLE: high demand"), fake_response())
    assert fetch(call).status == "ok" and len(call.calls) == 2
    trends._memory.clear()
    call = FakeCall(RuntimeError("503 UNAVAILABLE"), RuntimeError("503 UNAVAILABLE"))
    assert fetch(call).status == "unavailable" and len(call.calls) == 2  # one retry, then fail soft


def test_other_errors_are_not_retried():
    call = FakeCall(RuntimeError("400 invalid argument"), fake_response())
    assert fetch(call).status == "unavailable" and len(call.calls) == 1


# ---------------------------------------------------------------- cache

class DictCache:
    def __init__(self):
        self.data, self.sets = {}, []

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ttl_s):
        self.data[key] = value
        self.sets.append((key, ttl_s))


def test_a_second_request_for_the_same_niche_set_and_day_is_a_cache_hit_with_no_cost_or_call():
    cache, call = DictCache(), FakeCall(fake_response())
    first = fetch(call, cache=cache)
    second = fetch(call, cache=cache, keywords=["a different video"])
    assert first.status == second.status == "ok" and second.cached and second.cost_usd == 0.0 and len(call.calls) == 1
    assert second.formats == first.formats and second.sources == first.sources and second.keywords_used == ["a different video"]
    assert cache.sets[0][1] == 6 * 3600  # six-hour expiry


def test_the_cache_is_per_niche_set_platform_and_day_and_ignores_niche_order():
    k = lambda niches, platform="Instagram Reels", geo="global", now=NOW: trends.cache_key(niches, platform, geo, now)  # noqa: E731
    assert k(["Fashion", "Lifestyle"]) == k(["lifestyle", "FASHION"])
    assert k(["Fashion"]) != k(["Fashion", "Lifestyle"]) != k(["Comedy"])
    assert k(["Fashion"]) != k(["Fashion"], platform="TikTok") and k(["Fashion"]) != k(["Fashion"], now=NOW + timedelta(days=1))
    assert k(["Fashion"]).startswith("prysm:studio:trends:20261103:")


def test_without_a_redis_cache_an_in_memory_cache_serves_the_second_request():
    call = FakeCall(fake_response())
    fetch(call)
    assert fetch(call).cached and len(call.calls) == 1


def test_a_broken_redis_cache_falls_back_to_memory_and_never_breaks_the_stage():
    class Broken:
        async def get(self, key):
            raise ConnectionError("redis down")

        async def set(self, key, value, ttl_s):
            raise ConnectionError("redis down")

    call = FakeCall(fake_response())
    assert fetch(call, cache=Broken()).status == "ok"
    assert fetch(call, cache=Broken()).cached and len(call.calls) == 1


def test_unavailable_results_are_not_cached():
    cache, call = DictCache(), FakeCall(fake_response(text="nope"), fake_response())
    assert fetch(call, cache=cache).status == "unavailable" and cache.data == {}
    assert fetch(call, cache=cache).status == "ok"


# ---------------------------------------------------------------- the evidence pack

def report_with_trends(stage: TrendsStage):
    report = make_report()
    report.stages.trends = stage
    return report


def ok_stage(**over):
    return TrendsStage(status="ok", niches=["Fashion"], platform="Instagram Reels", formats=["outfit reveal"], audio=["Song A - Artist A"],
                       hashtags=["#grwm", "#ootd"], whats_working={"Fashion": "Fast reveals with a clear styling hook."}, **over)


def test_the_evidence_pack_gets_a_trends_block_when_the_stage_is_ok():
    pack = build_evidence_pack(report_with_trends(ok_stage()))
    lines = [i for i in pack.items if i.source == "trends"]
    assert pack.trends_ok and len(lines) == 4 and pack.trend_ids() == {i.id for i in lines}
    text = pack.render()
    assert "LIVE TREND from a Google search today" in text and "outfit reveal" in text and "Song A - Artist A" in text and "#grwm" in text
    assert "Fast reveals with a clear styling hook." in text
    assert pack.as_dict()["trends_ok"] is True and set(lines[0].id for _ in [0]) <= pack.citable_ids()


def test_an_unavailable_or_skipped_stage_adds_nothing_and_keeps_the_estimate_path():
    for stage in (TrendsStage(status="unavailable", error="timeout"), TrendsStage()):
        pack = build_evidence_pack(report_with_trends(stage))
        assert not pack.trends_ok and not [i for i in pack.items if i.source == "trends"]


def test_india_context_is_marked_in_the_evidence_line():
    pack = build_evidence_pack(report_with_trends(ok_stage(geo="India")))
    assert "India context" in pack.render()


def test_the_live_trend_lines_are_the_last_to_be_dropped_when_the_cap_is_hit(monkeypatch):
    from app.studio import config
    full = build_evidence_pack(report_with_trends(ok_stage()))
    monkeypatch.setattr(config, "EVIDENCE_MAX_TOKENS", 330)
    tight = build_evidence_pack(report_with_trends(ok_stage()))
    assert len(tight.items) < len(full.items) and tight.trends_ok and len(tight.trend_ids()) == 4


# ---------------------------------------------------------------- the scoring prompt

def test_the_scoring_prompt_asks_for_grounded_judging_when_trends_are_ok_and_for_an_estimate_otherwise():
    niche = get_niche("comedy")
    grounded = score.build_prompt(build_evidence_pack(report_with_trends(ok_stage())), niche)
    assert 'source "trends"' in grounded and "never invent or assume a trend" in grounded and "cite BOTH" in grounded
    assert "no live trend data in this version" not in grounded and TREND_PREFIX not in grounded
    estimate = score.build_prompt(build_evidence_pack(report_with_trends(TrendsStage(status="unavailable"))), niche)
    assert "no live trend data in this version" in estimate and "estimate" in estimate and TREND_PREFIX in estimate
    assert 'source "trends"' not in estimate


# ---------------------------------------------------------------- validation: grounded vs estimate

def trend_param(*ids, confidence="high"):
    return Parameter(id="trend", score=72, confidence=confidence, evidence=[EvidenceRef(id=i) for i in ids])


def validated(pack, param):
    out, flags = validate.validate_parameters([param], pack)
    return next(p for p in out if p.id == "trend"), flags


def test_a_trend_score_that_cites_a_trend_line_and_the_video_is_grounded_and_may_keep_high_confidence():
    pack = build_evidence_pack(report_with_trends(ok_stage()))
    trend_id = sorted(pack.trend_ids())[0]
    p, flags = validated(pack, trend_param(trend_id, "E4"))
    assert p.label == GROUNDED_LABEL and not p.evidence_limited and p.confidence == "high"
    assert "grounded" in GROUNDED_LABEL and not [f for f in flags if f.parameter == "trend"]


def test_citing_only_trend_lines_caps_confidence_at_medium():
    pack = build_evidence_pack(report_with_trends(ok_stage()))
    p, flags = validated(pack, trend_param(sorted(pack.trend_ids())[0]))
    assert p.label == GROUNDED_LABEL and p.confidence == "medium"
    assert any(f.code == "trend_confidence_capped" for f in flags)


def test_live_trends_that_are_not_cited_leave_the_score_an_estimate_and_flag_it():
    pack = build_evidence_pack(report_with_trends(ok_stage()))
    p, flags = validated(pack, trend_param("E4"))
    assert p.label == TREND_LABEL and p.evidence_limited and p.confidence == "medium"
    assert any(f.code == "trend_not_grounded" for f in flags)


def test_without_live_trends_the_label_is_the_estimate_as_before():
    pack = build_evidence_pack(make_report())
    p, flags = validated(pack, trend_param("E4"))
    assert p.label == TREND_LABEL and p.evidence_limited and p.confidence == "medium"
    assert not any(f.code == "trend_not_grounded" for f in flags)


# ---------------------------------------------------------------- the pipeline stage

class Creator:
    niches, description = ["Fashion", "Lifestyle"], "a brunch look"


def stage_for(creator=Creator, skip=(), fetch_fn=None, watch=None, progress=None, platform="Instagram Reels"):
    async def watch_getter():
        return watch

    async def default_fetch(niches, platform_, description, keywords, **kw):
        default_fetch.seen = {"niches": niches, "platform": platform_, "description": description, "keywords": keywords, **kw}
        return TrendsStage(status="ok", niches=list(niches), formats=["x"])

    return run(_trends_stage(progress or (lambda *a: None), set(skip), creator, platform, "CACHE", fetch_fn or default_fetch, watch_getter)), default_fetch


def test_the_pipeline_stage_passes_niches_description_platform_cache_and_watch_keywords():
    from app.studio.report import WatchStage
    watch = WatchStage(status="ok", format_type="skit", subject_description="two roommates arguing", setting="kitchen")
    stage, fetch_fn = stage_for(watch=watch)
    assert stage.status == "ok"
    seen = fetch_fn.seen
    assert seen["niches"] == ["Fashion", "Lifestyle"] and seen["description"] == "a brunch look" and seen["platform"] == "Instagram Reels"
    assert seen["cache"] == "CACHE" and seen["keywords"][0] == "skit" and "kitchen" in seen["keywords"]


def test_the_pipeline_stage_reports_running_and_is_skipped_by_skip_or_without_a_niche():
    events = []
    stage_for(progress=lambda s, st: events.append((s, st)))
    assert events == [("trends", "running")]
    assert stage_for(skip={"trends"})[0].status == "skipped"
    assert stage_for(creator=None)[0].status == "skipped"
    assert stage_for(creator=SimpleNamespace(niches=[], description=""))[0].status == "skipped"


def test_a_fetch_that_raises_or_hangs_never_breaks_or_stalls_the_pipeline(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("anything")

    stage, _ = stage_for(fetch_fn=boom)
    assert stage.status == "unavailable" and stage.niches == ["Fashion", "Lifestyle"]
    monkeypatch.setattr(trends, "TIMEOUT_S", 0.05)
    monkeypatch.setattr(trends, "STAGE_GRACE_S", 0.05)

    async def hang(*a, **k):
        await asyncio.sleep(30)

    started = time.time()
    stage, _ = stage_for(fetch_fn=hang)
    assert stage.status == "unavailable" and time.time() - started < 6


def test_trends_are_not_stored_in_the_perception_cache(tmp_path):
    from app.studio import perception_cache
    report = report_with_trends(ok_stage())
    perception_cache.save(tmp_path, "abc", "key", report)
    loaded = perception_cache.load(tmp_path, "abc", "key")
    assert loaded is not None and loaded.stages.trends.status == "skipped" and loaded.stages.watch.status == "ok"
    assert report.stages.trends.status == "ok"  # the caller's report is untouched


def test_watch_keywords_are_a_few_short_strings_and_empty_when_watch_failed():
    from app.studio.report import OnScreenText, WatchStage
    watch = WatchStage(status="ok", format_type="dance", subject_description="x" * 200, setting="studio", on_screen_text=[OnScreenText(text="Hello"), OnScreenText(text="World"), OnScreenText(text="Third")])
    words = trends.keywords_from_watch(watch)
    assert 1 < len(words) <= 4 and words[0] == "dance" and all(len(w) <= 60 for w in words)
    assert trends.keywords_from_watch(WatchStage(status="error")) == [] and trends.keywords_from_watch(None) == []


# ---------------------------------------------------------------- summary hashtags, cost and the Models-used rows

def test_the_summary_prompt_offers_live_trending_hashtags_only_when_available():
    from app.studio import adapter
    assert adapter.trend_block(None) == "" and adapter.trend_block(TrendsStage(status="unavailable")) == ""
    block = adapter.trend_block(ok_stage())
    assert "#grwm" in block and "never use a trending hashtag that does not fit" in block
    assert "{trend_block}" in adapter.EXTRAS_PROMPT


def test_trend_cost_is_in_the_cost_summary_even_when_the_perception_was_cached():
    from app.studio import adapter
    report = report_with_trends(ok_stage(cost_usd=0.04))
    summary = adapter.cost_summary(report, cached=True, extra_items=[])
    assert [i["label"] for i in summary["items"] if "trends" in i["label"].lower()] and summary["total_usd"] == pytest.approx(0.04)


def test_models_used_lists_gemini_flash_for_trends_with_its_status():
    from app.studio import adapter
    rows = adapter.models_used(report_with_trends(ok_stage(sources=[{"title": "a.com", "url": "https://x"}])))
    trend_row = next(r for r in rows if r["step"] == "Live trends")
    assert trend_row["model"] == "Gemini 2.5 Flash + Google Search" and trend_row["status"] == "ok" and "1 sources" in trend_row["note"]
    down = next(r for r in adapter.models_used(report_with_trends(TrendsStage(status="unavailable"))) if r["step"] == "Live trends")
    assert down["status"] == "unavailable" and "not available" in down["note"]
    assert any("muse-spark-1.3" in r["model"] and "Summary and chat" in r["step"] for r in rows)


def test_the_website_progress_list_and_results_panel_know_about_trends():
    root = Path(__file__).resolve().parents[4] / "web" / "src"
    assert "id: 'trends'" in (root / "studio" / "progress.ts").read_text()
    results = (root / "pages" / "Results.tsx").read_text()
    assert "Models used" in results and "models_used" in results and "param.label" in results
