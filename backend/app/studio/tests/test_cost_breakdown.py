"""Built during the Meta Global AI Developer Hackathon (new work). Cost breakdown, reasoning tokens in the cost, who may see it."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.studio import config, cost_breakdown as cb, meta_llm_shim as shim
from app.studio.cost_access import cost_visible, strip_cost
from app.studio.meta_client import CostEntry


# ---------------------------------------------------------------- reasoning tokens are billed as output tokens

class FakeResp:
    status_code = 200

    def __init__(self, payload):
        self.payload, self.text = payload, json.dumps(payload)

    def json(self):
        return self.payload


def test_reasoning_tokens_are_part_of_the_output_tokens_and_the_cost(monkeypatch):
    """Meta reports completion_tokens INCLUDING reasoning_tokens; the app prices completion_tokens, so reasoning is charged as output."""
    monkeypatch.setenv("META_API_KEY", "test-key-not-real")
    payload = {"choices": [{"message": {"content": "hello."}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 10_000, "completion_tokens": 8_000, "completion_tokens_details": {"reasoning_tokens": 6_500}}}
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp(payload))
    client = shim.AsyncAnthropic()
    reply = asyncio.run(client.messages.create(model="claude-sonnet-4-6", max_tokens=100, messages=[{"role": "user", "content": "x"}]))
    assert reply.usage.output_tokens == 8_000 and reply.reasoning_tokens == 6_500       # reasoning is inside the 8,000
    expected = (10_000 * config.SPARK_USD_PER_M_IN + 8_000 * config.SPARK_USD_PER_M_OUT) / 1e6
    assert reply.cost_usd == pytest.approx(expected, abs=1e-6)                          # not 1,500 output tokens
    entry = client.meta.ledger[-1]
    assert (entry.tokens_in, entry.tokens_out, entry.reasoning_tokens) == (10_000, 8_000, 6_500)


def test_the_app_cost_tracker_prices_the_same_tokens_the_same_way():
    from app.services.prysm_analyse_service import CostTracker
    t = CostTracker()
    t.add_claude("muse-spark-1.3", 10_000, 8_000, "Scoring")
    assert t.total_usd == pytest.approx((10_000 * config.SPARK_USD_PER_M_IN + 8_000 * config.SPARK_USD_PER_M_OUT) / 1e6)


# ---------------------------------------------------------------- breakdown

def fake_evidence():
    ledger = [CostEntry("spark", "muse-spark-1.2", 0.02, 4000, 3000, 2000),
              CostEntry("voice_transcribe", "a.wav", 0.001, units=21.7),
              CostEntry("sam", "person", 0.04, units=200),
              CostEntry("gemini", "gemini-2.5-pro", 0.01, 3000, 700)]
    sig = SimpleNamespace(modal=SimpleNamespace(server_seconds=60.0, gpu="NVIDIA L4"), cost_usd=0.0133)
    return SimpleNamespace(ledger=ledger, signals=sig)


def build(**kw):
    base = dict(vision_usage={"model": "muse-spark-1.3", "input_tokens": 23000, "output_tokens": 3000, "reasoning_tokens": 1500, "cost_usd": 0.0415},
                scoring_usage={"model": "muse-spark-1.3", "input_tokens": 22000, "output_tokens": 18000, "reasoning_tokens": 12000, "cost_usd": 0.104, "calls": 2},
                gemini_audio_usd=0.0143, gemini_audio_tokens=(4500, 860), evidence=fake_evidence(), source_type="upload",
                acr_called=True, acr_matched=False, trends_called=True, total_usd=0.3)
    base.update(kw)
    return cb.build(**base)


def test_breakdown_lists_every_part_with_tokens_including_reasoning():
    b = build()
    assert b["llm"]["scoring"] == {"model": "muse-spark-1.3", "tokens_in": 22000, "tokens_out": 18000, "reasoning_tokens": 12000, "cost_usd": 0.104, "calls": 2}
    assert b["llm"]["vision"]["reasoning_tokens"] == 1500 and b["llm"]["chat"]["calls"] == 0 and "ARIA" in b["llm"]["chat"]["note"]
    assert b["spark_1_2_audio"]["tokens_out"] == 3000 and b["spark_1_2_audio"]["reasoning_tokens"] == 2000
    assert b["voice_transcribe"]["audio_seconds"] == 21.7 and b["sam_3_1"]["frames"] == 200
    assert b["gemini"]["audio"]["cost_usd"] == 0.0143 and b["gemini"]["evidence_calls"]["calls"] == 1
    assert b["gemini"]["trends"]["cost_usd"] is None
    assert b["wonder_used"] is False and b["total_usd"] == 0.3


def test_modal_is_marked_as_an_estimate_with_gpu_seconds_and_a_rate():
    m = build()["modal_gpu"]
    assert m["estimate"] is True and m["gpu_seconds"] == 60.0 and m["gpu"] == "NVIDIA L4" and m["rate_usd_per_s"] == pytest.approx(0.000222, abs=1e-6)


def test_modal_rate_is_configurable(monkeypatch):
    from app.studio.stages.signals import gpu_price
    assert gpu_price("NVIDIA L4") == config.MODAL_GPU_USD_PER_S["L4"]
    monkeypatch.setenv("MODAL_USD_PER_GPU_SECOND", "0.001")
    assert gpu_price("NVIDIA L4") == 0.001


def test_acrcloud_and_apify_are_per_call_flags():
    b = build(source_type="link", apify_usd=0.0027, acr_matched=True)
    assert b["acrcloud"] == {"called": True, "matched": True, "cost_usd": None, "note": "per-call fee, not metered here"}
    assert b["apify"] == {"called": True, "cost_usd": 0.0027}
    assert build()["apify"]["called"] is False


def test_wonder_use_is_flagged():
    b = build(scoring_usage={"model": "muse-wonder-1.0", "input_tokens": 1, "output_tokens": 1, "reasoning_tokens": 0, "cost_usd": 0.0})
    assert b["wonder_used"] is True


def test_the_log_summary_is_one_line():
    line = cb.summary_line(build())
    assert line.startswith("[JobCost] total=$0.3000") and "\n" not in line and "(estimate)" in line and "12000reasoning" in line


# ---------------------------------------------------------------- visibility

@pytest.fixture
def settings(monkeypatch):
    from app.core.config import settings as s
    monkeypatch.setattr(s, "FOUNDER_EMAIL", "founder@example.org")
    monkeypatch.setattr(s, "STUDIO_EXPOSE_COST", False)
    return s


def test_only_the_founder_or_local_mode_may_see_the_cost(settings, monkeypatch):
    assert cost_visible("founder@example.org") and cost_visible(" Founder@Example.org ")
    assert not cost_visible("someone@example.org") and not cost_visible(None)
    monkeypatch.setattr(settings, "STUDIO_EXPOSE_COST", True)
    assert cost_visible("someone@example.org")


def test_strip_cost_removes_it_for_everyone_else_and_always_for_public_links(settings, monkeypatch):
    assert "_cost" not in strip_cost({"_cost": {"x": 1}, "a": 1}, "someone@example.org")
    assert "_cost" in strip_cost({"_cost": {"x": 1}}, "founder@example.org")
    assert "_cost" not in strip_cost({"_cost": {"x": 1}}, "founder@example.org", public=True)
    monkeypatch.setattr(settings, "STUDIO_EXPOSE_COST", True)
    assert "_cost" not in strip_cost({"_cost": {"x": 1}}, None, public=True)


def test_the_cost_endpoint_and_the_share_endpoint_are_wired_to_the_gate():
    import inspect
    from app.routers import prysm
    src = inspect.getsource(prysm)
    assert "cost_visible(current_user.email)" in src and "strip_cost(raw, public=True)" in src and "strip_cost(raw, current_user.email)" in src
