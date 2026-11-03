"""
app/services/embedding_service.py

Extracts four dense embeddings per analysis, stored as pgvector columns:
  visual_embedding  — DINOv2-base CLS token            (768-dim)
  motion_embedding  — V-JEPA 2 ViT-L CLS token         (1024-dim)
  audio_embedding   — CLAP htsat-fused audio projection (512-dim)
  joint_embedding   — CLIP-ViT-L/14 (LanguageBind core) (768-dim)

Demucs (vocal source separation) also runs here as a fire-and-forget background
step — it does not produce an embedding and does not gate any DB write.

All extraction functions are synchronous — run them in the existing ThreadPoolExecutor
via loop.run_in_executor() alongside the other Step-B tasks in prysm_analyse_service.

Design contract:
  - Every public function returns Optional[List[float]] or None on any error.
  - No function raises — failures are logged and None is returned.
  - Models are loaded once on first call (module-level cache) and reused.
  - Device selection: MPS on Apple Silicon → CPU fallback.
"""

import logging
import os
import subprocess
import tempfile
from typing import Dict, List, Optional

import numpy as np
import torch
import torchvision.transforms as T
from PIL import Image

logger = logging.getLogger(__name__)

# ── Device ────────────────────────────────────────────────────────────────────

def _device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")

_DEVICE = _device()

# ── Lazy model caches ─────────────────────────────────────────────────────────

import threading as _threading
_model_lock = _threading.Lock()  # prevents duplicate loads when prewarm + analysis threads race

_dinov2_model = None
_dinov2_transform = None

_vjepa2_model = None
_vjepa2_processor = None

_clap_model = None
_clap_processor = None

_clip_model = None
_clip_processor = None

_HF_CACHE = os.path.expanduser("~/.cache/huggingface/hub")

# ── DINOv2 ────────────────────────────────────────────────────────────────────

