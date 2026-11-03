"""Hackathon-built (Meta Global AI Developer Hackathon).

Small httpx client for the Meta API (Muse Spark, Voice Transcribe, SAM 3.1), Groq Whisper and
the Gemini REST API, with cost accounting. Blocking calls; the pipeline runs them with asyncio.to_thread.

- 5xx responses and timeouts are retried twice; 4xx errors fail immediately.
- Every error message is redacted of API key values before it leaves this module.
- Based on the request formats verified in spike.py, sam_probe.py and spark_audio_test.py.
"""
import base64
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.studio import config

RETRIES = 2
TIMEOUT = httpx.Timeout(connect=20.0, read=120.0, write=120.0, pool=20.0)

# <12f>0<|box;x1=79;y1=179;x2=295;y2=719;w=406;h=720;c=0.96|>   frame 12, object 0
SAM_DETECTION = re.compile(r"<(\d+)f>(\d+)<\|box;([^|]*)\|>")


class MetaError(RuntimeError):
    """Any API failure; the message never contains an API key.

    `status` is the HTTP status when there was one and `timeout` is True for a timeout, so callers can decide
    what is worth retrying without parsing the message.
    """

    def __init__(self, message: str = "", *, status: int | None = None, timeout: bool = False):
        super().__init__(message)
        self.status = status
        self.timeout = timeout


class MissingKey(MetaError):
    pass


