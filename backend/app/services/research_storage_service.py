"""
app/services/research_storage_service.py

R2 artifact storage for the niche-research pipeline (batch6+).
Uploads frames, audio, and raw AI JSON for a single reel analysis run.

Key scheme:
    research-batch/{niche}/{video_id}/frame_{n:02d}.jpg
    research-batch/{niche}/{video_id}/audio.mp3
    research-batch/{niche}/{video_id}/pipeline.json

Returns the prefix and audio key so callers can store them in research_reels.
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def _r2_client():
    from app.core.config import settings
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def _bucket() -> str:
    from app.core.config import settings
    return settings.R2_BUCKET_NAME


def _ready() -> bool:
    from app.core.config import settings
    return bool(settings.R2_ACCOUNT_ID and settings.R2_ACCESS_KEY_ID)


def upload_research_artifacts(
    *,
    niche: str,
    video_id: str,
    frame_paths: List[str],
    audio_path: Optional[str],
    raw_pipeline: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str]]:
    """
    Upload all artifacts for one research reel to R2.

    Args:
        niche:        Niche label, e.g. "fitness"
        video_id:     Stable identifier (cache_key or UUID) for this reel
        frame_paths:  Ordered list of local JPEG frame paths
        audio_path:   Local MP3 path, or None
        raw_pipeline: Pipeline result dict to store as pipeline.json, or None

    Returns:
        (frames_prefix, audio_key)  — R2 key prefix and audio key.
        Either may be None if credentials are missing or the upload failed.
    """
    if not _ready():
        logger.warning("[research_storage] R2 credentials not configured — skipping upload")
        return None, None

    prefix = f"research-batch/{niche}/{video_id}"
    client = _r2_client()
    bucket = _bucket()

    frames_prefix: Optional[str] = None
    audio_key: Optional[str] = None

    # Upload frames
    uploaded_frames = 0
    for i, path in enumerate(frame_paths):
        if not path or not os.path.exists(path):
            continue
        key = f"{prefix}/frame_{i:02d}.jpg"
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=f,
                    ContentType="image/jpeg",
                    ContentLength=size,
                )
            uploaded_frames += 1
        except (ClientError, Exception) as e:
            logger.error("[research_storage] Frame upload failed %s: %s", key, e)

    if uploaded_frames > 0:
        frames_prefix = f"{prefix}/"
        logger.info("[research_storage] Uploaded %d frames → %s", uploaded_frames, frames_prefix)

    # Upload audio
    if audio_path and os.path.exists(audio_path):
        key = f"{prefix}/audio.mp3"
        try:
            size = os.path.getsize(audio_path)
            with open(audio_path, "rb") as f:
                client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=f,
                    ContentType="audio/mpeg",
                    ContentLength=size,
                )
            audio_key = key
            logger.info("[research_storage] Uploaded audio → %s", key)
        except (ClientError, Exception) as e:
            logger.error("[research_storage] Audio upload failed %s: %s", key, e)

    # Upload raw pipeline JSON
    if raw_pipeline is not None:
        key = f"{prefix}/pipeline.json"
        try:
            payload = json.dumps(raw_pipeline, default=str).encode()
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=payload,
                ContentType="application/json",
                ContentLength=len(payload),
            )
            logger.info("[research_storage] Uploaded pipeline JSON → %s", key)
        except (ClientError, Exception) as e:
            logger.error("[research_storage] Pipeline JSON upload failed %s: %s", key, e)

    return frames_prefix, audio_key
