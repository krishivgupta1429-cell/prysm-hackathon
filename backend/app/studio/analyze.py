"""Hackathon-built (Meta Global AI Developer Hackathon).

CLI for the Studio perception core.

    python -m app.studio.analyze <video_path> [--out DIR] [--compare-watch] [--skip watch,speech,subjects,audio,captions,signals,music_id]
                                 [--sam-fps N] [--captions-lang en] [--signals-local]
                                 [--niche NICHES] [--description TEXT] [--repeat N] [--with-frames] [--no-cache]

Writes <out>/<video_stem>/report.json, captions.srt and raw outputs (raw/, media/), and prints a readable summary.
--captions-lang translates the caption text (timing is kept); the default is the spoken language.
--signals-local runs DINOv2, CLIP and CLAP on the CPU instead of Modal (needs requirements-ml.txt).
--niche gives the creator's niches (1 to 3, comma-separated, e.g. "Fashion,Lifestyle"; the first is the primary
one) and --description the creator's description; with --niche the model does not detect a niche. Without it
the model detects the niche (CLI only). The perception report is cached by the video's sha256, so rerunning
with a different niche or description only reruns the analysis (--no-cache to disable).
--repeat N runs the scoring N times and reports the spread; --with-frames attaches up to 6 keyframes.
--compare-watch runs the watch stage with muse-spark-1.1 and muse-spark-1.3 and saves one report
per model (report_<model>.json) so the two reads can be judged side by side.
"""
import argparse
import asyncio
import sys
from pathlib import Path

import json

from app.studio import config
from app.studio.analysis.creator import CreatorContext
from app.studio.analysis.intents import top_fixes
from app.studio.analysis.niches import get_niche
from app.studio.analysis.schema import Parameter
from app.studio.pipeline import ALL_STAGES, analyze_video
from app.studio.report import Report

COMPARE_MODELS = ["muse-spark-1.1", "muse-spark-1.3"]


def _fmt_status(stage) -> str:
    return {"ok": "OK", "partial": "PART", "error": "ERR", "skipped": "SKIP"}[stage.status]


