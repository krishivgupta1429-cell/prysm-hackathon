"""Built during the Meta Global AI Developer Hackathon (new work).

Grounding for everything that talks about a creator's own reels. Pure functions, no model call, no database.

A statement ("claim", a summary sentence, a sentence of a personalised answer) is GROUNDED when
  1. it cites at least one reel that exists in the import ("Reel 3"), and
  2. nothing in it is an invented detail: a quoted phrase, greeting, hashtag, @name, number, or a statement about speech, music or
     watch-through that is not backed by the cited reels' captions, fingerprints and numbers, and
  3. what it says about hook, format or pacing matches the fingerprints of the reels it cites.
The check works on the facts we really hold per reel: caption, fingerprint (hook, format, pacing, audio, on-screen text, topic),
performance index, watch-through, and how the reel was read (full video, thumbnail and caption only, caption only).
There is no transcript of imported reels, so anything about spoken words needs the words to be in the caption.
"""
import re
from dataclasses import dataclass
from typing import Iterable, Optional

UNKNOWN = (None, "", "unknown", "other")
SPEECH_AUDIO = ("speech", "speech_over_music")
MUSIC_AUDIO = ("music_only", "speech_over_music", "trending_sound")

NUMS = r"\d+(?:\s*(?:,|and|&|-|to)\s*\d+)*"
REEL_REF = re.compile(r"\b[Rr]eels?\s+(" + NUMS + r")")


def norm(s: str | None) -> str:
    """Lower case, curly quotes straightened, punctuation to spaces, spaces collapsed (so 'Hey, guys!' matches 'hey guys')."""
    s = (s or "").lower().replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9#@' ]+", " ", s)).strip()


def cited_numbers(text: str) -> list[int]:
    out: list[int] = []
    for m in REEL_REF.finditer(text or ""):
        out += [int(x) for x in re.findall(r"\d+", m.group(1))]
    return sorted(set(out))


@dataclass
class Facts:
    n: int
    caption: str          # normalised
    fp: dict
    wt: Optional[float]
    pi: Optional[float]
    level: str            # full | thumbnail_caption | caption_only


def facts_from(reels: Iterable[dict]) -> dict[int, Facts]:
    out: dict[int, Facts] = {}
    for r in reels:
        out[int(r["n"])] = Facts(int(r["n"]), norm(r.get("caption")), r.get("fingerprint") or {}, r.get("watch_through"),
                                 r.get("performance_index"), r.get("analysis_level") or "full")
    return out


# ------------------------------------------------------------------ detail checks

# (what the statement says, what a cited reel must offer for it to be true). A concrete phrase must appear in the caption itself;
# an abstract word ("greeting") is supported by any concrete example of it in a cited caption.
_GREET_CONCRETE = re.compile(r"\b(hey|hi|hello|yo|hiya)\s+(guys|everyone|friends|folks|y'?all|team|there|world)\b|\bwhat'?s up\b|"
                             r"\bwelcome(?: back)?\b|\bgood (?:morning|evening|afternoon)\b|\bhey\b", re.I)
_GREET_ABSTRACT = re.compile(r"\bgreet(?:s|ing|ings|ed)?\b|\bsay(?:s|ing)? (?:hi|hello|hey)\b|\bintroduc\w+ (?:themselves|yourself|himself|herself)\b", re.I)
_CTA_CONCRETE = re.compile(r"\blink in (?:my )?bio\b|\bfollow (?:me|for|us)\b|\bsubscribe\b|\bcomment below\b|\bsave this\b|\bshare this\b|"
                           r"\bturn on notifications\b|\bdm me\b", re.I)
