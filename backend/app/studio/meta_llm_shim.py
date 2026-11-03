"""Built during the Meta Global AI Developer Hackathon (new work).

A drop-in for the small part of the `anthropic` SDK that Prysm's original pipeline uses, backed by Muse Spark on the
Meta API. With AI_PROVIDER=meta every Claude call site (frame analysis, the two scoring calls, ARIA, the strategist
and the other helpers) imports this module instead of the `anthropic` package (see llm_switch.py), so the original
prompts, parsing and cost tracking run unchanged and the anthropic package is never imported.

What it mimics:
  - AsyncAnthropic(api_key=..., timeout=..., max_retries=...)
  - client.messages.create(model, max_tokens, messages, system=...)  ->  .content[0].text, .usage.input_tokens/.output_tokens
  - async with client.messages.stream(...) as s:  s.text_stream  /  await s.get_final_message()
  - the exception classes the retry helper catches: RateLimitError, APITimeoutError, APIConnectionError, APIStatusError

Translation (all measured against the live API):
  - Anthropic content blocks -> OpenAI-compatible ones: {"type":"text"} stays, a base64 {"type":"image"} becomes an
    image_url data URL. `system` becomes a system message.
  - Spark spends reasoning tokens inside max_tokens (a 30-token budget returned empty content), so the shim adds
    META_REASONING_HEADROOM (default 6000) to every max_tokens. If a reply is still cut off by the budget (empty, or JSON that
    stops mid-sentence: finish_reason "length"), it is retried up to twice with doubled headroom and the wasted attempts stay
    in the usage and cost. META_REASONING_EFFORT (optional) lowers the reasoning; streamed chat (ARIA) defaults to low.
  - Images per request: Spark accepted 150 images in one request in our probe (counting accuracy dropped past ~48).
    The original pipeline sends at most 18. META_MAX_IMAGES_PER_REQUEST (default 48) caps a request; beyond it the
    frames are thinned evenly (first and last kept) and a warning is logged, because one answer cannot be stitched
    from several partial ones.
  - Model names: every Claude model id maps to META_MODEL (default muse-spark-1.3); META_MODEL_MAP is an optional JSON
    object such as {"claude-opus-4-6": "muse-spark-1.3"} for per-name overrides.
  - Errors: 429 -> RateLimitError, timeouts -> APITimeoutError, 5xx after the client's own retries ->
    APIConnectionError (the original retry helper waits and tries again), other 4xx -> APIStatusError.
  - Cost: every call is priced with the published Spark prices and added to the client's ledger; the message carries
    `cost_usd`, and the original cost tracker prices the same tokens through PRICING (see prysm_analyse_service.py).
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import httpx

from app.studio import config
from app.studio.meta_client import MetaClient, MetaError, MissingKey, TIMEOUT

log = logging.getLogger(__name__)

DEFAULT_MODEL = "muse-spark-1.3"
DEFAULT_HEADROOM = 6000
DEFAULT_MAX_IMAGES = 48
MAX_ATTEMPTS = 3  # the first call plus up to two retries with doubled headroom when the reply is cut off by the token budget


# ------------------------------------------------------------------ exceptions (the subset the pipeline catches)

class APIError(Exception):
    pass


class APIConnectionError(APIError):
    pass


class APITimeoutError(APIConnectionError):
    pass


class APIStatusError(APIError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(APIStatusError):
    pass


# ------------------------------------------------------------------ response objects

@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class Message:
    content: list
    usage: Usage
    model: str
    stop_reason: str = "end_turn"
    role: str = "assistant"
    type: str = "message"
    id: str = "msg_meta"
    cost_usd: float = 0.0
    reasoning_tokens: int = 0


# ------------------------------------------------------------------ settings (read like the rest of the Meta layer)

def _env_int(name: str, default: int) -> int:
    try:
        return int(config.get_env(name) or default)
    except ValueError:
        return default


GROUPS = ("vision", "scoring", "chat")
# Reasoning headroom added to max_tokens, per call group (Spark's hidden reasoning counts against max_tokens; scoring reasons the most)
GROUP_DEFAULT_HEADROOM = {"vision": 6000, "scoring": 10000, "chat": 6000}


def call_group(messages: list, stream: bool = False) -> str:
    """Which family of calls this is: streamed chat (ARIA) = "chat", any image in the request = "vision", otherwise the
    text-only scoring/writing calls = "scoring". The group picks the model, headroom and reasoning effort (env below)."""
    if stream:
        return "chat"
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") in ("image", "image_url") for b in c):
            return "vision"
    return "scoring"


def meta_model_for(requested: str | None, group: str | None = None) -> str:
    """The Meta model that serves a Claude model id. Order: an explicit muse-/sam- id, a META_MODEL_MAP entry,
    META_VISION_MODEL / META_SCORING_MODEL / META_CHAT_MODEL for the call's group, META_MODEL, then muse-spark-1.3."""
    if requested and requested.startswith(("muse-", "sam-")):
        return requested
    raw = config.get_env("META_MODEL_MAP")
    if raw and requested:
        try:
            mapped = json.loads(raw).get(requested)
            if mapped:
                return str(mapped)
        except ValueError:
            log.warning("META_MODEL_MAP is not valid JSON; ignoring it")
    if group:
        by_group = config.get_env(f"META_{group.upper()}_MODEL")
        if by_group:
            return by_group
    return config.get_env("META_MODEL") or DEFAULT_MODEL


