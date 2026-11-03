"""Built during the Meta Global AI Developer Hackathon (new work).

Bake-off of the "Predicted Viewer Behaviour" backends on a few videos, without re-running the analysis pipeline.
Frames are cut with FFmpeg only (3 hook frames in the first 3 s, the rest spread over the video, 8 in all).
For each video: the Qwen2.5-VL zero-shot port and Q-Align on Modal (twice, to check determinism; the first call of
the session shows the cold start), and the baseline heuristic fed with the saved signals of an earlier local run
(vision score and audio energy) when there is one. Writes a JSON file; docs/engagement-bakeoff.md is written from it.

    python ../scripts/engagement_bakeoff.py --out /path/to/bakeoff.json \
        --video saree=/path/Video-57419.mp4 --saved saree=/path/result_cap_saree.json --video ...

Run from backend/. Nothing here is committed with its inputs: videos and saved results stay in samples/.
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    return float(out)


def cut_frames(path: str, out_dir: Path, n: int = 8) -> list:
    dur = duration(path)
    hook = [0.5, 1.5, 2.5]
    rest = n - len(hook)
    span = max(dur - 3.5, 0.5)
    times = hook + [round(3.0 + span * (i + 0.5) / rest, 2) for i in range(rest)]
    frames = []
    for i, t in enumerate(times):
        out = out_dir / f"f{i}.jpg"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", str(min(t, dur - 0.1)), "-i", path,
                        "-frames:v", "1", "-vf", "scale=-2:640", str(out)], check=True)
        frames.append({"timestamp": t, "path": str(out)})
    return frames


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", action="append", required=True, help="name=path")
    ap.add_argument("--saved", action="append", default=[], help="name=path of a saved local result (for the heuristic)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--niche", action="append", default=[], help="name=niche")
    ap.add_argument("--repeats", type=int, default=2)
    args = ap.parse_args()
    from app.services import engagement_service as svc
    from app.studio import engagement_modal as em

    videos = dict(v.split("=", 1) for v in args.video)
    saved = dict(v.split("=", 1) for v in args.saved)
    niches = dict(v.split("=", 1) for v in args.niche)
    results = {}
    for name, path in videos.items():
        work = Path(tempfile.mkdtemp(prefix=f"bakeoff_{name}_"))
        frames = cut_frames(path, work)
        dur = duration(path)
        entry = {"video": Path(path).name, "duration_s": round(dur, 1), "frames": [f["timestamp"] for f in frames], "modal": []}
        for i in range(args.repeats):
            t0 = time.time()
            try:
                r = em.predict(frames, niches.get(name, "general"), dur)
                entry["modal"].append({"run": i + 1, "ok": True, "nawp": r["nawp"], "ecr": r["ecr"], "raw": r.get("raw"),
                                       "qalign": r["qalign"], "wall_s": round(time.time() - t0, 1), **r["call"]})
            except Exception as e:
                entry["modal"].append({"run": i + 1, "ok": False, "error": f"{type(e).__name__}: {e}"[:300],
                                       "wall_s": round(time.time() - t0, 1)})
            print(name, entry["modal"][-1], flush=True)
        # the heuristic, from the saved signals of an earlier local run (the pipeline itself calls it with no signals)
        if name in saved:
            res = json.loads(Path(saved[name]).read_text())["result"]
            h = svc._heuristic_engagement(res.get("visionData"), res.get("energyCurve"),
                                          res.get("aestheticsData") or None, res.get("niche", ""))
            entry["heuristic"] = {k: h.get(k) for k in ("available", "nawp", "ecr", "source", "reason")}
            entry["saved_scores"] = {p["name"]: p["score"] for p in res.get("parameters", [])}
            entry["saved_overall"] = res.get("overallScore")
        results[name] = entry
        Path(args.out).write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
