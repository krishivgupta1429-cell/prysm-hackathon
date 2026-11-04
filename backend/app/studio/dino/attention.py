"""The attention strip: visual change + audio energy + audio change on one 0.5 s grid, as plain JSON.

    attention_strip(video, embed=None) -> dict
    planner_hints(strip)               -> dict   (what the edit planner can consume later)

Every part is optional and honest: if DINOv2 is off, "visual" is {"available": false, "reason": ...} and the audio
curves still come back (they need no model); if the video has no audio, the audio parts say so. Labelled "estimate".
"""
from app.studio.dino import audio as au
from app.studio.dino import visual as vis
from app.studio.dino.client import DinoUnavailable

LABEL = "attention strip (estimate)"
BIN_S = 0.5


def _fit(values: list[float], n: int) -> list[float | None]:
    v = list(values)[:n]
    return v + [None] * (n - len(v))


def attention_strip(video: str, embed=None, usage: dict | None = None) -> dict:
    from app.studio.dino import frames as fr
    info = fr.probe(video)
    duration = info["duration"]
    n = max(1, int(-(-duration // BIN_S)))

    visual: dict
    try:
        if embed is None:
            from app.studio.dino import client
            e = lambda jpgs: client.embed_jpegs(jpgs, usage=usage)
        else:
            e = embed
        visual = vis.visual_change_curve(video, embed=e)
    except DinoUnavailable as ex:
        visual = {"label": vis.LABEL, "available": False, "reason": str(ex)}

    x = au.decode_mono(video) if info["has_audio"] else None
    if x is None:
        reason = "no audio track"
        energy = au._unavailable("audio energy (estimate)", reason)
        change = au._unavailable("audio change (estimate)", reason)
        beats = au._unavailable("beats and onsets (estimate)", reason)
    else:
        energy, change, beats = au.energy_from_samples(x), au.change_from_samples(x), au.onsets_from_samples(x)

    strip = {
        "label": LABEL, "bin_s": BIN_S, "duration": round(duration, 2),
        "times": [round(i * BIN_S, 2) for i in range(n)],
        "visual_change": _fit(visual["scores"], n) if visual.get("available") else None,
        "audio_energy": _fit(energy["scores"], n) if energy.get("available") else None,
        "audio_change": _fit(change["scores"], n) if change.get("available") else None,
        "visual": visual, "energy": energy, "change": change, "beats": beats,
    }
    strip["planner"] = planner_hints(strip)
    return strip


def planner_hints(strip: dict) -> dict:
    """Plain JSON for the edit planner (all times in seconds of the ORIGINAL video):
    static_stretches (nothing changes on screen), loudness_dips (much quieter than the video's median), onset_times and
    beat_times. Each list is empty when its source is unavailable; `available` says which sources were used."""
    visual, energy, beats = strip.get("visual") or {}, strip.get("energy") or {}, strip.get("beats") or {}
    return {
        "label": "planner hints (estimate)",
        "available": {"visual": bool(visual.get("available")), "audio": bool(energy.get("available"))},
        "static_stretches": list(visual.get("static_segments") or []) if visual.get("available") else [],
        "loudness_dips": au.loudness_dips(energy) if energy.get("available") else [],
        "onset_times": list(beats.get("onsets") or []) if beats.get("available") else [],
        "beat_times": list(beats.get("beats") or []) if beats.get("available") else [],
        "tempo_bpm": beats.get("tempo_bpm") if beats.get("available") else None,
    }
