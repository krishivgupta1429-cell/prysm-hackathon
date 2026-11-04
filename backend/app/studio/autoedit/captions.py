"""Built during the Meta Global AI Developer Hackathon (new work).

Captions and text overlays as ASS subtitles (libass shapes Devanagari correctly): word-by-word highlight, at most two lines,
inside Instagram's safe area. `chunk_words` and `build_ass` work on the already retimed words; `to_srt` writes the plain captions.
"""
import re

from app.studio.autoedit import config

OUT_W, OUT_H = config.OUT_W, config.OUT_H
FONT_FILES = {"regular": "Poppins-Regular.ttf", "bold": "Poppins-Bold.ttf", "extrabold": "Poppins-ExtraBold.ttf"}
PAUSE_BREAK_S = 0.45
MIN_CHUNK_S = 0.5
END_HOLD_S = 0.18
POSITION_MARGINS = {  # (alignment, MarginV) for 1080x1920
    "lower": (2, OUT_H - config.SAFE["y1"]),
    "middle": (5, 0),
    "upper": (8, config.SAFE["y0"] + 20),
}
OVERLAY_POSITIONS = {"top": (8, config.SAFE["y0"] + 20), "center": (5, 0), "lower": (2, 700)}


def ass_color(rgb: str, alpha: int = 0) -> str:
    rgb = rgb.lstrip("#").upper()
    return f"&H{alpha:02X}{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}"


def tag_color(rgb: str) -> str:
    """Colour for an inline override tag: &HBBGGRR& (no alpha digits)."""
    rgb = rgb.lstrip("#").upper()
    return f"&H{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}&"


def max_line_chars(size: int) -> int:
    usable = config.SAFE["x1"] - config.SAFE["x0"] - 40
    return max(10, int(usable / (size * 0.60)))


def _ends_sentence(word: str) -> bool:
    return bool(re.search(r"[.!?।]$", word.strip()))


MAX_CHUNK_WORDS, MIN_CHUNK_WORDS = 5, 3


def chunk_words(words: list[dict], size: int) -> list[list[dict]]:
    """Caption chunks of 3-5 words (fewer only at a pause, a sentence end or a change of cut), at most two lines. A chunk is closed when it has
    5 words, when the next word follows a pause or a sentence end (and the chunk has at least 2 words), when the segment changes, or when
    two lines are full."""
    limit = max_line_chars(size) * 2
    chunks, cur, chars = [], [], 0
    for i, w in enumerate(words):
        gap = w["start"] - words[i - 1]["end"] if i else 0.0
        if cur:
            brk = (len(cur) >= MAX_CHUNK_WORDS or w.get("seg") != cur[-1].get("seg") or chars + len(w["word"]) + 1 > limit
                   or ((gap > PAUSE_BREAK_S or _ends_sentence(cur[-1]["word"])) and len(cur) >= 2)
                   or (_ends_sentence(cur[-1]["word"]) and len(cur) >= MIN_CHUNK_WORDS - 1))
            if brk:
                chunks.append(cur)
                cur, chars = [], 0
        cur.append(w)
        chars += len(w["word"]) + 1
    if cur:
        chunks.append(cur)
    # a lone trailing word is glued to the previous chunk when that still fits in 5 words / two lines
    if len(chunks) >= 2 and len(chunks[-1]) == 1 and len(chunks[-2]) < MAX_CHUNK_WORDS and chunks[-1][0]["start"] - chunks[-2][-1]["end"] <= PAUSE_BREAK_S \
            and chunks[-1][0].get("seg") == chunks[-2][-1].get("seg") and sum(len(x["word"]) + 1 for x in chunks[-2] + chunks[-1]) <= limit:
        chunks[-2] += chunks.pop()
    return chunks