def max_images() -> int:
    return max(1, _env_int("META_MAX_IMAGES_PER_REQUEST", DEFAULT_MAX_IMAGES))


def reasoning_headroom(group: str | None = None) -> int:
    """Tokens added to max_tokens for hidden reasoning: META_<GROUP>_HEADROOM, else META_REASONING_HEADROOM, else the group default."""
    default = GROUP_DEFAULT_HEADROOM.get(group, DEFAULT_HEADROOM) if group else DEFAULT_HEADROOM
    if group and config.get_env(f"META_{group.upper()}_HEADROOM"):
        return max(0, _env_int(f"META_{group.upper()}_HEADROOM", default))
    return max(0, _env_int("META_REASONING_HEADROOM", default))


def reasoning_effort(group: str | None = None) -> str | None:
    """META_<GROUP>_REASONING_EFFORT, else META_REASONING_EFFORT; streamed chat defaults to "low"."""
    eff = (config.get_env(f"META_{group.upper()}_REASONING_EFFORT") if group else None) or config.get_env("META_REASONING_EFFORT")
    if group == "chat":
        eff = eff or config.get_env("META_CHAT_REASONING_EFFORT") or "low"
    return eff or None


# ------------------------------------------------------------------ request translation

def _thin_images(blocks: list, limit: int) -> list:
    """Keep at most `limit` image blocks (evenly spaced, first and last kept); drop the caption that went with a dropped image."""
    idx = [i for i, b in enumerate(blocks) if b.get("type") == "image_url"]
    if len(idx) <= limit:
        return blocks
    keep = {idx[round(k * (len(idx) - 1) / (limit - 1))] for k in range(limit)} if limit > 1 else {idx[0]}
    log.warning("[MetaShim] %d images in one request, above META_MAX_IMAGES_PER_REQUEST=%d: keeping %d evenly spaced",
                len(idx), limit, len(keep))
    out = []
    for i, b in enumerate(blocks):
        if b.get("type") == "image_url" and i not in keep:
            if out and out[-1].get("type") == "text" and out[-1]["text"].lstrip().startswith(("=== FRAME", "Frame ")):
                out.pop()  # the "=== FRAME AT 3s ===" caption of a frame that was dropped
            continue
        out.append(b)
    return out


def _convert_content(content) -> object:
    """Anthropic message content (str or list of blocks) -> OpenAI-compatible content."""
    if isinstance(content, str):
        return content
    blocks = []
    for b in content or []:
        if isinstance(b, str):
            blocks.append({"type": "text", "text": b})
            continue
        kind = b.get("type")
        if kind == "text":
            blocks.append({"type": "text", "text": b.get("text", "")})
        elif kind == "image":
            src = b.get("source") or {}
            if src.get("type") == "base64":
                url = f"data:{src.get('media_type', 'image/jpeg')};base64,{src.get('data', '')}"
            elif src.get("type") == "url":
                url = src.get("url", "")
            else:
                raise APIStatusError(f"Unsupported image source: {src.get('type')!r}", 400)
            blocks.append({"type": "image_url", "image_url": {"url": url}})
        else:
            raise APIStatusError(f"Unsupported content block for the Meta shim: {kind!r}", 400)
    blocks = _thin_images(blocks, max_images())
    if all(b["type"] == "text" for b in blocks):
        return "\n".join(b["text"] for b in blocks)  # plain text turns are sent as plain strings
    return blocks


