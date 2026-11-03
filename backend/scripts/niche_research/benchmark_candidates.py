"""
benchmark_candidates.py — Latency benchmark: Sonnet vs 5 candidate models

Part 1: Baseline — real Sonnet vision call timing (async API calls)
Part 2: DINOv2, Demucs, CLAP, V-JEPA 2, LanguageBind timing (same cached reels)
Part 3: Dependency chain (Demucs→CLAP vs raw audio→CLAP)
Part 4: Summary table with added-latency verdict

Hardware context: Apple Silicon M-series, MPS available, no CUDA.
All models run on local hardware. Sonnet baseline uses real API calls.

Reels used: 6 cached reels from niche_research pipeline
  - varying durations: 15s, 18s, 24s, 69s, 72s, 177s
  - 6 pre-extracted frames each (256×455px JPEG)
  - pre-extracted MP3 audio
  - original MP4 video file

Usage:
    python -m scripts.niche_research.benchmark_candidates [--skip-sonnet] [--skip-<model>]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import sys
import time
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

warnings.filterwarnings("ignore")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("benchmark")

# ── Reels to benchmark ───────────────────────────────────────────────────────
# 6 representative reels with varying duration — all cached in /tmp/prysm_niche_research/
REELS = [
    {"code": "C97CAxPSypW",  "cache_key": "092f80b6b2cda293", "niche": "fitness",  "duration": 14.8},
    {"code": "DAlOO7iSgmz",  "cache_key": "2a5ed0326c206462", "niche": "beauty",   "duration": 18.5},
    {"code": "DbqX8rzJhB9",  "cache_key": "d10200e564a8baf4", "niche": "beauty",   "duration": 23.8},
    {"code": "DblNWIXNjdJ",  "cache_key": "d5f5ab001cf86ec7", "niche": "beauty",   "duration": 68.9},
    {"code": "Da0FKEOyJtE",  "cache_key": "8d4a5624e90ce32d", "niche": "beauty",   "duration": 71.6},
    {"code": "DblADp8SKGI",  "cache_key": "72064488d6721280", "niche": "beauty",   "duration": 176.7},
]

WORK_DIR = Path("/tmp/prysm_niche_research")


# ── Asset loader ─────────────────────────────────────────────────────────────

def load_assets(reel: Dict) -> Dict:
    k = reel["cache_key"]
    d = WORK_DIR / k
    frame_dir = d / f"frames_{k}"
    frames = sorted(frame_dir.glob("*.jpg")) if frame_dir.exists() else []
    mp3 = d / f"{k}.mp3"
    mp4 = d / f"{k}.mp4"
    return {
        "frames": frames,
        "mp3": mp3 if mp3.exists() else None,
        "mp4": mp4 if mp4.exists() else None,
        "duration": reel["duration"],
    }


# ── Timing helper ─────────────────────────────────────────────────────────────

def _t(fn, *args, **kwargs) -> Tuple[Any, float]:
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, round(time.perf_counter() - t0, 2)


async def _ta(coro) -> Tuple[Any, float]:
    t0 = time.perf_counter()
    result = await coro
    return result, round(time.perf_counter() - t0, 2)


# ─────────────────────────────────────────────────────────────────────────────
# PART 1 — Sonnet baseline (real API calls)
# ─────────────────────────────────────────────────────────────────────────────

async def bench_sonnet_vision(assets: Dict, niche: str, anthropic_key: str) -> float:
    """Time one Sonnet vision call (equivalent to analyse_frames_with_claude in prod)."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=anthropic_key)
    content = []
    content.append({"type": "text", "text": f"Watch these {len(assets['frames'])} frames from a {niche} video IN ORDER."})
    for fp in assets["frames"]:
        b64 = base64.b64encode(fp.read_bytes()).decode()
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text": """Describe the narrative arc and visual quality.
Return ONLY valid JSON:
{
  "overallVisualScore": 72,
  "hookType": "talking_head",
  "narrativeStructure": "...",
  "visualSummary": "..."
}"""})

    _, elapsed = await _ta(
        client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": content}],
        )
    )
    return elapsed


async def bench_sonnet_scoring(anthropic_key: str) -> float:
    """Time two parallel Sonnet scoring calls (equivalent to Step C in prod)."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=anthropic_key)

    # Lightweight proxy for the actual scoring prompt (same token scale)
    scoring_prompt = """You are scoring a 30-second beauty reel on 5 parameters.
Visual summary: Creator demonstrates a 3-step skincare routine on camera, confident delivery, good lighting.
Transcript: "Okay so I've been using this serum for 3 weeks and here is my honest review..."

Score each parameter 0-100 with one-sentence evidence:
Return ONLY valid JSON: {"parameters": [
  {"name": "Hook Strength", "score": 72, "evidence": "..."},
  {"name": "Pacing & Cuts", "score": 68, "evidence": "..."},
  {"name": "Audio Quality", "score": 80, "evidence": "..."},
  {"name": "Visual Quality", "score": 75, "evidence": "..."},
  {"name": "Captions & Text", "score": 60, "evidence": "..."}
]}"""

    t0 = time.perf_counter()
    await asyncio.gather(
        client.messages.create(model="claude-sonnet-4-6", max_tokens=1200,
                               messages=[{"role": "user", "content": scoring_prompt}]),
        client.messages.create(model="claude-sonnet-4-6", max_tokens=1200,
                               messages=[{"role": "user", "content": scoring_prompt.replace("5 parameters", "5 more parameters")}]),
    )
    return round(time.perf_counter() - t0, 2)


