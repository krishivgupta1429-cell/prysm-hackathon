"""
app/services/video_service.py
Handles video ingestion:
  - multipart upload → local / S3
  - link download via yt-dlp
  - audio extraction via FFmpeg
  - basic metadata extraction
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional, Tuple
import aiofiles
from fastapi import UploadFile

from app.core.config import settings
from app.models.media import MediaSource

logger = logging.getLogger(__name__)


SUPPORTED_MIME_TYPES = {
    "video/mp4", "video/quicktime", "video/x-msvideo",
    "video/webm", "video/x-matroska", "video/mpeg",
}

MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB

# Magic bytes for known video formats
_VIDEO_MAGIC = [
    (b'\x00\x00\x00', b'ftyp'),    # MP4/MOV — ftyp box at offset 4
    (b'\x1a\x45\xdf\xa3',),         # WebM/MKV — EBML header
    (b'RIFF',),                      # AVI — RIFF header
    (b'\x00\x00\x01\xba',),         # MPEG-PS
    (b'\x00\x00\x01\xb3',),         # MPEG video
]

def _validate_video_magic(data: bytes) -> bool:
    """Check file magic bytes to verify it's actually a video file."""
    if len(data) < 12:
        return False
    # MP4/MOV: bytes 4-7 should be 'ftyp'
    if data[4:8] == b'ftyp':
        return True
    # WebM/MKV
    if data[:4] == b'\x1a\x45\xdf\xa3':
        return True
    # AVI
    if data[:4] == b'RIFF' and data[8:12] == b'AVI ':
        return True
    # MPEG
    if data[:4] in (b'\x00\x00\x01\xba', b'\x00\x00\x01\xb3'):
        return True
    return False


# ── Storage helpers ───────────────────────────────────────────────────────────

def _local_path(filename: str) -> Path:
    base = Path(settings.LOCAL_MEDIA_PATH).resolve()
    base.mkdir(parents=True, exist_ok=True)
    resolved = (base / filename).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError(f"Invalid file path: path traversal detected")
    return resolved


def _public_url(storage_path: str) -> str:
    if settings.CDN_BASE_URL:
        return f"{settings.CDN_BASE_URL}/{storage_path}"
    return f"/media/files/{storage_path}"


async def _save_locally(data: bytes, filename: str) -> Tuple[str, str]:
    """Write bytes to local media storage. Returns (storage_path, public_url)."""
    path = _local_path(filename)
    async with aiofiles.open(path, "wb") as f:
        await f.write(data)
    return str(path), _public_url(filename)


