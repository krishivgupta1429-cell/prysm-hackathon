"""Built during the Meta Global AI Developer Hackathon (new work).

Ops the system adds by itself, from measurements, before and next to the planner (so the planner call can stay short and these SAFE
fixes never depend on a model's mood):
  - captions for any video with speech (English, Hindi, Hinglish);
  - dead-air trimming on word boundaries, and clear fillers;
  - loudness normalisation only when the measured loudness is off target, with a denoise only when the noise floor says so.
Every decision (including "nothing to do here") is returned as a note so the UI can say why something is missing.
"""
from dataclasses import dataclass, field

from app.studio.autoedit import config
from app.studio.autoedit.edl import Op
from app.studio.autoedit.inputs import Inputs
from app.studio.autoedit.planner import existing_caption_share

TARGET_LUFS = -14.0
LOUDNESS_TOLERANCE_LU = 1.5
CREATOR_CAPTIONS_SHARE = 0.6     # at least this share of analysed frames already shows text: the creator has captions of their own
MIN_GAP_S = 0.7                  # shortest word gap that counts as dead air (scaled by the preset's cut aggressiveness)
MAX_AUTO_CUTS = 8
NOISY_FLOOR_DB = -52.0           # quietest 10 % of the audio louder than this: background noise worth a denoise


@dataclass
class AutoOps:
    ops: list[Op] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)   # {"area", "text"}: why something was added, left off or not needed


def _note(out: AutoOps, area: str, text: str) -> None:
    out.notes.append({"area": area, "text": text})


def creator_caption_share(analysis: dict) -> float:
    """Share of analysed frames that show the creator's own CAPTIONS: text in most frames AND text that changes from frame to frame.
    A logo, a watermark or a title card shows the same words in every frame; that is not a caption track and does not count."""
    frames = ((analysis or {}).get("visionData") or {}).get("frames") or []
    texts = [(f.get("onScreenText") or "").strip() for f in frames]
    shown = [t for t in texts if t]
    if not frames or not shown:
        return 0.0
    if len(set(shown)) / len(shown) < 0.4:
        return 0.0
    return len(shown) / len(frames)


def caption_op(inp: Inputs, analysis: dict, out: AutoOps) -> None:
    if not inp.has_speech:
        _note(out, "captions", f"No captions: only {len(inp.words)} spoken words were recognised, so there is nothing to caption.")
        return
    share = creator_caption_share(analysis or {})
    op = Op(id="auto_captions", type="caption_burn", group="captions", target_parameter="On-Screen Text",
            reason="Burned-in word-by-word captions: most viewers watch with the sound off")
    if share >= CREATOR_CAPTIONS_SHARE:
        op.enabled = False
        _note(out, "captions", f"Captions left off by default: {share:.0%} of the frames already show the creator's own captions (text that changes with the speech). Turn them on if that is not captions.")
    else:
        _note(out, "captions", f"Captions added for {len(inp.words)} spoken words ({inp.language}).")
    out.ops.append(op)


def audio_op(inp: Inputs, out: AutoOps) -> None:
    if not inp.has_audio:
        return
    if inp.lufs is None:
        _note(out, "audio", "Loudness could not be measured, so it was left alone.")
        return
    off = abs(inp.lufs - TARGET_LUFS) > LOUDNESS_TOLERANCE_LU or (inp.true_peak_db is not None and inp.true_peak_db > -1.0)
    noisy = inp.noise_floor_db is not None and inp.noise_floor_db > NOISY_FLOOR_DB and inp.has_speech
    if not (off or noisy):
        _note(out, "audio", f"Loudness is already {inp.lufs:.1f} LUFS (target {TARGET_LUFS:.0f}) and the background is quiet: audio left alone.")
        return
    why = []
    if off:
        why.append(f"loudness is {inp.lufs:.1f} LUFS, target {TARGET_LUFS:.0f}")
    if noisy:
        why.append(f"background noise at {inp.noise_floor_db:.0f} dBFS")
    out.ops.append(Op(id="auto_loudness", type="loudnorm", group="audio", target_parameter="Audio & Music",
                      reason="Normalise to -14 LUFS" + (" and reduce background noise" if noisy else "") + f" ({'; '.join(why)})",
                      params={"target_lufs": TARGET_LUFS, "true_peak": -1.5, "denoise": bool(noisy)}))
    _note(out, "audio", "Loudness " + ("fixed: " if off else "unchanged; ") + "; ".join(why) + ".")


