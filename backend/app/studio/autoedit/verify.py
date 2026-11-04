"""Built during the Meta Global AI Developer Hackathon (new work).

Verification of one group of changes:
  a) objective checks made by tools (duration, loudness, silence, cuts and pacing, text on screen, caption legibility and safe area,
     caption sync against a fresh transcript, black / frozen frames, A/V length, cuts on word gaps, face kept in the zoom);
  b) ONE Wonder critic call per edit: it watches the original and the edited proxy and returns, per bundle of changes, approve / reject
     plus defects (text over face, cut mid-word, caption timing, audio pumping ...);
  c) one overall AI re-score (before and after in one call), INFORMATIONAL only: labelled AI-estimated, never a veto.
`decide_bundle` is the keep-or-revert rule: SAFE bundles are kept when the objective checks pass and the critic reports no defect;
RISKY bundles need an explicit critic approval and no failed objective check. Models are called only by `rescore_pair` and `critique_all`.
"""
import difflib
import hashlib
import subprocess
from pathlib import Path

from app.studio.autoedit import config, ffx
from app.studio.autoedit.edl import EDL, GROUPS, DEFAULT_TARGET, RISK, risk_of
from app.studio.autoedit.executor import RenderResult
from app.studio.autoedit.inputs import Inputs, norm_token
from app.studio.autoedit.llm import EditLLM, CostCapExceeded

SCORED = ["Hook & Opening", "Retention & Pacing", "Shareability", "Audio & Music", "Trend Alignment", "Visual Quality & Originality"]
RESCORE_PARAMS = SCORED + ["On-Screen Text"]
LABEL = "AI-estimated (not measured viewership)"
FLAGS = ("text_over_face", "cut_mid_word", "caption_timing", "caption_readability", "audio_pumping", "click_at_cut", "zoom_crops_face",
         "duplicates_creator_text", "jarring_cut", "sfx_masks_speech", "too_many_effects", "wrong_sound_for_moment", "tone_mismatch", "other")


# ------------------------------------------------------------------ a) objective measurements

