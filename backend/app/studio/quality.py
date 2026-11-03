"""Built during the Meta Global AI Developer Hackathon (new work).

Deterministic checks on a finished analysis: text that stops mid-sentence, empty required fields, steps per parameter,
transcript quotes, ready-to-say wording, repeated closing phrases. Used to log truncation in a live run and for the
bake-off metrics (scripts/rescore.py).
"""
import re
from typing import Iterable

from app.core.config import SCORED_PARAMETERS

TEXT_FIELDS = ("problem", "fix", "whatIsHappening", "whyItMatters", "whatToChange", "howToDoIt", "whatToExpect")
REQUIRED_FIELDS = ("problem", "fix", "whatIsHappening", "whyItMatters", "whatToChange", "howToDoIt", "whatToExpect")

_QUOTES = "'\"‘’“”"
_STEP_RE = re.compile(r"Step\s*\d+\s*[—:\-–.]")
_OPEN_CLOSE = {"“": "”", "‘": "’", "(": ")", "[": "]"}
_EMOJI_END = re.compile(r"[\U0001F300-\U0001FAFF☀-➿️]\s*$")
_ENDS_OK = tuple(".!?…)]}") + tuple(_QUOTES) + ("*",)


def looks_truncated(text: str) -> bool:
    """True when a text stops mid-word or mid-sentence: no closing punctuation/quote/emoji at the end, or a quote opened and never closed."""
    t = (text or "").strip()
    if not t:
        return False
    if _EMOJI_END.search(t):
        return False
    if t[-1] not in _ENDS_OK:
        return True
    if t.count("“") > t.count("”") or t.count("(") > t.count(")"):
        return True
    return False


def step_count(how: str) -> int:
    return len(_STEP_RE.findall(how or ""))


def find_truncated(result: dict) -> list:
    """[(parameter, field, tail)] for every field that looks cut off; an empty required field on a parameter counts too."""
    out = []
    for p in (result or {}).get("parameters", []) or []:
        for f in REQUIRED_FIELDS:
            v = p.get(f)
            if v is None or (isinstance(v, str) and not v.strip()):
                out.append((p.get("name"), f, ""))
            elif isinstance(v, str) and looks_truncated(v):
                out.append((p.get("name"), f, v.strip()[-40:]))
    return out


_CLOSERS_TAIL_WORDS = 7


def closing_phrases(result: dict) -> dict:
    """Last sentence of each parameter's howToDoIt (lower-cased, first 7 words) -> number of parameters ending the same way."""
    counts: dict = {}
    for p in (result or {}).get("parameters", []) or []:
        how = (p.get("howToDoIt") or "").strip()
        if not how:
            continue
        last = re.split(r"(?<=[.!?])\s+", how)[-1]
        last = re.sub(r"^Step\s*\d+\s*[—:\-–.]\s*", "", last).lower()
        key = " ".join(re.findall(r"[a-z']+", last)[:_CLOSERS_TAIL_WORDS])
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def repeated_closings(result: dict) -> int:
    """How many parameters share a closing phrase with another parameter (3 parameters ending alike count as 3)."""
    return sum(c for c in closing_phrases(result).values() if c > 1)


_QUOTED = re.compile(r"[‘'“\"]([^'’\"”]{4,200})[’'”\"]")
_READY = re.compile(r"[‘'“\"][^'’\"”]{4,200}[’'”\"]")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def transcript_quotes(result: dict, transcript: str) -> int:
    """Quoted phrases in the answer that appear word for word in the transcript (punctuation and case ignored)."""
    tn = _norm(transcript)
    if not tn:
        return 0
    n = 0
    for p in (result or {}).get("parameters", []) or []:
        for f in TEXT_FIELDS:
            for q in _QUOTED.findall(p.get(f) or ""):
                qn = _norm(q)
                if len(qn.split()) >= 2 and qn in tn:
                    n += 1
    return n


def advisory_with_ready_wording(result: dict) -> tuple:
    """(advisory parameters whose steps contain a quoted ready-to-say line, advisory parameters)."""
    adv = [p for p in (result or {}).get("parameters", []) or [] if p.get("advisory")]
    return sum(1 for p in adv if _READY.search(p.get("howToDoIt") or "")), len(adv)


def steps_per_scored(result: dict) -> dict:
    return {p.get("name"): step_count(p.get("howToDoIt")) for p in (result or {}).get("parameters", []) or []
            if p.get("name") in SCORED_PARAMETERS}


def avg_words(result: dict, field: str = "problem") -> float:
    vals = [len((p.get(field) or "").split()) for p in (result or {}).get("parameters", []) or []]
    return round(sum(vals) / len(vals), 1) if vals else 0.0
