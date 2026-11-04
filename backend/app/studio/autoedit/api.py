"""Built during the Meta Global AI Developer Hackathon (new work).

HTTP API of Auto-edit, mounted at /prysm/auto-edit only when AUTO_EDIT_ENABLED is on (otherwise these routes do not exist).
Login required on every route; an edit belongs to the user who owns the analysis. One running edit per user, a 90 s limit, an hourly
start limit, at most 5 revisions per edit. The run itself is the blocking orchestrator in a worker thread.
"""
import asyncio
import copy
import json
import logging
import os
import tempfile
import time
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.core.cache import redis_client
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.prysm_analysis import PrysmAnalysis
from app.models.user import User
from app.studio.autoedit import config, ffx, orchestrator as O, revise
from app.studio.autoedit.edl import EDL, GROUPS, RISK
from app.studio.autoedit import sfxlib as _sfx
from app.studio.autoedit.llm import CostCapExceeded, CostMeter, EditLLM
from app.studio.autoedit.presets import PRESETS
from app.studio.autoedit.storage import edit_prefix, get_storage, upload_key

log = logging.getLogger(__name__)
router = APIRouter()

_LIVE: dict[str, dict] = {}          # analysis_id -> the state of an edit running in this process
_TASKS: set = set()                  # keeps background tasks alive
LOCK_TTL_S = 1800
DOWNLOADS = {"mp4": ("final.mp4", "video/mp4", "mp4"), "srt": ("captions.srt", "text/plain", "srt"), "ass": ("captions.ass", "text/plain", "ass"),
             "edl": ("edl.json", "application/json", "json"), "cover": ("cover.jpg", "image/jpeg", "jpg"), "clean": ("clean.mp4", "video/mp4", "mp4")}


# ------------------------------------------------------------------ helpers

def _uid(s) -> str:
    return str(s)


def retain_upload(user_id: str, analysis_id: str, video_path: str) -> bool:
    """Called by the analysis worker (before its temp folder is deleted): keep the creator's original in storage for Auto-edit.
    Only videos within the 90 s limit are kept. The file is copied, never modified. Returns True when stored."""
    try:
        info = ffx.probe(video_path)
        if info["duration"] > config.MAX_SOURCE_SECONDS + 0.5:
            return False
        get_storage().put_file(upload_key(user_id, analysis_id), video_path, "video/mp4")
        return True
    except Exception as e:
        log.warning("[AutoEdit] could not keep the upload for %s: %s", analysis_id, e)
        return False


