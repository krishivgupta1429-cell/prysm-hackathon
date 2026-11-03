"""Hackathon-built (Meta Global AI Developer Hackathon).

Runs the perception stages concurrently (asyncio + to_thread for the blocking HTTP calls), each under
its own timeout: watch, speech, subjects, the two audio listeners (Spark, Gemini) and signals (Modal GPU
models + CPU audio/shot features). When those have finished, consensus (pure compute), captions (Spark 1.3
editor) and music_id (ACRCloud, which uses signals' Demucs stem) run on their results, and finally
analysis (niche, scoring, edit intents) runs on each finished report.

The finished perception report is cached by the sha256 of the video (perception_cache.py): when only the
creator's niche or description changes, a rerun skips straight to the analysis. `progress(stage, status)` is
called as each stage starts ("running") and ends, so a caller can show real progress. `after_perception(report)`
is awaited once the perception stages are done and before the analysis, which is where a host app checks credits.
One stage or listener failing or timing out never stops the others; its error is recorded in the report.

Note: Python cannot kill a thread, so a timed-out stage's thread is abandoned. Stages run in daemon
threads, so an abandoned one (say a hung Modal call) neither keeps the process alive nor delays the report.
"""
import asyncio
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from app.studio import config, media, perception_cache
from app.studio import trends as trends_mod
from app.studio.analysis.creator import CreatorContext
from app.studio.consensus import build_consensus
from app.studio.meta_client import MetaClient
from app.studio.report import (AnalysisStage, AudioStage, CaptionsStage, ConsensusStage, GeminiAudioResult, MusicIdStage, Report,
                               SignalsStage, SparkAudioResult, SpeechStage, Stages, SubjectsStage, TrendsStage, VideoMeta, WatchStage)
from app.studio.stages.audio import build_audio_stage, run_gemini_audio, run_spark_audio
from app.studio.stages.analysis import run_analysis
from app.studio.stages.captions import run_captions
from app.studio.stages.music_id import run_music_id
from app.studio.stages.signals import run_signals
from app.studio.stages.speech import run_speech
from app.studio.stages.subjects import run_subjects
from app.studio.stages.watch import run_watch

ALL_STAGES = ("watch", "speech", "subjects", "audio", "captions", "signals", "music_id", "trends", "analysis")
CONSENSUS_TIMEOUT_S = 30


@dataclass
class RunResult:
    reports: list[Report]  # one per watch model
    run_dir: Path
    actual_cost_usd: float  # what this run really spent (shared stages counted once)
    cached: bool = False  # the perception stages came from the cache: only the analysis ran


async def _in_daemon_thread(fn):
    """Like asyncio.to_thread, but in a daemon thread: if the caller gives up (timeout), the thread cannot block exit."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()

    def deliver(setter, value):
        if not fut.done():  # the caller may have timed out and cancelled the future already
            setter(value)

    def work():
        try:
            result = fn()
        except BaseException as e:  # delivered to the awaiting coroutine
            outcome = (fut.set_exception, e)
        else:
            outcome = (fut.set_result, result)
        try:
            loop.call_soon_threadsafe(deliver, *outcome)
        except RuntimeError:  # the loop is already closed (the run finished without this stage)
            pass

    threading.Thread(target=work, daemon=True, name="studio-stage").start()
    return await fut


async def _guard(name: str, timeout: float, fn, make_error):
    """Run a blocking stage function in a daemon thread under a timeout; never raises."""
    start = time.time()
    try:
        return await asyncio.wait_for(_in_daemon_thread(fn), timeout)
    except asyncio.TimeoutError:
        return make_error(f"{name} timed out after {timeout:.0f}s", round(time.time() - start, 2))
    except Exception as e:
        return make_error(config.redact(f"{name} crashed: {type(e).__name__}: {e}"), round(time.time() - start, 2))


def _done(value):
    return asyncio.sleep(0, value)


def _notify(progress: Callable[[str, str], None] | None, stage: str, status: str) -> None:
    """Tell the caller how a stage is going; a failing callback never affects the analysis."""
    if progress is not None:
        try:
            progress(stage, status)
        except Exception:
            pass


async def _tracked(progress, stage: str, awaitable):
    """Await a stage coroutine and report its status when it finishes."""
    result = await awaitable
    _notify(progress, stage, getattr(result, "status", "ok"))
    return result


async def _trends_stage(progress, skip: set[str], creator: CreatorContext | None, platform: str, cache, fetch, watch_getter) -> TrendsStage:
    """Live trend grounding for the creator's niches. Fail-soft by contract: it returns a stage, it never raises or stalls."""
    if "trends" in skip:
        return TrendsStage(status="skipped", error="skipped by --skip")
    if creator is None or not creator.niches:
        return TrendsStage(status="skipped", error="no creator niche given")
    _notify(progress, "trends", "running")
    keywords: list[str] = []
    try:
        keywords = trends_mod.keywords_from_watch(await watch_getter())  # a few content keywords from the watch stage, if it is in time
    except Exception:
        pass
    try:
        return await asyncio.wait_for(
            fetch(creator.niches, platform, creator.description, keywords, api_key=config.gemini_key(), cache=cache),
            trends_mod.TIMEOUT_S + trends_mod.STAGE_GRACE_S)
    except Exception as e:
        return TrendsStage(status="unavailable", error=f"{type(e).__name__}", niches=list(creator.niches), platform=platform,
                           model=trends_mod.MODEL, keywords_used=keywords)


