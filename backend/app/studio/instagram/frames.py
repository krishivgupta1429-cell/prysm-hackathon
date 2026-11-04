"""Built during the Meta Global AI Developer Hackathon (new work).

Temporary frame extraction: a reel is downloaded to a private temp folder, about six JPEG frames are cut from it, the
duration is read, and the folder (video and frames) is deleted. Only the base64 frames returned here (in memory) go to the
model; nothing is written to storage.
"""
import asyncio
import base64
import json
import shutil
import tempfile
from pathlib import Path
from typing import Awaitable, Callable, Optional

N_FRAMES = 6
FRAME_WIDTH = 480


async def _run(*cmd: str) -> tuple[int, bytes]:
    p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await p.communicate()
    return p.returncode or 0, out


async def probe_duration(path: str) -> Optional[float]:
    rc, out = await _run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path)
    if rc != 0:
        return None
    try:
        d = float(json.loads(out)["format"]["duration"])
        return d if d > 0 else None
    except (KeyError, ValueError, TypeError):
        return None


async def extract_frames(path: str, out_dir: str, duration: Optional[float], n: int = N_FRAMES) -> list[bytes]:
    """n JPEG frames spread evenly over the video (skipping the very first and last instants)."""
    dur = duration or 6.0
    frames: list[bytes] = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        target = str(Path(out_dir) / f"f{i}.jpg")
        rc, _ = await _run("ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                           "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "5", target)
        p = Path(target)
        if rc == 0 and p.exists() and p.stat().st_size > 0:
            frames.append(p.read_bytes())
    return frames


async def read_reel(fetch: Callable[[str], Awaitable[bool]], n: int = N_FRAMES, on_video: Optional[Callable] = None) -> tuple[list[bytes], Optional[float], object]:
    """fetch(dest_path) downloads the video. Returns (frames, duration_s, extra); ([], None, None) when the download failed.
    `on_video(video_path, tmp_dir)` (optional, async) runs while the video exists, e.g. to transcribe the first seconds; its result is
    `extra`. The temp folder, with the video, the audio and every frame file, is removed in all cases."""
    tmp = tempfile.mkdtemp(prefix="ig_reel_")
    try:
        video = str(Path(tmp) / "reel.mp4")
        if not await fetch(video):
            return [], None, None
        duration = await probe_duration(video)
        frames = await extract_frames(video, tmp, duration, n)
        extra = await on_video(video, tmp) if on_video else None
        return frames, duration, extra
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def frames_from_download(fetch: Callable[[str], Awaitable[bool]], n: int = N_FRAMES) -> tuple[list[bytes], Optional[float]]:
    """(frames, duration_s); ([], None) when the download failed. See read_reel."""
    frames, duration, _ = await read_reel(fetch, n)
    return frames, duration


def to_image_block(jpeg: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(jpeg).decode()}}
