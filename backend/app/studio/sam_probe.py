"""Probe Meta's SAM 3.1 API (video segmentation) over the /responses SSE stream.

Usage (from backend/):
    .venv/bin/python -m app.studio.sam_probe [../samples/prepared/Video-57419.sam.mp4]

Uploads the clip once, runs four prompt/mask variants, saves raw SSE events to
sam_events_<variant>.jsonl next to the clip and prints the structure of what came back.
The API key is read from backend/.env at runtime and never printed or written.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

BASE_URL = "https://api.meta.ai/v1"
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
DEFAULT_CLIP = Path(__file__).resolve().parents[3] / "samples/prepared/Video-57419.sam.mp4"
USD_PER_1000_FRAMES = 0.20
TIMEOUT = httpx.Timeout(600.0, connect=30.0)

KEY = ""
summary: list[dict] = []


def scrub(text: str) -> str:
    return text.replace(KEY, "***") if KEY else text


def clip_info(path: Path) -> tuple[int, int, int]:
    """(width, height, frame count) of the clip, via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=width,height,nb_read_frames", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"]), int(s["nb_read_frames"])


def clip_fps(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=r_frame_rate", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    num, den = out.split("/")
    return int(num) / int(den)


# Each output_text.delta is one frame's detection, e.g.
#   <12f>0<|box;x1=79;y1=179;x2=295;y2=719;w=406;h=720;c=0.96|><|mask;x=0;y=0;c=0.96;data=541,217,<ascii85>|>
# <frame>f = frame index, next number = object id. The mask is cropped to the box (data starts rows,cols,...).
DETECTION = re.compile(r"<(\d+)f>(\d+)<\|box;([^|]*)\|>(?:<\|mask;([^|]*)\|>)?")


def build_timeline(events_path: Path, fps: float) -> dict:
    """Per object per frame: timestamp, bounding box and confidence, from a saved sam_events_*.jsonl."""
    text = ""
    for line in events_path.read_text().splitlines():
        e = json.loads(line)
        if e["event"] == "response.output_text.delta":
            text += e["data"]["delta"]
    objects: dict[str, list[dict]] = {}
    for frame, obj, box, mask in DETECTION.findall(text):
        b = dict(kv.split("=") for kv in box.split(";"))
        entry = {"frame": int(frame), "t": round(int(frame) / fps, 3),
                 "box": [int(b["x1"]), int(b["y1"]), int(b["x2"]), int(b["y2"])],
                 "confidence": round(float(b["c"]), 4)}
        if mask:
            rows, cols = mask.split("data=")[1].split(",")[:2]
            entry["mask_rows_cols"] = [int(rows), int(cols)]
        objects.setdefault(obj, []).append(entry)
    for entries in objects.values():
        entries.sort(key=lambda x: x["frame"])
    return {"source": events_path.name, "fps": fps, "frame_size": [int(b["w"]), int(b["h"])] if objects else None,
            "objects": objects}


def shape(obj, depth: int = 0):
    """Keys and nesting only; strings over 200 chars are truncated, long lists show two items."""
    if isinstance(obj, dict):
        return {k: shape(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        items = [shape(v, depth + 1) for v in obj[:2]]
        return items + [f"... ({len(obj)} items total)"] if len(obj) > 2 else items
    if isinstance(obj, str):
        return obj if len(obj) <= 200 else obj[:200] + f"...[{len(obj)} chars]"
    return obj


def find_keys(obj, needles=("usage", "cost", "token", "price", "billing")) -> dict:
    """Any key whose name hints at usage/cost, with its value (strings truncated)."""
    found = {}

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                if any(n in k.lower() for n in needles):
                    found[".".join(path + [k])] = shape(v)
                walk(v, path + [k])
        elif isinstance(o, list):
            for i, v in enumerate(o[:3]):
                walk(v, path + [str(i)])

    walk(obj, [])
    return found


def upload(clip: Path) -> str:
    r = httpx.post(f"{BASE_URL}/files", headers={"Authorization": f"Bearer {KEY}"}, timeout=TIMEOUT,
                   data={"purpose": "user_data"}, files={"file": ("upload.mp4", clip.read_bytes(), "video/mp4")})
    print(f"upload: status={r.status_code} {len(r.content)} bytes")
    if not r.is_success:
        raise RuntimeError(f"upload failed: {scrub(r.text)}")
    body = r.json()
    print(f"upload response shape: {json.dumps(shape(body))}")
    return body["id"]


# The API rejects "1-bit": metadata["mask_encoding"] must be one of ['lossless', 'one_bit']
MASK_VALUES = {"lossless": "lossless", "1-bit": "one_bit"}


def run_variant(name: str, prompt: str, mask: str, file_id: str, frames: int, out_dir: Path) -> None:
    """One streaming /responses call. Never raises; appends a row to the summary table."""
    mask = MASK_VALUES.get(mask, mask)
    row = {"variant": name, "ok": False, "status": None, "seconds": None, "events": 0, "bytes": 0, "cost": None}
    summary.append(row)
    print("\n" + "=" * 100 + f"\nvariant {name}: prompt={prompt[:70]!r} mask_encoding={mask}\n" + "=" * 100)
    body = {"model": "sam-3.1",
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": prompt}, {"type": "input_video", "file_id": file_id}]}],
            "stream": True,
            "metadata": {"mask_encoding": mask, "include_confidence": "true"}}
    headers = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
               "Accept": "text/event-stream"}
    events: list[dict] = []
    start = time.time()
    try:
        with httpx.stream("POST", f"{BASE_URL}/responses", headers=headers, json=body, timeout=TIMEOUT) as r:
            row["status"] = r.status_code
            if not r.is_success:
                err = scrub(r.read().decode(errors="replace"))
                row["seconds"] = round(time.time() - start, 2)
                print(f"ERR status={r.status_code} full error body:\n{err}")
                return
            event_name = None
            for line in r.iter_lines():
                row["bytes"] += len(line.encode()) + 1
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    raw = line[5:].strip()
                    try:
                        data = json.loads(raw)
                    except Exception:
                        data = raw
                    events.append({"event": event_name or (data.get("type") if isinstance(data, dict) else None),
                                   "data": data})
                    event_name = None
        row["seconds"] = round(time.time() - start, 2)
        row["ok"] = True
    except Exception as e:
        row["seconds"] = round(time.time() - start, 2)
        print(f"ERR {type(e).__name__}: {scrub(str(e))}")

    row["events"] = len(events)
    failed = [e for e in events if e["event"] in ("error", "response.failed")]
    if failed:  # HTTP 200 but the stream itself reports failure
        row["ok"] = False
        err = next((e["data"] for e in failed if e["event"] == "error"), failed[0]["data"])
        print(f"ERR stream reported failure, full error event:\n{json.dumps(err)}")
    (out_dir / f"sam_events_{name}.jsonl").write_text("\n".join(scrub(json.dumps(e)) for e in events) + "\n")
    if not events:
        return
    types = sorted({str(e["event"]) for e in events})
    row["cost"] = round(frames * USD_PER_1000_FRAMES / 1000, 4)
    print(f"status={row['status']} seconds={row['seconds']} events={len(events)} bytes={row['bytes']}")
    print(f"distinct event types: {types}")
    for label, ev in [("event 1", events[0]), ("event 2", events[1] if len(events) > 1 else None),
                      ("event 3", events[2] if len(events) > 2 else None), ("LAST event", events[-1])]:
        if ev:
            print(f"\n{label} structure:\n{json.dumps(shape(ev), indent=2, ensure_ascii=False)}")
    usage = {}
    for i, e in enumerate(events):
        for k, v in find_keys(e["data"]).items():
            usage.setdefault(k, v)
    print(f"\nusage/cost fields found anywhere in the stream: {json.dumps(usage, indent=2) if usage else 'none'}")
    print(f"estimated cost: {frames} frames x $0.20/1000 = ${row['cost']}")


def main() -> None:
    global KEY
    clip = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else DEFAULT_CLIP
    KEY = (dotenv_values(ENV_PATH).get("META_API_KEY") or "").strip()
    if not KEY:
        sys.exit("META_API_KEY not found in backend/.env")
    w, h, frames = clip_info(clip)
    print(f"clip: {clip.name} {w}x{h}, {frames} frames\n")
    out_dir = clip.parent
    try:
        file_id = upload(clip)
    except Exception as e:
        sys.exit(f"upload error: {type(e).__name__}: {scrub(str(e))}")
    print(f"file id: {file_id}")

    box = f"<694f>1<|box;x1=100;y1=100;x2=300;y2=600;w={w};h={h}|>"  # as specified: frame 694 > 80 frames
    box0 = f"<0f>1<|box;x1=100;y1=100;x2=300;y2=600;w={w};h={h}|>"  # same box prompted on frame 0
    for name, prompt, mask in [("a_person_lossless", "person", "lossless"),
                               ("b_person_1bit", "person", "1-bit"),
                               ("c_face_1bit", "face", "1-bit"),
                               ("d_box_1bit", box, "1-bit"),
                               ("e_box_frame0_1bit", box0, "1-bit")]:
        run_variant(name, prompt, mask, file_id, frames, out_dir)

    print("\n" + "=" * 100 + "\nSUMMARY\n" + "=" * 100)
    print(f"{'variant':<20}{'result':<8}{'http':<6}{'seconds':<10}{'events':<8}{'bytes':<12}{'est cost':<10}")
    for r in summary:
        cost = f"${r['cost']}" if r["cost"] is not None else "n/a"
        print(f"{r['variant']:<20}{'OK' if r['ok'] else 'ERR':<8}{str(r['status']):<6}{str(r['seconds']):<10}"
              f"{r['events']:<8}{r['bytes']:<12}{cost:<10}")
    write_timeline(clip)


def write_timeline(clip: Path) -> None:
    src = clip.parent / "sam_events_b_person_1bit.jsonl"
    if not src.exists():
        print("no person events to build a timeline from")
        return
    tl = build_timeline(src, clip_fps(clip))
    out = clip.parent / "sam_timeline_person.json"
    out.write_text(json.dumps(tl, indent=1))
    entries = [(o, e) for o, es in tl["objects"].items() for e in es]
    print(f"\ntimeline: {len(tl['objects'])} object(s), {len(entries)} entries -> {out}\nfirst 5 entries:")
    for o, e in entries[:5]:
        print(f"  object {o}: {json.dumps(e)}")


if __name__ == "__main__":
    main()
