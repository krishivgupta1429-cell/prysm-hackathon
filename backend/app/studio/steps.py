"""Built during the Meta Global AI Developer Hackathon (new work).

Quote-aware splitting and normalising of a parameter's "howToDoIt" text into numbered steps.

Why: the baseline validator split steps at every ". " followed by a capital letter, inside quoted phrases too, so a ready-to-say
line like 'the lyrics said salwar suit.. wait for it' broke in two, a step with two sentences became two "steps", and
validator rewrites left unnumbered extra lines in the list. Here a step only starts at a "Step N" marker (outside quotes); without
markers the text is split into sentences, but never inside quotes, at an ellipsis (".." or "..."), after an abbreviation or before a
lower-case word. Fallback lines ("Fallback: ...") become their own step and never come first.
"""
import re

OPEN_CLOSE = {"“": "”", "‘": "’"}
_MARK = re.compile(r"Step\s*(\d+)\s*[—–:.\-)]\s*", re.I)
_FALLBACK = re.compile(r"\s*(?:fallback\b|alternatively\b|otherwise\b|if (?:that|this|it|you can['’]?t|you cannot|you['’]d rather|you prefer|voicing|the (?:line|wording|text))\b)", re.I)
_ABBREV = re.compile(r"(?:\be\.g|\bi\.e|\betc|\bvs|\bno|\bmr|\bmrs|\bdr)\.$", re.I)


def _scan(text: str) -> tuple:
    """(inside, open_quote): inside[i] is True when text[i] lies inside a quoted phrase ('...', "...", “...”, ‘...’); apostrophes inside words are not quotes."""
    inside = [False] * len(text)
    close_for = None
    for i, ch in enumerate(text):
        prev = text[i - 1] if i else " "
        nxt = text[i + 1] if i + 1 < len(text) else " "
        if close_for is None:
            if ch in OPEN_CLOSE and not (ch == "‘" and prev.isalnum() and nxt.isalnum()):
                close_for = OPEN_CLOSE[ch]
            elif ch == '"':
                close_for = '"'
            elif ch == "'" and not prev.isalnum() and not nxt.isspace():
                close_for = "'"
            if close_for:
                inside[i] = True
                continue
        else:
            inside[i] = True
            if ch == close_for:
                if ch == "'" and prev.isalnum() and nxt.isalnum():
                    continue                     # an apostrophe inside the quote (don't, it's)
                if ch == "’" and prev.isalnum() and nxt.isalnum():
                    continue
                close_for = None
    return inside, close_for


def quote_mask(text: str) -> list:
    return _scan(text)[0]


def has_unclosed_quote(text: str) -> bool:
    """True when a quoted phrase is opened and never closed (a quote cut off or split across two steps)."""
    return _scan(text)[1] is not None


def _markers(text: str, inside: list) -> list:
    return [m for m in _MARK.finditer(text) if not inside[m.start()] and (m.start() == 0 or text[m.start() - 1].isspace())]


def split_sentences(text: str) -> list:
    """Sentences, never split inside quotes, at an ellipsis, after an abbreviation, or before a lower-case word."""
    text = text.strip()
    if not text:
        return []
    inside = quote_mask(text)
    out, start = [], 0
    for m in re.finditer(r"[.!?]+[\"'’”)]*\s+", text):
        end = m.end()
        pos = m.start()
        if inside[pos]:
            continue
        if text[pos:m.start() + 1] == "." and pos > 0 and text[pos - 1] == "." or (m.group(0).startswith("..")):
            continue                                       # ellipsis
        if _ABBREV.search(text[start:pos + 1]) or re.search(r"\d\.$", text[start:pos + 1]) and end < len(text) and text[end].isdigit():
            continue
        if end >= len(text) or not (text[end].isupper() or text[end] in "'\"‘“(ऀ-ॿ" or text[end].isdigit()):
            continue                                       # the next word is lower case: still the same sentence
        out.append(text[start:end].strip())
        start = end
    out.append(text[start:].strip())
    return [s for s in out if s]


def is_fallback(step: str) -> bool:
    return bool(_FALLBACK.match(step))


def _clean(step: str) -> str:
    return re.sub(r"\s+", " ", step).strip(" \t\n-–—:")


def split_steps(text: str) -> list:
    """The steps of a howToDoIt text, as plain strings without their "Step N —" label."""
    if isinstance(text, list):
        text = " ".join(str(x) for x in text)
    text = (text or "").strip()
    if not text:
        return []
    inside = quote_mask(text)
    marks = _markers(text, inside)
    parts = []
    if marks:
        pre = text[:marks[0].start()].strip()
        if len(pre) > 15:
            parts.append(pre)
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            parts.append(text[m.end():end])
    else:
        lines = [l for l in re.split(r"\n+", text) if l.strip()]
        numbered = [re.sub(r"^\s*(?:\d+[.)]|[-•*→])\s+", "", l) for l in lines if re.match(r"\s*(?:\d+[.)]|[-•*→])\s+", l)]
        parts = numbered if len(lines) >= 2 and len(numbered) == len(lines) else split_sentences(re.sub(r"\s*\n+\s*", " ", text))
    steps = []
    for part in parts:
        main, fall = [], []
        for sent in split_sentences(_clean(part)):
            (fall if fall or is_fallback(sent) else main).append(sent)
        if main:
            steps.append(_clean(" ".join(main)))
        if fall:
            steps.append(_clean(" ".join(fall)))
    # a fallback never comes first: move the first real step in front of it
    if steps and is_fallback(steps[0]):
        for i, s in enumerate(steps):
            if not is_fallback(s):
                steps.insert(0, steps.pop(i))
                break
    return [s for s in steps if s]


def format_steps(steps: list) -> str:
    return " ".join(f"Step {i} — {s}" for i, s in enumerate(steps, 1))


def normalise(text: str) -> str:
    """One canonical text: 'Step 1 — ... Step 2 — ...', each step its own numbered item (empty stays empty)."""
    steps = split_steps(text)
    return format_steps(steps) if steps else (text if isinstance(text, str) else "")


def shorten_quote(text: str, limit: int = 140) -> str:
    """A quoted transcript snippet that ends at a word boundary (with an ellipsis) instead of mid-word."""
    t = " ".join((text or "").split())
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-–—.")
    return cut + "…"