_CTA_ABSTRACT = re.compile(r"\bcall[- ]to[- ]action\b|\bcta\b|\bcatch ?phrase\b|\bsign[- ]?off\b|\btag ?line\b|\boutro\b|\bslogan\b", re.I)
_CAPTION_CTA = re.compile(r"\bfollow\b|\blink in bio\b|\bcomment\b|\bsubscribe\b|\bsave\b|\bshare\b|\bdm\b|\btag\b|\bsign off\b|\bsee you\b|\bthanks?\b", re.I)
_SPEECH = re.compile(r"\b(?:says?|saying|said|speaks?|speaking|spoken|narrat\w+|dialogue|monologue|script(?:ed)?|"
                     r"talks? (?:about|through|over)|tells? (?:the )?viewers?)\b", re.I)
_MUSIC = re.compile(r"\b(?:music|song|soundtrack|trending (?:sound|audio)|background track|backing track|beat drop|sound ?bite)\b", re.I)
_WATCH = re.compile(r"\bwatch[- ]through\b|\bwatch ?time\b|\bskip rate\b|\bretention\b|\bhold(?:s|ing)? viewers\b", re.I)
_HASHTAG = re.compile(r"(?<![\w&])[#@][A-Za-z0-9_.]{2,}")
_URL = re.compile(r"https?://\S+|\bwww\.\S+", re.I)
_QUOTES = [re.compile(r'"([^"]{2,90})"'), re.compile("“([^”]{2,90})”"),
           re.compile(r"(?<![A-Za-z0-9])'([^']{3,70})'(?![A-Za-z0-9])"), re.compile("‘([^’]{3,70})’")]

# words that name a fingerprint value (hook, format, pacing) -> the value; only these are checked against the cited reels' fingerprints
VALUE_WORDS: dict[str, dict[str, re.Pattern]] = {
    "hook_type": {"question": re.compile(r"\bquestion\b", re.I), "bold_claim": re.compile(r"\bbold (?:claim|statement)\b", re.I),
                  "pattern_interrupt": re.compile(r"\bpattern[- ]interrupt", re.I), "story": re.compile(r"\bstory\b|\bstorytelling\b", re.I),
                  "demo": re.compile(r"\bdemo(?:nstration)?\b", re.I),
                  "list_promise": re.compile(r"\blist (?:promise|hook|opener)\b|\bnumbered list\b", re.I),
                  "visual_only": re.compile(r"\bvisual[- ]only\b", re.I)},
    "format": {"talking_head": re.compile(r"\btalking[- ]head\b|\bto[- ]camera\b|\bface[- ]to[- ]camera\b", re.I),
               "voiceover_broll": re.compile(r"\bvoice-?over\b|\bb-?roll\b", re.I),
               "screen_recording": re.compile(r"\bscreen[- ]record\w*\b", re.I), "montage": re.compile(r"\bmontage\b", re.I),
               "skit": re.compile(r"\bskit\b", re.I), "tutorial": re.compile(r"\btutorial\b|\bhow-?to\b", re.I),
               "text_only": re.compile(r"\btext[- ]only\b", re.I)},
    "pacing": {"slow": re.compile(r"\bslow(?:er|-paced| pacing)?\b", re.I), "medium": re.compile(r"\bmedium[- ]paced\b|\bmoderate pac\w+", re.I),
               "fast": re.compile(r"\bfast(?:er)?(?:[- ]paced| pacing| cuts?)?\b|\bquick cuts?\b|\brapid cuts?\b", re.I)},
}
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(%|x)?(?![\w])")


def _allowed_numbers(cited: list[Facts], total: int) -> set[str]:
    ok: set[str] = set()
    for f in cited:
        if f.pi is not None:
            ok |= {f"{f.pi:.2f}", f"{f.pi:.1f}", str(round(f.pi, 1)), f"{f.pi:.0f}"}
        if f.wt is not None:
            ok |= {str(int(round(f.wt * 100))), f"{f.wt * 100:.1f}"}
        ok |= set(re.findall(r"\d+(?:\.\d+)?", f.caption))
    return ok


