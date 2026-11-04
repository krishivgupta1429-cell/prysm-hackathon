"""Built during the Meta Global AI Developer Hackathon (new work).

Uses a creator's DNA on new analyses: (1) the "Creator DNA" addendum appended to the two scoring prompts (a new optional
parameter; the baseline prompt text is unchanged), (2) the "How this compares to your last N reels" block saved on the
result, (3) the measured numbers shown in the Predicted Viewer Behaviour area.
"""
import logging
from typing import Any, Optional

from sqlalchemy import select

from app.studio import config
from app.studio.instagram import dna as D
from app.studio.instagram import grounding as G
from app.studio.instagram import metrics as M
from app.studio.instagram.models import CreatorDna, IgAccount, IgReel, IgReelInsight

log = logging.getLogger(__name__)
COMPARE_FIELDS = (("hook_type", "Hook"), ("format", "Format"), ("pacing", "Pacing"), ("on_screen_text", "On-screen text"),
                  ("audio_type", "Audio"))


def personalise_enabled() -> bool:
    """COACH_PERSONALISE: "0" turns it off, "1" (or unset) leaves it on for users who have a DNA."""
    return (config.get_env("COACH_PERSONALISE") or "1") != "0"


PARTIAL_REASON = {
    "thumbnail_caption": "Instagram gave no video file for this reel (often licensed audio). Read from the thumbnail and caption only: "
                         "no watch-through, and hook, format and pacing are not judged.",
    "caption_only": "Instagram gave no video or thumbnail for this reel. Read from the caption only: no watch-through, "
                    "and hook, format and pacing are not judged.",
}


def level_of(row) -> str:
    """How a reel was read. Rows imported before the column existed: no duration and no frames => caption only; no duration => thumbnail."""
    lvl = getattr(row, "analysis_level", None)
    if lvl:
        return lvl
    if row.duration_s is None and not (row.frames_used or 0):
        return "caption_only"
    return "thumbnail_caption" if row.duration_s is None else "full"


async def load(db, user_id) -> Optional[dict]:
    """{"account", "dna", "reels"(numbered newest first, as dicts)} for a user with a DNA, else None."""
    acct = (await db.execute(select(IgAccount).where(IgAccount.user_id == user_id))).scalar_one_or_none()
    if acct is None:
        return None
    dna = (await db.execute(select(CreatorDna).where(CreatorDna.account_id == acct.id))).scalar_one_or_none()
    rows = (await db.execute(select(IgReel).where(IgReel.account_id == acct.id))).scalars().all()
    rows = sorted(rows, key=lambda r: r.posted_at.isoformat() if r.posted_at else "", reverse=True)
    ins: dict = {}
    if rows:
        for i in (await db.execute(select(IgReelInsight).where(IgReelInsight.reel_id.in_([r.id for r in rows])))).scalars():
            ins.setdefault(i.reel_id, {})[i.metric] = i.value if i.available else None
    reels = [{"n": i, "id": str(r.id), "media_id": r.media_id, "permalink": r.permalink, "posted_at": r.posted_at,
              "caption": r.caption, "duration_s": r.duration_s, "performance_index": r.performance_index,
              "performance_basis": r.performance_basis, "watch_through": r.watch_through, "fingerprint": r.fingerprint,
              "frames_used": r.frames_used, "note": r.note, "like_count": r.like_count, "comments_count": r.comments_count,
              "thumbnail_url": r.thumbnail_url, "insights": {m: ins.get(r.id, {}).get(m) for m in M.METRICS},
              "vs_usual": M.plain_vs_usual(r.performance_index, r.performance_basis),
              "analysis_level": level_of(r), "partial": level_of(r) != "full", "partial_reason": PARTIAL_REASON.get(level_of(r))}
             for i, r in enumerate(rows, 1)]
    return {"account": acct, "dna": dna, "reels": reels}


