"""Built during the Meta Global AI Developer Hackathon (new work).

"Did my fix work?": link a Prysm analysis to the reel the creator posted after it, then compare that reel's real Instagram numbers
with the creator's OWN usual (the median of their other imported reels): watch-through, reach, saves and shares per reach, skip rate.
Honest by construction: it says "better / worse / about the same than your usual", never "your fix worked"; Instagram numbers can
lag up to 48 hours; one reel is not proof; and it always says how many other reels the usual is based on.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import delete, select, text

from app.studio.instagram import metrics as M
from app.studio.instagram.models import IgFixLink

log = logging.getLogger(__name__)
LAG_HOURS = 48
SAME_PP = 3.0            # percentage points (watch-through, skip rate) within which we say "about the same"
SAME_RATIO = 0.10        # relative difference (saves / shares per reach) within which we say "about the same"
MAX_DAYS_AFTER = 21


# ------------------------------------------------------------------ advice and matching (pure)

def advice_items(analysis: dict) -> list[dict]:
    """The advice a creator can tick as applied: the top priorities plus the single fix of each low-scoring parameter."""
    seen, out = set(), []

    def add(t):
        t = " ".join(str(t or "").split())
        if t and t.lower() not in seen and len(out) < 8:
            seen.add(t.lower())
            out.append({"id": f"a{len(out)}", "text": t[:220]})
    for t in analysis.get("top_priorities") or []:
        add(t)
    for p in analysis.get("parameters") or []:
        if isinstance(p, dict) and not p.get("advisory") and isinstance(p.get("score"), (int, float)) and p["score"] < 70:
            add(p.get("fix"))
    return out


def suggest_matches(created_at: Optional[datetime], duration_s: Optional[float], reels: list[dict]) -> list[dict]:
    """Reels posted after the analysis, best guess first: close in time and (when both lengths are known) about the same length."""
    if created_at is None:
        return []
    out = []
    for r in reels:
        posted = r.get("posted_at")
        if not posted:
            continue
        days = (posted - created_at).total_seconds() / 86400
        if days < -0.25 or days > MAX_DAYS_AFTER:
            continue
        score, why, length_ok = max(days, 0.0), [f"posted {max(days, 0):.0f} day{'s' if round(max(days, 0)) != 1 else ''} after the analysis"], False
        d = r.get("duration_s")
        if duration_s and d:
            rel = abs(d - duration_s) / duration_s
            if rel <= 0.15:
                score, length_ok = score - 5, True
                why.append(f"length matches ({round(d)} s vs {round(duration_s)} s)")
            elif rel <= 0.3:
                score -= 2
                why.append(f"length is close ({round(d)} s vs {round(duration_s)} s)")
            else:
                score += 3
                why.append(f"length differs ({round(d)} s vs {round(duration_s)} s)")
        out.append({"n": r["n"], "media_id": r.get("media_id"), "score": round(score, 2), "reasons": why, "_ok": length_ok or days <= 3})
    out.sort(key=lambda x: x["score"])
    for i, x in enumerate(out):
        x["likely"] = bool(i == 0 and x.pop("_ok"))
        x.pop("_ok", None)
    return out


# ------------------------------------------------------------------ the numbers (pure)

def _median(xs):
    from statistics import median
    xs = [x for x in xs if x is not None]
    return median(xs) if xs else None


def _ins(r: dict, k: str) -> Optional[float]:
    return (r.get("insights") or {}).get(k)


def _ratio(r: dict, num: str) -> Optional[float]:
    n, reach = _ins(r, num), _ins(r, "reach")
    return None if n is None or not reach else n / reach * 1000.0


def _row(key, label, value, usual, kind, n_base, *, text_fn, higher_is_better=True, pp_scale=1.0) -> dict:
    row: dict[str, Any] = {"key": key, "label": label, "value": value, "usual": usual, "direction": "none", "valueText": None, "usualText": None, "note": None}
    if value is None:
        row["note"] = "no data yet"
        return row
    if usual is None or n_base == 0:
        row["valueText"], row["note"] = text_fn(value), "no usual to compare with yet"
        return row
    row["valueText"], row["usualText"] = text_fn(value), text_fn(usual)
    if kind == "pp":
        diff = (value - usual) * pp_scale            # percentage points
        same = abs(diff) < SAME_PP
    else:
        diff = (value - usual) / usual if usual else (0.0 if value == 0 else 1.0)
        same = abs(diff) < SAME_RATIO
    row["delta"] = round(diff, 2) if kind == "pp" else round(diff * 100)
    row["direction"] = "same" if same else ("better" if (diff > 0) == higher_is_better else "worse")
    return row


def before_after(reel: dict, others: list[dict], now: Optional[datetime] = None) -> dict:
    """The linked reel against the median of the creator's other imported reels. Reels in `others` do not include `reel`."""
    now = now or datetime.now(timezone.utc)
    n = len(others)
    med = _median      # a real 0 (no saves) counts: it is the creator's usual, unlike in the account median used for the index

    def many(f):
        return [v for v in (f(o) for o in others) if v is not None]
    rows = []
    # reach (or views when reach is missing) against the usual, in plain words
    basis = "reach" if _ins(reel, "reach") is not None and many(lambda o: _ins(o, "reach")) else "views"
    v, u = _ins(reel, basis), med(many(lambda o: _ins(o, basis)))
    rv = {"key": "reach_vs_usual", "label": "Reach" if basis == "reach" else "Views", "value": v, "usual": u, "direction": "none",
          "valueText": None, "usualText": None, "note": None}
    if v is None:
        rv["note"] = "no data yet"
    elif not u:
        rv["valueText"], rv["note"] = f"{round(v):,}", "no usual to compare with yet"
    else:
        plain = M.plain_vs_usual(v / u, basis)
        rv.update(valueText=f"{round(v):,}", usualText=f"{round(u):,}", plainText=plain["label"], delta=plain["pct"],
                  direction={"above": "better", "below": "worse", "same": "same"}[plain["direction"]])
    rows.append(rv)
    rows.append(_row("watch_through", "Watch-through", reel.get("watch_through"), med(many(lambda o: o.get("watch_through"))), "pp", n,
                     text_fn=lambda x: f"{x * 100:.0f}%", pp_scale=100.0))
    rows.append(_row("saves_per_reach", "Saves per 1,000 reached", _ratio(reel, "saved"), med(many(lambda o: _ratio(o, "saved"))), "ratio", n,
                     text_fn=lambda x: f"{x:.1f}"))
    rows.append(_row("shares_per_reach", "Shares per 1,000 reached", _ratio(reel, "shares"), med(many(lambda o: _ratio(o, "shares"))), "ratio", n,
                     text_fn=lambda x: f"{x:.1f}"))
    rows.append(_row("skip_rate", "Skip rate (lower is better)", _ins(reel, "reels_skip_rate"), med(many(lambda o: _ins(o, "reels_skip_rate"))), "pp", n,
                     text_fn=lambda x: f"{x:.0f}%", higher_is_better=False))
    posted = reel.get("posted_at")
    lagging = bool(posted and (now - posted) < timedelta(hours=LAG_HOURS))
    counted = [r for r in rows if r["direction"] != "none"]
    notes = ["Instagram numbers can take up to 48 hours to settle, so check again later." if lagging else
             "Instagram numbers can lag by up to 48 hours.",
             "One reel is not proof: a number can be above or below your usual for reasons that have nothing to do with the advice.",
             f"“Your usual” is the median of your other {n} imported reel{'s' if n != 1 else ''}." + (" With so few, treat it as a rough guide." if n < 5 else "")]
    return {"basedOn": n, "rows": rows, "lagging": lagging,
            "better": sum(1 for r in counted if r["direction"] == "better"), "worse": sum(1 for r in counted if r["direction"] == "worse"),
            "same": sum(1 for r in counted if r["direction"] == "same"), "compared": len(counted), "notes": notes}


