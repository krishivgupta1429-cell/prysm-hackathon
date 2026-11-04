"""Built during the Meta Global AI Developer Hackathon (new work).

A cover image for the edit: the strongest still of the FINAL video (the analysis' best-thumbnail time mapped onto the new timeline,
else the first second) with the hook text in the safe area. It is a real frame of the creator's video, not a generated picture.
(Muse Image answers /images/generations on this key, but a generated cutaway would not be the creator's own footage, so it is not used.)
"""
from pathlib import Path

from app.studio.autoedit import captions, config, ffx
from app.studio.autoedit.edl import EDL
from app.studio.autoedit.timeline import Timeline


def pick_time(analysis: dict, tl: Timeline, duration: float) -> float:
    """Output time of the analysis' best thumbnail moment; the nearest surviving moment if it was cut; else 1 s in."""
    best = (analysis.get("visionData") or {}).get("bestThumbnailFrame")
    try:
        t = float(best)
    except (TypeError, ValueError):
        return min(1.0, max(duration - 0.1, 0.0))
    mapped = tl.map_time(t)
    if mapped is None:
        pts = [(abs(s.src_start - t), s.out_start) for s in tl.segments if s.kind == "main"]
        mapped = min(pts)[1] if pts else 1.0
    return min(max(mapped, 0.0), max(duration - 0.1, 0.0))


def make(final_video: Path, edl: EDL, tl: Timeline, analysis: dict, work: Path, out: Path) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    info = ffx.probe(final_video)
    t = pick_time(analysis, tl, info["duration"])
    hook = next((o.params["text"] for o in edl.ops if o.enabled and o.type == "text_overlay" and o.params.get("role") == "hook"), None)
    frame = work / "cover_raw.jpg"
    ffx.frame_at(final_video, t, frame)
    if not hook:
        frame.replace(out)
        return out
    ass = work / "cover.ass"
    ass.write_text(captions.build_ass([], [{"text": hook, "start": 0.0, "end": 1.0, "position": "top", "role": "hook"}], edl.style, 1.0, with_captions=False).replace("\\fad(250,250)", ""), encoding="utf-8")
    fonts = str(config.FONTS_DIR).replace(":", "\\:")
    ffx.run(["-y", "-loop", "1", "-i", str(frame), "-t", "0.5", "-vf", f"ass='{str(ass).replace(':', chr(92) + ':')}':fontsdir='{fonts}'", "-frames:v", "1", "-q:v", "2", str(out)])
    return out
