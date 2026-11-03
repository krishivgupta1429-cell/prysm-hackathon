"""
app/services/encodec_service.py
Audio energy curve via librosa RMS analysis.

Outputs a per-second arousal curve (0.0–1.0) derived from the RMS energy
of each audio frame. This is fed to Claude as a quantified signal so it can
identify exactly where audio energy collapses — the primary drop-off risk.

No GPU required — runs entirely on CPU via librosa (~3–8s per minute of audio).
librosa is ~50MB vs torch's ~2GB, installs cleanly on Railway.
"""

import logging
import os
from typing import Dict, Any

logger = logging.getLogger(__name__)


def compute_audio_energy_curve(audio_path: str) -> Dict[str, Any]:
    """
    Compute per-second RMS energy curve from audio file.

    Returns:
        {
            "curve": [{"t": 0.0, "energy": 0.71}, ...],  # per-second
            "low_energy_segments": [{"start": 8.0, "end": 12.0, "avg_energy": 0.29}],
            "peak_energy_time": 3.5,
            "trough_energy_time": 9.0,
            "avg_energy": 0.54,
            "energy_variance": 0.12,
            "hook_energy": 0.68,        # avg energy in first 3s
            "mid_energy": 0.51,         # avg energy 3s–(end-3s)
            "closing_energy": 0.47,     # avg energy in last 3s
            "drop_detected": true,      # true if any segment drops >40% below peak
            "drop_at_seconds": 8.0,     # first major drop timestamp
        }
    """
    if not audio_path or not os.path.exists(audio_path):
        logger.warning("[EnCodec] Audio file not found — skipping energy curve")
        return {}

    try:
        import numpy as np
        import librosa

        # Load audio as mono at 22050 Hz (librosa default)
        waveform, sample_rate = librosa.load(audio_path, sr=22050, mono=True)

        total_samples = len(waveform)
        duration = total_samples / sample_rate
        samples_per_second = sample_rate

        # Compute RMS energy per second
        curve = []
        for sec in range(int(duration)):
            start = sec * samples_per_second
            end = min(start + samples_per_second, total_samples)
            chunk = waveform[start:end]
            if len(chunk) == 0:
                continue
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            curve.append({"t": float(sec), "energy": round(rms, 4)})

        if not curve:
            return {}

        energies = [p["energy"] for p in curve]
        max_energy = max(energies) if energies else 1.0
        min_energy = min(energies) if energies else 0.0

        # Normalise to 0–1 range
        energy_range = max_energy - min_energy
        if energy_range > 0:
            for p in curve:
                p["energy"] = round((p["energy"] - min_energy) / energy_range, 4)
        energies = [p["energy"] for p in curve]

        avg_energy = round(sum(energies) / len(energies), 4) if energies else 0.0
        peak_energy_time = curve[energies.index(max(energies))]["t"]
        trough_energy_time = curve[energies.index(min(energies))]["t"]

        # Hook / mid / closing zones
        hook_pts = [p["energy"] for p in curve if p["t"] <= 3.0]
        end_threshold = max(0.0, duration - 3.0)
        mid_pts = [p["energy"] for p in curve if 3.0 < p["t"] < end_threshold]
        close_pts = [p["energy"] for p in curve if p["t"] >= end_threshold]

        hook_energy = round(sum(hook_pts) / len(hook_pts), 4) if hook_pts else avg_energy
        mid_energy = round(sum(mid_pts) / len(mid_pts), 4) if mid_pts else avg_energy
        closing_energy = round(sum(close_pts) / len(close_pts), 4) if close_pts else avg_energy

        # Detect low-energy segments (energy < 40% of peak for 2+ consecutive seconds)
        low_energy_threshold = 0.40
        low_energy_segments = []
        in_low = False
        seg_start = 0.0
        for p in curve:
            if p["energy"] < low_energy_threshold:
                if not in_low:
                    in_low = True
                    seg_start = p["t"]
            else:
                if in_low:
                    seg_len = p["t"] - seg_start
                    if seg_len >= 2.0:
                        seg_energies = [
                            x["energy"] for x in curve
                            if seg_start <= x["t"] < p["t"]
                        ]
                        avg_seg = round(sum(seg_energies) / len(seg_energies), 4) if seg_energies else 0.0
                        low_energy_segments.append({
                            "start": seg_start,
                            "end": p["t"],
                            "avg_energy": avg_seg,
                        })
                    in_low = False
        # Close any open segment at end
        if in_low:
            seg_len = duration - seg_start
            if seg_len >= 2.0:
                seg_energies = [x["energy"] for x in curve if x["t"] >= seg_start]
                avg_seg = round(sum(seg_energies) / len(seg_energies), 4) if seg_energies else 0.0
                low_energy_segments.append({
                    "start": seg_start,
                    "end": round(duration, 1),
                    "avg_energy": avg_seg,
                })

        drop_detected = bool(low_energy_segments)
        drop_at_seconds = low_energy_segments[0]["start"] if low_energy_segments else None

        variance = round(
            sum((e - avg_energy) ** 2 for e in energies) / len(energies), 4
        ) if energies else 0.0

        result = {
            "curve": curve,
            "low_energy_segments": low_energy_segments,
            "peak_energy_time": peak_energy_time,
            "trough_energy_time": trough_energy_time,
            "avg_energy": avg_energy,
            "energy_variance": variance,
            "hook_energy": hook_energy,
            "mid_energy": mid_energy,
            "closing_energy": closing_energy,
            "drop_detected": drop_detected,
            "drop_at_seconds": drop_at_seconds,
            "duration": round(duration, 1),
        }
        logger.info(
            f"[EnCodec] Energy curve: {len(curve)} points, avg={avg_energy}, "
            f"hook={hook_energy}, drop={'yes at ' + str(drop_at_seconds) + 's' if drop_detected else 'no'}"
        )
        return result

    except Exception as e:
        import traceback
        logger.error(f"[EnCodec] Failed: {e}\n{traceback.format_exc()}")
        return {}


def build_energy_context(energy: Dict[str, Any]) -> str:
    """Format energy curve data into a Claude-readable context string."""
    if not energy:
        return ""

    ctx = "\nAUDIO ENERGY CURVE (measured from raw waveform):"
    ctx += f"\n- Average energy: {energy.get('avg_energy', '?')} | Hook zone (0-3s): {energy.get('hook_energy', '?')} | Mid: {energy.get('mid_energy', '?')} | Closing: {energy.get('closing_energy', '?')}"
    ctx += f"\n- Peak energy at: {energy.get('peak_energy_time', '?')}s | Lowest point at: {energy.get('trough_energy_time', '?')}s"

    if energy.get("drop_detected"):
        ctx += f"\n- ⚠ ENERGY DROP DETECTED at {energy.get('drop_at_seconds')}s"
        for seg in energy.get("low_energy_segments", []):
            ctx += f"\n  → Low energy segment: {seg['start']}s – {seg['end']}s (avg energy: {seg['avg_energy']} — this is a viewer drop-off risk zone)"
    else:
        ctx += "\n- No significant energy drops detected — audio energy is consistent"

    curve = energy.get("curve", [])
    if curve:
        sampled = [p for p in curve if p["t"] % 2 == 0]
        ctx += "\n- Energy timeline: " + " | ".join(
            f"{p['t']}s:{p['energy']}" for p in sampled[:15]
        )

    return ctx