def problems(text: str, cited: list[int], facts: dict[int, Facts]) -> list[str]:
    """Why `text` is not grounded in the reels it cites (empty list = grounded). Messages are safe to show/log."""
    out: list[str] = []
    bad = [n for n in cited if n not in facts]
    if bad:
        out.append("cites a reel that was not imported: " + ", ".join(f"Reel {n}" for n in bad))
    cf = [facts[n] for n in cited if n in facts]
    if not cf:
        return out or ["cites no reel"]
    captions = " | ".join(f.caption for f in cf)
    topics = " | ".join(norm(str(f.fp.get("topic") or "")) for f in cf)
    raw = REEL_REF.sub(" ", text)

    # 1. quoted phrases must be in a cited caption (or be the topic the model read from the frames)
    for rx in _QUOTES:
        for m in rx.finditer(raw):
            q = norm(m.group(1))
            if len(q) >= 3 and q not in captions and q not in topics:
                out.append(f"quotes \"{m.group(1)[:40]}\", which is not in the cited reels' captions")
    # 2. hashtags, @names, links
    for m in list(_HASHTAG.finditer(raw)) + list(_URL.finditer(raw)):
        if norm(m.group(0)) not in captions:
            out.append(f"mentions {m.group(0)[:30]}, which is not in the cited reels' captions")
    # 3. greetings / sign-offs / catchphrases
    for m in _GREET_CONCRETE.finditer(raw):
        if norm(m.group(0)) not in captions:
            out.append(f"mentions the greeting \"{m.group(0)}\", which is not in the cited reels' captions")
    if _GREET_ABSTRACT.search(raw) and not _GREET_CONCRETE.search(captions):
        out.append("describes a greeting, but no cited caption has one")
    for m in _CTA_CONCRETE.finditer(raw):
        if norm(m.group(0)) not in captions:
            out.append(f"mentions \"{m.group(0)}\", which is not in the cited reels' captions")
    if _CTA_ABSTRACT.search(raw) and not _CAPTION_CTA.search(captions):
        out.append("describes a sign-off or call to action, but no cited caption has one")
    # 4. speech and music: the fingerprint's audio type is the only evidence besides the caption
    if _SPEECH.search(raw) and not any(f.fp.get("audio_type") in SPEECH_AUDIO for f in cf) \
            and not any(m.group(0).lower() in captions for m in _SPEECH.finditer(raw)):
        out.append("says what is spoken, but nothing shows speech in the cited reels (audio is unknown)")
    if _MUSIC.search(raw) and not any(f.fp.get("audio_type") in MUSIC_AUDIO for f in cf) \
            and not any(m.group(0).lower() in captions for m in _MUSIC.finditer(raw)):
        out.append("names music or a sound, but nothing shows it in the cited reels (audio is unknown)")
    # 5. watch-through / skip / retention need a cited reel that has a measured watch-through
    if _WATCH.search(raw) and not any(f.wt is not None for f in cf):
        out.append("talks about watch-through, but none of the cited reels has a measured watch-through")
    # 6. numbers other than reel numbers and small counts
    allowed = _allowed_numbers(cf, len(facts))
    for m in _NUMBER.finditer(raw):
        val, unit = m.group(1), m.group(2)
        if unit is None and "." not in val and int(val) <= max(30, len(facts)):
            continue   # a count ("3 of the 4 weakest reels"), bounded by the sample size
        if val not in allowed and val.rstrip("0").rstrip(".") not in allowed:
            out.append(f"uses the number {val}{unit or ''}, which is not one of the cited reels' numbers")
    # 7. what it says about hook / format / pacing must match the cited fingerprints
    named = [(cat, v) for cat, vs in VALUE_WORDS.items() for v, rx in vs.items() if rx.search(raw)]
    if named:
        ok = False
        for cat, v in named:
            known = [f.fp.get(cat) for f in cf if f.fp.get(cat) not in UNKNOWN and f.level == "full"]
            hit = sum(1 for k in known if k == v)
            if hit and hit >= len(known) / 2:
                ok = True
                break
        if not ok:
            out.append("names a hook, format or pacing that the cited reels' fingerprints do not show")
    seen, uniq = set(), []
    for p in out:   # same message once
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def check_claim(claim: str, reel_ids: list, facts: dict[int, Facts], *, kind: str | None = None,
                top: Iterable[int] = (), bottom: Iterable[int] = ()) -> list[str]:
    """Problems with one structured claim {"claim", "reels"}. `kind` strongest/weakest also requires the cited reels to be
    mostly from the top / bottom group the claim talks about."""
    ids: list[int] = []
    for x in reel_ids or []:
        if str(x).lstrip("-").isdigit():
            ids.append(int(x))
    if not ids:
        return ["cites no reel"]
    out = problems(claim, sorted(set(ids)), facts)
    group = {"strongest": set(top), "weakest": set(bottom)}.get(kind or "")
    if group and sum(1 for n in set(ids) if n in group) < len(set(ids)) / 2:
        out.append(f"is filed under {kind} reels but cites mostly reels outside that group")
    return out