def measure(video: str | Path, light: bool = False) -> dict:
    """Tool-measured facts about a video file. `light` skips the frame-level detectors (cuts, black, frozen): used for the delivered file."""
    info = ffx.probe(video)
    out = {"duration_s": round(info["duration"], 2), "width": info["width"], "height": info["height"], "fps": info["fps"],
           "has_audio": info["has_audio"]}
    p = subprocess.run([config.ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True, timeout=60)
    durs = {}
    for line in p.stdout.splitlines():
        parts = line.split(",")
        if len(parts) >= 2:
            try:
                durs[parts[0]] = float(parts[1])
            except ValueError:
                pass
    out["av_diff_s"] = round(abs(durs.get("video", 0) - durs.get("audio", durs.get("video", 0))), 3)
    if info["has_audio"]:
        out.update(ffx.loudness(video))
        sil = ffx.silences(video)
        total = sum(((s["end"] if s["end"] is not None else info["duration"]) - s["start"]) for s in sil)
        out["silence_ratio"] = round(total / max(info["duration"], 1e-6), 3)
    if light:
        return out
    cuts = ffx.scene_cuts(video)
    out["cut_count"] = len(cuts)
    out["avg_shot_s"] = round(info["duration"] / (len(cuts) + 1), 2)
    blk, frz = ffx.black_frames(video), ffx.frozen_frames(video)
    out["black_frames"], out["frozen_frames"] = len(blk), len(frz)
    out["black_s"] = round(sum((b["end"] - b["start"]) for b in blk), 2)
    out["frozen_s"] = round(sum(f["duration"] or 0.0 for f in frz), 2)
    return out


def text_geometry(ass_path: str | Path | None, duration: float, work: Path, fontsdir: Path | None = None) -> dict:
    """Render the subtitles on black and measure where the text really is (union of bright-pixel boxes at 4 fps)."""
    if not ass_path:
        return {"has_text": False, "ok": True, "violations": 0}
    import numpy as np
    scale = 4
    w, h = config.OUT_W // scale, config.OUT_H // scale
    fonts = str(fontsdir or config.FONTS_DIR).replace(":", "\\:")
    ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
    cmd = [config.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
           f"color=c=black:s={config.OUT_W}x{config.OUT_H}:r=4:d={duration:.2f}", "-vf",
           f"ass='{ass}':fontsdir='{fonts}',scale={w}:{h},format=gray", "-f", "rawvideo", "-"]
    p = subprocess.run(cmd, capture_output=True, timeout=300)
    if p.returncode != 0:
        return {"has_text": True, "ok": False, "violations": -1, "error": p.stderr.decode(errors="replace")[-200:]}
    frames = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(frames) // (w * h)
    frames = frames[: n * w * h].reshape(n, h, w)
    safe = config.SAFE
    sx0, sx1, sy0, sy1 = safe["x0"] // scale - 2, safe["x1"] // scale + 2, safe["y0"] // scale - 2, safe["y1"] // scale + 2
    union = None
    violations = 0
    text_frames = 0
    for fr in frames:
        ys, xs = np.where(fr > 110)
        if len(xs) < 20:
            continue
        text_frames += 1
        box = (xs.min(), ys.min(), xs.max(), ys.max())
        union = box if union is None else (min(union[0], box[0]), min(union[1], box[1]), max(union[2], box[2]), max(union[3], box[3]))
        if box[0] < sx0 or box[2] > sx1 or box[1] < sy0 or box[3] > sy1:
            violations += 1
    return {"has_text": text_frames > 0, "ok": violations == 0, "violations": violations, "text_frames": text_frames,
            "bbox_px": [int(v) * scale for v in union] if union else None, "safe_px": [safe["x0"], safe["y0"], safe["x1"], safe["y1"]],
            "seconds_with_text": round(text_frames / 4, 2)}


def caption_sync(caption_words: list[dict], asr_words: list[dict]) -> dict | None:
    """Median offset (ms) between the retimed caption words and what a fresh recogniser hears in the edited video."""
    if not caption_words or not asr_words:
        return None
    a = [norm_token(w["word"]) for w in caption_words]
    b = [norm_token(w["word"]) for w in asr_words]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    offs = []
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            if a[blk.a + k]:
                offs.append(caption_words[blk.a + k]["start"] - asr_words[blk.b + k]["start"])
    if len(offs) < 3:
        return {"matched_words": len(offs), "median_offset_ms": None}
    offs.sort()
    med = offs[len(offs) // 2]
    return {"matched_words": len(offs), "median_offset_ms": int(round(med * 1000)),
            "p90_abs_ms": int(round(sorted(abs(o) for o in offs)[int(len(offs) * 0.9) - 1] * 1000))}


def exact_edges(edl: EDL) -> list[float]:
    """Source times of the edges of cuts that were measured from the signal (silence / hesitation ops): word timestamps do not apply to them."""
    out = []
    for o in edl.ops:
        if o.enabled and o.params.get("silence"):
            out += [o.params["start"], o.params["end"]]
    return out


def cuts_on_word_gaps(rr: RenderResult, words: list[dict], silences: list[dict] | None = None, exact: list[float] | None = None) -> dict:
    """Every keep-segment boundary in the source must lie in a gap between words (no cut through a word). A boundary inside (or within
    0.2 s of) a stretch the silence detector measured as silent is fine: the signal is the authority there, not the word timestamps."""
    bad = []
    quiet = [(s["start"] - 0.2, s["end"] + 0.2) for s in (silences or []) if s.get("end") is not None]
    for s in rr.timeline.segments:
        if s.kind == "beat":
            continue
        for t in (s.src_start, s.src_end):
            if t <= 0.01 or any(a <= t <= b for a, b in quiet) or any(abs(t - e) <= 0.08 for e in exact or []):
                continue
            for w in words:
                if w["start"] + 0.03 < t < w["end"] - 0.03:
                    bad.append({"t": round(t, 3), "word": w["word"]})
    return {"ok": not bad, "mid_word_cuts": bad}


def zoom_keeps_face(rr: RenderResult, faces: list[dict], inp: Inputs) -> dict:
    """At full zoom is the WHOLE face box still inside the crop window? The zoom is about the face centre (fx, fy), so the window
    is [f*(1-1/s), f*(1-1/s)+1/s]; the face fits when its half-size is at most min(f, 1-f)/s on each axis (analytic, no model)."""
    cut = []
    for z in rr.zooms:
        if not z["face"]:
            continue
        s = z["scale"]
        if z["fw"] / 2 > min(z["fx"], 1 - z["fx"]) / s + 1e-6 or z["fh"] / 2 > min(z["fy"], 1 - z["fy"]) / s + 1e-6:
            cut.append(z["id"])
    return {"ok": not cut, "face_cropped": cut, "zooms_with_face": sum(1 for z in rr.zooms if z["face"]), "zooms": len(rr.zooms)}


def cut_clicks(video: Path, rr: RenderResult, work: Path) -> dict:
    """Look for a sample-to-sample spike at every join of the timeline (a click from a hard cut inside a sound)."""
    import numpy as np
    import soundfile as sf
    wav = Path(work) / f"{Path(video).stem}.clk.wav"
    Path(work).mkdir(parents=True, exist_ok=True)
    ffx.extract_wav(video, wav, rate=16000)
    y, sr = sf.read(str(wav), dtype="float32")
    if y.ndim > 1:
        y = y.mean(axis=1)
    d = np.abs(np.diff(y))
    bad = []
    for seg in rr.timeline.segments[1:]:
        i = int(seg.out_start * sr)
        w = d[max(0, i - int(0.015 * sr)): i + int(0.015 * sr)]
        ctx = d[max(0, i - int(0.3 * sr)): i + int(0.3 * sr)]
        if len(w) and len(ctx) and float(w.max()) > 0.06 and float(w.max()) > 8 * (float(np.percentile(ctx, 99)) + 1e-4):
            bad.append(round(seg.out_start, 2))
    return {"ok": not bad, "at": bad, "joins": max(0, len(rr.timeline.segments) - 1)}


def objective_checks(group: str, before: dict, after: dict, rr: RenderResult, edl: EDL, inp: Inputs, work: Path, *,
                     asr_words: list[dict] | None = None, faces: list[dict] | None = None, video: Path | None = None) -> dict:
    """[{name, ok, detail}] for the group plus the numbers behind them. A failed check blocks keeping the group
    (a check marked `warn` is shown but never blocks)."""
    checks = []

    def add(name, ok, detail, warn=False):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, **({"warn": True} if warn else {})})

    add("duration within limits", 0 < after["duration_s"] <= config.MAX_RESULT_SECONDS, f"{after['duration_s']} s (limit {config.MAX_RESULT_SECONDS} s)")
    add("output format 9:16 (1080x1920; previews are 540x960)", (after["width"], after["height"]) in ((config.OUT_W, config.OUT_H), (config.OUT_W // 2, config.OUT_H // 2)),
        f"{after['width']}x{after['height']}")
    add("audio and video the same length", after["av_diff_s"] <= before.get("av_diff_s", 0.0) + 0.12, f"difference {after['av_diff_s']} s (source: {before.get('av_diff_s', 0.0)} s)")
    # compared by total duration with a tolerance: a source that is already nearly static (a guitarist standing still) has many
    # borderline "frozen" stretches, and an overlay or a zoom shifts which of them the detector counts
    add("no new black frames", after.get("black_s", 0) <= before.get("black_s", 0) + 0.3, f"{before.get('black_s', 0)} s -> {after.get('black_s', 0)} s")
    add("no new frozen picture", after.get("frozen_s", 0) <= before.get("frozen_s", 0) + 1.0, f"{before.get('frozen_s', 0)} s -> {after.get('frozen_s', 0)} s")
    extra: dict = {}
    if group in ("pacing", "rhythm"):
        removed = inp.duration - (rr.duration - sum(s.length for s in rr.timeline.segments if s.kind != "main"))
        add("at most 25 % removed", removed / inp.duration <= config.MAX_REMOVED_FRACTION + 1e-6, f"{removed:.1f} s of {inp.duration:.1f} s ({removed / inp.duration:.0%})")
        wg = cuts_on_word_gaps(rr, inp.words, inp.silences, exact_edges(edl))
        add("every cut sits between words", wg["ok"], "none inside a word" if wg["ok"] else str(wg["mid_word_cuts"][:3]))
        if "silence_ratio" in before and "silence_ratio" in after:
            add("silence not increased", after["silence_ratio"] <= before["silence_ratio"] + 0.02, f"{before['silence_ratio']:.1%} -> {after['silence_ratio']:.1%}")
    if group in ("pacing", "rhythm") and video is not None and len(rr.timeline.segments) > 1:
        ck = cut_clicks(video, rr, work)
        extra["clicks"] = ck
        add("no audible clicks at the cuts", ck["ok"], "none found" if ck["ok"] else f"spikes at {ck['at'][:4]} s", warn=True)
    if group == "audio" and after.get("lufs") is not None and any(o.type == "loudnorm" and o.enabled and o.group == "audio" for o in edl.ops):
        add("loudness near -14 LUFS", -15.5 <= after["lufs"] <= -12.5, f"{before.get('lufs')} -> {after['lufs']} LUFS")
        add("true peak at or below -1 dBTP", (after.get("true_peak_db") or -99) <= -0.9, f"{after.get('true_peak_db')} dBTP")
    if group in ("text", "hook", "captions"):
        from app.studio.autoedit.executor import fontsdir_for
        geo = text_geometry(rr.ass_path, rr.duration, work, fontsdir_for(rr.style or edl.style, Path(work)))
        extra["text_geometry"] = geo
        add("text inside Instagram's safe area", geo["ok"], "all text frames inside" if geo["ok"] else f"{geo['violations']} frames outside (box {geo.get('bbox_px')})")
        extra["text_seconds"] = geo.get("seconds_with_text", 0.0)
        if rr.caption_words:
            from app.studio.autoedit.executor import caption_band, face_overlap_share
            pos = (rr.style or edl.style).get("caption_position", "lower")
            share = face_overlap_share(faces or [], inp, caption_band(pos, int(edl.style.get("caption_size", 78)))) if faces else 0.0
            add("captions stay off the face", share <= 0.2, f"captions at '{pos}' overlap the face in {share:.0%} of the sampled shots")
            size = edl.style.get("caption_size", 0)
            add("caption text size legible (>= 56 px)", size >= 56, f"{size} px with a {max(4, size // 14)} px outline")
            sync = caption_sync(rr.caption_words, asr_words or [])
            extra["caption_sync"] = sync
            if sync and sync.get("median_offset_ms") is not None:
                add("captions in sync with the speech (median offset <= 150 ms)", abs(sync["median_offset_ms"]) <= 150, f"{sync['median_offset_ms']} ms over {sync['matched_words']} words")
    if group == "zoom":
        starts = sorted(z["out_at"] for z in rr.zooms)
        gaps_ok = all(b - a >= config.ZOOM_MIN_GAP_S - 1e-6 for a, b in zip(starts, starts[1:]))
        add("zoom punches at least 2.5 s apart", gaps_ok, f"{len(starts)} punches")
        zf = zoom_keeps_face(rr, faces or [], inp)
        extra["zoom_face"] = zf
        add("face stays in frame during zooms", zf["ok"], f"{zf['zooms_with_face']} of {zf['zooms']} punches had a detected face (the rest zoom about the centre)")
    return {"checks": checks, "ok": all(c["ok"] or c.get("warn") for c in checks), "extra": extra}


# ------------------------------------------------------------------ b) compact AI re-score

PARAM_DEFS = {
    "Hook & Opening": "how strongly the first 3 seconds make a viewer stop scrolling (visual + words + text)",
    "Retention & Pacing": "momentum, dead air, cut rhythm and how likely viewers are to stay to the end",
    "Shareability": "would a viewer send this to someone: relatable, surprising or useful",
    "Audio & Music": "voice clarity, loudness balance, music/voice mix, no distracting noise",
    "Trend Alignment": "fit with current Reels formats and styles",
    "Visual Quality & Originality": "sharpness, framing, lighting, visual variety and distinctiveness",
    "On-Screen Text": "legibility, timing and placement of captions / text, not covering faces or the UI",
}


def _jpegs(video: Path, work: Path, n: int = 8) -> list[bytes]:
    info = ffx.probe(video)
    out = []
    times = [0.4] + [info["duration"] * (i + 0.5) / n for i in range(n)]
    for i, t in enumerate(times):
        p = work / f"{video.stem}_{i}.jpg"
        ffx.frame_at(video, min(t, info["duration"] - 0.1), p, 448)
        out.append(p.read_bytes())
    return out


def _facts(m: dict, words: list[dict]) -> str:
    keys = ("duration_s", "lufs", "true_peak_db", "silence_ratio", "cut_count", "avg_shot_s")
    return ", ".join(f"{k}={m[k]}" for k in keys if m.get(k) is not None) + f"; spoken words: {len(words)}"


def rescore_pair(before_video: Path, after_video: Path, before_m: dict, after_m: dict, before_words: list[dict], after_words: list[dict],
                 analysis: dict, llm: EditLLM, work: Path, label: str) -> dict:
    """Both versions in one Spark call (random order, same rubric). Returns {"before": {param: score}, "after": {...}, "notes": str, "label": ...}."""
    seed = int(hashlib.sha1((str(after_video) + label).encode()).hexdigest(), 16)
    a_is_before = seed % 2 == 0
    vids = (before_video, after_video) if a_is_before else (after_video, before_video)
    mets = (before_m, after_m) if a_is_before else (after_m, before_m)
    wds = (before_words, after_words) if a_is_before else (after_words, before_words)
    imgs, texts = [], []
    for tag, v, m, w in zip("AB", vids, mets, wds):
        frames = _jpegs(v, work)
        imgs.extend(frames)
        texts.append(f"VERSION {tag}: {len(frames)} frames in time order (the first is at 0.4 s). Measured: {_facts(m, w)}.\n"
                     f"Transcript: {' '.join(x['word'] for x in w)[:900] or '(none)'}")
    rubric = "\n".join(f"- {k}: {v}" for k, v in PARAM_DEFS.items())
    prompt = f"""You are scoring two versions (A and B) of the same short vertical video for Instagram Reels. The images are given as
{len(imgs) // 2} frames of version A followed by {len(imgs) // 2} frames of version B. Niche: {analysis.get('niche', 'General')}.

{texts[0]}

{texts[1]}

Score EACH version from 0 to 100 on these parameters, with the same strictness for both (an edit that is only different is not better):
{rubric}

Judge from the frames and the numbers above; you cannot hear audio, so judge "Audio & Music" only from the measured loudness / silence
and the transcript. Return ONLY JSON: {{"A": {{"<parameter>": <int>, ...}}, "B": {{...}}, "notes": "<one or two sentences on what differs>"}}
with exactly the seven parameter names above."""
    r = llm.json_call(prompt, config.planner_model(), f"rescore:{label}", max_tokens=6000,
                      images=imgs, est_in_tokens=9000, reasoning_effort=config.planner_effort())
    def clean(d):
        return {k: int(max(0, min(100, float(d[k])))) for k in RESCORE_PARAMS if isinstance(d, dict) and k in d and isinstance(d[k], (int, float))}
    a, b = clean(r.get("A")), clean(r.get("B"))
    before, after = (a, b) if a_is_before else (b, a)
    return {"before": before, "after": after, "notes": str(r.get("notes", ""))[:300], "label": LABEL}


# ------------------------------------------------------------------ c) Wonder critic (one call per edit)

def _describe(o: dict) -> str:
    p = o.get("params") or {}
    t = o["type"]
    if t == "text_overlay":
        return f"text overlay ({p.get('role')}): \"{p.get('text')}\""
    if t in ("cut_segment", "remove_filler"):
        return f"{t} {p.get('start')}-{p.get('end')} s of the original" + (f" (the word \"{p.get('word')}\")" if p.get("word") else "")
    if t == "trim":
        return f"trim to keep {p.get('start')}-{p.get('end')} s"
    if t == "zoom_punch":
        return f"zoom punch at {p.get('at')} s x{p.get('scale')}"
    if t == "cold_open":
        return f"cold open: teaser of {p.get('start')}-{p.get('end')} s shown first"
    if t == "loudnorm":
        return f"loudness to {p.get('target_lufs')} LUFS" + (" with denoise" if p.get("denoise") else "")
    if t == "music_duck":
        return f"music ducked {p.get('duck_db')} dB under the voice"
    if t == "caption_burn":
        return "burned-in word-highlight captions (3-5 word chunks, pop-in, emphasis words in the accent colour)"
    if t == "sfx_hit":
        return f"sound effect '{p.get('effect')}' for a {p.get('kind')} moment at {p.get('at')} s (synthesised, mixed far under the voice)"
    return t


def critique_all(before_video: Path, after_video: Path, bundles: list[dict], llm: EditLLM, work: Path, label: str) -> dict:
    """ONE critic call: Wonder watches (and hears) the original and the edited proxy, with every bundle of changes applied, and judges
    each bundle. `bundles` = [{"id", "risk", "ops": [op dict]}]. Returns
    {"verdict": "ok|unavailable", "mode": "video|frames", "bundles": {id: {"verdict", "defects": [{flag, severity, detail}], "reasons": []}}, "notes"}.
    Falls back to frame strips when the API refuses video."""
    from app.studio.autoedit import config as cfg
    listing = "\n".join(f"- bundle \"{b['id']}\" ({b['risk']}): " + "; ".join(_describe(o) for o in b["ops"]) for b in bundles)[:3500]
    prompt = f"""You are the quality critic of an automatic video editor for Instagram Reels. Video 1 is the ORIGINAL; video 2 is the EDITED
version. The edit applies these bundles of changes:
{listing}

Watch AND listen to both. For each bundle decide whether it made the video better or at least not worse, and list every real DEFECT
it introduced that a viewer would notice: {', '.join(FLAGS)}.
- Judge captions for READABILITY (size, contrast against the picture, line length, not too fast to read), text against the face and the Instagram UI zones, sound effects for MASKING the speech (an effect that makes a word hard to hear), for being TOO MANY (the edit feels busy), and for being the WRONG SOUND for the moment (a comic sound on a serious line).
- Give each defect a severity: "major" (a viewer would notice and be annoyed: a cut inside a word, text on the face, captions out of sync,
  pumping or clicking audio, a zoom that crops the face, text that repeats what the creator already shows) or "minor" (barely noticeable).
- Do not invent defects. If a bundle is fine, give an empty defects list. A subtle but harmless change is an "approve".
- "reject" a bundle only for a major defect or when it clearly makes the video worse.
- Put the time (seconds in the edited video) in each detail.
Return ONLY JSON: {{"bundles": {{"<bundle id>": {{"verdict": "approve" or "reject", "reasons": ["<short>"], "defects": [{{"flag": "<one of the flags>", "severity": "major" or "minor", "at_s": <seconds in the EDITED video, a number>, "detail": "<what, when>"}}]}}}}, "notes": "<one sentence overall>"}}
with one entry for EVERY bundle id listed above."""
    mode = "video"
    try:
        r = llm.json_call(prompt, cfg.critic_model(), f"critic:{label}", max_tokens=6000, videos=[before_video, after_video], est_in_tokens=7000,
                          reasoning_effort=cfg.critic_effort())
    except CostCapExceeded:
        raise
    except Exception:
        mode = "frames"
        try:
            strips = _jpegs(before_video, work, 6) + _jpegs(after_video, work, 6)
            r = llm.json_call(prompt + "\n(The videos are given as frames: the first 7 images are the original, the next 7 the edit. You cannot hear audio in this mode: do not judge audio.)",
                              cfg.critic_model(), f"critic-frames:{label}", max_tokens=6000, images=strips, est_in_tokens=7000,
                              reasoning_effort=cfg.critic_effort())
        except CostCapExceeded:
            raise
        except Exception as e_frames:
            return {"verdict": "unavailable", "mode": "none", "bundles": {}, "notes": f"the critic could not be reached ({type(e_frames).__name__}: {str(e_frames)[:120]})"}
    out: dict = {}
    raw = r.get("bundles") if isinstance(r.get("bundles"), dict) else {}
    for b in bundles:
        e = raw.get(b["id"]) if isinstance(raw.get(b["id"]), dict) else None
        if e is None:
            out[b["id"]] = {"verdict": "unknown", "defects": [], "reasons": []}
            continue
        defects = []
        for d in (e.get("defects") or []):
            if isinstance(d, dict) and (d.get("flag") or d.get("detail")):
                sev = "minor" if str(d.get("severity", "major")).lower() == "minor" else "major"
                at = d.get("at_s")
                defects.append({"flag": str(d.get("flag", "other"))[:30], "severity": sev, "detail": str(d.get("detail", ""))[:200],
                                "at_s": float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None})
        v = str(e.get("verdict", "")).lower()
        out[b["id"]] = {"verdict": v if v in ("approve", "reject") else "unknown", "defects": defects[:6],
                        "reasons": [str(x)[:200] for x in (e.get("reasons") or [])][:4]}
    return {"verdict": "ok", "mode": mode, "bundles": out, "notes": str(r.get("notes", ""))[:300]}


# ------------------------------------------------------------------ keep or revert

def decide_bundle(group: str, objective_ok: bool, objective_failures: list[str], critic: dict | None) -> dict:
    """The keep-or-revert rule.
    SAFE bundles (hook, pacing, captions, audio): kept when every objective check passes and the critic reports no major defect
    (if the critic cannot be reached they are kept on the objective checks alone, and the reason says so).
    RISKY bundles (zoom, rhythm, text labels, polish): kept only when no objective check fails AND the critic explicitly approves.
    The AI re-score plays no part. Returns {keep, risk, reasons[]}."""
    risk = risk_of(group)
    reasons: list[str] = []
    keep = True
    if not objective_ok:
        keep = False
        reasons.append("objective checks failed: " + "; ".join(objective_failures))
    entry = None
    if critic and critic.get("verdict") == "ok":
        entry = critic["bundles"].get(group)
    if entry is None:
        if risk == "risky":
            keep = False
            reasons.append("this is a RISKY change and the critic could not verify it, so it is off (you can turn it on)")
        else:
            reasons.append("the critic could not be reached: kept on the objective checks alone")
    else:
        major = [d for d in entry["defects"] if d["severity"] == "major"]
        minor = [d for d in entry["defects"] if d["severity"] != "major"]
        if major or entry["verdict"] == "reject":
            keep = False
            what = "; ".join(f"{d['flag']}: {d['detail']}" for d in major) or "; ".join(entry["reasons"]) or "no reason given"
            reasons.append("the critic found a defect: " + what)
        elif risk == "risky" and entry["verdict"] != "approve":
            keep = False
            reasons.append("RISKY change, and the critic did not approve it" + (": " + "; ".join(entry["reasons"]) if entry["reasons"] else ""))
        elif keep:
            reasons.append("the critic approved it" if entry["verdict"] == "approve" else "the critic reported no defect")
        if minor and keep:
            reasons.append("minor notes: " + "; ".join(f"{d['flag']}: {d['detail']}" for d in minor))
    if keep and objective_ok:
        reasons.insert(0, "objective checks pass")
    return {"keep": keep, "risk": risk, "reasons": reasons}
