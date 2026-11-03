"""Built during the Meta Global AI Developer Hackathon (new work).

Renders an EDL with ONE FFmpeg run from the original (always re-encoded, never stream copy):
word-boundary cuts with 60-120 ms padding and 40 ms audio cross-fades, a cold-open teaser, ASS captions and text overlays
retimed to the new timeline, eased zoom punches about the face, loudnorm to -14 LUFS, and a 1080x1920 H.264 High / AAC output.
Groups are rendered by passing the set of groups to include; the caller caches and compares the results.
"""
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.studio.autoedit import config, ffx, captions, faces as facesmod
from app.studio.autoedit.edl import EDL, Op
from app.studio.autoedit.inputs import Inputs
from app.studio.autoedit.timeline import Timeline, build_timeline, surviving_words

ZOOM_IN_S, ZOOM_HOLD_S, ZOOM_OUT_S = 0.25, 1.0, 0.4
BAND_PAD_PX = 30


@dataclass
class RenderResult:
    path: Path
    duration: float
    timeline: Timeline
    overlays: list[dict] = field(default_factory=list)
    caption_words: list[dict] = field(default_factory=list)
    zooms: list[dict] = field(default_factory=list)
    ass_path: Path | None = None
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0
    ops_applied: list[str] = field(default_factory=list)


def render_key(src_id: str, edl: EDL, groups: set[str]) -> str:
    """Cache key: the source, the style and exactly the enabled ops of the included groups."""
    ops = [o.model_dump() for o in edl.ops if o.enabled and o.group in groups]
    blob = json.dumps({"src": src_id, "ops": ops, "style": edl.style, "g": sorted(groups)}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:20]


# ------------------------------------------------------------------ pieces of the graph

