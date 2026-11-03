"""Hackathon-built (Meta Global AI Developer Hackathon).

Consensus transcript with caption timing. Pure functions, no network.

Voters: Voice Transcribe, Whisper, Spark 1.2 quoted words, Gemini transcript, and an "ocr" voter made
of the burned-in caption text (watch.on_screen_text) that only counts where its time range overlaps.

Two voting modes, decided per Whisper segment
  latin  Whisper's words are the reference. Every other voter is aligned against them with difflib,
         per word position the word at least 2 voters agree on is kept, a word that 3 or more voters
         covering its position lack is dropped, ties go to the primary listener, and words that no
         voter produced are never added. Words in another script than the reference are dropped per
         word (they cannot agree word for word) and noted.
  indic  Used where at least 2 non-Whisper voters wrote an Indic script (Devanagari, Gurmukhi, ...) over
         that segment, which is what happens when Whisper writes Latin phonetics for Hindi or Punjabi
         lyrics. Whisper does NOT vote on words there; it only supplies the segment times. The skeleton
         is the Indic voter that agrees most with the others (highest average difflib similarity, ties
         to the primary listener); the others are aligned against it and the same voting applies.
  Which voters wrote Indic text over which segment is found by locating each voter's script runs on the
  Whisper word axis: a run is placed by the words it shares with Whisper (its neighbours in the voter's
  own text for an unmatched run), or spread by character count when there is nothing to anchor it.

Timing comes from Whisper. A kept word whose Whisper word did not vote for it, and sits next to a word
the voting dropped, does not inherit that word's time: it is placed from the nearest surviving Whisper
words (by character count). In indic mode the text is spread over the block's Whisper segment times
by character count (marked "approximate"). A cue made mostly of such guesses falls back to its Whisper
segment's start and end.

Cues are cut at Whisper segment boundaries, at most 3 s and 60 characters, joined to a neighbour when
shorter than 1 s (if the result still fits 2 lines of 32 characters), and a cue never bridges a pause over 1 s.
"""
import difflib
import logging
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from app.studio import config
from app.studio.report import (AudioStage, ConsensusSegment, ConsensusStage, LowConfidenceWord, SegmentMode,
                               SpeechStage, WatchStage)
from app.studio.stages.speech import INDIC_RATIO, indic_ratio

log = logging.getLogger("studio.consensus")

PRIORITY = ["whisper", "voice_transcribe", "gemini", "spark", "ocr"]  # tie-break order after the primary
INDIC_PRIORITY = ["voice_transcribe", "gemini", "spark", "ocr"]  # tie-break in indic mode (no whisper)
INDIC_SCRIPTS = {"DEVANAGARI", "GURMUKHI", "BENGALI", "GUJARATI", "TAMIL", "TELUGU", "KANNADA", "MALAYALAM", "ORIYA"}
LOW_CONFIDENCE = 0.5
DROP_DISSENT = 3  # a single-voter word is dropped when this many voters cover its position without it
MAX_CUE_CHARS = 2 * config.CAPTION_MAX_LINE_CHARS - 4  # slack so two lines of <= 32 always fit
OCR_TOLERANCE_S = 0.75
SILENCE_BREAK_S = 1.0  # never bridge a pause longer than this inside a cue
SEC_PER_CHAR = 0.075  # speaking rate used to size words that have no reliable Whisper time
UNRELIABLE_RATIO = 0.5  # a cue with this share of guessed word times falls back to the Whisper segment bounds
WARN_MIN_CUE_S = 0.8
WARN_MAX_CUE_S = 3.5


# ---------------------------------------------------------------- normalisation

Token = tuple[str, str]  # (normalised, display)


def tokenize(text: str) -> list[Token]:
    """Split into words, keeping letters, digits and combining marks (so Devanagari/Gurmukhi stay intact).

    Python's \\w drops vowel signs (Mn/Mc), which would cut Hindi words apart, hence the manual scan.
    Returns (normalised, display) pairs; display keeps the original case without punctuation.
    """
    out: list[Token] = []
    word: list[str] = []

    def flush():
        if word:
            display = "".join(word)
            norm = display.replace("'", "").replace("’", "").lower()
            if norm:
                out.append((norm, display.replace("’", "'")))
            word.clear()

    for c in unicodedata.normalize("NFC", text or ""):
        cat = unicodedata.category(c)
        if c.isalnum() or cat in ("Mn", "Mc"):
            word.append(c)
        elif c in "'’" and word:
            word.append(c)
        elif cat == "Cf":  # zero-width joiners etc. stay inside a word
            continue
        else:
            flush()
    flush()
    return out


def script_of(word: str) -> str | None:
    """Unicode script of a word ("LATIN", "DEVANAGARI", "GURMUKHI", ...); None for digits/symbols."""
    for c in word:
        if c.isalpha():
            return unicodedata.name(c, "UNKNOWN").split()[0]
    return None


def dominant_script(tokens: list[Token]) -> str | None:
    counts = Counter(s for s in (script_of(t[0]) for t in tokens) if s)
    return counts.most_common(1)[0][0] if counts else None


