"""Built during the Meta Global AI Developer Hackathon (new work).

Instagram API with Instagram Login (graph.instagram.com): the connect flow, long-lived token and refresh, the token
bookkeeping and Disconnect. Same pattern as the baseline (random state in Redis -> user id; token in
users.instagram_access_token), with two scopes instead of five and the state checked BEFORE the code is exchanged.
"""
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select, update

from app.studio import config
from app.studio.instagram.models import CreatorDna, IgAccount, IgReel, IgReelInsight

log = logging.getLogger(__name__)

SCOPES = ("instagram_business_basic", "instagram_business_manage_insights")
AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH = "https://graph.instagram.com"
STATE_PREFIX = "ig_oauth_state:"        # same key pattern as the baseline
STATE_TTL_S = 600
REFRESH_WHEN_DAYS_LEFT = 14
FIXTURE_TOKEN = "FIXTURE-NOT-A-REAL-TOKEN"


class OAuthError(Exception):
    """A connect step failed. The message is safe to log (never contains a token or a secret)."""


# ---------------------------------------------------------------- settings

def _settings():
    from app.core.config import settings
    return settings


def fixture_requested() -> bool:
    return (config.get_env("INSTAGRAM_FIXTURE") or "") == "1"


def is_local_request(host: str | None) -> bool:
    return (host or "") in ("127.0.0.1", "::1", "localhost", "testclient")


def fixture_enabled(client_host: str | None) -> bool:
    """Fixture mode is on only when INSTAGRAM_FIXTURE=1, the app is not in production, the frontend is on localhost and the
    request itself comes from the same machine. Anything else: refused."""
    if not fixture_requested():
        return False
    s = _settings()
    if str(getattr(s, "APP_ENV", "")).lower() in ("production", "prod", "staging"):
        return False
    front = (getattr(s, "INSTAGRAM_FRONTEND_URL", "") or getattr(s, "FRONTEND_URL", "") or "")
    if not any(h in front for h in ("://localhost", "://127.0.0.1")):
        return False
    return is_local_request(client_host)


def credentials_configured() -> bool:
    s = _settings()
    return bool(s.INSTAGRAM_APP_ID and s.INSTAGRAM_APP_SECRET and s.INSTAGRAM_REDIRECT_URI
                and "YOUR-BACKEND-URL" not in s.INSTAGRAM_REDIRECT_URI)


def frontend_url() -> str:
    s = _settings()
    return (s.INSTAGRAM_FRONTEND_URL or s.FRONTEND_URL or "").rstrip("/")


# ---------------------------------------------------------------- CSRF state (Redis)

async def create_state(redis, user_id: str) -> str:
    state = secrets.token_urlsafe(32)
    await redis.setex(f"{STATE_PREFIX}{state}", STATE_TTL_S, str(user_id))
    return state


async def consume_state(redis, state: str | None) -> str | None:
    """The user id the state was issued to, or None (unknown / expired / already used). Single use."""
    if not state:
        return None
    key = f"{STATE_PREFIX}{state}"
    try:
        uid = await redis.getdel(key)
    except Exception:  # very old Redis without GETDEL
        uid = await redis.get(key)
        if uid:
            await redis.delete(key)
    return str(uid) if uid else None


def authorize_url(state: str) -> str:
    s = _settings()
    params = urlencode({
        "client_id": s.INSTAGRAM_APP_ID,
        "redirect_uri": s.INSTAGRAM_REDIRECT_URI,
        "scope": ",".join(SCOPES),
        "response_type": "code",
        "state": state,
    })
    return f"{AUTHORIZE_URL}?{params}"


# ---------------------------------------------------------------- Graph calls

def _clean_code(code: str) -> str:
    return code.split("#", 1)[0].strip()   # Instagram may append "#_" to the code


def _unwrap(j: dict) -> dict:
    """The code exchange answers {"data":[{...}]} in current docs and a flat object in older ones."""
    if isinstance(j.get("data"), list) and j["data"]:
        return j["data"][0]
    return j