# ─────────────────────────────────────────────────────────────────────────────
# PART 2A — DINOv2 (frames → pooled embedding)
# ─────────────────────────────────────────────────────────────────────────────

def bench_dinov2(assets: Dict, device: str = "cpu") -> float:
    from transformers import AutoModel, AutoImageProcessor
    from PIL import Image
    import torch

    proc = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
    model = AutoModel.from_pretrained("facebook/dinov2-base").to(device).eval()

    images = [Image.open(fp).convert("RGB") for fp in assets["frames"]]
    inputs = proc(images=images, return_tensors="pt").to(device)

    t0 = time.perf_counter()
    with torch.no_grad():
        out = model(**inputs)
        emb = out.last_hidden_state[:, 0].mean(dim=0)  # pool CLS tokens across frames
    elapsed = round(time.perf_counter() - t0, 2)
    log.debug(f"DINOv2 embedding shape: {emb.shape}")
    return elapsed


def _load_dinov2(device: str):
    from transformers import AutoModel, AutoImageProcessor
    proc = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
    model = AutoModel.from_pretrained("facebook/dinov2-base").to(device).eval()
    return proc, model


# ─────────────────────────────────────────────────────────────────────────────
# PART 2B — Demucs (audio → vocal + instrumental stems)
# ─────────────────────────────────────────────────────────────────────────────

def bench_demucs(assets: Dict, outdir: Path) -> Tuple[float, Optional[Path]]:
    """Run demucs htdemucs on mp3. Returns (elapsed_sec, stems_dir)."""
    import subprocess

    mp3 = assets["mp3"]
    if not mp3:
        return 0.0, None

    stems_out = outdir / "demucs_out"
    stems_out.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    result = subprocess.run(
        ["python", "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs",
         "--mp3", "--mp3-bitrate", "128",
         "-o", str(stems_out), str(mp3)],
        capture_output=True, timeout=600,
    )
    elapsed = round(time.perf_counter() - t0, 2)

    if result.returncode != 0:
        log.warning(f"Demucs stderr: {result.stderr.decode()[-300:]}")
        return elapsed, None

    # Find output stems dir
    stems_dir = next(stems_out.glob("**/vocals.mp3"), None)
    if stems_dir:
        stems_dir = stems_dir.parent
    return elapsed, stems_dir


# ─────────────────────────────────────────────────────────────────────────────
# PART 2C — CLAP (audio → embedding + text prompt matching)
# ─────────────────────────────────────────────────────────────────────────────

TEXT_PROMPTS = [
    "upbeat background music with fast tempo",
    "slow emotional ballad",
    "lo-fi chill beats",
    "viral trending audio",
    "voice-only, no music",
    "loud energetic music",
]


