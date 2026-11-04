"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests (no network) for the Muse Spark shim that stands in for the anthropic SDK, the tolerant JSON extraction,
the progress helpers and the switch that keeps the anthropic package out of meta mode.
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.studio import meta_llm_shim as shim
from app.studio.json_tolerant import extract_json_object
from app.studio.meta_client import MetaError
from app.studio.progress import Progress, StageGroup, tracked

B64 = "QUJD"


def img(data=B64):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


# ---------------------------------------------------------------- request translation

def test_text_and_base64_image_blocks_become_openai_blocks():
    out = shim.build_messages(None, [{"role": "user", "content": [{"type": "text", "text": "Frame at 1s"}, img()]}])
    assert out == [{"role": "user", "content": [
        {"type": "text", "text": "Frame at 1s"},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{B64}"}}]}]


def test_text_only_blocks_collapse_to_a_plain_string_and_system_becomes_a_system_message():
    out = shim.build_messages("Be brief.", [{"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]},
                                            {"role": "assistant", "content": "ok"}, {"role": "user", "content": "more"}])
    assert out[0] == {"role": "system", "content": "Be brief."}
    assert out[1] == {"role": "user", "content": "a\nb"} and out[2]["content"] == "ok" and out[3]["content"] == "more"


def test_unsupported_blocks_fail_clearly():
    with pytest.raises(shim.APIStatusError):
        shim.build_messages(None, [{"role": "user", "content": [{"type": "tool_use", "id": "x"}]}])


def test_images_above_the_cap_are_thinned_evenly_keeping_first_and_last(monkeypatch):
    monkeypatch.setenv("META_MAX_IMAGES_PER_REQUEST", "4")
    content = []
    for i in range(10):
        content += [{"type": "text", "text": f"=== FRAME AT {i}s ==="}, img(f"d{i}")]
    out = shim.build_messages(None, [{"role": "user", "content": content}])[0]["content"]
    urls = [b["image_url"]["url"] for b in out if b["type"] == "image_url"]
    assert len(urls) == 4 and urls[0].endswith("d0") and urls[-1].endswith("d9")
    captions = [b["text"] for b in out if b["type"] == "text"]
    assert len(captions) == 4  # the caption of a dropped frame goes with it


def test_the_18_frames_the_pipeline_sends_are_not_thinned():
    content = []
    for i in range(18):
        content += [{"type": "text", "text": f"=== FRAME AT {i}s ==="}, img(f"d{i}")]
    out = shim.build_messages(None, [{"role": "user", "content": content}])[0]["content"]
    assert sum(b["type"] == "image_url" for b in out) == 18


def test_max_tokens_gets_reasoning_headroom_and_effort_is_optional(monkeypatch):
    monkeypatch.delenv("META_REASONING_HEADROOM", raising=False)
    monkeypatch.delenv("META_REASONING_EFFORT", raising=False)
    body = shim.build_body("muse-spark-1.3", 7000, [{"role": "user", "content": "x"}], None)
    assert body["max_tokens"] == 7000 + shim.GROUP_DEFAULT_HEADROOM["scoring"] and "reasoning_effort" not in body
    monkeypatch.setenv("META_REASONING_EFFORT", "minimal")
    assert shim.build_body("m", 100, [{"role": "user", "content": "x"}], None)["reasoning_effort"] == "minimal"


def test_streamed_chat_defaults_to_low_reasoning_effort(monkeypatch):
    monkeypatch.delenv("META_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("META_CHAT_REASONING_EFFORT", raising=False)
    body = shim.build_body("m", 1024, [{"role": "user", "content": "x"}], "sys", stream=True)
    assert body["reasoning_effort"] == "low" and body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["messages"][0]["role"] == "system"


def test_every_claude_name_maps_to_spark_unless_overridden(monkeypatch):
    monkeypatch.delenv("META_MODEL", raising=False)
    monkeypatch.delenv("META_MODEL_MAP", raising=False)
    assert shim.meta_model_for("claude-sonnet-4-6") == "muse-spark-1.3" == shim.meta_model_for("claude-opus-4-6")
    monkeypatch.setenv("META_MODEL_MAP", json.dumps({"claude-opus-4-6": "muse-spark-1.2"}))
    assert shim.meta_model_for("claude-opus-4-6") == "muse-spark-1.2" and shim.meta_model_for("claude-sonnet-4-6") == "muse-spark-1.3"
    assert shim.meta_model_for("muse-spark-1.2") == "muse-spark-1.2"


# ---------------------------------------------------------------- responses and errors

def fake_message(text, finish="end_turn", tin=10, tout=20):
    return shim.Message(content=[shim.TextBlock(text)], usage=shim.Usage(tin, tout), model="muse-spark-1.3", stop_reason=finish)


def test_create_returns_text_usage_and_retries_once_when_reasoning_ate_the_budget(monkeypatch):
    monkeypatch.delenv("META_REASONING_HEADROOM", raising=False)
    monkeypatch.delenv("META_SCORING_HEADROOM", raising=False)
    client = shim.AsyncAnthropic()
    calls = []

    def post(body):
        calls.append(body["max_tokens"])
        return fake_message("", "max_tokens") if len(calls) == 1 else fake_message("hello")

    monkeypatch.setattr(client, "_post", post)
    reply = asyncio.run(client.messages.create(model="claude-sonnet-4-6", max_tokens=100, messages=[{"role": "user", "content": "x"}]))
    assert reply.content[0].text == "hello" and reply.usage.input_tokens == 20 and reply.usage.output_tokens == 40  # the wasted attempt is billed too
    assert calls == [100 + 10000, 100 + 20000]


@pytest.mark.parametrize("status,timeout,cls", [(429, False, shim.RateLimitError), (None, True, shim.APITimeoutError),
                                                (503, False, shim.APIConnectionError), (400, False, shim.APIStatusError)])
def test_errors_map_to_the_classes_the_retry_helper_catches(monkeypatch, status, timeout, cls):
    client = shim.AsyncAnthropic()

    def post(body):
        raise MetaError("boom", status=status, timeout=timeout)

    monkeypatch.setattr(client, "_post", post)
    with pytest.raises(cls):
        asyncio.run(client.messages.create(model="x", max_tokens=10, messages=[{"role": "user", "content": "x"}]))


def test_the_exception_hierarchy_matches_the_sdk():
    assert issubclass(shim.RateLimitError, shim.APIStatusError) and issubclass(shim.APITimeoutError, shim.APIConnectionError)
    assert shim.APIStatusError("x", 529).status_code == 529


def test_the_anthropic_package_is_not_imported_in_meta_mode():
    code = ("import os; os.environ['AI_PROVIDER']='meta'; import sys\n"
            "from app.studio.llm_switch import anthropic\n"
            "import app.services.prysm_analyse_service, app.services.ai_service, app.services.chatbot_service, app.services.vision_service\n"
            "print(anthropic.__name__, 'anthropic' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[3]))
    assert out.stdout.strip().endswith("app.studio.meta_llm_shim False"), out.stderr[-400:]


# ---------------------------------------------------------------- tolerant JSON

@pytest.mark.parametrize("raw", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    'Here is the JSON you asked for:\n```json\n{"a": 1}\n```\nHope that helps!',
    '{"a": 1}\n\nLet me know if you want changes.',
])
def test_json_is_found_inside_fences_and_surrounding_text(raw):
    assert json.loads(extract_json_object(raw)) == {"a": 1}


def test_braces_inside_strings_do_not_end_the_object_early():
    raw = 'Sure: {"text": "a } b { c", "n": [1, {"x": 2}]} trailing'
    assert json.loads(extract_json_object(raw)) == {"text": "a } b { c", "n": [1, {"x": 2}]}


def test_an_unclosed_object_is_returned_from_the_first_brace_for_the_repair_step():
    out = extract_json_object('Result: {"a": 1, "b": [1, 2')
    assert out.startswith('{"a": 1') and '"b": [1, 2' in out
    from json_repair import repair_json
    assert json.loads(repair_json(out))["a"] == 1


# ---------------------------------------------------------------- progress

class FakeProgress:
    def __init__(self):
        self.events = []

    def __call__(self, stage, status):
        self.events.append((stage, status))


def test_tracked_reports_running_then_ok_or_skipped_or_error():
    async def ok():
        return {"x": 1}

    async def empty():
        return {}

    async def boom():
        raise ValueError("x")

    cb = FakeProgress()
    asyncio.run(tracked(cb, "vision", ok()))
    asyncio.run(tracked(cb, "audio", empty()))
    with pytest.raises(ValueError):
        asyncio.run(tracked(cb, "speech", boom()))
    assert cb.events == [("vision", "running"), ("vision", "ok"), ("audio", "running"), ("audio", "skipped"),
                         ("speech", "running"), ("speech", "error")]


def test_a_stage_group_is_ok_only_when_the_last_member_finishes():
    async def work():
        return 1

    async def main(cb):
        g = StageGroup(cb, "signals")
        await asyncio.gather(g.run(work()), g.run(work()), g.run(work()))

    cb = FakeProgress()
    asyncio.run(main(cb))
    assert cb.events[-1] == ("signals", "ok") and cb.events.count(("signals", "ok")) == 1


def test_progress_collects_stage_state_and_never_breaks_without_a_loop():
    p = Progress(None)
    p("frames", "running")
    p("frames", "ok")
    assert p.state == {"frames": "ok"}
    Progress("job")("x", "running")  # no running loop and a job id: dropped silently, not raised


# ---------------------------------------------------------------- baseline hooks

def test_the_safety_limits_come_from_the_settings():
    from types import SimpleNamespace
    from app.studio.safety import limits_from_settings
    s = SimpleNamespace(STUDIO_DAILY_SPEND_CAP_USD=3.5, STUDIO_USER_MAX_CONCURRENT=2, STUDIO_USER_HOURLY_LIMIT=7,
                        STUDIO_USER_DAILY_LIMIT=9, STUDIO_REGISTER_PER_IP_HOURLY=4, STUDIO_MAX_CONCURRENT=3)
    lim = limits_from_settings(s)
    assert (lim.daily_spend_cap_usd, lim.user_max_concurrent, lim.user_hourly_limit, lim.user_daily_limit, lim.register_per_ip_hourly, lim.max_concurrent) == (3.5, 2, 7, 9, 4, 3)


def test_the_baseline_cost_tracker_prices_spark_tokens():
    from app.services.prysm_analyse_service import CostTracker, PRICING
    t = CostTracker()
    t.add_claude("muse-spark-1.3", 1_000_000, 1_000_000, "x")
    assert t.total_usd == pytest.approx(PRICING["muse-spark-1.3"]["input"] + PRICING["muse-spark-1.3"]["output"])


def test_defaults_keep_the_baseline_behaviour():
    from app.core.config import Settings
    f = Settings.model_fields
    assert f["AI_PROVIDER"].default == "" and f["STUDIO_UNLIMITED"].default is False and f["DEMO_AUTO_VERIFY"].default is False
    assert f["AUTO_EDIT_ENABLED"].default is True and f["STUDIO_ALLOW_LINKS"].default is False
