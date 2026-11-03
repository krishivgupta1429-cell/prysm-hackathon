"""Built during the Meta Global AI Developer Hackathon (new work). Existing models and services, new integration:
the models and vendors wrapped here existed before the hackathon; only this integration code is new.

Audio listeners, two independent voters that hear the audio of the native video:

  a. Spark audio-in-video: muse-spark-1.2 on the 720p proxy (video_url data URL). Spark sometimes
     answers "NO AUDIO" for a file that has audio, so that answer (or unparseable JSON) is retried
     up to 3 times, then the listener is recorded as an error with the attempt count.
  b. Gemini audio analysis: gemini-2.5-pro (falls back to gemini-2.5-flash) with the media inline.
     The proxy video is sent when it is under 18 MB so A/V sync can be judged; otherwise the 16 kHz
     wav is sent and av_sync is "not assessed".

Gemini calls that fail with HTTP 503, 429 or a timeout are retried up to 5 times with exponential backoff
(2, 4, 8, 16, 30 s, each with up to 25% jitter) before the listener falls back to the other Gemini model or
gives up. A 429 whose message says "limit: 0" (a quota of zero, e.g. gemini-2.5-pro on the free tier) is not
retried: waiting cannot fix it. If Gemini still has no answer, the audio stage is "partial" (Spark alone) and
says so.

The pipeline guards each listener with its own timeout, so one failing never blocks the other.
"""
import random
import time
from pathlib import Path

from pydantic import ValidationError

from app.studio import config
from app.studio.meta_client import MetaClient, MetaError, MissingKey
from app.studio.report import (AudioStage, AvSync, GeminiAudioOutput, GeminiAudioResult, SparkAudioOutput,
                               SparkAudioResult, TokenUsage)
from app.studio.stages.watch import parse_watch_json

BACKOFF_SECONDS = (2, 4, 8, 16, 30)  # up to 5 retries after the first attempt
BACKOFF_JITTER = 0.25  # each wait is lengthened by a random 0-25%
_sleep = time.sleep  # replaced in tests so the schedule can be checked without waiting


def backoff_delays(rng=random) -> list[float]:
    """The waits before retry 1..5: 2, 4, 8, 16, 30 seconds, each with up to 25% jitter."""
    return [round(base * (1 + rng.uniform(0, BACKOFF_JITTER)), 2) for base in BACKOFF_SECONDS]


def is_transient(error: Exception) -> bool:
    """HTTP 503, HTTP 429 and timeouts are worth retrying; a zero-quota 429 and everything else are not."""
    if getattr(error, "timeout", False):
        return True
    status = getattr(error, "status", None)
    if status == 429:
        return "limit: 0" not in str(error).lower()
    return status == 503


SCRIPT_RULE = ("Write English words in Latin letters. Write Hindi AND Punjabi words in Devanagari script "
               "(the same script the other speech recognisers use, so transcripts can be compared word by word). "
               "Do not romanise Hindi or Punjabi.")

SPARK_PROMPT = f"""Listen to the audio track of this video. Reply with ONE JSON object and nothing else (no prose, no code fences):
{{
  "quoted_words": "the exact words spoken or sung, in order, from the whole video; do not invent words",
  "music_present": <true or false>,
  "language": "language(s) heard",
  "voice_music_balance": "how loud the voice is against the music, and which dominates",
  "quality_problems": ["clipping, noise, echo, dropouts... or an empty list"]
}}
{SCRIPT_RULE}
If you cannot hear any audio at all, reply exactly: NO AUDIO"""

GEMINI_PROMPT = f"""Analyse the audio of this short vertical social video. Reply with ONE JSON object only (no prose):
{{
  "transcript": "verbatim words spoken or sung, in order. Do not invent, complete or guess words you cannot hear.",
  "language": "language(s) heard",
  "speech_ratio": <0.0-1.0, share of the video with speech or singing>,
  "music": {{"present": <bool>, "description": "...", "mood": "...", "energy_0_10": <int>}},
  "voice_quality": {{"clarity_0_10": <int>, "noise": "background noise description or 'none'", "issues": ["..."]}},
  "av_sync": {{"ok": <bool>, "notes": "do lip movements and sound line up, any drift"}},
  "silence_segments": [{{"start_s": 0.0, "end_s": 0.0}}],
  "loudness_issues": ["clipping, sudden level jumps, too quiet... or an empty list"],
  "hook_audio": "what is heard in the first 3 seconds"
}}
{SCRIPT_RULE}"""