def script_runs(tokens: list[Token]) -> list[tuple[str | None, int, int]]:
    """Maximal runs of consecutive tokens in one script as (script, start, end); symbols join the previous run."""
    runs: list[tuple[str | None, int, int]] = []
    for k, (norm, _) in enumerate(tokens):
        sc = script_of(norm)
        if runs and (sc is None or runs[-1][0] is None or sc == runs[-1][0]):
            runs[-1] = (runs[-1][0] or sc, runs[-1][1], k + 1)
        else:
            runs.append((sc, k, k + 1))
    return runs


# ---------------------------------------------------------------- alignment

@dataclass
class Voter:
    name: str
    tokens: list[Token]
    times: list[tuple[float, float] | None] | None = None  # per token; only the ocr voter has them


@dataclass
class RefToken:
    norm: str
    display: str
    start: float | None
    end: float | None
    seg: int
    source: str  # "word" | "segment" | "spread"


@dataclass
class Alignment:
    mapped: dict[int, int | None] = field(default_factory=dict)  # ref index -> voter token index (None = skipped)
    inserts: dict[int, list[int]] = field(default_factory=dict)  # gap index -> voter token indexes
    lo: int | None = None
    hi: int | None = None
    dissent_ok: bool = True  # False: a missing word is an abstention, not a dissent (ocr)


def align(ref: list[str], hyp: list[str]) -> Alignment:
    """difflib alignment of a voter against the reference. Unequal replace blocks are paired by position."""
    a = Alignment()
    sm = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                a.mapped[i1 + k] = j1 + k
        elif op == "delete":
            for i in range(i1, i2):
                a.mapped[i] = None
        elif op == "insert":
            a.inserts.setdefault(i1, []).extend(range(j1, j2))
        else:  # replace
            n, m = i2 - i1, j2 - j1
            for k in range(min(n, m)):
                a.mapped[i1 + k] = j1 + k
            for k in range(min(n, m), n):
                a.mapped[i1 + k] = None
            if m > n:
                a.inserts.setdefault(i2, []).extend(range(j1 + n, j2))
    kept = [i for i, j in a.mapped.items() if j is not None]
    if kept:
        a.lo, a.hi = min(kept), max(kept)
    return a


def _overlaps(t: tuple[float, float] | None, start: float | None, end: float | None) -> bool:
    if t is None or start is None or end is None:
        return True  # unknown timing: do not exclude
    return t[0] <= end + OCR_TOLERANCE_S and t[1] >= start - OCR_TOLERANCE_S


def _restrict_ocr(a: Alignment, voter: Voter, ref: list["RefToken"]) -> Alignment:
    """OCR only votes where its time range overlaps the reference word; elsewhere it abstains."""
    out = Alignment(dissent_ok=False)
    for i, j in a.mapped.items():
        if j is not None and _overlaps(voter.times[j], ref[i].start, ref[i].end):
            out.mapped[i] = j
    for g, js in a.inserts.items():
        near = [ref[k] for k in (g - 1, g) if 0 <= k < len(ref)]
        keep = [j for j in js if not near or any(_overlaps(voter.times[j], r.start, r.end) for r in near)]
        if keep:
            out.inserts[g] = keep
    kept = list(out.mapped)
    if kept:
        out.lo, out.hi = min(kept), max(kept)
    return out


# ---------------------------------------------------------------- reference (whisper) construction

def build_ref(words: list[dict], segments: list[dict], text: str, duration_s: float) -> tuple[list[RefToken], list[tuple[float, float]]]:
    """Reference tokens and segment bounds from Whisper words, else segments, else plain text."""
    seg_bounds = [(float(s["start"]), float(s["end"])) for s in segments]
    ref: list[RefToken] = []

    def seg_of(t: float) -> int:
        for i, (a, b) in enumerate(seg_bounds):
            if a <= t < b:
                return i
        return min(range(len(seg_bounds)), key=lambda i: abs(seg_bounds[i][0] - t)) if seg_bounds else 0

    if words:
        if not seg_bounds:
            seg_bounds = [(float(words[0]["start"]), float(words[-1]["end"]))]
        for w in words:
            toks = tokenize(w["word"])
            for k, (norm, disp) in enumerate(toks):
                span = (float(w["end"]) - float(w["start"])) / len(toks)
                ref.append(RefToken(norm, disp, float(w["start"]) + k * span, float(w["start"]) + (k + 1) * span,
                                    seg_of(float(w["start"])), "word"))
    elif segments:
        for si, s in enumerate(segments):
            toks = tokenize(s["text"])
            span = (float(s["end"]) - float(s["start"])) / max(len(toks), 1)
            for k, (norm, disp) in enumerate(toks):
                ref.append(RefToken(norm, disp, float(s["start"]) + k * span, float(s["start"]) + (k + 1) * span,
                                    si, "segment"))
    else:
        seg_bounds = [(0.0, max(duration_s, 0.001))]
        for norm, disp in tokenize(text):
            ref.append(RefToken(norm, disp, None, None, 0, "spread"))
    return ref, seg_bounds


# ---------------------------------------------------------------- voting

