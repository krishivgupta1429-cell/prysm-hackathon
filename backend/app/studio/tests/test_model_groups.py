"""Built during the Meta Global AI Developer Hackathon (new work). Model, headroom and reasoning effort per call group."""
import pytest

from app.studio import meta_llm_shim as shim

IMG = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}}
TEXT = [{"role": "user", "content": "x"}]
VISION = [{"role": "user", "content": [{"type": "text", "text": "frame"}, IMG]}]

ENV = ["META_MODEL", "META_MODEL_MAP", "META_VISION_MODEL", "META_SCORING_MODEL", "META_CHAT_MODEL", "META_REASONING_HEADROOM",
       "META_VISION_HEADROOM", "META_SCORING_HEADROOM", "META_CHAT_HEADROOM", "META_REASONING_EFFORT", "META_VISION_REASONING_EFFORT",
       "META_SCORING_REASONING_EFFORT", "META_CHAT_REASONING_EFFORT"]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)


def test_calls_are_grouped_by_what_they_send():
    assert shim.call_group(VISION) == "vision" and shim.call_group(TEXT) == "scoring" and shim.call_group(TEXT, stream=True) == "chat"


def test_every_group_defaults_to_spark_13():
    assert {shim.meta_model_for("claude-sonnet-4-6", g) for g in shim.GROUPS} == {"muse-spark-1.3"}


def test_each_group_has_its_own_model_switch(monkeypatch):
    monkeypatch.setenv("META_SCORING_MODEL", "muse-wonder-1.0")
    assert shim.meta_model_for("claude-sonnet-4-6", "scoring") == "muse-wonder-1.0"
    assert shim.meta_model_for("claude-sonnet-4-6", "vision") == "muse-spark-1.3" == shim.meta_model_for("claude-sonnet-4-6", "chat")
    monkeypatch.setenv("META_VISION_MODEL", "muse-spark-1.2")
    monkeypatch.setenv("META_CHAT_MODEL", "muse-wonder-1.0")
    assert shim.meta_model_for("claude-sonnet-4-6", "vision") == "muse-spark-1.2"
    assert shim.meta_model_for("claude-sonnet-4-6", "chat") == "muse-wonder-1.0"


def test_an_explicit_muse_id_and_the_model_map_win_over_the_group_switch(monkeypatch):
    monkeypatch.setenv("META_SCORING_MODEL", "muse-wonder-1.0")
    assert shim.meta_model_for("muse-spark-1.2", "scoring") == "muse-spark-1.2"
    monkeypatch.setenv("META_MODEL_MAP", '{"claude-opus-4-6": "muse-spark-1.2"}')
    assert shim.meta_model_for("claude-opus-4-6", "scoring") == "muse-spark-1.2"


def test_the_body_carries_the_group_model_headroom_and_effort(monkeypatch):
    monkeypatch.setenv("META_SCORING_HEADROOM", "15000")
    monkeypatch.setenv("META_SCORING_REASONING_EFFORT", "medium")
    b = shim.build_body("muse-wonder-1.0", 7000, TEXT, None)
    assert b["model"] == "muse-wonder-1.0" and b["max_tokens"] == 22000 and b["reasoning_effort"] == "medium"
    v = shim.build_body("muse-spark-1.3", 4096, VISION, None)       # other groups keep their own values
    assert v["max_tokens"] == 4096 + shim.GROUP_DEFAULT_HEADROOM["vision"] and "reasoning_effort" not in v


def test_the_global_headroom_still_applies_to_every_group_without_its_own(monkeypatch):
    monkeypatch.setenv("META_REASONING_HEADROOM", "1234")
    assert [shim.reasoning_headroom(g) for g in shim.GROUPS] == [1234, 1234, 1234]
    monkeypatch.setenv("META_VISION_HEADROOM", "99")
    assert shim.reasoning_headroom("vision") == 99 and shim.reasoning_headroom("scoring") == 1234


def test_chat_effort_defaults_to_low_and_can_be_overridden(monkeypatch):
    assert shim.reasoning_effort("chat") == "low" and shim.reasoning_effort("scoring") is None
    monkeypatch.setenv("META_CHAT_REASONING_EFFORT", "minimal")
    assert shim.reasoning_effort("chat") == "minimal"


def test_the_create_call_uses_the_group_model(monkeypatch):
    import asyncio
    monkeypatch.setenv("META_SCORING_MODEL", "muse-wonder-1.0")
    client = shim.AsyncAnthropic()
    seen = []

    def post(body):
        seen.append(body["model"])
        return shim.Message(content=[shim.TextBlock("ok.")], usage=shim.Usage(1, 1), model=body["model"])

    monkeypatch.setattr(client, "_post", post)
    asyncio.run(client.messages.create(model="claude-sonnet-4-6", max_tokens=10, messages=TEXT))
    asyncio.run(client.messages.create(model="claude-sonnet-4-6", max_tokens=10, messages=VISION))
    assert seen == ["muse-wonder-1.0", "muse-spark-1.3"]


def test_wonder_is_priced_at_its_own_rate():
    from app.studio import config
    assert config.price_per_m("muse-wonder-1.0") == (config.WONDER_USD_PER_M_IN, config.WONDER_USD_PER_M_OUT)
    assert config.price_per_m("muse-spark-1.3") == (config.SPARK_USD_PER_M_IN, config.SPARK_USD_PER_M_OUT)