def split_lines(words: list[str], size: int) -> list[list[int]]:
    """Indices of the words on each of at most two balanced lines."""
    limit = max_line_chars(size)
    total = sum(len(w) + 1 for w in words)
    if total <= limit or len(words) < 2:
        return [list(range(len(words)))]
    best, best_diff = 1, None
    run = 0
    for i in range(1, len(words)):
        run += len(words[i - 1]) + 1
        diff = abs(run - (total - run))
        if best_diff is None or diff < best_diff:
            best, best_diff = i, diff
    return [list(range(best)), list(range(best, len(words)))]


def _fontspec(style: dict, role: str) -> dict:
    """The resolved font of a role ('caption' | 'headline'): {name, bold, dv_name?, dv_bold?}; Poppins when the run resolved none."""
    spec = (style.get("fonts") or {}).get(role)
    if spec:
        return spec
    w = style.get("caption_font_weight", "bold") if role == "caption" else "extrabold"
    return {"name": config.FONT_NAME, "bold": w != "regular"}


def _dv(word: str) -> bool:
    return any(0x0900 <= ord(c) <= 0x097F for c in word)


def _word_font(text: str, spec: dict) -> str:
    """The word, drawn with the family's Devanagari file when it is a Devanagari word (the Google subsets are split by script)."""
    if spec.get("dv_name") and _dv(text):
        return "{\\fn%s\\b%d}%s{\\fn%s\\b%d}" % (spec["dv_name"], 1 if spec.get("dv_bold") else 0, text, spec["name"], 1 if spec["bold"] else 0)
    return text


ANIMATIONS = {
    "pop": "{\\fscx70\\fscy70\\t(0,110,\\fscx110\\fscy110)\\t(110,200,\\fscx100\\fscy100)}",
    "bounce": "{\\fscx60\\fscy60\\t(0,120,\\fscx118\\fscy118)\\t(120,200,\\fscx94\\fscy94)\\t(200,280,\\fscx100\\fscy100)}",
    "fade": "{\\fad(140,0)}",
    "slide": "{\\fscy40\\fad(100,0)\\t(0,160,\\fscy100)}",
    "none": "",
}


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _ts(t: float) -> str:
    t = max(t, 0.0)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def caption_events(words: list[dict], style: dict) -> list[dict]:
    """[{start, end, text(ASS), plain, chunk}] one event per highlighted-word state."""
    size = int(style["caption_size"])
    base, hi = tag_color(style["caption_color"]), tag_color(style["highlight_color"])
    accent = tag_color(style.get("accent") or style["highlight_color"])
    spec = _fontspec(style, "caption")
    emph = {round(float(t), 2) for t in style.get("emphasis") or []}
    anim = ANIMATIONS.get(style.get("animation") or "none", "")
    big = int(size * 1.22)
    chunks = chunk_words(words, size)
    events = []
    for ci, chunk in enumerate(chunks):
        nxt_start = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else None
        texts = [_esc(w["word"]) for w in chunk]
        lines = split_lines(texts, size)
        for k, w in enumerate(chunk):
            start = w["start"] if k else max(w["start"] - 0.05, 0.0)
            if k + 1 < len(chunk):
                end = chunk[k + 1]["start"]
            else:
                end = w["end"] + END_HOLD_S
                if nxt_start is not None:
                    end = min(end, nxt_start)
            end = max(end, start + 0.08)
            parts = []
            for line in lines:
                toks = []
                for idx in line:
                    word = _word_font(texts[idx], spec)
                    emphasised = round(float(chunk[idx].get("src_start", chunk[idx]["start"])), 2) in emph
                    spoken = idx == k and hi != base
                    if emphasised:   # a word the director singled out: bigger, in the accent colour, for the whole chunk
                        tok = "{\\fs%d\\c%s}%s{\\fs%d\\c%s}" % (big, hi if spoken else accent, word, size, base)
                    elif spoken:
                        tok = "{\\c%s}%s{\\c%s}" % (hi, word, base)
                    else:
                        tok = word
                    toks.append(tok)
                parts.append(" ".join(toks))
            text = "\\N".join(parts)
            events.append({"start": start, "end": end, "text": (anim + text) if k == 0 else text, "plain": " ".join(texts), "chunk": ci})
    return _no_overlap_between_chunks(events)


