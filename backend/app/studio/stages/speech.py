"""Hackathon-built (Meta Global AI Developer Hackathon).

Speech stage: Voice Transcribe and Groq Whisper run side by side on the same wav, and one is
picked as primary by language routing (no consensus vote yet):

  Indic (Devanagari/Gurmukhi/... script, or Whisper language hi/pa/ur/bn/ta/te/mr/gu) -> voice_transcribe
  everything else                                                                   -> whisper

Script detection needs at least INDIC_RATIO of the letters to be Indic, so a stray Hindi
fragment in an otherwise English transcript does not flip the routing.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.studio import config
from app.studio.meta_client import MetaClient, MetaError, MissingKey
from app.studio.report import Segment, SpeechStage, VoiceTranscribeResult, Word, WhisperResult

INDIC_RATIO = 0.3
INDIC_LANGS = {"hi", "pa", "ur", "bn", "ta", "te", "mr", "gu"}
LANG_NAMES = {"hindi": "hi", "punjabi": "pa", "panjabi": "pa", "urdu": "ur", "bengali": "bn", "tamil": "ta",
              "telugu": "te", "marathi": "mr", "gujarati": "gu", "english": "en"}
# Unicode blocks: Devanagari, Bengali, Gurmukhi, Gujarati, Tamil, Telugu (Arabic script is not counted)
INDIC_RANGES = [(0x0900, 0x097F), (0x0980, 0x09FF), (0x0A00, 0x0A7F), (0x0A80, 0x0AFF),
                (0x0B80, 0x0BFF), (0x0C00, 0x0C7F)]


def indic_ratio(text: str) -> float:
    """Share of alphabetic characters that are in an Indic script."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(any(lo <= ord(c) <= hi for lo, hi in INDIC_RANGES) for c in letters) / len(letters)


def normalise_language(lang: str | None) -> str | None:
    """Whisper reports names ("Hindi") or codes ("hi"); return a lower-case code where known."""
    if not lang:
        return None
    lang = lang.strip().lower()
    return LANG_NAMES.get(lang, lang)


def route_primary(vt_text: str, whisper_text: str, whisper_lang: str | None,
                  vt_ok: bool = True, whisper_ok: bool = True) -> tuple[str | None, str]:
    """Pick the primary transcript. Returns (engine or None, reason)."""
    if not vt_ok and not whisper_ok:
        return None, "no transcript available"
    lang = normalise_language(whisper_lang)
    indic = (vt_ok and indic_ratio(vt_text) >= INDIC_RATIO) or (whisper_ok and indic_ratio(whisper_text) >= INDIC_RATIO) \
        or (whisper_ok and lang in INDIC_LANGS)
    wanted = "voice_transcribe" if indic else "whisper"
    why = "Indic script or language detected" if indic else "non-Indic speech"
    if wanted == "voice_transcribe" and not vt_ok:
        return "whisper", f"{why}, but Voice Transcribe failed; fell back to Whisper"
    if wanted == "whisper" and not whisper_ok:
        return "voice_transcribe", f"{why}, but Whisper unavailable; fell back to Voice Transcribe"
    return wanted, f"{why} -> {wanted}"


def _voice(client: MetaClient, wav: Path) -> VoiceTranscribeResult:
    start = time.time()
    try:
        call = client.transcribe(wav)
        ratio = indic_ratio(call.transcript)
        guess = "indic" if ratio >= INDIC_RATIO else ("latin" if call.transcript else "unknown")
        return VoiceTranscribeResult(status="ok", transcript=call.transcript, language_guess=guess,
                                     audio_duration_ms=call.audio_duration_ms, seconds=call.seconds,
                                     cost_usd=call.cost_usd)
    except Exception as e:
        return VoiceTranscribeResult(status="error", error=config.redact(f"{type(e).__name__}: {e}"),
                                     seconds=round(time.time() - start, 2))


def _whisper(client: MetaClient, wav: Path) -> WhisperResult:
    start = time.time()
    try:
        call = client.whisper(wav)
        return WhisperResult(status="ok", language=call.language, granularity=call.granularity, text=call.text,
                             segments=[Segment(**s) for s in call.segments], words=[Word(**w) for w in call.words],
                             seconds=call.seconds, cost_usd=call.cost_usd)
    except MissingKey as e:
        return WhisperResult(status="skipped", error=str(e))
    except Exception as e:
        return WhisperResult(status="error", error=config.redact(f"{type(e).__name__}: {e}"),
                             seconds=round(time.time() - start, 2))


def run_speech(client: MetaClient, wav: Path) -> SpeechStage:
    start = time.time()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            vt_f, wh_f = pool.submit(_voice, client, wav), pool.submit(_whisper, client, wav)
            vt, wh = vt_f.result(), wh_f.result()
        stage = SpeechStage(voice_transcribe=vt, whisper=wh)
        primary, reason = route_primary(vt.transcript, wh.text, wh.language, vt.status == "ok", wh.status == "ok")
        stage.primary, stage.routing_reason = primary, reason
        stage.primary_text = {"voice_transcribe": vt.transcript, "whisper": wh.text}.get(primary or "", "")
        stage.status = "ok" if primary else "error"
        if not primary:
            stage.error = "; ".join(f"{n}: {r.error}" for n, r in (("voice_transcribe", vt), ("whisper", wh)) if r.error)
        stage.cost_usd = round(vt.cost_usd + wh.cost_usd, 6)
    except MetaError as e:
        stage = SpeechStage(status="error", error=str(e))
    except Exception as e:
        stage = SpeechStage(status="error", error=config.redact(f"{type(e).__name__}: {e}"))
    stage.seconds = round(time.time() - start, 2)
    return stage
