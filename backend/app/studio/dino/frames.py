"""Frame sampling with FFmpeg for the DINOv2 features. Frames are JPEGs with a short side of about 224 px.

    sample_fps(video, fps=4)         -> [(t, jpeg_bytes)]   for the change curve
    sample_even(video, n=8)          -> [(t, jpeg_bytes)]   evenly spaced, for reel matching
    frames_at(video, [t, ...])       -> [jpeg_bytes | None] one frame per timestamp (render check)
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

SHORT_SIDE = 224
JPEG_Q = 4  # ffmpeg -q:v (2 best .. 31 worst)


class FrameError(Exception):
    pass


def _run(cmd: list[str], timeout: int = 180) -> bytes:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise FrameError("ffmpeg is not installed")
    except subprocess.TimeoutExpired:
        raise FrameError("ffmpeg timed out")
    if p.returncode != 0:
        raise FrameError("ffmpeg failed: " + p.stderr.decode("utf-8", "replace")[-200:].strip())
    return p.stdout


def probe(video: str) -> dict:
    """{"duration", "width", "height", "has_audio"} from ffprobe. Raises FrameError if the file is not a readable video."""
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                "-of", "json", str(video)], timeout=30)
    d = json.loads(out or b"{}")
    streams = d.get("streams") or []
    vs = [s for s in streams if s.get("codec_type") == "video"]
    if not vs:
        raise FrameError("no video stream")
    try:
        duration = float((d.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        raise FrameError("unknown duration")
    return {"duration": duration, "width": int(vs[0].get("width") or 0), "height": int(vs[0].get("height") or 0),
            "has_audio": any(s.get("codec_type") == "audio" for s in streams)}


def _scale_filter() -> str:
    # short side 224, keep the aspect ratio, even sizes
    return f"scale='if(gt(iw,ih),-2,{SHORT_SIDE})':'if(gt(iw,ih),{SHORT_SIDE},-2)'"


def sample_fps(video: str, fps: float = 4.0, max_seconds: float | None = None) -> list[tuple[float, bytes]]:
    """Frames at `fps` frames per second, [(timestamp_seconds, jpeg)]. Frame k is at k/fps."""
    with tempfile.TemporaryDirectory(prefix="dino_frames_") as tmp:
        cmd = ["ffmpeg", "-v", "error", "-i", str(video)]
        if max_seconds:
            cmd += ["-t", str(max_seconds)]
        cmd += ["-an", "-vf", f"fps={fps},{_scale_filter()}", "-q:v", str(JPEG_Q), os.path.join(tmp, "f_%05d.jpg")]
        _run(cmd)
        files = sorted(Path(tmp).glob("f_*.jpg"))
        if not files:
            raise FrameError("no frames could be read")
        return [(round(k / fps, 3), p.read_bytes()) for k, p in enumerate(files)]


def even_timestamps(duration: float, n: int = 8) -> list[float]:
    """n timestamps at the centres of n equal parts of the video (never the very first or last frame)."""
    n = max(1, int(n))
    return [round(duration * (k + 0.5) / n, 3) for k in range(n)]


def frames_at(video: str, timestamps: list[float]) -> list[bytes | None]:
    """One frame near each timestamp (input-seeking, so it is a few ms accurate on short GOPs). None if it failed."""
    out: list[bytes | None] = []
    for t in timestamps:
        try:
            data = _run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-an", "-frames:v", "1",
                         "-vf", _scale_filter(), "-q:v", str(JPEG_Q), "-f", "image2pipe", "-vcodec", "mjpeg", "-"], timeout=60)
            out.append(data if data[:2] == b"\xff\xd8" else None)
        except FrameError:
            out.append(None)
    return out


def sample_even(video: str, n: int = 8) -> list[tuple[float, bytes]]:
    dur = probe(video)["duration"]
    ts = even_timestamps(dur, n)
    jpgs = frames_at(video, ts)
    got = [(t, j) for t, j in zip(ts, jpgs) if j]
    if not got:
        raise FrameError("no frames could be read")
    return got
