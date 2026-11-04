"""Built during the Meta Global AI Developer Hackathon (new work).

Hook words: the first ~12 seconds of a reel's audio, transcribed with Meta Voice Transcribe through the SAME client the analysis
pipeline already uses (MetaClient.transcribe, 16 kHz mono WAV like app/studio/media.py). Only reels that have a video file get
this; a reel without one keeps "unknown". The audio is cut inside the temporary folder that is deleted right after the import
of that reel; only the text is stored.

status: "transcribed" (words found) | "no_speech" (audio read, nothing spoken in the first seconds) | "unknown" (no video, no key,
cost cap reached, or the call failed).
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
HOOK_SECONDS = 12
MAX_CHARS = 240
MIN_WORDS = 2


@dataclass
class HookResult:
    status: str = "unknown"
    text: Optional[str] = None
    cost_usd: float = 0.0
    audio_seconds: float = 0.0


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()[:MAX_CHARS]


async def cut_audio(video_path: str, out_wav: str, seconds: int = HOOK_SECONDS) -> bool:
    p = await asyncio.create_subprocess_exec("ffmpeg", "-y", "-v", "error", "-t", str(seconds), "-i", video_path, "-vn", "-ac", "1",
                                             "-ar", "16000", "-c:a", "pcm_s16le", out_wav,
                                             stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await p.communicate()
    f = Path(out_wav)
    return p.returncode == 0 and f.exists() and f.stat().st_size > 1000   # a header-only file means there is no audio track


async def transcribe_hook(video_path: str, work_dir: str, client=None) -> HookResult:
    """Never raises. `client` is injectable (tests); by default the pipeline's MetaClient."""
    try:
        wav = str(Path(work_dir) / "hook.wav")
        if not await cut_audio(video_path, wav):
            return HookResult("no_speech")            # no audio track at all
        if client is None:
            from app.studio.meta_client import MetaClient
            client = MetaClient()
        call = await asyncio.to_thread(client.transcribe, Path(wav))
        text = clean(call.transcript)
        secs = (call.audio_duration_ms or 0) / 1000
        if len(text.split()) < MIN_WORDS:
            return HookResult("no_speech", None, float(call.cost_usd or 0), secs)
        return HookResult("transcribed", text, float(call.cost_usd or 0), secs)
    except Exception as e:   # missing key, network, rate limit: the reel simply has no hook words
        log.info("[Hook] transcription skipped: %s", type(e).__name__)
        return HookResult("unknown")
