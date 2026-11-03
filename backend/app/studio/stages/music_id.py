"""Hackathon-built (Meta Global AI Developer Hackathon).

Music ID stage: ACRCloud identifies the track in 12-second windows of the reel's 16 kHz wav.

Windows: the start of the reel and the middle (a third one near the end for reels over 45 s); none for
reels under 6 s. If no window of the mix matches and signals produced a Demucs "rest" stem (everything
but the voice), the same windows are tried on that stem and the stage records which source matched.
A no-match is a normal answer (status "ok", matched False); only auth/limit errors (ACRCloud codes 3xxx)
and transport failures are errors. ACRCloud receives clips of the creator's own reel only.

The result is cross-checked with the music judgement of the Gemini audio listener and Spark's
music_present: agreement "true" when a match is backed by at least one of them (or no match and both
heard no music), "false" when a match contradicts both, "unknown" when neither said anything or music was
heard but not identified (original or unlisted music is not a contradiction).
"""
import time
from pathlib import Path

from app.studio import config
from app.studio.report import AudioStage, MusicIdStage, MusicWindow, SignalsStage
from app.studio.signals import acr

AUTH_OR_LIMIT_CODES = range(3000, 4000)  # ACRCloud: invalid key/signature, quota, QPS


def heard_music(audio: AudioStage) -> dict[str, bool | None]:
    """What the audio listeners said about music: {"gemini": bool|None, "spark": bool|None}."""
    gemini = audio.gemini.music.present if audio.gemini.status == "ok" and audio.gemini.music else None
    spark = audio.spark.music_present if audio.spark.status == "ok" else None
    return {"gemini": gemini, "spark": spark}


def agreement(matched: bool | None, heard: dict[str, bool | None]) -> tuple[str, str]:
    """("true" | "false" | "unknown", explanation)."""
    said = {k: v for k, v in heard.items() if v is not None}
    if matched is None or not said:
        return "unknown", "no music judgement from Gemini or Spark" if not said else "no identification result"
    detail = ", ".join(f"{k}: {'music' if v else 'no music'}" for k, v in said.items())
    if matched:
        return ("true", f"track identified and {detail}") if any(said.values()) else \
               ("false", f"track identified but both listeners heard no music ({detail})")
    if not any(said.values()):
        return "true", f"nothing identified and both listeners heard no music ({detail})"
    return "unknown", f"music heard but not identified, so it may be original or unlisted ({detail})"


def run_music_id(wav: Path | None, duration_s: float, signals: SignalsStage, audio: AudioStage, run_dir: Path,
                 identify_fn=acr.identify) -> MusicIdStage:
    start = time.time()
    stage = MusicIdStage()
    stage.heard_music = heard_music(audio)
    host, key, secret = config.acr_credentials()
    try:
        if not (host and key and secret):
            stage.status, stage.error = "skipped", "ACRCLOUD_HOST / ACRCLOUD_ACCESS_KEY / ACRCLOUD_ACCESS_SECRET not set"
            return stage
        if wav is None or not wav.exists():
            stage.status, stage.error = "skipped", "no audio track to identify"
            return stage
        starts = acr.window_starts(duration_s)
        if not starts:
            stage.status, stage.error = "skipped", f"reel is shorter than {config.ACR_MIN_REEL_S:g} s"
            return stage
        sources = [("mix", wav)]
        rest_rel = signals.demucs.stems.get("rest") if signals.demucs.status == "ok" else None
        if rest_rel and (run_dir / rest_rel).exists():
            sources.append(("rest_stem", run_dir / rest_rel))
        for source, path in sources:
            for s in starts:
                sample, w_start, w_end = acr.wav_window(path, s, config.ACR_WINDOW_S)
                http_status, body = identify_fn(sample, host, key, secret)
                code, msg, track = acr.parse_response(body)
                stage.windows.append(MusicWindow(start_s=round(w_start, 2), end_s=round(w_end, 2), source=source,
                                                 http_status=http_status, acr_code=code, acr_msg=msg, matched=track is not None))
                if code in AUTH_OR_LIMIT_CODES:
                    stage.status, stage.error = "error", f"ACRCloud refused the request (code {code}: {msg})"
                    return stage
                if track:
                    stage.matched, stage.source, stage.track = True, source, track
                    break
            if stage.matched:
                break
        if not stage.matched:
            stage.matched = False  # a normal "no match" answer
        stage.status = "ok"
        stage.agreement, stage.agreement_notes = agreement(stage.matched, stage.heard_music)
    except acr.AcrError as e:
        stage.status, stage.error = "error", config.redact(str(e))
    except Exception as e:  # never raise into the pipeline
        stage.status, stage.error = "error", config.redact(f"{type(e).__name__}: {e}")[:300]
    finally:
        stage.cost_usd = round(len(stage.windows) * config.ACRCLOUD_USD_PER_REQUEST, 6)
        stage.seconds = round(time.time() - start, 2)
    return stage