def _clap_embed_audio(proc, model, audio_path: Path, device: str) -> Tuple[Any, float]:
    import torch
    import librosa

    audio, sr = librosa.load(str(audio_path), sr=48000, mono=True, duration=30.0)
    inputs = proc(audios=[audio], return_tensors="pt", sampling_rate=48000)
    inputs = {k: v.to(device) for k, v in inputs.items() if isinstance(v, torch.Tensor)}

    t0 = time.perf_counter()
    with torch.no_grad():
        audio_emb = model.get_audio_features(**inputs)
        text_inputs = proc(text=TEXT_PROMPTS, return_tensors="pt", padding=True)
        text_inputs = {k: v.to(device) for k, v in text_inputs.items() if isinstance(v, torch.Tensor)}
        text_emb = model.get_text_features(**text_inputs)
        scores = (audio_emb @ text_emb.T).squeeze()
    elapsed = round(time.perf_counter() - t0, 2)
    return scores, elapsed


def bench_clap_raw(assets: Dict, device: str = "cpu") -> float:
    """CLAP on raw audio (no Demucs)."""
    from transformers import ClapModel, ClapProcessor
    proc = ClapProcessor.from_pretrained("laion/clap-htsat-fused")
    model = ClapModel.from_pretrained("laion/clap-htsat-fused").to(device).eval()
    _, elapsed = _clap_embed_audio(proc, model, assets["mp3"], device)
    return elapsed


def bench_clap_stems(stems_dir: Optional[Path], device: str = "cpu") -> float:
    """CLAP on Demucs vocals stem (Demucs→CLAP chain, additional time only)."""
    if not stems_dir:
        return 0.0
    vocals = stems_dir / "vocals.mp3"
    if not vocals.exists():
        log.warning("No vocals.mp3 found in stems dir")
        return 0.0
    from transformers import ClapModel, ClapProcessor
    proc = ClapProcessor.from_pretrained("laion/clap-htsat-fused")
    model = ClapModel.from_pretrained("laion/clap-htsat-fused").to(device).eval()
    _, elapsed = _clap_embed_audio(proc, model, vocals, device)
    return elapsed


# ─────────────────────────────────────────────────────────────────────────────
# PART 2D — V-JEPA 2 (video → temporal embedding)
# ─────────────────────────────────────────────────────────────────────────────

def _sample_video_frames(mp4: Path, n_frames: int = 16) -> List:
    """Sample n_frames evenly from video, return PIL Images."""
    import torchvision.io as tv_io
    from PIL import Image

    # Load video — torchvision reads to tensor (T, H, W, C)
    vframes, _, _ = tv_io.read_video(str(mp4), output_format="THWC", pts_unit="sec")
    T = vframes.shape[0]
    idxs = [int(i * (T - 1) / (n_frames - 1)) for i in range(n_frames)]
    frames = [Image.fromarray(vframes[i].numpy()) for i in idxs]
    return frames


def bench_vjepa2(assets: Dict, device: str = "cpu") -> float:
    """V-JEPA 2 ViT-L on video clip. Sample 16 frames, return temporal embedding."""
    from transformers import AutoModel, AutoVideoProcessor
    import torch

    proc = AutoVideoProcessor.from_pretrained("facebook/vjepa2-vitl-fpc64-256")
    model = AutoModel.from_pretrained("facebook/vjepa2-vitl-fpc64-256").to(device).eval()

    frames = _sample_video_frames(assets["mp4"], n_frames=16)
    # AutoVideoProcessor expects list of frames (PIL) or list-of-lists
    inputs = proc(videos=[frames], return_tensors="pt").to(device)

    t0 = time.perf_counter()
    with torch.no_grad():
        out = model(**inputs)
        emb = out.last_hidden_state.mean(dim=1)  # pool over spatial+temporal tokens
    elapsed = round(time.perf_counter() - t0, 2)
    log.debug(f"V-JEPA 2 embedding shape: {emb.shape}")
    return elapsed


# ─────────────────────────────────────────────────────────────────────────────
# PART 2E — LanguageBind (video + audio → joint embedding)
# ─────────────────────────────────────────────────────────────────────────────

def bench_languagebind(assets: Dict, device: str = "cpu") -> float:
    """LanguageBind Video model: video frames + audio → joint embedding."""
    from languagebind import LanguageBind, LanguageBindVideoProcessor
    import torch

    # LanguageBind video-only checkpoint (also handles audio)
    model = LanguageBind(
        cache_dir="/tmp/lb_cache",
        clip_type={"video": "LanguageBind/LanguageBind_Video_PT"},
    )
    proc = LanguageBindVideoProcessor(model.modality_config["video"])
    model.eval().to(device)

    mp4 = str(assets["mp4"])
    inputs = proc([mp4], return_tensors="pt").to(device)

    t0 = time.perf_counter()
    with torch.no_grad():
        emb = model.encode_video(inputs)
    elapsed = round(time.perf_counter() - t0, 2)
    log.debug(f"LanguageBind embedding shape: {emb.shape}")
    return elapsed