def _cite(items: list[dict]) -> str:
    return "; ".join(f"{i['claim']} (Reel{'s' if len(i['reels']) > 1 else ''} {', '.join(map(str, i['reels']))})" for i in items)


def usable(data: Optional[dict]) -> bool:
    return bool(data and data["dna"] is not None and (data["dna"].dna or {}).get("mode") == "full")


def addendum(data: Optional[dict]) -> str:
    """The text appended to both scoring prompts, or "" when there is no usable DNA or personalisation is off."""
    if not personalise_enabled() or not usable(data):
        return ""
    row, dna = data["dna"], data["dna"].dna
    lines = ["CREATOR DNA ADDENDUM - THIS CREATOR'S OWN REELS",
             "(This addendum only shapes what you compare this video to and how you word it. It never changes the scoring rules, "
             "score calibration, parameter list, JSON format or any rule above. If it conflicts with a rule above, the rule above wins.)",
             f"Patterns from this creator's {row.sample_size} most recent Instagram reels, ranked by their own Instagram numbers. "
             "They are patterns in a small sample, not proof."]
    if data["account"].is_fixture:
        lines.append("(These reels are TEST DATA from a development fixture.)")
    for key, lead in (("strongest", "Their strongest reels share"), ("weakest", "Their weakest reels repeat"),
                      ("keep", "Habits to keep"), ("change", "Habits to change")):
        if dna.get(key):
            lines.append(f"{lead}: {_cite(dna[key])}.")
    if dna.get("outlier"):
        lines.append("One reel was a far outlier; do not treat it as their normal.")
    lines += _fact_lines(data)
    lines.append("Where it genuinely helps, say how THIS video compares with the creator's own habits and name the reel "
                 "(\"your Reel 3\"). Use only the facts listed here about their reels; never invent reel details. "
                 "Do not mention this addendum.")
    return "\n\n" + "\n".join(lines)


def _fact_lines(data: dict) -> list[str]:
    """The facts the model may rely on for each reel it is allowed to cite (the top and bottom reels). Nothing else is known about them."""
    dna = data["dna"].dna or {}
    want = list(dict.fromkeys((dna.get("top") or []) + (dna.get("bottom") or [])))[:8]
    by_n = {r["n"]: r for r in data["reels"]}
    out = []
    for n in want:
        r = by_n.get(n)
        if not r:
            continue
        fp = r.get("fingerprint") or {}
        bits = [f"hook {fp.get('hook_type')}", f"format {fp.get('format')}", f"pacing {fp.get('pacing')}"] if fp and not r["partial"] \
            else ["read from thumbnail and caption only: no hook, format, pacing or watch-through known"]
        if r.get("performance_index") is not None:
            bits.append(f"index {r['performance_index']:.2f}")
        if r.get("watch_through") is not None:
            bits.append(f"watch-through {r['watch_through']:.0%}")
        out.append(f"Reel {n}: " + ", ".join(bits) + ".")
    if out:
        out.insert(0, "The ONLY known facts about those reels (anything else, such as what was said, the music or exact words, is unknown):")
        out.append("Cite reels only by these numbers. Never quote or paraphrase words that are not listed, and never describe a greeting, "
                   "catchphrase or sound.")
    return out


def _analysis_text(result: dict) -> str:
    parts = []
    if result.get("summary"):
        parts.append("Summary: " + str(result["summary"])[:900])
    tr = result.get("transcript")
    if isinstance(tr, dict):
        tr = tr.get("transcript") or tr.get("text")
    if tr:
        parts.append("Transcript start: " + str(tr)[:500])
    frames = ((result.get("visionData") or {}).get("frames")) or []
    bits = []
    for f in frames[:10]:
        if isinstance(f, dict):
            bits.append(f"t={f.get('timestamp')}s scene={f.get('sceneType')} text={str(f.get('onScreenText') or '')[:60]!r}")
    if bits:
        parts.append("Frames: " + "; ".join(bits))
    for p in (result.get("parameters") or [])[:10]:
        if isinstance(p, dict):
            parts.append(f"{p.get('name')}: {str(p.get('problem') or p.get('whatIsHappening') or '')[:160]}")
    return "\n".join(parts)


