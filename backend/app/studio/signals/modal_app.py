"""Built during the Meta Global AI Developer Hackathon (new work). Existing models and services, new integration:
the models and vendors wrapped here existed before the hackathon; only this integration code is new.

Modal app "prysm-studio-signals": the GPU signal models behind Prysm's signals stage.

    modal deploy app/studio/signals/modal_app.py        # from backend/
    modal run app/studio/signals/modal_app.py::download_weights

One GPU class (L4, falling back to T4) runs DINOv2, V-JEPA 2, EnCodec, CLAP, Demucs and CLIP ViT-L/14
(the stand-in for LanguageBind's video tower). LanguageBind's own towers run in a second, small GPU class
of the same app: LanguageBind's patched CLIP needs transformers 4.40 while V-JEPA 2 needs >= 4.53, so they
cannot share one image. Hugging Face and
Demucs weights live in a Modal Volume, the container stays up 120 s after a call (no keep-warm), and every
model runs in its own try/except so one failure never loses the others.

Input: the 1 fps frames (jpg bytes), the 720p proxy and the 16 kHz wav as bytes. Output: float32
embeddings as raw bytes plus small metadata, and the Demucs stems as wav bytes. This file must stay
self-contained (it runs in the container without the rest of the repo), so the model ids below mirror
config.SIGNAL_MODEL_IDS (a test keeps them in sync).
"""
import time

import modal

APP_NAME = "prysm-studio-signals"
VOLUME_NAME = "prysm-studio-hf-cache"
CACHE_DIR = "/cache"
HF_CACHE = f"{CACHE_DIR}/hf/hub"
DEMUCS_MODEL = "htdemucs"
MODEL_IDS = {
    "dinov2": "facebook/dinov2-base",
    "vjepa2": "facebook/vjepa2-vitl-fpc64-256",
    "languagebind_video": "LanguageBind/LanguageBind_Video_FT",
    "languagebind_audio": "LanguageBind/LanguageBind_Audio_FT",
    "languagebind_text": "LanguageBind/LanguageBind_Image",
    "clip": "openai/clip-vit-large-patch14",
    "encodec": "facebook/encodec_24khz",
    "clap": "laion/clap-htsat-fused",
}
MAX_SECONDS = 90
VJEPA_FRAMES = 64  # facebook/vjepa2-vitl-fpc64-256 takes 64-frame clips

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "git", "libsndfile1", "libgl1", "libglib2.0-0")
    .pip_install("torch", "torchvision", "torchaudio")
    .pip_install("transformers>=4.53,<5", "huggingface_hub", "accelerate", "numpy", "pillow", "soundfile", "librosa",
                 "einops", "peft", "sentencepiece", "protobuf", "demucs")
    .env({"HF_HOME": f"{CACHE_DIR}/hf", "TORCH_HOME": f"{CACHE_DIR}/torch", "TOKENIZERS_PARALLELISM": "false"})
)


# LanguageBind needs transformers 4.40 (its patched CLIP breaks on newer versions), V-JEPA 2 needs >= 4.53.
lb_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "git", "libsndfile1", "libgl1", "libglib2.0-0")
    .pip_install("torch", "torchvision", "torchaudio")
    .pip_install("transformers==4.40.2", "huggingface_hub<1.0", "numpy<2", "pillow", "soundfile", "einops", "peft==0.11.1",
                 "sentencepiece", "protobuf", "decord", "opencv-python-headless")
    .run_commands("pip install pytorchvideo || echo 'pytorchvideo install failed'",
                  "pip install --no-deps languagebind || echo 'languagebind install failed'")
    .env({"HF_HOME": f"{CACHE_DIR}/hf", "TOKENIZERS_PARALLELISM": "false"})
)


# ---------------------------------------------------------------- helpers (run inside the container)

def _enc(**arrays) -> dict:
    """float32 arrays as {name: {shape, dtype, data(bytes)}}."""
    import numpy as np
    out = {}
    for name, a in arrays.items():
        a = np.ascontiguousarray(np.asarray(a, dtype=np.float32))
        out[name] = {"shape": list(a.shape), "dtype": "float32", "data": a.tobytes()}
    return out


