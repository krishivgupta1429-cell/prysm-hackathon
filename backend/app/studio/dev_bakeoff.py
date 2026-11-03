"""Built during the Meta Global AI Developer Hackathon (new work).

Local-only endpoints (DEV_BAKEOFF=1) that serve the saved blind bake-off variants to the /dev/bakeoff page. No model is
called. Variants are labelled only X / Y / Z per video; model names, costs, latencies and tokens are returned by the
separate /reveal call only. Requests that do not come from this machine are refused (404), and the router is only
mounted when the flag is on.
"""
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = ROOT / "samples" / "_local" / "bakeoff"
KEY_FILE = ROOT / "docs" / "bakeoff-key.md"
VIDEOS = ("dance", "reel", "saree")
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}

router = APIRouter(prefix="/dev/bakeoff", tags=["dev-bakeoff"])


def _local_only(request: Request) -> None:
    host = (request.client.host if request.client else "") or ""
    hdr = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in LOCAL_HOSTS or hdr not in LOCAL_HOSTS or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip"):
        raise HTTPException(status_code=404, detail="Not found")


def _key_rows() -> dict:
    """{video: {letter: (label, model, style_on)}} from docs/bakeoff-key.md (the letters the founder sees)."""
    out, video = {}, None
    for line in KEY_FILE.read_text().splitlines():
        m = re.match(r"## (\w+)\s*$", line)
        if m:
            video = m.group(1)
            out[video] = {}
            continue
        m = re.match(r"\|\s*([XYZ])\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line)
        if m and video:
            out[video][m.group(1)] = (m.group(2), m.group(3), m.group(4).strip() != "off")
    return out


def _load() -> dict:
    """{video: {letter: saved record}}: each saved variant matched to the letter the key file gives it."""
    saved = {}
    for p in RAW_DIR.glob("*_*_*.json"):
        rec = json.loads(p.read_text())
        if "result" in rec:
            saved.setdefault(rec["video"], []).append(rec)
    keys = _key_rows()
    out = {}
    for video in VIDEOS:
        out[video] = {}
        for letter, (_label, model, style_on) in (keys.get(video) or {}).items():
            for rec in saved.get(video, []):
                if rec["stats"]["model"] == model and bool(rec["stats"]["style"]) == style_on:
                    out[video][letter] = rec
    return out


def _page_result(rec: dict) -> dict:
    """The saved scoring result as the results page reads it (nothing about the model, cost or time)."""
    r = json.loads(json.dumps(rec["result"]))
    for k in list(r):
        if k.startswith("_") or k in ("modelsUsed", "frames", "audioAnalysis"):
            r.pop(k, None)
    r.setdefault("intent", "my_video")
    r.setdefault("source_type", "upload")
    return r


@router.get("")
async def list_variants(request: Request):
    _local_only(request)
    data = _load()
    return {"videos": {v: {"variants": {l: _page_result(rec) for l, rec in sorted(letters.items())}} for v, letters in data.items()}}


@router.get("/reveal")
async def reveal(request: Request):
    _local_only(request)
    keys, data = _key_rows(), _load()
    out = {}
    for video, letters in data.items():
        out[video] = {}
        for letter, rec in sorted(letters.items()):
            s = rec["stats"]
            out[video][letter] = {"variant": keys[video][letter][0], "model": s["model"], "style": "COACH_STYLE=1" if s["style"] else "off",
                                  "cost_usd": s["cost_usd"], "latency_s": s["latency_s"], "tokens_in": s["tokens_in"],
                                  "tokens_out": s["tokens_out"], "reasoning_tokens": s["reasoning_tokens"]}
    return {"key": out}
