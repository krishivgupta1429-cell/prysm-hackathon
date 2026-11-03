"""
app/services/aesthetics_service.py
Per-frame aesthetic scoring via LAION Aesthetics v2 (CLIP-based).

Downloads the aesthetic predictor on first run (~300MB, cached to ~/.cache/huggingface).
Runs entirely on CPU — ~0.5s per frame, ~40s for 80 frames.
License: open weights, commercially usable.

Outputs a per-frame aesthetic score (1–10) which is fed to Claude so it can
identify visually weak frames with exact timestamps instead of guessing.
"""

import logging
import os
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Module-level model cache — loaded once per process
_aesthetic_model = None
_aesthetic_preprocess = None
_clip_model = None


def _load_models():
    """Load CLIP + aesthetic predictor head. Downloads on first call, cached after."""
    global _aesthetic_model, _aesthetic_preprocess, _clip_model
    if _aesthetic_model is not None:
        return True
    try:
        import torch
        import torch.nn as nn
        from transformers import CLIPProcessor, CLIPModel

        logger.info("[Aesthetics] Loading CLIP ViT-L/14 + aesthetic head...")

        # LAION Aesthetics v2 uses CLIP ViT-L/14 as backbone
        clip_model_id = "openai/clip-vit-large-patch14"
        _clip_model = CLIPModel.from_pretrained(clip_model_id)
        _aesthetic_preprocess = CLIPProcessor.from_pretrained(clip_model_id)
        _clip_model.eval()

        # Aesthetic predictor head — simple MLP trained on LAION data
        # Weights from: https://github.com/christophschuhmann/improved-aesthetic-predictor
        class AestheticPredictor(nn.Module):
            def __init__(self, input_size=768):
                super().__init__()
                self.layers = nn.Sequential(
                    nn.Linear(input_size, 1024),
                    nn.Dropout(0.2),
                    nn.Linear(1024, 128),
                    nn.Dropout(0.2),
                    nn.Linear(128, 64),
                    nn.Dropout(0.2),
                    nn.Linear(64, 16),
                    nn.Linear(16, 1),
                )

            def forward(self, x):
                return self.layers(x)

        predictor = AestheticPredictor()

        # Download pre-trained weights
        import urllib.request
        weights_url = "https://github.com/christophschuhmann/improved-aesthetic-predictor/raw/main/sac+logos+ava1-l14-linearMSE.pth"
        weights_path = os.path.expanduser("~/.cache/prysm/aesthetic_predictor.pth")
        os.makedirs(os.path.dirname(weights_path), exist_ok=True)

        if not os.path.exists(weights_path):
            logger.info("[Aesthetics] Downloading aesthetic predictor weights (~80MB)...")
            import socket
            socket.setdefaulttimeout(120)
            urllib.request.urlretrieve(weights_url, weights_path)
            socket.setdefaulttimeout(None)
            logger.info("[Aesthetics] Weights downloaded")

        state = torch.load(weights_path, map_location="cpu", weights_only=True)
        predictor.load_state_dict(state)
        predictor.eval()
        _aesthetic_model = predictor

        logger.info("[Aesthetics] Models loaded successfully")
        return True

    except Exception as e:
        import traceback
        logger.error(f"[Aesthetics] Model load failed: {e}\n{traceback.format_exc()}")
        return False


def score_frames(frames: List[Dict]) -> Dict[str, Any]:
    """
    Score each frame for aesthetic quality (1–10).

    Args:
        frames: list of {"path": "/path/to/frame.jpg", "timestamp": 1.5}

    Returns:
        {
            "scores": [{"t": 1.5, "score": 7.2}, ...],
            "best_frame": {"t": 3.0, "score": 8.4},
            "worst_frame": {"t": 22.5, "score": 3.1},
            "avg_score": 6.3,
            "weak_frames": [{"t": 22.5, "score": 3.1}, ...],  # score < 4.5
            "thumbnail_candidate": 3.0,
        }
    """
    if not frames:
        return {}

    if not _load_models():
        logger.warning("[Aesthetics] Models not available — skipping aesthetic scoring")
        return {}

    try:
        import torch
        from PIL import Image

        scores = []
        for frame in frames:
            path = frame.get("path", "")
            ts = frame.get("timestamp", 0.0)
            if not os.path.exists(path):
                continue
            try:
                image = Image.open(path).convert("RGB")
                inputs = _aesthetic_preprocess(images=image, return_tensors="pt")

                with torch.no_grad():
                    image_features = _clip_model.get_image_features(**inputs)
                    # Normalise embeddings
                    image_features = image_features / image_features.norm(dim=-1, keepdim=True)
                    score = _aesthetic_model(image_features).item()

                # Raw output is roughly 1–10, clamp to be safe
                score = round(max(1.0, min(10.0, score)), 2)
                scores.append({"t": ts, "score": score})
            except Exception as fe:
                logger.warning(f"[Aesthetics] Frame {ts}s failed: {fe}")

        if not scores:
            return {}

        all_scores = [s["score"] for s in scores]
        best = max(scores, key=lambda x: x["score"])
        worst = min(scores, key=lambda x: x["score"])
        avg = round(sum(all_scores) / len(all_scores), 2)
        weak = [s for s in scores if s["score"] < 4.5]

        result = {
            "scores": scores,
            "best_frame": best,
            "worst_frame": worst,
            "avg_score": avg,
            "weak_frames": weak,
            "thumbnail_candidate": best["t"],
        }
        logger.info(
            f"[Aesthetics] Scored {len(scores)} frames: avg={avg}, "
            f"best={best['score']}@{best['t']}s, worst={worst['score']}@{worst['t']}s, "
            f"weak_count={len(weak)}"
        )
        return result

    except Exception as e:
        import traceback
        logger.error(f"[Aesthetics] Scoring failed: {e}\n{traceback.format_exc()}")
        return {}


def build_aesthetics_context(aesthetics: Dict[str, Any]) -> str:
    """Format aesthetic scores into a Claude-readable context string."""
    if not aesthetics:
        return ""

    ctx = "\nFRAME AESTHETIC SCORES (LAION v2, scale 1–10):"
    ctx += f"\n- Average aesthetic score: {aesthetics.get('avg_score', '?')}/10"

    best = aesthetics.get("best_frame", {})
    worst = aesthetics.get("worst_frame", {})
    if best:
        ctx += f"\n- Strongest frame: {best.get('t')}s (score {best.get('score')}/10) — use as thumbnail candidate"
    if worst:
        ctx += f"\n- Weakest frame: {worst.get('t')}s (score {worst.get('score')}/10) — visually poor, consider cutting or re-shooting"

    weak = aesthetics.get("weak_frames", [])
    if weak:
        ctx += f"\n- ⚠ {len(weak)} visually weak frame(s) detected (score < 4.5):"
        for w in weak[:5]:
            ctx += f"\n  → {w['t']}s: score {w['score']}/10 — low aesthetic quality at this moment"
    else:
        ctx += "\n- No critically weak frames — visual quality is consistent"

    # Compact per-frame list for Claude
    scores = aesthetics.get("scores", [])
    if scores:
        ctx += "\n- Per-frame scores: " + " | ".join(
            f"{s['t']}s:{s['score']}" for s in scores[:12]
        )

    return ctx
