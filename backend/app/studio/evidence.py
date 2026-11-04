"""Built during the Meta Global AI Developer Hackathon (new work; existing models, new integration).

Phase 2: extra evidence for the original pipeline. The original prompts are not touched; only the inputs that already
have a slot get richer:

  speech          Groq Whisper (kept) + Meta Voice Transcribe + Muse Spark 1.2's listener -> one consensus transcript
                  with word-level timing, replacing the Whisper-only `whisper_data` (same keys, same segment shape)
  audio           Muse Spark 1.2 listens to the video next to Gemini; when Gemini is unavailable it fills the same
                  `gemini_audio` keys, otherwise it adds a second opinion
  subjects        SAM 3.1 person/face tracking -> a text block in the scoring prompt's `extra_signals` slot
  signals         DINOv2, V-JEPA 2, LanguageBind, EnCodec, CLAP and Demucs on Modal (the already-deployed app) ->
                  the embedding vectors the original code stores, plus measured facts for `extra_signals`
  music           the studio's ACRCloud windows (mix, then Demucs "rest" stem) when the original ACRCloud call found nothing

Each part has its own flag (EVIDENCE_VOICE_CONSENSUS, EVIDENCE_SPARK_AUDIO, EVIDENCE_SUBJECTS, EVIDENCE_SIGNALS,
EVIDENCE_MUSIC_ID; default on with AI_PROVIDER=meta, "0" turns one off) and fails soft: a part that errors or times out is
reported as such and the analysis carries on with what the original pipeline produced. Videos longer than 90 seconds skip
all of it (cost bound). Every Meta model is labelled "Meta model" and every pre-hackathon model "existing model / new
integration" in the Models used panel.
"""
import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from app.studio import config, media
from app.studio.consensus import build_consensus
from app.studio.meta_client import MetaClient
from app.studio.report import (AudioStage, ConsensusStage, GeminiAudioResult, MusicIdStage, SignalsStage, SpeechStage,
                               SubjectsStage, WatchStage)
from app.studio.stages.audio import run_spark_audio
from app.studio.stages.music_id import run_music_id
from app.studio.stages.signals import run_signals
from app.studio.stages.speech import run_speech
from app.studio.stages.subjects import run_subjects

log = logging.getLogger(__name__)

META = "Meta model"
EXISTING = "existing model / new integration"
FLAGS = {"voice": "EVIDENCE_VOICE_CONSENSUS", "spark_audio": "EVIDENCE_SPARK_AUDIO", "subjects": "EVIDENCE_SUBJECTS",
         "signals": "EVIDENCE_SIGNALS", "music": "EVIDENCE_MUSIC_ID"}
MAX_VIDEO_SECONDS = 90  # the studio layer's media limit; longer videos skip the extra evidence
OVERALL_TIMEOUT_S = 270.0


def flag(name: str) -> bool:
    """A part is on unless its variable is 0/false/off/no."""
    return (config.get_env(FLAGS[name]) or "1").strip().lower() not in ("0", "false", "off", "no")


def enabled_parts() -> dict[str, bool]:
    return {k: flag(k) for k in FLAGS}


@dataclass
class Evidence:
    speech: SpeechStage | None = None
    consensus: ConsensusStage | None = None
    audio: AudioStage | None = None
    subjects: SubjectsStage | None = None
    signals: SignalsStage | None = None
    music: MusicIdStage | None = None
    run_dir: Path | None = None
    notes: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    seconds: float = 0.0
    parts: dict[str, bool] = field(default_factory=dict)
    ledger: list = field(default_factory=list)  # the Meta client's cost entries (tokens, reasoning, units) for the cost breakdown


# ------------------------------------------------------------------ collecting

async def _guard(name: str, timeout: float, fn, notes: list[str]):
    """Run a blocking stage in a thread with a timeout; None (and a note) on failure, never an exception."""
    try:
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout)
        except asyncio.TimeoutError:
            raise
        except Exception as first:  # an error (not a timeout) is retried once before the step is reported as failed
            log.warning("[Evidence] %s failed (%s), retrying once", name, type(first).__name__)
            await asyncio.sleep(1.5)
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout)
    except Exception as e:  # includes asyncio.TimeoutError
        notes.append(f"{name}: {type(e).__name__}: {config.redact(str(e))[:200]}")
        log.warning("[Evidence] %s failed: %s", name, config.redact(str(e))[:200])
        return None


