"""
app/services/r2_service.py
Cloudflare R2 object storage — upload edited videos, generate pre-signed download URLs.
Uses boto3 S3-compatible API (R2 is S3-compatible).
"""
import os
import logging
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def _cfg():
    """Read R2 credentials lazily so Pydantic Settings has time to load the .env file."""
    from app.core.config import settings
    return {
        "account_id": settings.R2_ACCOUNT_ID,
        "access_key": settings.R2_ACCESS_KEY_ID,
        "secret_key": settings.R2_SECRET_ACCESS_KEY,
        "bucket":     settings.R2_BUCKET_NAME,
    }


def _client():
    cfg = _cfg()
    return boto3.client(
        "s3",
        endpoint_url=f"https://{cfg['account_id']}.r2.cloudflarestorage.com",
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def _bucket() -> str:
    return _cfg()["bucket"]


_warned_not_ready = False


def _ready() -> bool:
    global _warned_not_ready
    cfg = _cfg()
    ok = bool(cfg["account_id"] and cfg["access_key"])
    if not ok and not _warned_not_ready:  # fail soft, log once
        _warned_not_ready = True
        logger.warning("[R2] Credentials not configured — edited-video storage is unavailable")
    return ok


def upload_video(local_path: str, object_key: str) -> bool:
    """Upload a local video file to R2. Returns True on success."""
    if not _ready():
        return False
    try:
        file_size = os.path.getsize(local_path)
        with open(local_path, "rb") as f:
            _client().put_object(
                Bucket=_bucket(),
                Key=object_key,
                Body=f,
                ContentType="video/mp4",
                ContentLength=file_size,
            )
        logger.info(f"[R2] Uploaded {local_path} ({file_size // 1024}KB) → {object_key}")
        return True
    except ClientError as e:
        logger.error(f"[R2] Upload ClientError: {e}")
        return False
    except Exception as e:
        logger.error(f"[R2] Upload failed: {e}")
        return False


def get_presigned_url(object_key: str, expiry_seconds: int = 3600 * 24) -> str | None:
    """Generate a pre-signed GET URL. Default expiry: 24 hours."""
    if not _ready():
        return None
    try:
        url = _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": _bucket(), "Key": object_key},
            ExpiresIn=expiry_seconds,
        )
        return url
    except ClientError as e:
        logger.error(f"[R2] Presigned URL ClientError: {e}")
        return None
    except Exception as e:
        logger.error(f"[R2] Presigned URL failed: {e}")
        return None


def delete_object(object_key: str) -> bool:
    """Delete an object from R2."""
    try:
        _client().delete_object(Bucket=_bucket(), Key=object_key)
        return True
    except Exception as e:
        logger.warning(f"[R2] Delete failed for {object_key}: {e}")
        return False
