"""Calibrates INTEGRITY_THRESHOLD (render integrity) on real before/after pairs plus synthetic degradations.

    python -m app.studio.dino.calibrate <samples_dir> <work_dir> [out.json]     (needs the DINOv2 Modal service; costs a few cents)

<samples_dir> holds <name>_before.mp4, <name>_after.mp4 and <name>_after_edl.json for name in dance, saree, reel291 (read only).
Positives: the real pairs, scored with the real EDL. Negatives are built from the "after" videos (first 24 s) and compared with
their own source at the same timestamps: wrong segment (source offset by 7 s), black frames, 3x over-zoom and a hard
off-centre crop. Legit-zoom controls (1.14x, the planner's punch-in scale, and 1.3x held for the whole clip) show where
ordinary zooms land. Prints the numbers and the threshold that separates the real pairs from the broken renders.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from app.studio.dino import client, frames as fr, integrity

NAMES = ("dance", "saree", "reel291")
CLIP_S = 24.0
VARIANTS = {  # name: ffmpeg video filter applied to the "after" clip (output scaled back to 540x960)
    "black": "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill",
    "over_zoom_3x": "crop=iw/3:ih/3",
    "hard_crop_left_third": "crop=iw/3:ih:0:0",
    "zoom_1.14_held": "crop=iw/1.14:ih/1.14",
    "zoom_1.3_held": "crop=iw/1.3:ih/1.3",
}


def _make_variants(after: str, work: Path, name: str) -> dict[str, str]:
    out = {}
    for v, filt in VARIANTS.items():
        p = work / f"{name}_{v}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-t", str(CLIP_S), "-i", after, "-an", "-vf", f"{filt},scale=540:960,format=yuv420p",
                        "-c:v", "libx264", "-crf", "23", str(p)], check=True)
        out[v] = str(p)
    return out


def _sims(original: str, edited: str, segs: list[dict], usage: dict) -> list[float | None]:
    flat = [p for s in segs for p in s["pairs"]]
    a = fr.frames_at(original, [p[1] for p in flat])
    b = fr.frames_at(edited, [p[0] for p in flat])
    ok = [i for i in range(len(flat)) if a[i] and b[i]]
    emb = client.embed_jpegs([a[i] for i in ok] + [b[i] for i in ok], usage=usage)
    x, y = emb[:len(ok)], emb[len(ok):]
    sims: list[float | None] = [None] * len(flat)
    for j, i in enumerate(ok):
        sims[i] = float(np.dot(x[j], y[j]))
    return sims


def seg_means(segs, sims):
    out, k = [], 0
    for s in segs:
        vals = [v for v in sims[k:k + len(s["pairs"])] if v is not None]
        k += len(s["pairs"])
        if vals:
            out.append(float(np.mean(vals)))
    return out


def run(samples: Path, work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    usage: dict = {}
    res: dict = {"real": {}, "variants": {}, "usage": usage}
    for n in NAMES:
        before, after = str(samples / f"{n}_before.mp4"), str(samples / f"{n}_after.mp4")
        edl = json.loads((samples / f"{n}_after_edl.json").read_text())
        po, pe = fr.probe(before), fr.probe(after)
        segs = integrity.plan_pairs(edl, None, po["duration"], pe["duration"])
        sims = _sims(before, after, segs, usage)
        res["real"][n] = {"segments": seg_means(segs, sims), "samples": [s for s in sims if s is not None], "source_s": round(po["duration"], 1), "edited_s": round(pe["duration"], 1)}
        clip_segs = [s for s in segs if s["end"] <= CLIP_S]
        for v, path in _make_variants(after, work, n).items():
            s2 = _sims(before, path, clip_segs, usage)
            res["variants"].setdefault(v, {})[n] = {"segments": seg_means(clip_segs, s2), "samples": [s for s in s2 if s is not None]}
        # wrong segment: the genuine edited clip against the source 7 s later
        shifted = [{"start": s["start"], "end": s["end"], "pairs": [(o, min(po["duration"] - 0.5, sr + 7.0)) for o, sr in s["pairs"]]} for s in clip_segs]
        s3 = _sims(before, after, shifted, usage)
        res["variants"].setdefault("wrong_segment_+7s", {})[n] = {"segments": seg_means(clip_segs, s3), "samples": [s for s in s3 if s is not None]}
    return res


def summarise(res: dict) -> dict:
    real = np.array([m for r in res["real"].values() for m in r["segments"]])
    real_samples = np.array([m for r in res["real"].values() for m in r["samples"]])
    out = {"real_segments": len(real), "real_min": float(real.min()), "real_p5": float(np.percentile(real, 5)), "real_median": float(np.median(real)),
           "real_sample_min": float(real_samples.min()), "per_pair_min": {n: float(min(r["segments"])) for n, r in res["real"].items()}, "variants": {}}
    for v, per in res["variants"].items():
        segs = np.array([m for r in per.values() for m in r["segments"]])
        out["variants"][v] = {"n": len(segs), "min": float(segs.min()), "median": float(np.median(segs)), "max": float(segs.max())}
    return out


if __name__ == "__main__":
    samples, work = Path(sys.argv[1]), Path(sys.argv[2])
    result = run(samples, work)
    summary = summarise(result)
    if len(sys.argv) > 3:
        Path(sys.argv[3]).write_text(json.dumps({"result": result, "summary": summary}, indent=1))
    print(json.dumps(summary, indent=1))