def _no_overlap_between_chunks(events: list[dict]) -> list[dict]:
    """Two chunks must never be on screen together: libass would stack them and push the new one up for its whole duration (seen as a caption
    that sits higher than the others). A chunk's last event is cut where the next chunk starts; recogniser word times often overlap by 50-100 ms."""
    out = []
    for i, e in enumerate(events):
        nxt = next((x for x in events[i + 1:] if x["chunk"] > e["chunk"]), None)
        if nxt is not None and nxt["start"] < e["end"]:
            e = {**e, "end": nxt["start"]}
        if e["end"] - e["start"] >= 0.03:
            out.append(e)
    return out


def build_ass(words: list[dict], overlays: list[dict], style: dict, out_duration: float, with_captions: bool = True) -> str:
    """The whole subtitle file. `overlays` = [{text, start, end, position, role}] already on the output timeline."""
    cap_font, head_font = _fontspec(style, "caption"), _fontspec(style, "headline")
    bold = -1 if cap_font["bold"] else 0
    hbold = -1 if head_font["bold"] else 0
    align, mv = POSITION_MARGINS[style["caption_position"]]
    size, hook = int(style["caption_size"]), int(style["hook_size"])
    white = ass_color(style["caption_color"])
    outline = ass_color("000000")
    shadow = ass_color("000000", 0x70)
    lr = config.SAFE["x0"]
    rmargin = OUT_W - config.SAFE["x1"]
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {OUT_W}", f"PlayResY: {OUT_H}", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{cap_font['name']},{size},{white},{white},{outline},{shadow},{bold},0,0,0,100,100,0,0,1,{max(4, size // 14)},2,{align},{lr},{rmargin},{mv},1",
    ]
    box = bool(style.get("overlay_box"))
    for role, sz in (("Hook", hook), ("Label", int(hook * 0.72)), ("Cta", int(hook * 0.72))):
        for pos, (a, m) in OVERLAY_POSITIONS.items():
            if box:
                head.append(f"Style: {role}{pos.title()},{head_font['name']},{sz},{white},{white},{ass_color('000000', 0x40)},{ass_color('000000', 0x40)},{hbold},0,0,0,100,100,0,0,3,14,0,{a},{lr},{rmargin},{m},1")
            else:
                head.append(f"Style: {role}{pos.title()},{head_font['name']},{sz},{white},{white},{outline},{shadow},{hbold},0,0,0,100,100,0,0,1,{max(5, sz // 12)},3,{a},{lr},{rmargin},{m},1")
    head += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    lines = []
    if with_captions:
        for ev in caption_events(words, style):
            if ev["start"] < out_duration:
                lines.append(f"Dialogue: 1,{_ts(ev['start'])},{_ts(min(ev['end'], out_duration))},Caption,,0,0,0,,{ev['text']}")
    for o in overlays:
        role = o.get("role", "label").title()
        pos = o.get("position", "top").title()
        fade = "{\\fad(250,250)}"
        lines.append(f"Dialogue: 2,{_ts(o['start'])},{_ts(min(o['end'], out_duration))},{role}{pos},,0,0,0,,{fade}{' '.join(_word_font(w, head_font) for w in _esc(o['text']).split(' '))}")
    return "\n".join(head + lines) + "\n"


def srt_ts(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_srt(words: list[dict], style: dict) -> str:
    """Plain captions (one block per chunk, two lines) for creators who want to upload them separately."""
    size = int(style["caption_size"])
    blocks = []
    for i, chunk in enumerate(chunk_words(words, size), 1):
        texts = [w["word"] for w in chunk]
        lines = [" ".join(texts[j] for j in line) for line in split_lines(texts, size)]
        blocks.append(f"{i}\n{srt_ts(chunk[0]['start'])} --> {srt_ts(chunk[-1]['end'] + END_HOLD_S)}\n" + "\n".join(lines) + "\n")
    return "\n".join(blocks)
