"""Built during the Meta Global AI Developer Hackathon (new work).

Live trend grounding: what is trending right now for the creator's niches, from Gemini 2.5 Flash with Google Search
grounding. Label: existing service idea, new integration. The startup baseline (app/services/trend_service.py) already
asks Gemini with Google Search for trends and caches them for 24 hours; this is fresh code written for the new
engine: a stage with a strict output shape, source URLs from the grounding metadata, a 6-hour cache per niche set per
day, and a fail-soft contract so the analysis never waits on it or breaks because of it.

Inputs: the creator's niches (up to 3), the platform, the creator's description and a few content keywords from the
watch stage. Output: trending formats, trending audio, trending hashtags and a one-line "what's working now" per niche,
plus the web sources Gemini cited. Global by default; India context is added only when the creator themselves picked an
India-centric niche (dance, bollywood, indiancreator or food), as the baseline did.

Fail-soft: 20 s timeout. On any error (no key, timeout, API error, malformed or empty answer) the result is
{"status": "unavailable"} and the pipeline continues; Trend Alignment then stays an "estimate". This module never raises
and never logs or returns the API key. It imports nothing from the baseline: the host passes in the API key and,
optionally, a cache with async get(key) / set(key, value, ttl_s); an in-memory cache is the fallback.
"""
import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone

from app.studio import config
from app.studio.report import TrendSource, TrendsStage
from app.studio.stages.watch import parse_watch_json

log = logging.getLogger("prysm.studio.trends")

MODEL = "gemini-2.5-flash"
TIMEOUT_S = 20.0
RETRY_DELAY_S = 1.5
STAGE_GRACE_S = 5.0  # the pipeline stops waiting this long after the fetch's own timeout, whatever happens inside it
CACHE_TTL_S = 6 * 3600
INDIA_NICHES = {"dance", "bollywood", "indiancreator", "food"}  # the baseline's list of niches that get India context
GROUNDING_USD_PER_PROMPT = 0.035  # APPROXIMATE: Google Search grounding is billed per grounded prompt; check Google's price list
MAX_NICHES = 3
MAX_LIST = 8

_memory: dict[str, tuple[float, dict]] = {}  # fallback cache: key -> (stored_at, payload)

PROMPT = """Search Google for what is trending RIGHT NOW (today) in short-form video{platform_part}, for creators in: {niches}.{geo}
{hints}
Report only what your search results actually show; if something is not found, leave its list empty. Do not invent trends, sounds or hashtags.

Reply with ONE JSON object only (no prose, no code fences):
{{"formats": ["up to 6 content formats or video styles currently trending for these niches"],
  "audio": ["up to 6 specific trending songs or sounds used in reels for these niches, as 'title - artist' when known"],
  "hashtags": ["up to 8 hashtags currently used by trending videos for these niches, each starting with #"],
  "whats_working": {{{working_keys}}}}}"""


# ---------------------------------------------------------------- pure helpers

def normalise_niches(niches) -> list[str]:
    """Up to three distinct niche names, in the creator's order."""
    out: list[str] = []
    for n in niches or []:
        name = " ".join(str(n).split())[:60]
        if name and name.lower() not in {x.lower() for x in out}:
            out.append(name)
    return out[:MAX_NICHES]


def wants_india(niches) -> bool:
    """India context only when the creator explicitly picked an India-centric niche."""
    return any(n.strip().lower().replace(" ", "") in INDIA_NICHES for n in niches or [])


def cache_key(niches: list[str], platform: str, geo: str, now: datetime | None = None) -> str:
    """One key per niche set, platform and UTC day (the 6-hour expiry makes it at most a few fetches a day)."""
    now = now or datetime.now(timezone.utc)
    raw = "|".join(sorted(n.lower() for n in niches)) + "|" + platform.strip().lower() + "|" + geo
    return f"prysm:studio:trends:{now:%Y%m%d}:{hashlib.sha1(raw.encode()).hexdigest()[:16]}"


def keywords_from_watch(watch) -> list[str]:
    """A few content keywords from the watch stage (format, subject, setting, on-screen text); empty if watch failed."""
    if watch is None or getattr(watch, "status", "") != "ok":
        return []
    words: list[str] = []
    if watch.format_type and watch.format_type != "other":
        words.append(watch.format_type)
    for text in (watch.subject_description, watch.setting):
        if text and text.strip():
            words.append(" ".join(text.split())[:60])
    words += [" ".join(t.text.split())[:40] for t in watch.on_screen_text[:2] if t.text.strip()]
    return list(dict.fromkeys(words))[:4]


