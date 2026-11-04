"""Built during the Meta Global AI Developer Hackathon (new work).

Creator DNA: a short fingerprint per reel (one cheap Spark call on frames + caption), then one synthesis call that writes the
DNA JSON and a plain-language summary from the top and bottom reels. Claims must cite reels; uncited or invalid claims are
dropped in code. Medians everywhere, so one viral reel cannot define the creator. Style: backend/app/studio/coach_style.md.
"""
import json
import logging
import re
from collections import Counter
from typing import Any, Awaitable, Callable, Optional

from app.studio import config
from app.studio.instagram import grounding as G
from app.studio.instagram import metrics as M
from app.studio.json_tolerant import extract_json_object

log = logging.getLogger(__name__)

DEFAULT_DNA_MODEL = "muse-spark-1.3"
DEFAULT_COST_CAP_USD = 0.60       # per import: fingerprints stop when this is reached (synthesis keeps its own small reserve)
SYNTH_RESERVE_FRACTION = 0.15
OUTLIER_INDEX = 3.0               # performance_index above this (3x the account median) marks a viral outlier

ALLOWED = {
    "hook_type": ("question", "bold_claim", "pattern_interrupt", "story", "demo", "list_promise", "visual_only", "other"),
    "format": ("talking_head", "voiceover_broll", "screen_recording", "montage", "skit", "tutorial", "text_only", "other"),
    "pacing": ("slow", "medium", "fast"),
    "audio_type": ("speech", "music_only", "speech_over_music", "trending_sound", "unknown"),
}
FP_FIELDS = ("hook_type", "format", "pacing", "audio_type", "on_screen_text", "topic")


def dna_model() -> str:
    return config.get_env("META_DNA_MODEL") or DEFAULT_DNA_MODEL


def cost_cap_usd() -> float:
    try:
        return float(config.get_env("DNA_COST_CAP_USD") or DEFAULT_COST_CAP_USD)
    except ValueError:
        return DEFAULT_COST_CAP_USD


# ------------------------------------------------------------------ model call (through the existing shim)

class Spend:
    """Running cost of one import/analysis; logged to the shim's own ledger and returned to the caller for safety.record_spend."""

    def __init__(self, cap: float):
        self.cap = cap
        self.usd = 0.0
        self.calls = 0
        self.transcribe_usd = 0.0       # Voice Transcribe (hook words), included in usd
        self.transcribed = 0            # reels whose first seconds were sent to Voice Transcribe

    def add(self, usd: float):
        self.usd = round(self.usd + (usd or 0.0), 6)
        self.calls += 1

    def add_transcribe(self, usd: float):
        self.usd = round(self.usd + (usd or 0.0), 6)
        self.transcribe_usd = round(self.transcribe_usd + (usd or 0.0), 6)
        self.transcribed += 1

    def room_for_fingerprint(self) -> bool:
        return self.usd < self.cap * (1 - SYNTH_RESERVE_FRACTION)


async def _ask(content: list, max_tokens: int, spend: Spend, *, system: str | None = None) -> str:
    from app.studio.meta_llm_shim import get_shim_client
    client = get_shim_client()
    msg = await client.messages.create(model=dna_model(), max_tokens=max_tokens, system=system,
                                       messages=[{"role": "user", "content": content}],
                                       reasoning_effort="low", headroom=1500)
    spend.add(getattr(msg, "cost_usd", 0.0))
    return msg.content[0].text


def _parse(text: str) -> dict | None:
    try:
        j = json.loads(extract_json_object(text))
        return j if isinstance(j, dict) else None
    except ValueError:
        return None


# ------------------------------------------------------------------ fingerprints

FP_SYSTEM = ("You label short-form videos for a creator-coaching tool. Answer with ONE JSON object and nothing else. "
             "Use only what the frames and caption show. If you cannot tell, use \"unknown\" or \"other\"; never guess details.")


