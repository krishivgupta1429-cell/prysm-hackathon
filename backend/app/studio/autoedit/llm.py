"""Built during the Meta Global AI Developer Hackathon (new work).

The model calls of Auto-edit (Spark for planning, revising and scoring; Wonder for the critic) with a cost meter that enforces
AUTO_EDIT_MAX_COST_USD. Uses the existing Meta client (retries, key redaction, price table); adds max_tokens, video input and
JSON parsing.
"""
import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.studio import config as studio_config
from app.studio.autoedit import config
from app.studio.json_tolerant import extract_json_object
from app.studio.meta_client import MetaClient, MetaError, TIMEOUT


class CostCapExceeded(RuntimeError):
    def __init__(self, spent: float, cap: float):
        super().__init__(f"the cost cap of ${cap:.2f} per edit was reached (${spent:.2f} spent)")
        self.spent, self.cap = spent, cap


@dataclass
class CostMeter:
    """Everything one edit has spent, by service. `cap` is the whole-edit limit (revisions included)."""
    cap: float = field(default_factory=config.max_cost_usd)
    entries: list[dict] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(e["usd"] for e in self.entries), 6)

    def add(self, service: str, label: str, usd: float, **extra) -> None:
        self.entries.append({"service": service, "label": label, "usd": round(float(usd), 6), **extra})

    def check(self, estimate: float = 0.0) -> None:
        if self.total + estimate > self.cap + 1e-9:
            raise CostCapExceeded(self.total + estimate, self.cap)

    def by_service(self) -> dict:
        out: dict = {}
        for e in self.entries:
            s = out.setdefault(e["service"], {"usd": 0.0, "calls": 0, "seconds": 0.0})
            s["usd"] = round(s["usd"] + e["usd"], 6)
            s["calls"] += 1
            s["seconds"] = round(s["seconds"] + e.get("seconds", 0.0), 2)
        return out

    def to_json(self) -> dict:
        return {"total_usd": self.total, "cap_usd": self.cap, "by_service": self.by_service(), "entries": self.entries}

    @staticmethod
    def from_json(d: dict | None, cap: float | None = None) -> "CostMeter":
        m = CostMeter(cap=cap if cap is not None else (d or {}).get("cap_usd", config.max_cost_usd()))
        m.entries = list((d or {}).get("entries") or [])
        return m


def parse_json(text: str) -> dict:
    raw = extract_json_object(text)
    try:
        return json.loads(raw)
    except ValueError:
        try:
            from json_repair import repair_json
            return json.loads(repair_json(raw, return_objects=False))
        except Exception as e:
            raise ValueError(f"reply is not valid JSON ({str(e)[:80]})") from None


class EditLLM:
    """Blocking chat calls with a cost meter. `client` is injectable for tests (any object with `chat(content, model, max_tokens)`)."""

    def __init__(self, meter: CostMeter, client: MetaClient | None = None):
        self.meter = meter
        self.client = client or MetaClient()

    # -- low level
    def chat(self, content: list, model: str, max_tokens: int, label: str, est_in_tokens: int = 4000) -> dict:
        pin, pout = studio_config.price_per_m(model)
        self.meter.check((est_in_tokens * pin + 0.4 * max_tokens * pout) / 1e6)
        body = {"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": max_tokens}
        start = time.time()

        def call():
            r = httpx.post(f"{studio_config.META_BASE_URL}/chat/completions", headers=self.client._headers(), json=body, timeout=httpx.Timeout(connect=20.0, read=300.0, write=300.0, pool=20.0))
            self.client._check(label, r.status_code, r.text)
            return r.json()

        j = self.client._with_retries(label, call)
        choice = (j.get("choices") or [{}])[0]
        message = choice.get("message", {}).get("content")
        text = " ".join(p.get("text", "") for p in message if isinstance(p, dict)) if isinstance(message, list) else (message or "")
        usage = j.get("usage") or {}
        tin, tout = usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0
        usd = (tin * pin + tout * pout) / 1e6
        svc = "wonder" if model == studio_config.WONDER_MODEL or "wonder" in model else "spark"
        self.meter.add(svc, label, usd, model=model, tokens_in=tin, tokens_out=tout, seconds=round(time.time() - start, 2),
                       finish=choice.get("finish_reason"))
        return {"text": text, "usd": usd, "finish": choice.get("finish_reason")}

    # -- helpers
    def json_call(self, prompt: str, model: str, label: str, *, max_tokens: int = 12000, images: list[bytes] | None = None,
                  videos: list[Path] | None = None, est_in_tokens: int = 4000) -> dict:
        content: list = [{"type": "text", "text": prompt}]
        for img in images or []:
            content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(img).decode()}})
        for v in videos or []:
            content.append({"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + base64.b64encode(Path(v).read_bytes()).decode()}})
        r = self.chat(content, model, max_tokens, label, est_in_tokens)
        try:
            return parse_json(r["text"])
        except ValueError as e:
            if r["finish"] == "length":
                raise ValueError(f"{label}: the reply was cut off at the token limit") from None
            raise ValueError(f"{label}: {e}") from None