async def _load_analysis(db: AsyncSession, analysis_id: str, user: User) -> PrysmAnalysis:
    try:
        aid = _uuid.UUID(analysis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid analysis id.")
    rec = (await db.execute(select(PrysmAnalysis).where(PrysmAnalysis.id == aid, PrysmAnalysis.user_id == user.id))).scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return rec


def _eligibility(rec: PrysmAnalysis, user: User) -> dict:
    if (rec.source_type or "upload") != "upload":
        return {"eligible": False, "reason": "Auto-edit is only available for videos you uploaded, not pasted links."}
    store = get_storage()
    if not store.exists(upload_key(_uid(user.id), _uid(rec.id))):
        return {"eligible": False, "reason": "The original upload is no longer stored (it was too long, older than the retention period, or deleted)."}
    return {"eligible": True, "reason": ""}


def _load_state(aid: str) -> dict | None:
    if aid in _LIVE:
        return _LIVE[aid]
    store = get_storage()
    key = O.state_key(aid)
    try:
        return json.loads(store.get_bytes(key)) if store.exists(key) else None
    except Exception:
        return None


OP_LABELS = {
    "trim": lambda p: f"Trim the video to {p['start']:.1f}-{p['end']:.1f} s",
    "cut_segment": lambda p: f"Cut {p['start']:.1f}-{p['end']:.1f} s (dead air / stumble)",
    "remove_filler": lambda p: f"Remove the filler “{p.get('word', '')}” at {p['start']:.1f} s",
    "cold_open": lambda p: f"Cold open: the {p['start']:.1f}-{p['end']:.1f} s moment first, then the video",
    "text_overlay": lambda p: f"{'Hook text' if p.get('role') == 'hook' else 'Text'}: “{p['text']}”",
    "caption_burn": lambda p: "Burned-in captions with word-by-word highlight",
    "zoom_punch": lambda p: f"Zoom punch at {p['at']:.1f} s (×{p['scale']:.2f})",
    "loudnorm": lambda p: f"Loudness to {p['target_lufs']:g} LUFS + light denoise",
    "sfx_hit": lambda p: f"Sound accent ({p['effect']}, {p['kind']} moment) at {p['at']:.1f} s",
    "music_duck": lambda p: f"Music lowered {abs(p['duck_db']):g} dB under the voice",
}


def public_state(st: dict) -> dict:
    """The state as the UI needs it: raw state plus a flat `changes` list with plain-English labels and verdicts."""
    out = copy.deepcopy(st)
    groups = out.get("groups") or {}
    overrides = out.get("overrides") or {}
    changes = []
    for o in (out.get("edl") or {}).get("ops", []):
        g = groups.get(o["group"], {})
        try:
            label = OP_LABELS[o["type"]](o["params"])
        except Exception:
            label = o["type"]
        verdict = g.get("status", "pending")
        op_rev = (out.get("op_reverts") or {}).get(o["id"])
        changes.append({"id": o["id"], "type": o["type"], "group": o["group"], "label": label, "reason": o.get("reason", ""),
                        "params": {k: v for k, v in (o.get("params") or {}).items() if k in ("start", "end", "at", "role", "text", "anchor", "word", "scale", "duration", "target_lufs", "effect", "kind")},
                        "target_parameter": o.get("target_parameter", ""), "source_step_id": o.get("source_step_id"), "enabled": o["enabled"],
                        "verdict": "reverted" if op_rev else verdict, "verdict_reasons": [op_rev] if op_rev else g.get("reasons", []), "user_override": o["id"] in overrides, "finding": o.get("finding", ""),
                        "effect_options": (_sfx.MOMENT_EFFECTS.get(o["params"].get("kind"), []) if o["type"] == "sfx_hit" else []),
                        "risk": g.get("risk") or RISK.get(o["group"], "risky")})
    out["changes"] = changes
    out["running"] = out.get("status") in ("queued", "running")
    out["presets"] = list(PRESETS)
    from app.studio.autoedit import packs as _packs
    out["packs"] = {k: {"label": v["label"], "headline": v["headline"], "caption": v["caption"], "accent": v["accent"], "animation": v["animation"], "for": v["for"]}
                    for k, v in _packs.PACKS.items()}
    out["intensities"] = list(_packs.INTENSITIES)
    ov = out.get("style_overrides") or {}
    out["style"] = {"pack": ov.get("pack"), "font_headline": ov.get("font_headline"), "font_caption": ov.get("font_caption"), "animation": ov.get("animation"),
                    "intensity": ov.get("intensity", "balanced"), "sfx": ov.get("sfx", True),
                    "pack_source": "your choice" if "pack" in (ov.get("user_set") or []) else "suggested"}
    out["revisions_left"] = max(0, config.MAX_REVISIONS - len(out.get("revisions") or []))
    out["trace"] = (out.get("trace") or [])[-80:]
    return out


async def _acquire_user(user_id: str) -> bool:
    return bool(await redis_client.set(f"autoedit:run:{user_id}", "1", nx=True, ex=LOCK_TTL_S))


async def _release_user(user_id: str) -> None:
    try:
        await redis_client.delete(f"autoedit:run:{user_id}")
    except Exception:
        pass


async def _rate_limit(user_id: str) -> None:
    key = f"autoedit:starts:{user_id}:{int(time.time() // 3600)}"
    try:
        n = await redis_client.incr(key)
        await redis_client.expire(key, 3700)
    except Exception:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable — please try again in a moment.")
    if n > config.START_LIMIT_PER_HOUR:
        raise HTTPException(status_code=429, detail="Too many edits this hour. Please try again later.")


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)


async def _record_spend(usd: float) -> None:
    try:
        from app.core.config import settings
        if settings.META_MODE:
            from app.studio import safety
            await safety.record_spend(redis_client, float(usd))
    except Exception as e:
        log.warning("[AutoEdit] could not record spend: %s", e)