def _rms_db(x) -> float:
    import numpy as np
    return float(20 * np.log10(max(float(np.sqrt(np.mean(np.square(x)))), 1e-6)))


def _duration(path: str) -> float:
    import subprocess
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    return min(float(out or 0) or MAX_SECONDS, MAX_SECONDS)


def _sample_frames(path: str, n: int, short_side: int = 256):
    """n frames spread evenly over the clip (padded with the last frame if the clip yields fewer)."""
    import glob
    import subprocess
    import tempfile
    import numpy as np
    from PIL import Image
    dur = max(_duration(path), 0.5)
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-t", str(MAX_SECONDS), "-i", path, "-vf",
                        f"fps={n / dur:.5f},scale=-2:{short_side}", "-frames:v", str(n), f"{d}/%04d.jpg"], check=True)
        imgs = [np.array(Image.open(f).convert("RGB")) for f in sorted(glob.glob(f"{d}/*.jpg"))]
    if not imgs:
        raise ValueError("no frames could be decoded")
    imgs += [imgs[-1]] * (n - len(imgs))
    return np.stack(imgs[:n])


def _patch_torchvision() -> None:
    """pytorchvideo (a LanguageBind dependency) imports torchvision.transforms.functional_tensor, which recent
    torchvision removed. Provide the one function it needs from the module that replaced it."""
    import sys
    import types
    if "torchvision.transforms.functional_tensor" in sys.modules:
        return
    try:
        import torchvision.transforms.functional as F
        shim = types.ModuleType("torchvision.transforms.functional_tensor")
        shim.rgb_to_grayscale = F.rgb_to_grayscale
        sys.modules["torchvision.transforms.functional_tensor"] = shim
    except Exception:
        pass


def _patch_torchaudio() -> None:
    """torchaudio >= 2.9 needs torchcodec for load(); LanguageBind's audio transform calls torchaudio.load on a
    wav, so route that to soundfile instead (same (waveform, sample_rate) result)."""
    import soundfile as sf
    import torch
    import torchaudio

    def load(path, *args, **kwargs):
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        return torch.from_numpy(data.T.copy()), sr
    torchaudio.load = load


