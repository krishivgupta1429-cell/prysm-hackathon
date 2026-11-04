"""Built during the Meta Global AI Developer Hackathon (new work).

music_duck: Demucs (the existing prysm-studio-signals Modal app) separates voice from the rest. The stems are 22 kHz mono, so
they are only used to find where the voice is and to take the music down there: out = original + (gain(t) - 1) * rest(t).
The full-quality original audio is what gets processed. Only called when music was detected AND speech exists.
"""
import time
from pathlib import Path

from app.studio.autoedit import config, ffx
from app.studio.autoedit.llm import CostMeter

SR = 48000
HOP_S = 0.02
ATTACK_S, RELEASE_S = 0.15, 0.6
HOLD_S = 0.6   # the voice counts as present through short gaps between words, so the music does not bounce back and forth (pumping)


def voice_activity(vocals, sr: int, floor_db: float = -38.0):
    """Per-hop 0/1 voice activity from the vocals stem: RMS above a floor relative to the stem's own loud level."""
    import numpy as np
    hop = int(sr * HOP_S)
    n = len(vocals) // hop
    if n == 0:
        return np.zeros(0)
    rms = np.sqrt(np.mean(vocals[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    thresh = max(floor_db, np.percentile(db, 95) - 24)
    act = (db > thresh).astype(float)
    from scipy.ndimage import maximum_filter1d
    return maximum_filter1d(act, size=max(1, int(HOLD_S / HOP_S)))


def smooth_gain(active, duck_gain: float):
    """Gain per hop: 1.0 without voice, `duck_gain` with it, with an attack (fast) and a release (slow) so it does not pump."""
    import numpy as np
    g = np.ones(len(active))
    a_coef, r_coef = np.exp(-HOP_S / ATTACK_S), np.exp(-HOP_S / RELEASE_S)
    cur = 1.0
    for i, act in enumerate(active):
        target = duck_gain if act else 1.0
        coef = a_coef if target < cur else r_coef
        cur = target + (cur - target) * coef
        g[i] = cur
    return g


def make_duck_wav(orig_wav: Path, vocals_wav: Path, rest_wav: Path, duck_db: float, out: Path) -> dict:
    """orig_wav: the original audio (any rate / channels). Writes a 48 kHz stereo wav with the music ducked under the voice."""
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    orig, sr = sf.read(str(orig_wav), dtype="float32", always_2d=True)
    if sr != SR:
        orig = resample_poly(orig, SR, sr, axis=0).astype("float32")
    if orig.shape[1] == 1:
        orig = np.repeat(orig, 2, axis=1)
    vocals, vsr = sf.read(str(vocals_wav), dtype="float32")
    rest, rsr = sf.read(str(rest_wav), dtype="float32")
    rest = resample_poly(rest, SR, rsr).astype("float32") if rsr != SR else rest
    active = voice_activity(vocals, vsr)
    g = smooth_gain(active, 10 ** (duck_db / 20))
    gain = np.repeat(g, int(SR * HOP_S))
    n = orig.shape[0]
    gain = np.pad(gain, (0, max(0, n - len(gain))), constant_values=1.0)[:n]
    rest = np.pad(rest, (0, max(0, n - len(rest))))[:n]
    mixed = orig + ((gain - 1.0) * rest)[:, None]
    sf.write(str(out), np.clip(mixed, -1.0, 1.0), SR, subtype="PCM_16")
    return {"voice_share": round(float(active.mean()), 3) if len(active) else 0.0, "min_gain": round(float(gain.min()), 3)}


def separate_on_modal(src: Path, work: Path, meter: CostMeter) -> tuple[Path, Path]:
    """Send only the audio to the Demucs class on Modal; returns (vocals.wav, rest.wav) paths."""
    from app.studio.stages.signals import call_modal, gpu_price  # the existing, tested way of calling the deployed app
    meter.check(0.05)
    work.mkdir(parents=True, exist_ok=True)
    audio_only = work / "audio_only.mp4"
    ffx.run(["-y", "-i", str(src), "-vn", "-c:a", "aac", "-b:a", "128k", "-t", str(config.MAX_SOURCE_SECONDS), str(audio_only)])
    tiny = work / "tiny.wav"
    ffx.run(["-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "1", str(tiny)])
    t0 = time.time()
    res = call_modal({"frames": [], "proxy": audio_only.read_bytes(), "wav": tiny.read_bytes(), "want": ["demucs"], "texts": None})
    stems = res.get("stems") or {}
    if "vocals" not in stems or "rest" not in stems:
        err = (res.get("models", {}).get("demucs") or {}).get("error", "no stems returned")
        raise RuntimeError(f"Demucs on Modal gave no stems ({err})")
    seconds = float(res.get("server_seconds") or (time.time() - t0))
    gpu = (res.get("container") or {}).get("gpu") or "L4"
    price = gpu_price(gpu)
    meter.add("modal", "demucs", seconds * price, seconds=round(seconds, 1), gpu=gpu)
    v, r = work / "vocals.wav", work / "rest.wav"
    v.write_bytes(stems["vocals"])
    r.write_bytes(stems["rest"])
    return v, r


def prepare(src: Path, work: Path, meter: CostMeter, duck_db: float, stems: tuple[Path, Path] | None = None) -> tuple[Path, dict]:
    """The pre-ducked audio for the renderer (source timeline, 48 kHz stereo) and a small report. `stems` = (vocals, rest) when the
    separation already ran (it is started early, in parallel with the planner, because it is the slowest step)."""
    vocals, rest = stems if stems else separate_on_modal(src, work, meter)
    orig = work / "orig48.wav"
    ffx.extract_wav(src, orig, rate=SR, channels=2)
    out = work / "duck.wav"
    info = make_duck_wav(orig, vocals, rest, duck_db, out)
    return out, info
