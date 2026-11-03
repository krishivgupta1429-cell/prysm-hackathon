"""Built during the Meta Global AI Developer Hackathon (new work).

HTTP side of the Instagram connect + Creator DNA feature, mounted at /instagram. The three baseline routes in
routers/auth.py (/auth/instagram, /auth/instagram/callback, DELETE /auth/instagram) call the handlers at the bottom, so the
redirect URI registered with Meta (…/auth/instagram/callback) keeps working.
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import redis_client
from app.core.database import AsyncSessionLocal, get_db
from app.core.security import decode_token, get_current_user
from app.models.user import User
from app.studio.instagram import fixtures, importer, oauth, personalise
from app.studio.instagram import metrics as M
from app.studio.instagram.graph import InstagramSource

log = logging.getLogger(__name__)
router = APIRouter()
_tasks: set[asyncio.Task] = set()


class ImportBody(BaseModel):
    count: int = importer.DEFAULT_COUNT


def _host(request: Request) -> str | None:
    return request.client.host if request.client else None


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


async def _source_for(db, user: User, request: Request):
    acct = await oauth.get_account(db, user.id)
    if acct is None:
        raise HTTPException(409, "Connect Instagram first.")
    if acct.is_fixture:
        if not oauth.fixture_enabled(_host(request)):
            raise HTTPException(403, "Fixture mode is not available here.")
        return acct, fixtures.FixtureSource()
    token = await oauth.ensure_fresh_token(db, user)
    if not token:
        raise HTTPException(409, "Instagram is not connected. Please connect again.")
    return acct, InstagramSource(token)


# ------------------------------------------------------------------ JSON API

@router.get("/status")
async def status(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    acct = await oauth.get_account(db, user.id)
    fixture = oauth.fixture_enabled(_host(request))
    return {
        "configured": oauth.credentials_configured(), "fixtureAvailable": fixture, "scopes": list(oauth.SCOPES),
        "connected": acct is not None,
        "account": None if acct is None else {
            "username": acct.username, "isFixture": bool(acct.is_fixture), "accountType": acct.account_type,
            "tokenExpiresAt": _iso(acct.token_expires_at), "lastImportAt": _iso(acct.last_import_at),
            "lastImportRequested": acct.last_import_requested, "lastImport": acct.last_import_summary},
        "limits": {"min": importer.MIN_COUNT, "max": importer.MAX_COUNT, "default": importer.DEFAULT_COUNT},
    }


@router.post("/connect")
async def connect(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """The address to send the browser to. Instagram's consent page normally; in fixture mode (dev, localhost only) the account is
    created at once and the address is the Creator DNA page."""
    if oauth.fixture_enabled(_host(request)):
        await oauth.save_connection(db, user.id, token=oauth.FIXTURE_TOKEN, ig_user_id=fixtures.FIXTURE_IG_ID,
                                    username=fixtures.FIXTURE_USERNAME, account_type="FIXTURE", expires_in=60 * 24 * 3600,
                                    scopes=",".join(oauth.SCOPES), is_fixture=True)
        return {"url": f"{oauth.frontend_url()}/creator-dna?instagram=connected", "fixture": True}
    if oauth.fixture_requested():
        raise HTTPException(403, "Fixture mode is only available on localhost in development.")
    if not oauth.credentials_configured():
        raise HTTPException(503, "Instagram is not configured on this server (app id, app secret and redirect URI).")
    state = await oauth.create_state(redis_client, str(user.id))
    return {"url": oauth.authorize_url(state), "fixture": False}


@router.post("/import")
async def start_import(body: ImportBody, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.core.config import settings
    from app.studio import safety
    from app.studio.progress import Progress
    count = importer.clamp_count(body.count)
    acct, source = await _source_for(db, user, request)
    try:
        await safety.check_daily_cap(redis_client, safety.limits_from_settings(settings))
    except safety.LimitExceeded as e:
        raise HTTPException(429, str(e))
    job_id, why = await importer.start_import(redis_client, str(user.id), count)
    if not job_id:
        raise HTTPException(409, why)

    async def spend_hook(usd: float):
        await safety.record_spend(redis_client, usd)

    progress = Progress(job_id)

    async def run():
        try:
            await importer.run_import(redis=redis_client, session_factory=AsyncSessionLocal, user_id=user.id, count=count,
                                      job_id=job_id, source=source, progress=progress, record_spend=spend_hook)
        finally:
            await progress.flush()
            await source.aclose()

    t = asyncio.create_task(run())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return {"jobId": job_id, "count": count}


@router.get("/import/{job_id}")
async def import_status(job_id: str, user: User = Depends(get_current_user)):
    job = await importer.job_status(redis_client, job_id)
    if not job or job.get("user_id") != str(user.id):
        raise HTTPException(404, "Unknown import.")
    job.pop("user_id", None)
    return job


@router.get("/dna")
async def creator_dna(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await personalise.load(db, user.id)
    if data is None:
        return {"connected": False}
    acct, row = data["account"], data["dna"]
    reels = [{**{k: v for k, v in r.items() if k not in ("id", "posted_at")}, "id": r["id"], "postedAt": _iso(r["posted_at"])}
             for r in data["reels"]]
    return {
        "connected": True, "isFixture": bool(acct.is_fixture), "username": acct.username,
        "lastImport": acct.last_import_summary, "lastImportAt": _iso(acct.last_import_at),
        "dna": None if row is None else {"summary": row.summary, "data": row.dna, "sampleSize": row.sample_size,
                                         "metricsAvailable": row.metrics_available, "medians": row.medians,
                                         "model": row.model, "costUsd": row.cost_usd, "createdAt": _iso(row.created_at)},
        "reels": reels, "metricLabels": M.METRIC_LABEL,
    }


@router.get("/measured")
async def measured(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Measured numbers from the user's own imported reels, for the results page (connected: false when nothing is imported)."""
    m = personalise.measured(await personalise.load(db, user.id))
    return {"connected": m is not None, **(m or {})}


@router.delete("/disconnect")
async def disconnect(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    counts = await oauth.disconnect(db, user.id)
    return {"detail": "Instagram disconnected. The token and every imported reel, insight and DNA were deleted.", "deleted": counts}


# ------------------------------------------------------------------ handlers used by the baseline routes in routers/auth.py

async def start_redirect(token: str):
    """GET /auth/instagram?token=... (baseline route kept for compatibility; the new UI uses POST /instagram/connect)."""
    if not oauth.credentials_configured():
        raise HTTPException(503, "Instagram integration not configured")
    payload = decode_token(token)
    if payload.get("type") != "access" or not payload.get("sub"):
        raise HTTPException(401, "Invalid token")
    state = await oauth.create_state(redis_client, payload["sub"])
    return RedirectResponse(oauth.authorize_url(state))


async def callback_redirect(db, code: Optional[str], state: Optional[str], error: Optional[str]):
    front = oauth.frontend_url()
    if error or not code:
        await oauth.consume_state(redis_client, state)   # a denied consent still uses up its state
        return RedirectResponse(f"{front}/creator-dna?instagram=error")
    try:
        await oauth.complete_connection(db, redis_client, code=code, state=state)
    except oauth.OAuthError as e:
        log.warning("[Instagram] connect failed: %s", e)
        return RedirectResponse(f"{front}/creator-dna?instagram=error")
    except Exception as e:
        log.warning("[Instagram] connect failed: %s", type(e).__name__)
        return RedirectResponse(f"{front}/creator-dna?instagram=error")
    return RedirectResponse(f"{front}/creator-dna?instagram=connected")
