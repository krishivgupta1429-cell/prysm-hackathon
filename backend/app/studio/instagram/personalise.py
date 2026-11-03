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
from app.studio.instagram.models import CreatorDna, IgAccount, IgReel

log = logging.getLogger(__name__)
COMPARE_FIELDS = (("hook_type", "Hook"), ("format", "Format"), ("pacing", "Pacing"), ("on_screen_text", "On-screen text"),
                  ("audio_type", "Audio"))


def personalise_enabled() -> bool:
    """COACH_PERSONALISE: "0" turns it off, "1" (or unset) leaves it on for users who have a DNA."""
    return (config.get_env("COACH_PERSONALISE") or "1") != "0"


async def load(db, user_id) -> Optional[dict]:
    """{"account", "dna", "reels"(numbered newest first, as dicts)} for a user with a DNA, else None."""
    acct = (await db.execute(select(IgAccount).where(IgAccount.user_id == user_id))).scalar_one_or_none()
    if acct is None:
        return None
    dna = (await db.execute(select(CreatorDna).where(CreatorDna.account_id == acct.id))).scalar_one_or_none()
    rows = (await db.execute(select(IgReel).where(IgReel.account_id == acct.id))).scalars().all()
    rows = sorted(rows, key=lambda r: r.posted_at.isoformat() if r.posted_at else "", reverse=True)
    reels = [{"n": i, "id": str(r.id), "media_id": r.media_id, "permalink": r.permalink, "posted_at": r.posted_at,
              "caption": r.caption, "duration_s": r.duration_s, "performance_index": r.performance_index,
              "performance_basis": r.performance_basis, "watch_through": r.watch_through, "fingerprint": r.fingerprint,
              "frames_used": r.frames_used, "note": r.note, "like_count": r.like_count, "comments_count": r.comments_count}
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
    lines.append("Where it genuinely helps, say how THIS video compares with the creator's own habits and name the reel "
                 "(\"your Reel 3\"). Use only the facts listed here about their reels; never invent reel details. "
                 "Do not mention this addendum.")
    return "\n\n" + "\n".join(lines)


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
            have = [by_n[n]["fingerprint"][key] for n in ns if n in by_n and by_n[n].get("fingerprint")]
            return (sum(1 for h in have if h == val), len(have), [n for n in ns if n in by_n and by_n[n].get("fingerprint") and by_n[n]["fingerprint"][key] == val])
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


async def comparison_block(db, user_id, result: dict, spend: D.Spend, enabled: bool = True) -> Optional[dict]:
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
            "topReels": dna.get("top") or [], "bottomReels": dna.get("bottom") or [], "costUsd": spend.usd}


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
            "missing": (data["account"].last_import_summary or {}).get("missing_metrics", [])}