def build_prompt(niches: list[str], platform: str, description: str, keywords: list[str], geo: str) -> str:
    hints = []
    if description:
        hints.append(f"The creator describes their video as (unverified context): {description[:300]}")
    if keywords:
        hints.append(f"Content keywords seen in the video: {', '.join(keywords)}. Prefer trends relevant to this kind of content.")
    working = ", ".join(f'"{n}": "one line on what is working now in this niche"' for n in niches)
    return PROMPT.format(platform_part=f" on {platform}" if platform else "", niches=", ".join(niches),
                         geo=(" Focus on India: Indian creators, Bollywood and desi content trends." if geo == "India" else ""),
                         hints=("\n" + "\n".join(hints)) if hints else "", working_keys=working)


def _strings(value, limit: int = MAX_LIST, size: int = 120) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = " ".join(str(item).split())[:size]
        if text and text not in out:
            out.append(text)
    return out[:limit]


def parse_trends(text: str, niches: list[str]) -> dict:
    """The model's JSON as clean lists, or raises ValueError when it is malformed or says nothing."""
    data = parse_watch_json(text or "")
    if not isinstance(data, dict):
        raise ValueError("answer is not a JSON object")
    hashtags = []
    for tag in _strings(data.get("hashtags"), size=60):
        tag = tag if tag.startswith("#") else "#" + tag.lstrip("#")
        if len(tag) > 1 and " " not in tag and tag not in hashtags:
            hashtags.append(tag)
    working = data.get("whats_working")
    by_niche = {}
    if isinstance(working, dict):
        lowered = {str(k).strip().lower(): v for k, v in working.items()}
        for n in niches:
            line = lowered.get(n.lower())
            if isinstance(line, str) and line.strip():
                by_niche[n] = " ".join(line.split())[:240]
    elif isinstance(working, str) and working.strip() and len(niches) == 1:
        by_niche[niches[0]] = " ".join(working.split())[:240]
    result = {"formats": _strings(data.get("formats")), "audio": _strings(data.get("audio")), "hashtags": hashtags[:MAX_LIST],
              "whats_working": by_niche}
    if not (result["formats"] or result["audio"] or result["hashtags"] or result["whats_working"]):
        raise ValueError("the answer holds no trends")
    return result


def sources_from(response) -> list[dict]:
    """Web sources Gemini cited, from the grounding metadata (title is usually the site's domain, url a redirect link)."""
    out, seen = [], set()
    try:
        for cand in getattr(response, "candidates", None) or []:
            meta = getattr(cand, "grounding_metadata", None)
            for chunk in (getattr(meta, "grounding_chunks", None) or []):
                web = getattr(chunk, "web", None)
                url = getattr(web, "uri", None)
                if url and url not in seen:
                    seen.add(url)
                    out.append({"title": (getattr(web, "title", "") or "")[:120], "url": url})
    except Exception:  # a metadata shape we do not know must never lose the trends themselves
        return out
    return out[:12]


def response_text(response) -> str:
    """The reply text; with grounding it can be None on .text and live in the first candidate's parts."""
    text = getattr(response, "text", None)
    if text:
        return text
    try:
        parts = response.candidates[0].content.parts
        return "".join(getattr(p, "text", "") or "" for p in parts)
    except Exception:
        return ""


def cost_of(response) -> tuple[float, int | None, int | None]:
    """Estimated USD: tokens at Flash's price plus the per-prompt Search grounding fee (approximate, see the constants)."""
    usage = getattr(response, "usage_metadata", None)
    tin = getattr(usage, "prompt_token_count", None)
    tout = (getattr(usage, "candidates_token_count", None) or 0) + (getattr(usage, "thoughts_token_count", None) or 0)
    price_in, price_out = config.GEMINI_USD_PER_M[MODEL]
    return round((tin or 0) * price_in / 1e6 + tout * price_out / 1e6 + GROUNDING_USD_PER_PROMPT, 6), tin, (tout or None)


# ---------------------------------------------------------------- the call