async def exchange_code(code: str, client: httpx.AsyncClient | None = None) -> dict:
    s = _settings()
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        r = await client.post(TOKEN_URL, data={
            "client_id": s.INSTAGRAM_APP_ID, "client_secret": s.INSTAGRAM_APP_SECRET, "grant_type": "authorization_code",
            "redirect_uri": s.INSTAGRAM_REDIRECT_URI, "code": _clean_code(code)})
    finally:
        if own:
            await client.aclose()
    if r.status_code != 200:
        raise OAuthError(f"code exchange failed (HTTP {r.status_code})")
    d = _unwrap(r.json())
    if not d.get("access_token") or d.get("user_id") is None:
        raise OAuthError("code exchange returned no token")
    return {"access_token": d["access_token"], "user_id": str(d["user_id"]), "permissions": d.get("permissions")}


async def to_long_lived(short_token: str, client: httpx.AsyncClient | None = None) -> tuple[str, int]:
    """(long-lived token, seconds until it expires). A failure raises: we never silently keep the 1-hour token."""
    s = _settings()
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        r = await client.get(f"{GRAPH}/access_token", params={
            "grant_type": "ig_exchange_token", "client_secret": s.INSTAGRAM_APP_SECRET, "access_token": short_token})
    finally:
        if own:
            await client.aclose()
    if r.status_code != 200 or not r.json().get("access_token"):
        raise OAuthError(f"long-lived token exchange failed (HTTP {r.status_code})")
    j = r.json()
    return j["access_token"], int(j.get("expires_in") or 60 * 24 * 3600)


async def refresh_long_lived(token: str, client: httpx.AsyncClient | None = None) -> tuple[str, int]:
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        r = await client.get(f"{GRAPH}/refresh_access_token", params={"grant_type": "ig_refresh_token", "access_token": token})
    finally:
        if own:
            await client.aclose()
    if r.status_code != 200 or not r.json().get("access_token"):
        raise OAuthError(f"token refresh failed (HTTP {r.status_code})")
    j = r.json()
    return j["access_token"], int(j.get("expires_in") or 60 * 24 * 3600)


async def fetch_profile(token: str, client: httpx.AsyncClient | None = None) -> dict:
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        r = await client.get(f"{GRAPH}/me", params={"fields": "id,user_id,username,account_type,profile_picture_url", "access_token": token})
    finally:
        if own:
            await client.aclose()
    if r.status_code != 200:
        raise OAuthError(f"profile read failed (HTTP {r.status_code})")
    return r.json()


# ---------------------------------------------------------------- storage

def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_account(db, user_id) -> IgAccount | None:
    return (await db.execute(select(IgAccount).where(IgAccount.user_id == user_id))).scalar_one_or_none()


async def save_connection(db, user_id, *, token: str, ig_user_id: str, username: str | None, account_type: str | None,
                          expires_in: int, scopes: str, is_fixture: bool = False, profile_picture_url: str | None = None) -> IgAccount:
    """Store the token where the baseline stores it (users columns) and the bookkeeping in ig_account.
    A different Instagram account than before replaces the old one: its derived data is deleted first."""
    from app.models.user import User
    acct = await get_account(db, user_id)
    if acct is not None and (acct.ig_user_id != ig_user_id or acct.is_fixture != is_fixture):
        await _delete_derived(db, acct)
        await db.delete(acct)
        await db.flush()
        acct = None
    await db.execute(update(User).where(User.id == user_id).values(
        instagram_access_token=token, instagram_user_id=ig_user_id, instagram_username=username))
    expires = _now() + timedelta(seconds=expires_in)
    if acct is None:
        acct = IgAccount(user_id=user_id, ig_user_id=ig_user_id, username=username, account_type=account_type,
                         is_fixture=is_fixture, scopes=scopes, token_expires_at=expires, token_refreshed_at=_now(),
                         profile_picture_url=profile_picture_url)
        db.add(acct)
    else:
        acct.username, acct.account_type, acct.scopes = username, account_type, scopes
        acct.profile_picture_url = profile_picture_url or acct.profile_picture_url
        acct.token_expires_at, acct.token_refreshed_at = expires, _now()
    await db.commit()
    return acct


async def complete_connection(db, redis, *, code: str | None, state: str | None, client: httpx.AsyncClient | None = None) -> str:
    """The callback. State first (single use), then code -> short -> long-lived token -> profile -> store.
    Returns the user id. Raises OAuthError with a safe message on any failure."""
    user_id = await consume_state(redis, state)
    if not user_id:
        raise OAuthError("unknown or expired state")
    if not code:
        raise OAuthError("no code")
    got = await exchange_code(code, client)
    long_token, expires_in = await to_long_lived(got["access_token"], client)
    prof = await fetch_profile(long_token, client)
    await save_connection(db, user_id, token=long_token, ig_user_id=str(prof.get("user_id") or prof.get("id") or got["user_id"]),
                          username=prof.get("username"), account_type=prof.get("account_type"),
                          expires_in=expires_in, scopes=",".join(SCOPES), profile_picture_url=prof.get("profile_picture_url"))
    return user_id