@dataclass
class OutTok:
    norm: str
    display: str
    conf: float
    seg: int  # Whisper segment index (for an indic block: its first segment)
    start: float | None
    end: float | None
    source: str  # "word" | "segment" | "spread" | "interp"
    votes: dict[str, str]
    gap: bool = False  # inserted between reference words
    slot: int | None = None  # reference position, None for inserted words
    anchored: bool = False  # the Whisper word at this position voted for the kept word
    timeless: bool = False  # the time was derived from neighbours, not from Whisper
    mode: str = "latin"
    block: int = 0  # indic block number (0 for latin words)
    agree: int = 0  # voters that produced this word
    pool: int = 1  # voters available (the reference counts as one)


def _unrelated(a: str, b: str) -> bool:
    """True when two words are not spelling variants of each other."""
    return difflib.SequenceMatcher(None, a, b).ratio() < 0.5


def _rank_fn(primary: str | None):
    order = ([primary] if primary else []) + [n for n in PRIORITY if n != primary]
    rank = {n: i for i, n in enumerate(order)}
    return lambda name: rank.get(name, len(rank))


def vote(ref: list[RefToken], ref_name: str, voters: list[Voter], primary: str | None) -> tuple[list[OutTok], set[int]]:
    """Per-position voting over the reference slots and the gaps between them.

    Returns the kept words and the set of reference positions whose word was dropped.
    Confidence = voters agreeing / max(voters covering the position, min(voters in the pool, 3)), so a word
    only one voter heard is never reported as fully confident when others were available.
    """
    rank = _rank_fn(primary)
    pool = len(voters) + 1
    n = len(ref)
    ref_norms = [t.norm for t in ref]
    aligns: dict[str, tuple[Voter, Alignment]] = {}
    for v in voters:
        a = align(ref_norms, [t[0] for t in v.tokens])
        if v.name == "ocr":
            a = _restrict_ocr(a, v, ref)
        aligns[v.name] = (v, a)
    out: list[OutTok] = []
    dropped: set[int] = set()

    def gap_tokens(g: int):
        cands: dict[tuple, list[str]] = {}
        for name, (v, a) in aligns.items():
            js = a.inserts.get(g)
            if js:
                cands.setdefault(tuple(v.tokens[j] for j in js), []).append(name)
        if not cands:
            return
        participants = set(n for names in cands.values() for n in names)
        if 0 < g < n:
            participants.add(ref_name)  # the reference covers the interior and dissents
        for name, (v, a) in aligns.items():
            if name not in participants and a.dissent_ok and a.lo is not None and a.lo < g <= a.hi:
                participants.add(name)
        best = max(len(names) for names in cands.values())
        if best >= 2:
            key = min((k for k, names in cands.items() if len(names) == best),
                      key=lambda k: min(rank(x) for x in cands[k]))
        elif primary in participants and primary in {x for names in cands.values() for x in names}:
            key = next(k for k, names in cands.items() if primary in names)
        else:
            return
        names = cands[key]
        seg = ref[g - 1].seg if g > 0 else (ref[0].seg if ref else 0)
        neighbours = {ref[k].norm for k in (g - 1, g) if 0 <= k < n}
        for norm, disp in key:
            if norm in neighbours:  # an insertion repeating the adjacent word is an alignment offset, not a new word
                continue
            out.append(OutTok(norm, disp, len(names) / max(len(participants), min(pool, 3)), seg, None, None, "interp",
                              {x: disp for x in names}, gap=True, agree=len(names), pool=pool))

    for i in range(n + 1):
        gap_tokens(i)
        if i == n:
            break
        votes: dict[str, str | None] = {ref_name: ref_norms[i]}
        shown: dict[str, str] = {ref_name: ref[i].display}
        for name, (v, a) in aligns.items():
            if i in a.mapped and a.mapped[i] is not None:
                votes[name] = v.tokens[a.mapped[i]][0]
                shown[name] = v.tokens[a.mapped[i]][1]
            elif a.dissent_ok and a.lo is not None and a.lo <= i <= a.hi:
                votes[name] = None  # covers this position but did not produce the word
        tally: dict[str, list[str]] = {}
        for name, w in votes.items():
            if w is not None:
                tally.setdefault(w, []).append(name)
        best = max(len(v) for v in tally.values())
        cands = [w for w, names in tally.items() if len(names) == best]
        if best >= 2:
            pick = min(cands, key=lambda w: min(rank(x) for x in tally[w]))
        else:
            primary_voted = primary in votes and votes[primary] is not None
            pick = votes[primary] if primary_voted else min(cands, key=lambda w: min(rank(x) for x in tally[w]))
            # a covering voter contradicts the word if it produced nothing there or an unrelated word
            # (a spelling variant of the same word does not count)
            contra = sum(1 for w in votes.values() if w is None or (w != pick and _unrelated(w, pick)))
            if contra >= DROP_DISSENT or (contra >= 2 and not primary_voted):
                dropped.add(i)  # a single voter heard it, several others covered the spot without it
                continue
        names = tally[pick]
        display = shown[min(names, key=rank)]
        out.append(OutTok(pick, display, len(names) / max(len(votes), min(pool, 3)), ref[i].seg, ref[i].start, ref[i].end,
                          ref[i].source, {k: shown[k] for k, w in votes.items() if w is not None},
                          slot=i, anchored=(pick == ref_norms[i]), agree=len(names), pool=pool))
    return out, dropped


# ---------------------------------------------------------------- timing

