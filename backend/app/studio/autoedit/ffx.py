"""Built during the Meta Global AI Developer Hackathon (new work).

FFmpeg / ffprobe helpers: probing, audio extraction, scene cuts, silence map, loudness, black and frozen frame detection, frames.
Everything shells out to the ffmpeg chosen by config.ffmpeg_bin() (one with libass); results are plain dicts / lists.
"""
import json
import re
import subprocess
from pathlib import Path

from app.studio.autoedit import config


class FFmpegError(RuntimeError):
    pass


def run(args: list[str], timeout: float = 900) -> subprocess.CompletedProcess:
    cmd = [config.ffmpeg_bin(), "-hide_banner", "-nostdin", *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"ffmpeg exit timeout: no result after {timeout:.0f} s (the process was stopped)") from None
    if p.returncode != 0:
        raise FFmpegError(_why(p))
    return p


def _why(p: subprocess.CompletedProcess) -> str:
    """A readable failure: the exit code first (a negative one means the process was killed, e.g. by the memory limit), then the
    last line ffmpeg printed, then the tail of its log. The creator-facing error shows only the first ~300 characters."""
    lines = [ln for ln in (p.stderr or "").strip().splitlines() if ln.strip()]
    sig = -p.returncode if p.returncode < 0 else 9 if p.returncode == 137 else 0   # 137 = 128 + SIGKILL, what a shell reports
    killed = f" (killed by signal {sig}, probably out of memory)" if sig else ""
    return f"ffmpeg exit {p.returncode}{killed}: {(lines[-1] if lines else 'no output')[:160]} || " + (p.stderr or "")[-1200:]


def run_stderr(args: list[str], timeout: float = 600) -> str:
    """Run an analysis pass (null output) and return stderr, where the filters print their findings."""
    cmd = [config.ffmpeg_bin(), "-hide_banner", "-nostdin", *args]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise FFmpegError(_why(p))
    return p.stderr


def probe(path: str | Path) -> dict:
    p = subprocess.run([config.ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {p.stderr[-300:]}")
    j = json.loads(p.stdout)
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v:
        raise FFmpegError("no video stream")
    num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    rot = rot or int((v.get("tags") or {}).get("rotate", 0) or 0)
    if abs(rot) in (90, 270):
        w, h = h, w
    duration = float(j.get("format", {}).get("duration") or v.get("duration") or 0)
    return {"duration": duration, "width": w, "height": h, "fps": round(fps, 3) if 1 < fps < 120 else 30.0,
            "has_audio": a is not None, "orientation": "portrait" if h >= w else "landscape",
            "is_9x16": abs(w / h - 9 / 16) < 0.02, "video_codec": v.get("codec_name")}


def extract_wav(src: str | Path, dst: str | Path, rate: int = 16000, channels: int = 1) -> Path:
    run(["-y", "-i", str(src), "-vn", "-ac", str(channels), "-ar", str(rate), str(dst)])
    return Path(dst)


def scene_cuts(path: str | Path, threshold: float = 0.3) -> list[float]:
    """Hard cut times (seconds) found with the `scdet`-style select filter."""
    err = run_stderr(["-i", str(path), "-an", "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"])
    times = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", err)]
    return [round(t, 3) for t in times if t > 0.15]


def silence_threshold(lufs: float | None, p10_db: float | None = None) -> float:
    """The level under which audio counts as silence (silencedetect compares PEAK amplitude, so room noise needs ~16 dB of crest margin above
    its RMS level). A normal-loudness file (-20 LUFS or louder) keeps the usual -35 dB. A quiet recording is judged against its own noise:
    the quietest 10 % of the audio (`p10_db`, RMS) + 16 dB, between -48 and -32 dB; a threshold fixed at -35 would call quiet speech silence
    and one scaled only by loudness could sit under the room noise and find no pause at all."""
    if lufs is None or lufs >= -20.0 or p10_db is None:
        return -35.0
    return round(min(max(p10_db + 16.0, -48.0), -32.0), 1)


def silences(path: str | Path, noise_db: float = -35.0, min_len: float = 0.3) -> list[dict]:
    err = run_stderr(["-i", str(path), "-vn", "-af", f"silencedetect=noise={noise_db}dB:d={min_len}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: (-?[0-9.]+)", err)]
    ends = [(float(a), float(b)) for a, b in re.findall(r"silence_end: ([0-9.]+) \| silence_duration: ([0-9.]+)", err)]
    out = []
    for i, (end, dur) in enumerate(ends):
        start = starts[i] if i < len(starts) else end - dur
        out.append({"start": round(max(0.0, start), 3), "end": round(end, 3)})
    if len(starts) > len(ends):  # silence running to the end of the file
        out.append({"start": round(starts[-1], 3), "end": None})
    return out


def loudness(path: str | Path) -> dict:
    """Integrated loudness (LUFS), loudness range and true peak (dBTP) of a file's audio with ebur128."""
    err = run_stderr(["-i", str(path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"])
    tail = err[err.rfind("Summary:"):] if "Summary:" in err else err
    def num(pat):
        m = re.search(pat, tail)
        return float(m.group(1)) if m else None
    return {"lufs": num(r"I:\s+(-?[0-9.]+) LUFS"), "lra": num(r"LRA:\s+(-?[0-9.]+) LU"), "true_peak_db": num(r"Peak:\s+(-?[0-9.]+) dBFS")}


def black_frames(path: str | Path) -> list[dict]:
    err = run_stderr(["-i", str(path), "-an", "-vf", "blackdetect=d=0.3:pix_th=0.06", "-f", "null", "-"])
    return [{"start": float(a), "end": float(b)} for a, b in re.findall(r"black_start:([0-9.]+) black_end:([0-9.]+)", err)]


def frozen_frames(path: str | Path) -> list[dict]:
    err = run_stderr(["-i", str(path), "-an", "-vf", "freezedetect=n=-60dB:d=0.8", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"freeze_start: ([0-9.]+)", err)]
    durs = [float(x) for x in re.findall(r"freeze_duration: ([0-9.]+)", err)]
    return [{"start": s, "duration": durs[i] if i < len(durs) else None} for i, s in enumerate(starts)]


def frame_at(path: str | Path, t: float, dst: str | Path, width: int | None = None) -> Path:
    vf = ["-vf", f"scale={width}:-2"] if width else []
    run(["-y", "-ss", f"{max(t, 0):.3f}", "-i", str(path), "-frames:v", "1", "-q:v", "3", *vf, str(dst)])
    return Path(dst)


def frames_evenly(path: str | Path, out_dir: str | Path, n: int, duration: float, width: int = 512) -> list[tuple[float, Path]]:
    out = []
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    for i in range(n):
        t = duration * (i + 0.5) / n
        dst = Path(out_dir) / f"f{i:02d}.jpg"
        frame_at(path, t, dst, width)
        out.append((round(t, 2), dst))
    return out


def make_proxy(src: str | Path, dst: str | Path, height: int = 480, crf: int = 29) -> Path:
    """A small H.264 copy used for sending a video to a model (never delivered to the creator)."""
    run(["-y", "-i", str(src), "-vf", f"scale=-2:{height}", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-movflags", "+faststart", str(dst)])
    return Path(dst)
