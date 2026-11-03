"""Built during the Meta Global AI Developer Hackathon (new work). The local-only bake-off review endpoints."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.studio import dev_bakeoff as db


@pytest.fixture
def client(tmp_path, monkeypatch):
    key = ["# Bake-off key", ""]
    for v, order in {"dance": ("spark13_style", "wonder_style", "spark13_plain"), "reel": ("wonder_style", "spark13_plain", "spark13_style"),
                     "saree": ("spark13_plain", "spark13_style", "wonder_style")}.items():
        key += [f"## {v}", "Inputs: `x.json`", "", "| Letter | Variant | Model | Style |", "|---|---|---|---|"]
        spec = {"spark13_style": ("Spark 1.3 + COACH_STYLE", "muse-spark-1.3", "COACH_STYLE=1"),
                "spark13_plain": ("Spark 1.3, no style", "muse-spark-1.3", "off"),
                "wonder_style": ("Wonder + COACH_STYLE", "muse-wonder-1.0", "COACH_STYLE=1")}
        for letter, vid in zip("XYZ", order):
            key.append(f"| {letter} | {spec[vid][0]} | {spec[vid][1]} | {spec[vid][2]} |")
            (tmp_path / f"{v}_{vid}.json").write_text(json.dumps({
                "video": v, "variant": vid, "result": {"parameters": [{"name": "Hook & Opening", "score": 60}], "overallScore": 60,
                                                       "_cost": {"x": 1}, "modelsUsed": [1]},
                "stats": {"model": spec[vid][1], "style": spec[vid][2] != "off", "cost_usd": 0.1, "latency_s": 5, "tokens_in": 1, "tokens_out": 2, "reasoning_tokens": 1}}))
        key.append("")
    kf = tmp_path / "key.md"
    kf.write_text("\n".join(key))
    monkeypatch.setattr(db, "RAW_DIR", tmp_path)
    monkeypatch.setattr(db, "KEY_FILE", kf)
    monkeypatch.setattr(db, "LOCAL_HOSTS", {"127.0.0.1", "::1", "localhost", "testclient"})  # the test client's peer name
    app = FastAPI()
    app.include_router(db.router)
    return TestClient(app, base_url="http://127.0.0.1:8775")


def test_the_listing_has_three_letters_per_video_and_no_model_cost_or_time(client):
    r = client.get("/dev/bakeoff")
    assert r.status_code == 200
    body = r.json()
    assert set(body["videos"]) == {"dance", "reel", "saree"}
    assert all(set(v["variants"]) == {"X", "Y", "Z"} for v in body["videos"].values())
    text = r.text.lower()
    for banned in ["muse-", "wonder", "spark", "cost", "latency", "token", "coach_style", "_cost", "modelsused"]:
        assert banned not in text, banned


def test_the_reveal_gives_the_key_cost_and_time_per_letter(client):
    key = client.get("/dev/bakeoff/reveal").json()["key"]
    assert key["dance"]["X"]["model"] == "muse-spark-1.3" and key["dance"]["X"]["style"] == "COACH_STYLE=1"
    assert key["reel"]["X"]["model"] == "muse-wonder-1.0" and key["saree"]["Z"]["model"] == "muse-wonder-1.0"
    assert key["dance"]["Y"]["cost_usd"] == 0.1 and key["dance"]["Y"]["latency_s"] == 5


def test_requests_that_are_not_from_this_machine_are_refused(client, monkeypatch):
    app = FastAPI()
    app.include_router(db.router)
    other_host = TestClient(app, base_url="http://example.com")                       # a public Host header
    assert other_host.get("/dev/bakeoff").status_code == 404 and other_host.get("/dev/bakeoff/reveal").status_code == 404
    assert client.get("/dev/bakeoff", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 404   # behind a proxy
    monkeypatch.setattr(db, "LOCAL_HOSTS", {"127.0.0.1", "::1", "localhost"})        # the peer is not a loopback address
    assert client.get("/dev/bakeoff").status_code == 404


def test_the_router_is_only_mounted_when_the_flag_is_on():
    import inspect
    from app import main
    from app.core.config import Settings
    assert Settings.model_fields["DEV_BAKEOFF"].default is False
    assert "if settings.DEV_BAKEOFF" in inspect.getsource(main)
