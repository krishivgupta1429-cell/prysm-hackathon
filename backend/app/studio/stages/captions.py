"""Hackathon-built (Meta Global AI Developer Hackathon).

Captions stage: Spark 1.3 as a caption editor over the consensus transcript.

The model only proposes the text of each cue (casing, punctuation, line breaks, dropping stutters,
or a translation with --captions-lang). Everything checkable is checked in code, and a cue that
breaks a rule falls back to a deterministic wrap of the consensus text:
  - timing always comes from the consensus segments, never from the model; the final cues are validated
    and any out-of-order, overlapping, < 0.8 s or > 3.5 s cue is logged as a warning in the report
  - at most 2 lines of at most 32 characters
  - without translation, every word must come from that segment's consensus text (no added words)
Every edit, including each fallback, is listed in the stage result. A captions.srt file is written.
"""
import json
import time
from collections import Counter
from pathlib import Path

from app.studio import config
from app.studio.consensus import tokenize, validate_timing
from app.studio.meta_client import MetaClient, MetaError
from app.studio.report import CaptionCue, CaptionsStage, ConsensusStage, OnScreenText, TokenUsage
from app.studio.stages.watch import parse_watch_json

PROMPT = """You are a caption editor for a short vertical social video. Below are caption segments (already timed) made
from a consensus of several speech recognisers, with the words the recognisers disagreed on.

Return ONE JSON object only, no prose: {{"cues": [{{"index": <segment index>, "lines": ["line 1", "line 2"], "edits": ["what you changed"]}}]}}

Rules:
- Exactly one cue per segment, same index. Never merge, split, add or drop segments. Do not output times.
- At most {max_lines} lines per cue and at most {max_chars} characters per line.
{language_rule}
- You may fix capitalisation and punctuation, break lines at natural phrase boundaries, and drop an obvious stutter
  or duplicated word. List every change in "edits" (use [] when you changed nothing).
- The on-screen text below is a spelling hint only. Do not copy words from it that are not in the segment.

SEGMENTS (index, start_s, end_s, text, uncertain_words):
{segments}

ON-SCREEN TEXT (hint only):
{on_screen}"""

KEEP_WORDS_RULE = "- Use only words that appear in that segment's text. Never add a word."
TRANSLATE_RULE = ("- Translate each segment's text into {lang}, keeping its meaning and its cue boundaries. "
                  "Words of the target language are of course new; keep names unchanged.")


# ---------------------------------------------------------------- pure helpers (unit tested)