def _read_wav16k(path: str):
    import soundfile as sf
    y, sr = sf.read(path, dtype="float32", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    return y[: MAX_SECONDS * sr], sr


# ---------------------------------------------------------------- the GPU class

@app.cls(image=image, gpu=["L4", "T4"], volumes={CACHE_DIR: volume}, scaledown_window=120, timeout=900)
class Signals:
    @modal.enter()
    def boot(self):
        import torch
        volume.reload()  # see weights committed by download_weights
        self._born = time.time()
        self._calls = 0
        self._models: dict = {}
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.gpu = torch.cuda.get_device_name(0) if self.device == "cuda" else "cpu"

    def _get(self, key, loader):
        if key not in self._models:
            self._models[key] = loader()
        return self._models[key]

    # ---- models; each returns (arrays, meta)

    def _dinov2(self, frames: list):
        import io
        import torch
        from PIL import Image
        from transformers import AutoImageProcessor, AutoModel

        def load():
            return (AutoImageProcessor.from_pretrained(MODEL_IDS["dinov2"]),
                    AutoModel.from_pretrained(MODEL_IDS["dinov2"]).to(self.device).eval())
        proc, model = self._get("dinov2", load)
        imgs = [Image.open(io.BytesIO(b)).convert("RGB") for b in frames[:MAX_SECONDS]]
        if not imgs:
            raise ValueError("no frames")
        outs = []
        with torch.inference_mode():
            for i in range(0, len(imgs), 16):
                inp = proc(images=imgs[i:i + 16], return_tensors="pt").to(self.device)
                outs.append(model(**inp).pooler_output.float().cpu())
        per_frame = torch.cat(outs).numpy()
        return {"frames": per_frame, "mean": per_frame.mean(axis=0)}, {"n_frames": len(imgs)}

    def _vjepa2(self, proxy_path: str):
        import torch
        from transformers import AutoModel, AutoVideoProcessor

        def load():
            return (AutoVideoProcessor.from_pretrained(MODEL_IDS["vjepa2"]),
                    AutoModel.from_pretrained(MODEL_IDS["vjepa2"], attn_implementation="sdpa").to(self.device).eval())
        proc, model = self._get("vjepa2", load)
        video = _sample_frames(proxy_path, VJEPA_FRAMES)
        with torch.inference_mode():
            inp = proc(video, return_tensors="pt").to(self.device)
            feats = model.get_vision_features(**inp) if hasattr(model, "get_vision_features") else model(**inp).last_hidden_state
        return {"embedding": feats.float().mean(dim=1)[0].cpu().numpy()}, {"clip_frames": VJEPA_FRAMES}

    def _clip_standin(self, frames: list):
        import io
        import numpy as np
        import torch
        from PIL import Image
        from transformers import CLIPModel, CLIPProcessor

        def load():
            return (CLIPProcessor.from_pretrained(MODEL_IDS["clip"]),
                    CLIPModel.from_pretrained(MODEL_IDS["clip"]).to(self.device).eval())
        proc, model = self._get("clip", load)
        idx = np.linspace(0, len(frames) - 1, num=min(16, len(frames))).round().astype(int)
        imgs = [Image.open(io.BytesIO(frames[i])).convert("RGB") for i in idx]
        with torch.inference_mode():
            inp = proc(images=imgs, return_tensors="pt").to(self.device)
            feats = model.get_image_features(**inp).float().cpu().numpy()
        return {"embedding": feats.mean(axis=0)}, {"n_frames": len(imgs)}

    def _encodec(self, wav_path: str):
        import librosa
        import numpy as np
        import torch
        from transformers import AutoProcessor, EncodecModel

        def load():
            return (AutoProcessor.from_pretrained(MODEL_IDS["encodec"]),
                    EncodecModel.from_pretrained(MODEL_IDS["encodec"]).to(self.device).eval())
        proc, model = self._get("encodec", load)
        y, sr = _read_wav16k(wav_path)
        y24 = librosa.resample(y, orig_sr=sr, target_sr=24000)
        inp = proc(raw_audio=y24, sampling_rate=24000, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            latent = model.encoder(inp["input_values"])  # (1, 128, T')
            codes = model.encode(inp["input_values"], inp["padding_mask"], bandwidth=6.0).audio_codes[0, 0].cpu().numpy()
        usage = [len(np.unique(codes[q])) / 1024 for q in range(codes.shape[0])]
        counts = np.bincount(codes[0], minlength=1024).astype(float)
        p = counts[counts > 0] / counts.sum()
        entropy = float(-(p * np.log(p)).sum() / np.log(1024))
        return ({"embedding": latent.float().mean(dim=-1)[0].cpu().numpy()},
                {"codebook_usage_ratio": round(float(np.mean(usage)), 4), "code_entropy_norm": round(entropy, 4),
                 "n_codebooks": int(codes.shape[0]), "code_frames": int(codes.shape[1])})

    def _clap(self, wav_path: str):
        import librosa
        import torch
        from transformers import ClapModel, ClapProcessor

        def load():
            return (ClapProcessor.from_pretrained(MODEL_IDS["clap"]),
                    ClapModel.from_pretrained(MODEL_IDS["clap"]).to(self.device).eval())
        proc, model = self._get("clap", load)
        y, sr = _read_wav16k(wav_path)
        y48 = librosa.resample(y, orig_sr=sr, target_sr=48000)
        chunk = 48000 * 10  # the fused model takes 10 s windows
        windows = [y48[i:i + chunk] for i in range(0, max(len(y48), 1), chunk)][:9]
        try:
            inp = proc(audios=windows, sampling_rate=48000, return_tensors="pt", padding=True)
        except TypeError:  # newer transformers renamed the argument
            inp = proc(audio=windows, sampling_rate=48000, return_tensors="pt", padding=True)
        with torch.inference_mode():
            feats = model.get_audio_features(**{k: v.to(self.device) for k, v in inp.items()}).float().cpu().numpy()
        return {"embedding": feats.mean(axis=0)}, {"windows": len(windows)}

    def _demucs(self, proxy_path: str):
        import io
        import subprocess
        import librosa
        import numpy as np
        import soundfile as sf
        import torch
        from demucs.apply import apply_model
        from demucs.pretrained import get_model
        raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-t", str(MAX_SECONDS), "-i", proxy_path,
                              "-vn", "-ac", "2", "-ar", "44100", "-f", "f32le", "-"], capture_output=True).stdout
        if len(raw) < 44100 * 4:
            raise ValueError("no usable audio track")
        mix = torch.from_numpy(np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).T.copy())
        model = self._get("demucs", lambda: get_model(DEMUCS_MODEL).to(self.device).eval())
        ref = mix.mean(0)
        norm = (mix - ref.mean()) / (ref.std() + 1e-8)
        with torch.inference_mode():
            sources = apply_model(model, norm[None].to(self.device), device=self.device, shifts=1, split=True,
                                  overlap=0.25, progress=False)[0].cpu()
        sources = sources * (ref.std() + 1e-8) + ref.mean()
        names = list(model.sources)
        vocals = sources[names.index("vocals")].numpy()
        rest = sum(sources[i] for i, n in enumerate(names) if n != "vocals").numpy()
        vocals_db, rest_db = _rms_db(vocals), _rms_db(rest)

        def stem_wav(stem) -> bytes:  # mono, 22.05 kHz, 16-bit keeps the transfer small
            mono = librosa.resample(stem.mean(axis=0), orig_sr=44100, target_sr=22050)
            buf = io.BytesIO()
            sf.write(buf, np.clip(mono, -1, 1), 22050, format="WAV", subtype="PCM_16")
            return buf.getvalue()
        return ({}, {"vocals_rms_db": round(vocals_db, 2), "rest_rms_db": round(rest_db, 2),
                     "voice_to_music_db": round(vocals_db - rest_db, 2)},
                {"vocals": stem_wav(vocals), "rest": stem_wav(rest)})

    # ---- the entry point

    @modal.method()
    def run(self, frames: list, proxy: bytes, wav: bytes, want: list, texts: list | None = None) -> dict:
        import os
        import tempfile
        t_call = time.time()
        cold = self._calls == 0
        self._calls += 1
        results: dict = {}
        stems: dict = {}
        with tempfile.TemporaryDirectory() as d:
            proxy_path, wav_path = os.path.join(d, "proxy.mp4"), os.path.join(d, "audio.wav")
            open(proxy_path, "wb").write(proxy)
            open(wav_path, "wb").write(wav)

            def attempt(key, fn):
                t0 = time.time()
                try:
                    out = fn()
                    arrays, meta = out[0], out[1]
                    if len(out) > 2:
                        stems.update(out[2])
                    results[key] = {"status": "ok", "arrays": _enc(**arrays), "meta": meta,
                                    "seconds": round(time.time() - t0, 3)}
                except Exception as e:  # one model failing never loses the others
                    import traceback
                    tb = traceback.extract_tb(e.__traceback__)[-1]
                    where = f" (at {tb.filename.split('/')[-1]}:{tb.lineno} in {tb.name})"
                    results[key] = {"status": "error", "error": (f"{type(e).__name__}: {e}"[:300] + where)[:450],
                                    "seconds": round(time.time() - t0, 3)}

            if "dinov2" in want:
                attempt("dinov2", lambda: self._dinov2(frames))
            if "vjepa2" in want:
                attempt("vjepa2", lambda: self._vjepa2(proxy_path))
            if "encodec" in want:
                attempt("encodec", lambda: self._encodec(wav_path))
            if "clap" in want:
                attempt("clap", lambda: self._clap(wav_path))
            if "clip" in want:
                attempt("clip", lambda: self._clip_standin(frames))
            if "demucs" in want:
                attempt("demucs", lambda: self._demucs(proxy_path))
        return {"container": {"cold": cold, "gpu": self.gpu, "calls": self._calls,
                              "container_age_s": round(time.time() - self._born, 1)},
                "models": results, "stems": stems, "server_seconds": round(time.time() - t_call, 3)}


