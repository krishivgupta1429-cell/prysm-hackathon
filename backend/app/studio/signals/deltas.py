"""Built during the Meta Global AI Developer Hackathon (new work). Existing models and services, new integration:
the models and vendors wrapped here existed before the hackathon; only this integration code is new.

Compare two signal sets (before / after an edit): what Stage 2's Verify step uses.

A SignalSet holds one pooled embedding per model plus a few audio, shot and music facts. `compare`
returns, after minus before:
  - cosine distance per model (0 = identical direction, 1 = unrelated, 2 = opposite)
  - audio deltas: voice-to-music ratio (dB), integrated loudness (LU), silence ratio
  - cut-rate change (cuts per 10 s)
  - whether the identified track changed (None when it cannot be said)
Models that are missing on one side, or where a stand-in model was used on only one side, are listed
under "skipped" instead of being compared.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class SignalSet:
    embeddings: dict[str, np.ndarray] = field(default_factory=dict)  # model key -> pooled 1-D vector
    stand_ins: dict[str, str] = field(default_factory=dict)  # model key -> stand-in model id, if any
    voice_to_music_db: float | None = None
    loudness_lufs: float | None = None
    silence_ratio: float | None = None
    cuts_per_10s: float | None = None
    track_id: str | None = None  # ACRCloud acrid of the identified track
    track_matched: bool | None = None  # False = looked and found nothing, None = not looked


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    """1 - cosine similarity of two vectors (0.0 for identical direction)."""
    a, b = np.asarray(a, dtype=np.float64).ravel(), np.asarray(b, dtype=np.float64).ravel()
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch: {a.shape} vs {b.shape}")
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        raise ValueError("zero vector")
    return float(max(0.0, 1.0 - float(np.dot(a, b)) / (na * nb)))


def _delta(after: float | None, before: float | None) -> float | None:
    return None if after is None or before is None else round(after - before, 4)


def compare(before: SignalSet, after: SignalSet) -> dict:
    distances: dict[str, float] = {}
    skipped: dict[str, str] = {}
    for key in sorted(set(before.embeddings) | set(after.embeddings)):
        if key not in before.embeddings or key not in after.embeddings:
            skipped[key] = "missing on one side"
        elif before.stand_ins.get(key) != after.stand_ins.get(key):
            skipped[key] = "different models on each side (stand-in used on one)"
        else:
            try:
                distances[key] = round(cosine_distance(before.embeddings[key], after.embeddings[key]), 6)
            except ValueError as e:
                skipped[key] = str(e)
    if before.track_matched and after.track_matched:
        track_changed: bool | None = before.track_id != after.track_id
    elif before.track_matched is None or after.track_matched is None:
        track_changed = None  # no music ID on one side
    else:  # at least one side found nothing: changed only if exactly one found a track
        track_changed = bool(before.track_matched) != bool(after.track_matched)
    return {
        "cosine_distance": distances,
        "skipped": skipped,
        "audio": {"voice_to_music_db": _delta(after.voice_to_music_db, before.voice_to_music_db),
                  "loudness_lufs": _delta(after.loudness_lufs, before.loudness_lufs),
                  "silence_ratio": _delta(after.silence_ratio, before.silence_ratio)},
        "cut_rate_change_per_10s": _delta(after.cuts_per_10s, before.cuts_per_10s),
        "track_changed": track_changed,
    }


def load_signal_set(run_dir: Path) -> SignalSet:
    """Read a SignalSet back from a run folder (report.json plus the .npy files in signals/)."""
    report = json.loads((run_dir / "report.json").read_text())["stages"]
    sig, music = report.get("signals", {}), report.get("music_id", {})
    out = SignalSet()
    for key, model in (sig.get("models") or {}).items():
        files = [f for f in model.get("files", []) if f.endswith(".npy")]
        pooled = next((f for f in files if "frames" not in f), None)
        if model.get("status") == "ok" and pooled and (run_dir / pooled).exists():
            out.embeddings[key] = np.load(run_dir / pooled)
            if model.get("stand_in"):
                out.stand_ins[key] = model["stand_in"]
    out.voice_to_music_db = (sig.get("demucs") or {}).get("voice_to_music_db")
    out.loudness_lufs = (sig.get("audio_features") or {}).get("integrated_loudness_lufs")
    out.silence_ratio = (sig.get("audio_features") or {}).get("silence_ratio")
    out.cuts_per_10s = (sig.get("cuts") or {}).get("cuts_per_10s")
    if music.get("status") == "ok":
        out.track_matched = bool(music.get("matched"))
        out.track_id = ((music.get("track") or {}).get("acrid"))
    return out
