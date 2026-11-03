"""Built during the Meta Global AI Developer Hackathon (new work).

Everything the planner and the executor need to know about the source video: probe data, a word-level transcript
(word, start, end), scene cuts, speech / silence map, audio-energy peaks, a music flag and the "analysis timestamps" that
Prysm's fix steps mention. Hinglish and Hindi (Devanagari) words are kept as the recogniser wrote them.
"""
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from app.studio.autoedit import ffx

# Clear fillers only. Hinglish ones are listed in Latin and Devanagari because the recogniser writes either.
FILLERS_EN = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "hmm", "hm", "ah", "ahh"}
FILLERS_HI = {"matlab", "toh", "तो", "मतलब", "अम्म", "उम्म", "आं", "अं"}
# Words that are only fillers on their own (never inside a phrase we could break): "matlab" and "toh" are real words,
# so they count only when isolated by a pause on both sides (see find_fillers).
AMBIGUOUS_FILLERS = {"matlab", "toh", "तो", "मतलब"}

TIME_RE = re.compile(r"(?<![\d.:])(\d{1,2}):(\d{2})(?![\d])|(?<![\d.])(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)\b", re.I)
RANGE_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:s|sec)?\s*[-–—]\s*(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)\b", re.I)


@dataclass
class Inputs:
    duration: float
    width: int
    height: int
    fps: float
    orientation: str
    is_9x16: bool
    has_audio: bool
    language: str
    words: list[dict] = field(default_factory=list)        # {"word", "start", "end"}
    segments: list[dict] = field(default_factory=list)     # {"text", "start", "end"}
    scene_cuts: list[float] = field(default_factory=list)
    silences: list[dict] = field(default_factory=list)     # {"start", "end"}
    energy_peaks: list[dict] = field(default_factory=list)  # {"t", "level"}
    has_music: bool = False
    has_speech: bool = False
    analysis_times: list[dict] = field(default_factory=list)  # {"t", "step_id", "context"}
    fillers: list[dict] = field(default_factory=list)       # candidates: {"word", "start", "end"}
    asr_cost_usd: float = 0.0

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "Inputs":
        return Inputs(**d)


def clean_words(words: list[dict], duration: float) -> list[dict]:
    """Make a recogniser's word list usable: strip, drop empties, clamp to the duration, force start <= end <= next start."""
    out = []
    for w in words:
        text = (w.get("word") or "").strip()
        if not text:
            continue
        s, e = float(w.get("start", 0)), float(w.get("end", 0))
        s = min(max(s, 0.0), duration)
        e = min(max(e, s + 0.02), duration)
        out.append({"word": text, "start": round(s, 3), "end": round(e, 3)})
    out.sort(key=lambda w: (w["start"], w["end"]))
    for i in range(len(out) - 1):  # overlaps from the recogniser: the earlier word ends where the next one starts
        if out[i]["end"] > out[i + 1]["start"]:
            out[i]["end"] = max(out[i]["start"] + 0.02, out[i + 1]["start"])
    return out


def norm_token(word: str) -> str:
    return re.sub(r"[^\wऀ-ॿ]+", "", word.lower())


def find_fillers(words: list[dict], pause_s: float = 0.12) -> list[dict]:
    """Clear fillers only (English and Hinglish). The ambiguous Hinglish ones ('matlab', 'toh') count only when they stand alone
    between pauses; everything is an exact whole word of the transcript, so a cut is always on a word boundary."""
    out = []
    for i, w in enumerate(words):
        tok = norm_token(w["word"])
        if tok in FILLERS_EN or (tok in FILLERS_HI and tok not in AMBIGUOUS_FILLERS):
            out.append({"word": w["word"], "start": w["start"], "end": w["end"]})
        elif tok in AMBIGUOUS_FILLERS:
            prev_gap = w["start"] - words[i - 1]["end"] if i else 1.0
            next_gap = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 1.0
            if prev_gap >= pause_s and next_gap >= pause_s:
                out.append({"word": w["word"], "start": w["start"], "end": w["end"]})
    return out