# ------------------------------------------------------------------ free text (the personalised analysis)

_PERSONAL = re.compile(
    r"\byour (?:last|previous|recent|other|past|earlier|usual|typical|normal|top|best|strongest|weakest|best[- ]performing) "
    r"(?:\d+ )?(?:reels?|videos?|posts?|content)\b|\bcompared (?:to|with) your (?:usual|other|typical|previous|own|last)\b|"
    r"\byou (?:usually|typically|normally|tend to)\b|\blast \d+ reels\b|\byour (?:reel|video) history\b", re.I)
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENT.split(text.strip())] if text and text.strip() else []


def sentence_problems(sentence: str, facts: dict[int, Facts]) -> list[str] | None:
    """None when the sentence says nothing about the creator's own reels. Otherwise the list of problems (empty = grounded)."""
    refs = cited_numbers(sentence)
    if refs:
        return problems(sentence, refs, facts)
    if _PERSONAL.search(sentence):
        return ["talks about the creator's own reels without naming one"]
    return None


def check_fields(result: dict, facts: dict[int, Facts], fields: Iterable[str]) -> list[dict]:
    """Every sentence of the analysis (summary + each parameter's text fields) that talks about the creator's reels and is not
    grounded: [{"where": ("summary",) | ("parameters", i, field), "sentence", "problems"}]."""
    bad: list[dict] = []

    def scan(where, text):
        if isinstance(text, str):
            for s in split_sentences(text):
                p = sentence_problems(s, facts)
                if p:
                    bad.append({"where": where, "sentence": s, "problems": p})
    scan(("summary",), result.get("summary"))
    for i, prm in enumerate(result.get("parameters") or []):
        if isinstance(prm, dict):
            for f in fields:
                scan(("parameters", i, f), prm.get(f))
    return bad


def _get(result: dict, where: tuple):
    return result.get(where[0]) if where[0] == "summary" else (result["parameters"][where[1]] or {}).get(where[2])


def _put(result: dict, where: tuple, value: str):
    if where[0] == "summary":
        result["summary"] = value
    else:
        result["parameters"][where[1]][where[2]] = value


def apply_fixes(result: dict, bad: list[dict], fixed: dict[int, str | None], placeholder: str) -> None:
    """Replace each bad sentence by its fixed text, or drop it (None). A field left empty gets `placeholder`."""
    by_where: dict[tuple, list[tuple[str, str | None]]] = {}
    for i, b in enumerate(bad):
        by_where.setdefault(b["where"], []).append((b["sentence"], fixed.get(i)))
    for where, items in by_where.items():
        text = _get(result, where) or ""
        for old, new in items:
            text = text.replace(old, new if new else "", 1)
        text = re.sub(r"\s{2,}", " ", text).strip()
        _put(result, where, text or placeholder)