def summarize(report: Report, label: str = "") -> str:
    s, lines = report.stages, []
    w = s.watch
    lines.append(f"{'=' * 100}\n{Path(report.video.path).name}  {label}  "
                 f"{report.video.width}x{report.video.height} {report.video.duration_s:.1f}s\n{'=' * 100}")
    lines.append(f"{'stage':<10}{'status':<8}{'seconds':<10}{'cost':<12}note")
    for name, st in (("watch", w), ("speech", s.speech), ("subjects", s.subjects), ("audio", s.audio),
                     ("consensus", s.consensus), ("captions", s.captions), ("signals", s.signals),
                     ("music_id", s.music_id), ("analysis", s.analysis)):
        note = (st.error or "")[:90]
        lines.append(f"{name:<10}{_fmt_status(st):<8}{st.seconds:<10.1f}${st.cost_usd:<11.5f}{note}")
    lines.append(f"{'total':<10}{'':<8}{report.total_seconds:<10.1f}${report.total_cost_usd:<11.5f}")
    costs = {"watch": w.cost_usd, "speech": s.speech.cost_usd, "subjects": s.subjects.cost_usd, "audio": s.audio.cost_usd,
             "captions": s.captions.cost_usd, "signals": s.signals.cost_usd, "music_id": s.music_id.cost_usd,
             "analysis": s.analysis.cost_usd}
    lines.append("cost by stage: " + " | ".join(f"{k} ${v:.4f}" for k, v in costs.items()) + f" | total ${sum(costs.values()):.4f}")
    if w.status == "ok" and w.hook:
        lines.append(f"\nHook rating ({w.model}): {w.hook.attention_rating}/10 - {w.hook.rating_reason}")
        lines.append(f"Hook: {w.hook.what_happens}")
        lines.append("First 3 beats:")
        for b in w.timeline[:3]:
            lines.append(f"  {b.start_s:g}-{b.end_s:g}s  {b.what_happens}")
        lines.append(f"Format: {w.format_type} | niche: {w.niche_guess.niche if w.niche_guess else '?'} | "
                     f"CTA: {w.cta_text if w.cta_present else 'none'}")
    sp = s.speech
    if sp.status == "ok":
        lines.append(f"\nPrimary transcript [{sp.primary}] ({sp.routing_reason}):\n  {sp.primary_text[:300]}")
    au = s.audio
    if au.status in ("ok", "partial"):
        sp, ge = au.spark, au.gemini
        lines.append(f"\nAudio: spark={sp.status} (attempts {sp.attempts}) gemini={ge.status}"
                     + (f" ({ge.model_used}{', fallback' if ge.fallback_used else ''}, media={ge.media_sent})"
                        if ge.status == "ok" else ""))
        if ge.status == "ok":
            lines.append(f"  Gemini hook audio: {ge.hook_audio[:160]}")
    cs = s.consensus
    if cs.status == "ok":
        lines.append(f"Consensus [{', '.join(cs.voters_used)}; primary={cs.primary}; timing={cs.timing_source}]: "
                     f"{len(cs.segments)} segments, {len(cs.low_confidence_words)} low-confidence words\n  {cs.consensus_text[:300]}")
    cp = s.captions
    if cp.status in ("ok", "partial"):
        lines.append(f"Captions: {len(cp.cues)} cues, {len(cp.edits)} edits, {cp.fallback_cues} deterministic fallbacks"
                     f"{' (translated to ' + cp.language + ')' if cp.translated else ''} -> {cp.srt_path}")
    sg = s.signals
    if sg.status in ("ok", "partial"):
        mi = sg.modal
        temp = f"main {'cold' if mi.cold_main else 'warm'}, LanguageBind {'cold' if mi.cold_lb else 'warm' if mi.cold_lb is not None else 'n/a'}"
        lines.append(f"\nSignals (Modal {mi.gpu or 'n/a'}: {temp}, wall {mi.wall_seconds}s, "
                     f"GPU {mi.server_seconds}s):" if mi.ok else f"\nSignals (Modal unavailable: {(mi.error or '')[:90]}):")
        for key, m in sg.models.items():
            lines.append(f"  {key:<20}{m.status:<8}{(str(m.dims) + 'd') if m.dims else '-':<7}{m.seconds:<8.2f}s ${m.cost_usd:.5f}"
                         f"{'  [' + m.source + ']' if m.source else ''}{'  stand-in: ' + m.stand_in if m.stand_in else ''}"
                         f"{'  ' + m.error[:80] if m.status != 'ok' and m.error else ''}")
        dm, af, sc = sg.demucs, sg.audio_features, sg.cuts
        lines.append(f"  Demucs voice-to-music: {dm.voice_to_music_db} dB | tempo {af.tempo_bpm} bpm | "
                     f"cuts/10s {sc.cuts_per_10s} | loudness {af.integrated_loudness_lufs} LUFS | silence {af.silence_ratio}")
    mu = s.music_id
    if mu.status == "ok":
        t = mu.track
        lines.append("Music ID: " + (f"MATCH {t.title} - {', '.join(t.artists)} (score {t.score}, offset {t.play_offset_ms} ms, "
                                      f"source {mu.source})" if t else "no match") + f" | agreement with Gemini/Spark: {mu.agreement}")
    elif mu.status in ("error", "skipped"):
        lines.append(f"Music ID: {mu.status} ({(mu.error or '')[:100]})")
    lines += _analysis_lines(report)
    for name, tr in (("person", s.subjects.person), ("face", s.subjects.face)):
        if tr:
            lines.append(f"Subjects/{name}: visible_in_first_3s={tr.visible_in_first_3s} "
                         f"screen_time_ratio={tr.screen_time_ratio:.2f} first_seen={tr.first_seen_s}s "
                         f"mean_conf={tr.mean_confidence} mean_box_area={tr.mean_box_area_ratio}")
    return "\n".join(lines)


