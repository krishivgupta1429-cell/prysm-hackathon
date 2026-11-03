"""Built during the Meta Global AI Developer Hackathon (new work). Existing models and services, new integration:
the models and vendors wrapped here existed before the hackathon; only this integration code is new.

CPU-only signals: librosa audio features and FFmpeg scene-cut detection. No GPU, no network.
"""
import json
import re
import subprocess
import time
from pathlib import Path

from app.studio.report import AudioFeatures, SceneCuts

SCENE_THRESHOLD = 0.3  # ffmpeg scene score above which a frame counts as a cut
SILENCE_DBFS = -50.0  # frames quieter than this are silence


def _rms_dbfs(x) -> float:
    import numpy as np
    return float(20 * np.log10(max(float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0, 1e-6)))


def integrated_loudness_lufs(wav_path: Path) -> float | None:
    """EBU R128 integrated loudness measured with ffmpeg's loudnorm filter (no extra dependency)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", str(wav_path), "-af",
                        "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not blocks:
        return None
    value = json.loads(blocks[-1]).get("input_i")
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if v == float("-inf") or v < -90 else round(v, 2)


def audio_features(wav_path: Path, max_seconds: float = 90.0) -> AudioFeatures:
    """RMS curve at 1 Hz, integrated loudness, tempo, onset density, silence ratio, spectral centroid."""
    start = time.time()
    out = AudioFeatures()
    try:
        import librosa
        import numpy as np
        y, sr = librosa.load(str(wav_path), sr=16000, mono=True, duration=max_seconds)
        if len(y) < sr // 2:
            raise ValueError("audio shorter than half a second")
        seconds = len(y) / sr
        out.rms_curve_1hz = [round(_rms_dbfs(y[i * sr:(i + 1) * sr]), 2) for i in range(int(np.ceil(seconds)))]
        out.integrated_loudness_lufs = integrated_loudness_lufs(wav_path)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        out.tempo_bpm = round(float(np.atleast_1d(tempo)[0]), 1)
        out.onset_density_per_s = round(len(librosa.onset.onset_detect(y=y, sr=sr, units="time")) / seconds, 3)
        rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
        out.silence_ratio = round(float(np.mean(20 * np.log10(np.maximum(rms, 1e-6)) < SILENCE_DBFS)), 4)
        out.spectral_centroid_mean_hz = round(float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr))), 1)
        out.status = "ok"
    except Exception as e:
        out.status, out.error = "error", f"{type(e).__name__}: {e}"[:300]
    out.seconds = round(time.time() - start, 2)
    return out


def scene_cuts(video_path: Path, duration_s: float) -> SceneCuts:
    """Cut times from ffmpeg's scene detector, cuts per 10 s and the average shot length."""
    start = time.time()
    out = SceneCuts()
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", str(video_path), "-an", "-vf",
                            f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[-200:])
        times = sorted({round(float(t), 2) for t in re.findall(r"pts_time:([0-9.]+)", r.stderr)})
        out.cut_times_s = times
        out.cuts_per_10s = round(len(times) / duration_s * 10, 3) if duration_s > 0 else None
        out.avg_shot_length_s = round(duration_s / (len(times) + 1), 3) if duration_s > 0 else None
        out.status = "ok"
    except Exception as e:
        out.status, out.error = "error", f"{type(e).__name__}: {e}"[:300]
    out.seconds = round(time.time() - start, 2)
    return out