async def ensure_fresh_token(db, user) -> str | None:
    """The user's token, refreshed first when it has fewer than REFRESH_WHEN_DAYS_LEFT days left (and is at least a day old).
    None if no token. A failed refresh keeps the old token (it may still work) and is logged without the token."""
    token = getattr(user, "instagram_access_token", None)
    acct = await get_account(db, user.id)
    if not token or acct is None or acct.is_fixture:
        return token
    await _encrypt_if_plain(db, user.id)
    left = (acct.token_expires_at - _now()) if acct.token_expires_at else None
    age = (_now() - acct.token_refreshed_at) if acct.token_refreshed_at else timedelta(days=99)
    if left is not None and left < timedelta(days=REFRESH_WHEN_DAYS_LEFT) and left > timedelta(0) and age > timedelta(hours=24):
        try:
            new, exp = await refresh_long_lived(token)
            from app.models.user import User
            await db.execute(update(User).where(User.id == user.id).values(instagram_access_token=new))
            acct.token_expires_at, acct.token_refreshed_at = _now() + timedelta(seconds=exp), _now()
            await db.commit()
            return new
        except OAuthError as e:
            log.warning("[Instagram] token refresh failed: %s", e)
    return token


async def _encrypt_if_plain(db, user_id) -> None:
    """A token saved before encryption existed is re-saved encrypted the first time it is used (the migration does the same in bulk).
    Does nothing without TOKEN_ENCRYPTION_KEY. Never raises: the connection keeps working either way."""
    from sqlalchemy import Text, cast
    from app.models.user import User
    from app.studio.instagram import tokencrypt
    if not tokencrypt.key_configured():
        return
    try:
        raw = (await db.execute(select(cast(User.__table__.c.instagram_access_token, Text)).where(User.id == user_id))).scalar()
        if raw and not tokencrypt.is_encrypted(raw):
            await db.execute(update(User).where(User.id == user_id).values(instagram_access_token=raw))   # the column type encrypts
            await db.commit()
    except Exception as e:
        log.warning("[Instagram] could not encrypt the stored token yet: %s", type(e).__name__)


async def _delete_derived(db, acct: IgAccount) -> dict:
    reel_ids = [r for (r,) in (await db.execute(select(IgReel.id).where(IgReel.account_id == acct.id))).all()]
    counts = {"reels": len(reel_ids)}
    if reel_ids:
        res = await db.execute(delete(IgReelInsight).where(IgReelInsight.reel_id.in_(reel_ids)))
        counts["insights"] = res.rowcount or 0
    await db.execute(delete(CreatorDna).where(CreatorDna.account_id == acct.id))
    await db.execute(delete(IgReel).where(IgReel.account_id == acct.id))
    return counts


async def _scrub_saved_results(db, user_id) -> int:
    """The "How this compares to your last N reels" block saved on the user's earlier analyses is derived from their reels:
    remove it. Fails soft (no prysm_analyses table in a unit-test database)."""
    from sqlalchemy import text
    try:
        async with db.begin_nested():
            res = await db.execute(text(
                "UPDATE prysm_analyses SET raw_result = (raw_result::jsonb - 'creatorDna')::json "
                "WHERE user_id = :u AND (raw_result::jsonb ->> 'creatorDna') IS NOT NULL"), {"u": str(user_id)})
            return res.rowcount or 0
    except Exception as e:
        log.info("[Instagram] saved-result scrub skipped: %s", type(e).__name__)
        return 0


async def disconnect(db, user_id) -> dict:
    """Delete the token and ALL derived data (reels, insights, DNA, the account row, the comparison block on saved analyses)."""
    from app.models.user import User
    acct = await get_account(db, user_id)
    counts = {}
    if acct is not None:
        counts = await _delete_derived(db, acct)
        await db.delete(acct)
        counts["saved_results_scrubbed"] = await _scrub_saved_results(db, user_id)
    await db.execute(update(User).where(User.id == user_id).values(
        instagram_access_token=None, instagram_user_id=None, instagram_username=None))
    await db.commit()
    return counts