def run_spark_audio(client: MetaClient, proxy: Path, model: str = config.SPARK_AUDIO_MODEL, attempts: int = 3,
                    raw_dir: Path | None = None) -> SparkAudioResult:
    start = time.time()
    res = SparkAudioResult(model=model)
    tokens_in = tokens_out = 0
    why = "no attempt made"
    for n in range(1, attempts + 1):
        res.attempts = n
        try:
            call = client.watch_video(proxy, SPARK_PROMPT, model)
        except Exception as e:
            why = config.redact(f"{type(e).__name__}: {e}")
            continue
        tokens_in += call.tokens_in or 0
        tokens_out += call.tokens_out or 0
        res.cost_usd = round(res.cost_usd + call.cost_usd, 6)
        if raw_dir:
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"audio_spark_attempt{n}.txt").write_text(config.redact(call.text))
        if "NO AUDIO" in call.text:
            why = "model answered NO AUDIO"
            res.raw_text = call.text
            continue
        try:
            out = SparkAudioOutput.model_validate(parse_watch_json(call.text))
            if not out.quoted_words.strip() and out.music_present is None:
                raise ValueError("empty answer")
        except (ValueError, ValidationError) as e:
            why = f"unparseable answer: {str(e)[:120]}"
            res.raw_text = call.text
            continue
        for name in SparkAudioOutput.model_fields:
            setattr(res, name, getattr(out, name))
        res.status, res.error, res.raw_text = "ok", None, None
        break
    else:
        res.status, res.error = "error", f"failed after {attempts} attempts; last: {why}"
    res.usage = TokenUsage(input_tokens=tokens_in, output_tokens=tokens_out)
    res.seconds = round(time.time() - start, 2)
    return res


def _call_with_backoff(client: MetaClient, model: str, path: Path, mime: str, res: GeminiAudioResult):
    """One Gemini call; transient failures wait out the backoff schedule and retry (counted in res.retries)."""
    delays = backoff_delays()
    for i in range(len(delays) + 1):
        try:
            return client.gemini(model, GEMINI_PROMPT, path, mime, retries=0)  # the backoff here replaces the client's
        except MetaError as e:
            if i == len(delays) or not is_transient(e):
                raise
            res.retries += 1
            _sleep(delays[i])


def run_gemini_audio(client: MetaClient, proxy: Path | None, wav: Path | None, raw_dir: Path | None = None) -> GeminiAudioResult:
    start = time.time()
    res = GeminiAudioResult()
    use_video = proxy is not None and proxy.exists() and proxy.stat().st_size < config.GEMINI_MAX_VIDEO_BYTES
    if not use_video and wav is None:
        res.status, res.error = "error", "no media available for Gemini (proxy too large and no wav)"
        return res
    tokens_in = tokens_out = think = 0
    last_error = "no attempt made"
    models = [config.GEMINI_MODEL, config.GEMINI_FALLBACK_MODEL]

    def attempt(model: str, path: Path, mime: str):
        """One generateContent call + parse. Returns (output, call). Raises on API or parse failure."""
        call = _call_with_backoff(client, model, path, mime, res)
        if raw_dir:
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"audio_gemini_{model}_{res.attempts}.txt").write_text(config.redact(call.text))
        try:
            return GeminiAudioOutput.model_validate(parse_watch_json(call.text)), call
        except (ValueError, ValidationError) as e:
            res.raw_text = call.text
            raise ValueError(f"unparseable answer: {str(e)[:120]}") from None

    for model in models:
        path, mime = (proxy, "video/mp4") if use_video else (wav, "audio/wav")
        for retry in (1, 2):  # one retry on a parse failure, then move to the next model
            res.attempts += 1
            try:
                out, call = attempt(model, path, mime)
            except MissingKey as e:
                res.status, res.error = "skipped", str(e)
                res.seconds = round(time.time() - start, 2)
                return res
            except MetaError as e:
                last_error = config.redact(f"{model}: {e}")
                if use_video and wav and ("HTTP 413" in last_error or "too large" in last_error.lower()):
                    use_video, (path, mime) = False, (wav, "audio/wav")  # request too big: fall back to the wav
                    continue
                break  # unavailable / exhausted retries: try the fallback model
            except ValueError as e:
                last_error = f"{model}: {e}"
                continue
            tokens_in += call.tokens_in or 0
            tokens_out += call.tokens_out or 0
            think += call.thinking_tokens or 0
            res.cost_usd = round(res.cost_usd + call.cost_usd, 6)
            for name in GeminiAudioOutput.model_fields:
                setattr(res, name, getattr(out, name))
            res.status, res.error, res.raw_text = "ok", None, None
            res.model_used, res.fallback_used = model, model != config.GEMINI_MODEL
            res.media_sent = "video" if use_video else "wav"
            break
        if res.status == "ok":
            break
    else:
        res.status, res.error = "error", last_error
    if res.status == "ok" and res.media_sent == "wav":
        res.av_sync = AvSync(ok=None, notes="not assessed")  # no video was sent, so sync cannot be judged
    res.usage = TokenUsage(input_tokens=tokens_in, output_tokens=tokens_out)
    res.thinking_tokens = think
    res.seconds = round(time.time() - start, 2)
    return res


def build_audio_stage(spark: SparkAudioResult, gemini: GeminiAudioResult) -> AudioStage:
    """Combine the two listeners: ok if both worked, partial if one did, error if neither."""
    oks = [r.status == "ok" for r in (spark, gemini)]
    stage = AudioStage(spark=spark, gemini=gemini)
    stage.status = "ok" if all(oks) else ("partial" if any(oks) else "error")
    errors = [f"{n}: {r.error}" + (" (Gemini audio facts missing)" if n == "gemini" else "")
              for n, r in (("spark", spark), ("gemini", gemini)) if r.status == "error" and r.error]
    stage.error = "; ".join(errors) or None
    stage.seconds = round(max(spark.seconds, gemini.seconds), 2)  # they run concurrently
    stage.cost_usd = round(spark.cost_usd + gemini.cost_usd, 6)
    return stage