async def _upload_to_s3(data: bytes, key: str, content_type: str) -> Tuple[str, str]:
    """Upload bytes to S3. Returns (s3_key, https_url)."""
    import boto3
    from botocore.exceptions import ClientError

    loop = asyncio.get_event_loop()
    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
    )

    def _upload():
        s3.put_object(
            Bucket=settings.AWS_BUCKET_NAME,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    await loop.run_in_executor(None, _upload)
    url = f"https://{settings.AWS_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/{key}"
    return key, url


# ── Public API ────────────────────────────────────────────────────────────────

async def ingest_upload(file: UploadFile, user_id: str) -> dict:
    """
    Accept a multipart upload, validate, store it, and return media metadata.
    """
    if file.content_type not in SUPPORTED_MIME_TYPES:
        raise ValueError(f"Unsupported file type: {file.content_type}")

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE_BYTES:
        raise ValueError("File exceeds maximum allowed size of 500 MB.")

    # Validate actual file content matches video magic bytes
    if not _validate_video_magic(contents[:12]):
        raise ValueError("File content does not match a supported video format.")

    ext = Path(file.filename).suffix or ".mp4"
    unique_name = f"{user_id}/{uuid.uuid4()}{ext}"

    if settings.STORAGE_BACKEND == "s3":
        storage_path, storage_url = await _upload_to_s3(contents, unique_name, file.content_type)
    else:
        storage_path, storage_url = await _save_locally(contents, unique_name)

    metadata = await extract_video_metadata(storage_path)

    return {
        "storage_path": storage_path,
        "storage_url":  storage_url,
        "file_size_bytes": len(contents),
        "mime_type": file.content_type,
        "source": MediaSource.UPLOAD,
        "original_url": None,
        **metadata,
    }


async def ingest_link(url: str, user_id: str) -> dict:
    """
    Download a Reel / Short / TikTok via yt-dlp and store it.
    Returns same dict shape as ingest_upload.
    """
    source = _detect_source(url)
    out_dir = Path(settings.LOCAL_MEDIA_PATH) / user_id
    out_dir.mkdir(parents=True, exist_ok=True)

    unique_id = str(uuid.uuid4())
    output_template = str(out_dir / f"{unique_id}.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--limit-rate", settings.YT_DLP_RATE_LIMIT,
        "--output", output_template,
        "--print-json",
        "--quiet",
        url,
    ]

    logger.info("Downloading video: %s", url)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {stderr.decode()[:500]}")

    # yt-dlp prints JSON info to stdout
    try:
        info = json.loads(stdout.decode())
    except json.JSONDecodeError:
        info = {}

    # Find downloaded file
    downloaded = next(out_dir.glob(f"{unique_id}.*"), None)
    if not downloaded:
        raise RuntimeError("Downloaded file not found after yt-dlp.")

    storage_path = str(downloaded)
    unique_name  = f"{user_id}/{downloaded.name}"
    storage_url  = _public_url(unique_name)

    # Optionally push to S3
    if settings.STORAGE_BACKEND == "s3":
        async with aiofiles.open(downloaded, "rb") as f:
            data = await f.read()
        storage_path, storage_url = await _upload_to_s3(data, unique_name, "video/mp4")

    metadata = await extract_video_metadata(storage_path)

    return {
        "storage_path": storage_path,
        "storage_url":  storage_url,
        "original_url": url,
        "title": info.get("title"),
        "file_size_bytes": downloaded.stat().st_size if downloaded.exists() else None,
        "mime_type": "video/mp4",
        "source": source,
        **metadata,
    }


async def extract_audio(video_path: str, output_path: Optional[str] = None) -> str:
    """
    Use FFmpeg to extract audio as 16kHz mono WAV (optimal for Whisper).
    Returns path to the extracted audio file.
    """
    if output_path is None:
        base = Path(video_path).with_suffix(".wav")
        output_path = str(base)

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        output_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed: {stderr.decode()[:500]}")

    logger.info("Audio extracted: %s", output_path)
    return output_path


async def extract_video_metadata(video_path: str) -> dict:
    """
    Use ffprobe to extract duration, dimensions, fps.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        video_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()

    if proc.returncode != 0:
        return {}

    try:
        info = json.loads(stdout.decode())
    except json.JSONDecodeError:
        return {}

    video_stream = next(
        (s for s in info.get("streams", []) if s.get("codec_type") == "video"), {}
    )
    fmt = info.get("format", {})

    fps = None
    r_frame_rate = video_stream.get("r_frame_rate", "")
    if "/" in r_frame_rate:
        num, den = r_frame_rate.split("/")
        fps = round(int(num) / int(den), 2) if int(den) else None

    return {
        "duration_sec": float(fmt.get("duration", 0)) or None,
        "width":  video_stream.get("width"),
        "height": video_stream.get("height"),
        "fps":    fps,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_source(url: str) -> MediaSource:
    if re.search(r"instagram\.com", url):
        return MediaSource.INSTAGRAM
    if re.search(r"tiktok\.com", url):
        return MediaSource.TIKTOK
    if re.search(r"(youtube\.com|youtu\.be)", url):
        return MediaSource.YOUTUBE
    return MediaSource.UPLOAD