def _fp_prompt(caption: str | None, n_frames: int, level: str = "full", hook_words: str | None = None) -> str:
    opts = "; ".join(f'"{k}": one of {list(v)}' for k, v in ALLOWED.items())
    if level != "full":   # no video from Instagram: one still image (or nothing) and the caption
        what = ("This single image is the thumbnail of one Instagram reel" if n_frames else "No image is available for one Instagram reel")
        return (f"{what}; Instagram gave no video file. Caption: \"{(caption or '')[:300]}\".\n"
                f"Return JSON with exactly these keys: {opts}; \"on_screen_text\": true or false (is there written text on screen); "
                "\"topic\": at most 6 words. You cannot judge the opening seconds, format, pacing or audio from one still image: for hook_type "
                "and format answer \"other\", for pacing \"medium\", for audio_type \"unknown\".")
    spoken = (f" The first seconds of audio were transcribed as: \"{hook_words[:240]}\"." if hook_words else "")
    return (f"These {n_frames} frames are spread evenly over one Instagram reel. Caption: \"{(caption or '')[:300]}\".{spoken}\n"
            f"Return JSON with exactly these keys: {opts}; \"on_screen_text\": true or false (is there written text on screen); "
            "\"topic\": at most 6 words. hook_type is how the first seconds try to hold attention. "
            "audio_type cannot be seen: use \"unknown\" unless the frames or caption make it obvious (for example a person speaking to camera).")