def _spread(tokens: list[OutTok], seg_bounds: list[tuple[float, float]]) -> None:
    """Spread tokens across the given segment times in proportion to character count (approximate)."""
    total_chars = sum(len(t.display) for t in tokens) or 1
    total_dur = sum(b - a for a, b in seg_bounds) or 1.0

    def at(fraction: float) -> float:
        target = fraction * total_dur
        for a, b in seg_bounds:
            if target <= b - a:
                return a + target
            target -= b - a
        return seg_bounds[-1][1]

    done = 0
    for t in tokens:
        t.start, t.end = at(done / total_chars), at((done + len(t.display)) / total_chars)
        done += len(t.display)
        t.source = "spread"


def assign_times(out: list[OutTok], dropped: set[int]) -> None:
    """Whisper times for kept words, derived from surviving Whisper words where Whisper cannot be trusted.

    A kept word keeps its reference position's time unless the voting dropped a neighbouring position and
    either Whisper did not vote for the word, or both neighbours were dropped (an isolated survivor inside
    a hallucinated run): that time belongs to a hallucinated word, so it is not inherited.
    Such words, and words inserted between reference words, get times between the nearest words that do
    have one, sized by character count: spread across the gap when they fill most of it, otherwise placed
    right before the next timed word.
    """
    for t in out:
        timed = (not t.gap) and t.start is not None
        if timed and t.slot is not None:
            before, after = (t.slot - 1) in dropped, (t.slot + 1) in dropped
            if (not t.anchored and (before or after)) or (before and after):  # next to a dropped word, or isolated by two
                timed = False
        if not timed:
            t.start = t.end = None
            t.timeless = True
    i = 0
    while i < len(out):
        if out[i].start is not None:
            i += 1
            continue
        j = i
        while j < len(out) and out[j].start is None:
            j += 1
        run = out[i:j]
        chars = [max(len(t.display), 1) for t in run]
        est = sum(chars) * SEC_PER_CHAR
        prev_end = out[i - 1].end if i > 0 else None
        next_start = out[j].start if j < len(out) else None
        if prev_end is None and next_start is None:
            a, b = 0.0, est
        elif next_start is None:
            a, b = prev_end, prev_end + est
        elif prev_end is None:
            a, b = max(0.0, next_start - est), next_start
        else:
            gap = next_start - prev_end
            if gap <= 0:
                a, b = prev_end, prev_end + est
            elif est >= 0.6 * gap:
                a, b = prev_end, next_start
            else:
                a, b = next_start - est, next_start
        pos, total = a, sum(chars)
        for t, c in zip(run, chars):
            width = (b - a) * c / total
            t.start, t.end, t.source = pos, pos + width, "interp"
            pos += width
        i = j


# ---------------------------------------------------------------- cutting into cues + validation

def fits_two_lines(text: str) -> bool:
    """True when the text can be wrapped into at most 2 lines of the caption line width."""
    width = config.CAPTION_MAX_LINE_CHARS
    words = text.split()
    if len(text) <= width:
        return True
    return any(len(" ".join(words[:k])) <= width and len(" ".join(words[k:])) <= width for k in range(1, len(words)))


def validate_timing(cues, what: str = "cue") -> list[str]:
    """Warnings for cues that are out of order, overlap, are under 0.8 s or longer than 3.5 s."""
    warnings = []
    for i, c in enumerate(cues):
        dur = c.end_s - c.start_s
        if dur < WARN_MIN_CUE_S:
            warnings.append(f"{what} {i} ({c.start_s:.2f}-{c.end_s:.2f}s) is under {WARN_MIN_CUE_S}s long")
        if dur > WARN_MAX_CUE_S:
            warnings.append(f"{what} {i} ({c.start_s:.2f}-{c.end_s:.2f}s) is longer than {WARN_MAX_CUE_S}s")
        if i:
            p = cues[i - 1]
            if c.start_s < p.start_s:
                warnings.append(f"{what} {i} starts before {what} {i - 1} (out of order)")
            elif c.start_s < p.end_s - 0.01:
                warnings.append(f"{what} {i} ({c.start_s:.2f}s) overlaps {what} {i - 1} (ends {p.end_s:.2f}s)")
    for w in warnings:
        log.warning(w)
    return warnings


def _dur(chunk: list[OutTok]) -> float:
    return chunk[-1].end - chunk[0].start


def _text(chunk: list[OutTok]) -> str:
    return " ".join(t.display for t in chunk)


def _same_block(a: list[OutTok], b: list[OutTok]) -> bool:
    return a[-1].mode == b[0].mode and a[-1].block == b[0].block


