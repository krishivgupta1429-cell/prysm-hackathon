"""Built during the Meta Global AI Developer Hackathon (new work).

Verification of one group of changes:
  a) objective checks made by tools (duration, loudness, silence, cuts and pacing, text on screen, caption legibility and safe area,
     caption sync against a fresh transcript, black / frozen frames, A/V length, cuts on word gaps, face kept in the zoom);
  b) a compact AI re-score of the targeted parameters (before and after in ONE call, labelled AI-estimated);
  c) a Wonder critic that watches before and after (video, else frame strips) and returns approve / reject with flags.
`decide` is the keep-or-revert rule. Nothing here calls a model except `rescore_pair` and `critique`.
"""
import difflib
import hashlib
import subprocess
from pathlib import Path

from app.studio.autoedit import config, ffx
from app.studio.autoedit.edl import EDL, GROUPS, DEFAULT_TARGET
from app.studio.autoedit.executor import RenderResult
from app.studio.autoedit.inputs import Inputs, norm_token
from app.studio.autoedit.llm import EditLLM, CostCapExceeded

SCORED = ["Hook & Opening", "Retention & Pacing", "Shareability", "Audio & Music", "Trend Alignment", "Visual Quality & Originality"]
RESCORE_PARAMS = SCORED + ["On-Screen Text"]
KEEP_MIN_GAIN = 2
MAX_DROP = 3
BLOCKING_FLAGS = {"text_over_face", "cut_mid_word", "audio_pumping"}
LABEL = "AI-estimated (not measured viewership)"


# ------------------------------------------------------------------ a) objective measurements

def measure(video: str | Path) -> dict:
    """Tool-measured facts about a video file."""
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
    cuts = ffx.scene_cuts(video)
    out["cut_count"] = len(cuts)
    out["avg_shot_s"] = round(info["duration"] / (len(cuts) + 1), 2)
    out["black_frames"] = len(ffx.black_frames(video))
    out["frozen_frames"] = len(ffx.frozen_frames(video))
    return out


def text_geometry(ass_path: str | Path | None, duration: float, work: Path) -> dict:
    """Render the subtitles on black and measure where the text really is (union of bright-pixel boxes at 4 fps)."""
    if not ass_path:
        return {"has_text": False, "ok": True, "violations": 0}
    import numpy as np
    scale = 4
    w, h = config.OUT_W // scale, config.OUT_H // scale
    fonts = str(config.FONTS_DIR).replace(":", "\\:")
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


def cuts_on_word_gaps(rr: RenderResult, words: list[dict]) -> dict:
    """Every keep-segment boundary in the source must lie in a gap between words (no cut through a word)."""
    bad = []
    for s in rr.timeline.segments:
        if s.kind == "beat":
            continue
        for t in (s.src_start, s.src_end):
            if t <= 0.01:
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


def objective_checks(group: str, before: dict, after: dict, rr: RenderResult, edl: EDL, inp: Inputs, work: Path, *,
                     asr_words: list[dict] | None = None, faces: list[dict] | None = None) -> dict:
    """[{name, ok, detail}] for the group plus the numbers behind them. A failed check blocks keeping the group."""
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("duration within limits", 0 < after["duration_s"] <= config.MAX_RESULT_SECONDS, f"{after['duration_s']} s (limit {config.MAX_RESULT_SECONDS} s)")
    add("output format 1080x1920", (after["width"], after["height"]) == (config.OUT_W, config.OUT_H), f"{after['width']}x{after['height']}")
    add("audio and video the same length", after["av_diff_s"] <= 0.12, f"difference {after['av_diff_s']} s")
    add("no new black frames", after["black_frames"] <= before["black_frames"], f"{before['black_frames']} -> {after['black_frames']}")
    add("no new frozen frames", after["frozen_frames"] <= before["frozen_frames"], f"{before['frozen_frames']} -> {after['frozen_frames']}")
    extra: dict = {}
    if group == "pacing":
        removed = inp.duration - (rr.duration - sum(s.length for s in rr.timeline.segments if s.kind != "main"))
        add("at most 25 % removed", removed / inp.duration <= config.MAX_REMOVED_FRACTION + 1e-6, f"{removed:.1f} s of {inp.duration:.1f} s ({removed / inp.duration:.0%})")
        wg = cuts_on_word_gaps(rr, inp.words)
        add("every cut sits between words", wg["ok"], "none inside a word" if wg["ok"] else str(wg["mid_word_cuts"][:3]))
        if "silence_ratio" in before and "silence_ratio" in after:
            add("silence not increased", after["silence_ratio"] <= before["silence_ratio"] + 0.02, f"{before['silence_ratio']:.1%} -> {after['silence_ratio']:.1%}")
    if group == "audio" and after.get("lufs") is not None and any(o.type == "loudnorm" and o.enabled and o.group == "audio" for o in edl.ops):
        add("loudness near -14 LUFS", -15.5 <= after["lufs"] <= -12.5, f"{before.get('lufs')} -> {after['lufs']} LUFS")
        add("true peak at or below -1 dBTP", (after.get("true_peak_db") or -99) <= -0.9, f"{after.get('true_peak_db')} dBTP")
    if group in ("text", "hook"):
        geo = text_geometry(rr.ass_path, rr.duration, work)
        extra["text_geometry"] = geo
        add("text inside Instagram's safe area", geo["ok"], "all text frames inside" if geo["ok"] else f"{geo['violations']} frames outside (box {geo.get('bbox_px')})")
        extra["text_seconds"] = geo.get("seconds_with_text", 0.0)
        if any(o.type == "caption_burn" for o in edl.ops if o.enabled):
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
    return {"checks": checks, "ok": all(c["ok"] for c in checks), "extra": extra}


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
                      images=imgs, est_in_tokens=9000)
    def clean(d):
        return {k: int(max(0, min(100, float(d[k])))) for k in RESCORE_PARAMS if isinstance(d, dict) and k in d and isinstance(d[k], (int, float))}
    a, b = clean(r.get("A")), clean(r.get("B"))
    before, after = (a, b) if a_is_before else (b, a)
    return {"before": before, "after": after, "notes": str(r.get("notes", ""))[:300], "label": LABEL}


