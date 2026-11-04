"""Built during the Meta Global AI Developer Hackathon (new work).

A tiny relay for the Meta API. Meta rejects the key when a request comes from some cloud servers (Railway) but accepts it from a home connection,
so the deployed backend can send its Meta calls here instead (META_BASE_URL=<this relay's public url>/v1) and this relay forwards them from the
machine where the key works. See docs/meta-relay.md.

  RELAY_SECRET=<long random string> python scripts/meta_relay.py [--port 8790]

  * Every request needs the header  X-Relay-Secret: <RELAY_SECRET>  (anything else is answered 401 and never forwarded).
  * /v1/<anything> is forwarded unchanged (method, query, body, headers) to https://api.meta.ai/v1/<anything>; the status code and the (streamed)
    reply come back unchanged.
  * If the incoming request has no Authorization header, the relay adds one from META_API_KEY in backend/.env (or the environment).
  * Nothing sensitive is logged: no keys, no headers, no bodies, no query strings; only "METHOD /path -> status in N s".
"""
from __future__ import annotations

import argparse
import hmac
import logging
import os
import sys
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

UPSTREAM = "https://api.meta.ai/v1"
ENV_PATH = Path(__file__).resolve().parents[1] / "backend" / ".env"
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}
DROP_REQUEST = HOP_BY_HOP | {"host", "content-length", "x-relay-secret", "accept-encoding"}
DROP_RESPONSE = HOP_BY_HOP | {"content-length"}

log = logging.getLogger("meta_relay")


def local_meta_key() -> str | None:
    """META_API_KEY from backend/.env, else the environment."""
    value = None
    if ENV_PATH.exists():
        from dotenv import dotenv_values
        value = dotenv_values(ENV_PATH).get("META_API_KEY")
    return (value or os.environ.get("META_API_KEY") or "").strip() or None


def create_app(meta_key: str | None, secret: str, client: httpx.AsyncClient | None = None, upstream: str = UPSTREAM) -> FastAPI:
    if not secret:
        raise ValueError("RELAY_SECRET must not be empty")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx logs full URLs (query strings included) at INFO
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def require_secret(request: Request, call_next):
        sent = request.headers.get("x-relay-secret", "")
        if not hmac.compare_digest(sent.encode(), secret.encode()):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    async def forward(path: str, request: Request):
        started = time.time()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_REQUEST}
        if "authorization" not in {k.lower() for k in headers}:
            if not meta_key:
                return JSONResponse({"error": "relay has no Meta key"}, status_code=500)
            headers["Authorization"] = f"Bearer {meta_key}"
        url = f"{upstream}/{path}"
        if request.url.query:
            url += f"?{request.url.query}"
        try:
            req = http.build_request(request.method, url, headers=headers, content=await request.body())
            resp = await http.send(req, stream=True)
        except httpx.HTTPError as e:
            log.warning("%s /v1/%s -> upstream error %s", request.method, path, type(e).__name__)
            return JSONResponse({"error": "relay could not reach Meta"}, status_code=502)
        log.info("%s /v1/%s -> %s in %.1fs", request.method, path, resp.status_code, time.time() - started)
        out = {k: v for k, v in resp.headers.items() if k.lower() not in DROP_RESPONSE}
        return StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, headers=out, background=BackgroundTask(resp.aclose))

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=int(os.environ.get("RELAY_PORT", "8790")))
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    secret = (os.environ.get("RELAY_SECRET") or "").strip()
    if not secret:
        sys.exit("RELAY_SECRET is not set. Example: RELAY_SECRET=$(openssl rand -hex 32) python scripts/meta_relay.py")
    key = local_meta_key()
    if not key:
        sys.exit("META_API_KEY not found in backend/.env or the environment")
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    uvicorn.run(create_app(key, secret), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