async def _job(kind: str, st: dict, analysis: dict, user_id: str, **kw) -> None:
    aid = st["analysis_id"]
    storage = get_storage()
    meter = CostMeter.from_json(st.get("cost"))
    spent_before = meter.total
    _LIVE[aid] = st
    runner = O.Runner(st, analysis, storage, meter=meter, publish=lambda s: _LIVE.__setitem__(aid, s))
    try:
        if kind == "run":
            await asyncio.to_thread(runner.run)
        elif kind == "clean":
            await asyncio.to_thread(runner.clean_cut)
        else:
            await asyncio.to_thread(runner.rerender, kw.get("summary", "changes"))
    except Exception as e:  # the runner reports its own failures; this is the last line of defence
        log.exception("[AutoEdit] job crashed")
        st["status"], st["error"] = "failed", f"{type(e).__name__}: {str(e)[:200]}"
        try:
            storage.put_bytes(O.state_key(aid), json.dumps(st).encode(), "application/json")
        except Exception:
            pass
    finally:
        await _record_spend(meter.total - spent_before)
        await _release_user(user_id)
        _LIVE.pop(aid, None)


async def _owned_state(analysis_id: str, user: User, db: AsyncSession) -> tuple[PrysmAnalysis, dict]:
    rec = await _load_analysis(db, analysis_id, user)
    st = _load_state(analysis_id)
    if not st or st.get("user_id") != _uid(user.id):
        raise HTTPException(status_code=404, detail="No auto-edit has been started for this analysis.")
    return rec, st


def _need_idle(st: dict) -> None:
    if st.get("status") in ("queued", "running") or (st.get("clean_cut") or {}).get("running"):
        raise HTTPException(status_code=409, detail="An edit is still running; wait for it to finish.")


# ------------------------------------------------------------------ routes

class StartBody(BaseModel):
    preset: str = "clean"


class ToggleBody(BaseModel):
    op_id: str | None = None
    op_ids: list[str] | None = None      # a whole group at once: one re-render for all of them
    enabled: bool


class StyleBody(BaseModel):
    pack: str | None = None
    font_headline: str | None = None
    font_caption: str | None = None
    animation: str | None = None
    intensity: str | None = None
    sfx: bool | None = None


KEEP_ON_PRESET = ("user_set", "pack", "font_headline", "font_caption", "animation", "intensity", "sfx", "emphasis", "caption_weight", "accent")


class SwapBody(BaseModel):
    op_id: str
    effect: str


class PresetBody(BaseModel):
    preset: str


class ReviseBody(BaseModel):
    text: str = Field(min_length=2, max_length=300)


@router.get("/available")
async def available(current_user: User = Depends(get_current_user)):
    return {"enabled": True, "presets": list(PRESETS), "max_video_seconds": config.MAX_SOURCE_SECONDS, "max_revisions": config.MAX_REVISIONS,
            "label": "AI-estimated, not measured viewership"}


