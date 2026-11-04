"""Client for the DINOv2 Modal service (modal_app.py).

    embed_jpegs(jpegs, usage=None) -> (n, 768) float32 array, rows L2-normalised   (raises DinoUnavailable)

Settings (backend/.env or the process environment; names only here, values never printed or logged):
    DINO_BACKEND     modal | off      unset: modal when DINO_MODAL_URL and DINO_MODAL_TOKEN are set, else off
    DINO_MODAL_URL   base URL printed by `modal deploy` (GET /health, POST /embed)
    DINO_MODAL_TOKEN bearer token (the Modal secret prysm-hackathon-dino-token holds the same value)

Never invents numbers: off, a bad reply, an HTTP error or a timeout all raise DinoUnavailable(reason) and the caller
reports available=false.
"""
import base64
import logging
import os
import time

import numpy as np

from app.studio import config

logger = logging.getLogger(__name__)

DIM = 768
CHUNK = 48  # frames per request (the service accepts up to 64)
CALL_TIMEOUT_S = float(os.environ.get("DINO_CALL_TIMEOUT_S", "420"))  # includes a possible cold start
# APPROXIMATE Modal prices per GPU second (`modal billing rates`, $/hour / 3600); DINO_USD_PER_GPU_SECOND overrides.
GPU_USD_PER_SECOND = {"L4": 0.000222, "A10": 0.000306}  # Modal reports an A10G as "NVIDIA A10"
DEFAULT_USD_PER_GPU_SECOND = GPU_USD_PER_SECOND["L4"]


class DinoUnavailable(Exception):
    """The embeddings could not be computed. The message is the reason shown to the user (no secrets in it)."""


def backend_choice() -> str:
    raw = (config.get_env("DINO_BACKEND") or "").strip().lower()
    if raw in ("modal", "off"):
        return raw
    return "modal" if (config.get_env("DINO_MODAL_URL") and config.get_env("DINO_MODAL_TOKEN")) else "off"


def usd_per_gpu_second(gpu_name: str | None = None) -> float:
    raw = config.get_env("DINO_USD_PER_GPU_SECOND")
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


def _post(url: str, token: str, payload: dict, timeout: float) -> dict:
    import httpx
    r = httpx.post(url.rstrip("/") + "/embed", json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        raise DinoUnavailable(f"the DINOv2 service answered HTTP {r.status_code} with a non-JSON body")
    if r.status_code != 200 or not body.get("available"):
        raise DinoUnavailable(f"the DINOv2 service answered HTTP {r.status_code}: {str(body.get('error') or 'no reason given')[:200]}")
    return body


def health(timeout: float = 20.0) -> dict:
    """GET /health. {"ok": False, "reason"} when off, unreachable or unhappy: never raises."""
    if backend_choice() != "modal":
        return {"ok": False, "reason": "DINO_BACKEND is off"}
    url = config.get_env("DINO_MODAL_URL")
    if not url:
        return {"ok": False, "reason": "DINO_MODAL_URL is not set"}
    try:
        import httpx
        r = httpx.get(url.rstrip("/") + "/health", timeout=timeout)
        return r.json() if r.status_code == 200 else {"ok": False, "reason": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "reason": config.redact(f"{type(e).__name__}: {e}")[:200]}


def embed_jpegs(jpegs: list[bytes], usage: dict | None = None, post=_post) -> np.ndarray:
    """Embeddings of JPEG frames, (n, 768). `usage` (optional dict) collects calls, frames, latency, cold, GPU seconds and
    the estimated cost. `post(url, token, payload, timeout)` is replaceable for tests."""
    if backend_choice() != "modal":
        raise DinoUnavailable("DINOv2 is off (DINO_BACKEND=off or no DINO_MODAL_URL / DINO_MODAL_TOKEN)")
    url, token = config.get_env("DINO_MODAL_URL"), config.get_env("DINO_MODAL_TOKEN")
    if not url or not token:
        raise DinoUnavailable("DINO_MODAL_URL and DINO_MODAL_TOKEN must both be set")
    if not jpegs:
        raise DinoUnavailable("no frames to embed")
    rows, u = [], (usage if usage is not None else {})
    u.setdefault("calls", 0); u.setdefault("frames", 0); u.setdefault("gpu_seconds", 0.0); u.setdefault("est_cost_usd", 0.0)
    u.setdefault("latency_s", 0.0); u.setdefault("cold", False)
    for i in range(0, len(jpegs), CHUNK):
        chunk = jpegs[i:i + CHUNK]
        t0 = time.time()
        try:
            body = post(url, token, {"frames": [base64.b64encode(b).decode() for b in chunk]}, CALL_TIMEOUT_S)
        except DinoUnavailable:
            raise
        except Exception as e:
            raise DinoUnavailable(config.redact(f"could not reach the DINOv2 service: {type(e).__name__}: {e}").replace(token, "***")[:240])
        emb = np.asarray(body.get("embeddings"), dtype=np.float32)
        if emb.shape != (len(chunk), DIM) or not np.isfinite(emb).all():
            raise DinoUnavailable(f"the DINOv2 service returned an unexpected shape {tuple(emb.shape)}")
        norms = np.linalg.norm(emb, axis=1)
        if np.abs(norms - 1.0).max() > 1e-2:
            raise DinoUnavailable("the DINOv2 service returned vectors that are not unit length")
        rows.append(emb)
        gpu_s = float(body.get("gpu_seconds") or 0.0)
        u["calls"] += 1; u["frames"] += len(chunk); u["gpu_seconds"] = round(u["gpu_seconds"] + gpu_s, 3)
        u["est_cost_usd"] = round(u["est_cost_usd"] + gpu_s * usd_per_gpu_second(body.get("gpu")), 6)
        u["latency_s"] = round(u["latency_s"] + time.time() - t0, 3)
        u["cold"] = bool(u["cold"] or body.get("cold")); u["gpu"] = body.get("gpu")
    logger.info("[DINO] backend=modal calls=%d frames=%d latency=%.1fs gpu_seconds=%.1f est_cost=$%.5f (estimate)",
                u["calls"], u["frames"], u["latency_s"], u["gpu_seconds"], u["est_cost_usd"])
    return np.concatenate(rows)