async def collect_evidence(video_path: str, work_dir: str, progress: Callable[[str, str], None] | None = None,
                           client: MetaClient | None = None) -> Evidence:
    """Run the enabled evidence parts in parallel on one prepared set of media files."""
    start = time.time()
    ev = Evidence(parts=enabled_parts())
    notes = ev.notes
    noted: set[str] = set()

    def note(stage, status):
        noted.add(stage)
        if progress:
            progress(stage, status)
    if not any(ev.parts.values()):
        note("music_id", "skipped")
        return ev
    try:
        video = Path(video_path)
        info = await asyncio.to_thread(media.probe, video)
        if info.duration_s > MAX_VIDEO_SECONDS + 0.5:
            notes.append(f"extra evidence skipped: video is {info.duration_s:.0f}s, longer than {MAX_VIDEO_SECONDS}s")
            note("music_id", "skipped")
            return ev
        client = client or MetaClient()
        run_dir = Path(work_dir) / "evidence"
        raw_dir, media_dir = run_dir / "raw", run_dir / "media"
        run_dir.mkdir(parents=True, exist_ok=True)
        ev.run_dir = run_dir
        duration = min(info.duration_s, config.MEDIA_MAX_SECONDS)
        want = set()
        if ev.parts["spark_audio"] or ev.parts["signals"]:
            want.add("proxy")
        if info.has_audio and (ev.parts["voice"] or ev.parts["signals"] or ev.parts["music"]):
            want.add("wav")
        if ev.parts["signals"]:
            want.add("frames")
        if ev.parts["subjects"]:
            want.add("sam")
        prepared = await asyncio.to_thread(media.prepare, video, media_dir, want, info.has_audio, config.sam_fps())
        for k, err in prepared.errors.items():
            notes.append(f"media prep {k}: {err[:160]}")

        async def speech():
            if not (ev.parts["voice"] and prepared.wav):
                return None
            note("consensus", "running")
            return await _guard("speech", config.TIMEOUT_SPEECH, lambda: run_speech(client, prepared.wav), notes)

        async def spark():
            if not (ev.parts["spark_audio"] and prepared.proxy and info.has_audio):
                return None
            return await _guard("spark audio", config.TIMEOUT_AUDIO,
                                lambda: run_spark_audio(client, prepared.proxy, raw_dir=raw_dir), notes)

        async def subjects():
            if not (ev.parts["subjects"] and prepared.sam_clip):
                return None
            note("subjects", "running")
            clip = await asyncio.to_thread(media.probe, prepared.sam_clip)
            out = await _guard("subjects", config.TIMEOUT_SUBJECTS,
                               lambda: run_subjects(client, prepared.sam_clip, clip.duration_s, raw_dir, config.sam_fps()), notes)
            note("subjects", out.status if out else "error")
            return out

        async def signals():
            if not (ev.parts["signals"] and prepared.frames_dir):
                return None
            note("gpu_signals", "running")
            out = await _guard("signals", config.TIMEOUT_SIGNALS,
                               lambda: run_signals(run_dir, prepared.frames_dir, prepared.proxy, prepared.wav, duration, False), notes)
            note("gpu_signals", out.status if out else "error")
            return out

        ev.speech, spark_result, ev.subjects, ev.signals = await asyncio.wait_for(
            asyncio.gather(speech(), spark(), subjects(), signals()), OVERALL_TIMEOUT_S)
        if spark_result is not None:
            ev.audio = AudioStage(status=spark_result.status, spark=spark_result,
                                  gemini=GeminiAudioResult(status="skipped", error="run by the original pipeline"))
        if ev.speech is not None and ev.speech.status in ("ok", "partial"):
            ev.consensus = await _guard("consensus", 60, lambda: build_consensus(
                ev.speech, ev.audio or AudioStage(), WatchStage(status="skipped"), duration), notes)
            note("consensus", ev.consensus.status if ev.consensus else "error")
        elif ev.speech is not None:
            note("consensus", "skipped")
        if ev.parts["music"] and prepared.wav and not all(config.acr_credentials()):
            note("music_id", "not_configured")   # the music service is really not set up: the screen shows "(skipped)"
            ev.music = run_music_id(prepared.wav, duration, ev.signals or SignalsStage(), ev.audio or AudioStage(), run_dir)
        elif ev.parts["music"] and prepared.wav:
            note("music_id", "running")
            ev.music = await _guard("music id", config.TIMEOUT_MUSIC_ID, lambda: run_music_id(
                prepared.wav, duration, ev.signals or SignalsStage(), ev.audio or AudioStage(), run_dir), notes)
            note("music_id", ev.music.status if ev.music else "error")
    except Exception as e:  # nothing here may break an analysis
        notes.append(f"extra evidence stopped: {type(e).__name__}: {config.redact(str(e))[:200]}")
        log.warning("[Evidence] stopped: %s", config.redact(str(e))[:200])
    if "music_id" not in noted:   # not configured, no audio, or the evidence stopped early: show it as skipped, never stuck grey
        note("music_id", "skipped")
    ev.ledger = list(client.ledger) if client else []
    ev.cost_usd = round(client.total_cost if client else 0.0, 6)
    ev.cost_usd += _signals_cost(ev.signals)
    ev.seconds = round(time.time() - start, 1)
    return ev