@app.cls(image=lb_image, gpu=["L4", "T4"], volumes={CACHE_DIR: volume}, scaledown_window=120, timeout=600)
class LanguageBindSignals:
    """LanguageBind's video and audio towers, each loaded and run on its own so one failing cannot block the other."""

    @modal.enter()
    def boot(self):
        import torch
        volume.reload()
        self._born = time.time()
        self._calls = 0
        self._models: dict = {}
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.gpu = torch.cuda.get_device_name(0) if self.device == "cuda" else "cpu"

    def _tower(self, modality: str, path: str):
        import torch
        _patch_torchvision()
        if modality == "audio":
            _patch_torchaudio()
        from languagebind import LanguageBind, to_device, transform_dict
        name = {"video": "LanguageBind_Video_FT", "audio": "LanguageBind_Audio_FT"}[modality]
        if modality not in self._models:
            model = LanguageBind(clip_type={modality: name}, cache_dir=HF_CACHE).to(self.device).eval()
            self._models[modality] = (model, transform_dict[modality](model.modality_config[modality]))
        model, transform = self._models[modality]
        with torch.inference_mode():
            emb = model({modality: to_device(transform([path]), self.device)})[modality]
        return {"embedding": emb.float().cpu().numpy()[0]}, {"tower": name}

    @modal.method()
    def run(self, proxy: bytes, wav: bytes) -> dict:
        import os
        import tempfile
        import traceback
        t_call = time.time()
        cold = self._calls == 0
        self._calls += 1
        results: dict = {}
        with tempfile.TemporaryDirectory() as d:
            paths = {"video": os.path.join(d, "proxy.mp4"), "audio": os.path.join(d, "audio.wav")}
            open(paths["video"], "wb").write(proxy)
            open(paths["audio"], "wb").write(wav)
            for modality in ("video", "audio"):
                t0 = time.time()
                try:
                    arrays, meta = self._tower(modality, paths[modality])
                    results[f"languagebind_{modality}"] = {"status": "ok", "arrays": _enc(**arrays), "meta": meta,
                                                           "seconds": round(time.time() - t0, 3)}
                except Exception as e:
                    tb = traceback.extract_tb(e.__traceback__)[-1]
                    where = f" (at {tb.filename.split('/')[-1]}:{tb.lineno} in {tb.name})"
                    results[f"languagebind_{modality}"] = {"status": "error", "seconds": round(time.time() - t0, 3),
                                                           "error": (f"{type(e).__name__}: {e}"[:300] + where)[:450]}
        return {"container": {"cold": cold, "gpu": self.gpu, "calls": self._calls,
                              "container_age_s": round(time.time() - self._born, 1)},
                "models": results, "stems": {}, "server_seconds": round(time.time() - t_call, 3)}


