"""Built during the Meta Global AI Developer Hackathon (new work).

Dev-only endpoints (DEV_DINO=1 and a logged-in user) for the DINOv2 features. The router is only mounted when the flag is on.

    GET  /dino/status                       service state (off / ok), no GPU wake-up
    POST /dino/curve        video           attention strip: visual change + audio energy + audio change + planner hints
    POST /dino/integrity    before, after [, edl]   render integrity per segment
    POST /dino/embed/{analysis_id}  video   store the analysis' mean embedding (so /nearest has something to match)
    GET  /dino/nearest/{analysis_id}        the user's own reels that look most like that analysis

Honest errors: when the model service is off or failing, the visual parts answer {"available": false, "reason": ...}.
"""
import asyncio
import json
import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.studio.dino import attention, client, frames as fr, integrity, store
from app.studio.dino.client import DinoUnavailable

router = APIRouter(prefix="/dino", tags=["dev-dino"])
MAX_UPLOAD_BYTES = 120 * 1024 * 1024


async def _save(upload: UploadFile, tmp: str, name: str) -> str:
    path = os.path.join(tmp, name)
    size = 0
    with open(path, "wb") as f:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="video too large for the dev endpoint (120 MB)")
            f.write(chunk)
    try:
        await asyncio.to_thread(fr.probe, path)
    except fr.FrameError as e:
        raise HTTPException(status_code=422, detail=f"not a readable video: {e}")
    return path


@router.get("/status")
async def status(user: User = Depends(get_current_user)):
    return {"backend": client.backend_choice(), "health": await asyncio.to_thread(client.health)}


@router.post("/curve")
async def curve(video: UploadFile = File(...), user: User = Depends(get_current_user)):
    with tempfile.TemporaryDirectory(prefix="dino_curve_") as tmp:
        path = await _save(video, tmp, "video")
        usage: dict = {}
        strip = await asyncio.to_thread(attention.attention_strip, path, None, usage)
    strip["usage"] = usage
    return strip


@router.post("/integrity")
async def integrity_check(before: UploadFile = File(...), after: UploadFile = File(...), edl: str = Form(""),
                          user: User = Depends(get_current_user)):
    edl_obj = None
    if edl.strip():
        try:
            edl_obj = json.loads(edl)
        except ValueError:
            raise HTTPException(status_code=422, detail="edl is not valid JSON")
    with tempfile.TemporaryDirectory(prefix="dino_integrity_") as tmp:
        p_before = await _save(before, tmp, "before")
        p_after = await _save(after, tmp, "after")
        usage: dict = {}
        try:
            res = await asyncio.to_thread(integrity.render_integrity, p_before, p_after, edl_obj, None, None, integrity.INTEGRITY_THRESHOLD, usage)
        except DinoUnavailable as e:
            return {"label": "render integrity (estimate)", "available": False, "reason": str(e), "note": integrity.NOTE}
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    res["usage"] = usage
    return res


@router.post("/embed/{analysis_id}")
async def embed_analysis(analysis_id: uuid.UUID, video: UploadFile = File(...), user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from app.models.prysm_analysis import PrysmAnalysis
    row = (await db.execute(select(PrysmAnalysis.user_id).where(PrysmAnalysis.id == analysis_id))).first()
    if row is None or row[0] != user.id:
        raise HTTPException(status_code=404, detail="analysis not found")
    with tempfile.TemporaryDirectory(prefix="dino_embed_") as tmp:
        path = await _save(video, tmp, "video")
        try:
            res = await asyncio.to_thread(store.embed_reel, path)
        except DinoUnavailable as e:
            return {"available": False, "reason": str(e)}
    await store.save_analysis_embedding(db, analysis_id, res["mean"], res["frame_count"])
    return {"available": True, "analysis_id": str(analysis_id), "frame_count": res["frame_count"], "usage": res["usage"]}


@router.get("/nearest/{analysis_id}")
async def nearest(analysis_id: uuid.UUID, k: int = 5, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from app.models.prysm_analysis import PrysmAnalysis
    row = (await db.execute(select(PrysmAnalysis.user_id).where(PrysmAnalysis.id == analysis_id))).first()
    if row is None or row[0] != user.id:
        raise HTTPException(status_code=404, detail="analysis not found")
    vec = await store.load_analysis_embedding(db, analysis_id)
    if vec is None:
        return {"label": store.LABEL, "available": False, "reels": [],
                "reason": "no visual embedding is stored for this analysis (POST /dino/embed/{analysis_id} stores one)"}
    return await store.nearest_reels(db, vec, user.id, k=max(1, min(int(k), 20)))
