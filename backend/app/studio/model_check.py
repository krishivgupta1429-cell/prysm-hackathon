"""Built during the Meta Global AI Developer Hackathon (new work).

Which Meta models does this deployment's key really have? Muse Spark is picked by name (META_MODEL, META_VISION_MODEL, META_SCORING_MODEL,
META_CHAT_MODEL, META_MODEL_MAP or the built-in default). When a name is not available to the key, Meta answers HTTP 404 "model_not_found" and,
without this module, an analysis only finds out after minutes of work. Here:

  * `startup_check()`   logs once at start-up which configured model is missing (names only, never the key);
  * `ensure_models()`   runs at the start of every analysis (cached for a few minutes) and fails it in the first seconds with a friendly message;
  * `apply_fallback()`  swaps an unavailable plain Muse Spark id for the newest plain Muse Spark id the key can use (never a -contributor id);
  * `friendly_error()`  turns any raw provider error ("shim: HTTP 404 ...") into a message that is safe to show a user.

The check is free (GET /v1/models). Only when a configured model is missing from that list does it make one tiny real call (a few tokens) to
confirm, because the list could be incomplete. If the list itself cannot be fetched, nothing is blocked (unknown is not "unavailable").
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from app.studio import config

log = logging.getLogger(__name__)

FRIENDLY_START = "We couldn't start the analysis. Please try again in a few minutes."
FRIENDLY_FAILED = "We couldn't finish the analysis. Please try again in a few minutes."
GROUPS = ("vision", "scoring", "chat")
TTL_S = 300.0

_state: dict = {"available": None, "at": 0.0, "bad": [], "probed_ok": set(), "warned": set()}
_PLAIN_SPARK = re.compile(r"^muse-spark-(\d+(?:\.\d+)*)$")
_RAW = re.compile(r"shim:|HTTP\s*\d{3}|model_not_found|APIStatusError|MetaError|api\.meta\.ai|anthropic", re.I)


class AnalysisUnavailable(Exception):
    """An analysis cannot run (or finish) because a model is unavailable. `str(e)` is safe to show; `detail` is for the log."""

    def __init__(self, message: str = FRIENDLY_START, detail: str = ""):
        super().__init__(message)
        self.detail = detail


def is_model_error(e: BaseException) -> bool:
    """A deterministic provider refusal (unknown model, no access), as opposed to a transient failure."""
    status = getattr(e, "status_code", None) or getattr(e, "status", None)
    text = str(e)
    return status in (401, 403, 404) or "model_not_found" in text or "HTTP 404" in text


def friendly_error(e: BaseException) -> str:
    """The message to show a user for a failed job: ours as they are, raw provider text replaced by a friendly line."""
    if isinstance(e, AnalysisUnavailable):
        return str(e)
    text = str(e)
    if is_model_error(e) or _RAW.search(text):
        return FRIENDLY_START if is_model_error(e) else FRIENDLY_FAILED
    return text or FRIENDLY_FAILED


# ------------------------------------------------------------------ what is configured, and what is available

def configured_models() -> dict[str, tuple[str, str]]:
    """{group: (model id, where it came from)} for the three kinds of Spark call, exactly as the shim would pick them (no fallback)."""
    from app.studio import meta_llm_shim as shim
    return {g: shim.configured_model("claude-sonnet-4-6", g) for g in GROUPS}


def evidence_models() -> dict[str, str]:
    """The other models the analysis can use. They fail soft (the analysis carries on without them), so they only get a warning."""
    out = {"spark audio listener": config.SPARK_AUDIO_MODEL, "caption model": config.CAPTION_MODEL,
           "voice transcribe": config.VOICE_TRANSCRIBE_MODEL, "SAM subjects": config.SAM_MODEL}
    try:
        from app.studio.autoedit import config as ae
        if ae.enabled():   # Auto-edit's two models, only when it is switched on
            out["auto-edit planner"], out["auto-edit critic"] = ae.planner_model(), ae.critic_model()
    except Exception:  # noqa: BLE001
        pass
    return out


def _version(model: str):
    m = _PLAIN_SPARK.match(model)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def plain_spark_ids(available) -> list[str]:
    """Plain Muse Spark ids (never -contributor ones), newest first."""
    return sorted((m for m in (available or ()) if _version(m)), key=_version, reverse=True)


def apply_fallback(model: str) -> str:
    """`model` if it is usable (or availability is unknown); otherwise the newest plain Muse Spark id the key can use; else `model` unchanged."""
    avail = _state["available"]
    if avail is None or model in avail or model in _state["probed_ok"] or not _PLAIN_SPARK.match(model):
        return model
    for cand in plain_spark_ids(avail):
        if ("fb", model) not in _state["warned"]:
            _state["warned"].add(("fb", model))
            log.warning("[Models] %s is not available to this key; using %s instead", model, cand)
        return cand
    return model


def fetch_available() -> set[str] | None:
    """The model ids this key can call (GET /v1/models, free), or None when that cannot be read."""
    try:
        from app.studio.meta_client import MetaClient
        ids = {m.get("id") for m in MetaClient().list_models() if m.get("id")}
        return ids or None
    except Exception as e:  # noqa: BLE001 - unknown is not unavailable
        log.warning("[Models] could not read the model list (%s); not blocking anything", type(e).__name__)
        return None


def probe(model: str) -> bool:
    """One tiny real call to `model`. False only for a definite "model not found / no access"; any other outcome counts as available."""
    try:
        from app.studio import meta_llm_shim as shim
        from app.studio.meta_client import MetaError
        owner = shim.AsyncAnthropic()
        body = shim.build_body(model, 4, [{"role": "user", "content": "Reply: ok"}], None, None, 200, group="scoring")
        owner._post(body)
        return True
    except MetaError as e:
        return not (e.status in (401, 403, 404))
    except Exception:  # noqa: BLE001
        return True


async def refresh(force: bool = False) -> list[tuple[str, str, str]]:
    """Re-check every configured Spark model (at most every TTL_S). Returns [(group, model, source)] that are definitely unavailable AND
    have no fallback; models that are missing but have a fallback are switched over (apply_fallback) and logged."""
    now = time.time()
    if not force and _state["at"] and now - _state["at"] < TTL_S:
        return list(_state["bad"])
    avail = await asyncio.to_thread(fetch_available)
    _state["available"] = avail
    bad: list[tuple[str, str, str]] = []
    if avail is not None:
        for group, (model, source) in configured_models().items():
            if model in avail or model in _state["probed_ok"]:
                continue
            if await asyncio.to_thread(probe, model):   # the list may be incomplete: a tiny real call decides
                _state["probed_ok"].add(model)
                continue
            if apply_fallback(model) == model:
                bad.append((group, model, source))
    _state["at"], _state["bad"] = now, bad
    return list(bad)


async def ensure_models() -> None:
    """Start of an analysis: raise AnalysisUnavailable (friendly) at once if a required model cannot be used."""
    from app.core.config import settings
    if not settings.META_MODE:
        return
    bad = await refresh()
    if bad:
        detail = "; ".join(f"{g} model {m} (set by {s}) is not available to this key" for g, m, s in bad)
        log.error("[Models] analysis refused: %s", detail)
        raise AnalysisUnavailable(FRIENDLY_START, detail)


async def startup_check() -> None:
    """Log, once at start-up, every configured model that this key cannot use. Never raises, never prints the key."""
    try:
        bad = await refresh(force=True)
        avail = _state["available"]
        if avail is None:
            log.warning("[Models] startup check: could not read the model list, so configured models were not verified")
            return
        shown = sorted(avail)
        for group, (model, source) in configured_models().items():
            if model in avail or model in _state["probed_ok"]:
                continue
            fb = apply_fallback(model)
            if (group, model, source) in bad:
                log.warning("[Models] WARNING: the %s model %s (from %s) is not available to this key and there is no fallback; analyses will be "
                            "refused. Available: %s", group, model, source, ", ".join(shown))
            else:
                log.warning("[Models] WARNING: the %s model %s (from %s) is not in this key's model list; using %s. Available: %s",
                            group, model, source, fb, ", ".join(shown))
        for what, model in evidence_models().items():
            if model not in avail:
                log.warning("[Models] WARNING: the %s model %s is not in this key's model list; that optional step will fail", what, model)
        if not bad:
            log.info("[Models] startup check: %s", ", ".join(f"{g}={apply_fallback(m)}" for g, (m, _s) in configured_models().items()))
    except Exception as e:  # noqa: BLE001
        log.warning("[Models] startup check failed: %s", type(e).__name__)


def reset_for_tests() -> None:
    _state.update(available=None, at=0.0, bad=[], probed_ok=set(), warned=set())
