"""Built during the Meta Global AI Developer Hackathon (new work).

Helpers for the "study any reel" (link) flow, which is the baseline's own flow switched on by STUDIO_ALLOW_LINKS (default off):

  * the friendly messages for a link that cannot be fetched (blocked by Instagram, too slow, too long, too many today);
  * `fetch_linked_reel`: the baseline's metadata lookup and download (Apify first, yt-dlp second), run in a worker thread with a time limit
    so a slow download never blocks the server and never hangs a request;
  * `is_studied`: a reel the creator studies (intent "competitor") is someone else's video. It gets no auto-edit, no Creator DNA personalisation,
    is never compared with the creator's own reels, and only its result text is kept (the downloaded file is deleted).

Nothing here changes the baseline download code; it only wraps it.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

BLOCKED_MESSAGE = "Instagram blocked this link, so Prysm could not fetch the reel. Upload the video file instead."
TIMEOUT_MESSAGE = "Fetching this reel took too long, so Prysm stopped. Try again in a moment, or upload the video file instead."
DAILY_LIMIT_MESSAGE = "You've reached today's limit for studying reels. Try again tomorrow, or upload your own video."
SWITCHED_OFF_MESSAGE = "Studying reels from a link is switched off in this deployment. Please upload your video instead."


def is_studied(intent: str | None) -> bool:
    """True for a reel the creator is studying (someone else's video)."""
    return (intent or "") == "competitor"


def _fetch(url: str, work_dir: str):
    from app.services.prysm_analyse_service import download_video, extract_post_metadata
    metadata = extract_post_metadata(url)
    return metadata, download_video(url, work_dir, metadata=metadata)


async def fetch_linked_reel(url: str, work_dir: str, timeout_s: float):
    """(post_metadata, video_path). Raises LinkError(message) for any failure, with a message that is safe to show."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(_fetch, url, work_dir), timeout=timeout_s)
    except asyncio.TimeoutError:
        log.warning("[Links] download timed out after %ss", timeout_s)
        raise LinkError(TIMEOUT_MESSAGE) from None
    except Exception as e:   # noqa: BLE001 - whatever the downloader raised, the user gets one plain message
        log.warning("[Links] download failed: %s", type(e).__name__)
        raise LinkError(BLOCKED_MESSAGE) from None


class LinkError(Exception):
    """A linked reel could not be fetched; `str(e)` is the message to show the user."""
