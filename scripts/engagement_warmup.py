"""Built during the Meta Global AI Developer Hackathon (new work).

Warm up the "prysm-hackathon-engagement" Modal app before a demo: boots one container of each GPU class (Qwen2.5-VL and
Q-Align) with a tiny blank frame so the first real analysis does not pay the cold start. Containers then stay up for
ENGAGEMENT_SCALEDOWN_S (default 240 s) after the last call. Run from backend/:

    python ../scripts/engagement_warmup.py            # warm both, print the times
    python ../scripts/engagement_warmup.py --repeat 5 # call every 3 minutes, 5 times, to hold them warm

The numbers it prints are only timings; the blank frame's prediction is meaningless and is discarded.
"""
import argparse
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def blank_frames():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (360, 640), (128, 128, 128)).save(buf, "JPEG")
    return [{"timestamp": 0.5, "jpg": buf.getvalue()}]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--every", type=int, default=180, help="seconds between repeats (below ENGAGEMENT_SCALEDOWN_S)")
    args = ap.parse_args()
    from concurrent.futures import ThreadPoolExecutor
    from app.studio import engagement_modal as em
    frames = blank_frames()
    for i in range(args.repeat):
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=2) as pool:
            fq = pool.submit(em._remote, em.QWEN_CLASS, "predict", frames=frames, niche="general", duration=10.0)
            fa = pool.submit(em._remote, em.QALIGN_CLASS, "score", frames=frames)
            for name, f in (("qwen", fq), ("qalign", fa)):
                try:
                    r = f.result()
                    print(f"{name}: {'cold' if r['cold'] else 'warm'} gpu_seconds={r['gpu_seconds']} boot={r['boot_seconds']}s")
                except Exception as e:  # a blank frame may not parse as a prediction; the container is warm anyway
                    print(f"{name}: call ended with {type(e).__name__} (the container is still warm)")
        print(f"round {i + 1}: {time.time() - t0:.1f}s")
        if i + 1 < args.repeat:
            time.sleep(args.every)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