def parse_times(text: str, duration: float) -> list[tuple[float, str]]:
    """Seconds mentioned in a fix step ('6.5s', '0.0s-3.0s', '0:12', '21s'), with the matched text."""
    found = []
    for m in RANGE_RE.finditer(text):
        for g in (m.group(1), m.group(2)):
            found.append((float(g), m.group(0)))
    for m in TIME_RE.finditer(text):
        if m.group(1) is not None:
            t = int(m.group(1)) * 60 + int(m.group(2))
        else:
            t = float(m.group(3))
        found.append((t, m.group(0)))
    seen, out = set(), []
    for t, ctx in found:
        if 0 <= t <= duration and round(t, 2) not in seen:
            seen.add(round(t, 2))
            out.append((round(t, 2), ctx))
    return sorted(out)


def energy_peaks(wav_path: str | Path, n: int = 6, win_s: float = 0.5) -> list[dict]:
    """Loudest, well-separated moments of the audio (RMS over `win_s` windows), strongest first."""
    import numpy as np
    import soundfile as sf
    y, sr = sf.read(str(wav_path), dtype="float32")
    if y.ndim > 1:
        y = y.mean(axis=1)
    hop = int(sr * 0.1)
    win = int(sr * win_s)
    if len(y) < win:
        return []
    rms = np.array([float(np.sqrt(np.mean(y[i:i + win] ** 2))) for i in range(0, len(y) - win + 1, hop)])
    if rms.max() <= 0:
        return []
    lvl = rms / rms.max()
    order = np.argsort(-lvl)
    peaks = []
    for idx in order:
        t = idx * 0.1 + win_s / 2
        if all(abs(t - p["t"]) >= 2.0 for p in peaks):
            peaks.append({"t": round(float(t), 2), "level": round(float(lvl[idx]), 3)})
        if len(peaks) >= n:
            break
    return peaks


def detect_music(analysis: dict | None) -> bool:
    aa = (analysis or {}).get("audioAnalysis") or {}
    g = aa.get("gemini") or {}
    return bool(g.get("hasMusic") or aa.get("hasMusic") or g.get("songConfirmed"))


def build_inputs(video_path: str | Path, work_dir: str | Path, analysis: dict | None, client=None, steps: list[dict] | None = None) -> Inputs:
    """Probe + ASR + cuts + silence map + energy. `client` is a MetaClient (word-level Groq Whisper); without a key the analysis'
    own words are used when it has them. `steps` = [{"id", "text"}] to harvest analysis timestamps from."""
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    info = ffx.probe(video_path)
    dur = info["duration"]
    words, segments, language, asr_cost = [], [], "unknown", 0.0
    wav16 = work / "audio16k.wav"
    peaks, silences = [], []
    if info["has_audio"]:
        ffx.extract_wav(video_path, wav16)
        silences = ffx.silences(video_path)
        peaks = energy_peaks(wav16)
        if client is not None:
            try:
                r = client.whisper(wav16)
                words, segments, language, asr_cost = r.words, r.segments, r.language or "unknown", r.cost_usd
            except Exception:
                words = []
        if not words:
            aa = (analysis or {}).get("audioAnalysis") or {}
            words, segments, language = aa.get("words") or [], aa.get("segments") or [], aa.get("language") or "unknown"
    words = clean_words(words, dur)
    segs = [{"text": (s.get("text") or "").strip(), "start": float(s["start"]), "end": float(s["end"])} for s in segments
            if s.get("start") is not None and s.get("end") is not None]
    analysis_times = []
    for st in steps or []:
        for t, ctx in parse_times(st["text"], dur):
            analysis_times.append({"t": t, "step_id": st["id"], "context": ctx})
    cuts = ffx.scene_cuts(video_path)
    return Inputs(duration=dur, width=info["width"], height=info["height"], fps=info["fps"], orientation=info["orientation"],
                  is_9x16=info["is_9x16"], has_audio=info["has_audio"], language=language, words=words, segments=segs,
                  scene_cuts=cuts, silences=silences, energy_peaks=peaks, has_music=detect_music(analysis),
                  has_speech=len(words) >= 3, analysis_times=analysis_times, fillers=find_fillers(words), asr_cost_usd=asr_cost)
