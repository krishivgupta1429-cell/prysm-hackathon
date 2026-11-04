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
    hesitations: list[dict] = field(default_factory=list)   # um/uh-like sounds the recogniser dropped: {"start", "end", "f0", "level_db"}
    asr_cost_usd: float = 0.0
    beats: list[float] = field(default_factory=list)         # beat times (s) of a music-driven reel
    bpm: float = 0.0
    music_driven: bool = False                               # music present and little speech: cuts snap to beats
    lufs: float | None = None                                # integrated loudness of the source (ebur128)
    true_peak_db: float | None = None
    noise_floor_db: float | None = None                      # level of the quietest 10 % of the audio (dBFS, 50 ms windows)
    speech_level_db: float | None = None                     # level of the loudest 10 %
    stats_version: int = 0                                   # 2 = noise floor measured inside silences only (older caches are re-measured)

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


ASR_PROMPT = "Umm, uh, so, like, you know, I mean, hmm. Uh, um, yeah, okay."   # primes the recogniser to keep disfluencies instead of cleaning them up


def _accepts_prompt(client) -> bool:
    import inspect
    try:
        return "prompt" in inspect.signature(client.whisper).parameters
    except (TypeError, ValueError):
        return False


def needs_conditioning(lufs: float | None, noise_floor_db: float | None) -> bool:
    return (lufs is not None and lufs < -21.0) or (noise_floor_db is not None and noise_floor_db > -50.0)


def noise_in_silences(wav_path: str | Path, silences: list[dict]) -> float | None:
    """Background noise level (dBFS RMS) measured only inside the stretches the silence detector found, 0.5 s of them at least. Music has no
    such stretches, so music is never mistaken for noise (a plain percentile of the whole track would call a guitar 'noise')."""
    import numpy as np
    import soundfile as sf
    y, sr = sf.read(str(wav_path), dtype="float32")
    if y.ndim > 1:
        y = y.mean(axis=1)
    parts = [y[int(s["start"] * sr) + int(0.1 * sr): int(s["end"] * sr) - int(0.1 * sr)] for s in silences if s.get("end") is not None and s["end"] - s["start"] >= 0.5]
    parts = [p for p in parts if len(p) > 0]
    if sum(len(p) for p in parts) < 1.0 * sr:   # under a second of real silence is not enough to call anything 'noise'
        return None
    z = np.concatenate(parts)
    return round(float(20 * np.log10(np.sqrt(np.mean(z ** 2)) + 1e-9)), 1)


def noise_stats(wav_path: str | Path) -> dict:
    """Loud level (90th percentile of 50 ms RMS) in dBFS, plus the 10th percentile (a rough floor that is only meaningful for speech)."""
    import numpy as np
    import soundfile as sf
    y, _sr = sf.read(str(wav_path), dtype="float32")
    if y.ndim > 1:
        y = y.mean(axis=1)
    win = int(_sr * 0.05)
    if len(y) < win * 20:
        return {}
    n = len(y) // win
    rms = np.sqrt(np.mean(y[: n * win].reshape(n, win) ** 2, axis=1)) + 1e-9
    db = 20 * np.log10(rms)
    return {"noise_floor_db": round(float(np.percentile(db, 10)), 1), "speech_level_db": round(float(np.percentile(db, 90)), 1)}


def find_hesitations(wav_path: str | Path, words: list[dict], noise_floor_db: float | None, min_len: float = 0.25, max_len: float = 1.2) -> list[dict]:
    """Filled pauses ("um", "uh", "mm") that a recogniser dropped from its transcript: a steady voiced sound (stable pitch for 0.25-1.2 s)
    that no recognised word explains. Only stretches outside the cores of the recognised words are examined, so a held vowel inside a word
    is never touched. Returns [{start, end, f0, level_db}]. Pitch tracking (pyin) runs on those stretches only."""
    import numpy as np
    import soundfile as sf
    y, sr = sf.read(str(wav_path), dtype="float32")
    if y.ndim > 1:
        y = y.mean(axis=1)
    dur = len(y) / sr
    # word cores: a recogniser often stretches a word's end over the pause after it, so only the first 0.12 s per letter (+0.25 s) count
    cores = []
    for w in words:
        end = min(w["end"], w["start"] + 0.25 + 0.1 * len(w["word"]))
        cores.append((w["start"], end))
    free, cur = [], 0.0
    for a, b in sorted(cores):
        if a - cur >= min_len:
            free.append((cur, a))
        cur = max(cur, b)
    if dur - cur >= min_len:
        free.append((cur, dur))
    import librosa
    hop = 160
    out = []
    for a, b in free:
        seg = y[int(max(a - 0.05, 0) * sr): int(min(b + 0.05, dur) * sr)]
        if len(seg) < 2048:
            continue
        f0, vf, _ = librosa.pyin(seg, fmin=70, fmax=350, sr=sr, frame_length=1024, hop_length=hop)
        rms = librosa.feature.rms(y=seg, frame_length=1024, hop_length=hop)[0][: len(f0)]
        db = 20 * np.log10(rms + 1e-9)
        t0 = max(a - 0.05, 0)
        i = 0
        while i < len(f0):
            if vf[i] and not np.isnan(f0[i]) and f0[i] > 75 and (noise_floor_db is None or db[i] > noise_floor_db + 6):
                j = i
                while j + 1 < len(f0) and vf[j + 1] and not np.isnan(f0[j + 1]) and f0[j + 1] > 75 and abs(np.log2(f0[j + 1] / f0[j])) < 0.03 and (noise_floor_db is None or db[j + 1] > noise_floor_db + 6):
                    j += 1
                length = (j - i + 1) * hop / sr
                if min_len <= length <= max_len:
                    out.append({"start": round(t0 + i * hop / sr, 3), "end": round(t0 + (j + 1) * hop / sr, 3), "f0": round(float(np.nanmean(f0[i:j + 1])), 1),
                                "level_db": round(float(db[i:j + 1].mean()), 1)})
                i = j + 1
            else:
                i += 1
    return out


