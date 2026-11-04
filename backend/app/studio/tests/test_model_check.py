"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests (no network) for the Meta model availability check: which model each call uses and where that came from, the fallback to a model the
key really has (never a -contributor id), the start-of-analysis check that fails in seconds with a friendly message, the start-up warning, raw
provider text never reaching a user, and a failed step being retried once (and never shown as skipped).
"""
import asyncio
import logging

import pytest

from app.core.config import settings
from app.studio import config, evidence, model_check as mc
from app.studio import meta_llm_shim as shim
from app.studio.meta_client import MetaError

run = asyncio.run
ALL = {"muse-spark-1.1", "muse-spark-1.2", "muse-spark-1.3", "muse-spark-1.3-contributor", "muse-wonder-1.0", "sam-3.1"}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    mc.reset_for_tests()
    monkeypatch.setattr(config, "get_env", lambda name: None)
    monkeypatch.setattr(settings, "AI_PROVIDER", "meta")
    yield
    mc.reset_for_tests()


def env(monkeypatch, **values):
    monkeypatch.setattr(config, "get_env", lambda name: values.get(name))


def available(monkeypatch, ids, probe_ok=False):
    calls = {"list": 0, "probe": []}

    def fetch():
        calls["list"] += 1
        return None if ids is None else set(ids)

    def probe(model):
        calls["probe"].append(model)
        return probe_ok
    monkeypatch.setattr(mc, "fetch_available", fetch)
    monkeypatch.setattr(mc, "probe", probe)
    return calls


# ------------------------------------------------------------------ which model each call uses

def test_every_group_uses_muse_spark_1_3_by_default_and_says_where_it_came_from():
    assert mc.configured_models() == {g: ("muse-spark-1.3", "the built-in default") for g in ("vision", "scoring", "chat")}


def test_the_env_overrides_still_work(monkeypatch):
    env(monkeypatch, META_MODEL="muse-spark-1.2", META_VISION_MODEL="muse-spark-1.1")
    got = mc.configured_models()
    assert got["vision"] == ("muse-spark-1.1", "META_VISION_MODEL") and got["scoring"] == ("muse-spark-1.2", "META_MODEL")
    env(monkeypatch, META_MODEL_MAP='{"claude-sonnet-4-6": "muse-spark-1.2"}', META_CHAT_MODEL="muse-spark-1.1")
    assert shim.meta_model_for("claude-sonnet-4-6", "chat") == "muse-spark-1.2"           # the map wins over the group variable
    assert shim.meta_model_for("muse-spark-1.1", "scoring") == "muse-spark-1.1"            # an explicit id is kept


# ------------------------------------------------------------------ fallback

def test_a_missing_model_falls_back_to_the_newest_plain_spark_the_key_has(monkeypatch):
    available(monkeypatch, {"muse-spark-1.1", "muse-spark-1.2", "muse-spark-1.3-contributor"})
    run(mc.refresh())
    assert shim.meta_model_for("claude-sonnet-4-6", "vision") == "muse-spark-1.2"
    assert shim.meta_model_for("claude-sonnet-4-6", "scoring") == "muse-spark-1.2"


def test_a_contributor_id_is_never_used_as_a_fallback(monkeypatch):
    available(monkeypatch, {"muse-spark-1.3-contributor", "muse-spark-1.2-contributor"})
    assert run(mc.refresh()) != []
    assert shim.meta_model_for("claude-sonnet-4-6", "vision") == "muse-spark-1.3"          # unchanged: no plain id to switch to


def test_unknown_availability_changes_nothing(monkeypatch):
    available(monkeypatch, None)
    assert run(mc.refresh()) == [] and shim.meta_model_for("claude-sonnet-4-6", "vision") == "muse-spark-1.3"


# ------------------------------------------------------------------ the check at the start of an analysis

def test_all_models_present_passes_with_one_free_list_call_and_no_paid_probe(monkeypatch):
    calls = available(monkeypatch, ALL)
    run(mc.ensure_models()); run(mc.ensure_models())
    assert calls["list"] == 1 and calls["probe"] == []                                      # cached, and free


def test_a_required_model_that_cannot_be_used_fails_at_once_with_the_friendly_message(monkeypatch):
    calls = available(monkeypatch, {"muse-spark-1.3-contributor", "muse-wonder-1.0"})
    with pytest.raises(mc.AnalysisUnavailable) as e:
        run(mc.ensure_models())
    assert str(e.value) == "We couldn't start the analysis. Please try again in a few minutes."
    assert "muse-spark-1.3" in e.value.detail and "built-in default" in e.value.detail and "shim" not in str(e.value)
    assert calls["probe"] == ["muse-spark-1.3"] * 3                                         # one tiny confirming call per group


def test_the_name_that_set_the_bad_model_is_in_the_log_detail(monkeypatch):
    env(monkeypatch, META_SCORING_MODEL="muse-spark-9.9")
    available(monkeypatch, {"muse-wonder-1.0", "muse-spark-1.3-contributor"})
    with pytest.raises(mc.AnalysisUnavailable) as e:
        run(mc.ensure_models())
    assert "META_SCORING_MODEL" in e.value.detail and "muse-spark-9.9" in e.value.detail


def test_a_model_missing_from_the_list_but_callable_is_accepted(monkeypatch):
    calls = available(monkeypatch, {"muse-wonder-1.0"}, probe_ok=True)
    run(mc.ensure_models())
    assert calls["probe"] == ["muse-spark-1.3"]


def test_a_missing_model_with_a_fallback_does_not_refuse_the_analysis(monkeypatch):
    available(monkeypatch, {"muse-spark-1.2"})
    run(mc.ensure_models())


def test_an_unreadable_list_never_blocks(monkeypatch):
    available(monkeypatch, None)
    run(mc.ensure_models())


def test_the_check_is_off_without_the_meta_provider(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "")
    calls = available(monkeypatch, set())
    run(mc.ensure_models())
    assert calls["list"] == 0


def test_the_worker_runs_the_check_first_and_stores_a_friendly_error():
    import inspect
    from app.routers import prysm
    src = inspect.getsource(prysm._run_analysis_task)
    assert src.index("ensure_models()") < src.index("credit_checker")
    assert '"error": friendly_error(e)' in src


# ------------------------------------------------------------------ start-up warning

def test_startup_names_every_configured_model_the_key_cannot_use_and_never_the_key(monkeypatch, caplog):
    env(monkeypatch, META_API_KEY="sk-test-secret-123", META_VISION_MODEL="muse-spark-9.9")
    available(monkeypatch, {"muse-spark-1.2", "muse-wonder-1.0"})
    with caplog.at_level(logging.INFO, logger="app.studio.model_check"):
        run(mc.startup_check())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "muse-spark-9.9" in text and "META_VISION_MODEL" in text and "muse-spark-1.3" in text
    assert "muse-voice-transcribe-1.0" in text and "sk-test-secret-123" not in text


def test_startup_is_quiet_about_models_when_everything_is_available(monkeypatch, caplog):
    available(monkeypatch, ALL | {"muse-voice-transcribe-1.0"})
    with caplog.at_level(logging.INFO, logger="app.studio.model_check"):
        run(mc.startup_check())
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ------------------------------------------------------------------ raw provider text never reaches a user

@pytest.mark.parametrize("raw", ['shim: HTTP 404: {"error":{"code":"model_not_found"}}', "shim: HTTP 404: model_not_found",
                                 "Error code: 404 - model_not_found"])
def test_a_404_is_shown_as_the_friendly_start_message(raw):
    assert mc.friendly_error(shim.APIStatusError(raw, 404)) == mc.FRIENDLY_START
    assert mc.friendly_error(RuntimeError(raw)) == mc.FRIENDLY_START


def test_other_raw_provider_text_is_hidden_but_our_own_messages_are_kept():
    assert mc.friendly_error(shim.APIStatusError("shim: HTTP 400: invalid image", 400)) == mc.FRIENDLY_FAILED
    assert mc.friendly_error(RuntimeError("Prysm is busy right now. Please try again in a few minutes.")) == "Prysm is busy right now. Please try again in a few minutes."
    assert mc.friendly_error(mc.AnalysisUnavailable()) == mc.FRIENDLY_START
    for e in (shim.APIStatusError("shim: HTTP 404 model_not_found", 404), mc.AnalysisUnavailable(detail="shim: HTTP 404")):
        assert "shim" not in mc.friendly_error(e) and "404" not in mc.friendly_error(e)


# ------------------------------------------------------------------ a failed step is retried once, never shown as skipped

def test_vision_is_retried_once_and_then_fails_the_analysis_clearly():
    import inspect
    from app.services import prysm_analyse_service as svc
    src = inspect.getsource(svc.run_full_analysis)
    assert "_vision_with_one_retry" in src and "retrying once" in src
    assert "raise AnalysisUnavailable(FRIENDLY_START if is_model_error(_vf) else FRIENDLY_FAILED" in src


def test_an_evidence_step_that_errors_is_retried_once_but_a_timeout_is_not():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("blip")
        return "ok"
    assert run(evidence._guard("x", 5, flaky, [])) == "ok" and len(attempts) == 2
    notes, always = [], []

    def broken():
        always.append(1)
        raise RuntimeError("down")
    assert run(evidence._guard("x", 5, broken, notes)) is None and len(always) == 2 and notes
    import time
    slow = []
    assert run(evidence._guard("x", 0.05, lambda: (slow.append(1), time.sleep(0.3)), [])) is None and len(slow) == 1


def test_a_probe_only_calls_a_model_not_found_unavailable(monkeypatch):
    class Owner:
        def __init__(self, status): self.status = status
        def _post(self, body): raise MetaError("x", status=self.status)
    for status, expected in ((404, False), (403, False), (429, True), (500, True)):
        monkeypatch.setattr(shim, "AsyncAnthropic", lambda s=status: Owner(s))
        assert mc.probe("muse-spark-1.3") is expected


def test_the_progress_screen_never_calls_an_error_skipped():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[4] / "web/src/studio/progress.ts").read_text()
    assert "if (v === 'error') return 'failed'" in src
