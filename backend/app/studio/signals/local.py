"""Built during the Meta Global AI Developer Hackathon (new work). Existing models and services, new integration:
the models and vendors wrapped here existed before the hackathon; only this integration code is new.

CPU fallback for the three lighter signal models (DINOv2, CLIP ViT-L/14, CLAP), used with the
`--signals-local` CLI flag. Everything else (V-JEPA 2, LanguageBind, EnCodec, Demucs) is Modal-only.

Needs `pip install -r requirements-ml.txt` (torch, transformers). If they are not installed the models
come back with status "skipped" and the reason, so the pipeline never crashes.
Each result has the same shape as the Modal app's, except the arrays are numpy arrays, not bytes.
"""
import io
import time
from pathlib import Path

from app.studio import config

MAX_SECONDS = 90


def _skipped(reason: str) -> dict:
    return {"status": "skipped", "error": reason, "seconds": 0.0}


def _timed(fn) -> dict:
    t0 = time.time()
    try:
        arrays, meta = fn()
        return {"status": "ok", "arrays": arrays, "meta": meta, "seconds": round(time.time() - t0, 3)}
    except Exception as e:  # a failing model never stops the others
        return {"status": "error", "error": f"{type(e).__name__}: {e}"[:400], "seconds": round(time.time() - t0, 3)}


def _dinov2(frames: list[bytes]):
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor, AutoModel
    repo = config.SIGNAL_MODEL_IDS["dinov2"]
    proc, model = AutoImageProcessor.from_pretrained(repo), AutoModel.from_pretrained(repo).eval()
    imgs = [Image.open(io.BytesIO(b)).convert("RGB") for b in frames[:MAX_SECONDS]]
    if not imgs:
        raise ValueError("no frames")
    outs = []
    with torch.inference_mode():
        for i in range(0, len(imgs), 8):
            outs.append(model(**proc(images=imgs[i:i + 8], return_tensors="pt")).pooler_output.float())
    per_frame = torch.cat(outs).numpy()
    return {"frames": per_frame, "mean": per_frame.mean(axis=0)}, {"n_frames": len(imgs)}


def _clip(frames: list[bytes]):
    import numpy as np
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor
    repo = config.SIGNAL_MODEL_IDS["clip"]
    proc, model = CLIPProcessor.from_pretrained(repo), CLIPModel.from_pretrained(repo).eval()
    idx = np.linspace(0, len(frames) - 1, num=min(16, len(frames))).round().astype(int)
    imgs = [Image.open(io.BytesIO(frames[i])).convert("RGB") for i in idx]
    with torch.inference_mode():
        feats = model.get_image_features(**proc(images=imgs, return_tensors="pt")).float().numpy()
    return {"embedding": feats.mean(axis=0)}, {"n_frames": len(imgs)}


def _clap(wav_path: Path):
    import librosa
    import torch
    from transformers import ClapModel, ClapProcessor
    repo = config.SIGNAL_MODEL_IDS["clap"]
    proc, model = ClapProcessor.from_pretrained(repo), ClapModel.from_pretrained(repo).eval()
    y, _ = librosa.load(str(wav_path), sr=48000, mono=True, duration=MAX_SECONDS)
    chunk = 48000 * 10
    windows = [y[i:i + chunk] for i in range(0, max(len(y), 1), chunk)][:9]
    try:
        inp = proc(audios=windows, sampling_rate=48000, return_tensors="pt", padding=True)
    except TypeError:  # newer transformers renamed the argument
        inp = proc(audio=windows, sampling_rate=48000, return_tensors="pt", padding=True)
    with torch.inference_mode():
        feats = model.get_audio_features(**inp).float().numpy()
    return {"embedding": feats.mean(axis=0)}, {"windows": len(windows)}


def run_local(frames: list[bytes], wav_path: Path | None, want: list[str]) -> dict[str, dict]:
    """Run the requested models of dinov2 / clip / clap on the CPU. Keys are the model names."""
    wanted = [m for m in want if m in config.LOCAL_SIGNAL_MODELS]
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError as e:
        return {m: _skipped(f"local CPU fallback needs torch and transformers ({e.name} is not installed; "
                            f"pip install -r requirements-ml.txt)") for m in wanted}
    out = {}
    for m in wanted:
        if m == "dinov2":
            out[m] = _timed(lambda: _dinov2(frames))
        elif m == "clip":
            out[m] = _timed(lambda: _clip(frames))
        elif m == "clap":
            out[m] = _skipped("no wav available") if wav_path is None else _timed(lambda: _clap(wav_path))
    return out