# ------------------------------------------------------------------ database

async def list_analyses(db, user_id, limit: int = 20) -> list[dict]:
    """The user's saved Prysm analyses (newest first) with the advice they can tick. Fails soft when the table is missing."""
    try:
        async with db.begin_nested():
            res = await db.execute(text(
                "SELECT id, created_at, overall_score, summary, top_priorities, parameters, edited_video_key, "
                "(raw_result::jsonb -> 'energyCurve' ->> 'duration') AS dur, intent "
                "FROM prysm_analyses WHERE user_id = :u ORDER BY created_at DESC LIMIT :n"), {"u": str(user_id), "n": limit})
            rows = res.mappings().all()
    except Exception as e:
        log.info("[CloseLoop] analyses unavailable: %s", type(e).__name__)
        return []
    out = []
    for r in rows:
        try:
            dur = float(r["dur"]) if r["dur"] else None
        except ValueError:
            dur = None
        out.append({"id": str(r["id"]), "created_at": r["created_at"], "score": r["overall_score"], "summary": (r["summary"] or "")[:220],
                    "duration_s": dur, "has_edited_video": bool(r["edited_video_key"]), "intent": r["intent"],
                    "advice": advice_items({"top_priorities": r["top_priorities"], "parameters": r["parameters"]})})
    return out


def _snap(a: dict) -> dict:
    return {"createdAt": a["created_at"].isoformat() if a.get("created_at") else None, "score": a.get("score"), "summary": a.get("summary"),
            "duration": a.get("duration_s"), "hasEditedVideo": a.get("has_edited_video")}


async def save_link(db, user_id, analysis: dict, media_id: str, applied: list[str], used_auto_edit: bool) -> IgFixLink:
    """One link per analysis: linking again replaces the earlier choice."""
    await db.execute(delete(IgFixLink).where(IgFixLink.user_id == user_id, IgFixLink.analysis_id == analysis["id"]))
    link = IgFixLink(user_id=user_id, analysis_id=analysis["id"], media_id=media_id, applied=[a[:220] for a in applied][:12],
                     used_auto_edit=bool(used_auto_edit), analysis_snapshot=_snap(analysis))
    db.add(link)
    await db.commit()
    return link


async def delete_link(db, user_id, link_id) -> bool:
    res = await db.execute(delete(IgFixLink).where(IgFixLink.user_id == user_id, IgFixLink.id == link_id))
    await db.commit()
    return bool(res.rowcount)


async def links_with_results(db, user_id, reels: list[dict]) -> list[dict]:
    rows = (await db.execute(select(IgFixLink).where(IgFixLink.user_id == user_id).order_by(IgFixLink.created_at.desc()))).scalars().all()
    by_media = {r["media_id"]: r for r in reels}
    out = []
    for l in rows:
        r = by_media.get(l.media_id)
        out.append({"id": str(l.id), "analysisId": str(l.analysis_id), "mediaId": l.media_id, "applied": l.applied or [], "usedAutoEdit": bool(l.used_auto_edit),
                    "analysis": l.analysis_snapshot or {}, "createdAt": l.created_at.isoformat() if l.created_at else None,
                    "reel": None if r is None else {"n": r["n"], "postedAt": r["posted_at"].isoformat() if r.get("posted_at") else None,
                                                    "duration_s": r.get("duration_s"), "permalink": r.get("permalink"), "caption": (r.get("caption") or "")[:120]},
                    "result": None if r is None else before_after(r, [o for o in reels if o["media_id"] != l.media_id])})
    return out