def cut_segments(tokens: list[OutTok], respect_segments: bool, seg_bounds: list[tuple[float, float]] | None = None,
                 seg_info: dict[int, SegmentMode] | None = None) -> list[ConsensusSegment]:
    """Cut timed words into caption-sized cues.

    Greedy cuts at Whisper segment boundaries, <= 3 s, <= 60 characters, pauses > 1 s and mode changes; then a
    cue under 1 s is joined to a neighbour when the result is within 3 s, bridges no pause over 1 s and
    still fits 2 lines of 32 characters; cues mostly made of guessed word times fall back to their Whisper
    segment's start and end.
    """
    chunks: list[list[OutTok]] = []
    cur: list[OutTok] = []
    chars = 0
    for t in tokens:
        if cur:
            last = cur[-1]
            too_long = t.end - cur[0].start > config.CAPTION_MAX_S
            too_wide = chars + 1 + len(t.display) > MAX_CUE_CHARS
            silence = t.start - last.end > SILENCE_BREAK_S
            new_seg = respect_segments and t.seg != last.seg and t.source != "spread" and last.source != "spread" \
                and t.mode == "latin" and last.mode == "latin"
            new_mode = (t.mode, t.block) != (last.mode, last.block)
            if too_long or too_wide or silence or new_seg or new_mode:
                chunks.append(cur)
                cur, chars = [], 0
        cur.append(t)
        chars += len(t.display) + (1 if chars else 0)
    if cur:
        chunks.append(cur)

    def can_join(a: list[OutTok], b: list[OutTok]) -> bool:
        return (_same_block(a, b) and b[0].start - a[-1].end <= SILENCE_BREAK_S
                and b[-1].end - a[0].start <= config.CAPTION_MAX_S and fits_two_lines(_text(a) + " " + _text(b)))

    changed = True
    while changed:  # fold cues shorter than the minimum into the neighbour with the smaller gap
        changed = False
        for k, c in enumerate(chunks):
            if _dur(c) >= config.CAPTION_MIN_S:
                continue
            options = []
            if k > 0 and can_join(chunks[k - 1], c):
                options.append((c[0].start - chunks[k - 1][-1].end, k - 1))
            if k + 1 < len(chunks) and can_join(c, chunks[k + 1]):
                options.append((chunks[k + 1][0].start - c[-1].end, k))
            if options:
                _, left = min(options)
                chunks[left:left + 2] = [chunks[left] + chunks[left + 1]]
                changed = True
                break

    segments: list[ConsensusSegment] = []
    for k, c in enumerate(chunks):
        start, end = c[0].start, c[-1].end
        if seg_bounds and sum(t.timeless for t in c) / len(c) >= UNRELIABLE_RATIO:
            ids = {t.seg for t in c if 0 <= t.seg < len(seg_bounds)}
            if ids:
                lo, hi = min(seg_bounds[i][0] for i in ids), max(seg_bounds[i][1] for i in ids)
                if hi - lo <= config.CAPTION_MAX_S + 0.5:  # unreliable word times: use the Whisper segment bounds
                    start, end = lo, hi
        if end - start < config.CAPTION_MIN_S:  # cannot be joined: stretch without running into the next cue
            limit = chunks[k + 1][0].start if k + 1 < len(chunks) else start + config.CAPTION_MIN_S
            end = min(start + config.CAPTION_MIN_S, max(limit, end))
        if segments and start < segments[-1].end_s:  # a cue never starts before the previous one ends
            start = segments[-1].end_s
            end = max(end, start + 0.2)
        first = c[0]
        info = (seg_info or {}).get(first.seg)
        segments.append(ConsensusSegment(
            start_s=round(start, 2), end_s=round(end, 2), text=_text(c),
            confidence=round(sum(t.conf for t in c) / len(c), 3),
            timing="approximate" if any(t.source == "spread" for t in c) else "whisper",
            mode=first.mode, mode_reason=info.reason if info else "",
            whisper_role="timing-only" if first.mode == "indic" else "voter"))
    return segments


# ---------------------------------------------------------------- mode decision (latin vs indic, per Whisper segment)

@dataclass
class RunLoc:
    voter: str
    script: str | None
    start: int  # voter token range [start, end)
    end: int
    lo: int  # span on the Whisper word axis [lo, hi)
    hi: int
    t_lo: float | None = None  # time span, only for the ocr voter
    t_hi: float | None = None


