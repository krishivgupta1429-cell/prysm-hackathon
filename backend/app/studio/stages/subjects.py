"""Built during the Meta Global AI Developer Hackathon (new work).

Subjects stage: SAM 3.1 tracks "person" and "face" on a low-fps clip (10 by default) and the per-frame
detections are reduced to compact tracks (presence per second, a 0.5 s box timeline, safe
area). Masks are not decoded and raw SAM events are stored in the work dir, not the report.
"""
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.studio import config
from app.studio.meta_client import Detection, MetaClient
from app.studio.report import Box, BoxPoint, SubjectsStage, SubjectTrack

PROMPTS = {"person": "person", "face": "face"}
TIMELINE_STEP_S = 0.5


def _norm(d: Detection) -> tuple[float, float, float, float]:
    clamp = lambda v: max(0.0, min(1.0, v))
    return clamp(d.x1 / d.w), clamp(d.y1 / d.h), clamp(d.x2 / d.w), clamp(d.y2 / d.h)


def _area(box: tuple[float, float, float, float]) -> float:
    return max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0)


def build_track(prompt: str, detections: list[Detection], duration_s: float, fps: float = config.DEFAULT_SAM_FPS) -> SubjectTrack:
    """Reduce per-frame detections to a SubjectTrack. Frames with several objects use the largest box."""
    total_frames = max(int(round(duration_s * fps)), 1)
    by_frame: dict[int, Detection] = {}
    for d in detections:
        if d.frame not in by_frame or _area(_norm(d)) > _area(_norm(by_frame[d.frame])):
            by_frame[d.frame] = d
    track = SubjectTrack(prompt=prompt, detected_frames=len(by_frame),
                         object_count=len({d.obj for d in detections}),
                         presence_per_second=[0] * max(math.ceil(duration_s), 1))
    if not by_frame:
        return track
    frames = sorted(by_frame)
    track.first_seen_s = round(frames[0] / fps, 3)
    track.screen_time_ratio = round(len(frames) / total_frames, 4)
    track.visible_in_first_3s = any(f / fps < 3 for f in frames)
    track.mean_confidence = round(sum(by_frame[f].conf for f in frames) / len(frames), 4)
    boxes = {f: _norm(by_frame[f]) for f in frames}
    track.mean_box_area_ratio = round(sum(_area(b) for b in boxes.values()) / len(boxes), 4)
    for f in frames:
        sec = int(f / fps)
        if sec < len(track.presence_per_second):
            track.presence_per_second[sec] = 1
    t = 0.0
    while t < duration_s:
        f = int(round(t * fps))
        if f in boxes:
            x1, y1, x2, y2 = boxes[f]
            track.box_timeline.append(BoxPoint(t=round(t, 2), x1=round(x1, 4), y1=round(y1, 4), x2=round(x2, 4),
                                               y2=round(y2, 4), conf=round(by_frame[f].conf, 4)))
        t += TIMELINE_STEP_S
    track.safe_area = Box(x1=round(min(b[0] for b in boxes.values()), 4), y1=round(min(b[1] for b in boxes.values()), 4),
                          x2=round(max(b[2] for b in boxes.values()), 4), y2=round(max(b[3] for b in boxes.values()), 4))
    return track


def subjects_status(errors: dict[str, str]) -> tuple[str, str | None]:
    """Honest stage status from per-track errors (keys "person" / "face").

    No errors -> ok. Person track failed -> error (the person track is the one that matters, even if the
    face track worked). Only the face track failed -> partial.
    """
    if not errors:
        return "ok", None
    detail = "; ".join(f"{name} track failed: {msg}" for name, msg in errors.items())
    if "person" in errors:
        return "error", f"person track failed, so subjects is unusable: {detail}"
    return "partial", f"person track ok, but {detail}"


def run_subjects(client: MetaClient, sam_clip: Path, clip_duration_s: float, raw_dir: Path,
                 fps: float = config.DEFAULT_SAM_FPS) -> SubjectsStage:
    start = time.time()
    stage = SubjectsStage()
    try:
        client.upload(sam_clip)  # once, so the two prompts below reuse the same file id

        def track(name: str):
            try:
                call = client.sam_track(sam_clip, PROMPTS[name], raw_dir / f"sam_events_{name}.jsonl")
                return name, call, None
            except Exception as e:
                return name, None, config.redact(f"{type(e).__name__}: {e}")

        with ThreadPoolExecutor(max_workers=len(PROMPTS)) as pool:
            results = list(pool.map(track, PROMPTS))
        for name, call, err in results:
            if err:
                stage.errors[name] = err
                continue
            setattr(stage, name, build_track(name, call.detections, clip_duration_s, fps))
            stage.cost_usd = round(stage.cost_usd + call.cost_usd, 6)
            stage.frames_processed = max(stage.frames_processed or 0, call.frames_processed or 0) or None
        stage.status, stage.error = subjects_status(stage.errors)
    except Exception as e:
        stage.status, stage.error = "error", config.redact(f"{type(e).__name__}: {e}")
    stage.seconds = round(time.time() - start, 2)
    return stage