def _fmt(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") or "0"


def _smooth(u: str) -> str:
    return f"(pow({u},2)*(3-2*{u}))"


def zoom_expressions(zooms: list[dict]) -> tuple[str, str, str]:
    """(zoom factor, focus x, focus y) as ffmpeg expressions of `t`. Each punch eases in, holds, eases out (smoothstep)."""
    terms, fx, fy = [], [], []
    for z in zooms:
        a, d = z["out_at"], ZOOM_IN_S
        b = a + d + ZOOM_HOLD_S
        up = f"clip((t-{_fmt(a)})/{_fmt(d)},0,1)"
        down = f"clip((t-{_fmt(b)})/{_fmt(ZOOM_OUT_S)},0,1)"
        terms.append(f"({_fmt(z['scale'] - 1)}*({_smooth(up)}-{_smooth(down)}))")
        end = b + ZOOM_OUT_S
        win = f"between(t,{_fmt(a)},{_fmt(end)})"
        fx.append(f"(({_fmt(z['fx'] - 0.5)})*{win})")
        fy.append(f"(({_fmt(z['fy'] - 0.5)})*{win})")
    return "1+" + "+".join(terms), "0.5+" + "+".join(fx), "0.5+" + "+".join(fy)


def _fit_chain(inp: Inputs) -> str:
    if inp.is_9x16 or abs(inp.width / inp.height - 9 / 16) < 0.06:
        return f"scale={config.OUT_W}:{config.OUT_H}:flags=lanczos,setsar=1"
    return (f"split[fa][fb];[fa]scale={config.OUT_W}:{config.OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={config.OUT_W}:{config.OUT_H},boxblur=40:8,eq=brightness=-0.08[fbg];"
            f"[fb]scale={config.OUT_W}:{config.OUT_H}:force_original_aspect_ratio=decrease:flags=lanczos,setsar=1[ffg];"
            f"[fbg][ffg]overlay=(W-w)/2:(H-h)/2,setsar=1")


def fit_rect(inp: Inputs) -> tuple[float, float, float, float]:
    """(x0, y0, w, h) of the source picture inside the 1080x1920 canvas, as fractions of the canvas."""
    if inp.is_9x16 or abs(inp.width / inp.height - 9 / 16) < 0.06:
        return 0.0, 0.0, 1.0, 1.0
    s = min(config.OUT_W / inp.width, config.OUT_H / inp.height)
    w, h = inp.width * s / config.OUT_W, inp.height * s / config.OUT_H
    return (1 - w) / 2, (1 - h) / 2, w, h


def _to_canvas(face: dict, inp: Inputs) -> dict:
    x0, y0, w, h = fit_rect(inp)
    return {"cx": x0 + face["cx"] * w, "cy": y0 + face["cy"] * h, "w": face["w"] * w, "h": face["h"] * h}


def text_band(position: str, role: str, text: str, style: dict) -> tuple[float, float]:
    """Approximate (top, bottom) in canvas pixels of a text block, for the face-overlap check."""
    size = int(style["hook_size"] * (1.0 if role == "hook" else 0.72))
    per_line = captions.max_line_chars(size)
    lines = max(1, -(-len(text) // per_line))
    height = lines * size * 1.3 + BAND_PAD_PX
    align, mv = captions.OVERLAY_POSITIONS[position]
    if align == 8:
        return mv, mv + height
    if align == 5:
        return config.OUT_H / 2 - height / 2, config.OUT_H / 2 + height / 2
    return config.OUT_H - mv - height, config.OUT_H - mv


def place_overlay(text: str, role: str, wanted: str, face_y: tuple[float, float] | None, style: dict) -> str:
    """The requested position unless the text block would sit on the face; then the first of top / lower / center that does not."""
    for pos in [wanted, "top", "lower", "center"]:
        top, bottom = text_band(pos, role, text, style)
        if face_y is None or bottom < face_y[0] or top > face_y[1]:
            return pos
    return wanted


# ------------------------------------------------------------------ the render

def _audio_chain(tl: Timeline, src_label: str, has_duck: bool) -> tuple[list[str], str]:
    """Filter lines producing [aout0]: per-segment atrim, tail-extended by the cross-fade length, joined with acrossfade (net length = sum of segments)."""
    d = config.XFADE_S
    lines, labels = [], []
    n = len(tl.segments)
    for i, s in enumerate(tl.segments):
        ext = d if i < n - 1 else 0.0
        if s.kind == "beat":
            lines.append(f"aevalsrc=0:s=48000:c=stereo:d={_fmt(s.length + ext)},asetpts=PTS-STARTPTS[a{i}]")
        else:
            lines.append(f"{src_label}atrim=start={_fmt(s.src_start)}:end={_fmt(s.src_end + ext)},asetpts=PTS-STARTPTS,"
                         f"aformat=sample_rates=48000:channel_layouts=stereo[a{i}]")
        labels.append(f"[a{i}]")
    if n == 1:
        lines.append(f"{labels[0]}anull[aout0]")
        return lines, "[aout0]"
    prev = labels[0]
    for i in range(1, n):
        out = "[aout0]" if i == n - 1 else f"[ax{i}]"
        lines.append(f"{prev}{labels[i]}acrossfade=d={_fmt(d)}:c1=qsin:c2=qsin{out}")
        prev = out
    return lines, "[aout0]"


def _video_segments(tl: Timeline, inp: Inputs) -> tuple[list[str], str]:
    lines, labels = [], []
    fps = inp.fps or 30.0
    lines.append(f"[0:v]fps={_fmt(fps)},setpts=PTS-STARTPTS[vsrc]")
    n = len(tl.segments)
    lines.append(f"[vsrc]split={n}" + "".join(f"[s{i}]" for i in range(n)))
    for i, s in enumerate(tl.segments):
        if s.kind == "beat":
            lines.append(f"[s{i}]trim=start=0:end={_fmt(1.0 / fps)},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={_fmt(s.length)}[v{i}]")
        else:
            lines.append(f"[s{i}]trim=start={_fmt(s.src_start)}:end={_fmt(s.src_end)},setpts=PTS-STARTPTS[v{i}]")
        labels.append(f"[v{i}]")
    lines.append("".join(labels) + f"concat=n={n}:v=1:a=0[vcat]")
    return lines, "[vcat]"


def measure_loudnorm(src: Path, duck: Path | None, tl: Timeline, op: Op) -> dict | None:
    """Pass 1 of loudnorm over exactly the audio that will be rendered (cuts and denoise included)."""
    lines, last = _audio_chain(tl, "[1:a]" if duck else "[0:a]", False)
    pre = _pre_filters(op)
    graph = ";".join(lines) + f";{last}{pre}loudnorm=I={op.params['target_lufs']}:TP={op.params['true_peak']}:LRA=11:print_format=json[an]"
    args = ["-i", str(src)] + (["-i", str(duck)] if duck else [])
    err = ffx.run_stderr([*args, "-filter_complex", graph, "-map", "[an]", "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def _pre_filters(op: Op) -> str:
    chain = "highpass=f=70,"
    if op.params.get("denoise", True):
        chain += "afftdn=nr=8:nf=-45:tn=0,"
    return chain


@dataclass
class Pieces:
    active: list
    timeline: Timeline
    words: list
    overlays: list
    zooms: list
    notes: list
    applied: list


def plan_pieces(edl: EDL, inp: Inputs, groups: set[str], faces: list[dict] | None = None) -> Pieces:
    """Everything the render needs that is not ffmpeg: the new timeline, retimed captions, overlays kept off the face and zoom punches
    mapped onto the new timeline. Pure (no files), so cached renders can be re-described and tests can check it."""
    active = [o for o in edl.ops if o.enabled and o.group in groups]
    style = edl.style
    tl = build_timeline(active, inp)
    notes: list[str] = []
    applied: list[str] = [o.id for o in active]
    caps = any(o.type == "caption_burn" for o in active)
    words_out = surviving_words(tl, inp.words) if caps and inp.words else []
    faces = faces or []
    # overlays on the output timeline, kept off the face
    overlays = []
    for o in active:
        if o.type != "text_overlay":
            continue
        p = o.params
        if p["anchor"] == "output":
            a, b = 0.0, min(p["end"], tl.duration)
            mid_src = 0.0
        else:
            mapped = tl.map_range(p["start"], p["end"])
            if not mapped:
                notes.append(f"overlay {o.id} fell inside a cut and was skipped")
                applied.remove(o.id)
                continue
            a, b = mapped
            mid_src = (p["start"] + p["end"]) / 2
        f = facesmod.nearest(faces, mid_src)
        face_y = None
        if f:
            c = _to_canvas(f, inp)
            face_y = ((c["cy"] - c["h"] / 2) * config.OUT_H - 20, (c["cy"] + c["h"] / 2) * config.OUT_H + 20)
        pos = place_overlay(p["text"], p["role"], p["position"], face_y, style)
        if pos != p["position"]:
            notes.append(f"overlay {o.id} moved from {p['position']} to {pos} to stay off the face")
        overlays.append({"id": o.id, "text": p["text"], "start": round(a, 3), "end": round(b, 3), "position": pos, "role": p["role"]})

    # zoom punches mapped onto the new timeline (at least 2.5 s apart, alternating strength, about the face)
    zooms, last_out, k = [], -99.0, 0
    for o in sorted((o for o in active if o.type == "zoom_punch"), key=lambda o: o.params["at"]):
        at = tl.map_time(o.params["at"])
        if at is None or at - last_out < config.ZOOM_MIN_GAP_S or at + ZOOM_IN_S + ZOOM_HOLD_S + ZOOM_OUT_S > tl.duration:
            notes.append(f"zoom {o.id} skipped (cut out, too close to another punch or to the end)")
            applied.remove(o.id)
            continue
        if k >= int(style["zoom_max"]):
            notes.append(f"zoom {o.id} skipped: the {style['preset']} preset allows {int(style['zoom_max'])} punches")
            applied.remove(o.id)
            continue
        base = min(max(style["zoom_scale"] * o.params["scale"] / 1.10, 1.04), 1.15)  # the style's intensity sets the strength; the op's value is a relative hint
        scale = round(base if k % 2 == 0 else max(1.04, base - 0.03), 3)
        f = facesmod.nearest(faces, o.params["at"])
        fx, fy, fw, fh = 0.5, 0.42, 0.0, 0.0
        if f:
            c = _to_canvas(f, inp)
            fx, fy, fw, fh = min(max(c["cx"], 0.0), 1.0), min(max(c["cy"], 0.0), 1.0), c["w"], c["h"]
        if f and fw > 0 and fh > 0:  # keep the whole face inside the crop: shrink the punch, or skip it when the face is too big to zoom on
            limit = min(min(fx, 1 - fx) / (fw / 2), min(fy, 1 - fy) / (fh / 2)) * 0.97
            if limit < 1.05:
                notes.append(f"zoom {o.id} skipped: the face fills too much of the frame to zoom in without cropping it")
                applied.remove(o.id)
                continue
            if scale > limit:
                notes.append(f"zoom {o.id} reduced from {scale:.2f} to {limit:.2f} to keep the face in frame")
                scale = round(limit, 3)
        zooms.append({"id": o.id, "out_at": at, "scale": scale, "fx": fx, "fy": fy, "fw": fw, "fh": fh, "face": bool(f)})
        last_out, k = at, k + 1

    return Pieces(active, tl, words_out, overlays, zooms, notes, applied)


def render(edl: EDL, inp: Inputs, src: str | Path, out: str | Path, work: str | Path, groups: set[str], *,
           faces: list[dict] | None = None, duck_wav: str | Path | None = None, preset_x264: str = "fast") -> RenderResult:
    """Render the enabled ops of `groups` (an empty set = just the 9:16 conversion). Returns the result with the retimed
    captions / overlays so the caller can verify them."""
    t0 = time.time()
    src, out, work = Path(src), Path(out), Path(work)
    work.mkdir(parents=True, exist_ok=True)
    pc = plan_pieces(edl, inp, groups, faces)
    active, tl, words_out, overlays, zooms, notes, applied = pc.active, pc.timeline, pc.words, pc.overlays, pc.zooms, pc.notes, pc.applied
    style = edl.style

    # video graph
    vlines, vlast = _video_segments(tl, inp)
    fit = _fit_chain(inp)
    vlines.append(f"{vlast}{fit}[vfit]")
    cur = "[vfit]"
    if zooms:
        z, fx, fy = zoom_expressions(zooms)
        vlines.append(f"{cur}scale=w='trunc({config.OUT_W}*({z})/2)*2':h='trunc({config.OUT_H}*({z})/2)*2':eval=frame:flags=bicubic,"
                      f"crop={config.OUT_W}:{config.OUT_H}:x='(iw-{config.OUT_W})*({fx})':y='(ih-{config.OUT_H})*({fy})'[vz]")
        cur = "[vz]"
    ass_path = None
    if words_out or overlays:
        ass_path = work / "captions.ass"
        ass_path.write_text(captions.build_ass(words_out, overlays, style, tl.duration, with_captions=bool(words_out)), encoding="utf-8")
        fonts = str(config.FONTS_DIR).replace("\\", "/").replace(":", "\\:")
        vlines.append(f"{cur}ass='{str(ass_path).replace(chr(92), '/').replace(':', chr(92) + ':')}':fontsdir='{fonts}'[vout]")
    else:
        vlines.append(f"{cur}null[vout]")

    # audio graph
    alines, alast = [], None
    ln = next((o for o in active if o.type == "loudnorm"), None)
    duck_in = Path(duck_wav) if duck_wav and any(o.type == "music_duck" for o in active) else None
    if inp.has_audio:
        alines, alast = _audio_chain(tl, "[1:a]" if duck_in else "[0:a]", bool(duck_in))
        tail = alast
        if ln is not None:
            m = None
            try:
                m = measure_loudnorm(src, duck_in, tl, ln)
            except ffx.FFmpegError as e:
                notes.append(f"loudness pass failed ({str(e)[:80]}); one-pass loudnorm used")
            lim = 10 ** (ln.params["true_peak"] / 20)
            ln_args = f"I={ln.params['target_lufs']}:TP={ln.params['true_peak']}:LRA=11"
            if m and all(k in m for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")):
                ln_args += (f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
                            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
            alines.append(f"{tail}{_pre_filters(ln)}loudnorm={ln_args},alimiter=limit={lim:.3f}:level=disabled,aresample=48000[aout]")
        else:
            alines.append(f"{tail}aresample=48000[aout]")

    graph = ";".join(vlines + alines)
    cmd = ["-y", "-i", str(src)]
    if duck_in is not None:
        cmd += ["-i", str(duck_in)]
    cmd += ["-filter_complex", graph, "-map", "[vout]"]
    if alines:
        cmd += ["-map", "[aout]"]
    cmd += ["-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", preset_x264,
            "-r", _fmt(inp.fps or 30.0), "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    if alines:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-t", _fmt(tl.duration + 0.001), "-movflags", "+faststart", str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    ffx.run(cmd, timeout=1200)
    return RenderResult(path=out, duration=tl.duration, timeline=tl, overlays=overlays, caption_words=words_out, zooms=zooms,
                        ass_path=ass_path, notes=notes, seconds=round(time.time() - t0, 2), ops_applied=applied)