# ---------------------------------------------------------------- weights + diagnostics

@app.function(image=image, volumes={CACHE_DIR: volume}, timeout=3600)
def download_weights() -> dict:
    """Download every model's weights into the Volume once, so inference never waits on the network."""
    from huggingface_hub import snapshot_download
    report = {}
    for key, repo in MODEL_IDS.items():
        t0 = time.time()
        try:
            snapshot_download(repo_id=repo)
            report[key] = {"repo": repo, "status": "ok", "seconds": round(time.time() - t0, 1)}
        except Exception as e:
            report[key] = {"repo": repo, "status": "error", "error": f"{type(e).__name__}: {e}"[:300]}
    try:
        from demucs.pretrained import get_model
        get_model(DEMUCS_MODEL)
        report["demucs"] = {"repo": DEMUCS_MODEL, "status": "ok"}
    except Exception as e:
        report["demucs"] = {"repo": DEMUCS_MODEL, "status": "error", "error": f"{type(e).__name__}: {e}"[:300]}
    volume.commit()
    return report


@app.function(image=image, timeout=300)
def check_environment(module: str = "") -> str:
    """Does one library import inside the image (CPU only)? One module per call, so a library that breaks
    serialization of results (some video libraries do) can be told apart from the others.

    "VJEPA2Model" checks that specific import. Returns a plain string. (This runs in the main image;
    LanguageBind lives in the second image, so its imports are not checked here.)
    """
    import importlib
    try:
        if module == "VJEPA2Model":
            from transformers import VJEPA2Model  # noqa: F401
            return "ok"
        if module == "languagebind_api":
            _patch_torchvision()
            from languagebind import LanguageBind, to_device, transform_dict  # noqa: F401
            return "ok"
        if module == "languagebind":
            _patch_torchvision()
        m = importlib.import_module(module)
        return str(getattr(m, "__version__", "ok"))[:]
    except BaseException as e:
        return f"IMPORT FAILED: {type(e).__name__}: {str(e)[:200]}"
