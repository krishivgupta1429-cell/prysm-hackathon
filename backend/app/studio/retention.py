"""Hackathon-built (Meta Global AI Developer Hackathon).

Retention for Prysm's work files: a small background cleanup that deletes per-job work folders, cached
perception reports and any edited-video files that have not been touched for STUDIO_RETENTION_HOURS (default 24).

Layout under the Studio base directory: jobs/<job_id>/ (one folder per analysis), _cache/<sha256>/ (perception
reports) and edited/ (edited-video outputs). A folder's age is the newest modification time of anything in it, so
a job that is still running is never removed. Nothing outside the base directory is ever touched.
"""
import asyncio
import logging
import shutil
import time
from pathlib import Path

log = logging.getLogger("prysm.studio.retention")
CLEANUP_INTERVAL_S = 3600
EDITED_SUFFIXES = (".mp4", ".mov", ".webm", ".mkv")


def newest_mtime(path: Path) -> float:
    """The most recent modification time of the path or anything inside it."""
    newest = path.stat().st_mtime
    if path.is_dir():
        for child in path.rglob("*"):
            try:
                newest = max(newest, child.stat().st_mtime)
            except OSError:
                continue
    return newest


def cleanup(base: Path, max_age_hours: float, now: float | None = None) -> list[Path]:
    """Delete job folders, cache entries and edited-video files older than max_age_hours. Returns what was removed."""
    now = time.time() if now is None else now
    cutoff = now - max_age_hours * 3600
    removed: list[Path] = []
    if not base.is_dir():
        return removed
    for parent in ("jobs", "_cache"):
        for entry in sorted((base / parent).glob("*")) if (base / parent).is_dir() else []:
            try:
                if entry.is_dir() and newest_mtime(entry) < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed.append(entry)
            except OSError:
                continue
    edited = base / "edited"
    if edited.is_dir():
        for f in edited.rglob("*"):
            try:
                if f.is_file() and f.suffix.lower() in EDITED_SUFFIXES and f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed.append(f)
            except OSError:
                continue
    return removed


async def retention_loop(base: Path, max_age_hours: float, interval_s: float = CLEANUP_INTERVAL_S) -> None:
    """Clean once at startup, then every interval. Never raises."""
    while True:
        try:
            removed = await asyncio.to_thread(cleanup, base, max_age_hours)
            if removed:
                log.info("Studio retention removed %d item(s) older than %sh", len(removed), max_age_hours)
        except Exception as e:
            log.warning("Studio retention failed: %s", e)
        await asyncio.sleep(interval_s)


def start_retention(base: Path, max_age_hours: float) -> asyncio.Task:
    """Start the background cleanup on the running event loop."""
    return asyncio.create_task(retention_loop(base, max_age_hours))