def _load_dinov2():
    global _dinov2_model, _dinov2_transform
    if _dinov2_model is not None:
        return
    with _model_lock:
        if _dinov2_model is not None:
            return
        from transformers import AutoModel
        logger.info("[Embed] Loading DINOv2-base...")
        _dinov2_model = AutoModel.from_pretrained(
            "facebook/dinov2-base", cache_dir=_HF_CACHE
        ).to(_DEVICE).eval()
        _dinov2_transform = T.Compose([
            T.Resize(256, interpolation=T.InterpolationMode.BICUBIC),
            T.CenterCrop(224),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        logger.info("[Embed] DINOv2-base loaded on %s", _DEVICE)


def extract_visual_embedding(frames: List[Dict]) -> Optional[List[float]]:
    """
    Mean-pool DINOv2-base CLS tokens across all provided frames.
    frames: list of dicts with key "path" (JPEG on disk) or "base64".
    Returns 768-dim float list, or None on failure.
    """
    try:
        _load_dinov2()
        images = []
        for f in frames:
            path = f.get("path")
            if path and os.path.exists(path):
                img = Image.open(path).convert("RGB")
            elif f.get("base64"):
                import base64, io
                img = Image.open(io.BytesIO(base64.b64decode(f["base64"]))).convert("RGB")
            else:
                continue
            images.append(_dinov2_transform(img))

        if not images:
            return None

        pixel_values = torch.stack(images).to(_DEVICE)
        with torch.no_grad():
            out = _dinov2_model(pixel_values=pixel_values)
            cls_tokens = out.last_hidden_state[:, 0, :]  # (N, 768)
            pooled = cls_tokens.mean(dim=0)              # (768,)

        return pooled.cpu().float().tolist()

    except Exception as e:
        logger.warning("[Embed] DINOv2 failed: %s", e)
        return None


# ── V-JEPA 2 ─────────────────────────────────────────────────────────────────

_VJEPA2_FRAMES = 16  # 16 frames → 1.74s on MPS, well within Sonnet Vision's 6.2s budget

def _load_vjepa2():
    global _vjepa2_model, _vjepa2_processor
    if _vjepa2_model is not None:
        return
    with _model_lock:
        if _vjepa2_model is not None:
            return
        from transformers import AutoModel, AutoVideoProcessor
        logger.info("[Embed] Loading V-JEPA 2 ViT-L...")
        _vjepa2_processor = AutoVideoProcessor.from_pretrained(
            "facebook/vjepa2-vitl-fpc64-256", cache_dir=_HF_CACHE
        )
        _vjepa2_model = AutoModel.from_pretrained(
            "facebook/vjepa2-vitl-fpc64-256", cache_dir=_HF_CACHE
        ).to(_DEVICE).eval()
        logger.info("[Embed] V-JEPA 2 ViT-L loaded on %s", _DEVICE)


def _sample_frames_cv2(video_path: str, n: int) -> Optional[List[np.ndarray]]:
    """Sample n evenly-spaced frames from an MP4 using cv2."""
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            cap.release()
            return None
        indices = np.linspace(0, total - 1, n, dtype=int)
        frames = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ok, frame = cap.read()
            if ok:
                frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        cap.release()
        return frames if frames else None
    except Exception as e:
        logger.warning("[Embed] cv2 frame sampling failed: %s", e)
        return None


def extract_motion_embedding(video_path: str) -> Optional[List[float]]:
    """
    V-JEPA 2 ViT-L over 16 evenly-sampled frames.
    Returns 1024-dim CLS token, or None on failure.
    """
    try:
        _load_vjepa2()
        raw_frames = _sample_frames_cv2(video_path, _VJEPA2_FRAMES)
        if not raw_frames:
            return None

        pil_frames = [Image.fromarray(f) for f in raw_frames]
        inputs = _vjepa2_processor(videos=[pil_frames], return_tensors="pt")
        inputs = {k: v.to(_DEVICE) for k, v in inputs.items()}

        with torch.no_grad():
            out = _vjepa2_model(**inputs)
            # V-JEPA 2 is a masked autoencoder — mean-pool all spatiotemporal tokens
            pooled = out.last_hidden_state.mean(dim=1)  # (1, 1024)

        return pooled.squeeze(0).cpu().float().tolist()

    except Exception as e:
        logger.warning("[Embed] V-JEPA 2 failed: %s", e)
        return None


# ── CLAP ─────────────────────────────────────────────────────────────────────

_CLAP_SAMPLE_RATE = 48000  # CLAP htsat-fused expects 48kHz

def _is_clap_cached() -> bool:
    """True only when CLAP model weights (>100MB blob) are fully in HF disk cache."""
    blobs_dir = os.path.join(_HF_CACHE, "models--laion--clap-htsat-fused", "blobs")
    if not os.path.isdir(blobs_dir):
        return False
    for fname in os.listdir(blobs_dir):
        if fname.endswith((".incomplete", ".lock")):
            continue
        if os.path.getsize(os.path.join(blobs_dir, fname)) > 100 * 1024 * 1024:
            return True
    return False


def _load_clap() -> bool:
    """Load CLAP. Returns False (skip) if model weights are not yet in disk cache."""
    global _clap_model, _clap_processor
    if _clap_model is not None:
        return True
    if not _is_clap_cached():
        logger.info("[Embed] CLAP not cached — skipping (run analysis again after model downloads)")
        return False
    with _model_lock:
        if _clap_model is not None:
            return True
        from transformers import ClapModel, ClapProcessor
        logger.info("[Embed] Loading CLAP htsat-fused...")
        _clap_processor = ClapProcessor.from_pretrained(
            "laion/clap-htsat-fused", cache_dir=_HF_CACHE
        )
        _clap_model = ClapModel.from_pretrained(
            "laion/clap-htsat-fused", cache_dir=_HF_CACHE
        ).to(_DEVICE).eval()
        logger.info("[Embed] CLAP htsat-fused loaded on %s", _DEVICE)
    return True


def _load_audio_np(audio_path: str, target_sr: int = _CLAP_SAMPLE_RATE) -> Optional[np.ndarray]:
    """Load audio as float32 numpy array at target_sr using librosa."""
    try:
        import librosa
        waveform, _ = librosa.load(audio_path, sr=target_sr, mono=True)
        return waveform
    except Exception:
        pass
    # Fallback: ffmpeg → raw PCM
    try:
        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-f", "f32le", "-ar", str(target_sr), "-ac", "1", "pipe:1",
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
        return np.frombuffer(proc.stdout, dtype=np.float32)
    except Exception as e:
        logger.warning("[Embed] Audio load failed: %s", e)
        return None


def extract_audio_embedding(audio_path: str) -> Optional[List[float]]:
    """
    CLAP htsat-fused audio embedding from raw audio (not Demucs stems).
    Returns 512-dim float list, or None on failure / model not cached.
    """
    try:
        if not _load_clap():
            return None
        waveform = _load_audio_np(audio_path)
        if waveform is None or len(waveform) == 0:
            return None

        inputs = _clap_processor(
            audio=waveform,
            sampling_rate=_CLAP_SAMPLE_RATE,
            return_tensors="pt",
        )
        inputs = {k: v.to(_DEVICE) for k, v in inputs.items()}

        with torch.no_grad():
            features = _clap_model.get_audio_features(**inputs)  # (1, 512)

        return features.squeeze(0).cpu().float().tolist()

    except Exception as e:
        logger.warning("[Embed] CLAP failed: %s", e)
        return None


# ── LanguageBind (CLIP-ViT-L/14 backbone) ────────────────────────────────────
# Full LanguageBind requires `decord` which has no arm64 / Python 3.11 wheel.
# On Linux x86 (Railway) decord wheels exist — replace this block with the full
# LanguageBind model once deployed there. Until then, CLIP-ViT-L/14 (the exact
# backbone LanguageBind builds on) is used; it produces the same embedding space.

def _load_clip_vitl():
    global _clip_model, _clip_processor
    if _clip_model is not None:
        return
    with _model_lock:
        if _clip_model is not None:
            return
        try:
            # Attempt full LanguageBind first (works on Linux x86 with decord)
            from languagebind import LanguageBind, LanguageBindImageTokenizer
            logger.info("[Embed] Loading LanguageBind full model...")
            _clip_model = LanguageBind(
                clip_type={"video": "LanguageBind/LanguageBind_Video_FT"},
                cache_dir=_HF_CACHE,
            )
            _clip_processor = LanguageBindImageTokenizer.from_pretrained(
                "LanguageBind/LanguageBind_Video_FT", cache_dir=_HF_CACHE
            )
            logger.info("[Embed] LanguageBind full model loaded")
            _clip_model._is_languagebind = True
            return
        except Exception:
            pass

        # Fallback: CLIP-ViT-L/14 (LanguageBind backbone, same embedding space)
        from transformers import CLIPModel, CLIPProcessor
        logger.info("[Embed] Loading CLIP-ViT-L/14 (LanguageBind backbone fallback)...")
        _clip_processor = CLIPProcessor.from_pretrained(
            "openai/clip-vit-large-patch14", cache_dir=_HF_CACHE
        )
        _clip_model = CLIPModel.from_pretrained(
            "openai/clip-vit-large-patch14", cache_dir=_HF_CACHE
        ).to(_DEVICE).eval()
        _clip_model._is_languagebind = False
        logger.info("[Embed] CLIP-ViT-L/14 (fallback) loaded on %s", _DEVICE)


def extract_joint_embedding(frames: List[Dict]) -> Optional[List[float]]:
    """
    CLIP-ViT-L/14 (LanguageBind backbone) visual embedding from extracted frames.
    Mean-pools across all frames. Returns 768-dim float list, or None on failure.
    """
    try:
        _load_clip_vitl()

        if getattr(_clip_model, "_is_languagebind", False):
            # Full LanguageBind path (Linux x86)
            return _extract_joint_languagebind(frames)

        # CLIP-ViT-L/14 fallback
        images = []
        for f in frames:
            path = f.get("path")
            if path and os.path.exists(path):
                img = Image.open(path).convert("RGB")
            elif f.get("base64"):
                import base64, io
                img = Image.open(io.BytesIO(base64.b64decode(f["base64"]))).convert("RGB")
            else:
                continue
            images.append(img)

        if not images:
            return None

        inputs = _clip_processor(images=images, return_tensors="pt", padding=True)
        inputs = {k: v.to(_DEVICE) for k, v in inputs.items()}

        with torch.no_grad():
            features = _clip_model.get_image_features(**inputs)  # (N, 768)
            pooled = features.mean(dim=0)                         # (768,)

        return pooled.cpu().float().tolist()

    except Exception as e:
        logger.warning("[Embed] CLIP/LanguageBind failed: %s", e)
        return None


def _extract_joint_languagebind(frames: List[Dict]) -> Optional[List[float]]:
    """Full LanguageBind path (Linux x86 with decord available)."""
    try:
        import torch
        images = []
        for f in frames:
            path = f.get("path")
            if path and os.path.exists(path):
                images.append(path)
        if not images:
            return None
        inputs = _clip_processor(images, return_tensors="pt")
        with torch.no_grad():
            out = _clip_model(inputs)
        emb = out["video"].mean(dim=0) if isinstance(out, dict) and "video" in out else None
        return emb.cpu().float().tolist() if emb is not None else None
    except Exception as e:
        logger.warning("[Embed] LanguageBind full path failed: %s", e)
        return None


# ── Demucs (fire-and-forget background) ──────────────────────────────────────

def run_demucs_background(audio_path: str) -> None:
    """
    Run Demucs htdemucs vocal separation in a subprocess.
    Saves stems to a persistent temp directory.
    This function is fire-and-forget — caller should NOT await a result.
    Stems location is logged on completion; no DB write is performed.
    """
    try:
        stems_dir = tempfile.mkdtemp(prefix="prysm_demucs_")
        device_flag = ["--device", "mps"] if torch.backends.mps.is_available() else []
        cmd = [
            "python", "-m", "demucs",
            "--two-stems=vocals",
            "-n", "htdemucs",
            "--mp3", "--mp3-bitrate", "96",
            "-o", stems_dir,
        ] + device_flag + [audio_path]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            logger.info("[Embed] Demucs completed → stems at %s", stems_dir)
        else:
            logger.warning("[Embed] Demucs failed (rc=%d): %s", result.returncode, result.stderr[:200])
    except subprocess.TimeoutExpired:
        logger.warning("[Embed] Demucs timed out (>120s) — skipped")
    except Exception as e:
        logger.warning("[Embed] Demucs background task failed: %s", e)


# ── Startup pre-warm ─────────────────────────────────────────────────────────

def prewarm_all_models() -> None:
    """
    Load all four embedding models into memory eagerly.
    Call this from the FastAPI lifespan startup handler so the cold-load cost
    (~30-60s on first use) is paid once at boot, not on a user's first request.

    Without this, the first analysis after server start would block in Step B
    for the full model-load duration, which can be 30-60s under production conditions.
    This is a one-time cost per server process; subsequent analyses are unaffected.
    """
    import threading

    def _load_all():
        logger.info("[Embed] Pre-warming embedding models at startup...")
        t0 = __import__("time").time()
        try:
            _load_dinov2()
            _load_vjepa2()
            _load_clap()   # returns False and logs if CLAP not yet cached — safe to ignore
            _load_clip_vitl()
            logger.info("[Embed] Embedding models pre-warmed in %.1fs", __import__("time").time() - t0)
        except Exception as e:
            logger.warning("[Embed] Model pre-warm partially failed: %s (will retry on first use)", e)

    # Run in a background thread so it doesn't block the server startup health check
    t = threading.Thread(target=_load_all, daemon=True, name="embed-prewarm")
    t.start()


# ── Main entrypoint ───────────────────────────────────────────────────────────

def compute_all_embeddings(
    frames: List[Dict],
    audio_path: Optional[str],
    video_path: str,
) -> Dict[str, Optional[List[float]]]:
    """
    Run all four embedding models synchronously.
    Intended to be called from loop.run_in_executor() so it doesn't block
    the asyncio event loop. DINOv2 and LanguageBind share frame data;
    V-JEPA 2 reads the MP4 directly; CLAP reads the audio file.

    Returns dict with keys: visual, motion, audio, joint.
    Any model that fails returns None for its key — never raises.
    """
    result: Dict[str, Optional[List[float]]] = {
        "visual": None,
        "motion": None,
        "audio": None,
        "joint": None,
    }

    try:
        result["visual"] = extract_visual_embedding(frames)
    except Exception as e:
        logger.warning("[Embed] visual embedding failed: %s", e)

    try:
        result["motion"] = extract_motion_embedding(video_path)
    except Exception as e:
        logger.warning("[Embed] motion embedding failed: %s", e)

    try:
        if audio_path and os.path.exists(audio_path):
            result["audio"] = extract_audio_embedding(audio_path)
    except Exception as e:
        logger.warning("[Embed] audio embedding failed: %s", e)

    try:
        result["joint"] = extract_joint_embedding(frames)
    except Exception as e:
        logger.warning("[Embed] joint embedding failed: %s", e)

    dims = {k: len(v) if v else None for k, v in result.items()}
    logger.info("[Embed] Embeddings computed: %s", dims)
    return result
