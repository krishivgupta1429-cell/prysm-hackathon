"""Built during the Meta Global AI Developer Hackathon (new work). Existing models and services, new integration:
the models and vendors wrapped here existed before the hackathon; only this integration code is new.

Signals stage: embeddings from the Modal GPU app (DINOv2, V-JEPA 2, LanguageBind, EnCodec, CLAP, Demucs)
plus CPU signals computed locally (librosa audio features, FFmpeg scene cuts).

Embeddings are saved as .npy files in <run>/signals/ and never go into report.json, which keeps only
dims, L2 norms, status, seconds and an APPROXIMATE cost (GPU seconds x the Modal price in config.py).
Demucs stems are saved in <run>/signals/stems/. If Modal fails, the GPU models are recorded as "skipped"
with the reason and the CPU signals still run: this stage never raises.

With --signals-local, DINOv2, CLIP and CLAP run on the CPU instead (signals/local.py); the other GPU
models stay Modal-only.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from app.studio import config
from app.studio.report import DemucsResult, ModalCallInfo, ModelSignal, SignalsStage
from app.studio.signals import local
from app.studio.signals.features import audio_features, scene_cuts

MODAL_WANT = ["dinov2", "vjepa2", "languagebind", "encodec", "clap", "demucs"]
# (result key, array name holding the pooled vector, extra array name or None)
POOLED = {"dinov2": ("mean", "frames"), "vjepa2": ("embedding", None), "languagebind_video": ("embedding", None),
          "languagebind_audio": ("embedding", None), "encodec": ("embedding", None), "clap": ("embedding", None),
          "clip": ("embedding", None)}
EXPECTED_MODELS = ["dinov2", "vjepa2", "languagebind_video", "languagebind_audio", "encodec", "clap"]


def merge_languagebind(main: dict, lb: dict | None, lb_error: str | None, use_lb: bool) -> dict:
    """Combine the main class's result with the LanguageBind class's.

    The main call also computed CLIP ViT-L/14; it becomes "languagebind_video" (flagged as the stand-in) only if
    LanguageBind's video tower did not run. Container facts are merged: cold if either was cold, GPU seconds added.
    """
    models = dict(main["models"])
    clip = models.pop("clip", None)
    server_seconds = float(main["server_seconds"])
    container = dict(main["container"])
    container["cold_main"] = bool(container.get("cold"))
    container["cold_lb"] = None
    if use_lb:
        lbm = (lb or {}).get("models", {})
        video = lbm.get("languagebind_video")
        if video and video.get("status") == "ok":
            models["languagebind_video"] = video
        elif clip and clip.get("status") == "ok":
            why = (video or {}).get("error") or lb_error or "LanguageBind video tower unavailable"
            models["languagebind_video"] = {**clip, "meta": {**(clip.get("meta") or {}),
                                                              "stand_in": config.SIGNAL_MODEL_IDS["clip"],
                                                              "video_tower_error": why[:200]}}
        else:
            models["languagebind_video"] = video or {"status": "error", "seconds": 0.0,
                                                     "error": lb_error or (clip or {}).get("error") or "no video embedding"}
        models["languagebind_audio"] = lbm.get("languagebind_audio") or {
            "status": "error", "seconds": 0.0, "error": f"LanguageBind call failed: {lb_error}"}
        if lb:
            server_seconds += float(lb["server_seconds"])
            container["cold_lb"] = bool(lb["container"].get("cold"))
            container["cold"] = container["cold_main"] or container["cold_lb"]
    return {"container": container, "models": models, "stems": main.get("stems", {}), "server_seconds": round(server_seconds, 3)}


def call_modal(payload: dict) -> dict:
    """Call the deployed Modal classes (the Modal client reads its own credentials; nothing is passed here).

    The main class and the LanguageBind class run in parallel. A LanguageBind failure is tolerated (CLIP stands
    in for the video tower); a failure of the main call raises.
    """
    import modal
    use_lb = "languagebind" in payload["want"]
    main_payload = {**payload, "want": [w for w in payload["want"] if w != "languagebind"] + (["clip"] if use_lb else [])}

    def call(cls_name: str, **kwargs):
        return modal.Cls.from_name(config.MODAL_APP_NAME, cls_name)().run.remote(**kwargs)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_main = pool.submit(call, config.MODAL_CLASS_NAME, **main_payload)
        f_lb = pool.submit(call, config.MODAL_LB_CLASS_NAME, proxy=payload["proxy"], wav=payload["wav"]) if use_lb else None
        main = f_main.result()
        lb, lb_error = None, None
        if f_lb is not None:
            try:
                lb = f_lb.result()
            except Exception as e:
                lb_error = config.redact(f"{type(e).__name__}: {e}")[:300]
    return merge_languagebind(main, lb, lb_error, use_lb)


def decode_array(enc: dict) -> np.ndarray:
    return np.frombuffer(enc["data"], dtype=np.float32).reshape(enc["shape"]).copy()


def gpu_price(gpu_name: str | None) -> float:
    """Approximate Modal price per second for the GPU the container reported (L4 if unknown).
    MODAL_USD_PER_GPU_SECOND overrides the table (a configurable rate; the cost is an estimate either way)."""
    override = config.get_env("MODAL_USD_PER_GPU_SECOND")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    name = (gpu_name or "").upper()
    for key, price in config.MODAL_GPU_USD_PER_S.items():
        if key in name:
            return price
    return config.MODAL_GPU_USD_PER_S["L4"]


def _model_signal(key: str, res: dict, source: str, price: float, out_dir: Path, run_dir: Path) -> ModelSignal:
    """Turn one model result (from Modal or local) into a report entry, saving its arrays as .npy."""
    if res.get("status") != "ok":
        return ModelSignal(status="skipped" if res.get("status") == "skipped" else "error",
                           error=res.get("error"), seconds=float(res.get("seconds") or 0.0), source=source)
    arrays = {name: (decode_array(a) if isinstance(a, dict) else np.asarray(a, dtype=np.float32))
              for name, a in res["arrays"].items()}
    pooled_name, extra = POOLED.get(key, ("embedding", None))
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for name in [pooled_name] + ([extra] if extra else []):
        suffix = "" if name == "embedding" else f"_{name}"
        path = out_dir / f"{key}{suffix}.npy"
        np.save(path, arrays[name])
        files.append(str(path.relative_to(run_dir)))
    vec = arrays[pooled_name].astype(np.float64).ravel()
    meta = dict(res.get("meta") or {})
    seconds = float(res.get("seconds") or 0.0)
    return ModelSignal(status="ok", dims=int(vec.size), l2_norm=round(float(np.linalg.norm(vec)), 4), seconds=seconds,
                       cost_usd=round(seconds * price, 6) if source == "modal" else 0.0, source=source,
                       stand_in=meta.pop("stand_in", None), files=files,
                       meta={k: v for k, v in meta.items() if isinstance(v, (int, float, str, bool, type(None)))})


def _demucs(res: dict | None, stems: dict, price: float, out_dir: Path, run_dir: Path) -> DemucsResult:
    if res is None:
        return DemucsResult(status="skipped", error="Demucs was not run")
    if res.get("status") != "ok":
        return DemucsResult(status="skipped" if res.get("status") == "skipped" else "error", error=res.get("error"),
                            seconds=float(res.get("seconds") or 0.0))
    stem_dir = out_dir / "stems"
    stem_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, data in stems.items():
        (stem_dir / f"{name}.wav").write_bytes(data)
        paths[name] = str((stem_dir / f"{name}.wav").relative_to(run_dir))
    m = res.get("meta") or {}
    seconds = float(res.get("seconds") or 0.0)
    return DemucsResult(status="ok", voice_to_music_db=m.get("voice_to_music_db"), vocals_rms_db=m.get("vocals_rms_db"),
                        rest_rms_db=m.get("rest_rms_db"), stems=paths, seconds=seconds, cost_usd=round(seconds * price, 6))


def run_signals(run_dir: Path, frames_dir: Path | None, proxy: Path | None, wav: Path | None, duration_s: float,
                use_local: bool = False, modal_call=call_modal) -> SignalsStage:
    start = time.time()
    stage = SignalsStage()
    out_dir = run_dir / "signals"
    frames = [p.read_bytes() for p in sorted(frames_dir.glob("*.jpg"))[:config.MEDIA_MAX_SECONDS]] if frames_dir else []
    can_gpu = bool(frames) and proxy is not None  # no audio track: the visual models still run
    local_want = list(config.LOCAL_SIGNAL_MODELS) if use_local else []
    modal_want = [m for m in MODAL_WANT if not (use_local and m in ("dinov2", "clap"))]

    def modal_job():
        t0 = time.time()
        info = ModalCallInfo()
        try:
            if not can_gpu:
                raise RuntimeError("frames or the proxy video are missing, nothing to send to Modal")
            result = modal_call({"frames": frames, "proxy": proxy.read_bytes(), "wav": wav.read_bytes() if wav else b"",
                                 "want": modal_want})
            info.ok, info.cold = True, result["container"]["cold"]
            info.cold_main, info.cold_lb = result["container"].get("cold_main"), result["container"].get("cold_lb")
            info.gpu = result["container"]["gpu"]
            info.server_seconds = float(result["server_seconds"])
            info.wall_seconds = round(time.time() - t0, 2)
            info.overhead_seconds = round(max(info.wall_seconds - info.server_seconds, 0.0), 2)
            return info, result
        except Exception as e:  # Modal being down must never crash the pipeline
            info.error = config.redact(f"{type(e).__name__}: {e}")[:400]
            info.wall_seconds = round(time.time() - t0, 2)
            return info, None

    def local_job():
        return local.run_local(frames, wav, local_want) if use_local else {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_audio = pool.submit(audio_features, wav) if wav else None
        f_cuts = pool.submit(scene_cuts, proxy, duration_s) if proxy else None
        f_modal, f_local = pool.submit(modal_job), pool.submit(local_job)
        info, result = f_modal.result()
        local_results = f_local.result()
        if f_audio:
            stage.audio_features = f_audio.result()
        if f_cuts:
            stage.cuts = f_cuts.result()
    stage.modal = info
    price = gpu_price(info.gpu)
    models = (result or {}).get("models", {})
    reason = f"Modal call failed: {info.error}" if not info.ok else None
    for key in EXPECTED_MODELS + (["clip"] if use_local else []):
        if key in local_results:
            stage.models[key] = _model_signal(key, local_results[key], "local", price, out_dir, run_dir)
        elif key in models:
            stage.models[key] = _model_signal(key, models[key], "modal", price, out_dir, run_dir)
        else:
            stage.models[key] = ModelSignal(status="skipped", error=reason or "not requested from Modal")
    stage.demucs = _demucs(models.get("demucs") if info.ok else None, (result or {}).get("stems", {}), price, out_dir, run_dir)
    if not info.ok:
        stage.demucs.error = reason
    for key, m in stage.models.items():
        if m.stand_in:
            stage.notes.append(f"{key}: {m.stand_in} stood in because LanguageBind's video tower could not run"
                               + (f" ({m.meta.get('video_tower_error')})" if m.meta.get("video_tower_error") else ""))
    if info.ok:
        stage.notes.append(f"Modal GPU {info.gpu}: {'cold start' if info.cold else 'warm container'}, "
                           f"{info.wall_seconds}s wall, {info.server_seconds}s on the GPU; cost is APPROXIMATE "
                           f"(GPU seconds x price) and excludes the up-to-120 s idle scaledown after the last call")
        stage.cost_usd = round(info.server_seconds * price, 6)
    statuses = [m.status for m in stage.models.values()] + [stage.demucs.status]
    cpu_ok = stage.audio_features.status == "ok" or stage.cuts.status == "ok"
    if all(s == "ok" for s in statuses) and stage.audio_features.status == "ok" and stage.cuts.status == "ok":
        stage.status = "ok"
    elif any(s == "ok" for s in statuses) or cpu_ok:
        stage.status = "partial"
        stage.error = "; ".join(f"{k}: {m.error}" for k, m in stage.models.items() if m.status != "ok" and m.error)[:400] or None
    else:
        stage.status, stage.error = "error", reason or "no signal could be computed"
    stage.seconds = round(time.time() - start, 2)
    return stage
