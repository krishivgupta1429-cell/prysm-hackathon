"""Built during the Meta Global AI Developer Hackathon (new work).

The new timeline: which source ranges are kept (cuts, fillers, trims, cold open), and how source time maps to output time.
Boundaries sit on the frame grid so the video (counted in frames) and the audio (counted in seconds) stay the same length.
Captions are retimed from the words that survive.
"""
from dataclasses import dataclass, field

from app.studio.autoedit.edl import EDL, Op, removal_ranges
from app.studio.autoedit.inputs import Inputs


@dataclass
class Segment:
    src_start: float
    src_end: float
    out_start: float = 0.0
    kind: str = "main"   # main | cold (the teaser) | beat (a held first frame, silent)

    @property
    def length(self) -> float:
        return self.src_end - self.src_start if self.kind != "beat" else self.src_end

    @property
    def out_end(self) -> float:
        return self.out_start + self.length


@dataclass
class Timeline:
    segments: list[Segment] = field(default_factory=list)
    fps: float = 30.0

    @property
    def duration(self) -> float:
        return self.segments[-1].out_end if self.segments else 0.0

    def map_time(self, t: float, which: str = "main") -> float | None:
        """Output time of source time `t` in the main part of the video (None when `t` was cut out)."""
        for s in self.segments:
            if s.kind == which and s.src_start - 1e-6 <= t <= s.src_end + 1e-6:
                return s.out_start + (t - s.src_start)
        return None

    def map_range(self, a: float, b: float) -> tuple[float, float] | None:
        """Output span of the part of [a,b] that survives (first kept point to last kept point), or None if all of it was cut."""
        pts = []
        for s in self.segments:
            if s.kind != "main":
                continue
            lo, hi = max(a, s.src_start), min(b, s.src_end)
            if hi - lo > 1e-3:
                pts.append((s.out_start + (lo - s.src_start), s.out_start + (hi - s.src_start)))
        return (pts[0][0], pts[-1][1]) if pts else None


def _grid(t: float, fps: float) -> float:
    return round(round(t * fps) / fps, 4)


def build_timeline(ops: list[Op], inp: Inputs) -> Timeline:
    """Keep-segments of the source after the enabled removing ops, with a cold-open teaser and beat in front when planned."""
    fps = inp.fps or 30.0
    dur = inp.duration
    cuts = [(_grid(a, fps), _grid(b, fps)) for a, b in removal_ranges(ops, inp)]
    keep, pos = [], 0.0
    for a, b in cuts:
        if a - pos > 1.0 / fps:
            keep.append((pos, a))
        pos = max(pos, b)
    end = _grid(dur, fps)
    if end - pos > 1.0 / fps:
        keep.append((pos, min(end, dur)))
    segs: list[Segment] = []
    cold = next((o for o in ops if o.enabled and o.type == "cold_open"), None)
    if cold is not None:
        s, e = _grid(cold.params["start"], fps), _grid(cold.params["end"], fps)
        segs.append(Segment(s, e, kind="cold"))
        segs.append(Segment(0.0, cold.params.get("beat_s", 0.3), kind="beat"))
    segs.extend(Segment(a, b) for a, b in keep)
    t = 0.0
    for s in segs:
        s.out_start = t
        t += s.length
    return Timeline(segs, fps)


def surviving_words(tl: Timeline, words: list[dict]) -> list[dict]:
    """Words retimed onto the new timeline (a word in the cold-open teaser appears twice: once in the teaser, once in place)."""
    out = []
    for si, s in enumerate(tl.segments):
        if s.kind == "beat":
            continue
        for w in words:
            if w["start"] >= s.src_start - 0.02 and w["end"] <= s.src_end + 0.02:
                a = s.out_start + max(w["start"] - s.src_start, 0.0)
                b = s.out_start + min(w["end"] - s.src_start, s.src_end - s.src_start)
                out.append({"word": w["word"], "start": round(a, 3), "end": round(max(b, a + 0.05), 3), "src_start": w["start"], "kind": s.kind, "seg": si})
    out.sort(key=lambda w: w["start"])
    return out
