"""Built during the Meta Global AI Developer Hackathon (new work).

Shared helpers for the Auto-edit tests: a short generated clip (no network, no sample videos) and a hand-made Inputs."""
import subprocess
from pathlib import Path

from app.studio.autoedit import config
from app.studio.autoedit.inputs import Inputs

WORDS = [("This", 0.40, 0.70), ("is", 0.72, 0.85), ("um", 1.00, 1.30), ("exactly", 1.80, 2.30), ("what", 2.35, 2.55), ("I", 2.57, 2.65),
         ("mean", 2.67, 3.00), ("नमस्ते", 3.30, 3.80), ("दोस्तों", 3.85, 4.40), ("let's", 4.90, 5.20), ("go", 5.22, 5.60)]


def make_clip(path: Path, seconds: float = 6.0, size: str = "360x640", fps: int = 30, tone_db: float = -20.0, audio: bool = True) -> Path:
    """A test pattern with a 440 Hz tone at a known level (so loudness and cuts are measurable)."""
    w, h = size.split("x")
    cmd = [config.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r={fps}:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:r=48000:d={seconds},volume={tone_db}dB", "-c:a", "aac", "-b:a", "128k"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30", str(path)]
    subprocess.run(cmd, check=True)
    return path


def make_inputs(duration: float = 6.0, width: int = 360, height: int = 640, fps: float = 30.0, **kw) -> Inputs:
    words = [{"word": w, "start": s, "end": e} for w, s, e in WORDS]
    base = dict(duration=duration, width=width, height=height, fps=fps, orientation="portrait" if height >= width else "landscape",
                is_9x16=abs(width / height - 9 / 16) < 0.02, has_audio=True, language="Hinglish", words=words,
                segments=[{"text": "This is um exactly what I mean", "start": 0.4, "end": 3.0}, {"text": "नमस्ते दोस्तों let's go", "start": 3.3, "end": 5.6}],
                scene_cuts=[2.0, 4.0], silences=[{"start": 0.0, "end": 0.35}, {"start": 5.7, "end": 6.0}],
                energy_peaks=[{"t": 4.2, "level": 1.0}], has_music=False, has_speech=True,
                analysis_times=[{"t": 4.0, "step_id": "hook#1", "context": "4s"}], fillers=[{"word": "um", "start": 1.0, "end": 1.3}])
    base.update(kw)
    return Inputs(**base)