def _signals_cost(s: SignalsStage | None) -> float:
    if not s:
        return 0.0
    if s.cost_usd:  # GPU seconds on the Modal container x the (configurable) rate: one figure for the total and the breakdown
        return round(s.cost_usd, 6)
    return round(sum(m.cost_usd for m in s.models.values()) + (s.demucs.cost_usd or 0.0), 6)


# ------------------------------------------------------------------ applying to the original slots

def _consensus_segments(c: ConsensusStage) -> list[dict]:
    return [{"start": round(s.start_s, 2), "end": round(s.end_s, 2), "text": s.text.strip()} for s in c.segments if s.text.strip()]


def merge_transcript(whisper: dict, ev: Evidence) -> dict:
    """Replace the Whisper-only transcript with the consensus one (same keys and segment shape the prompt reads)."""
    c = ev.consensus
    if not c or c.status not in ("ok", "partial") or not c.consensus_text.strip():
        return whisper
    segments = _consensus_segments(c)
    if not segments:
        return whisper
    out = dict(whisper)
    out["transcript"] = c.consensus_text.strip()
    out["segments"] = segments
    out["hasSpeech"] = len(out["transcript"]) > 5
    hook = " ".join(s["text"] for s in segments if s["end"] <= 3).strip()
    out["hookText"] = hook or whisper.get("hookText") or "No speech in first 3 seconds"
    out["transcriptSources"] = list(c.voters_used)
    out["transcriptConsensus"] = True
    if ev.speech and ev.speech.whisper.words:
        out["words"] = [{"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word} for w in ev.speech.whisper.words]
    return out


def merge_audio(gemini: dict | None, ev: Evidence) -> tuple[dict, str]:
    """Spark's listener fills the original `gemini_audio` keys when Gemini gave none, else it is a second opinion (text)."""
    gemini = dict(gemini or {})
    sp = ev.audio.spark if ev.audio else None
    if sp is None or sp.status != "ok":
        return gemini, ""
    has_gemini = bool(gemini) and any(k in gemini for k in ("hasMusic", "voiceQuality", "speechSummary"))
    balance = sp.voice_music_balance.strip()
    problems = "; ".join(p for p in sp.quality_problems if p) or "none heard"
    if not has_gemini:
        fill = ({
            "hasSpeech": bool(sp.quoted_words.strip()),
            "hasMusic": bool(sp.music_present),
            "speechLanguage": sp.language or "unknown",
            "speechSummary": sp.quoted_words.strip()[:500],
            "voiceQuality": balance or "unknown",
            "musicGenre": "unknown" if sp.music_present else "none",
            "musicMood": "unknown" if sp.music_present else "none",
            "musicEnergy": "unknown" if sp.music_present else "none",
            "overallAudioQuality": f"issues heard: {problems}",
            "songTitle": "unknown", "songArtist": "unknown",
            "listener": "muse-spark-1.2",
        })
        for k, v in fill.items():  # only what is missing: a song the ACRCloud call confirmed stays
            gemini.setdefault(k, v)
        return gemini, ""
    text = ("\nSECOND AUDIO LISTENER (Muse Spark 1.2 heard the same video): "
            f"music present: {sp.music_present}; voice and music balance: {balance or 'n/a'}; audio problems: {problems}.")
    return gemini, text


def merge_music(gemini: dict, acr_found: bool, ev: Evidence) -> dict:
    """The studio's ACRCloud windows when the original single-pass call found nothing."""
    m = ev.music
    if acr_found or not m or not m.matched or not m.track or not m.track.title:
        return gemini
    gemini = dict(gemini or {})
    gemini["songTitle"] = m.track.title
    gemini["songArtist"] = ", ".join(m.track.artists)
    gemini["songConfidence"] = m.track.score or 0
    gemini["songConfirmed"] = True
    gemini["hasMusic"] = True
    return gemini


def embeddings_from_signals(ev: Evidence) -> dict:
    """The vectors the original code stores (visual 768, motion 1024, audio 512, joint 768) from the Modal models."""
    s = ev.signals
    if not s or not ev.run_dir:
        return {}
    wanted = {"visual": ("dinov2", "dinov2_mean.npy", 768), "motion": ("vjepa2", "vjepa2.npy", 1024),
              "audio": ("clap", "clap.npy", 512), "joint": ("languagebind_video", "languagebind_video.npy", 768)}
    out = {}
    for key, (model, fname, dims) in wanted.items():
        sig = s.models.get(model)
        path = ev.run_dir / "signals" / fname
        if sig and sig.status == "ok" and path.exists():
            vec = np.load(path).astype(np.float32).ravel()
            if vec.size == dims:
                out[key] = [round(float(x), 6) for x in vec]
    return out


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / d) if d else 0.0


def signals_text(ev: Evidence) -> str:
    """Measured facts from the signal models, as plain lines for the scoring prompt's extra-signals slot."""
    s = ev.signals
    if not s or s.status not in ("ok", "partial"):
        return ""
    lines = []
    cuts = s.cuts
    if cuts.status == "ok" and cuts.cut_times_s is not None:
        shown = ", ".join(f"{t:.1f}s" for t in cuts.cut_times_s[:12])
        lines.append(f"- Scene cuts: {len(cuts.cut_times_s)}" + (f" at {shown}" if shown else "") +
                     (f"; average shot {cuts.avg_shot_length_s:.1f}s" if cuts.avg_shot_length_s else ""))
    if ev.run_dir:
        frames_path = ev.run_dir / "signals" / "dinov2_frames.npy"
        sig = s.models.get("dinov2")
        if sig and sig.status == "ok" and frames_path.exists():
            f = np.load(frames_path).astype(np.float64)
            if f.ndim == 2 and len(f) > 2:
                d = [1 - _cos(f[i], f[i + 1]) for i in range(len(f) - 1)]
                j = int(np.argmax(d))
                lines.append(f"- Visual change between consecutive seconds (DINOv2): average {np.mean(d):.2f}, biggest jump {d[j]:.2f} "
                             f"at {j + 1}s (0 = identical frame, higher = a new look); {sum(x < 0.05 for x in d)} of {len(d)} seconds are near-static")
        lb_v, lb_a = ev.run_dir / "signals" / "languagebind_video.npy", ev.run_dir / "signals" / "languagebind_audio.npy"
        if lb_v.exists() and lb_a.exists():
            sim = _cos(np.load(lb_v).astype(np.float64).ravel(), np.load(lb_a).astype(np.float64).ravel())
            lines.append(f"- Audio and picture belong together (LanguageBind similarity of the video and the audio): {sim:.2f} (about 0.3+ means they fit)")
    d = s.demucs
    if d.status == "ok" and d.voice_to_music_db is not None:
        who = "voice clearly on top of the music/background" if d.voice_to_music_db > 3 else \
            "voice and music/background about equal" if d.voice_to_music_db > -3 else "music/background louder than the voice"
        lines.append(f"- Voice vs music (Demucs separation): voice is {d.voice_to_music_db:+.1f} dB relative to everything else: {who}")
    af = s.audio_features
    if af.status == "ok":
        bits = []
        if af.integrated_loudness_lufs is not None:
            bits.append(f"loudness {af.integrated_loudness_lufs:.1f} LUFS")
        if af.tempo_bpm:
            bits.append(f"tempo about {af.tempo_bpm:.0f} BPM")
        if af.silence_ratio is not None:
            bits.append(f"{af.silence_ratio * 100:.0f}% silence")
        if bits:
            lines.append("- Audio measurements: " + ", ".join(bits))
    enc = s.models.get("encodec")
    if enc and enc.status == "ok" and enc.meta:
        ent = enc.meta.get("codebook_entropy") or enc.meta.get("entropy")
        if isinstance(ent, (int, float)) and not math.isnan(ent):
            lines.append(f"- Audio complexity (EnCodec codebook entropy): {ent:.2f}")
    return ("\nMEASURED SIGNALS (from signal models, facts only):\n" + "\n".join(lines)) if lines else ""


def subjects_text(ev: Evidence) -> str:
    st = ev.subjects
    if not st or st.status not in ("ok", "partial"):
        return ""
    lines = []
    for label, tr in (("Person", st.person), ("Face", st.face)):
        if tr is None:
            continue
        if tr.detected_frames == 0:
            lines.append(f"- {label}: never detected on screen")
            continue
        lines.append(f"- {label}: on screen {tr.screen_time_ratio * 100:.0f}% of the video, first seen at {tr.first_seen_s:.1f}s, "
                     f"{'visible' if tr.visible_in_first_3s else 'NOT visible'} in the first 3 seconds"
                     + (f", fills about {tr.mean_box_area_ratio * 100:.0f}% of the frame" if tr.mean_box_area_ratio else "")
                     + (f", up to {tr.object_count} at once" if tr.object_count and tr.object_count > 1 else ""))
        gaps = _gaps(tr.presence_per_second)
        if gaps:
            lines.append(f"  {label} absent during: " + ", ".join(f"{a}-{b}s" for a, b in gaps[:5]))
    return ("\nON-SCREEN SUBJECTS (SAM 3.1 tracking, facts only):\n" + "\n".join(lines)) if lines else ""


def _gaps(presence: list[int], min_len: int = 2) -> list[tuple[int, int]]:
    out, start = [], None
    for i, p in enumerate(presence + [1]):
        if not p and start is None:
            start = i
        elif p and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def extra_signals_text(ev: Evidence, audio_second_opinion: str = "") -> str:
    return "".join(t for t in (subjects_text(ev), signals_text(ev), audio_second_opinion) if t)


# ------------------------------------------------------------------ Models used panel

def models_used(ev: Evidence | None, *, whisper_ok: bool, gemini_ok: bool, acr_found: bool, trends_ok: bool,
                embeddings_ok: bool = False) -> list[dict]:
    """Rows for the results page. Meta models say 'Meta model'; pre-hackathon models say 'existing model / new integration'."""
    def row(step, model, ok, label, extra=""):
        return {"step": step, "model": model, "status": "ok" if ok else "unavailable",
                "note": label + (f" · {extra}" if extra else "")}
    rows = [row("Watching the video", "Muse Spark 1.3", True, META),
            row("Scoring the 10 parameters and report", "Muse Spark 1.3", True, META),
            row("ARIA chat", "Muse Spark 1.3", True, META),
            row("Speech transcript", "Groq Whisper large-v3-turbo", whisper_ok, EXISTING),
            row("Audio listener", "Gemini 2.5 Pro", gemini_ok, EXISTING),
            row("Music identification", "ACRCloud", acr_found, EXISTING, "" if acr_found else "no match")]
    if ev is None:
        rows.append(row("Live trends", "Gemini 2.5 Flash + Google Search", trends_ok, EXISTING))
        return rows
    p = ev.parts
    if p.get("voice"):
        ok = bool(ev.speech and ev.speech.voice_transcribe.status == "ok")
        rows.append(row("Second transcript", "Meta Voice Transcribe", ok, META,
                        "consensus with Groq Whisper" if ev.consensus and ev.consensus.status in ("ok", "partial") else ""))
    if p.get("spark_audio"):
        rows.append(row("Second audio listener", "Muse Spark 1.2", bool(ev.audio and ev.audio.spark.status == "ok"), META))
    if p.get("subjects"):
        rows.append(row("People and objects on screen", "SAM 3.1", bool(ev.subjects and ev.subjects.status in ("ok", "partial")), META))
    if p.get("signals"):
        names = [("dinov2", "Visual embedding", "DINOv2"), ("vjepa2", "Motion embedding", "V-JEPA 2"),
                 ("languagebind_video", "Audio-visual match", "LanguageBind"), ("encodec", "Audio complexity", "EnCodec"),
                 ("clap", "Audio embedding", "CLAP")]
        for key, step, label in names:
            sig = ev.signals.models.get(key) if ev.signals else None
            rows.append(row(step, label, bool(sig and sig.status == "ok"), EXISTING,
                            f"stand-in: {sig.stand_in}" if sig and sig.stand_in else ""))
        rows.append(row("Voice and music separation", "Demucs", bool(ev.signals and ev.signals.demucs.status == "ok"), EXISTING))
    if p.get("music") and ev.music is not None:
        rows.append(row("Music identification (second pass)", "ACRCloud windows + Demucs stem",
                        bool(ev.music.matched), EXISTING, "" if ev.music.matched else "no match"))
    rows.append(row("Live trends", "Gemini 2.5 Flash + Google Search", trends_ok, EXISTING))
    return rows
