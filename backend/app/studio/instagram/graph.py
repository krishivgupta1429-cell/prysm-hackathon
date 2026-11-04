"""Built during the Meta Global AI Developer Hackathon (new work).

Reads from graph.instagram.com for the connected account only: the N most recent reels, their insights (empty data stays
None, never 0), and the temporary media download. Fixture mode swaps in fixtures.FixtureSource with the same interface.
"""
import logging
from typing import Optional

import httpx

from app.studio.instagram.metrics import METRICS

log = logging.getLogger(__name__)
GRAPH = "https://graph.instagram.com"
MEDIA_FIELDS = "id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count"
MEDIA_FIELDS_SMALL = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count"
RATE_LIMIT_CODES = {4, 17, 32, 613}
MAX_PAGES = 12            # 12 pages x 25 = 300 media looked at while searching for N reels


class RateLimited(Exception):
    """Instagram's rate limit was hit; what was fetched so far is kept."""


class TokenInvalid(Exception):
    """The token was rejected (expired or revoked): the creator has to connect again."""


def _error(r: httpx.Response) -> dict:
    try:
        return (r.json() or {}).get("error") or {}
    except ValueError:
        return {}


def _raise_for(r: httpx.Response):
    err = _error(r)
    if r.status_code == 429 or err.get("code") in RATE_LIMIT_CODES:
        raise RateLimited(err.get("message") or "rate limited")
    if r.status_code in (401, 403) or err.get("code") in (190, 102):
        raise TokenInvalid(err.get("message") or "token rejected")


class InstagramSource:
    """Real Instagram. `client` is injectable for tests (httpx.MockTransport)."""
    is_fixture = False

    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self._token = token
        self._own = client is None
        self._c = client or httpx.AsyncClient(timeout=30)

    async def aclose(self):
        if self._own:
            await self._c.aclose()

    async def list_reels(self, n: int) -> list[dict]:
        """The n most recent reels, newest first. Looks through up to MAX_PAGES pages because feed posts and stories are mixed in."""
        fields = MEDIA_FIELDS
        reels: list[dict] = []
        url, params = f"{GRAPH}/me/media", {"fields": fields, "limit": 25, "access_token": self._token}
        for _ in range(MAX_PAGES):
            r = await self._c.get(url, params=params)
            if r.status_code == 400 and fields == MEDIA_FIELDS and not _error(r).get("code") in RATE_LIMIT_CODES:
                log.info("[Instagram] media list refused the full field list; retrying with fewer fields")
                fields = MEDIA_FIELDS_SMALL
                params = {**params, "fields": fields}
                continue
            _raise_for(r)
            if r.status_code != 200:
                raise RuntimeError(f"media list failed (HTTP {r.status_code}, code {_error(r).get('code')})")
            j = r.json()
            for m in j.get("data", []):
                kind = m.get("media_product_type")
                is_reel = kind == "REELS" or (kind is None and m.get("media_type") == "VIDEO")
                if is_reel:
                    reels.append(m)
                    if len(reels) >= n:
                        return reels
            nxt = (j.get("paging") or {}).get("next")
            if not nxt:
                break
            url, params = nxt, None   # the cursor URL already carries every parameter
        return reels

    async def profile_picture(self) -> Optional[str]:
        """The link to the profile picture (one small call at import time); None when Instagram does not return one."""
        try:
            r = await self._c.get(f"{GRAPH}/me", params={"fields": "profile_picture_url", "access_token": self._token})
            return (r.json() or {}).get("profile_picture_url") if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    async def count_reels(self, stop_at: int = 30) -> tuple[int, bool]:
        """(reels found, complete). Cheap: pages of 100 with only the type fields, stops as soon as `stop_at` reels are seen
        (usually one call). complete=False means "at least this many"."""
        url, params = f"{GRAPH}/me/media", {"fields": "id,media_type,media_product_type", "limit": 100, "access_token": self._token}
        found = 0
        for _ in range(5):
            r = await self._c.get(url, params=params)
            _raise_for(r)
            if r.status_code != 200:
                raise RuntimeError(f"media count failed (HTTP {r.status_code}, code {_error(r).get('code')})")
            j = r.json()
            for m in j.get("data", []):
                kind = m.get("media_product_type")
                if kind == "REELS" or (kind is None and m.get("media_type") == "VIDEO"):
                    found += 1
            if found >= stop_at:
                return found, False
            nxt = (j.get("paging") or {}).get("next")
            if not nxt:
                return found, True
            url, params = nxt, None
        return found, False

    async def insights(self, media_id: str) -> dict[str, Optional[float]]:
        """{metric: value or None}. All metrics in one call; if Instagram refuses the combination, one call per metric.
        An empty `data` for a metric (or a refused metric) is None, never 0."""
        out: dict[str, Optional[float]] = {m: None for m in METRICS}
        url = f"{GRAPH}/{media_id}/insights"
        r = await self._c.get(url, params={"metric": ",".join(METRICS), "access_token": self._token})
        _raise_for(r)
        if r.status_code == 200:
            self._fill(out, r.json())
            return out
        for m in METRICS:
            r = await self._c.get(url, params={"metric": m, "access_token": self._token})
            _raise_for(r)
            if r.status_code == 200:
                self._fill(out, r.json())
        return out

    @staticmethod
    def _fill(out: dict, j: dict) -> None:
        for item in j.get("data", []) or []:
            name = item.get("name")
            vals = item.get("values") or []
            v = vals[0].get("value") if vals else (item.get("total_value") or {}).get("value")
            if name in out and isinstance(v, (int, float)) and not isinstance(v, bool):
                out[name] = float(v)

    async def download(self, reel: dict, dest_path: str) -> bool:
        """Stream media_url to dest_path (a temp file the caller deletes). False if the reel has no media_url
        (licensed audio) or the download fails."""
        url = reel.get("media_url")
        if not url:
            return False
        try:
            async with self._c.stream("GET", url, follow_redirects=True) as r:
                if r.status_code != 200:
                    return False
                with open(dest_path, "wb") as f:
                    async for chunk in r.aiter_bytes(1 << 16):
                        f.write(chunk)
            return True
        except httpx.HTTPError as e:
            log.info("[Instagram] media download failed: %s", type(e).__name__)
            return False

    async def thumbnail(self, reel: dict) -> Optional[bytes]:
        url = reel.get("thumbnail_url")
        if not url:
            return None
        try:
            r = await self._c.get(url, follow_redirects=True)
            return r.content if r.status_code == 200 else None
        except httpx.HTTPError:
            return None