def normalise_fingerprint(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    fp: dict[str, Any] = {}
    for k, allowed in ALLOWED.items():
        v = str(raw.get(k, "")).strip().lower().replace(" ", "_").replace("-", "_")
        fp[k] = v if v in allowed else ("unknown" if k == "audio_type" else "other" if k != "pacing" else None)
    ost = raw.get("on_screen_text")
    fp["on_screen_text"] = ost if isinstance(ost, bool) else str(ost).strip().lower() in ("true", "yes", "1")
    fp["topic"] = str(raw.get("topic", "")).strip()[:60]
    if fp["pacing"] is None:
        fp["pacing"] = "medium"
    return fp


def mark_partial(fp: dict | None, level: str) -> dict | None:
    """A reel read without its video: hook, format, pacing and audio are NOT judged (set to "unknown"), whatever the model answered."""
    if fp is not None and level != "full":
        fp = {**fp, "hook_type": "unknown", "format": "unknown", "pacing": "unknown", "audio_type": "unknown", "hook_words": None}
    return fp


async def fingerprint_reel(frames_b64_blocks: list[dict], caption: str | None, spend: Spend, level: str = "full",
                           hook_words: str | None = None) -> dict | None:
    """One call on the frames plus the caption. None if the call or the parsing failed (the reel stays, without a fingerprint).
    level != "full" (no video link): thumbnail/caption only, so hook, format, pacing and audio come back "unknown"."""
    content = list(frames_b64_blocks) + [{"type": "text", "text": _fp_prompt(caption, len(frames_b64_blocks), level, hook_words)}]
    if not frames_b64_blocks and level == "full":
        content = [{"type": "text", "text": _fp_prompt(caption, 0, "caption_only")}]
    try:
        text = await _ask(content, 400, spend, system=FP_SYSTEM)
    except Exception as e:  # network, rate limit, missing key: the import goes on without this fingerprint
        log.warning("[DNA] fingerprint call failed: %s", type(e).__name__)
        return None
    fp = mark_partial(normalise_fingerprint(_parse(text)), level)
    if fp is not None and level == "full":
        fp["hook_words"] = hook_words or None            # the real words, from the transcript (never the model's)
        if hook_words and fp.get("audio_type") not in G.SPEECH_AUDIO:
            fp["audio_type"] = "speech"                  # speech was heard
    return fp


async def fingerprint_text_only(summary_text: str, spend: Spend) -> dict | None:
    """Fingerprint of a NEW video from what the analysis already found out about it (no extra frames sent)."""
    prompt = (f"Here is what an analysis found about one short video:\n{summary_text[:3500]}\n\n" + _fp_prompt(None, 0)
              .split("\n", 1)[1])
    try:
        text = await _ask([{"type": "text", "text": prompt}], 400, spend, system=FP_SYSTEM)
    except Exception as e:
        log.warning("[DNA] new-video fingerprint failed: %s", type(e).__name__)
        return None
    return normalise_fingerprint(_parse(text))


# ------------------------------------------------------------------ the DNA

def reel_label(r: dict) -> str:
    return f"Reel {r['n']}"


def _fp_line(fp: dict | None) -> str:
    if not fp:
        return "no fingerprint"
    return (f"hook={fp['hook_type']}, format={fp['format']}, pacing={fp['pacing']}, audio={fp['audio_type']}, "
            f"on-screen text={'yes' if fp['on_screen_text'] else 'no'}, topic={fp['topic']}")


def prepare(reels: list[dict]) -> dict:
    """Number the reels (Reel 1 = most recent), score them, split top/bottom. Pure; used before and without any model call.
    Each reel dict needs: performance_index, watch_through, fingerprint, caption, posted_at (optional)."""
    ordered = sorted(reels, key=lambda r: r.get("posted_at") or "", reverse=True)
    for i, r in enumerate(ordered, 1):
        r["n"] = i
    wt_med = M.account_median(r.get("watch_through") for r in ordered)
    for r in ordered:
        r["score"] = M.blended_score(r.get("performance_index"), r.get("watch_through"), wt_med)
    top, bottom, _ = M.split_top_bottom(ordered)
    # one reel far above the account's median (index > 3x) is an outlier: flagged, and the summary says medians were used
    outlier = any((r.get("performance_index") or 0) > OUTLIER_INDEX for r in ordered)
    scored = sum(1 for r in ordered if r["score"] is not None)
    partial = [r["n"] for r in ordered if (r.get("analysis_level") or "full") != "full"]   # read without their video
    return {"reels": ordered, "top": top, "bottom": bottom, "outlier": outlier, "scored": scored,
            "n": len(ordered), "wt_median": wt_med, "partial": partial}


def header(prep: dict, basis: str | None, missing: list[str]) -> str:
    n, scored = prep["n"], prep["scored"]
    parts = [f"Based on your {n} most recent imported reel{'s' if n != 1 else ''}"
             f" ({scored} with enough Instagram numbers to rank)."]
    if prep.get("partial"):
        k = len(prep["partial"])
        parts.append(f"{n - k} {'was' if n - k == 1 else 'were'} read from the video; {k} only from the thumbnail and caption "
                     f"(Instagram gave no video file, often licensed audio), so {'it has' if k == 1 else 'they have'} no watch-through and "
                     "no hook, format or pacing judgement.")
    if missing:
        parts.append("Instagram returned nothing for: " + ", ".join(M.METRIC_LABEL[m] for m in missing) + ".")
    if n >= M.MIN_FOR_DNA:
        parts.append("These are patterns in a small sample, not proof.")
    if prep["outlier"]:
        parts.append("One reel did far better than the rest; the comparison uses medians so it does not define your normal.")
    return " ".join(parts)


def _common(reels: list[dict], key: str) -> list[tuple[Any, int]]:
    c = Counter(r["fingerprint"][key] for r in reels if r.get("fingerprint") and r["fingerprint"].get(key) not in G.UNKNOWN)
    return c.most_common()


def descriptive_dna(prep: dict, basis: str | None, missing: list[str], reason: str) -> dict:
    """No model call: used when there are too few reels or too little data to rank. Honest and short."""
    lines = []
    for key, label in (("hook_type", "hook"), ("format", "format"), ("pacing", "pacing")):
        mc = _common(prep["reels"], key)
        have = sum(c for _, c in mc)
        if mc and have:
            val, c = mc[0]
            if c >= 2:
                cites = [reel_label(r) for r in prep["reels"] if r.get("fingerprint") and r["fingerprint"].get(key) == val]
                lines.append({"claim": f"{c} of {have} reels use the same {label}: {str(val).replace('_', ' ')}",
                              "reels": [int(x.split()[1]) for x in cites]})
    return {"mode": "descriptive", "reason": reason, "shared": lines, "strongest": [], "weakest": [], "keep": [], "change": [],
            "sample_size": prep["n"], "scored": prep["scored"], "outlier": prep["outlier"],
            "partial_reels": prep.get("partial", [])}


SYNTH_SYSTEM = ("You are a sharp, friendly creator-coach. Write like talking to one creator about their own reels: clear, descriptive, "
                "plain words, no jargon, no filler openers, no invented numbers. Every claim must name the reels it comes from "
                "(for example 'Reels 2, 5'). Describe patterns; never promise results. Answer with ONE JSON object and nothing else.")


def _synth_prompt(prep: dict, repair: str = "") -> str:
    def row(r):
        wt = f"{r['watch_through']:.0%}" if r.get("watch_through") is not None else "unknown"
        pi = f"{r['performance_index']:.2f}" if r.get("performance_index") is not None else "unknown"
        tag = "" if (r.get("analysis_level") or "full") == "full" else " [THUMBNAIL+CAPTION ONLY: no video, so hook, format, pacing and audio are unknown]"
        hw = r.get("hook_words")
        hook = f"first spoken words: \"{hw[:140]}\"" if hw else "first spoken words: unknown"
        return (f"Reel {r['n']}: index {pi} (1.00 = typical for this account), watch-through {wt}; "
                f"{_fp_line(r.get('fingerprint'))}; {hook}; caption: \"{(r.get('caption') or '')[:120]}\"{tag}")
    top_ids = {r["n"] for r in prep["top"]}
    bottom_ids = {r["n"] for r in prep["bottom"]}
    rows = "\n".join(("[TOP] " if r["n"] in top_ids else "[BOTTOM] " if r["n"] in bottom_ids else "[MIDDLE] ") + row(r) for r in prep["reels"])
    outlier = ("One reel is far above the rest. Treat it as an outlier: do not build a habit on it alone and say so.\n"
               if prep["outlier"] else "")
    return (f"Here are {prep['n']} recent reels from one creator, newest first, labelled TOP, MIDDLE or BOTTOM by their own Instagram "
            f"numbers.\n{rows}\n\n{outlier}"
            "Return JSON with these keys:\n"
            '"summary": 90-140 words of plain prose: what the strongest reels share, what repeats in the weakest, one or two habits '
            "to keep, one or two to change. Name reels as 'Reel N'. Do not use the words TOP, MIDDLE or BOTTOM (they are internal labels).\n"
            '"strongest": [{"claim": "...", "reels": [N, ...]}]  (what the TOP reels share; 1-3 items)\n'
            '"weakest": [{"claim": "...", "reels": [N, ...]}]  (what repeats in the BOTTOM reels; 1-3 items)\n'
            '"keep": [{"claim": "habit to keep", "reels": [N, ...]}]  (1-2 items)\n'
            '"change": [{"claim": "habit to change", "reels": [N, ...]}]  (1-2 items)\n'
            "Only claim what the rows above support, and cite at least one reel number in every item. Use ONLY facts that are in the rows (the first spoken words are real and may be quoted): "
            "no other quoted words, greetings, catchphrases, hashtags, music or spoken lines that are not in a caption, and no numbers that are "
            "not in the rows. Do not base a claim about hooks, pacing or watch-through on a THUMBNAIL+CAPTION ONLY reel."
            + (("\n\n" + repair) if repair else ""))


_NUMS = r"\d+(?:\s*(?:,|and|&)\s*\d+)*"


_LABEL = re.compile(r"^(?:the\s+)?(?:(?:top|bottom|middle)\s+reels?\s*(?:" + _NUMS + r")?\s*|reels\s+" + _NUMS + r"\s*)", re.I)


def _strip_labels(claim: str) -> str:
    """Replace the internal TOP/BOTTOM labels and a leading 'Reels 2, 3' (the citation is kept in `reels`) by 'These reels'."""
    c, stripped = claim, False
    while True:
        m = _LABEL.match(c)
        if not m or not c[m.end():].strip():
            break
        c, stripped = c[m.end():], True
    c = ("These reels " + c) if stripped else c
    return c[:1].upper() + c[1:]


def validate_dna_report(j: dict | None, prep: dict) -> tuple[dict | None, list[dict], int]:
    """(dna or None, rejected, checked). A claim survives only when it is GROUNDED (grounding.py): it cites reels that exist,
    invents no detail (quotes, greetings, numbers, speech, music) and matches the cited fingerprints. `rejected` lists
    {"where", "text", "problems"} for everything dropped, for the one repair attempt and for the stats."""
    if not isinstance(j, dict):
        return None, [], 0
    facts = G.facts_from(prep["reels"])
    top, bottom = [r["n"] for r in prep["top"]], [r["n"] for r in prep["bottom"]]
    media = {r["n"]: r.get("media_id") for r in prep["reels"]}
    out: dict[str, Any] = {}
    rejected: list[dict] = []
    checked = 0
    for key in ("strongest", "weakest", "keep", "change"):
        items = []
        for it in j.get(key) or []:
            if not isinstance(it, dict) or not str(it.get("claim", "")).strip():
                continue
            checked += 1
            claim_raw = str(it["claim"]).strip()
            ids = sorted({int(x) for x in (it.get("reels") or []) if str(x).lstrip("-").isdigit()})
            probs = G.check_claim(claim_raw, ids, facts, kind=key, top=top, bottom=bottom)
            if probs:
                rejected.append({"where": key, "text": claim_raw[:200], "problems": probs})
                continue
            claim = _strip_labels(claim_raw)
            items.append({"claim": (claim[:1].upper() + claim[1:])[:300], "reels": ids,
                          "media_ids": [media.get(n) for n in ids if media.get(n)]})
        out[key] = items[:3]
    summary = str(j.get("summary") or "").strip()
    cited = set(G.cited_numbers(summary))
    sprobs = G.problems(summary, sorted(cited), facts) if summary and cited else (["cites no reel"] if summary else [])
    if sprobs:
        rejected.append({"where": "summary", "text": summary[:200], "problems": sprobs})
    out["summary"] = summary if summary and cited and not sprobs else ""
    if not any(out[k] for k in ("strongest", "weakest", "keep", "change")):
        return None, rejected, checked
    return out, rejected, checked


def validate_dna(j: dict | None, prep: dict) -> dict | None:
    """Keep only grounded claims; None when nothing usable survives."""
    return validate_dna_report(j, prep)[0]


def _repair_note(rejected: list[dict]) -> str:
    lines = [f"- {r['where']}: \"{r['text'][:140]}\" -> {'; '.join(r['problems'])}" for r in rejected[:8]]
    return ("Your previous answer was checked against the rows and these items were rejected:\n" + "\n".join(lines) +
            "\nWrite the whole JSON again. Keep what was fine, and fix or remove the rejected items using only what the rows show.")


def summary_from_claims(dna: dict) -> str:
    def cite(it):
        return f"{it['claim']} (Reel{'s' if len(it['reels']) > 1 else ''} {', '.join(map(str, it['reels']))})"
    parts = []
    for key, lead in (("strongest", "Your strongest reels: "), ("weakest", "What repeats in your weakest: "),
                      ("keep", "Keep: "), ("change", "Change: ")):
        if dna.get(key):
            parts.append(lead + "; ".join(cite(i) for i in dna[key]) + ".")
    return " ".join(parts)


async def build_dna(reels: list[dict], basis: str | None, missing: list[str], spend: Spend) -> dict:
    """Returns {"dna": {...}, "summary": str, "sample_size": n}. The model is called once, and only when there is enough to rank."""
    prep = prepare(reels)
    hdr = header(prep, basis, missing)
    if prep["n"] < M.MIN_FOR_DNA:   # 1-2 reels: the per-reel breakdown only, no pattern claim of any kind
        dna = {"mode": "breakdown", "reason": "Patterns need at least 3 reels.", "shared": [], "strongest": [], "weakest": [], "keep": [],
               "change": [], "sample_size": prep["n"], "scored": prep["scored"], "outlier": False,
               "partial_reels": prep.get("partial", [])}
        return {"dna": dna, "summary": hdr + " " + dna["reason"], "sample_size": prep["n"], "prep": prep}
    if prep["n"] < M.MIN_FOR_PATTERNS:   # 3-4 reels: what the reels have in common, worded as early observations
        dna = descriptive_dna(prep, basis, missing, f"Only {prep['n']} reels: these are early observations, not patterns.")
        return {"dna": dna, "summary": hdr + " " + dna["reason"] + (" " + "; ".join(f"{s['claim']} (Reels {', '.join(map(str, s['reels']))})" for s in dna["shared"]) + "." if dna["shared"] else ""),
                "sample_size": prep["n"], "prep": prep}
    if not prep["top"]:
        dna = descriptive_dna(prep, basis, missing, "Instagram gave too few reach or view numbers to say which reels did better.")
        return {"dna": dna, "summary": hdr + " " + dna["reason"], "sample_size": prep["n"], "prep": prep}
    j = None
    try:
        j = _parse(await _ask([{"type": "text", "text": _synth_prompt(prep)}], 1100, spend, system=SYNTH_SYSTEM))
    except Exception as e:
        log.warning("[DNA] synthesis call failed: %s", type(e).__name__)
    dna, rejected, checked = validate_dna_report(j, prep)
    regenerated = False
    if (rejected or dna is None) and j is not None and spend.usd < spend.cap:
        # ONE repair attempt, told exactly what was rejected and why; the better of the two answers is kept
        regenerated = True
        try:
            j2 = _parse(await _ask([{"type": "text", "text": _synth_prompt(prep, _repair_note(rejected))}], 1100, spend, system=SYNTH_SYSTEM))
        except Exception as e:
            log.warning("[DNA] repair call failed: %s", type(e).__name__)
            j2 = None
        dna2, rejected2, checked2 = validate_dna_report(j2, prep)
        size = lambda d: sum(len(d[k]) for k in ("strongest", "weakest", "keep", "change")) if d else 0
        if dna2 is not None and (dna is None or len(rejected2) < len(rejected) or (len(rejected2) == len(rejected) and size(dna2) >= size(dna))):
            dna, rejected, checked = dna2, rejected2, checked2
    if dna is None:
        d = descriptive_dna(prep, basis, missing, "The summary could not be written this time; re-import to try again.")
        d["grounding"] = {"checked": checked, "dropped": len(rejected), "regenerated": regenerated}
        return {"dna": d, "summary": hdr + " " + d["reason"], "sample_size": prep["n"], "prep": prep}
    dna.update(mode="full", sample_size=prep["n"], scored=prep["scored"], outlier=prep["outlier"],
               top=[r["n"] for r in prep["top"]], bottom=[r["n"] for r in prep["bottom"]], partial_reels=prep["partial"],
               grounding={"checked": checked, "dropped": len(rejected), "regenerated": regenerated})
    return {"dna": dna, "summary": hdr + "\n\n" + (dna["summary"] or summary_from_claims(dna)), "sample_size": prep["n"], "prep": prep}
