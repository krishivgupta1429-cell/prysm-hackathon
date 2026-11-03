"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests for the Predicted Viewer Behaviour backends: the ENGAGEMENT_BACKEND dispatch, the available=false path
(never invented 40/55 numbers), the response shape, the Q-Align band, the Qwen reply parser and the scoring-evidence flag.
Nothing here reaches Modal: the Modal caller is replaced by a fake.
"""
import asyncio
import importlib.util
from pathlib import Path

import pytest

from app.services import engagement_service as svc
from app.studio import engagement_modal as em

FRAME = {"timestamp": 0.5, "base64": "/9j/4AAQ"}  # decodes; the content never reaches a model in these tests
RESULT_KEYS = {"available", "nawp", "ecr", "hook_risk", "retention_risk", "source", "interpretation", "qalign"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(monkeypatch):
    def set_env(**kw):
        values = {k.upper(): v for k, v in kw.items()}
        monkeypatch.setattr(em.config, "get_env", lambda name: values.get(name))
    return set_env


def fake_caller(qwen=None, qalign=None, qwen_exc=None, qalign_exc=None):
    def call(cls, method, **kwargs):
        if cls == em.QWEN_CLASS:
            if qwen_exc:
                raise qwen_exc
            return qwen
        if qalign_exc:
            raise qalign_exc
        return qalign
    return call


QWEN_OK = {"nawp": 52.0, "ecr": 71.0, "raw": "{}", "cold": True, "gpu": "NVIDIA L4", "gpu_seconds": 40.0}
QA_OK = {"quality": 3.8, "aesthetic": 2.9, "cold": True, "gpu": "NVIDIA L4", "gpu_seconds": 30.0}


# ---------------------------------------------------------------- dispatch

def test_default_is_modal_with_credentials_else_heuristic(env, monkeypatch):
    env()
    monkeypatch.setattr(em, "modal_credentials_exist", lambda: True)
    assert em.backend_choice() == "modal"
    monkeypatch.setattr(em, "modal_credentials_exist", lambda: False)
    assert em.backend_choice() == "heuristic"


@pytest.mark.parametrize("value", ["modal", "runpod", "heuristic"])
def test_explicit_choice_wins(env, monkeypatch, value):
    monkeypatch.setattr(em, "modal_credentials_exist", lambda: value != "heuristic")
    env(engagement_backend=value.upper())
    assert em.backend_choice() == value


def test_unknown_choice_falls_back_to_default(env, monkeypatch):
    monkeypatch.setattr(em, "modal_credentials_exist", lambda: False)
    env(engagement_backend="banana")
    assert em.backend_choice() == "heuristic"


def test_heuristic_backend_never_calls_modal(env, monkeypatch):
    env(engagement_backend="heuristic")
    monkeypatch.setattr(em, "predict", lambda *a, **k: pytest.fail("Modal must not be called"))
    out = run(svc.predict_engagement([FRAME], None, {"overallVisualScore": 70}, {"hook_energy": 0.6}, {"avg_score": 6}, "Fashion"))
    assert out["source"] == "heuristic" and out["available"] is True
    assert RESULT_KEYS <= set(out)


def test_modal_backend_shapes_the_result_and_keeps_the_dict_contract(env, monkeypatch):
    env(engagement_backend="modal")
    monkeypatch.setattr(em, "load_frames", lambda f: [{"timestamp": 0.5, "jpg": b"x"}])
    monkeypatch.setattr(em, "_remote", fake_caller(QWEN_OK, QA_OK))
    monkeypatch.setattr(em, "predict", lambda fr, n, d, _p=em.predict: _p(fr, n, d, caller=fake_caller(QWEN_OK, QA_OK)))
    out = run(svc.predict_engagement([FRAME], None, None, None, None, "Fashion", 30.0))
    assert RESULT_KEYS <= set(out)
    assert out["available"] is True and out["source"] == "qwen_zero_shot"
    assert (out["nawp"], out["ecr"]) == (52.0, 71.0)
    assert out["hook_risk"] == "low" and out["retention_risk"] == "medium"
    assert out["qalign"] == {"quality": 3.8, "aesthetic": 2.9, "band": "Good", "scale": [1, 5], "source": "q-align (one-align)"}
    assert out["_modal_cost_usd"] == pytest.approx(70.0 * em.DEFAULT_USD_PER_GPU_SECOND)


# ---------------------------------------------------------------- available=false, never 40/55

def assert_unavailable(out, source="qwen_zero_shot"):
    assert out["available"] is False and out["reason"]
    assert out["source"] == source
    assert out["nawp"] is None and out["ecr"] is None and out["qalign"] is None
    assert out["hook_risk"] is None and out["retention_risk"] is None


def test_modal_failure_is_unavailable_not_invented(env, monkeypatch):
    env(engagement_backend="modal")
    monkeypatch.setattr(em, "load_frames", lambda f: [{"timestamp": 0.5, "jpg": b"x"}])
    monkeypatch.setattr(em, "predict", lambda fr, n, d, _p=em.predict: _p(
        fr, n, d, caller=fake_caller(qwen_exc=RuntimeError("GPU out of memory"), qalign=QA_OK)))
    out = run(svc.predict_engagement([FRAME], None, None, None, None, "Fashion"))
    assert_unavailable(out)
    assert "out of memory" in out["reason"]
    assert 40.0 not in (out["nawp"], out["ecr"]) and 55.0 not in (out["nawp"], out["ecr"])


def test_unexpected_exception_in_client_is_unavailable(env, monkeypatch):
    env(engagement_backend="modal")

    def boom(*a, **k):
        raise ConnectionError("secret-looking details must not leak")

    monkeypatch.setattr(em, "predict", boom)
    out = run(svc.predict_engagement([FRAME], None, None, None, None, "Fashion"))
    assert_unavailable(out)
    assert "secret-looking" not in out["reason"] and "ConnectionError" in out["reason"]


def test_no_frames_is_unavailable(env, monkeypatch):
    env(engagement_backend="modal")
    out = run(svc.predict_engagement([], None, None, None, None, "Fashion"))
    assert_unavailable(out)


def test_qalign_failure_only_drops_qalign(monkeypatch):
    monkeypatch.setattr(em, "load_frames", lambda f: [{"timestamp": 0.5, "jpg": b"x"}])
    res = em.predict([FRAME], "Fashion", 30, caller=fake_caller(QWEN_OK, qalign_exc=RuntimeError("no gpu")))
    assert res["qalign"] is None and res["call"]["qalign_error"] and (res["nawp"], res["ecr"]) == (52.0, 71.0)


def test_heuristic_with_no_signals_is_unavailable_not_45_55(env):
    env(engagement_backend="heuristic")
    out = run(svc.predict_engagement([FRAME], None, None, None, None, "Fashion"))
    assert_unavailable(out, source="heuristic")


def test_context_is_empty_when_unavailable():
    assert svc.build_engagement_context(svc._unavailable("qwen_zero_shot", "x")) == ""


# ---------------------------------------------------------------- scoring evidence

def test_qwen_zero_shot_stays_out_of_scoring_by_default(env):
    res = svc._build_result(30.0, 40.0, "qwen_zero_shot")
    res["qalign"] = {"quality": 3.8, "aesthetic": 2.9, "band": "Good"}
    env()
    assert svc.build_engagement_context(res) == ""
    env(engagement_qalign_evidence="1")  # the Q-Align flag works on its own; the zero-shot ECR/NAWP is still not handed over
    ctx = svc.build_engagement_context(res)
    assert "Q-Align" in ctx and "ECR" not in ctx and "NAWP" not in ctx


def test_qwen_zero_shot_context_does_not_invite_quoting_percentages(env):
    env(engagement_zeroshot_evidence="1")
    res = svc._build_result(30.0, 40.0, "qwen_zero_shot")
    ctx = svc.build_engagement_context(res)
    assert "Do NOT quote the specific percentages" in ctx and "not trained on watch data" in ctx


def test_qalign_in_scoring_only_behind_flag(env):
    res = svc._build_result(50.0, 60.0, "qwen_zero_shot")
    res["qalign"] = {"quality": 3.8, "aesthetic": 2.9, "band": "Good"}
    env(engagement_zeroshot_evidence="1")
    assert "Q-Align" not in svc.build_engagement_context(res)
    env(engagement_zeroshot_evidence="1", engagement_qalign_evidence="1")
    ctx = svc.build_engagement_context(res)
    assert "Q-Align" in ctx and "3.8/5" in ctx and "%" not in ctx.split("VISUAL QUALITY SIGNAL")[1]


# ---------------------------------------------------------------- bands, frames, cost

@pytest.mark.parametrize("score,band", [(1.0, "Bad"), (1.9, "Poor"), (2.5, "Fair"), (3.49, "Fair"), (3.5, "Good"), (4.4, "Good"), (4.5, "Excellent"), (5.0, "Excellent")])
def test_qalign_band(score, band):
    assert em.qalign_band(score) == band


def test_cost_rate_is_configurable(env):
    env(engagement_usd_per_gpu_second="0.001")
    assert em.usd_per_gpu_second() == 0.001
    env(engagement_usd_per_gpu_second="oops")
    assert em.usd_per_gpu_second() == em.DEFAULT_USD_PER_GPU_SECOND


def test_load_frames_caps_at_eight_and_skips_unreadable(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"jpgdata")
    frames = [{"timestamp": i, "path": str(p)} for i in range(12)] + [{"timestamp": 99}]
    assert len(em.load_frames(frames)) == em.MAX_FRAMES
    assert em.load_frames([{"timestamp": 1}, {"timestamp": 2, "path": "/nonexistent"}]) == []


# ---------------------------------------------------------------- the Modal app's reply parser (pure function)

def _app_module():
    spec = importlib.util.spec_from_file_location("engagement_modal_app", Path(em.__file__).with_name("engagement_modal_app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_response_accepts_json_and_rejects_everything_else():
    pytest.importorskip("modal")
    app = _app_module()
    assert app.parse_response('```json\n{"nawp": 42.5, "ecr": 61}\n```') == (42.5, 61.0)
    for bad in ["I cannot tell", '{"nawp": 40}', '{"nawp": "x", "ecr": 5}', '{"nawp": 0, "ecr": 50}', '{"nawp": 140, "ecr": 50}']:
        with pytest.raises(ValueError):
            app.parse_response(bad)


def test_cost_rate_follows_the_gpu_that_ran(env):
    env()
    assert em.usd_per_gpu_second("NVIDIA A10") == em.GPU_USD_PER_SECOND["A10"] > em.usd_per_gpu_second("NVIDIA L4")
    assert em.usd_per_gpu_second("NVIDIA T4") < em.usd_per_gpu_second("NVIDIA L4")
    assert em.usd_per_gpu_second(None) == em.DEFAULT_USD_PER_GPU_SECOND
