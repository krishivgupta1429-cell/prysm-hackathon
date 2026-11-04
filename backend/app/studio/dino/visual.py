"""Visual change curve from DINOv2 CLS embeddings.

    visual_change_curve(video, embed=...)  -> dict   (plain JSON; embed is replaceable so tests use mocked embeddings)
    change_curve_from_embeddings(emb, fps) -> dict   (pure)

Method: frames at 4 fps -> unit-length CLS vectors -> cosine distance between consecutive frames -> the larger of the
two distances that fall into each 0.5 s bin. Scores are 0-1 per video (divided by the video's 95th percentile, with a
floor so a nearly still video does not get stretched). Static segments use the RAW distance (not the normalised
score) so that "nothing changes" means the same thing in every video.

Honest limits: this is "visual change (estimate)". It measures how different consecutive frames look to DINOv2, not
whether the change is interesting. Camera shake and a cut look alike; a talking head with small movements reads as low
change; compression noise sets a floor.
"""
import numpy as np

LABEL = "visual change (estimate)"
FPS = 4.0
BIN_S = 0.5
# Raw cosine distance (1 - cosine similarity) between frames 0.25 s apart.
# Calibrated on real clips, see docs/dinov2.md ("Static segments").
STATIC_RAW_THRESHOLD = 0.02
STATIC_MIN_S = 1.5
SCALE_FLOOR = 0.10  # a video whose 95th-percentile change is below this is treated as nearly still, not stretched


def as_unit_rows(emb) -> np.ndarray:
    a = np.asarray(emb, dtype=np.float64)
    if a.ndim != 2 or a.shape[0] == 0:
        raise ValueError("embeddings must be a non-empty 2-D array")
    n = np.linalg.norm(a, axis=1, keepdims=True)
    return a / np.maximum(n, 1e-12)


def consecutive_distances(emb) -> np.ndarray:
    """Cosine distance between neighbouring rows, shape (n-1,)."""
    u = as_unit_rows(emb)
    return np.clip(1.0 - np.sum(u[1:] * u[:-1], axis=1), 0.0, 2.0)


def static_segments_from_raw(raw: np.ndarray, bin_s: float = BIN_S, threshold: float = STATIC_RAW_THRESHOLD,
                             min_s: float = STATIC_MIN_S, duration: float | None = None) -> list[dict]:
    """Runs of bins below `threshold` lasting at least `min_s` seconds -> [{"start", "end", "duration"}]."""
    out, run_start = [], None
    n = len(raw)
    for i in range(n + 1):
        low = i < n and raw[i] < threshold
        if low and run_start is None:
            run_start = i
        if not low and run_start is not None:
            start, end = run_start * bin_s, i * bin_s
            if duration is not None:
                end = min(end, duration)
            if end - start >= min_s - 1e-9:
                out.append({"start": round(start, 2), "end": round(end, 2), "duration": round(end - start, 2)})
            run_start = None
    return out


def change_curve_from_embeddings(emb, fps: float = FPS, bin_s: float = BIN_S, duration: float | None = None) -> dict:
    emb = np.asarray(emb)
    if emb.shape[0] < 2:
        raise ValueError("need at least two frames for a change curve")
    d = consecutive_distances(emb)
    # distance k lies between frame k and k+1; the bin is the one holding the middle of that interval
    mids = (np.arange(len(d)) + 0.5) / fps
    idx = np.floor(mids / bin_s + 1e-9).astype(int)
    n_bins = int(idx.max()) + 1
    raw = np.zeros(n_bins)
    for b, v in zip(idx, d):
        raw[b] = max(raw[b], v)
    scale = max(float(np.percentile(raw, 95)), SCALE_FLOOR)
    scores = np.clip(raw / scale, 0.0, 1.0)
    if duration is None:
        duration = emb.shape[0] / fps
    return {
        "label": LABEL, "available": True, "model": "dinov2-base", "fps": fps, "bin_s": bin_s, "duration": round(float(duration), 2),
        "times": [round(i * bin_s, 2) for i in range(n_bins)],
        "scores": [round(float(x), 3) for x in scores],
        "raw": [round(float(x), 4) for x in raw],
        "scale": round(scale, 4),
        "static_threshold_raw": STATIC_RAW_THRESHOLD, "static_min_s": STATIC_MIN_S,
        "static_segments": static_segments_from_raw(raw, bin_s, duration=float(duration)),
    }


def visual_change_curve(video: str, embed=None, fps: float = FPS, max_seconds: float = 120.0) -> dict:
    """The curve for a video file. `embed(list_of_jpeg_bytes) -> (n, 768) array` defaults to the Modal client.
    Raises DinoUnavailable when the model service is off or fails (the caller reports available=false)."""
    from app.studio.dino import frames as fr
    if embed is None:
        from app.studio.dino import client
        embed = client.embed_jpegs
    info = fr.probe(video)
    frames = fr.sample_fps(video, fps=fps, max_seconds=max_seconds)
    emb = embed([j for _, j in frames])
    return change_curve_from_embeddings(emb, fps=fps, duration=min(info["duration"], max_seconds))