async def _analyse_report(report: Report, client: MetaClient, path: Path, skip: set[str], niche_override, repeat: int,
                          with_frames: bool, frames_dir: Path | None, creator: CreatorContext | None,
                          weights_override: dict | None, progress) -> None:
    """Run (or skip) the analysis stage on a finished report and store the result in it."""
    if "analysis" in skip:
        report.stages.analysis = AnalysisStage(status="skipped", error="skipped by --skip")
        _notify(progress, "analysis", "skipped")
        return
    _notify(progress, "analysis", "running")
    report.stages.analysis = await _guard(
        "analysis", config.TIMEOUT_ANALYSIS,
        lambda: run_analysis(report, client, path, niche_override, repeat, with_frames, frames_dir, creator, weights_override),
        lambda msg, secs: AnalysisStage(status="error", error=msg, seconds=secs))
    _notify(progress, "analysis", report.stages.analysis.status)


async def analyze_video(video: Path, out_root: Path, watch_models: list[str] | None = None,
                        skip: set[str] | None = None, client: MetaClient | None = None,
                        sam_fps: float | None = None, captions_lang: str | None = None,
                        signals_local: bool = False, niche_override: str | None = None, repeat: int = 2,
                        with_frames: bool = False, creator: CreatorContext | None = None,
                        weights_override: dict[str, float] | None = None,
                        progress: Callable[[str, str], None] | None = None, use_cache: bool = True,
                        cache_root: Path | None = None, platform: str = "", trends_cache=None, trends_fetch=None,
                        after_perception: Callable[[Report], Awaitable[None]] | None = None) -> RunResult:
    started = time.time()
    skip = skip or set()
    sam_fps = config.sam_fps(sam_fps)  # CLI override > STUDIO_SAM_FPS > 10
    watch_models = watch_models or [config.watch_model()]
    client = client or MetaClient()
    run_dir = out_root / video.stem
    raw_dir, media_dir = run_dir / "raw", run_dir / "media"
    run_dir.mkdir(parents=True, exist_ok=True)

    # ---- perception cache: the same video with the same options needs no new perception run
    sha = cache_key = None
    cache_root = cache_root or out_root  # a host app gives each job its own out_root but shares one cache
    if use_cache and len(watch_models) == 1:
        sha = await asyncio.to_thread(perception_cache.video_sha256, video)
        cache_key = perception_cache.options_key(watch=watch_models[0], sam_fps=sam_fps, captions_lang=captions_lang,
                                                 skip=sorted(skip - {"analysis"}), signals_local=signals_local)
        cached = await asyncio.to_thread(perception_cache.load, cache_root, sha, cache_key)
        if cached is not None:
            cached.video.path = str(video)
            for stage in ("watch", "speech", "subjects", "audio", "signals", "consensus", "captions", "music_id"):
                _notify(progress, stage, "cached")
            if after_perception is not None:
                await after_perception(cached)

            async def cached_watch():
                return cached.stages.watch

            cached.stages.trends = await _tracked(progress, "trends", _trends_stage(
                progress, skip, creator, platform, trends_cache, trends_fetch or trends_mod.fetch_trends, cached_watch))
            await _analyse_report(cached, client, run_dir / "analysis.json", skip, niche_override, repeat, with_frames,
                                  None, creator, weights_override, progress)
            cached.total_seconds = round(time.time() - started, 2)
            cached.total_cost_usd = cached.total_cost()
            return RunResult(reports=[cached], run_dir=run_dir, actual_cost_usd=client.total_cost, cached=True)

    info = await asyncio.to_thread(media.probe, video)
    want = {"frames"}
    if "watch" not in skip or "audio" not in skip or "signals" not in skip:
        want.add("proxy")  # Spark audio, Gemini and the signal models read the same proxy video
    if "speech" not in skip or "audio" not in skip or "signals" not in skip or "music_id" not in skip:
        want.add("wav")  # speech models, Gemini's fallback, signal models and music ID
    if "subjects" not in skip:
        want.add("sam")
    prepared = await asyncio.to_thread(media.prepare, video, media_dir, want, info.has_audio, sam_fps)
    duration = min(info.duration_s, config.MEDIA_MAX_SECONDS)

    def skipped(cls, why):
        return cls(status="skipped", error=why)

    # ---- watch (one task per model)
    watch_tasks = {}
    for model in watch_models:
        if "watch" in skip:
            watch_tasks[model] = _done(skipped(WatchStage, "skipped by --skip"))
        elif prepared.proxy is None:
            watch_tasks[model] = _done(WatchStage(status="error", model=model,
                                                  error=f"proxy prep failed: {prepared.errors.get('proxy')}"))
        else:
            watch_tasks[model] = _guard(
                f"watch[{model}]", config.TIMEOUT_WATCH,
                lambda m=model: run_watch(client, prepared.proxy, duration, m, raw_dir),
                lambda msg, secs, m=model: WatchStage(status="error", error=msg, seconds=secs, model=m))

    # ---- speech
    if "speech" in skip:
        speech_task = _done(skipped(SpeechStage, "skipped by --skip"))
    elif not info.has_audio:
        speech_task = _done(skipped(SpeechStage, "video has no audio track"))
    elif prepared.wav is None:
        speech_task = _done(SpeechStage(status="error", error=f"wav prep failed: {prepared.errors.get('wav')}"))
    else:
        speech_task = _guard("speech", config.TIMEOUT_SPEECH, lambda: run_speech(client, prepared.wav),
                             lambda msg, secs: SpeechStage(status="error", error=msg, seconds=secs))

    # ---- audio listeners (each guarded on its own so one cannot block the other)
    if "audio" in skip or not info.has_audio or prepared.proxy is None:
        why = ("skipped by --skip" if "audio" in skip else
               "video has no audio track" if not info.has_audio else f"proxy prep failed: {prepared.errors.get('proxy')}")
        spark_task = _done(SparkAudioResult(status="skipped", error=why))
        gemini_task = _done(GeminiAudioResult(status="skipped", error=why))
    else:
        spark_task = _guard("audio/spark", config.TIMEOUT_AUDIO,
                            lambda: run_spark_audio(client, prepared.proxy, raw_dir=raw_dir),
                            lambda msg, secs: SparkAudioResult(status="error", error=msg, seconds=secs))
        gemini_task = _guard("audio/gemini", config.TIMEOUT_AUDIO,
                             lambda: run_gemini_audio(client, prepared.proxy, prepared.wav, raw_dir=raw_dir),
                             lambda msg, secs: GeminiAudioResult(status="error", error=msg, seconds=secs))

    # ---- subjects
    if "subjects" in skip:
        subjects_task = _done(skipped(SubjectsStage, "skipped by --skip"))
    elif prepared.sam_clip is None:
        subjects_task = _done(SubjectsStage(status="error", error=f"SAM clip prep failed: {prepared.errors.get('sam')}"))
    else:
        async def subjects():
            clip = await asyncio.to_thread(media.probe, prepared.sam_clip)
            return await _guard("subjects", config.TIMEOUT_SUBJECTS,
                                lambda: run_subjects(client, prepared.sam_clip, clip.duration_s, raw_dir, sam_fps),
                                lambda msg, secs: SubjectsStage(status="error", error=msg, seconds=secs))
        subjects_task = subjects()

    # ---- signals (Modal GPU models + CPU features); never blocks or breaks the other stages
    if "signals" in skip:
        signals_task = _done(skipped(SignalsStage, "skipped by --skip"))
    else:
        signals_task = _guard("signals", config.TIMEOUT_SIGNALS,
                              lambda: run_signals(run_dir, prepared.frames_dir, prepared.proxy, prepared.wav, duration,
                                                  signals_local),
                              lambda msg, secs: SignalsStage(status="error", error=msg, seconds=secs))

    async def audio_pair():  # both listeners; the audio stage's own status is reported when the pair is done
        spark, gemini = await asyncio.gather(spark_task, gemini_task)
        stage = build_audio_stage(spark, gemini) if (spark.status != "skipped" or gemini.status != "skipped") \
            else AudioStage(status="skipped", error=spark.error, spark=spark, gemini=gemini)
        _notify(progress, "audio", stage.status)
        return stage

    for stage_name in ("watch", "speech", "subjects", "audio", "signals"):
        _notify(progress, stage_name, "running")
    models = list(watch_tasks)
    watch_futures = {m: asyncio.ensure_future(t) for m, t in watch_tasks.items()}
    first_watch = watch_futures[models[0]]

    async def watch_in_time():  # trends wait a little for the watch keywords, but never hold anything up
        try:
            return await asyncio.wait_for(asyncio.shield(first_watch), 45)
        except Exception:
            return None

    results = await asyncio.gather(*(_tracked(progress, "watch", t) for t in watch_futures.values()),
                                   _tracked(progress, "speech", speech_task), _tracked(progress, "subjects", subjects_task),
                                   audio_pair(), _tracked(progress, "signals", signals_task),
                                   _tracked(progress, "trends", _trends_stage(progress, skip, creator, platform, trends_cache,
                                                                              trends_fetch or trends_mod.fetch_trends, watch_in_time)))
    watch_results = dict(zip(models, results[:len(models)]))
    speech, subjects_stage, audio, signals_stage, trends_stage = results[len(models):]
    spark, gemini = audio.spark, audio.gemini

    # ---- consensus + captions, per watch report (the OCR voter and caption hints come from watch)
    async def post(model: str, watch: WatchStage, index: int):
        _notify(progress, "consensus", "running")
        consensus = await _guard("consensus", CONSENSUS_TIMEOUT_S, lambda: _timed(build_consensus, speech, audio, watch, duration),
                                 lambda msg, secs: ConsensusStage(status="error", error=msg, seconds=secs))
        _notify(progress, "consensus", consensus.status)
        if "captions" in skip:
            _notify(progress, "captions", "skipped")
            return consensus, skipped(CaptionsStage, "skipped by --skip")
        _notify(progress, "captions", "running")
        srt = run_dir / ("captions.srt" if index == 0 else f"captions_{model}.srt")
        captions = await _guard("captions", config.TIMEOUT_CAPTIONS,
                                lambda: run_captions(client, consensus, watch.on_screen_text if watch.status == "ok" else [],
                                                     srt, captions_lang),
                                lambda msg, secs: CaptionsStage(status="error", error=msg, seconds=secs))
        _notify(progress, "captions", captions.status)
        return consensus, captions

    async def music():
        if "music_id" in skip:
            _notify(progress, "music_id", "skipped")
            return skipped(MusicIdStage, "skipped by --skip")
        _notify(progress, "music_id", "running")
        stage = await _guard("music_id", config.TIMEOUT_MUSIC_ID,
                             lambda: run_music_id(prepared.wav, duration, signals_stage, audio, run_dir),
                             lambda msg, secs: MusicIdStage(status="error", error=msg, seconds=secs))
        _notify(progress, "music_id", stage.status)
        return stage

    music_stage, *posts = await asyncio.gather(music(), *(post(m, w, i) for i, (m, w) in enumerate(watch_results.items())))

    meta = VideoMeta(path=str(video), duration_s=info.duration_s, width=info.width, height=info.height, fps=info.fps)
    total_seconds = round(time.time() - started, 2)
    reports = []
    for (model, watch), (consensus, captions) in zip(watch_results.items(), posts):
        report = Report(video=meta, stages=Stages(
            watch=watch, speech=speech.model_copy(deep=True), subjects=subjects_stage.model_copy(deep=True),
            audio=audio.model_copy(deep=True), consensus=consensus, captions=captions,
            signals=signals_stage.model_copy(deep=True), music_id=music_stage.model_copy(deep=True),
            trends=trends_stage.model_copy(deep=True)),
            total_seconds=total_seconds)
        reports.append(report)

    if sha is not None:  # cache the perception report (before the analysis) for the next run on this video
        await asyncio.to_thread(perception_cache.save, cache_root, sha, cache_key, reports[0])
    if after_perception is not None:
        await after_perception(reports[0])

    # ---- analysis: on each finished report (it reads every other stage)
    await asyncio.gather(*(
        _analyse_report(r, client, run_dir / ("analysis.json" if i == 0 else f"analysis_{m}.json"), skip, niche_override, repeat,
                        with_frames, prepared.frames_dir, creator, weights_override, progress)
        for i, (r, m) in enumerate(zip(reports, watch_results))))
    total_seconds = round(time.time() - started, 2)
    for report in reports:
        report.total_seconds = total_seconds
        report.total_cost_usd = report.total_cost()
    return RunResult(reports=reports, run_dir=run_dir, actual_cost_usd=client.total_cost)


def _timed(fn, *args):
    """Run a pure stage function and stamp how long it took."""
    start = time.time()
    stage = fn(*args)
    stage.seconds = round(time.time() - start, 3)
    return stage