def build_messages(system, messages: list) -> list:
    out = []
    if system:
        text = system if isinstance(system, str) else "\n".join(b.get("text", "") for b in system if isinstance(b, dict))
        if text.strip():
            out.append({"role": "system", "content": text})
    for m in messages or []:
        out.append({"role": m["role"], "content": _convert_content(m["content"])})
    return out


def build_body(model: str, max_tokens: int | None, messages: list, system, temperature=None, headroom: int | None = None,
               stream: bool = False, group: str | None = None) -> dict:
    group = group or call_group(messages, stream)
    body = {"model": model, "messages": build_messages(system, messages)}
    if max_tokens:
        body["max_tokens"] = int(max_tokens) + (reasoning_headroom(group) if headroom is None else headroom)
    # ARIA streams to a person waiting for the first word: it defaults to low reasoning effort
    effort = reasoning_effort(group)
    if effort:
        body["reasoning_effort"] = effort
    if temperature is not None:
        body["temperature"] = temperature
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    return body


# ------------------------------------------------------------------ the client

def _map_error(e: Exception) -> Exception:
    if isinstance(e, MetaError):
        if e.timeout:
            return APITimeoutError(str(e))
        status = e.status
        if status == 429:
            return RateLimitError(str(e), 429)
        if status is None or status >= 500:
            return APIConnectionError(str(e))
        return APIStatusError(str(e), status)
    return e


class _Messages:
    def __init__(self, owner: "AsyncAnthropic"):
        self._owner = owner

    async def create(self, *, model: str, max_tokens: int = 1024, messages: list, system=None, temperature=None,
                     **_ignored) -> Message:
        owner = self._owner
        group = call_group(messages)
        target = meta_model_for(model, group)
        headroom = reasoning_headroom(group)
        spent = Usage()
        spent_cost, spent_reasoning = 0.0, 0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            body = build_body(target, max_tokens, messages, system, temperature, headroom, group=group)
            try:
                reply = await asyncio.to_thread(owner._post, body)
            except MissingKey as e:
                raise APIStatusError(str(e), 401) from None
            except MetaError as e:
                raise _map_error(e) from None
            # The answer is cut off when the hidden reasoning ate the budget: either nothing came back, or the JSON stops
            # mid-sentence (finish_reason "length"). Both are retried with more room; the wasted attempt stays in the cost.
            if reply.stop_reason != "max_tokens" or attempt == MAX_ATTEMPTS:
                break
            spent.input_tokens += reply.usage.input_tokens
            spent.output_tokens += reply.usage.output_tokens
            spent_cost += reply.cost_usd
            spent_reasoning += reply.reasoning_tokens
            headroom *= 2
            log.warning("[MetaShim] reply cut off by the token budget (%d chars, %d reasoning tokens); retrying with headroom %d",
                        len(reply.content[0].text), reply.reasoning_tokens, headroom)
        if spent.input_tokens or spent.output_tokens:
            reply.usage = Usage(reply.usage.input_tokens + spent.input_tokens, reply.usage.output_tokens + spent.output_tokens)
            reply.cost_usd = round(reply.cost_usd + spent_cost, 6)
            reply.reasoning_tokens += spent_reasoning
        return reply

    def stream(self, *, model: str, max_tokens: int = 1024, messages: list, system=None, temperature=None, **_ignored):
        return _StreamManager(self._owner, meta_model_for(model, "chat"), max_tokens, messages, system, temperature)