# ------------------------------------------------------------------ c) Wonder critic

def critique(before_video: Path, after_video: Path, group: str, ops: list[dict], llm: EditLLM, work: Path, label: str) -> dict:
    """The critic watches both videos. Falls back to frame strips if the API refuses video. Returns
    {"verdict": "approve|reject|unavailable", "reasons": [...], "flags": [...], "mode": "video|frames"}."""
    from app.studio.autoedit import config as cfg
    changes = "\n".join(f"- {o['type']}: {o.get('reason', '')} {o.get('params', {})}" for o in ops)[:1500]
    prompt = f"""You are the critic of an automatic video editor. Video 1 is the ORIGINAL (before); video 2 is the EDITED version (after) with
this group of changes ("{group}") applied:
{changes}

Watch and listen to both. Decide whether the edited version is better for a viewer scrolling Instagram Reels, and look for faults the
edit introduced. Flags you may raise (only when real): "text_over_face", "cut_mid_word", "caption_timing", "audio_pumping" (volume
ducking/normalising that breathes or clips), "tone_mismatch" (the edit feels off for this creator or content).
Return ONLY JSON: {{"verdict": "approve" or "reject", "reasons": ["<short reason>", ...], "flags": [{{"flag": "<name>", "detail": "<where/what>"}}]}}.
Approve only if the edited version is at least as good overall and has no blocking fault."""
    mode = "video"
    try:
        b, a = (work / "critic_before.mp4"), (work / "critic_after.mp4")
        if not b.exists():
            ffx.make_proxy(before_video, b)
        ffx.make_proxy(after_video, a)
        r = llm.json_call(prompt, cfg.critic_model(), f"critic:{label}", max_tokens=6000, videos=[b, a], est_in_tokens=5000)
    except CostCapExceeded:
        raise
    except Exception as e_video:
        mode = "frames"
        try:
            strips = _jpegs(before_video, work, 6) + _jpegs(after_video, work, 6)
            r = llm.json_call(prompt + "\n(The videos are given as frames: the first 7 images are the original, the next 7 the edit.)",
                              cfg.critic_model(), f"critic-frames:{label}", max_tokens=6000, images=strips, est_in_tokens=7000)
        except CostCapExceeded:
            raise
        except Exception as e_frames:
            return {"verdict": "unavailable", "reasons": [f"the critic could not be reached ({type(e_frames).__name__}: {str(e_frames)[:120]})"],
                    "flags": [], "mode": "none"}
    verdict = str(r.get("verdict", "")).lower()
    flags = [f for f in (r.get("flags") or []) if isinstance(f, dict) and f.get("flag")]
    return {"verdict": verdict if verdict in ("approve", "reject") else "reject", "reasons": [str(x)[:200] for x in (r.get("reasons") or [])][:5],
            "flags": flags[:6], "mode": mode}


# ------------------------------------------------------------------ keep or revert

def decide(group: str, targeted: str, scores: dict | None, objective_ok: bool, objective_failures: list[str], critic: dict | None) -> dict:
    """The rule: keep only if the targeted parameter improves by >= 2, no scored parameter drops by more than 3, objective checks
    pass and the critic approves. Returns {keep, reasons[]} (the reasons explain a revert or confirm a keep)."""
    reasons, keep = [], True
    if scores and scores.get("before") and scores.get("after"):
        b, a = scores["before"], scores["after"]
        gain = a.get(targeted, 0) - b.get(targeted, 0) if targeted in a and targeted in b else None
        if gain is None:
            keep = False
            reasons.append(f"the targeted parameter ({targeted}) could not be scored")
        elif gain < KEEP_MIN_GAIN:
            keep = False
            reasons.append(f"{targeted} changed by {gain:+d} (needs at least +{KEEP_MIN_GAIN})")
        drops = [(k, a[k] - b[k]) for k in SCORED if k in a and k in b and a[k] - b[k] < -MAX_DROP]
        if drops:
            keep = False
            reasons.append("scored parameters dropped by more than 3: " + ", ".join(f"{k} {d:+d}" for k, d in drops))
        if keep:
            reasons.append(f"{targeted} {gain:+d}")
    else:
        keep = False
        reasons.append("the AI re-score was not available")
    if not objective_ok:
        keep = False
        reasons.append("objective checks failed: " + "; ".join(objective_failures))
    if critic is None or critic.get("verdict") == "unavailable":
        if config.critic_required():
            keep = False
            reasons.append("the critic was not available, so the change could not be verified")
    elif critic["verdict"] != "approve":
        keep = False
        reasons.append("the critic rejected it: " + "; ".join(critic.get("reasons") or ["no reason given"]))
    blocking = [f["flag"] for f in (critic or {}).get("flags", []) if f.get("flag") in BLOCKING_FLAGS]
    if blocking:
        keep = False
        reasons.append("the critic flagged: " + ", ".join(blocking))
    return {"keep": keep, "reasons": reasons}
