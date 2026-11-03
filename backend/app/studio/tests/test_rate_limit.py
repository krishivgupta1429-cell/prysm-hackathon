"""Built during the Meta Global AI Developer Hackathon (new work).

The baseline limiter pushed the expiry back on every request, so a client polling the job status every 3 s hit HTTP 429
after about 6 minutes (120 requests) and stayed blocked. The status poll is now exempt from the global limit, and every
window is fixed from its first request.
"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.middleware import RateLimitMiddleware


class Clock:
    now = 0.0


class TimedRedis:
    """Redis semantics that matter here: INCR, and EXPIRE (re)setting a key's time to live; keys vanish when it elapses."""

    def __init__(self, clock):
        self.clock, self.data, self.exp = clock, {}, {}

    def _alive(self, key):
        if key in self.exp and self.clock.now >= self.exp[key]:
            self.data.pop(key, None)
            self.exp.pop(key, None)

    async def incr(self, key):
        self._alive(key)
        self.data[key] = self.data.get(key, 0) + 1
        return self.data[key]

    async def expire(self, key, seconds):
        self.exp[key] = self.clock.now + seconds
        return True

    def pipeline(self):  # the baseline limiter used a pipeline: kept so these tests also reproduce the old behaviour
        return _Pipe(self)


class _Pipe:
    def __init__(self, redis):
        self.redis, self.ops = redis, []

    def incr(self, key):
        self.ops.append(("incr", key, None))

    def expire(self, key, seconds):
        self.ops.append(("expire", key, seconds))

    async def execute(self):
        out = []
        for op, key, arg in self.ops:
            out.append(await (self.redis.incr(key) if op == "incr" else self.redis.expire(key, arg)))
        return out


@pytest.fixture
def client(monkeypatch):
    clock = Clock()
    redis = TimedRedis(clock)
    import app.core.cache as cache
    monkeypatch.setattr(cache, "redis_client", redis, raising=False)
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/prysm/analyse/status/{job}")
    async def status(job: str):
        return {"ok": True}

    @app.get("/prysm/other")
    async def other():
        return {"ok": True}

    @app.post("/auth/login")
    async def login():
        return {"ok": True}

    c = TestClient(app)
    c.clock = clock
    return c


def test_polling_the_status_every_3_seconds_for_half_an_hour_is_never_limited(client):
    H = {"Authorization": "Bearer abc"}
    for i in range(600):  # 30 minutes at one poll per 3 s
        client.clock.now = i * 3.0
        assert client.get("/prysm/analyse/status/j1", headers=H).status_code == 200, f"blocked at poll {i}"


def test_the_status_endpoint_keeps_its_own_generous_per_user_limit(client):
    H = {"Authorization": "Bearer abc"}
    codes = [client.get("/prysm/analyse/status/j1", headers=H).status_code for _ in range(125)]  # all inside one second
    assert codes[:120] == [200] * 120 and 429 in codes[120:]
    client.clock.now = 61.0   # a new window opens
    assert client.get("/prysm/analyse/status/j1", headers=H).status_code == 200


def test_other_routes_still_hit_the_global_limit_and_recover_when_the_window_ends(client):
    assert all(client.get("/prysm/other").status_code == 200 for _ in range(120))
    assert client.get("/prysm/other").status_code == 429
    client.clock.now = 60.5
    assert client.get("/prysm/other").status_code == 200


def test_a_client_that_keeps_hammering_an_ordinary_route_is_not_blocked_forever(client):
    # baseline bug: requests every 10 s kept resetting the expiry so the counter never reset
    codes = []
    for i in range(200):
        client.clock.now = i * 10.0
        codes.append(client.get("/prysm/other").status_code)
    assert set(codes) == {200}


def test_route_specific_limits_are_unchanged(client):
    assert [client.post("/auth/login").status_code for _ in range(6)] == [200] * 5 + [429]
