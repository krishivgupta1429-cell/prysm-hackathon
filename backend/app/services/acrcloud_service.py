"""
app/services/acrcloud_service.py
Song identification via ACRCloud audio fingerprinting.

ACRCloud is a Shazam-equivalent service — identifies songs from audio clips
even with background noise, speech mixed in, or partial audio.
Free tier: 5,000 recognitions/month.

Used in the Prysm analysis pipeline BEFORE Gemini audio analysis:
  1. ACRCloud fingerprints the audio → confirmed song title + artist
  2. That confirmed song is passed as a hard fact to Gemini + Claude
  3. No more guessing — Gemini no longer needs to identify the song,
     just describe genre/mood/energy

Returns {} if no song found or credentials not configured.
"""

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Only trust matches at or above this confidence score.
# ACRCloud scores 0-100. Below 60 = unreliable / likely wrong match.
_MIN_CONFIDENCE = 60


def identify_song(audio_path: str, start_seconds: float = 0) -> Dict[str, Any]:
    """
    Fingerprint an audio file with ACRCloud and return song metadata.

    Uses the pyacrcloud SDK's native fingerprinter — no FFmpeg pre-extraction needed,
    the SDK reads a 10s window directly from the file at `start_seconds`.

    Returns:
        {"found": True, "title": ..., "artist": ..., "confidence": ...}
        {"found": False, "reason": ...}  — no match or below confidence threshold
        {}                               — credentials missing or error
    """
    from app.core.config import settings
    from acrcloud.recognizer import ACRCloudRecognizer

    host = settings.ACRCLOUD_HOST
    access_key = settings.ACRCLOUD_ACCESS_KEY
    access_secret = settings.ACRCLOUD_ACCESS_SECRET

    if not all([host, access_key, access_secret]):
        logger.warning("[ACRCloud] Credentials not configured — skipping song ID")
        return {}

    if not os.path.exists(audio_path):
        logger.warning(f"[ACRCloud] Audio file not found: {audio_path}")
        return {}

    try:
        recognizer = ACRCloudRecognizer({
            "host": host,
            "access_key": access_key,
            "access_secret": access_secret,
            "timeout": 10,
        })

        # SDK reads a 10s fingerprint window starting at start_seconds — no temp file needed
        raw = recognizer.recognize_by_file(audio_path, start_seconds, rec_length=10)
        result = json.loads(raw)

        status = result.get("status", {})
        code = status.get("code", -1)

        if code != 0:
            msg = status.get("msg", "no match")
            logger.info(f"[ACRCloud] No match at {start_seconds}s: {msg} (code={code})")
            return {"found": False, "reason": msg}

        music_list = result.get("metadata", {}).get("music", [])
        if not music_list:
            return {"found": False, "reason": "empty music list"}

        song = music_list[0]
        score = song.get("score", 0)

        # Reject low-confidence matches — they are wrong guesses, not real IDs
        if score < _MIN_CONFIDENCE:
            logger.info(f"[ACRCloud] Match at {start_seconds}s rejected — score {score} < {_MIN_CONFIDENCE} (title='{song.get('title')}')")
            return {"found": False, "reason": f"low confidence ({score})"}

        title = song.get("title", "")
        artists = song.get("artists", [])
        artist = ", ".join(a.get("name", "") for a in artists) if artists else ""
        album = (song.get("album") or {}).get("name", "")
        release_date = song.get("release_date", "")

        external_metadata = song.get("external_metadata", {})
        spotify_track_id = ((external_metadata.get("spotify") or {}).get("track") or {}).get("id", "")
        youtube_vid = (external_metadata.get("youtube") or {}).get("vid", "")

        logger.info(
            f"[ACRCloud] MATCH at {start_seconds}s: '{title}' by '{artist}' "
            f"(score={score}, album='{album}')"
        )
        return {
            "found": True,
            "title": title,
            "artist": artist,
            "album": album,
            "release_date": release_date,
            "confidence": score,
            "spotify_track_id": spotify_track_id,
            "youtube_vid": youtube_vid,
        }

    except Exception as e:
        logger.error(f"[ACRCloud] Error at offset {start_seconds}s: {e}")
        return {}


def identify_song_multi_sample(audio_path: str) -> Dict[str, Any]:
    """
    Try ACRCloud at 2 offsets — return the highest-confidence match above threshold.
    Keeps total wall time under ~20s to avoid blocking the analysis pipeline.
    """
    best: Dict[str, Any] = {}

    for offset in [0, 10]:
        result = identify_song(audio_path, start_seconds=offset)
        if result.get("found"):
            confidence = result.get("confidence", 0)
            if not best or confidence > best.get("confidence", 0):
                best = result
            if confidence >= 85:
                break  # Very confident — no need to try more offsets

    return best
