"""Audio attention curves from the soundtrack. No model, no download, no EnCodec (its licence is CC-BY-NC).

    audio_energy_curve(video)  RMS loudness in dB per 0.5 s (FFmpeg decode + NumPy)
    audio_change_curve(video)  spectral flux per 0.5 s, 0-1 per video (NumPy FFT)
    beat_onsets(video)         onset and beat timestamps (librosa, ISC licence; falls back to flux peaks if missing)

The pure cores (`*_from_samples`) take a mono float array so tests can feed synthetic audio.
Honest limits: "estimate". Loudness is not the same as what the viewer hears on a phone speaker; spectral flux rises on
speech onsets, music hits and noise bursts alike; beats are computed on the whole mix, speech included.
"""
import subprocess

import numpy as np

SR = 16000
BIN_S = 0.5
N_FFT = 1024
HOP = 256
DB_FLOOR = -80.0


class AudioError(Exception):
    pass


def decode_mono(video: str, sr: int = SR, max_seconds: float | None = 300.0) -> np.ndarray | None:
    """Mono float32 samples at `sr`, or None when the file has no audio stream. Raises AudioError on a real failure."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(sr)]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += ["-f", "f32le", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=180)
    except FileNotFoundError:
        raise AudioError("ffmpeg is not installed")
    except subprocess.TimeoutExpired:
        raise AudioError("ffmpeg timed out")
    err = p.stderr.decode("utf-8", "replace")
    if p.returncode != 0:
        if "does not contain any stream" in err or "Output file #0 does not contain" in err:
            return None
        raise AudioError("ffmpeg failed: " + err[-200:].strip())
    x = np.frombuffer(p.stdout, dtype="<f4")
    return x.astype(np.float32) if x.size else None


def _n_bins(n_samples: int, sr: int, bin_s: float) -> int:
    return max(1, int(np.ceil(n_samples / sr / bin_s - 1e-9)))


def energy_from_samples(x: np.ndarray, sr: int = SR, bin_s: float = BIN_S) -> dict:
    n = _n_bins(len(x), sr, bin_s)
    size = int(round(bin_s * sr))
    db = []
    for i in range(n):
        seg = x[i * size:(i + 1) * size].astype(np.float64)
        rms = float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0
        db.append(max(DB_FLOOR, 20.0 * np.log10(rms)) if rms > 0 else DB_FLOOR)
    db = np.array(db)
    lo, hi = float(np.percentile(db, 5)), float(np.percentile(db, 95))
    span = max(hi - lo, 6.0)  # a flat signal is not stretched to the full range
    scores = np.clip((db - lo) / span, 0.0, 1.0)
    if hi - lo < 1e-6:
        scores = np.where(db <= DB_FLOOR + 1e-6, 0.0, 0.5)
    return {"label": "audio energy (estimate)", "available": True, "bin_s": bin_s, "unit": "dBFS (RMS)",
            "times": [round(i * bin_s, 2) for i in range(n)],
            "db": [round(float(v), 1) for v in db], "scores": [round(float(v), 3) for v in scores]}


def _stft_mag(x: np.ndarray, n_fft: int = N_FFT, hop: int = HOP) -> np.ndarray:
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    win = np.hanning(n_fft).astype(np.float32)
    n_frames = 1 + (len(x) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    return np.abs(np.fft.rfft(x[idx] * win, axis=1))  # (frames, bins)


def flux_from_samples(x: np.ndarray, sr: int = SR, bin_s: float = BIN_S) -> tuple[np.ndarray, np.ndarray]:
    """(per-STFT-frame flux, per-bin flux). Flux = sum of positive changes of the log-magnitude spectrum."""
    mag = _stft_mag(x)
    logm = np.log1p(mag)  # log compression: loud peaks do not dominate, and a noise floor near -60 dBFS adds almost nothing
    flux = np.maximum(logm[1:] - logm[:-1], 0.0).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    n = _n_bins(len(x), sr, bin_s)
    t = (np.arange(len(flux)) * HOP + N_FFT / 2) / sr
    b = np.minimum((t / bin_s).astype(int), n - 1)
    per_bin = np.zeros(n)
    for i in range(n):
        sel = flux[b == i]
        per_bin[i] = sel.mean() if sel.size else 0.0
    return flux, per_bin


def change_from_samples(x: np.ndarray, sr: int = SR, bin_s: float = BIN_S) -> dict:
    _, per_bin = flux_from_samples(x, sr, bin_s)
    scale = float(np.percentile(per_bin, 95)) if per_bin.size else 0.0
    # a floor in absolute flux units: a silent or perfectly steady signal has no change, it is not rescaled to look busy
    scores = np.clip(per_bin / max(scale, FLUX_FLOOR), 0.0, 1.0)
    return {"label": "audio change (estimate)", "available": True, "bin_s": bin_s, "method": "spectral flux",
            "times": [round(i * bin_s, 2) for i in range(len(per_bin))],
            "raw": [round(float(v), 3) for v in per_bin], "scale": round(max(scale, FLUX_FLOOR), 3),
            "scores": [round(float(v), 3) for v in scores]}


FLUX_FLOOR = 8.0  # flux units: a -60 dBFS noise floor gives about 2, speech about 10-14, music 40-70 (docs/dinov2.md)


ONSET_DELTA = 0.3
ONSET_WAIT_S = 0.1
BEAT_CONFIDENCE_MIN = 0.2  # autocorrelation of the onset strength at beat lags (0.3-1.0 s); see docs/dinov2.md for the calibration


def beat_confidence(env: np.ndarray, fps: float) -> float:
    """0-1: how periodic the onset-strength envelope is at lags of 0.3 to 1.0 s (60-200 bpm). Click train at 120 bpm: 0.93,
    music clip: 0.69, speech: 0.05-0.11."""
    e = np.asarray(env, dtype=np.float64)
    e = e - e.mean()
    if e.size < 2 * fps or float(np.dot(e, e)) < 1e-12:
        return 0.0
    ac = np.correlate(e, e, "full")[len(e) - 1:]
    ac = ac / ac[0]
    lo, hi = int(0.3 * fps), min(int(1.0 * fps), len(ac) - 1)
    return float(max(0.0, ac[lo:hi].max())) if hi > lo else 0.0


def onsets_from_samples(x: np.ndarray, sr: int = SR) -> dict:
    """Onset and beat times with librosa (ISC licence). Falls back to peaks of the spectral flux if librosa is missing."""
    try:
        import librosa
    except ImportError:
        librosa = None
    if librosa is not None and len(x) >= sr:
        env = librosa.onset.onset_strength(y=x, sr=sr, hop_length=HOP)
        fps = sr / HOP
        # delta and wait keep speech from producing an onset every few frames (librosa's defaults gave about 4 per second)
        onsets = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=HOP, units="time",
                                            delta=ONSET_DELTA, wait=int(ONSET_WAIT_S * fps))
        conf = beat_confidence(env, fps)
        tempo, beats = 0.0, np.array([])
        if conf >= BEAT_CONFIDENCE_MIN:  # beats only when the soundtrack is actually periodic: speech is not
            t, beats = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=HOP, units="time")
            tempo = float(np.atleast_1d(t)[0])
        return {"label": "beats and onsets (estimate)", "available": True, "method": "librosa",
                "onsets": [round(float(t), 2) for t in onsets], "beats": [round(float(t), 2) for t in beats],
                "tempo_bpm": round(tempo, 1) if tempo else None, "beat_confidence": round(conf, 3),
                "beat_note": None if conf >= BEAT_CONFIDENCE_MIN else "no steady beat found (speech or free-time audio)"}
    flux, _ = flux_from_samples(x, sr)
    thr = float(np.mean(flux) + 2.0 * np.std(flux))
    peaks = [i for i in range(1, len(flux) - 1) if flux[i] > thr and flux[i] >= flux[i - 1] and flux[i] > flux[i + 1]]
    times, last = [], -1.0
    for i in peaks:
        t = (i * HOP + N_FFT / 2) / sr
        if t - last >= 0.1:
            times.append(round(t, 2))
            last = t
    return {"label": "beats and onsets (estimate)", "available": True, "method": "spectral-flux peaks",
            "onsets": times, "beats": [], "tempo_bpm": None}


def _unavailable(label: str, reason: str) -> dict:
    return {"label": label, "available": False, "reason": reason}


def audio_energy_curve(video: str) -> dict:
    x = decode_mono(video)
    return _unavailable("audio energy (estimate)", "no audio track") if x is None else energy_from_samples(x)


def audio_change_curve(video: str) -> dict:
    x = decode_mono(video)
    return _unavailable("audio change (estimate)", "no audio track") if x is None else change_from_samples(x)


def beat_onsets(video: str) -> dict:
    x = decode_mono(video)
    return _unavailable("beats and onsets (estimate)", "no audio track") if x is None else onsets_from_samples(x)


def loudness_dips(energy: dict, below_median_db: float = 8.0, min_s: float = 1.0) -> list[dict]:
    """Stretches at least `min_s` long that sit `below_median_db` or more under the video's median loudness
    (pauses, drops). Silence at the very start or end counts too: the planner decides what to do with it."""
    if not energy.get("available"):
        return []
    db = np.array(energy["db"])
    bin_s = energy["bin_s"]
    med = float(np.median(db))
    low = db <= med - below_median_db
    out, start = [], None
    for i in range(len(db) + 1):
        if i < len(db) and low[i] and start is None:
            start = i
        if (i == len(db) or not low[i]) and start is not None:
            if (i - start) * bin_s >= min_s - 1e-9:
                out.append({"start": round(start * bin_s, 2), "end": round(i * bin_s, 2),
                            "min_db": round(float(db[start:i].min()), 1), "below_median_db": round(med - float(db[start:i].mean()), 1)})
            start = None
    return out
