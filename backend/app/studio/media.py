"""Hackathon-built (Meta Global AI Developer Hackathon).

ffmpeg/ffprobe media prep. The original video is only ever read, never modified.

Produces, under <work>/<stem>/media/:
  proxy.720p.mp4   720p-height H.264 + AAC proxy (capped at 90 s) for Spark
  audio.wav        16 kHz mono wav for speech models
  frames/          1 fps jpgs, 512 px high
  sam.<fps>fps.mp4 720p silent clip for SAM, 10 fps by default (billed per frame, so keep it light)
"""
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from app.studio import config


@dataclass
class VideoInfo:
    duration_s: float
    width: int
    height: int
    fps: float
    has_audio: bool


@dataclass
class Media:
    proxy: Path | None = None
    wav: Path | None = None
    frames_dir: Path | None = None
    sam_clip: Path | None = None
    errors: dict[str, str] = field(default_factory=dict)  # artifact -> ffmpeg error for failed jobs


def _run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {r.stderr.strip()[-500:]}")
    return r.stdout


def probe(path: Path) -> VideoInfo:
    """Duration, display dimensions (rotation applied), fps and audio presence via ffprobe."""
    data = json.loads(_run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]))
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError(f"no video stream in {path.name}")
    width, height = int(video["width"]), int(video["height"])
    rotation = video.get("tags", {}).get("rotate")
    for side in video.get("side_data_list", []):
        rotation = side.get("rotation", rotation)
    if rotation is not None and abs(int(float(rotation))) % 180 == 90:
        width, height = height, width
    num, _, den = video.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    duration = float(video.get("duration") or data["format"].get("duration") or 0)
    return VideoInfo(duration_s=round(duration, 3), width=width, height=height, fps=round(fps, 3),
                     has_audio=any(s.get("codec_type") == "audio" for s in data["streams"]))


def _scale() -> str:
    return f"scale=-2:'min({config.PROXY_HEIGHT},ih)'"  # 720p height, never upscale


def _ffmpeg(src: Path, out: Path, *args: str) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-t", str(config.MEDIA_MAX_SECONDS),
          "-i", str(src), *args, str(out)])
    return out


def prepare(video: Path, media_dir: Path, want: set[str], has_audio: bool = True,
            sam_fps: float = config.DEFAULT_SAM_FPS) -> Media:
    """Build the requested artifacts ("proxy", "wav", "frames", "sam") in parallel."""
    media = Media()
    jobs = {}
    if "proxy" in want:
        jobs["proxy"] = lambda: _ffmpeg(video, media_dir / "proxy.720p.mp4", "-vf", _scale(), "-c:v", "libx264",
                                        "-crf", "28", "-preset", "veryfast", "-c:a", "aac", "-b:a", "96k",
                                        "-movflags", "+faststart")
    if "wav" in want and has_audio:
        jobs["wav"] = lambda: _ffmpeg(video, media_dir / "audio.wav", "-vn", "-ac", "1", "-ar", "16000")
    if "frames" in want:
        jobs["frames"] = lambda: (_ffmpeg(video, media_dir / "frames" / "frame_%03d.jpg", "-vf",
                                          "fps=1,scale=-2:512", "-q:v", "4") and media_dir / "frames")
    if "sam" in want:
        jobs["sam"] = lambda: _ffmpeg(video, media_dir / f"sam.{sam_fps:g}fps.mp4", "-an", "-vf",
                                      f"fps={sam_fps:g},{_scale()}", "-c:v", "libx264", "-crf", "28",
                                      "-preset", "veryfast")
    with ThreadPoolExecutor(max_workers=max(len(jobs), 1)) as pool:
        futures = {name: pool.submit(fn) for name, fn in jobs.items()}
        for name, fut in futures.items():
            try:
                path = fut.result()
            except Exception as e:  # one failed artifact must not block the others
                media.errors[name] = str(e)
                continue
            setattr(media, {"proxy": "proxy", "wav": "wav", "frames": "frames_dir", "sam": "sam_clip"}[name], path)
    return media