# ─────────────────────────────────────────────────────────────────────────────
# PART 3 — Runner
# ─────────────────────────────────────────────────────────────────────────────

def _detect_device() -> str:
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


async def run_all(args) -> None:
    device = _detect_device()
    log.info(f"Benchmark device: {device}")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    results: Dict[str, List[float]] = {}

    SCRATCHPAD = Path("/private/tmp/benchmark_scratch")
    SCRATCHPAD.mkdir(parents=True, exist_ok=True)

    reel_infos = []
    for reel in REELS:
        assets = load_assets(reel)
        if not assets["frames"]:
            log.warning(f"[{reel['code']}] No frames found, skipping")
            continue
        reel_infos.append((reel, assets))

    log.info(f"Benchmarking on {len(reel_infos)} reels")

    # ── 1. Sonnet vision baseline ─────────────────────────────────────────────
    if not args.skip_sonnet:
        if not anthropic_key:
            log.warning("ANTHROPIC_API_KEY not set — skipping Sonnet baseline. Set via env or --skip-sonnet.")
        else:
            log.info("=== PART 1: Sonnet vision baseline ===")
            vision_times = []
            for reel, assets in reel_infos:
                log.info(f"  [{reel['code']}] ({reel['duration']:.0f}s) Sonnet vision call...")
                t = await bench_sonnet_vision(assets, reel["niche"], anthropic_key)
                log.info(f"  [{reel['code']}] vision={t:.1f}s")
                vision_times.append(t)
            results["Sonnet-Vision"] = vision_times

            log.info("  [scoring] Two parallel Sonnet scoring calls (Step C proxy)...")
            scoring_times = []
            for _ in range(3):  # 3 samples
                t = await bench_sonnet_scoring(anthropic_key)
                scoring_times.append(t)
                log.info(f"  [scoring] {t:.1f}s")
            results["Sonnet-Scoring-2x-parallel"] = scoring_times

    # ── 2A. DINOv2 ───────────────────────────────────────────────────────────
    if not args.skip_dinov2:
        log.info("=== PART 2A: DINOv2 ===")
        try:
            # Warm up (first call loads model weights into cache)
            first_assets = reel_infos[0][1]
            log.info("  Loading DINOv2 model (first call includes weight loading)...")
            t_cold = bench_dinov2(first_assets, device)
            log.info(f"  [cold load+inference] {t_cold:.1f}s")

            dinov2_times = []
            from transformers import AutoModel, AutoImageProcessor
            import torch
            from PIL import Image
            proc, model_dino = _load_dinov2(device)
            for reel, assets in reel_infos:
                images = [Image.open(fp).convert("RGB") for fp in assets["frames"]]
                inputs = proc(images=images, return_tensors="pt").to(device)
                t0 = time.perf_counter()
                with torch.no_grad():
                    out = model_dino(**inputs)
                    _ = out.last_hidden_state[:, 0].mean(dim=0)
                t = round(time.perf_counter() - t0, 2)
                log.info(f"  [{reel['code']}] ({reel['duration']:.0f}s) DINOv2={t:.2f}s")
                dinov2_times.append(t)
            results["DINOv2"] = dinov2_times
            results["DINOv2-cold-load"] = [t_cold]
        except Exception as e:
            log.error(f"DINOv2 failed: {e}")
            results["DINOv2"] = [None]

    # ── 2B. Demucs ───────────────────────────────────────────────────────────
    if not args.skip_demucs:
        log.info("=== PART 2B: Demucs ===")
        try:
            demucs_times = []
            stems_dirs = {}
            for reel, assets in reel_infos:
                reel_scratch = SCRATCHPAD / reel["cache_key"]
                reel_scratch.mkdir(exist_ok=True)
                log.info(f"  [{reel['code']}] ({reel['duration']:.0f}s) Demucs...")
                t, stems_dir = bench_demucs(assets, reel_scratch)
                log.info(f"  [{reel['code']}] Demucs={t:.1f}s, stems={'ok' if stems_dir else 'FAIL'}")
                demucs_times.append(t)
                stems_dirs[reel["code"]] = stems_dir
            results["Demucs"] = demucs_times
        except Exception as e:
            log.error(f"Demucs failed: {e}")
            results["Demucs"] = [None]
            stems_dirs = {}
    else:
        stems_dirs = {}

    # ── 2C. CLAP (raw audio) ─────────────────────────────────────────────────
    if not args.skip_clap:
        log.info("=== PART 2C: CLAP (raw audio) ===")
        try:
            from transformers import ClapModel, ClapProcessor
            log.info("  Loading CLAP model...")
            proc_clap = ClapProcessor.from_pretrained("laion/clap-htsat-fused")
            model_clap = ClapModel.from_pretrained("laion/clap-htsat-fused").to(device).eval()

            clap_raw_times = []
            for reel, assets in reel_infos:
                if not assets["mp3"]:
                    clap_raw_times.append(None)
                    continue
                _, t = _clap_embed_audio(proc_clap, model_clap, assets["mp3"], device)
                log.info(f"  [{reel['code']}] ({reel['duration']:.0f}s) CLAP-raw={t:.2f}s")
                clap_raw_times.append(t)
            results["CLAP-raw-audio"] = clap_raw_times

            # CLAP on Demucs stems (dependency chain)
            if stems_dirs:
                log.info("  CLAP on Demucs stems (chain ordering)...")
                clap_stems_times = []
                for reel, _ in reel_infos:
                    sd = stems_dirs.get(reel["code"])
                    if sd:
                        _, t = _clap_embed_audio(proc_clap, model_clap, sd / "vocals.mp3", device)
                        log.info(f"  [{reel['code']}] CLAP-stems={t:.2f}s")
                        clap_stems_times.append(t)
                    else:
                        clap_stems_times.append(None)
                results["CLAP-on-Demucs-stems"] = clap_stems_times

        except Exception as e:
            log.error(f"CLAP failed: {e}")
            results["CLAP-raw-audio"] = [None]

    # ── 2D. V-JEPA 2 ─────────────────────────────────────────────────────────
    if not args.skip_vjepa2:
        log.info("=== PART 2D: V-JEPA 2 ===")
        try:
            from transformers import AutoModel, AutoVideoProcessor
            import torch
            log.info("  Loading V-JEPA 2 ViT-L (will download ~1.2GB on first run)...")
            proc_vj = AutoVideoProcessor.from_pretrained("facebook/vjepa2-vitl-fpc64-256")
            t_load_start = time.perf_counter()
            model_vj = AutoModel.from_pretrained("facebook/vjepa2-vitl-fpc64-256").to(device).eval()
            t_load = round(time.perf_counter() - t_load_start, 1)
            log.info(f"  V-JEPA 2 model loaded in {t_load}s")

            vjepa_times = []
            for reel, assets in reel_infos:
                if not assets["mp4"]:
                    vjepa_times.append(None)
                    continue
                frames = _sample_video_frames(assets["mp4"], n_frames=16)
                inputs = proc_vj(videos=[frames], return_tensors="pt").to(device)
                t0 = time.perf_counter()
                with torch.no_grad():
                    out = model_vj(**inputs)
                    _ = out.last_hidden_state.mean(dim=1)
                t = round(time.perf_counter() - t0, 2)
                log.info(f"  [{reel['code']}] ({reel['duration']:.0f}s) V-JEPA2={t:.2f}s")
                vjepa_times.append(t)
            results["V-JEPA-2"] = vjepa_times
        except Exception as e:
            log.error(f"V-JEPA 2 failed: {e}")
            import traceback; traceback.print_exc()
            results["V-JEPA-2"] = [None]

    # ── 2E. LanguageBind ─────────────────────────────────────────────────────
    if not args.skip_languagebind:
        log.info("=== PART 2E: LanguageBind ===")
        try:
            from languagebind import LanguageBind, LanguageBindVideoProcessor
            import torch
            log.info("  Loading LanguageBind Video model...")
            model_lb = LanguageBind(
                cache_dir="/tmp/lb_cache",
                clip_type={"video": "LanguageBind/LanguageBind_Video_PT"},
            )
            proc_lb = LanguageBindVideoProcessor(model_lb.modality_config["video"])
            model_lb.eval().to(device)

            lb_times = []
            for reel, assets in reel_infos:
                if not assets["mp4"]:
                    lb_times.append(None)
                    continue
                inputs = proc_lb([str(assets["mp4"])], return_tensors="pt").to(device)
                t0 = time.perf_counter()
                with torch.no_grad():
                    emb = model_lb.encode_video(inputs)
                t = round(time.perf_counter() - t0, 2)
                log.info(f"  [{reel['code']}] ({reel['duration']:.0f}s) LanguageBind={t:.2f}s")
                lb_times.append(t)
            results["LanguageBind"] = lb_times
        except Exception as e:
            log.error(f"LanguageBind failed: {e}")
            import traceback; traceback.print_exc()
            results["LanguageBind"] = [None]

    # ── Report ────────────────────────────────────────────────────────────────
    print_report(results, reel_infos, device)


