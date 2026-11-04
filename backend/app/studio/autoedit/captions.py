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


def chunk_words(words: list[dict], size: int) -> list[list[dict]]:
    """Group words into caption chunks of at most two lines: a new chunk starts after a pause, a sentence end or when two lines are full."""
    limit = max_line_chars(size) * 2
    chunks, cur, chars = [], [], 0
    for i, w in enumerate(words):
        gap = w["start"] - words[i - 1]["end"] if i else 0.0
        if cur and (gap > PAUSE_BREAK_S or w.get("seg") != cur[-1].get("seg") or _ends_sentence(cur[-1]["word"]) or chars + len(w["word"]) + 1 > limit):
            chunks.append(cur)
            cur, chars = [], 0
        cur.append(w)
        chars += len(w["word"]) + 1
    if cur:
        chunks.append(cur)
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
                    toks.append("{\\c%s}%s{\\c%s}" % (hi, texts[idx], base) if idx == k and hi != base
                                else texts[idx])
                parts.append(" ".join(toks))
            events.append({"start": start, "end": end, "text": "\\N".join(parts), "plain": " ".join(texts), "chunk": ci})
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
    weight = FONT_FILES.get(style.get("caption_font_weight", "bold"), FONT_FILES["bold"])
    bold = -1 if "Bold" in weight else 0
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
        f"Style: Caption,{config.FONT_NAME},{size},{white},{white},{outline},{shadow},{bold},0,0,0,100,100,0,0,1,{max(4, size // 14)},2,{align},{lr},{rmargin},{mv},1",
    ]
    box = bool(style.get("overlay_box"))
    for role, sz in (("Hook", hook), ("Label", int(hook * 0.72)), ("Cta", int(hook * 0.72))):
        for pos, (a, m) in OVERLAY_POSITIONS.items():
            if box:
                head.append(f"Style: {role}{pos.title()},{config.FONT_NAME},{sz},{white},{white},{ass_color('000000', 0x40)},{ass_color('000000', 0x40)},-1,0,0,0,100,100,0,0,3,14,0,{a},{lr},{rmargin},{m},1")
            else:
                head.append(f"Style: {role}{pos.title()},{config.FONT_NAME},{sz},{white},{white},{outline},{shadow},-1,0,0,0,100,100,0,0,1,{max(5, sz // 12)},3,{a},{lr},{rmargin},{m},1")
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
        lines.append(f"Dialogue: 2,{_ts(o['start'])},{_ts(min(o['end'], out_duration))},{role}{pos},,0,0,0,,{fade}{_esc(o['text'])}")
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
