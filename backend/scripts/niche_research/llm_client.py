"""OpenAI-compatible LLM client.

Works against:
  - Ollama  (http://localhost:11434/v1)  — standard OpenAI SDK, synchronous
  - vLLM local (http://localhost:8000/v1) — standard OpenAI SDK, synchronous
  - RunPod serverless vLLM               — native RunPod queue API with openai_route
    Detected automatically when LLM_BASE_URL contains 'api.runpod.ai'

The client is vision-capable: pass `image_paths` to score frames.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests
from openai import OpenAI

from .config import SETTINGS

log = logging.getLogger(__name__)

_client: Optional[OpenAI] = None


def _is_runpod() -> bool:
    return "api.runpod.ai" in (SETTINGS.llm_base_url or "")


def _runpod_endpoint_id() -> str:
    # URL format: https://api.runpod.ai/v2/<endpoint_id>/openai/v1
    # split("/") → ['https:', '', 'api.runpod.ai', 'v2', '<endpoint_id>', ...]
    parts = SETTINGS.llm_base_url.split("/")
    return parts[4] if len(parts) > 4 else ""


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=SETTINGS.llm_base_url,
            api_key=SETTINGS.llm_api_key or "ollama",
            timeout=SETTINGS.llm_timeout_seconds,
        )
        log.info("LLM client ready: backend=%s url=%s model=%s",
                 SETTINGS.llm_backend, SETTINGS.llm_base_url, SETTINGS.llm_model)
    return _client


def _img_data_url(path: Path | str) -> str:
    p = Path(path)
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f"data:image/jpeg;base64,{b64}"


# ── RunPod native API ─────────────────────────────────────────────────────────

def _runpod_call(openai_input: Dict[str, Any]) -> str:
    """Submit a request via RunPod's openai_route proxy and poll until done."""
    endpoint_id = _runpod_endpoint_id()
    api_key = SETTINGS.llm_api_key
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    base = f"https://api.runpod.ai/v2/{endpoint_id}"

    payload = {"input": {"openai_route": "/v1/chat/completions", "openai_input": openai_input}}

    # Try runsync first (waits up to 90s synchronously)
    r = requests.post(f"{base}/runsync", headers=headers, json=payload, timeout=100)
    r.raise_for_status()
    data = r.json()

    job_id = data.get("id")
    status = data.get("status")

    # If completed inline (warm worker)
    if status == "COMPLETED":
        return _extract_runpod_text(data)

    if status == "FAILED":
        raise RuntimeError(f"RunPod job failed: {data.get('error')}")

    # Poll until done (cold start or queue)
    if not job_id:
        raise RuntimeError(f"No job ID returned: {data}")

    deadline = time.time() + SETTINGS.llm_timeout_seconds
    poll_interval = 5
    while time.time() < deadline:
        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.5, 30)
        r2 = requests.get(f"{base}/status/{job_id}", headers=headers, timeout=30)
        r2.raise_for_status()
        d = r2.json()
        s = d.get("status")
        if s == "COMPLETED":
            return _extract_runpod_text(d)
        if s in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"RunPod job {s}: {d.get('error')}")

    raise TimeoutError(f"RunPod job {job_id} timed out after {SETTINGS.llm_timeout_seconds}s")


def _extract_runpod_text(data: Dict[str, Any]) -> str:
    """Pull the assistant text out of a completed RunPod openai_route response."""
    output = data.get("output")
    if isinstance(output, list) and output:
        output = output[0]
    if isinstance(output, dict):
        # Standard OpenAI chat completion format
        choices = output.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            if msg.get("content"):
                return msg["content"]
            # Streaming token array fallback
            tokens = choices[0].get("tokens") or choices[0].get("text")
            if isinstance(tokens, list):
                return "".join(tokens)
            if isinstance(tokens, str):
                return tokens
    return str(output or "")


# ── Ollama native API (bypasses OpenAI-compat path for correct think=false) ───

def _is_ollama() -> bool:
    return SETTINGS.llm_backend == "ollama"


def _ollama_base_url() -> str:
    # Strip /v1 suffix that the OpenAI-compat path expects
    base = (SETTINGS.llm_base_url or "http://localhost:11434/v1").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


def _ollama_chat(messages: List[Dict[str, Any]], model: str, temperature: float,
                 max_tokens: int) -> str:
    """Call Ollama's native /api/chat with think=false so thinking tokens stay separate."""
    url = _ollama_base_url() + "/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "repeat_penalty": 1.3,  # prevent repetition loops in onScreenText fields
        },
    }
    timeout = SETTINGS.llm_timeout_seconds or 600
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data.get("message", {}).get("content", "")


# ── Public API ────────────────────────────────────────────────────────────────

def chat_with_vision(
    system_prompt: str,
    user_prompt: str,
    image_paths: Iterable[Path | str] = (),
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    m = model or SETTINGS.llm_model
    t = temperature if temperature is not None else SETTINGS.llm_temperature
    mt = max_tokens if max_tokens is not None else SETTINGS.llm_max_tokens

    if _is_runpod():
        content: List[Dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for p in image_paths:
            content.append({"type": "image_url", "image_url": {"url": _img_data_url(p)}})
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]
        return _runpod_call({"model": m, "messages": messages, "temperature": t, "max_tokens": mt})

    if _is_ollama():
        # Native Ollama API: images go in the message's `images` list as raw base64
        images_b64 = [base64.b64encode(Path(p).read_bytes()).decode() for p in image_paths]
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt, "images": images_b64},
        ]
        return _ollama_chat(messages, m, t, mt)

    # Generic OpenAI-compatible path (vLLM, etc.)
    content_list: List[Dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    for p in image_paths:
        content_list.append({"type": "image_url", "image_url": {"url": _img_data_url(p)}})
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content_list},
    ]
    client = get_client()
    resp = client.chat.completions.create(model=m, temperature=t, max_tokens=mt, messages=messages)
    return resp.choices[0].message.content or ""


def chat_text(
    system_prompt: str,
    user_prompt: str,
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    m = model or SETTINGS.llm_model
    t = temperature if temperature is not None else SETTINGS.llm_temperature
    mt = max_tokens if max_tokens is not None else SETTINGS.llm_max_tokens

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    if _is_runpod():
        return _runpod_call({"model": m, "messages": messages, "temperature": t, "max_tokens": mt})

    if _is_ollama():
        return _ollama_chat(messages, m, t, mt)

    client = get_client()
    resp = client.chat.completions.create(model=m, temperature=t, max_tokens=mt, messages=messages)
    return resp.choices[0].message.content or ""


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(raw: str) -> Dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = _FENCE_RE.search(raw)
    if m:
        inner = m.group(1).strip()
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidate = raw[start: end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    log.warning("Could not parse LLM JSON; returning empty dict. Snippet: %s", raw[:200])
    return {}
