"""Built during the Meta Global AI Developer Hackathon (new work). Existing models, new integration:
Qwen2.5-VL-7B-Instruct and Q-Align (one-align) are used unmodified through the Modal app in
engagement_modal_app.py; only this client is new.

Client for the "Predicted Viewer Behaviour" model and the Q-Align visual-quality signal.

    predict(frames, niche, duration)  ->  {"nawp", "ecr", "source", "qalign", "call"}  (raises EngagementUnavailable)

Never invents numbers: any failure raises EngagementUnavailable(reason) and the caller reports available=false.
The Q-Align call runs in parallel with the Qwen call and its failure only drops the qalign key.
Studio is standalone: this module imports nothing from app.services.
"""
import base64
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.studio import config

logger = logging.getLogger(__name__)

APP_NAME = "prysm-hackathon-engagement"
QWEN_CLASS = "QwenZeroShot"
QALIGN_CLASS = "QAlign"
MAX_FRAMES = 8
# Q-Align's native scale is 1 to 5 (bad, poor, fair, good, excellent); the band is the nearest level.
QALIGN_BANDS = ((1.5, "Bad"), (2.5, "Poor"), (3.5, "Fair"), (4.5, "Good"), (5.01, "Excellent"))
# APPROXIMATE Modal prices per GPU second (`modal billing rates`, $/hour / 3600); ENGAGEMENT_USD_PER_GPU_SECOND
# overrides them all. The rate follows the GPU the container reported (the app falls back L4 -> A10G / T4).
GPU_USD_PER_SECOND = {"L4": 0.000222, "A10G": 0.000306, "T4": 0.000164}
DEFAULT_USD_PER_GPU_SECOND = GPU_USD_PER_SECOND["L4"]
CALL_TIMEOUT_S = float(os.environ.get("ENGAGEMENT_CALL_TIMEOUT_S", "420"))  # includes a possible cold start


class EngagementUnavailable(Exception):
    """The prediction could not be made. The message is the reason shown to the user (no secrets in it)."""


def modal_credentials_exist() -> bool:
    """True if Modal can authenticate: the two token variables, or a ~/.modal.toml from `modal token new`."""
    if config.get_env("MODAL_TOKEN_ID") and config.get_env("MODAL_TOKEN_SECRET"):
        return True
    return (Path.home() / ".modal.toml").exists()


def backend_choice() -> str:
    """ENGAGEMENT_BACKEND = modal | runpod | heuristic. Unset: modal when Modal credentials exist, else heuristic."""
    raw = (config.get_env("ENGAGEMENT_BACKEND") or "").lower()
    if raw in ("modal", "runpod", "heuristic"):
        return raw
    return "modal" if modal_credentials_exist() else "heuristic"


def qalign_band(score: float) -> str:
    for upper, name in QALIGN_BANDS:
        if score < upper:
            return name
    return "Excellent"


def usd_per_gpu_second(gpu_name: str | None = None) -> float:
    for name in ("ENGAGEMENT_USD_PER_GPU_SECOND", "MODAL_USD_PER_GPU_SECOND"):
        raw = config.get_env(name)
        if raw:
            try:
                return float(raw)
            except ValueError:
                pass
    upper = (gpu_name or "").upper()
    for key, price in GPU_USD_PER_SECOND.items():
        if key in upper:
            return price
    return DEFAULT_USD_PER_GPU_SECOND


def qalign_for_scoring_enabled() -> bool:
    """Flag (default off): hand the Q-Align score to the scoring step as evidence."""
    return (config.get_env("ENGAGEMENT_QALIGN_EVIDENCE") or "").lower() in ("1", "true", "yes", "on")


def load_frames(frames: list) -> list:
    """The baseline frame dicts ({"timestamp", "path"} or {"timestamp", "base64"}) -> [{"timestamp", "jpg": bytes}], up to 8."""
    out = []
    for f in (frames or [])[:MAX_FRAMES]:
        try:
            path = f.get("path") or ""
            if path and os.path.exists(path):
                data = Path(path).read_bytes()
            elif f.get("base64"):
                data = base64.b64decode(f["base64"])
            else:
                continue
            out.append({"timestamp": float(f.get("timestamp", 0.0)), "jpg": data})
        except Exception:
            continue
    return out


def _remote(cls_name: str, method: str, **kwargs):
    import modal  # imported here so the rest of the backend works without the modal package
    cls = modal.Cls.from_name(APP_NAME, cls_name)
    return getattr(cls(), method).remote(**kwargs)


def _cost(res: dict | None) -> float:
    return round(float((res or {}).get("gpu_seconds") or 0.0) * usd_per_gpu_second((res or {}).get("gpu")), 6)


def predict(frames: list, niche: str, duration: float, caller=_remote) -> dict:
    """Run Qwen zero-shot and Q-Align on Modal. `caller(cls_name, method, **kwargs)` is replaceable for tests.

    Returns {"nawp", "ecr", "source": "qwen_zero_shot", "qalign": {...} | None, "call": {...}}.
    Raises EngagementUnavailable when the ECR/NAWP prediction fails.
    """
    t0 = time.time()
    loaded = load_frames(frames)
    if not loaded:
        raise EngagementUnavailable("no frames could be read for the prediction")
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_qwen = pool.submit(caller, QWEN_CLASS, "predict", frames=loaded, niche=niche, duration=float(duration))
        f_qa = pool.submit(caller, QALIGN_CLASS, "score", frames=loaded)
        qwen, qwen_err = None, None
        try:
            qwen = f_qwen.result(timeout=CALL_TIMEOUT_S)
        except Exception as e:
            qwen_err = config.redact(f"{type(e).__name__}: {e}")[:300]
        qa, qa_err = None, None
        try:
            qa = f_qa.result(timeout=CALL_TIMEOUT_S)
        except Exception as e:
            qa_err = config.redact(f"{type(e).__name__}: {e}")[:300]
    latency = round(time.time() - t0, 2)

    gpu_s = float((qwen or {}).get("gpu_seconds") or 0) + float((qa or {}).get("gpu_seconds") or 0)
    cost = round(_cost(qwen) + _cost(qa), 6)
    call = {"backend": "modal", "latency_s": latency, "cold": bool((qwen or {}).get("cold") or (qa or {}).get("cold")),
            "qwen_cold": (qwen or {}).get("cold"), "qalign_cold": (qa or {}).get("cold"),
            "gpu_seconds": round(gpu_s, 2), "est_cost_usd": cost, "gpu": (qwen or qa or {}).get("gpu"), "qalign_gpu": (qa or {}).get("gpu"),
            "qalign_error": qa_err, "qwen_error": qwen_err}
    logger.info("[Engagement] backend=modal latency=%.1fs %s gpu_seconds=%.1f est_cost=$%.4f (estimate, %s / %s) "
                "qwen=%s qalign=%s", latency, "cold" if call["cold"] else "warm", gpu_s, cost,
                (qwen or {}).get("gpu"), (qa or {}).get("gpu"),
                "ok" if qwen else "FAILED", "ok" if qa else "failed")
    if qwen is None:
        raise EngagementUnavailable(f"the prediction model did not answer ({qwen_err})")

    qalign = None
    if qa is not None:
        q, a = float(qa["quality"]), float(qa["aesthetic"])
        qalign = {"quality": round(q, 2), "aesthetic": round(a, 2), "band": qalign_band(q),
                  "scale": [1, 5], "source": "q-align (one-align)"}
    return {"nawp": float(qwen["nawp"]), "ecr": float(qwen["ecr"]), "source": "qwen_zero_shot", "qalign": qalign,
            "call": call, "raw": qwen.get("raw")}
