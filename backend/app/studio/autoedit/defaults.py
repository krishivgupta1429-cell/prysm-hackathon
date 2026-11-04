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


def caption_op(inp: Inputs, analysis: dict, out: AutoOps) -> None:
    if not inp.has_speech:
        _note(out, "captions", f"No captions: only {len(inp.words)} spoken words were recognised, so there is nothing to caption.")
        return
    share = existing_caption_share(analysis or {})
    op = Op(id="auto_captions", type="caption_burn", group="captions", target_parameter="On-Screen Text",
            reason="Burned-in word-by-word captions: most viewers watch with the sound off")
    if share >= CREATOR_CAPTIONS_SHARE:
        op.enabled = False
        _note(out, "captions", f"Captions left off by default: {share:.0%} of the frames already show the creator's own text. Turn them on if that is not captions.")
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
    """Dead air and clear fillers. Dead air = (a) a pause between two spoken words that is long enough, and (b) a stretch of real silence
    found by the silence detector. A pause is only cut when no music bed plays through it. On a reel cut to music, only real silence
    is cut (the beat-synced cuts of the planner are a separate RISKY bundle)."""
    if not inp.has_audio:
        return
    aggr = float(style.get("cut_aggressiveness", 0.5))
    min_gap = max(0.5, MIN_GAP_S + (0.5 - aggr) * 0.6)
    words = inp.words
    silent = [s for s in inp.silences if s.get("end") is not None]

    def quiet_part(a: float, b: float) -> list[tuple[float, float]]:
        """The parts of [a, b] the silence detector measured as silent. A recogniser's word times are only good to ~0.1-0.2 s, so a pause is cut
        only where the SIGNAL is silent (this also keeps music beds and breaths out of the cut)."""
        out = []
        for s in silent:
            lo, hi = max(a, s["start"]), min(b, s["end"])
            if hi - lo >= 0.3:
                out.append((lo, hi))
        return out

    cands: list[tuple[float, float, float, str]] = []   # (length, start, end, kind)
    if inp.has_speech and not inp.music_driven:
        for i in range(len(words) - 1):
            a, b = words[i]["end"], words[i + 1]["start"]
            if b - a >= min_gap:
                for lo, hi in quiet_part(a, b):
                    if hi - lo >= min_gap * 0.8:
                        cands.append((hi - lo, lo, hi, "silence"))
        if words and words[0]["start"] >= 1.0:
            for lo, hi in quiet_part(0.0, words[0]["start"]):
                if lo <= 0.05 and hi - lo >= 1.0:
                    cands.append((hi - lo, lo, hi, "silence"))
    for s in silent:  # real silence anywhere (a stretch with no words at all, e.g. a held pose), at least 0.8 s
        a, b = s["start"], s["end"]
        if b - a >= max(min_gap, 0.8) and 0.2 < a and b < inp.duration - 0.2 and not any(a < c[2] and b > c[1] for c in cands):
            cands.append((b - a, a, b, "silence"))
    for s in inp.silences:  # silence running to the very end of the file
        if s.get("end") is None and inp.duration - s["start"] >= 1.0 and not any(s["start"] < c[2] and inp.duration > c[1] for c in cands):
            cands.append((inp.duration - s["start"], s["start"], inp.duration, "silence"))
    cands.sort(reverse=True)
    budget = inp.duration * config.MAX_REMOVED_FRACTION * 0.8
    removed = 0.0
    n = 0
    chosen = []
    for length, a, b, kind in cands[:MAX_AUTO_CUTS]:
        if removed + length > budget:
            continue
        removed += length
        chosen.append((length, a, b, kind))
        n += 1
        where = "at the start" if a <= 0.05 else "at the end" if b >= inp.duration - 0.05 else "between words" if any(w["end"] <= a + 0.3 for w in words) else ""
        op = Op(id=f"auto_silence{n}", type="cut_segment", group="pacing", target_parameter="Retention & Pacing",
                reason=f"Cut {length:.1f} s of silence" + (f" {where}" if where else ""), params={"start": a, "end": b, "silence": True})
        out.ops.append(op)
    fillers = []
    if inp.has_speech and not inp.music_driven:
        fillers = [f for f in inp.fillers if not any(a <= f["start"] and f["end"] <= b for _l, a, b, _k in chosen)][:6]
    for i, f in enumerate(fillers, 1):
        out.ops.append(Op(id=f"auto_filler{i}", type="remove_filler", group="pacing", target_parameter="Retention & Pacing",
                          reason=f"Remove the filler word \"{f['word']}\"", params={"start": f["start"], "end": f["end"], "word": f["word"]}))
    if chosen or fillers:
        _note(out, "pacing", f"{len(chosen)} dead-air stretches ({removed:.1f} s) and {len(fillers)} filler words found; cuts sit on word boundaries.")
    elif inp.music_driven:
        _note(out, "pacing", "No real silence long enough to cut (the reel is cut to music, so pauses between words are left alone).")
    else:
        _note(out, "pacing", "No dead air or filler words long enough to cut.")


def build(inp: Inputs, analysis: dict, style: dict) -> AutoOps:
    out = AutoOps()
    caption_op(inp, analysis, out)
    audio_op(inp, out)
    pacing_ops(inp, style, out)
    return out