@router.get("/{analysis_id}/status")
async def status(analysis_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rec = await _load_analysis(db, analysis_id, current_user)
    st = _load_state(analysis_id)
    if st and st.get("user_id") == _uid(current_user.id):
        return {"eligible": True, "reason": "", "state": public_state(st)}
    return {**_eligibility(rec, current_user), "state": None}


@router.post("/{analysis_id}/start")
async def start(analysis_id: str, body: StartBody, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rec = await _load_analysis(db, analysis_id, current_user)
    el = _eligibility(rec, current_user)
    if not el["eligible"]:
        raise HTTPException(status_code=400, detail=el["reason"])
    if body.preset not in PRESETS:
        raise HTTPException(status_code=400, detail="Unknown style preset.")
    existing = _load_state(analysis_id)
    if existing and existing.get("status") in ("queued", "running"):
        raise HTTPException(status_code=409, detail="An edit is already running for this analysis.")
    uid = _uid(current_user.id)
    from app.core.config import settings
    if settings.META_MODE:
        from app.studio import safety
        from app.studio.safety import limits_from_settings
        try:
            await safety.check_daily_cap(redis_client, limits_from_settings(settings))
        except safety.LimitExceeded as limit:
            raise HTTPException(status_code=limit.status_code, detail=limit.message)
    await _rate_limit(uid)
    if not await _acquire_user(uid):
        raise HTTPException(status_code=409, detail="You already have an edit running. Wait for it to finish before starting another.")
    try:
        analysis = dict(rec.raw_result or {})
        analysis.setdefault("niche", rec.niche)
        st = O.new_state(analysis_id, uid, body.preset)
        _LIVE[analysis_id] = st
        st["trace"].append({"t": O.now(), "kind": "phase", "msg": "queued"})
        _spawn(_job("run", st, analysis, uid))
        # opportunistic retention sweep (cheap): files older than AUTO_EDIT_RETENTION_DAYS go
        asyncio.get_running_loop().run_in_executor(None, sweep_expired)
    except Exception:
        await _release_user(uid)
        raise
    return public_state(st)


@router.post("/{analysis_id}/clean-cut")
async def clean_cut(analysis_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """A second render of the same edit with ONLY the safe pause and loudness fixes (no captions, zoom, labels, hook text or sound accents),
    for finishing in another editor. No model call; the main edit and its files are not touched."""
    rec, st = await _owned_state(analysis_id, current_user, db)
    _need_idle(st)
    if not st.get("edl") or not st.get("final"):
        raise HTTPException(status_code=409, detail="Make the edit first.")
    uid = _uid(current_user.id)
    if not await _acquire_user(uid):
        raise HTTPException(status_code=409, detail="You already have an edit running.")
    st["clean_cut"] = {"running": True}
    _spawn(_job("clean", st, dict(rec.raw_result or {}), uid))
    return public_state(st)


@router.post("/{analysis_id}/toggle")
async def toggle(analysis_id: str, body: ToggleBody, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rec, st = await _owned_state(analysis_id, current_user, db)
    _need_idle(st)
    ops = (st.get("edl") or {}).get("ops", [])
    ids = list(dict.fromkeys(body.op_ids or ([body.op_id] if body.op_id else [])))
    if not ids or len(ids) > 40:
        raise HTTPException(status_code=422, detail="Name the change (op_id) or a list of changes (op_ids).")
    picked = [next((o for o in ops if o["id"] == i), None) for i in ids]
    if any(o is None for o in picked):
        raise HTTPException(status_code=404, detail="Unknown change.")
    todo = [o for o in picked if o["enabled"] != body.enabled]
    if not todo:
        return public_state(st)
    uid = _uid(current_user.id)
    if not await _acquire_user(uid):
        raise HTTPException(status_code=409, detail="You already have an edit running.")
    for o in todo:
        o["enabled"] = body.enabled
        st.setdefault("overrides", {})[o["id"]] = body.enabled
    st["status"] = "running"
    what = todo[0]["type"].replace("_", " ") if len(todo) == 1 else f"{len(todo)} changes"
    _spawn(_job("rerender", st, dict(rec.raw_result or {}), uid, summary=f"{'on' if body.enabled else 'off'}: {what}"))
    return public_state(st)


@router.post("/{analysis_id}/preset")
async def set_preset(analysis_id: str, body: PresetBody, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rec, st = await _owned_state(analysis_id, current_user, db)
    _need_idle(st)
    if body.preset not in PRESETS:
        raise HTTPException(status_code=400, detail="Unknown style preset.")
    uid = _uid(current_user.id)
    if not await _acquire_user(uid):
        raise HTTPException(status_code=409, detail="You already have an edit running.")
    keep = {k: v for k, v in (st.get("style_overrides") or {}).items() if k in KEEP_ON_PRESET}   # the pack, fonts and switches survive a preset change
    st["preset"], st["style_overrides"], st["status"] = body.preset, keep, "running"
    _spawn(_job("rerender", st, dict(rec.raw_result or {}), uid, summary=f"style preset {body.preset}"))
    return public_state(st)


@router.post("/{analysis_id}/swap")
async def swap_effect(analysis_id: str, body: SwapBody, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Swap the sound effect of one sound accent for another effect that suits its moment."""
    from app.studio.autoedit import sfxlib
    rec, st = await _owned_state(analysis_id, current_user, db)
    _need_idle(st)
    op = next((o for o in (st.get("edl") or {}).get("ops", []) if o["id"] == body.op_id and o["type"] == "sfx_hit"), None)
    if not op:
        raise HTTPException(status_code=404, detail="Unknown sound accent.")
    if body.effect not in sfxlib.MOMENT_EFFECTS.get(op["params"]["kind"], []):
        raise HTTPException(status_code=400, detail="That effect does not suit this moment.")
    uid = _uid(current_user.id)
    if not await _acquire_user(uid):
        raise HTTPException(status_code=409, detail="You already have an edit running.")
    op["params"]["effect"] = body.effect
    st["status"] = "running"
    _spawn(_job("rerender", st, dict(rec.raw_result or {}), uid, summary=f"sound {body.op_id}: {body.effect}"))
    return public_state(st)


@router.get("/fonts")
async def search_fonts(q: str = "", category: str | None = None, script: str | None = None, current_user: User = Depends(get_current_user)):
    """The Google Fonts picker: search by name, filter by category (display, sans, serif, rounded, handwriting, mono, devanagari)."""
    from app.studio.autoedit import fonts
    if category and category not in fonts.CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown category.")
    return {"categories": list(fonts.CATEGORIES), "fonts": fonts.search(q[:60], category, script, limit=60)}


@router.post("/{analysis_id}/style")
async def set_style(analysis_id: str, body: StyleBody, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Change the style pack, either font, the caption animation, the effect intensity or the sound-effects switch with one call."""
    from app.studio.autoedit import fonts, packs
    rec, st = await _owned_state(analysis_id, current_user, db)
    _need_idle(st)
    fams = fonts.families()
    ov = dict(st.get("style_overrides") or {})
    if body.pack is not None:
        if body.pack not in packs.PACKS:
            raise HTTPException(status_code=400, detail="Unknown style pack.")
        p = packs.pack_style(body.pack)
        ov.update({"pack": body.pack, "font_headline": p["font_headline"], "font_caption": p["font_caption"], "animation": p["animation"],
                   "caption_weight": p["caption_weight"], "accent": p["accent"]})
        ov["user_set"] = sorted(set(ov.get("user_set") or []) | {"pack", "font_headline", "font_caption"})
    for key in ("font_headline", "font_caption"):
        v = getattr(body, key)
        if v is not None:
            if v not in fams:
                raise HTTPException(status_code=400, detail=f"{v!r} is not a Google Fonts family.")
            ov[key] = v
            ov["user_set"] = sorted(set(ov.get("user_set") or []) | {key})
    if body.animation is not None:
        if body.animation not in packs.ANIMATIONS:
            raise HTTPException(status_code=400, detail="Unknown caption animation.")
        ov["animation"] = body.animation
    if body.intensity is not None:
        if body.intensity not in packs.INTENSITIES:
            raise HTTPException(status_code=400, detail="Intensity must be subtle, balanced or punchy.")
        ov["intensity"] = body.intensity
    if body.sfx is not None:
        ov["sfx"] = body.sfx
    uid = _uid(current_user.id)
    if not await _acquire_user(uid):
        raise HTTPException(status_code=409, detail="You already have an edit running.")
    st["style_overrides"], st["status"] = ov, "running"
    _spawn(_job("rerender", st, dict(rec.raw_result or {}), uid, summary="style: " + ", ".join(f"{k} {v}" for k, v in body.model_dump(exclude_none=True).items())))
    return public_state(st)


@router.post("/{analysis_id}/revise")
async def revise_edit(analysis_id: str, body: ReviseBody, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rec, st = await _owned_state(analysis_id, current_user, db)
    _need_idle(st)
    if len(st.get("revisions") or []) >= config.MAX_REVISIONS:
        raise HTTPException(status_code=429, detail=f"You have used all {config.MAX_REVISIONS} revisions for this edit.")
    if st.get("status") == "failed" or not st.get("edl"):
        raise HTTPException(status_code=409, detail="This edit has no plan to revise.")
    uid = _uid(current_user.id)
    if not await _acquire_user(uid):
        raise HTTPException(status_code=409, detail="You already have an edit running.")
    try:
        meter = CostMeter.from_json(st.get("cost"))
        try:
            applied, summary = await asyncio.to_thread(_prepare_revision, st, body.text, meter)
        except CostCapExceeded as e:
            raise HTTPException(status_code=402, detail=str(e))
        if applied.refused:
            raise HTTPException(status_code=422, detail=f"That change was not applied: {applied.refused}")
        st["edl"] = applied.edl.model_dump()
        st["style_overrides"] = applied.style_overrides
        if applied.preset:
            st["preset"] = applied.preset
        st["revisions"].append({"text": body.text.strip(), "summary": summary, "at": O.now()})
        st["cost"] = meter.to_json()
        st["status"] = "running"
    except Exception:
        await _release_user(uid)
        raise
    _spawn(_job("rerender", st, dict(rec.raw_result or {}), uid, summary="; ".join(summary) or "revision"))
    return public_state(st)


def _prepare_revision(st: dict, text: str, meter: CostMeter):
    """Blocking: parse the request (rules first, Spark if nothing matched), apply it and validate. Needs the stored inputs."""
    store = get_storage()
    from app.studio.autoedit.inputs import Inputs
    inp = Inputs.from_json(json.loads(store.get_bytes(edit_prefix(st["analysis_id"]) + "inputs.json")))
    edl = EDL.model_validate(st["edl"])
    edl.style = O.resolve_style(st["preset"], st["style_overrides"])
    diff = revise.parse_revision(text, edl, inp)
    if not diff.resolved:
        diff = revise.revise_with_spark(text, edl, inp, EditLLM(meter))
    if not diff.resolved:
        raise HTTPException(status_code=422, detail="I could not turn that into an edit I can make. Try something like “bigger captions”, “less zoom” or “keep the intro”.")
    applied = revise.apply_diff(edl, diff, inp, st["style_overrides"])
    return applied, (applied.changes or diff.summary)


@router.get("/{analysis_id}/download/{kind}")
async def download(analysis_id: str, kind: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rec = await _load_analysis(db, analysis_id, current_user)
    store = get_storage()
    if kind == "original":
        key, ctype, ext = upload_key(_uid(current_user.id), _uid(rec.id)), "video/mp4", "mp4"
        name = f"prysm-original-{analysis_id[:8]}.mp4"
    elif kind in DOWNLOADS:
        st = _load_state(analysis_id)
        if not st or st.get("user_id") != _uid(current_user.id):
            raise HTTPException(status_code=404, detail="No auto-edit for this analysis.")
        fname, ctype, ext = DOWNLOADS[kind]
        key = edit_prefix(analysis_id) + fname
        name = f"prysm-{'clean-cut' if kind == 'clean' else 'edit'}-{analysis_id[:8]}.{ext}"
    else:
        raise HTTPException(status_code=404, detail="Unknown download.")
    if not store.exists(key):
        raise HTTPException(status_code=404, detail="That file is not available (for example captions were not part of this edit).")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}")
    tmp.close()
    await asyncio.to_thread(store.get_file, key, tmp.name)
    return FileResponse(tmp.name, media_type=ctype, filename=name, background=BackgroundTask(lambda p=tmp.name: os.path.exists(p) and os.unlink(p)))


@router.delete("/{analysis_id}")
async def delete(analysis_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rec = await _load_analysis(db, analysis_id, current_user)
    st = _load_state(analysis_id)
    if st and st.get("user_id") == _uid(current_user.id):
        _need_idle(st)
    store = get_storage()
    n = store.delete_prefix(edit_prefix(analysis_id))
    n += store.delete_prefix(f"uploads/{_uid(current_user.id)}/{_uid(rec.id)}/")
    _LIVE.pop(analysis_id, None)
    return {"deleted": True, "files": n}


@router.get("/{analysis_id}/cost")
async def cost(analysis_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """The cost of the auto-edit for the founder cost panel (same visibility rule as the analysis cost: founder, or local STUDIO_EXPOSE_COST=1)."""
    from app.studio.cost_access import cost_visible
    if not cost_visible(current_user.email):
        raise HTTPException(status_code=404, detail="Not found")
    await _load_analysis(db, analysis_id, current_user)
    st = _load_state(analysis_id)
    if not st or st.get("user_id") != _uid(current_user.id):
        raise HTTPException(status_code=404, detail="Not found")
    return {"cost": st.get("cost"), "timing": st.get("timing"), "status": st.get("status"),
            "ops": len((st.get("edl") or {}).get("ops", [])), "revisions": len(st.get("revisions") or [])}


def sweep_expired() -> int:
    """Delete stored uploads and edit files older than AUTO_EDIT_RETENTION_DAYS."""
    try:
        store, days = get_storage(), config.retention_days()
        n = store.sweep("edits/", days) + store.sweep("uploads/", days)
        if n:
            log.info("[AutoEdit] retention sweep removed %d files older than %g days", n, days)
        return n
    except Exception as e:
        log.warning("[AutoEdit] retention sweep failed: %s", e)
        return 0