def _call_gemini(api_key: str, prompt: str, timeout_s: float):
    """Blocking google-genai call: Gemini Flash with the Google Search tool (run in a thread)."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
    return client.models.generate_content(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.2,
                                           thinking_config=types.ThinkingConfig(thinking_budget=0)))  # no hidden reasoning: faster and cheaper


def _unavailable(error: str, niches, platform, geo, keywords, seconds=0.0, cost=0.0, api_key: str | None = None) -> TrendsStage:
    if api_key:
        error = error.replace(api_key, "***")
    return TrendsStage(status="unavailable", error=error[:240], seconds=round(seconds, 2), cost_usd=cost, model=MODEL,
                       niches=niches, platform=platform, geo=geo, keywords_used=keywords)


async def _cache_get(cache, key: str, now_ts: float):
    if cache is not None:
        try:
            hit = await cache.get(key)
            if hit:
                return hit
        except Exception as e:
            log.debug("trends cache read failed: %s", e)
    stored = _memory.get(key)
    if stored and now_ts - stored[0] < CACHE_TTL_S:
        return stored[1]
    return None


async def _cache_set(cache, key: str, payload: dict, now_ts: float) -> None:
    _memory[key] = (now_ts, payload)
    if cache is not None:
        try:
            await cache.set(key, payload, CACHE_TTL_S)
        except Exception as e:
            log.debug("trends cache write failed: %s", e)


async def fetch_trends(niches, platform: str = "", description: str = "", keywords: list[str] | None = None, *,
                       api_key: str | None, cache=None, timeout_s: float = TIMEOUT_S, now: datetime | None = None,
                       call=None) -> TrendsStage:
    """Live trends for these niches. Never raises: on any problem returns status "unavailable"."""
    call = call or _call_gemini  # late-bound so tests can replace it
    niches = normalise_niches(niches)
    platform = (platform or "").strip()
    keywords = list(keywords or [])
    geo = "India" if wants_india(niches) else "global"
    start = time.time()
    if not niches:
        return TrendsStage(status="skipped", error="no niche given", platform=platform)
    if not api_key:
        return _unavailable("GEMINI_API_KEY is not set", niches, platform, geo, keywords)
    now = now or datetime.now(timezone.utc)
    key = cache_key(niches, platform, geo, now)
    try:
        hit = await _cache_get(cache, key, now.timestamp())
        if hit:
            stage = TrendsStage.model_validate({**hit, "cached": True, "cost_usd": 0.0, "seconds": round(time.time() - start, 2),
                                                "keywords_used": keywords})
            return stage
    except Exception as e:  # a corrupt cache entry is just a miss
        log.debug("trends cache entry unusable: %s", e)
    prompt = build_prompt(niches, platform, description, keywords, geo)
    response = None
    for attempt in (1, 2):  # one quick retry when Gemini says it is overloaded (503), as long as the 20 s budget allows
        remaining = timeout_s - (time.time() - start)
        try:
            response = await asyncio.wait_for(asyncio.to_thread(call, api_key, prompt, max(remaining, 1.0)), remaining + 1)
            break
        except asyncio.TimeoutError:
            return _unavailable(f"timed out after {timeout_s:g} s", niches, platform, geo, keywords, time.time() - start)
        except Exception as e:
            overloaded = "503" in str(e) or "UNAVAILABLE" in str(e)
            if attempt == 1 and overloaded and timeout_s - (time.time() - start) > 8:
                await asyncio.sleep(RETRY_DELAY_S)
                continue
            return _unavailable(f"{type(e).__name__}: {e}", niches, platform, geo, keywords, time.time() - start, api_key=api_key)
    cost, tin, tout = cost_of(response)
    try:
        parsed = parse_trends(response_text(response), niches)
    except (ValueError, TypeError) as e:
        return _unavailable(f"unusable answer: {e}", niches, platform, geo, keywords, time.time() - start, cost, api_key)
    stage = TrendsStage(status="ok", seconds=round(time.time() - start, 2), cost_usd=cost, model=MODEL, niches=niches,
                        platform=platform, geo=geo, cached=False, fetched_at=now.isoformat(timespec="seconds"),
                        keywords_used=keywords, tokens_in=tin, tokens_out=tout,
                        sources=[TrendSource(**s) for s in sources_from(response)], **parsed)
    await _cache_set(cache, key, stage.model_dump(mode="json", exclude={"keywords_used", "seconds", "cost_usd", "cached"}), now.timestamp())
    return stage