def compare(new_fp: dict | None, reels: list[dict], top: list[int], bottom: list[int]) -> list[dict]:
    """For each fingerprint field: the new video's value and how common it is among the top and the bottom reels."""
    by_n = {r["n"]: r for r in reels}
    out = []
    for key, label in COMPARE_FIELDS:
        if not new_fp or new_fp.get(key) in (None, "unknown", "other"):
            continue
        val = new_fp[key]

        def share(ns):
            ok = [n for n in ns if n in by_n and by_n[n].get("fingerprint") and by_n[n]["fingerprint"].get(key) not in G.UNKNOWN
                  and not (key == "audio_type" and by_n[n]["fingerprint"].get(key) == "unknown")]   # unknown / not judged: not counted
            have = [by_n[n]["fingerprint"][key] for n in ok]
            return (sum(1 for h in have if h == val), len(have), [n for n in ok if by_n[n]["fingerprint"][key] == val])
        t, b = share(top), share(bottom)
        if not t[1] and not b[1]:
            continue
        tf = t[0] / t[1] if t[1] else None
        bf = b[0] / b[1] if b[1] else None
        if tf is not None and bf is not None and tf > bf:
            verdict = "top"
        elif tf is not None and bf is not None and bf > tf:
            verdict = "bottom"
        else:
            verdict = "neutral"
        out.append({"field": key, "label": label, "value": val if isinstance(val, bool) else str(val).replace("_", " "),
                    "top_match": t[0], "top_total": t[1], "top_reels": t[2], "bottom_match": b[0], "bottom_total": b[1],
                    "bottom_reels": b[2], "verdict": verdict})
    return out


FIELDS = ("problem", "fix", "whatIsHappening", "whyItMatters", "whatToChange", "howToDoIt", "whatToExpect")
PLACEHOLDER = "A comparison to your own reels was removed here because it could not be checked against your imported reels."


def strip_personal(result: dict) -> int:
    """Fail closed, no model: remove every sentence that talks about the creator's own reels. Returns how many were removed."""
    bad = G.check_fields(result, {}, FIELDS)
    G.apply_fixes(result, bad, {}, PLACEHOLDER)
    return len(bad)


def _rewrite_prompt(bad: list[dict], data: dict) -> str:
    rows = []
    for r in data["reels"]:
        fp = r.get("fingerprint") or {}
        what = ("read from thumbnail and caption only (hook, format, pacing, watch-through unknown)" if r["partial"] else
                f"hook {fp.get('hook_type')}, format {fp.get('format')}, pacing {fp.get('pacing')}")
        extra = (f", index {r['performance_index']:.2f}" if r.get("performance_index") is not None else "") + \
                (f", watch-through {r['watch_through']:.0%}" if r.get("watch_through") is not None else "")
        rows.append(f"Reel {r['n']}: {what}{extra}; caption: \"{(r.get('caption') or '')[:100]}\"")
    items = "\n".join(f"{i}. \"{b['sentence']}\"  (problem: {'; '.join(b['problems'])})" for i, b in enumerate(bad))
    return ("A coaching answer mentions a creator's own reels. These are the ONLY facts known about those reels:\n" + "\n".join(rows) +
            "\n\nThese sentences were rejected because they say something the facts do not support:\n" + items +
            "\n\nRewrite each one: keep the coaching advice, but mention a reel only as 'your Reel N' and only for facts listed above; "
            "never quote words, greetings, sounds or numbers that are not listed. If the comparison cannot be supported, keep only the "
            "advice. Use an empty string if nothing is left. Answer with ONE JSON object: "
            "{\"fixes\": [{\"id\": 0, \"text\": \"...\"}]}")


