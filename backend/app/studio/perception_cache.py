"""Built during the Meta Global AI Developer Hackathon (new work).

Perception-report cache keyed by the sha256 of the video file.

The perception stages (watch, speech, subjects, audio, signals, consensus, captions, music_id) cost money and
take minutes; the analysis stage is cheap and depends on the creator's niche and description. Caching the
finished perception report by video hash means changing the niche or description reruns only the analysis.

Layout: <root>/_cache/<sha256>/report.json + options.json. An entry is reused only when it was made with the
same options (watch model, SAM fps, captions language, skipped stages, schema version) and none of the core
stages failed. The retention cleanup removes old entries together with the other work folders.
"""
import hashlib
import json
import os
from pathlib import Path

from app.studio.report import SCHEMA_VERSION, Report, TrendsStage

CORE_STAGES = ("watch", "speech", "subjects", "consensus")  # a cached report with an error here is not reused


def video_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def options_key(**options) -> str:
    """A short stable key for the options the perception stages depend on."""
    payload = json.dumps({"schema": SCHEMA_VERSION, **options}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def entry_dir(root: Path, sha: str) -> Path:
    return root / "_cache" / sha


def usable(report: Report) -> bool:
    return all(getattr(report.stages, name).status != "error" for name in CORE_STAGES)


def load(root: Path, sha: str, key: str) -> Report | None:
    """The cached perception report for this video and these options, or None."""
    d = entry_dir(root, sha)
    try:
        meta = json.loads((d / "options.json").read_text())
        if meta.get("key") != key:
            return None
        report = Report.model_validate_json((d / "report.json").read_text())
    except (OSError, ValueError):
        return None
    return report if usable(report) else None


def save(root: Path, sha: str, key: str, report: Report) -> None:
    """Store the perception report (written to a temp file first so a reader never sees half of it)."""
    if not usable(report):
        return
    # live trends belong to a niche set and a day, not to a video: they are fetched again for every analysis
    report = report.model_copy(update={"stages": report.stages.model_copy(update={"trends": TrendsStage()})})
    d = entry_dir(root, sha)
    d.mkdir(parents=True, exist_ok=True)
    for name, text in (("report.json", report.model_dump_json()), ("options.json", json.dumps({"key": key, "sha256": sha}))):
        tmp = d / f".{name}.tmp"
        tmp.write_text(text)
        os.replace(tmp, d / name)
