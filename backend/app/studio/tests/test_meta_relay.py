"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests for the Meta API relay (scripts/meta_relay.py) and for META_BASE_URL / X-Relay-Secret in the backend: the relay refuses calls without
the shared secret, forwards method, path, query, body and status unchanged, adds the Meta key only when the caller sent none, streams replies and
logs nothing sensitive; the backend reads META_BASE_URL, sends the secret only to a relay (never to Meta) and needs no Meta key when it uses one.
"""
import importlib.util
import logging
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.studio import config
from app.studio.meta_client import MetaClient, MissingKey

BACKEND = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("meta_relay", BACKEND.parent / "scripts" / "meta_relay.py")
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)

SECRET, KEY = "relay-secret-xyz", "meta-key-abc123"


class _Body(httpx.AsyncByteStream):
    def __init__(self, data: bytes):
        self.data = data

    async def __aiter__(self):
        yield self.data


def reply(status=200, data=None, headers=None):
    """A reply the way a real network response arrives: a stream not yet read."""
    import json as _json
    return httpx.Response(status, headers={"content-type": "application/json", **(headers or {})}, stream=_Body(_json.dumps(data or {}).encode()))


def make(handler):
    seen = []

    def wrapped(request: httpx.Request):
        seen.append(request)
        return handler(request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return TestClient(relay.create_app(KEY, SECRET, client)), seen


H = {"X-Relay-Secret": SECRET}


# ------------------------------------------------------------------ the relay

def test_a_call_without_or_with_the_wrong_secret_is_refused_and_never_forwarded():
    c, seen = make(lambda r: reply())
    assert c.get("/v1/models").status_code == 401
    assert c.get("/v1/models", headers={"X-Relay-Secret": "nope"}).status_code == 401
    assert c.post("/anything/else").status_code == 401
    assert seen == []


def test_the_request_is_forwarded_unchanged_with_the_key_added():
    c, seen = make(lambda r: reply(200, {"data": [{"id": "muse-spark-1.3"}]}))
    r = c.post("/v1/chat/completions?x=1", headers={**H, "Content-Type": "application/json"}, content=b'{"model":"muse-spark-1.3"}')
    assert r.status_code == 200 and r.json()["data"][0]["id"] == "muse-spark-1.3"
    u = seen[0]
    assert str(u.url) == "https://api.meta.ai/v1/chat/completions?x=1" and u.method == "POST" and u.content == b'{"model":"muse-spark-1.3"}'
    assert u.headers["authorization"] == f"Bearer {KEY}" and "x-relay-secret" not in u.headers


def test_an_authorization_header_from_the_caller_is_kept():
    c, seen = make(lambda r: reply())
    c.get("/v1/models", headers={**H, "Authorization": "Bearer caller-key"})
    assert seen[0].headers["authorization"] == "Bearer caller-key"


def test_status_codes_pass_through_including_errors():
    c, _ = make(lambda r: reply(404, {"error": {"code": "model_not_found"}}))
    r = c.post("/v1/chat/completions", headers=H, json={})
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"


def test_a_streamed_reply_comes_back_whole():
    chunks = [b"data: one\n\n", b"data: two\n\n", b"data: [DONE]\n\n"]

    async def body():
        for ch in chunks:
            yield ch
    c, _ = make(lambda r: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body()))
    r = c.post("/v1/chat/completions", headers=H, json={"stream": True})
    assert r.content == b"".join(chunks) and r.headers["content-type"].startswith("text/event-stream")


def test_an_unreachable_meta_is_a_plain_502_without_detail():
    def boom(request):
        raise httpx.ConnectError("connection refused to api.meta.ai")
    c, _ = make(boom)
    r = c.get("/v1/models", headers=H)
    assert r.status_code == 502 and "refused" not in r.text


def test_nothing_sensitive_is_logged(caplog):
    c, _ = make(lambda r: reply(200, {"secret_reply": "reply-body-text"}))
    with caplog.at_level(logging.DEBUG):
        c.post("/v1/chat/completions?token=query-secret", headers=H, content=b"prompt-body-text")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "/v1/chat/completions" in text
    for s in (KEY, SECRET, "prompt-body-text", "reply-body-text", "query-secret"):
        assert s not in text


def test_the_relay_will_not_start_without_a_secret():
    with pytest.raises(ValueError):
        relay.create_app(KEY, "")


# ------------------------------------------------------------------ the backend side

def test_meta_base_url_defaults_to_meta_and_the_env_var_overrides_it():
    assert config.DEFAULT_META_BASE_URL == "https://api.meta.ai/v1"
    code = "from app.studio import config; print(config.META_BASE_URL, config.via_relay())"
    run = lambda env: subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, env=env).stdout.split()
    import os
    base = {k: v for k, v in os.environ.items() if k != "META_BASE_URL"}
    if not config._dotenv().get("META_BASE_URL"):
        assert run(base) == ["https://api.meta.ai/v1", "False"]
        assert run({**base, "META_BASE_URL": "https://relay.example.com/v1/"}) == ["https://relay.example.com/v1", "True"]


def test_the_backend_never_hard_codes_the_meta_address_where_it_calls_meta():
    for name in ("meta_client.py", "meta_llm_shim.py", "model_check.py"):
        src = (BACKEND / "app" / "studio" / name).read_text()
        assert "api.meta.ai" not in src, name
    assert "config.META_BASE_URL" in (BACKEND / "app/studio/meta_client.py").read_text()


def test_the_secret_goes_to_a_relay_only_and_a_relay_needs_no_meta_key(monkeypatch):
    env = {"RELAY_SECRET": SECRET}
    monkeypatch.setattr(config, "get_env", lambda name: env.get(name))
    direct = MetaClient(meta_key="k")
    monkeypatch.setattr(config, "META_BASE_URL", config.DEFAULT_META_BASE_URL)
    assert direct._headers() == {"Authorization": "Bearer k"}                       # Meta itself never sees the relay secret
    with pytest.raises(MissingKey):
        MetaClient(meta_key=None)._headers()
    monkeypatch.setattr(config, "META_BASE_URL", "https://relay.example.com/v1")
    assert MetaClient(meta_key=None)._headers() == {"X-Relay-Secret": SECRET}        # the relay adds the Meta key
    assert MetaClient(meta_key="k")._headers({"Accept": "x"}) == {"Accept": "x", "X-Relay-Secret": SECRET, "Authorization": "Bearer k"}


def test_the_relay_secret_is_redacted_from_logged_text(monkeypatch):
    monkeypatch.setattr(config, "get_env", lambda name: {"RELAY_SECRET": SECRET}.get(name))
    assert SECRET not in config.redact(f"failed with {SECRET}") and SECRET not in MetaClient(meta_key="k")._redact(f"x {SECRET}")


def test_production_startup_wants_the_relay_secret_instead_of_the_meta_key_when_using_a_relay(monkeypatch):
    from app.studio import prod_check
    import inspect
    src = inspect.getsource(prod_check)
    assert "via_relay()" in src and "RELAY_SECRET: required because META_BASE_URL points at a relay" in src