class _Retryable(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status


@dataclass
class WatchCall:
    text: str
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float
    seconds: float
    model: str


@dataclass
class VoiceCall:
    transcript: str
    audio_duration_ms: int | None
    session_id: str | None
    turns: list
    cost_usd: float
    seconds: float


@dataclass
class WhisperCall:
    language: str | None
    text: str
    segments: list[dict]
    words: list[dict]
    cost_usd: float
    seconds: float
    granularity: str = "none"  # "word+segment", "segment" or "none": what the API actually returned


@dataclass
class GeminiCall:
    text: str
    model: str
    tokens_in: int | None
    tokens_out: int | None
    thinking_tokens: int | None
    cost_usd: float
    seconds: float


@dataclass
class Detection:
    obj: int
    frame: int
    x1: int
    y1: int
    x2: int
    y2: int
    w: int
    h: int
    conf: float


@dataclass
class SamCall:
    prompt: str
    detections: list[Detection]
    frames_processed: int | None
    events: int
    cost_usd: float
    seconds: float


@dataclass
class CostEntry:
    service: str
    detail: str
    usd: float


def parse_sam_deltas(text: str) -> list[Detection]:
    """Parse the concatenated SAM delta strings into per-frame detections (masks are not decoded)."""
    out = []
    for frame, obj, box in SAM_DETECTION.findall(text):
        kv = dict(part.split("=", 1) for part in box.split(";") if "=" in part)
        out.append(Detection(obj=int(obj), frame=int(frame), x1=int(kv["x1"]), y1=int(kv["y1"]),
                             x2=int(kv["x2"]), y2=int(kv["y2"]), w=int(kv["w"]), h=int(kv["h"]),
                             conf=float(kv.get("c", 0.0))))
    return out


@dataclass
class MetaClient:
    meta_key: str | None = field(default_factory=config.meta_key)
    groq_key: str | None = field(default_factory=config.groq_key)
    gemini_key: str | None = field(default_factory=config.gemini_key)
    ledger: list[CostEntry] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _uploads: dict[str, str] = field(default_factory=dict)
    _data_urls: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------ plumbing

    def _redact(self, text: str) -> str:
        for key in (self.meta_key, self.groq_key, self.gemini_key):
            if key:
                text = text.replace(key, "***")
        return config.redact(text)

    @property
    def total_cost(self) -> float:
        return round(sum(e.usd for e in self.ledger), 6)

    def _charge(self, service: str, detail: str, usd: float) -> None:
        with self._lock:
            self.ledger.append(CostEntry(service, detail, usd))

    def _headers(self, extra: dict | None = None) -> dict:
        if not self.meta_key:
            raise MissingKey("META_API_KEY is not set in backend/.env")
        return {"Authorization": f"Bearer {self.meta_key}", **(extra or {})}

    def _check(self, label: str, status: int, body: str) -> None:
        if status >= 500:
            raise _Retryable(status, body)
        if status >= 400:
            raise MetaError(self._redact(f"{label}: HTTP {status}: {body[:600]}"), status=status)

    def _with_retries(self, label: str, fn, retries: int | None = None):
        retries = RETRIES if retries is None else retries  # 0 = a single attempt (the caller does its own backoff)
        last = None
        for attempt in range(retries + 1):
            try:
                return fn()
            except (_Retryable, httpx.TimeoutException) as e:
                last = e
            except MetaError:
                raise
            except httpx.HTTPError as e:
                raise MetaError(self._redact(f"{label}: {type(e).__name__}: {e}")) from None
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        raise MetaError(self._redact(f"{label}: gave up after {retries + 1} attempts: {type(last).__name__}: {last}"),
                        status=getattr(last, "status", None), timeout=isinstance(last, httpx.TimeoutException)) from None

    def _data_url(self, path: Path, mime: str) -> str:
        key = f"{path}:{mime}"
        with self._lock:
            if key not in self._data_urls:
                self._data_urls[key] = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"
            return self._data_urls[key]

    # ------------------------------------------------------------ Spark on native video

    def watch_video(self, video_path: Path, prompt: str, model: str) -> WatchCall:
        """chat/completions with a video_url data URL (the format verified in spike.py)."""
        return self._chat([{"type": "text", "text": prompt},
                           {"type": "video_url", "video_url": {"url": self._data_url(video_path, "video/mp4")}}],
                          model, "watch")

    def complete(self, prompt: str, model: str, temperature: float | None = None,
                 images: list[bytes] | None = None) -> WatchCall:
        """chat/completions with text, optionally a temperature and JPEG images (caption editor, analysis)."""
        content: list = [{"type": "text", "text": prompt}]
        for img in images or []:
            url = "data:image/jpeg;base64," + base64.b64encode(img).decode()
            content.append({"type": "image_url", "image_url": {"url": url}})
        return self._chat(content, model, "complete", temperature)

    def _chat(self, content: list, model: str, label: str, temperature: float | None = None) -> WatchCall:
        body = {"model": model, "messages": [{"role": "user", "content": content}]}
        if temperature is not None:
            body["temperature"] = temperature
        start = time.time()

        def call():
            r = httpx.post(f"{config.META_BASE_URL}/chat/completions", headers=self._headers(),
                           json=body, timeout=TIMEOUT)
            if r.status_code == 400 and "temperature" in body and "temperature" in r.text.lower():
                body.pop("temperature")  # this model does not take a temperature: retry without it
                r = httpx.post(f"{config.META_BASE_URL}/chat/completions", headers=self._headers(),
                               json=body, timeout=TIMEOUT)
            self._check(label, r.status_code, r.text)
            return r.json()

        j = self._with_retries(label, call)
        message = (j.get("choices") or [{}])[0].get("message", {}).get("content")
        text = " ".join(p.get("text", "") for p in message if isinstance(p, dict)) if isinstance(message, list) \
            else (message or "")
        usage = j.get("usage") or {}
        tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
        price_in, price_out = config.price_per_m(model)
        cost = ((tin or 0) * price_in + (tout or 0) * price_out) / 1e6
        self._charge("wonder" if model == config.WONDER_MODEL else "spark", model, cost)
        return WatchCall(text=text, tokens_in=tin, tokens_out=tout, cost_usd=round(cost, 6),
                         seconds=round(time.time() - start, 2), model=model)

    def list_models(self) -> list[dict]:
        """GET /v1/models: the models this key may call (used by the director probe to find the exact Wonder id)."""
        def call():
            r = httpx.get(f"{config.META_BASE_URL}/models", headers=self._headers(), timeout=TIMEOUT)
            self._check("models", r.status_code, r.text)
            return r.json()

        j = self._with_retries("models", call)
        return j.get("data") if isinstance(j, dict) and isinstance(j.get("data"), list) else (j if isinstance(j, list) else [])

    # ------------------------------------------------------------ chat (ARIA)

    def chat_stream(self, messages: list[dict], model: str) -> "ChatStream":
        """A streaming multi-turn chat/completions call (OpenAI chunk format, usage in the last chunk). Nothing is sent
        until the stream is iterated; the cost is added to the ledger when it ends."""
        return ChatStream(self, messages, model)

    # ------------------------------------------------------------ Gemini

    def gemini(self, model: str, prompt: str, media_path: Path, mime: str, retries: int | None = None) -> GeminiCall:
        """generateContent with the media inline (inline_data) and JSON output. Key goes in a header only."""
        if not self.gemini_key:
            raise MissingKey("GEMINI_API_KEY is not set in backend/.env")
        with self._lock:
            key = f"b64:{media_path}"
            if key not in self._data_urls:
                self._data_urls[key] = base64.b64encode(media_path.read_bytes()).decode()
            data = self._data_urls[key]
        body = {"contents": [{"role": "user", "parts": [{"text": prompt},
                                                         {"inline_data": {"mime_type": mime, "data": data}}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
        start = time.time()

        def call():
            r = httpx.post(f"{config.GEMINI_BASE_URL}/models/{model}:generateContent",
                           headers={"x-goog-api-key": self.gemini_key, "Content-Type": "application/json"},
                           json=body, timeout=httpx.Timeout(connect=20.0, read=150.0, write=120.0, pool=20.0))
            self._check("gemini", r.status_code, r.text)
            return r.json()

        j = self._with_retries("gemini", call, retries)
        cand = (j.get("candidates") or [{}])[0]
        parts = cand.get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text:
            raise MetaError(self._redact(f"gemini: empty answer (finishReason={cand.get('finishReason')}, "
                                         f"promptFeedback={j.get('promptFeedback')})"))
        u = j.get("usageMetadata") or {}
        tin, tout, think = u.get("promptTokenCount"), u.get("candidatesTokenCount"), u.get("thoughtsTokenCount")
        price_in, price_out = config.GEMINI_USD_PER_M.get(model, (0.0, 0.0))
        cost = ((tin or 0) * price_in + ((tout or 0) + (think or 0)) * price_out) / 1e6
        self._charge("gemini", model, cost)
        return GeminiCall(text=text, model=model, tokens_in=tin, tokens_out=tout, thinking_tokens=think,
                          cost_usd=round(cost, 6), seconds=round(time.time() - start, 2))

    # ------------------------------------------------------------ Voice Transcribe

    def transcribe(self, wav_path: Path) -> VoiceCall:
        """POST /asr/transcribe: multipart with exactly a `request` part and an `audio` part."""
        request = json.dumps({"mode": "PUSH_TO_TALK", "model": config.VOICE_TRANSCRIBE_MODEL,
                              "audioEncoding": "WAV", "partialMode": "CUMULATIVE", "emitAudioProgress": False},
                             separators=(",", ":"))
        audio = wav_path.read_bytes()
        start = time.time()

        def call():
            r = httpx.post(f"{config.META_BASE_URL}/asr/transcribe", headers=self._headers(), timeout=TIMEOUT,
                           files={"request": ("request.json", request, "application/json"),
                                  "audio": (wav_path.name, audio, "audio/wav")})
            self._check("transcribe", r.status_code, r.text)
            return r.json()

        j = self._with_retries("transcribe", call)
        ms = j.get("audioDurationMs")
        cost = (ms or 0) / 3_600_000 * config.VOICE_TRANSCRIBE_USD_PER_HOUR
        self._charge("voice_transcribe", wav_path.name, cost)
        return VoiceCall(transcript=(j.get("transcript") or "").strip(), audio_duration_ms=ms,
                         session_id=j.get("sessionId"), turns=j.get("turns") or [], cost_usd=round(cost, 6),
                         seconds=round(time.time() - start, 2))

    # ------------------------------------------------------------ Groq Whisper

    def whisper(self, wav_path: Path) -> WhisperCall:
        """whisper-large-v3-turbo, language auto-detected, word + segment timestamps when supported."""
        if not self.groq_key:
            raise MissingKey("GROQ_API_KEY is not set in backend/.env")
        audio = wav_path.read_bytes()
        start = time.time()

        def call(granularities: bool):
            data = {"model": config.GROQ_WHISPER_MODEL, "response_format": "verbose_json"}
            if granularities:
                data["timestamp_granularities[]"] = ["word", "segment"]
            r = httpx.post(config.GROQ_URL, headers={"Authorization": f"Bearer {self.groq_key}"}, timeout=TIMEOUT,
                           data=data, files={"file": (wav_path.name, audio, "audio/wav")})
            self._check("whisper", r.status_code, r.text)
            return r.json()

        try:
            j = self._with_retries("whisper", lambda: call(True))
        except MetaError as e:
            if "HTTP 400" not in str(e):  # timestamps unsupported -> retry once without them
                raise
            j = self._with_retries("whisper", lambda: call(False))
        words = [{"start": w["start"], "end": w["end"], "word": w["word"]} for w in j.get("words") or []]
        segments = [{"start": x["start"], "end": x["end"], "text": x["text"].strip()} for x in j.get("segments") or []]
        granularity = "word+segment" if words and segments else ("segment" if segments else "none")
        cost = float(j.get("duration") or 0) / 3600 * config.GROQ_USD_PER_HOUR
        self._charge("whisper", wav_path.name, cost)
        return WhisperCall(language=j.get("language"), text=(j.get("text") or "").strip(), segments=segments,
                           words=words, cost_usd=round(cost, 6), seconds=round(time.time() - start, 2),
                           granularity=granularity)

    # ------------------------------------------------------------ SAM 3.1

    def upload(self, clip_path: Path) -> str:
        """POST /files (purpose=user_data); reused across prompts for the same clip."""
        with self._lock:
            if str(clip_path) in self._uploads:
                return self._uploads[str(clip_path)]
        data = clip_path.read_bytes()

        def call():
            r = httpx.post(f"{config.META_BASE_URL}/files", headers=self._headers(), timeout=TIMEOUT,
                           data={"purpose": "user_data"}, files={"file": ("upload.mp4", data, "video/mp4")})
            self._check("upload", r.status_code, r.text)
            return r.json()

        file_id = self._with_retries("upload", call)["id"]
        with self._lock:
            self._uploads[str(clip_path)] = file_id
        return file_id

    def sam_track(self, clip_path: Path, prompt: str, raw_path: Path | None = None) -> SamCall:
        """Stream /responses for a noun-phrase prompt and parse the per-frame detections.

        HTTP 200 can still carry an error event; that raises MetaError (not retried).
        Raw events are saved to raw_path (jsonl) when given.
        """
        file_id = self.upload(clip_path)
        body = {"model": config.SAM_MODEL,
                "input": [{"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": prompt}, {"type": "input_video", "file_id": file_id}]}],
                "stream": True, "metadata": {"mask_encoding": "one_bit", "include_confidence": "true"}}
        headers = self._headers({"Content-Type": "application/json", "Accept": "text/event-stream"})
        start = time.time()

        def call():
            events = []
            with httpx.stream("POST", f"{config.META_BASE_URL}/responses", headers=headers, json=body,
                              timeout=TIMEOUT) as r:
                if not r.is_success:
                    self._check("sam", r.status_code, r.read().decode(errors="replace"))
                name = None
                for line in r.iter_lines():
                    if line.startswith("event:"):
                        name = line[6:].strip()
                    elif line.startswith("data:"):
                        raw = line[5:].strip()
                        try:
                            data = json.loads(raw)
                        except ValueError:
                            data = raw
                        events.append({"event": name or (data.get("type") if isinstance(data, dict) else None),
                                       "data": data})
                        name = None
            return events

        events = self._with_retries("sam", call)
        if raw_path:
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(self._redact("\n".join(json.dumps(e) for e in events)) + "\n")
        failed = next((e for e in events if e["event"] in ("error", "response.failed")), None)
        if failed:
            d = failed["data"] if isinstance(failed["data"], dict) else {}
            err = d.get("message") or (d.get("response", {}).get("error") or {}).get("message") or d
            raise MetaError(self._redact(f"sam: stream reported an error: {err}"))
        text = "".join(e["data"].get("delta", "") for e in events
                       if e["event"] == "response.output_text.delta" and isinstance(e["data"], dict))
        completed = next((e["data"] for e in events if e["event"] == "response.completed"), {})
        frames = (completed.get("response", {}).get("usage") or {}).get("video_frames_processed")
        detections = parse_sam_deltas(text)
        cost = (frames or 0) * config.SAM_USD_PER_1000_FRAMES / 1000
        self._charge("sam", prompt, cost)
        return SamCall(prompt=prompt, detections=detections, frames_processed=frames, events=len(events),
                       cost_usd=round(cost, 6), seconds=round(time.time() - start, 2))


class ChatStream:
    """Async iterator over the text deltas of one streamed Spark chat reply (used by ARIA).

    After the stream ends `tokens_in`, `tokens_out`, `cost_usd` and `seconds` are filled in. Any failure is raised as a
    MetaError (the caller turns it into a friendly in-chat message); the key never appears in the message.
    """

    def __init__(self, client: MetaClient, messages: list[dict], model: str):
        self.client, self.messages, self.model = client, messages, model
        self.tokens_in: int | None = None
        self.tokens_out: int | None = None
        self.cost_usd: float = 0.0
        self.seconds: float = 0.0

    async def __aiter__(self):
        start = time.time()
        body = {"model": self.model, "messages": self.messages, "stream": True, "stream_options": {"include_usage": True}}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as http:
                async with http.stream("POST", f"{config.META_BASE_URL}/chat/completions", headers=self.client._headers(), json=body) as r:
                    if r.status_code >= 400:
                        text = (await r.aread()).decode("utf-8", "replace")
                        raise MetaError(self.client._redact(f"chat: HTTP {r.status_code}: {text[:300]}"), status=r.status_code)
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
                            self.tokens_in, self.tokens_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
                        for choice in chunk.get("choices") or []:
                            text = (choice.get("delta") or {}).get("content")
                            if text:
                                yield text
        except MetaError:
            raise
        except httpx.HTTPError as e:
            raise MetaError(self.client._redact(f"chat: {type(e).__name__}: {e}"), timeout=isinstance(e, httpx.TimeoutException)) from None
        finally:
            self.seconds = round(time.time() - start, 2)
            self.cost_usd = round(((self.tokens_in or 0) * config.SPARK_USD_PER_M_IN + (self.tokens_out or 0) * config.SPARK_USD_PER_M_OUT) / 1e6, 6)
            if self.cost_usd:
                self.client._charge("spark", f"{self.model} chat", self.cost_usd)