def locate_runs(v: Voter, ref_norms: list[str]) -> list[RunLoc]:
    """Place each script run of a voter on the Whisper word axis.

    A run that shares words with Whisper is placed over them (widened by its unmatched words); an unmatched run
    sits between the Whisper words matched by the voter's own neighbouring text; a voter with no shared words
    at all is spread over the axis by character count.
    """
    n = len(ref_norms)
    v_norms = [t[0] for t in v.tokens]
    matched: dict[int, int] = {}
    for blk in difflib.SequenceMatcher(None, ref_norms, v_norms, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            matched[blk.b + k] = blk.a + k
    chars = [len(t[1]) for t in v.tokens]
    total = sum(chars) or 1
    out = []
    for script, s, e in script_runs(v.tokens):
        m = [j for j in range(s, e) if j in matched]
        if len(m) >= 2 or (m and len(m) >= 0.3 * (e - s)):
            lo = max(0, matched[m[0]] - (m[0] - s))
            hi = min(n, matched[m[-1]] + 1 + (e - 1 - m[-1]))
        else:
            prev = [matched[j] for j in range(0, s) if j in matched]
            nxt = [matched[j] for j in range(e, len(v_norms)) if j in matched]
            if prev or nxt:
                lo, hi = (max(prev) + 1 if prev else 0), (min(nxt) if nxt else n)
            else:
                lo, hi = round(n * sum(chars[:s]) / total), round(n * sum(chars[:e]) / total)
        loc = RunLoc(v.name, script, s, e, lo, max(hi, lo))
        if v.times:
            spans = [v.times[j] for j in range(s, e) if v.times[j]]
            if spans:
                loc.t_lo, loc.t_hi = min(x[0] for x in spans), max(x[1] for x in spans)
        out.append(loc)
    return out


@dataclass
class SegInfo:
    idx: int
    start: float
    end: float
    tok_lo: int  # Whisper word range [tok_lo, tok_hi)
    tok_hi: int
    mode: str = "latin"
    reason: str = ""
    supporters: list[str] = field(default_factory=list)


def _overlap(a_lo: float, a_hi: float, b_lo: float, b_hi: float) -> float:
    return max(0.0, min(a_hi, b_hi) - max(a_lo, b_lo))


def decide_modes(ref: list[RefToken], seg_bounds: list[tuple[float, float]], voters: list[Voter]
                 ) -> tuple[list[SegInfo], dict[int, dict[str, list[RunLoc]]], list[str]]:
    """Mode per Whisper segment, plus the Indic runs assigned to each block of consecutive indic segments."""
    ref_norms = [t.norm for t in ref]
    infos = []
    for i, (a, b) in enumerate(seg_bounds):
        idx = [k for k, t in enumerate(ref) if t.seg == i]
        infos.append(SegInfo(i, a, b, min(idx) if idx else 0, max(idx) + 1 if idx else 0))
    indic_voters = [v for v in voters if indic_ratio(" ".join(t[0] for t in v.tokens)) >= INDIC_RATIO]
    runs: dict[str, list[RunLoc]] = {
        v.name: [r for r in locate_runs(v, ref_norms) if r.script in INDIC_SCRIPTS] for v in indic_voters}

    for info in infos:
        for name, rl in runs.items():
            for r in rl:
                if name == "ocr":
                    ok = r.t_lo is not None and _overlap(r.t_lo, r.t_hi, info.start, info.end) >= min(1.0, 0.5 * (info.end - info.start))
                elif info.tok_hi > info.tok_lo:
                    ok = _overlap(r.lo, r.hi, info.tok_lo, info.tok_hi) / (info.tok_hi - info.tok_lo) >= 0.5
                else:
                    ok = not ref  # no Whisper words at all: every Indic voter covers the one pseudo segment
                if ok:
                    info.supporters.append(name)
                    break
        info.mode = "indic" if len(info.supporters) >= 2 else "latin"

    # group consecutive indic segments into blocks and give each block the Indic runs that fall mostly in it
    blocks: list[list[SegInfo]] = []
    for info in infos:
        if info.mode == "indic":
            if blocks and blocks[-1][-1].idx == info.idx - 1:
                blocks[-1].append(info)
            else:
                blocks.append([info])
    assigned: dict[int, dict[str, list[RunLoc]]] = {k: {} for k in range(len(blocks))}
    for name, rl in runs.items():
        for r in rl:
            best, best_ov = None, 0.0
            for k, blk in enumerate(blocks):
                if name == "ocr":
                    ov = _overlap(r.t_lo or 0, r.t_hi or 0, blk[0].start, blk[-1].end) if r.t_lo is not None else 0.0
                elif ref:
                    ov = _overlap(r.lo, r.hi, blk[0].tok_lo, blk[-1].tok_hi)
                else:
                    ov = 1.0
                if ov > best_ov:
                    best, best_ov = k, ov
            if best is not None:
                assigned[best].setdefault(name, []).append(r)
    notes = []
    for k, blk in enumerate(blocks):
        if len(assigned[k]) < 2:  # the runs did not actually line up on this block: back to latin mode
            for info in blk:
                info.mode, info.reason = "latin", "Indic text of the supporting voters did not line up on this block"
            notes.append(f"segments {blk[0].idx}-{blk[-1].idx}: indic candidates reverted to latin (fewer than 2 aligned voters)")
            assigned[k] = {}
    return infos, assigned, notes


# ---------------------------------------------------------------- the two passes

def _filter_script(v: Voter, script: str | None, notes: list[str], ref_label: str) -> Voter | None:
    """Drop the voter's words written in another script than the reference; None if nothing is left."""
    keep, dropped = [], Counter()
    for k, tok in enumerate(v.tokens):
        sc = script_of(tok[0])
        if script and sc and sc != script:
            dropped[sc] += 1
        else:
            keep.append(k)
    if dropped:
        notes.append(f"{v.name}: dropped {sum(dropped.values())} word(s) written in "
                     f"{'/'.join(s.lower() for s in dropped)} ({ref_label} is {script.lower()})")
    if not keep:
        return None
    return Voter(v.name, [v.tokens[k] for k in keep], [v.times[k] for k in keep] if v.times else None)


def _latin_pass(ref: list[RefToken], seg_bounds, voters: list[Voter], primary: str | None, duration_s: float,
                notes: list[str], mixed: bool):
    """Latin mode: Whisper's words are the reference. Returns (tokens, kept voters, ref voter name, approximate)."""
    ref_name = "whisper"
    if not ref:  # whisper produced nothing: use the best other voter as the reference, without timestamps
        pick = next((v for v in voters if v.name == primary), None) or min(voters, key=lambda v: _rank_fn(primary)(v.name))
        voters = [v for v in voters if v is not pick]
        ref_name = pick.name
        ref = [RefToken(n, d, None, None, 0, "spread") for n, d in pick.tokens]
        seg_bounds = [(0.0, max(duration_s, 0.001))]
        notes.append(f"whisper had no words; {ref_name} used as the alignment reference without timestamps")
    # A single Indic primary listener against a Whisper in another script: Whisper misheard the language, so the
    # primary's words become the reference and Whisper only supplies segment times (two or more Indic voters
    # are handled by indic mode instead).
    prim = next((v for v in voters if v.name == primary), None)
    if prim is not None and ref_name == "whisper" and not mixed:
        ref_script, prim_script = dominant_script([(t.norm, t.display) for t in ref]), dominant_script(prim.tokens)
        if ref_script and prim_script and ref_script != prim_script:
            notes.append(f"whisper wrote {ref_script.lower()} but {primary} wrote {prim_script.lower()}: "
                         f"{primary} used as the reference, whisper only supplies segment times")
            voters = [v for v in voters if v is not prim] + [Voter("whisper", [(t.norm, t.display) for t in ref])]
            ref_name = primary
            ref = [RefToken(n, d, None, None, 0, "spread") for n, d in prim.tokens]
    ref_script = dominant_script([(t.norm, t.display) for t in ref])
    kept = [f for f in (_filter_script(v, ref_script, notes, "the reference") for v in voters) if f]
    out, dropped = vote(ref, ref_name, kept, primary)
    approximate = ((primary == "voice_transcribe" and not mixed) or any(t.start is None for t in out if not t.gap)
                   or all(t.source == "spread" for t in ref))
    if out:
        if approximate:
            _spread(out, seg_bounds)
        else:
            assign_times(out, dropped)
    return out, kept, ref_name, approximate


def _indic_pass(block: list[SegInfo], assigned: dict[str, list[RunLoc]], by_name: dict[str, Voter],
                notes: list[str], block_id: int):
    """Indic mode for one block of segments. Returns (tokens, voter names) or None if it cannot be run."""
    start_t, end_t = block[0].start, block[-1].end
    toks: dict[str, Voter] = {}
    for name, runs in assigned.items():
        v = by_name[name]
        idx = []
        for r in runs:
            idx += [j for j in range(r.start, r.end) if name != "ocr" or _overlaps(v.times[j], start_t, end_t)]
        if idx:
            toks[name] = Voter(name, [v.tokens[j] for j in idx], [v.times[j] for j in idx] if v.times else None)
    script_counts = Counter(s for v in toks.values() for s in (script_of(t[0]) for t in v.tokens) if s)
    if not script_counts:
        return None
    winning = script_counts.most_common(1)[0][0]
    voters = [f for f in (_filter_script(v, winning, notes, "the winning script") for v in toks.values()) if f]
    if len(voters) < 2:
        return None
    names = [v.name for v in voters]
    norms = {v.name: [t[0] for t in v.tokens] for v in voters}

    def sim(a: str, b: str) -> float:
        return difflib.SequenceMatcher(None, norms[a], norms[b], autojunk=False).ratio()

    avg = {a: sum(sim(a, b) for b in names if b != a) / (len(names) - 1) for a in names}
    primary = next((n for n in INDIC_PRIORITY if n in names), names[0])  # primary listener for Indic text
    rank = _rank_fn(primary)
    skeleton = max(voters, key=lambda v: (round(avg[v.name], 6), -rank(v.name)))
    ref = [RefToken(n, d, None, None, block[0].idx, "spread") for n, d in skeleton.tokens]
    out, _ = vote(ref, skeleton.name, [v for v in voters if v is not skeleton], primary)
    for t in out:
        t.mode, t.block, t.seg = "indic", block_id, block[0].idx
    _spread(out, [(i.start, i.end) for i in block])  # Whisper only supplies the times
    notes.append(f"indic block over segments {block[0].idx}-{block[-1].idx}: skeleton={skeleton.name} "
                 f"(avg similarity {avg[skeleton.name]:.2f}), voters={names}, script={winning.lower()}")
    return out, names


# ---------------------------------------------------------------- entry points

def build_consensus_from_voters(voters: list[Voter], ref_words: list[dict], ref_segments: list[dict],
                                whisper_text: str, primary: str | None, duration_s: float) -> ConsensusStage:
    """Core algorithm. `voters` must not include whisper (it is the reference)."""
    stage = ConsensusStage(primary=primary)
    notes: list[str] = []
    ref, seg_bounds = build_ref(ref_words, ref_segments, whisper_text, duration_s)
    voters = [v for v in voters if v.tokens]
    if not ref and not voters:
        stage.status, stage.error = "error", "no transcript from any voter"
        return stage
    by_name = {v.name: v for v in voters}
    infos, assigned, mode_notes = decide_modes(ref, seg_bounds, voters)
    notes += mode_notes
    whisper_script = dominant_script([(t.norm, t.display) for t in ref]) if ref else None
    blocks: list[list[SegInfo]] = []
    for info in infos:
        if info.mode == "indic":
            if blocks and blocks[-1][-1].idx == info.idx - 1:
                blocks[-1].append(info)
            else:
                blocks.append([info])

    out: list[OutTok] = []
    kept_names: list[str] = []
    ref_name = "whisper"
    if blocks:
        # --- mixed or fully indic: run each indic block, then latin mode on the remaining Whisper words
        for k, blk in enumerate(blocks):
            result = _indic_pass(blk, assigned.get(k, {}), by_name, notes, k + 1)
            if result is None:
                for info in blk:
                    info.mode, info.reason = "latin", "indic block could not be voted on (fewer than 2 voters in the winning script)"
            else:
                tokens, names = result
                out += tokens
                kept_names += [n for n in names if n not in kept_names]
        blocks = [b for b in blocks if b[0].mode == "indic"]
        latin_ref = [t for t in ref if infos[t.seg].mode == "latin"] if ref else []
        if latin_ref:
            latin_out, latin_kept, ref_name, _ = _latin_pass(
                latin_ref, seg_bounds, voters, primary, duration_s, notes, mixed=True)
            out += latin_out
            kept_names += [v.name for v in latin_kept if v.name not in kept_names]
        out.sort(key=lambda t: t.seg)  # stable: block words sit at their first segment, latin words at their own
    else:
        out, latin_kept, ref_name, _ = _latin_pass(ref, seg_bounds, voters, primary, duration_s, notes, mixed=False)
        kept_names = [v.name for v in latin_kept]
    if not out:
        stage.status, stage.error = "error", "voting produced no words"
        return stage

    # --- reasons for every Whisper segment
    n_indic_voters = len([v for v in voters if indic_ratio(" ".join(t[0] for t in v.tokens)) >= INDIC_RATIO])
    for info in infos:
        if info.mode == "indic":
            sup = ", ".join(info.supporters)
            info.reason = (f"{len(info.supporters)} voters wrote an Indic script over this segment ({sup})"
                           + (f" while whisper wrote {whisper_script.lower()}" if whisper_script else "")
                           + ": whisper is timing-only here")
        elif not info.reason:
            if n_indic_voters < 2:
                info.reason = f"fewer than 2 non-whisper voters wrote mostly Indic script ({n_indic_voters})"
            elif len(info.supporters) == 1:
                info.reason = f"only {info.supporters[0]} wrote Indic script over this segment (2 needed)"
            else:
                info.reason = "no Indic-script voter text overlaps this segment"
    seg_info = {i.idx: SegmentMode(index=i.idx, start_s=round(i.start, 2), end_s=round(i.end, 2), mode=i.mode,
                                   reason=i.reason, supporters=i.supporters,
                                   whisper_role="timing-only" if i.mode == "indic" else "voter") for i in infos}
    stage.segment_modes = list(seg_info.values())
    for info in infos:
        if info.mode == "indic" and whisper_script:
            notes.append(f"whisper segment {info.idx}: timing-only (it wrote {whisper_script.lower()}; "
                         f"voters wrote an Indic script)")

    has_spread = any(t.source == "spread" for t in out)
    has_whisper = any(t.source != "spread" for t in out)
    stage.segments = cut_segments(out, respect_segments=True, seg_bounds=seg_bounds, seg_info=seg_info)
    stage.consensus_text = " ".join(t.display for t in out)
    stage.timing_source = "mixed" if has_spread and has_whisper else ("approximate" if has_spread else "whisper")
    stage.ref_voter = ref_name
    uses_ref_voter = any(t.mode == "latin" for t in out)  # in an all-indic reel whisper never votes
    stage.voters_used = list(dict.fromkeys(([ref_name] if uses_ref_voter else []) + kept_names))
    stage.warnings = validate_timing(stage.segments, "consensus cue")
    for idx, t in enumerate(out):
        # also flag a word only one voter produced whenever others were available: with a pool of 2 its
        # confidence is 0.5, which would otherwise pass although the voters never agreed
        if t.conf < LOW_CONFIDENCE or (t.agree == 1 and t.pool >= 2):
            stage.low_confidence_words.append(LowConfidenceWord(
                word=t.display, index=idx, start_s=None if t.start is None else round(t.start, 2),
                confidence=round(t.conf, 3), votes=t.votes))
    stage.notes = notes
    stage.status = "ok"
    return stage


def build_consensus(speech: SpeechStage, audio: AudioStage, watch: WatchStage, duration_s: float) -> ConsensusStage:
    """Collect the voters that produced text from the stage results and run the consensus."""
    voters: list[Voter] = []
    if speech.voice_transcribe.status == "ok" and speech.voice_transcribe.transcript:
        voters.append(Voter("voice_transcribe", tokenize(speech.voice_transcribe.transcript)))
    if audio.spark.status == "ok" and audio.spark.quoted_words:
        voters.append(Voter("spark", tokenize(audio.spark.quoted_words)))
    if audio.gemini.status == "ok" and audio.gemini.transcript:
        voters.append(Voter("gemini", tokenize(audio.gemini.transcript)))
    if watch.status == "ok" and watch.on_screen_text:
        toks, times = [], []
        for item in sorted(watch.on_screen_text, key=lambda x: x.start_s):
            for tok in tokenize(item.text):
                toks.append(tok)
                times.append((item.start_s, item.end_s))
        voters.append(Voter("ocr", toks, times))
    w = speech.whisper
    whisper_ok = w.status == "ok"
    return build_consensus_from_voters(
        voters, [x.model_dump() for x in w.words] if whisper_ok else [],
        [x.model_dump() for x in w.segments] if whisper_ok else [], w.text if whisper_ok else "",
        speech.primary, duration_s)
