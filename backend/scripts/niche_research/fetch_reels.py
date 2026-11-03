"""Bulk-fetch Instagram reels via Playwright (browser automation), one niche at a time.

Reads a niche → creators CSV and produces a reels CSV ready for analyse.py.

Usage:
    python -m scripts.niche_research.fetch_reels --map new_creators_batch5.csv --output reels_v2_batch5.csv
    python -m scripts.niche_research.fetch_reels --map new_creators_batch5.csv --niche dance --output out.csv
    python -m scripts.niche_research.fetch_reels --map new_creators_batch5.csv --dry-run

niche_creators CSV format (one row per niche):
    niche,creator1,creator2,...
    dance,nayanikabhayana,vartikajha07
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_reels")

# ── Constants ─────────────────────────────────────────────────────────────────

_SLEEP_BETWEEN_CREATORS = 20   # seconds — let Instagram breathe
_SCROLLS_PER_PROFILE   = 5    # 5 scrolls = ~72 posts, enough for 40 reels
_SCROLL_PAUSE          = 3.0  # seconds to wait after each scroll for posts to load
_PAGE_LOAD_TIMEOUT     = 40_000  # ms

# Built-in fallback session cookies (user 37805258434).
# Rotate by passing a fresh Netscape-format file via --cookies.
_COOKIES = [
    {"name": "sessionid",  "value": "37805258434%3AgI5iXtEB0wE59b%3A26%3AAYjqWDN3s5ZRSwXmzr3O3qW6Uf7DHsjmWMdWNLEozA",
     "domain": ".instagram.com", "path": "/"},
    {"name": "csrftoken",  "value": "hJg8o9C58Ue6zPfu79rJv8AiNAhfURL5", "domain": ".instagram.com", "path": "/"},
    {"name": "ds_user_id", "value": "37805258434", "domain": ".instagram.com", "path": "/"},
    {"name": "datr",       "value": "RhbHaLuQpQdcTD9zqzs62iZX", "domain": ".instagram.com", "path": "/"},
    {"name": "ig_did",     "value": "26151BD2-829A-42F9-B5FF-A4471613F489", "domain": ".instagram.com", "path": "/"},
    {"name": "mid",        "value": "aOy2DwAEAAGcvMGPezdS-VLFGGwA", "domain": ".instagram.com", "path": "/"},
]


# ── Session management ────────────────────────────────────────────────────────

class SessionExpiredError(RuntimeError):
    """Raised when Instagram signals the session is invalid, rate-limited, or
    has hit a checkpoint.  Callers must stop the run immediately and exit
    non-zero — do not silently continue."""


def _load_cookies_file(path: Path) -> List[Dict[str, Any]]:
    """Parse a Netscape HTTP cookie file and return Playwright cookie dicts.

    Netscape format per line (tab-separated, 7 fields):
        domain  httponly-flag  path  secure-flag  expiry  name  value
    Lines starting with '#' and blank lines are ignored.
    """
    cookies: List[Dict[str, Any]] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _flag, path_, _secure, _expiry, name, value = parts[:7]
            cookies.append({"name": name, "value": value, "domain": domain, "path": path_})
    if not cookies:
        raise ValueError(f"No valid cookie entries found in {path}")
    return cookies


def _session_signal(data: dict, url: str) -> Optional[str]:
    """Return an error string if the JSON body signals a dead session, else None."""
    if data.get("checkpoint_required"):
        return f"checkpoint_required in response from {url}"
    if data.get("require_login"):
        return f"require_login in response from {url}"
    err = data.get("error") or {}
    if isinstance(err, dict) and (err.get("require_login") or err.get("checkpoint_required")):
        return f"session signal in error object from {url}"
    return None


# ── Playwright browser setup ──────────────────────────────────────────────────

async def _make_context(p, cookies: List[Dict[str, Any]]):
    """Create a Playwright browser context with Instagram session cookies."""
    browser = await p.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    )
    await context.add_cookies(cookies)
    return browser, context


# ── Per-creator fetch (async) ─────────────────────────────────────────────────

async def _fetch_creator_reels_async(username: str, per_creator: int, page) -> List[Dict[str, Any]]:
    """Navigate to @username's profile and collect up to per_creator reels.

    Raises SessionExpiredError if Instagram signals the session is invalid.
    Only keeps reels whose uploader matches username (skips injected suggestions).
    """
    import json as _json

    reels: List[Dict[str, Any]] = []
    seen_codes: Set[str] = set()
    session_error: List[Optional[str]] = [None]  # mutable flag readable from closure
    fallback_count: List[int] = [0]  # tracks how many reels used video_view_count fallback

    async def on_response(response):
        # Session health: HTTP-level signals
        if response.status in (401, 429):
            session_error[0] = (
                f"HTTP {response.status} from {response.url} — "
                f"{'session expired' if response.status == 401 else 'rate-limited'}"
            )
            return

        # Instagram moved timeline from /graphql/query to /api/graphql — accept both
        if "graphql/query" not in response.url and "api/graphql" not in response.url:
            return
        if response.status != 200:
            return

        try:
            text = await response.text()
            # Session health: JSON-level signals
            try:
                probe = _json.loads(text)
                sig = _session_signal(probe, response.url)
                if sig:
                    session_error[0] = sig
                    return
            except _json.JSONDecodeError:
                pass

            if "user_timeline_graphql_connection" not in text:
                return
            data = _json.loads(text)
            edges = data["data"]["xdt_api__v1__feed__user_timeline_graphql_connection"]["edges"]
            for e in edges:
                n = e["node"]
                code = n.get("code")
                if not code or code in seen_codes:
                    continue
                if not n.get("video_versions"):
                    continue
                # Uploader guard: skip reels injected from other creators
                actual_user = (n.get("user") or {}).get("username") or ""
                if actual_user.lower() != username.lower():
                    continue
                seen_codes.add(code)
                # view_count is absent for some accounts (Instagram returns it in
                # video_view_count instead). Fall back and track when this happens.
                raw_vc = n.get("view_count")
                if not raw_vc and raw_vc != 0:
                    raw_vc = n.get("video_view_count")
                    if raw_vc:
                        fallback_count[0] += 1
                reels.append({
                    "url":            f"https://www.instagram.com/reel/{code}/",
                    "view_count":     raw_vc or "",
                    "like_count":     n.get("like_count") or "",
                    "comment_count":  n.get("comment_count") or "",
                    "uploader":       actual_user or username,
                    "duration_sec":   "",
                    "follower_count": (n.get("user") or {}).get("follower_count") or "",
                })
        except Exception:
            pass

    page.on("response", on_response)

    try:
        log.info("  → playwright: @%s (want %d reels)", username, per_creator)
        await page.goto(
            f"https://www.instagram.com/{username}/",
            wait_until="networkidle",
            timeout=_PAGE_LOAD_TIMEOUT,
        )

        # Session health: URL-redirect check.
        # Instagram 302-redirects invalid sessions to instagram.com/ (homepage),
        # not always to /accounts/login/ — so check that we actually landed on
        # the target profile rather than checking for a specific destination.
        current_url = page.url
        if "/accounts/login/" in current_url or "/challenge/" in current_url:
            raise SessionExpiredError(
                f"@{username}: redirected to {current_url!r} — "
                "session is invalid or Instagram issued a checkpoint"
            )
        if username.lower() not in current_url.lower():
            raise SessionExpiredError(
                f"@{username}: navigation did not land on profile — "
                f"ended up at {current_url!r} (session redirect)"
            )
        if session_error[0]:
            raise SessionExpiredError(f"@{username}: {session_error[0]}")

        for _ in range(_SCROLLS_PER_PROFILE):
            if len(reels) >= per_creator:
                break
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(_SCROLL_PAUSE)
            if session_error[0]:
                raise SessionExpiredError(f"@{username}: {session_error[0]}")

        log.info("    +%d reels from @%s", len(reels), username)
        if fallback_count[0] > 0:
            log.info(
                "    @%s: view_count missing on %d/%d reels — used video_view_count fallback",
                username, fallback_count[0], len(reels),
            )
    except SessionExpiredError:
        raise
    except Exception as e:
        log.warning("    @%s: error — %s", username, e)
    finally:
        page.remove_listener("response", on_response)

    return reels[:per_creator]


def fetch_creator_reels(username: str, per_creator: int, page, loop) -> List[Dict[str, Any]]:
    """Sync wrapper for the async fetch (used by the sync main loop)."""
    return loop.run_until_complete(_fetch_creator_reels_async(username, per_creator, page))


# ── Per-niche orchestration ──────────────────────────────────────────────────

def fetch_niche(niche: str, creators: List[str], limit: int, page, loop) -> List[Dict[str, Any]]:
    if not creators:
        log.warning("Niche '%s' has no creators — skipping", niche)
        return []

    per_creator = max(1, limit // len(creators))
    seen: Set[str] = set()
    rows: List[Dict[str, Any]] = []

    for i, username in enumerate(creators):
        if len(rows) >= limit:
            break
        reels = fetch_creator_reels(username, per_creator, page, loop)
        added = 0
        for r in reels:
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            r["niche"] = niche
            rows.append(r)
            added += 1
        log.info("  [%s] %d/%d creators done — %d reels total", niche, i + 1, len(creators), len(rows))
        if i < len(creators) - 1:
            time.sleep(_SLEEP_BETWEEN_CREATORS)

    return rows[:limit]


# ── CSV helpers ───────────────────────────────────────────────────────────────

def load_creator_map(path: Path) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    with open(path, newline="") as f:
        for r in csv.reader(f):
            if not r:
                continue
            niche = r[0].strip().lower()
            if niche == "niche":
                continue
            creators = [c.strip().lstrip("@") for c in r[1:] if c.strip()]
            if niche and creators:
                out[niche] = creators
    return out


def _load_existing_csv(path: Path) -> List[Dict[str, Any]]:
    """Load rows from an existing output CSV (for resume support)."""
    if not path.exists():
        return []
    rows = []
    fields = ["url", "niche", "view_count", "like_count", "comment_count", "uploader", "duration_sec", "follower_count"]
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows.append({k: r.get(k, "") for k in fields})
    return rows


def write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = ["url", "niche", "view_count", "like_count", "comment_count", "uploader", "duration_sec", "follower_count"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    here = Path(__file__).resolve().parent
    default_map = here / "niche_creators.csv"
    default_out = here / "reels_input.csv"

    ap = argparse.ArgumentParser(description="Fetch Instagram reels via Playwright")
    ap.add_argument("--map", type=Path, default=default_map)
    ap.add_argument("--limit", type=int, default=100,
                    help="Reels per niche total (split across creators). Default: 100")
    ap.add_argument("--niche", action="append", default=[])
    ap.add_argument("--output", type=Path, default=default_out)
    ap.add_argument("--cookies", type=Path, default=None,
                    help="Netscape-format cookie file; overrides the built-in session cookies")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.map.exists():
        log.error("Creator map not found: %s", args.map)
        return 2

    # Cookie selection
    if args.cookies:
        if not args.cookies.exists():
            log.error("Cookie file not found: %s", args.cookies)
            return 2
        try:
            active_cookies = _load_cookies_file(args.cookies)
            log.info("Loaded %d cookies from %s", len(active_cookies), args.cookies)
        except Exception as e:
            log.error("Failed to parse cookie file %s: %s", args.cookies, e)
            return 2
    else:
        active_cookies = _COOKIES
        log.info("Using built-in session cookies (user 37805258434)")

    creator_map = load_creator_map(args.map)
    if args.niche:
        wanted = {n.lower() for n in args.niche}
        creator_map = {k: v for k, v in creator_map.items() if k in wanted}

    if not creator_map:
        log.error("No niches matched.")
        return 2

    total_creators = sum(len(v) for v in creator_map.values())
    log.info("Plan: %d niches | %d creators", len(creator_map), total_creators)
    for niche, creators in creator_map.items():
        per = max(1, args.limit // len(creators))
        log.info("  %-20s %d creators × %d reels = %d", niche, len(creators), per, per * len(creators))

    if args.dry_run:
        log.info("Dry run — exiting.")
        return 0

    # Single event loop + browser for all niches
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
        )
        context.add_cookies(active_cookies)
        page = context.new_page()

        # Load any rows already saved so URL dedup works across re-runs
        all_rows: List[Dict[str, Any]] = _load_existing_csv(args.output)
        already_done_urls: Set[str] = {r["url"] for r in all_rows}
        if all_rows:
            log.info("Resuming: %d existing rows loaded for URL dedup", len(all_rows))

        for niche, creators in creator_map.items():
            log.info("─── %s (%d creators) ───", niche.upper(), len(creators))
            per_creator = max(1, args.limit // len(creators))
            seen: Set[str] = already_done_urls.copy()
            niche_rows: List[Dict[str, Any]] = []

            for i, username in enumerate(creators):
                if len(niche_rows) >= args.limit:
                    break

                try:
                    reels = _sync_fetch_creator(username, per_creator, page)
                except SessionExpiredError as exc:
                    log.error(
                        "SESSION FAILURE at @%s (niche=%s): %s",
                        username, niche, exc,
                    )
                    log.error("Stopping run. Save partial output and refresh the session before restarting.")
                    all_rows.extend(niche_rows)
                    write_csv(all_rows, args.output)
                    browser.close()
                    return 1

                added = 0
                for r in reels:
                    if r["url"] in seen:
                        continue
                    seen.add(r["url"])
                    r["niche"] = niche
                    niche_rows.append(r)
                    added += 1
                log.info("  [%s] %d/%d creators done — %d reels total", niche, i + 1, len(creators), len(niche_rows))
                if i < len(creators) - 1:
                    time.sleep(_SLEEP_BETWEEN_CREATORS)

            niche_rows = niche_rows[:args.limit]
            log.info("Niche '%s' done: %d reels", niche, len(niche_rows))
            all_rows.extend(niche_rows)
            write_csv(all_rows, args.output)

        browser.close()

    log.info("Wrote %d total rows → %s", len(all_rows), args.output)
    return 0


def _sync_fetch_creator(username: str, per_creator: int, page) -> List[Dict[str, Any]]:
    """Sync Playwright version of per-creator fetch.

    Raises SessionExpiredError if Instagram signals the session is invalid.
    Only keeps reels whose uploader matches username (skips injected suggestions).
    """
    import json as _json

    reels: List[Dict[str, Any]] = []
    seen_codes: Set[str] = set()
    session_error: List[Optional[str]] = [None]
    fallback_count: List[int] = [0]  # tracks how many reels used video_view_count fallback

    def on_response(response):
        # Session health: HTTP-level signals
        if response.status in (401, 429):
            session_error[0] = (
                f"HTTP {response.status} from {response.url} — "
                f"{'session expired' if response.status == 401 else 'rate-limited'}"
            )
            return

        # Instagram moved timeline from /graphql/query to /api/graphql — accept both
        if "graphql/query" not in response.url and "api/graphql" not in response.url:
            return
        if response.status != 200:
            return

        try:
            text = response.text()
            # Session health: JSON-level signals
            try:
                probe = _json.loads(text)
                sig = _session_signal(probe, response.url)
                if sig:
                    session_error[0] = sig
                    return
            except _json.JSONDecodeError:
                pass

            if "user_timeline_graphql_connection" not in text:
                return
            data = _json.loads(text)
            edges = data["data"]["xdt_api__v1__feed__user_timeline_graphql_connection"]["edges"]
            for e in edges:
                n = e["node"]
                code = n.get("code")
                if not code or code in seen_codes:
                    continue
                if not n.get("video_versions"):
                    continue
                # Uploader guard: skip reels injected from other creators
                actual_user = (n.get("user") or {}).get("username") or ""
                if actual_user.lower() != username.lower():
                    continue
                seen_codes.add(code)
                # view_count is absent for some accounts (Instagram returns it in
                # video_view_count instead). Fall back and track when this happens.
                raw_vc = n.get("view_count")
                if not raw_vc and raw_vc != 0:
                    raw_vc = n.get("video_view_count")
                    if raw_vc:
                        fallback_count[0] += 1
                reels.append({
                    "url":            f"https://www.instagram.com/reel/{code}/",
                    "view_count":     raw_vc or "",
                    "like_count":     n.get("like_count") or "",
                    "comment_count":  n.get("comment_count") or "",
                    "uploader":       actual_user or username,
                    "duration_sec":   "",
                    "follower_count": (n.get("user") or {}).get("follower_count") or "",
                })
        except Exception:
            pass

    page.on("response", on_response)

    try:
        log.info("  → playwright: @%s (want %d reels)", username, per_creator)
        page.goto(
            f"https://www.instagram.com/{username}/",
            wait_until="networkidle",
            timeout=_PAGE_LOAD_TIMEOUT,
        )

        # Session health: URL-redirect check.
        # Instagram 302-redirects invalid sessions to instagram.com/ (homepage),
        # not always to /accounts/login/ — so check that we actually landed on
        # the target profile rather than checking for a specific destination.
        current_url = page.url
        if "/accounts/login/" in current_url or "/challenge/" in current_url:
            raise SessionExpiredError(
                f"@{username}: redirected to {current_url!r} — "
                "session is invalid or Instagram issued a checkpoint"
            )
        if username.lower() not in current_url.lower():
            raise SessionExpiredError(
                f"@{username}: navigation did not land on profile — "
                f"ended up at {current_url!r} (session redirect)"
            )
        if session_error[0]:
            raise SessionExpiredError(f"@{username}: {session_error[0]}")

        for _ in range(_SCROLLS_PER_PROFILE):
            if len(reels) >= per_creator:
                break
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(_SCROLL_PAUSE)
            if session_error[0]:
                raise SessionExpiredError(f"@{username}: {session_error[0]}")

        log.info("    +%d reels from @%s", len(reels), username)
        if fallback_count[0] > 0:
            log.info(
                "    @%s: view_count missing on %d/%d reels — used video_view_count fallback",
                username, fallback_count[0], len(reels),
            )
    except SessionExpiredError:
        raise
    except Exception as e:
        log.warning("    @%s: error — %s", username, e)
    finally:
        page.remove_listener("response", on_response)

    return reels[:per_creator]


if __name__ == "__main__":
    sys.exit(main())