def pacing_ops(inp: Inputs, style: dict, out: AutoOps) -> None:
    """Dead air, tightened. What counts as a pause is decided by the SIGNAL (the silence detector, and for sounds the recogniser dropped a
    pitch tracker), never by word timestamps, which are only good to ~0.1-0.2 s and often stretch a word over the pause after it. A pause
    is not removed whole: it is shortened to a natural beat (`keep` seconds on each side stay), so speech keeps its rhythm. Clear filler
    words the recogniser did write down are cut too (whole words, with their own padding). On a reel cut to music only silences of 0.8 s
    or more are touched."""
    if not inp.has_audio:
        return
    aggr = float(style.get("cut_aggressiveness", 0.5))
    keep = min(max(0.14 - (aggr - 0.5) * 0.08, 0.08), 0.2)
    min_silence = 0.8 if inp.music_driven else max(0.25, 0.36 - (aggr - 0.5) * 0.2)
    cands: list[tuple[float, float, float, str]] = []   # (removed seconds, start, end, kind)
    for s in inp.silences:
        a, b = s["start"], s["end"] if s.get("end") is not None else inp.duration
        if b - a < min_silence:
            continue
        lo = a + (keep if a > 0.05 else 0.0)                 # a silence at the very start is cut right up to the first sound
        hi = b - (keep if b < inp.duration - 0.05 else 0.0)  # and one at the very end from just after the last
        if hi - lo >= 0.1:
            cands.append((hi - lo, lo, hi, "silence"))
    if inp.has_speech and not inp.music_driven:
        for h in inp.hesitations:
            cands.append((h["end"] - h["start"], max(h["start"] - 0.02, 0.0), h["end"] + 0.02, "hesitation"))
    cands.sort(reverse=True)
    budget = inp.duration * config.MAX_REMOVED_FRACTION * 0.8
    removed, n = 0.0, 0
    chosen = []
    for length, a, b, kind in cands[:MAX_AUTO_CUTS * 2]:
        if len(chosen) >= MAX_AUTO_CUTS * 2 or removed + length > budget or any(a < c[2] and b > c[1] for c in chosen):
            continue
        removed += length
        chosen.append((length, a, b, kind))
        n += 1
        if kind == "silence":
            where = "at the start" if a <= 0.05 else "at the end" if b >= inp.duration - 0.05 else ""
            op = Op(id=f"auto_silence{n}", type="cut_segment", group="pacing", target_parameter="Retention & Pacing",
                    reason=f"Tighten a pause: cut {length:.1f} s of silence" + (f" {where}" if where else ""),
                    params={"start": round(a, 3), "end": round(b, 3), "silence": True})
        else:
            op = Op(id=f"auto_hesitation{n}", type="cut_segment", group="pacing", target_parameter="Retention & Pacing",
                    reason=f"Remove an um/uh-like sound ({length:.1f} s) the transcript did not show",
                    params={"start": round(a, 3), "end": round(b, 3), "silence": True, "hesitation": True})
        out.ops.append(op)
    fillers = []
    if inp.has_speech and not inp.music_driven:
        fillers = [f for f in inp.fillers if not any(a <= f["start"] and f["end"] <= b for _l, a, b, _k in chosen)][:6]
    for i, f in enumerate(fillers, 1):
        out.ops.append(Op(id=f"auto_filler{i}", type="remove_filler", group="pacing", target_parameter="Retention & Pacing",
                          reason=f"Remove the filler word \"{f['word']}\"", params={"start": f["start"], "end": f["end"], "word": f["word"]}))
    hes = sum(1 for c in chosen if c[3] == "hesitation")
    if chosen or fillers:
        _note(out, "pacing", f"{len(chosen) - hes} pauses tightened ({removed:.1f} s cut), {hes} um/uh-like sounds the transcript missed, {len(fillers)} filler words from the transcript.")
    elif inp.music_driven:
        _note(out, "pacing", "No real silence long enough to cut (the reel is cut to music, so pauses between words are left alone).")
    else:
        _note(out, "pacing", f"No pause of {min_silence:.2f} s or more, no um/uh-like sound and no filler word found: nothing to tighten.")


def build(inp: Inputs, analysis: dict, style: dict) -> AutoOps:
    out = AutoOps()
    caption_op(inp, analysis, out)
    audio_op(inp, out)
    pacing_ops(inp, style, out)
    return out