def _stats(times: List[Optional[float]]) -> Tuple[str, str, str]:
    clean = [t for t in times if t is not None]
    if not clean:
        return "ERR", "ERR", "ERR"
    return f"{min(clean):.1f}s", f"{max(clean):.1f}s", f"{sum(clean)/len(clean):.1f}s"


def print_report(results: Dict, reel_infos, device: str) -> None:
    reels = [r["code"] for r, _ in reel_infos]
    durations = [r["duration"] for r, _ in reel_infos]

    sonnet_vision = results.get("Sonnet-Vision", [])
    sonnet_scoring = results.get("Sonnet-Scoring-2x-parallel", [])

    print("\n" + "=" * 78)
    print("BENCHMARK RESULTS")
    print(f"Hardware: Apple Silicon M-series, MPS={device == 'mps'}, {len(reel_infos)} reels")
    print("=" * 78)

    # Per-reel raw table
    print("\n### Per-Reel Timings (seconds)\n")
    header_models = [k for k in results if k not in ("Sonnet-Scoring-2x-parallel", "DINOv2-cold-load")]
    col_w = 18
    header = f"{'Reel':<12} {'Dur':>5} | " + " | ".join(f"{m[:16]:<16}" for m in header_models)
    print(header)
    print("-" * len(header))
    for i, (code, dur) in enumerate(zip(reels, durations)):
        row = f"{code:<12} {dur:>4.0f}s | "
        for m in header_models:
            vals = results[m]
            v = vals[i] if i < len(vals) else None
            row += f"{str(v) + 's':<16} | " if v is not None else f"{'n/a':<16} | "
        print(row)

    # Summary stats
    print("\n### Summary Statistics\n")
    print(f"{'Model':<35} {'Min':>7} {'Max':>7} {'Avg':>7}  {'Hardware':<12}")
    print("-" * 75)
    for name, times in results.items():
        mn, mx, avg = _stats(times)
        hw = device.upper()
        print(f"{name:<35} {mn:>7} {mx:>7} {avg:>7}  {hw:<12}")

    # Critical path analysis
    sonnet_v_avg = sum(t for t in sonnet_vision if t) / len([t for t in sonnet_vision if t]) if any(sonnet_vision) else None
    sonnet_s_avg = sum(t for t in sonnet_scoring if t) / len([t for t in sonnet_scoring if t]) if any(sonnet_scoring) else None
    sonnet_total = (sonnet_v_avg or 0) + (sonnet_s_avg or 0)

    print("\n### Critical Path Analysis\n")
    print("Current pipeline sequence:")
    print(f"  Step B (parallel): Sonnet Vision + Whisper + Gemini + Trends")
    if sonnet_v_avg:
        print(f"    → Sonnet Vision:           {sonnet_v_avg:.1f}s avg (MEASURED)")
    else:
        print(f"    → Sonnet Vision:           [not measured — skipped]")
    if sonnet_s_avg:
        print(f"  Step C (parallel A+B):  {sonnet_s_avg:.1f}s avg (MEASURED proxy)")
    else:
        print(f"  Step C (parallel A+B):  [not measured — skipped]")

    print()
    print("Candidate models — would run in Step B parallel slot:")
    print(f"  {'Model':<30} {'Avg':>7}  {'Verdict'}")
    print("  " + "-" * 65)

    step_b_budget = sonnet_v_avg or None
    for name in ["DINOv2", "CLAP-raw-audio", "V-JEPA-2", "LanguageBind"]:
        if name not in results:
            continue
        times = [t for t in results[name] if t is not None]
        if not times:
            print(f"  {name:<30} {'ERR':>7}  could not benchmark")
            continue
        avg = sum(times) / len(times)
        if step_b_budget:
            added = max(0, avg - step_b_budget)
            verdict = f"FREE (finishes {step_b_budget - avg:.1f}s before Sonnet)" if avg < step_b_budget else f"+{added:.1f}s added latency"
        else:
            verdict = f"{avg:.1f}s (Sonnet not measured)"
        print(f"  {name:<30} {avg:>6.1f}s  {verdict}")

    print()
    print("Demucs → CLAP dependency chain:")
    dem = results.get("Demucs", [])
    clap_raw = results.get("CLAP-raw-audio", [])
    clap_stems = results.get("CLAP-on-Demucs-stems", [])

    dem_times = [t for t in dem if t is not None]
    clap_raw_times = [t for t in clap_raw if t is not None]
    clap_stems_times = [t for t in clap_stems if t is not None]

    if dem_times and clap_raw_times:
        dem_avg = sum(dem_times) / len(dem_times)
        raw_avg = sum(clap_raw_times) / len(clap_raw_times)
        chain_avg = dem_avg + (sum(clap_stems_times) / len(clap_stems_times) if clap_stems_times else raw_avg)
        print(f"  Option A (raw audio → CLAP directly): CLAP = {raw_avg:.1f}s avg")
        print(f"  Option B (raw audio → Demucs → CLAP):  Demucs={dem_avg:.1f}s + CLAP≈{sum(clap_stems_times)/len(clap_stems_times):.1f}s = {chain_avg:.1f}s avg")
        print(f"  → Option A is {chain_avg - raw_avg:.1f}s faster (skip Demucs for CLAP)")

    # Final verdict table
    print("\n### Final Verdict\n")
    print(f"{'Model':<30} {'Avg time':>9} {'vs Sonnet Vision':>20} {'Adds latency?':>15}")
    print("-" * 80)

    all_models = ["DINOv2", "Demucs", "CLAP-raw-audio", "V-JEPA-2", "LanguageBind"]
    for name in all_models:
        if name not in results:
            print(f"{name:<30} {'N/A':>9} {'not benchmarked':>20} {'?':>15}")
            continue
        times = [t for t in results[name] if t is not None]
        if not times:
            print(f"{name:<30} {'FAILED':>9} {'—':>20} {'?':>15}")
            continue
        avg = sum(times) / len(times)
        if step_b_budget:
            if avg < step_b_budget:
                verdict = f"NO — finishes {step_b_budget - avg:.1f}s early"
            else:
                verdict = f"YES — +{avg - step_b_budget:.1f}s"
        else:
            verdict = f"{avg:.1f}s (Sonnet N/A)"
        print(f"{name:<30} {avg:>8.1f}s {'—' if not step_b_budget else f'{step_b_budget:.1f}s Sonnet':>20} {verdict:>15}")

    # Serial total (for batch processing context)
    print("\n### Serial total (all 5 models, no parallelism, per reel):")
    serial_parts = []
    for name in all_models:
        times = [t for t in results.get(name, []) if t is not None]
        if times:
            serial_parts.append((name, sum(times) / len(times)))
    if serial_parts:
        total = sum(v for _, v in serial_parts)
        print(f"  " + " + ".join(f"{n.split('-')[0]}={v:.1f}s" for n, v in serial_parts))
        print(f"  = {total:.1f}s/reel added to batch pipeline")

    # Save JSON for later analysis
    out_path = Path(__file__).parent / "results" / "benchmark_candidates.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "hardware": device,
            "reels": [{"code": r["code"], "duration": r["duration"]} for r, _ in reel_infos],
            "results": {k: v for k, v in results.items()},
        }, f, indent=2, default=str)
    log.info(f"Raw results saved to {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-sonnet",       action="store_true")
    ap.add_argument("--skip-dinov2",       action="store_true")
    ap.add_argument("--skip-demucs",       action="store_true")
    ap.add_argument("--skip-clap",         action="store_true")
    ap.add_argument("--skip-vjepa2",       action="store_true")
    ap.add_argument("--skip-languagebind", action="store_true")
    args = ap.parse_args()

    asyncio.run(run_all(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
