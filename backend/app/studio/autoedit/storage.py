"""Built during the Meta Global AI Developer Hackathon (new work).

Storage interface for Auto-edit: uploads, edited outputs and intermediate files all go through it.
`LocalStorage` (default) keeps files under a directory; `S3Storage` talks to any S3-compatible bucket (Cloudflare R2) with boto3.
Keys are plain "a/b/c" strings; they are validated so a key can never leave the storage root.
"""
import os
import re
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path

from app.studio.autoedit import config

_KEY_OK = re.compile(r"^[A-Za-z0-9._\-/=]+$")


class StorageError(RuntimeError):
    pass


def check_key(key: str) -> str:
    if not key or not _KEY_OK.match(key) or key.startswith("/") or ".." in key.split("/") or "//" in key:
        raise StorageError(f"invalid storage key: {key!r}")
    return key


class Storage(ABC):
    """put/get files and bytes, exists, delete, delete_prefix, list (with modified time) and an optional direct URL."""

    @abstractmethod
    def put_file(self, key: str, path: str | Path, content_type: str | None = None) -> None: ...

    @abstractmethod
    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None: ...

    @abstractmethod
    def get_file(self, key: str, path: str | Path) -> Path: ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def list(self, prefix: str = "") -> list[tuple[str, float]]:
        """[(key, modified epoch seconds)] for keys that start with `prefix`."""

    def delete_prefix(self, prefix: str) -> int:
        n = 0
        for key, _ in self.list(prefix):
            self.delete(key)
            n += 1
        return n

    def url(self, key: str, expires: int = 3600) -> str | None:
        """A time-limited direct download URL when the backend can make one (S3), else None."""
        return None

    def sweep(self, prefix: str, max_age_days: float, now: float | None = None) -> int:
        """Delete everything under `prefix` older than `max_age_days`; returns how many objects were removed."""
        cutoff = (now if now is not None else time.time()) - max_age_days * 86400
        n = 0
        for key, mtime in self.list(prefix):
            if mtime < cutoff:
                self.delete(key)
                n += 1
        return n


class LocalStorage(Storage):
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / check_key(key)).resolve()
        if self.root not in p.parents:
            raise StorageError(f"key escapes the storage root: {key!r}")
        return p

    def put_file(self, key, path, content_type=None):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".part")
        shutil.copyfile(path, tmp)
        os.replace(tmp, p)

    def put_bytes(self, key, data, content_type=None):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def get_file(self, key, path):
        p = self._path(key)
        if not p.is_file():
            raise StorageError(f"not found: {key}")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, path)
        return Path(path)

    def get_bytes(self, key):
        p = self._path(key)
        if not p.is_file():
            raise StorageError(f"not found: {key}")
        return p.read_bytes()

    def exists(self, key):
        return self._path(key).is_file()

    def delete(self, key):
        p = self._path(key)
        if p.is_file():
            p.unlink()
            parent = p.parent
            while parent != self.root and not any(parent.iterdir()):  # tidy empty folders
                parent.rmdir()
                parent = parent.parent

    def list(self, prefix=""):
        out = []
        for p in self.root.rglob("*"):
            if p.is_file() and not p.name.endswith(".part"):
                key = p.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    out.append((key, p.stat().st_mtime))
        return sorted(out)


class S3Storage(Storage):
    """S3 / Cloudflare R2. `client` is injectable so tests use a fake; otherwise boto3 is built from the R2_* variables."""

    def __init__(self, bucket: str, client=None, *, endpoint: str | None = None, access_key: str | None = None,
                 secret_key: str | None = None, prefix: str = ""):
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                                  config=Config(signature_version="s3v4"), region_name="auto")
        self.client = client

    def _k(self, key: str) -> str:
        return self.prefix + check_key(key)

    def put_file(self, key, path, content_type=None):
        extra = {"ContentType": content_type} if content_type else {}
        self.client.upload_file(str(path), self.bucket, self._k(key), ExtraArgs=extra or None)

    def put_bytes(self, key, data, content_type=None):
        kw = {"ContentType": content_type} if content_type else {}
        self.client.put_object(Bucket=self.bucket, Key=self._k(key), Body=data, **kw)

    def get_file(self, key, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self.client.download_file(self.bucket, self._k(key), str(path))
        except Exception as e:
            raise StorageError(f"not found or unreadable: {key} ({type(e).__name__})") from None
        return Path(path)

    def get_bytes(self, key):
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()
        except Exception as e:
            raise StorageError(f"not found or unreadable: {key} ({type(e).__name__})") from None

    def exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except Exception:
            return False

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=self._k(key))

    def list(self, prefix=""):
        out, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": self.prefix + prefix}
            if token:
                kw["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kw)
            for o in page.get("Contents") or []:
                lm = o["LastModified"]
                out.append((o["Key"][len(self.prefix):], lm.timestamp() if hasattr(lm, "timestamp") else float(lm)))
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        return sorted(out)

    def url(self, key, expires=3600):
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": self._k(key)}, ExpiresIn=expires)


_storage: Storage | None = None


def get_storage() -> Storage:
    """The configured storage (cached): AUTO_EDIT_STORAGE=s3 uses R2_*; anything else is local."""
    global _storage
    if _storage is None:
        if (config.env("AUTO_EDIT_STORAGE", "local") or "local").lower() == "s3":
            bucket, endpoint = config.env("R2_BUCKET"), config.env("R2_ENDPOINT")
            key, secret = config.env("R2_ACCESS_KEY_ID"), config.env("R2_SECRET_ACCESS_KEY")
            if not (bucket and endpoint and key and secret):
                raise StorageError("AUTO_EDIT_STORAGE=s3 needs R2_ENDPOINT, R2_BUCKET, R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY")
            _storage = S3Storage(bucket, endpoint=endpoint, access_key=key, secret_key=secret)
        else:
            _storage = LocalStorage(config.storage_dir())
    return _storage


def set_storage(storage: Storage | None) -> None:
    """For tests: replace (or clear) the cached storage."""
    global _storage
    _storage = storage


def upload_key(user_id: str, analysis_id: str) -> str:
    return f"uploads/{user_id}/{analysis_id}/original.mp4"


def edit_prefix(analysis_id: str) -> str:
    return f"edits/{analysis_id}/"