def _analysis_lines(report: Report) -> list[str]:
    an = report.stages.analysis
    if an.status != "ok":
        return [f"Analysis: {an.status} ({(an.error or '')[:100]})"] if an.status == "error" else []
    lines = [f"\nAnalysis: niche {an.niche} (confidence {an.niche_confidence:.2f}, {an.niche_source}"
             + (f"; all niches: {', '.join(an.niches)}" if len(an.niches) > 1 else "") + f") | overall "
             f"{an.overall_score} {an.band} | {an.runs} run(s)"
             + (f" | overall spread {an.overall_spread}" if an.runs > 1 else "")]
    if an.description_fit:
        fit = an.description_fit
        lines.append(f"  description fit: {fit['verdict']}{' (unsupported)' if fit.get('unsupported') else ''} - {fit['note'][:160]}")
    lines.append(f"  {'parameter':<30}{'score':<14}{'confidence':<12}{'kind':<10}note")
    for p in an.parameters:
        notes = []
        if p.unsupported:
            notes.append("UNSUPPORTED")
        if not p.stable:
            notes.append("unstable")
        if p.evidence_limited and p.label:
            notes.append(p.label)
        score = str(p.score) + (f" ({p.range[0]}-{p.range[1]})" if p.range else "")
        lines.append(f"  {p.name:<30}{score:<14}{(p.confidence or ''):<12}{'scored' if p.scored else 'advisory':<10}{'; '.join(notes)}")
    path = Path(an.analysis_file) if an.analysis_file else None
    if path and path.exists():
        data = json.loads(path.read_text())
        params = [Parameter.model_validate(p) for p in data["parameters"]]
        lines.append("  Top 3 fixes:")
        for rank, p, fix in top_fixes(params, get_niche(an.niche), 3):
            at = f" @{fix.target.start_s:g}-{fix.target.end_s:g}s" if fix.target else ""
            lines.append(f"    [{p.id}] {fix.title}{at} ({fix.fixable_by}{', auto' if fix.auto_applicable else ''})")
        lines.append(f"  Edit intents ({len(data['edit_intents'])}):")
        for i, it in enumerate(data["edit_intents"], 1):
            t = it["target"]
            at = f"{t['start_s']:g}-{t['end_s']:g}s" if t else "whole video"
            lines.append(f"    {i}. {it['intent']} @ {at} <- {it['source_parameter']}: {it['parameters'].get('title', '')}")
    if an.flags:
        lines.append(f"  Validator flags ({len(an.flags)}): " + " | ".join(an.flags[:6]) + (" ..." if len(an.flags) > 6 else ""))
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.studio.analyze", description=__doc__.splitlines()[2])
    ap.add_argument("video", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="output root (default: samples/studio_runs)")
    ap.add_argument("--compare-watch", action="store_true", help="run watch with muse-spark-1.1 and 1.3")
    ap.add_argument("--sam-fps", type=float, default=None,
                    help="frame rate of the clip sent to SAM (default 10, or STUDIO_SAM_FPS); lower it to cut cost")
    ap.add_argument("--captions-lang", default=None, metavar="LANG",
                    help="translate the captions into this language, e.g. en (default: the spoken language)")
    ap.add_argument("--signals-local", action="store_true",
                    help="run DINOv2, CLIP and CLAP on the CPU instead of Modal (the other signal models stay Modal-only)")
    ap.add_argument("--niche", default=None, metavar="NICHES", help="the creator's niche list (1-3, comma-separated); skips niche detection")
    ap.add_argument("--description", default=None, metavar="TEXT", help="the creator's description of the video (judged as description_fit)")
    ap.add_argument("--no-cache", action="store_true", help="do not reuse a cached perception report for this video")
    ap.add_argument("--repeat", type=int, default=2, metavar="N",
                    help="score N times (default 2), average each parameter and report its spread")
    ap.add_argument("--with-frames", action="store_true", help="attach up to 6 keyframes to the scoring call")
    ap.add_argument("--skip", default="", help="comma list of stages to skip: watch,speech,subjects,audio,captions,signals,music_id,analysis")
    args = ap.parse_args(argv)
    creator = None
    if args.niche or args.description:
        try:
            creator = CreatorContext.from_inputs(args.niche, args.description, strict=True)
        except ValueError as e:
            ap.error(str(e))
    if args.repeat < 1:
        ap.error("--repeat must be at least 1")
    skip = {x.strip() for x in args.skip.split(",") if x.strip()}
    bad = skip - set(ALL_STAGES)
    if bad:
        ap.error(f"unknown stage(s) in --skip: {sorted(bad)}")
    video = args.video.expanduser().resolve()
    if not video.is_file():
        ap.error(f"video not found: {video}")
    if not config.meta_key():
        ap.error("META_API_KEY is not set in backend/.env")

    try:
        sam_fps = config.sam_fps(args.sam_fps)
    except ValueError as e:
        ap.error(str(e))
    default = config.watch_model()
    models = [default] + [m for m in COMPARE_MODELS if m != default] if args.compare_watch else [default]
    out_root = (args.out or config.work_dir()).expanduser().resolve()
    result = asyncio.run(analyze_video(video, out_root, models, skip, sam_fps=sam_fps, captions_lang=args.captions_lang,
                                         signals_local=args.signals_local, creator=creator,
                                         repeat=args.repeat, with_frames=args.with_frames,
                                         use_cache=not args.no_cache))
    for model, report in zip(models, result.reports):
        name = "report.json" if model == models[0] else f"report_{model}.json"
        (result.run_dir / name).write_text(report.model_dump_json(indent=2))
        if args.compare_watch:
            (result.run_dir / f"report_{model}.json").write_text(report.model_dump_json(indent=2))
        print(summarize(report, f"[watch={model}]" if args.compare_watch else ""))
    print(f"\nactual spend this run: ${result.actual_cost_usd:.5f} | reports in {result.run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