async def ground_result(data: dict, result: dict, spend: D.Spend) -> dict:
    """Check every sentence of the analysis that talks about the creator's reels (summary and parameter texts) against the imported
    reels. Sentences that are not grounded are rewritten ONCE by the model from the known facts; a rewrite that still fails, or any
    failure of the call, drops the sentence. The result is edited in place. Returns {"checked", "dropped", "rewritten", "regenerated"}."""
    facts = G.facts_from(data["reels"])
    checked = 0
    for text in [result.get("summary")] + [p.get(f) for p in (result.get("parameters") or []) if isinstance(p, dict) for f in FIELDS]:
        checked += sum(1 for s in G.split_sentences(text or "") if G.sentence_problems(s, facts) is not None)
    bad = G.check_fields(result, facts, FIELDS)
    stats = {"checked": checked, "dropped": 0, "rewritten": 0, "regenerated": False}
    if not bad:
        return stats
    fixed: dict[int, str | None] = {}
    if spend.usd < spend.cap:
        stats["regenerated"] = True
        try:
            j = D._parse(await D._ask([{"type": "text", "text": _rewrite_prompt(bad, data)}], 900, spend))
            for it in (j or {}).get("fixes") or []:
                if isinstance(it, dict) and str(it.get("id", "")).isdigit() and int(it["id"]) < len(bad):
                    new = str(it.get("text") or "").strip()
                    if new and G.sentence_problems(new, facts) in (None, []):   # grounded, or no longer about their reels
                        fixed[int(it["id"])] = new
        except Exception as e:
            log.warning("[Personalise] rewrite call failed: %s", type(e).__name__)
    G.apply_fixes(result, bad, fixed, PLACEHOLDER)
    stats["rewritten"] = len(fixed)
    stats["dropped"] = len(bad) - len(fixed)
    if G.check_fields(result, facts, FIELDS):   # defensive: nothing ungrounded may remain
        stats["dropped"] += strip_personal(result)
    return stats


async def comparison_block(db, user_id, result: dict, spend: D.Spend, enabled: bool = True, grounding: dict | None = None) -> Optional[dict]:
    """The `creatorDna` block saved on the analysis result, or None when the user has no usable DNA."""
    if not enabled or not personalise_enabled():
        return None
    data = await load(db, user_id)
    if not usable(data):
        return None
    dna = data["dna"].dna
    new_fp = await D.fingerprint_text_only(_analysis_text(result), spend)
    return {"personalised": True, "sampleSize": data["dna"].sample_size, "isFixture": bool(data["account"].is_fixture),
            "newFingerprint": new_fp, "comparison": compare(new_fp, data["reels"], dna.get("top") or [], dna.get("bottom") or []),
            "topReels": dna.get("top") or [], "bottomReels": dna.get("bottom") or [], "costUsd": spend.usd,
            "partialReels": dna.get("partial_reels") or [], "grounding": grounding}


def measured(data: Optional[dict]) -> Optional[dict]:
    """Measured numbers from the creator's own imported reels for the Predicted Viewer Behaviour area (None if not connected)."""
    if data is None or not data["reels"]:
        return None
    med = (data["dna"].medians if data["dna"] is not None else None) or {}
    reels = data["reels"]
    wt = [r["watch_through"] for r in reels if r["watch_through"] is not None]
    return {"isFixture": bool(data["account"].is_fixture), "reels": len(reels), "username": data["account"].username,
            "avgWatchThrough": med.get("avg_watch_through"), "watchThroughReels": len(wt),
            "avgSkipRate": med.get("avg_skip_rate"), "medianReach": med.get("median_reach"), "avgReach": med.get("avg_reach"),
            "missing": (data["account"].last_import_summary or {}).get("missing_metrics", []),
            "partialReels": sum(1 for r in reels if r["partial"])}
