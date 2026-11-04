"""Render integrity: does each part of the edited video still look like the source it came from?

    render_integrity(original, edited, edl=None, timestamps=None, embed=None) -> dict

For every kept stretch of the edit, a few frames are taken at matching timestamps (edited time t <-> source time s,
from the EDL's cut_segment ops or from explicit pairs), embedded with DINOv2 and compared by cosine similarity. A
segment whose mean similarity is below INTEGRITY_THRESHOLD is flagged: it no longer looks like its source (broken
render, wrong segment, bad crop, over-zoom, black or frozen frame).

The EDL is read as plain JSON ({"ops": [{"type": "cut_segment", "enabled": true, "params": {"start", "end"}}]}), start
and end in SOURCE seconds. This module does not import the edit engine.

THIS DOES NOT DETECT A CUT-OFF FACE. DINOv2's CLS vector is a global description of the picture: a crop that removes
half a face but keeps the background, the clothes and the colours can still score high. Use a face or pose check for
that. The threshold is calibrated on three before/after pairs plus synthetic degradations (docs/dinov2.md): it is an
estimate, not a guarantee.
"""
import numpy as np

# Cosine similarity of CLS embeddings. Calibrated in docs/dinov2.md (calibrate.py); see there for the numbers behind it.
INTEGRITY_THRESHOLD = 0.85
SEGMENT_S = 2.0
SAMPLES_PER_SEGMENT = 3
NOTE = ("Compares each edited stretch with its source at matching timestamps. It flags broken renders, wrong segments, "
        "black or frozen frames and heavy crops or over-zoom. It does NOT detect a cut-off face: a global picture "
        "embedding can stay similar when a face is half cropped.")


def kept_intervals(edl: dict | None, source_duration: float) -> list[tuple[float, float, float]]:
    """[(out_start, out_end, src_start)] after removing the enabled cut_segment ranges of the EDL (source seconds)."""
    cuts = []
    for op in ((edl or {}).get("ops") or []):
        if op.get("type") == "cut_segment" and op.get("enabled", True):
            p = op.get("params") or {}
            try:
                a, b = float(p["start"]), float(p["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if b > a:
                cuts.append((max(0.0, a), min(source_duration, b)))
    cuts.sort()
    merged: list[list[float]] = []
    for a, b in cuts:
        if merged and a <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out, pos, off = [], 0.0, 0.0
    for a, b in merged + [[source_duration, source_duration]]:
        if a > pos + 1e-6:
            out.append((round(off, 3), round(off + (a - pos), 3), round(pos, 3)))
            off += a - pos
        pos = max(pos, b)
    return out


def plan_pairs(edl: dict | None, timestamps: list | None, source_duration: float, edited_duration: float,
               segment_s: float = SEGMENT_S, samples: int = SAMPLES_PER_SEGMENT) -> list[dict]:
    """Segments with matching (edited_t, source_t) sample pairs: [{"start", "end", "pairs": [(out_t, src_t), ...]}]
    (start/end in EDITED seconds). `timestamps` = [(edited_t, source_t), ...] or [{"out", "src"}], grouped in threes."""
    segs = []
    if timestamps:
        pairs = [(float(p["out"]), float(p["src"])) if isinstance(p, dict) else (float(p[0]), float(p[1])) for p in timestamps]
        pairs.sort()
        for i in range(0, len(pairs), samples):
            g = pairs[i:i + samples]
            segs.append({"start": g[0][0], "end": g[-1][0], "pairs": g})
        return segs
    for out_s, out_e, src_s in kept_intervals(edl, source_duration):
        out_e = min(out_e, edited_duration)
        if out_e - out_s < 0.2:
            continue
        n = max(1, int(np.ceil((out_e - out_s) / segment_s - 1e-9)))
        for k in range(n):
            a, b = out_s + k * (out_e - out_s) / n, out_s + (k + 1) * (out_e - out_s) / n
            ts = [a + (b - a) * (j + 0.5) / samples for j in range(samples)]
            segs.append({"start": round(a, 2), "end": round(b, 2), "pairs": [(round(t, 3), round(src_s + (t - out_s), 3)) for t in ts]})
    return segs


def score_segments(segs: list[dict], sims: list[float | None], threshold: float = INTEGRITY_THRESHOLD) -> list[dict]:
    """Attach similarity numbers to the segments. `sims` is flat, one per pair in order (None = frame unreadable)."""
    out, k = [], 0
    for s in segs:
        vals = sims[k:k + len(s["pairs"])]
        k += len(s["pairs"])
        good = [v for v in vals if v is not None]
        if not good:
            out.append({"start": s["start"], "end": s["end"], "similarity": None, "min_similarity": None, "flagged": True,
                        "reason": "no frame could be read"})
            continue
        mean, mn = float(np.mean(good)), float(np.min(good))
        flagged = mean < threshold
        out.append({"start": s["start"], "end": s["end"], "source_start": round(s["pairs"][0][1], 2),
                    "similarity": round(mean, 4), "min_similarity": round(mn, 4), "flagged": flagged,
                    "reason": "no longer looks like its source" if flagged else None})
    return out


def render_integrity(original: str, edited: str, edl: dict | None = None, timestamps: list | None = None, embed=None,
                     threshold: float = INTEGRITY_THRESHOLD, usage: dict | None = None) -> dict:
    """See the module docstring. `embed(list_of_jpeg_bytes) -> (n, 768)` defaults to the Modal client (one request per
    chunk, both videos together, so there is one cold start at most)."""
    from app.studio.dino import frames as fr
    if embed is None:
        from app.studio.dino import client
        embed = lambda jpgs: client.embed_jpegs(jpgs, usage=usage)
    po, pe = fr.probe(original), fr.probe(edited)
    segs = plan_pairs(edl, timestamps, po["duration"], pe["duration"])
    if not segs:
        raise ValueError("nothing to compare (empty edit)")
    flat = [p for s in segs for p in s["pairs"]]
    src_frames = fr.frames_at(original, [p[1] for p in flat])
    out_frames = fr.frames_at(edited, [p[0] for p in flat])
    ok = [i for i in range(len(flat)) if src_frames[i] and out_frames[i]]
    sims: list[float | None] = [None] * len(flat)
    if ok:
        emb = embed([src_frames[i] for i in ok] + [out_frames[i] for i in ok])
        a, b = np.asarray(emb[:len(ok)], dtype=np.float64), np.asarray(emb[len(ok):], dtype=np.float64)
        a /= np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-12)
        b /= np.maximum(np.linalg.norm(b, axis=1, keepdims=True), 1e-12)
        for j, i in enumerate(ok):
            sims[i] = float(np.dot(a[j], b[j]))
    scored = score_segments(segs, sims, threshold)
    flagged = [s for s in scored if s["flagged"]]
    vals = [s["similarity"] for s in scored if s["similarity"] is not None]
    return {"label": "render integrity (estimate)", "available": True, "model": "dinov2-base", "threshold": threshold,
            "segments": scored, "flagged_count": len(flagged), "ok": not flagged,
            "mean_similarity": round(float(np.mean(vals)), 4) if vals else None,
            "min_similarity": round(float(np.min(vals)), 4) if vals else None,
            "original_duration": round(po["duration"], 2), "edited_duration": round(pe["duration"], 2), "note": NOTE}
