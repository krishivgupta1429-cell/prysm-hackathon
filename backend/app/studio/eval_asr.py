"""Hackathon-built (Meta Global AI Developer Hackathon).

Offline scoring of every transcript voter against the video's burned-in captions.

    python -m app.studio.eval_asr [runs_dir]

For each saved report whose watch result has on-screen text, the caption text (in time order) is the
answer key and each voter is scored as 1 - WER (word error rate over normalised words, so higher is
better and a very wrong transcript can go below 0).

IMPORTANT: burned-in captions are only a PROXY answer key. They are often cleaned up, shortened or
styled, can lag or lead the audio, may cover only part of the speech, and for songs may show lyrics in
a different script than the audio is transcribed in. A low score can be the caption's fault, not the
voter's.
"""
import sys
from pathlib import Path

from app.studio import config
from app.studio.consensus import tokenize
from app.studio.report import Report

VOTERS = ["voice_transcribe", "whisper", "spark", "gemini", "consensus"]


def edit_distance(a: list[str], b: list[str]) -> int:
    """Word-level Levenshtein distance."""
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def score(reference: list[str], hypothesis: list[str]) -> float | None:
    """1 - WER, or None when there is no reference to compare with."""
    if not reference:
        return None
    return 1.0 - edit_distance(reference, hypothesis) / len(reference)


def caption_words(items, until_s: float | None = None) -> list[str]:  # until_s: only captions starting before it
    """Normalised words of the on-screen text in time order (consecutive duplicates removed)."""
    words, last = [], None
    for item in sorted(items, key=lambda x: x.start_s):
        if until_s is not None and item.start_s >= until_s:
            continue
        if item.text.strip() == last:
            continue
        last = item.text.strip()
        words += [t[0] for t in tokenize(item.text)]
    return words


def voter_texts(report: Report) -> dict[str, str]:
    s = report.stages
    return {"voice_transcribe": s.speech.voice_transcribe.transcript if s.speech.voice_transcribe.status == "ok" else "",
            "whisper": s.speech.whisper.text if s.speech.whisper.status == "ok" else "",
            "spark": s.audio.spark.quoted_words if s.audio.spark.status == "ok" else "",
            "gemini": s.audio.gemini.transcript if s.audio.gemini.status == "ok" else "",
            "consensus": s.consensus.consensus_text if s.consensus.status == "ok" else ""}


def evaluate(report: Report) -> dict | None:
    """{voter: score or None, ...} plus reference sizes; None when the reel has no burned-in text."""
    watch = report.stages.watch
    if watch.status != "ok" or not watch.on_screen_text:
        return None
    ref = caption_words(watch.on_screen_text)
    scores = {}
    for name, text in voter_texts(report).items():
        scores[name] = score(ref, [t[0] for t in tokenize(text)]) if text else None
    return {"scores": scores, "ref_words": len(ref)}


def format_table(results: dict[str, dict]) -> str:
    columns = VOTERS
    lines = ["Score = 1 - WER against the burned-in caption text (higher is better).",
             "CAUTION: burned-in captions are a PROXY answer key. They may be cleaned up, shortened, mistimed,",
             "or in a different script than the speech, so a low score may be the caption's fault.\n",
             f"{'reel':<14}{'ref words':<11}" + "".join(f"{c:<20}" for c in columns)]
    for reel, r in results.items():
        cells = "".join(f"{'n/a' if r['scores'].get(c) is None else format(r['scores'][c], '.2f'):<20}" for c in columns)
        lines.append(f"{reel:<14}{r['ref_words']:<11}{cells}")
    mean = {}
    for c in columns:
        vals = [r["scores"][c] for r in results.values() if r["scores"].get(c) is not None]
        mean[c] = sum(vals) / len(vals) if vals else None
    lines.append(f"{'mean':<14}{'':<11}" + "".join(f"{'n/a' if mean[c] is None else format(mean[c], '.2f'):<20}" for c in columns))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    runs = Path(args[0]).expanduser().resolve() if args else config.work_dir()
    results = {}
    for report_path in sorted(runs.glob("*/report.json")):
        report = Report.model_validate_json(report_path.read_text())
        r = evaluate(report)
        if r is None:
            print(f"{report_path.parent.name}: no burned-in on-screen text in the watch result, skipped")
        else:
            results[report_path.parent.name] = r
    if not results:
        print("no reports with burned-in text found in", runs)
        return 1
    table = format_table(results)
    print(table)
    (runs / "eval_asr.txt").write_text(table + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