def srt_time(seconds: float) -> str:
    ms = int(round(max(seconds, 0.0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_srt(cues: list[CaptionCue]) -> str:
    blocks = [f"{n}\n{srt_time(c.start_s)} --> {srt_time(c.end_s)}\n" + "\n".join(c.lines)
              for n, c in enumerate(cues, 1)]
    return "\n\n".join(blocks) + "\n"


def write_srt(cues: list[CaptionCue], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_srt(cues), encoding="utf-8")
    return path


def wrap_lines(text: str, max_chars: int = config.CAPTION_MAX_LINE_CHARS,
               max_lines: int = config.CAPTION_MAX_LINES) -> list[str] | None:
    """Wrap into at most max_lines lines of at most max_chars, as balanced as possible. None if impossible."""
    words = text.split()
    if not words:
        return None
    if len(" ".join(words)) <= max_chars:
        return [" ".join(words)]
    if max_lines < 2:
        return None
    best = None
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        if len(a) <= max_chars and len(b) <= max_chars:
            score = max(len(a), len(b))
            if best is None or score < best[0]:
                best = (score, [a, b])
    return best[1] if best else None


def lines_ok(lines) -> bool:
    return (isinstance(lines, list) and 1 <= len(lines) <= config.CAPTION_MAX_LINES
            and all(isinstance(x, str) and x.strip() and len(x) <= config.CAPTION_MAX_LINE_CHARS for x in lines))


def words_subset(candidate: str, source: str) -> bool:
    """True when every word of candidate (normalised) occurs in source with at least that multiplicity."""
    need, have = Counter(t[0] for t in tokenize(candidate)), Counter(t[0] for t in tokenize(source))
    return all(have[w] >= c for w, c in need.items())


def validate_cue(proposed, source_text: str, translating: bool) -> str | None:
    """Reason the proposed lines are unusable, or None when they are fine."""
    if not lines_ok(proposed):
        return "lines break the 2 x 32 character limit"
    if not translating and not words_subset(" ".join(proposed), source_text):
        return "model added words that are not in the consensus"
    return None


def assemble_cues(segments, proposals: dict[int, dict], translating: bool) -> tuple[list[CaptionCue], list[str], int]:
    """Cues with consensus timing; model lines where valid, deterministic wrap otherwise."""
    cues, edits, fallbacks = [], [], 0
    for i, seg in enumerate(segments):
        prop = proposals.get(i) or {}
        lines = prop.get("lines")
        cue_edits = [str(e) for e in (prop.get("edits") or []) if e]
        problem = "model gave no cue for this segment" if not prop else validate_cue(lines, seg.text, translating)
        if problem:
            # a translation that only breaks the length limit is re-wrapped; anything else falls back to the consensus text
            text = lines_text(lines) if translating and lines_text(lines) else seg.text
            wrapped = wrap_lines(text) or wrap_lines(seg.text) or [seg.text[:config.CAPTION_MAX_LINE_CHARS]]
            lines, fallbacks = wrapped, fallbacks + 1
            cue_edits = [f"segment {i}: {problem}; used a deterministic wrap"]
        cues.append(CaptionCue(index=i, start_s=seg.start_s, end_s=seg.end_s, lines=lines, text=" ".join(lines),
                               edits=cue_edits))
        edits += [f"segment {i}: {e}" if not e.startswith("segment") else e for e in cue_edits]
    return cues, edits, fallbacks


def lines_text(lines) -> str:
    return " ".join(x for x in lines if isinstance(x, str)) if isinstance(lines, list) else ""


# ---------------------------------------------------------------- stage

def run_captions(client: MetaClient, consensus: ConsensusStage, on_screen: list[OnScreenText], srt_path: Path,
                 lang: str | None = None, model: str = config.CAPTION_MODEL) -> CaptionsStage:
    start = time.time()
    stage = CaptionsStage(model=model, language=lang, translated=bool(lang), srt_path=None)
    if consensus.status != "ok" or not consensus.segments:
        stage.status, stage.error = "skipped", "no consensus transcript to caption"
        return stage
    segs = consensus.segments
    low = {w.word.lower() for w in consensus.low_confidence_words}
    payload = [{"index": i, "start_s": s.start_s, "end_s": s.end_s, "text": s.text,
                "uncertain_words": [w for w in s.text.split() if w.lower() in low]} for i, s in enumerate(segs)]
    prompt = PROMPT.format(
        max_lines=config.CAPTION_MAX_LINES, max_chars=config.CAPTION_MAX_LINE_CHARS,
        language_rule=TRANSLATE_RULE.format(lang=lang) if lang else KEEP_WORDS_RULE,
        segments=json.dumps(payload, ensure_ascii=False),
        on_screen=json.dumps([t.text for t in on_screen], ensure_ascii=False))
    proposals: dict[int, dict] = {}
    tokens_in = tokens_out = 0
    error = None
    for attempt in (1, 2):
        try:
            call = client.complete(prompt if attempt == 1 else prompt + "\n\nReply with the JSON object only.", model)
        except MetaError as e:
            error = str(e)
            break
        tokens_in += call.tokens_in or 0
        tokens_out += call.tokens_out or 0
        stage.cost_usd = round(stage.cost_usd + call.cost_usd, 6)
        try:
            cues = parse_watch_json(call.text).get("cues")
            if not isinstance(cues, list):
                raise ValueError("no 'cues' list")
            proposals = {int(c["index"]): c for c in cues if isinstance(c, dict) and "index" in c}
            error = None
            break
        except (ValueError, KeyError, TypeError) as e:
            error, stage.raw_text = f"unparseable editor output: {str(e)[:100]}", call.text
    stage.cues, stage.edits, stage.fallback_cues = assemble_cues(segs, proposals, bool(lang))
    stage.warnings = validate_timing(stage.cues, "caption cue")  # out of order, overlapping, < 0.8 s, > 3.5 s
    stage.usage = TokenUsage(input_tokens=tokens_in, output_tokens=tokens_out)
    if error:  # the editor failed: captions are still produced, deterministically
        stage.status, stage.error = "partial", f"caption editor failed ({error}); deterministic captions used"
    else:
        stage.status, stage.raw_text = "ok", None
    stage.srt_path = str(write_srt(stage.cues, srt_path))
    stage.seconds = round(time.time() - start, 2)
    return stage