class _StreamManager:
    """`async with client.messages.stream(...) as stream` -> stream.text_stream, await stream.get_final_message()."""

    def __init__(self, owner, model, max_tokens, messages, system, temperature):
        self.owner, self.model = owner, model
        self.body = build_body(model, max_tokens, messages, system, temperature, stream=True, group="chat")
        self.text_stream = self._iter()
        self._text: list[str] = []
        self._usage = Usage()
        self._cost = 0.0
        self._done = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def _iter(self):
        owner = self.owner
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as http:
                async with http.stream("POST", f"{config.META_BASE_URL}/chat/completions",
                                       headers=owner.meta._headers(), json=self.body) as r:
                    if r.status_code >= 400:
                        text = (await r.aread()).decode("utf-8", "replace")
                        raise _map_error(MetaError(owner.meta._redact(f"chat: HTTP {r.status_code}: {text[:300]}"),
                                                   status=r.status_code))
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except ValueError:
                            continue
                        usage = chunk.get("usage")
                        if usage:
                            self._usage = Usage(usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0)
                        for choice in chunk.get("choices") or []:
                            piece = (choice.get("delta") or {}).get("content")
                            if piece:
                                self._text.append(piece)
                                yield piece
        except MissingKey as e:
            raise APIStatusError(str(e), 401) from None
        except (httpx.TimeoutException,) as e:
            raise APITimeoutError(owner.meta._redact(str(e))) from None
        except httpx.HTTPError as e:
            raise APIConnectionError(owner.meta._redact(f"{type(e).__name__}: {e}")) from None
        finally:
            price_in, price_out = config.price_per_m(self.model)
            self._cost = round((self._usage.input_tokens * price_in + self._usage.output_tokens * price_out) / 1e6, 6)
            if self._cost:
                owner.meta._charge("spark", f"{self.model} chat", self._cost)
            self._done = True
            log.info("[MetaShim] stream done in %.1fs, %d in / %d out", time.time() - start,
                     self._usage.input_tokens, self._usage.output_tokens)

    async def get_final_message(self) -> Message:
        return Message(content=[TextBlock("".join(self._text))], usage=self._usage, model=self.model,
                       cost_usd=self._cost)


@dataclass
class AsyncAnthropic:
    """Looks like anthropic.AsyncAnthropic to the callers; the arguments are accepted and the key is ignored (META_API_KEY is used)."""
    api_key: str | None = None
    timeout: float | None = None
    max_retries: int = 0
    meta: MetaClient = field(default_factory=MetaClient)
    messages: _Messages = field(init=False)

    def __post_init__(self):
        self.messages = _Messages(self)

    def _post(self, body: dict) -> Message:
        """One blocking chat/completions call (run in a worker thread) with the Meta client's retry and cost plumbing."""
        meta = self.meta
        read = float(self.timeout) if self.timeout else 360.0
        timeout = httpx.Timeout(connect=20.0, read=max(read, 60.0), write=120.0, pool=20.0)
        label = "shim"

        def call():
            r = httpx.post(f"{config.META_BASE_URL}/chat/completions", headers=meta._headers(), json=body, timeout=timeout)
            meta._check(label, r.status_code, r.text)
            return r.json()

        start = time.time()
        j = meta._with_retries(label, call)
        choice = (j.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content")
        text = " ".join(p.get("text", "") for p in content if isinstance(p, dict)) if isinstance(content, list) else (content or "")
        usage = j.get("usage") or {}
        tin, tout = usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0
        reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        price_in, price_out = config.price_per_m(body["model"])
        cost = round((tin * price_in + tout * price_out) / 1e6, 6)
        meta._charge("wonder" if body["model"] == config.WONDER_MODEL else "spark", body["model"], cost, tin, tout, reasoning)
        finish = choice.get("finish_reason")
        log.info("[MetaShim] %s: %d in / %d out (%d reasoning) $%.4f in %.1fs", body["model"], tin, tout, reasoning, cost,
                 time.time() - start)
        return Message(content=[TextBlock(text)], usage=Usage(tin, tout), model=body["model"],
                       stop_reason="max_tokens" if finish == "length" else "end_turn", cost_usd=cost,
                       reasoning_tokens=reasoning)


_shared: AsyncAnthropic | None = None


def get_shim_client() -> AsyncAnthropic:
    """One process-wide client (ARIA and ad-hoc callers); the pipeline's own getters create theirs the Anthropic way."""
    global _shared
    if _shared is None:
        _shared = AsyncAnthropic()
    return _shared