BEAT_SNAP_S = 0.12
MUSIC_DRIVEN_MAX_SPEECH_SHARE = 0.35


def speech_share(words: list[dict], duration: float) -> float:
    return sum(w["end"] - w["start"] for w in words) / duration if duration else 0.0


def track_beats(wav_path: str | Path) -> tuple[list[float], float]:
    """Beat times and tempo of the audio (librosa beat tracker). ([], 0) when no steady beat is found."""
    import librosa
    import numpy as np
    y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
    if len(y) < sr * 3:
        return [], 0.0
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    times = librosa.frames_to_time(frames, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0])
    if len(times) < 6:
        return [], 0.0
    return [round(float(t), 3) for t in times], round(bpm, 1)


def snap_to_beat(t: float, beats: list[float], tol: float = BEAT_SNAP_S) -> float:
    """The nearest beat within `tol` seconds, else `t` unchanged."""
    if not beats:
        return t
    b = min(beats, key=lambda x: abs(x - t))
    return b if abs(b - t) <= tol else t


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
    peaks, silences, loud, noise = [], [], {}, {}
    asr_wav = wav16
    if info["has_audio"]:
        ffx.extract_wav(video_path, wav16)
        silences = ffx.silences(video_path)
        peaks = energy_peaks(wav16)
        loud = ffx.loudness(video_path)
        try:
            noise = noise_stats(wav16)
            noise["noise_floor_db"] = noise_in_silences(wav16, silences)
        except Exception:
            noise = {}
        if needs_conditioning(loud.get("lufs"), noise.get("noise_floor_db")):
            # a quiet or noisy recording makes the recogniser guess (wrong language, invented words): give it a levelled, de-noised copy
            asr_wav = work / "audio16k_asr.wav"
            try:
                ffx.run(["-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", "-af", "highpass=f=80,afftdn=nr=12:nf=-45,loudnorm=I=-16:TP=-1.5:LRA=11", str(asr_wav)])
            except ffx.FFmpegError:
                asr_wav = wav16
        if client is not None:
            try:
                r = client.whisper(asr_wav, prompt=ASR_PROMPT) if _accepts_prompt(client) else client.whisper(asr_wav)
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
    hes = []
    if info["has_audio"] and len(words) >= 3:
        try:
            hes = find_hesitations(wav16, words, noise.get("noise_floor_db"))
        except Exception:
            hes = []
    cuts = ffx.scene_cuts(video_path)
    has_music = detect_music(analysis)
    beats, bpm, music_driven = [], 0.0, False
    if info["has_audio"] and (has_music or len(words) < 3):
        try:
            beats, bpm = track_beats(wav16)
        except Exception:
            beats, bpm = [], 0.0
        music_driven = bool(beats) and speech_share(words, dur) < MUSIC_DRIVEN_MAX_SPEECH_SHARE
    return Inputs(duration=dur, width=info["width"], height=info["height"], fps=info["fps"], orientation=info["orientation"],
                  is_9x16=info["is_9x16"], has_audio=info["has_audio"], language=language, words=words, segments=segs,
                  scene_cuts=cuts, silences=silences, energy_peaks=peaks, has_music=has_music,
                  has_speech=len(words) >= 3, beats=beats, bpm=bpm, music_driven=music_driven, analysis_times=analysis_times,
                  stats_version=2, lufs=loud.get("lufs"), true_peak_db=loud.get("true_peak_db"), noise_floor_db=noise.get("noise_floor_db"), speech_level_db=noise.get("speech_level_db"), fillers=find_fillers(words), hesitations=hes, asr_cost_usd=asr_cost)
