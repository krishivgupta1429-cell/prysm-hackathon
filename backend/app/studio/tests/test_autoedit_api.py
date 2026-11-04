"""Built during the Meta Global AI Developer Hackathon (new work).

The Auto-edit HTTP API with the database, Redis and the background job faked: eligibility (uploads only), one running edit per user,
the hourly limit, the 5-revision limit, ownership, downloads, delete, and that the flag defaults to off."""
import json
import types
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.studio.autoedit import api, edl as E, orchestrator as O, storage as ST
from app.studio.tests.autoedit_helpers import make_clip, make_inputs

USER = types.SimpleNamespace(id=uuid.uuid4(), email="me@example.org")
OTHER = types.SimpleNamespace(id=uuid.uuid4(), email="other@example.org")


class FakeRedis:
    def __init__(self):
        self.kv = {}

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def get(self, k):
        return self.kv.get(k)

    async def incrbyfloat(self, k, v):
        self.kv[k] = float(self.kv.get(k, 0)) + v
        return self.kv[k]

    async def delete(self, k):
        self.kv.pop(k, None)

    async def incr(self, k):
        self.kv[k] = int(self.kv.get(k, 0)) + 1
        return self.kv[k]

    async def expire(self, k, s):
        return True


class FakeDB:
    def __init__(self, rec):
        self.rec = rec

    async def execute(self, stmt):
        rec = self.rec

        class R:
            def scalar_one_or_none(self_inner):
                return rec
        return R()


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    store = ST.LocalStorage(tmp_path / "store")
    ST.set_storage(store)
    aid = uuid.uuid4()
    rec = types.SimpleNamespace(id=aid, user_id=USER.id, source_type="upload", raw_result={"niche": "Fashion", "parameters": []}, niche="Fashion")
    redis = FakeRedis()
    monkeypatch.setattr(api, "redis_client", redis)
    spawned = []
    monkeypatch.setattr(api, "_spawn", lambda coro: (spawned.append(coro), coro.close()))
    monkeypatch.setattr(api, "sweep_expired", lambda: 0)
    app = FastAPI()
    app.include_router(api.router, prefix="/prysm/auto-edit")
    holder = {"user": USER, "rec": rec}
    from app.core.database import get_db
    from app.core.security import get_current_user
    app.dependency_overrides[get_current_user] = lambda: holder["user"]
    app.dependency_overrides[get_db] = lambda: FakeDB(holder["rec"])
    api._LIVE.clear()
    yield types.SimpleNamespace(client=TestClient(app), store=store, aid=str(aid), rec=rec, redis=redis, spawned=spawned, holder=holder, tmp=tmp_path)
    ST.set_storage(None)
    api._LIVE.clear()


def keep_original(ctx, user=USER):
    ctx.store.put_file(ST.upload_key(str(user.id), ctx.aid), make_clip(ctx.tmp / "o.mp4", 2.0))


def test_the_flag_defaults_to_off_and_the_baseline_step_needs_its_own_flag():
    from app.core.config import Settings
    assert Settings.model_fields["AUTO_EDIT_ENABLED"].default is False and Settings.model_fields["AUTO_EDIT_LEGACY"].default is False


def test_status_says_not_eligible_without_a_stored_original_and_for_links(ctx):
    r = ctx.client.get(f"/prysm/auto-edit/{ctx.aid}/status").json()
    assert r["eligible"] is False and "no longer stored" in r["reason"]
    ctx.rec.source_type = "link"
    r = ctx.client.get(f"/prysm/auto-edit/{ctx.aid}/status").json()
    assert r["eligible"] is False and "uploaded" in r["reason"]
    assert ctx.client.post(f"/prysm/auto-edit/{ctx.aid}/start", json={}).status_code == 400


def test_start_creates_a_running_state_and_a_second_start_is_refused(ctx):
    keep_original(ctx)
    r = ctx.client.post(f"/prysm/auto-edit/{ctx.aid}/start", json={"preset": "energetic"})
    assert r.status_code == 200 and r.json()["preset"] == "energetic" and r.json()["running"] is True and len(ctx.spawned) == 1
    again = ctx.client.post(f"/prysm/auto-edit/{ctx.aid}/start", json={})
    assert again.status_code == 409


def test_only_one_running_edit_per_user_even_across_analyses(ctx):
    keep_original(ctx)
    ctx.redis.kv[f"autoedit:run:{USER.id}"] = "1"   # another edit of this user is running
    r = ctx.client.post(f"/prysm/auto-edit/{ctx.aid}/start", json={})
    assert r.status_code == 409 and "already have an edit running" in r.json()["detail"]


def test_hourly_start_limit(ctx):
    keep_original(ctx)
    for i in range(6):
        ctx.redis.kv.pop(f"autoedit:run:{USER.id}", None)
        api._LIVE.clear()
        assert ctx.client.post(f"/prysm/auto-edit/{ctx.aid}/start", json={}).status_code == 200
    ctx.redis.kv.pop(f"autoedit:run:{USER.id}", None)
    api._LIVE.clear()
    assert ctx.client.post(f"/prysm/auto-edit/{ctx.aid}/start", json={}).status_code == 429


def test_unknown_preset_is_a_400(ctx):
    keep_original(ctx)
    assert ctx.client.post(f"/prysm/auto-edit/{ctx.aid}/start", json={"preset": "wild"}).status_code == 400


def finished_state(ctx, revisions=0):
    st = O.new_state(ctx.aid, str(USER.id))
    inp = make_inputs()
    edl = E.validate_edl({"ops": [{"id": "o1", "type": "zoom_punch", "group": "zoom", "reason": "r", "params": {"at": 2.0, "scale": 1.1}},
                                  {"id": "o2", "type": "caption_burn", "group": "text", "reason": "c", "params": {}}]}, inp).edl
    st.update(status="done", edl=edl.model_dump(), groups={"zoom": {"label": "Zoom punches", "status": "kept", "reasons": ["Visual +3"]}},
              revisions=[{"text": f"r{i}", "summary": [], "at": ""} for i in range(revisions)])
    ctx.store.put_bytes(O.state_key(ctx.aid), json.dumps(st).encode())
    ctx.store.put_bytes(f"edits/{ctx.aid}/inputs.json", json.dumps(inp.to_json()).encode())
    return st


def test_status_returns_changes_with_labels_verdicts_and_revisions_left(ctx):
    finished_state(ctx, revisions=2)
    s = ctx.client.get(f"/prysm/auto-edit/{ctx.aid}/status").json()["state"]
    zoom = next(c for c in s["changes"] if c["id"] == "o1")
    assert zoom["label"].startswith("Zoom punch at 2.0 s") and zoom["verdict"] == "kept" and zoom["enabled"]
    assert s["revisions_left"] == 3 and set(s["presets"]) == {"clean", "energetic", "minimal"}


def test_toggle_flips_one_change_and_starts_a_rerender(ctx):
    finished_state(ctx)
    r = ctx.client.post(f"/prysm/auto-edit/{ctx.aid}/toggle", json={"op_id": "o1", "enabled": False})
    assert r.status_code == 200 and len(ctx.spawned) == 1
    c = next(c for c in r.json()["changes"] if c["id"] == "o1")
    assert c["enabled"] is False and c["user_override"] is True
    assert ctx.client.post(f"/prysm/auto-edit/{ctx.aid}/toggle", json={"op_id": "zzz", "enabled": True}).status_code in (404, 409)


def test_the_font_picker_searches_the_catalogue_by_name_and_category(ctx):
    r = ctx.client.get("/prysm/auto-edit/fonts", params={"q": "baloo", "category": "devanagari"}).json()
    assert "devanagari" in r["categories"] and r["fonts"] and all(f["devanagari"] and "baloo" in f["family"].lower() for f in r["fonts"])
    assert ctx.client.get("/prysm/auto-edit/fonts", params={"category": "nope"}).status_code == 400


def test_the_style_endpoint_changes_pack_fonts_intensity_and_sfx_then_rerenders(ctx):
    finished_state(ctx)
    base = f"/prysm/auto-edit/{ctx.aid}/style"
    r = ctx.client.post(base, json={"pack": "fitness", "intensity": "punchy", "sfx": False})
    assert r.status_code == 200 and len(ctx.spawned) == 1
    body = r.json()
    assert body["style"] == {"pack": "fitness", "font_headline": "Bebas Neue", "font_caption": "Oswald", "animation": "slide", "intensity": "punchy", "sfx": False, "pack_source": "your choice"}
    assert "fitness" in body["packs"] and body["intensities"] == ["subtle", "balanced", "punchy"]
    ctx.redis.kv.clear()
    api._LIVE.clear()
    st = json.loads(ctx.store.get_bytes(O.state_key(ctx.aid)))
    st["status"] = "done"
    ctx.store.put_bytes(O.state_key(ctx.aid), json.dumps(st).encode())
    assert ctx.client.post(base, json={"font_caption": "Not A Font"}).status_code == 400
    assert ctx.client.post(base, json={"pack": "nope"}).status_code == 400
    assert ctx.client.post(base, json={"intensity": "max"}).status_code == 400


def test_a_sound_accent_can_be_swapped_for_another_effect_that_suits_its_moment(ctx):
    st = finished_state(ctx)
    st["edl"]["ops"].append({"id": "fx1", "type": "sfx_hit", "group": "sound", "enabled": True, "reason": "", "finding": "sag", "target_parameter": "", "source_step_id": None,
                             "params": {"at": 4.0, "kind": "funny", "effect": "scratch"}})
    ctx.store.put_bytes(O.state_key(ctx.aid), json.dumps(st).encode())
    base = f"/prysm/auto-edit/{ctx.aid}/swap"
    assert ctx.client.post(base, json={"op_id": "fx1", "effect": "ring"}).status_code == 400
    r = ctx.client.post(base, json={"op_id": "fx1", "effect": "womp"})
    assert r.status_code == 200 and len(ctx.spawned) == 1
    c = next(c for c in r.json()["changes"] if c["id"] == "fx1")
    assert "womp" in c["label"] and c["effect_options"] == ["scratch", "womp", "boing", "toot"] and c["finding"] == "sag"
    assert ctx.client.post(base, json={"op_id": "o1", "effect": "womp"}).status_code in (404, 409)


def test_a_preset_change_keeps_the_pack_and_the_creators_font_choices(ctx):
    st = finished_state(ctx)
    st["style_overrides"] = {"pack": "comedy", "font_caption": "Lora", "caption_size": 99}
    ctx.store.put_bytes(O.state_key(ctx.aid), json.dumps(st).encode())
    r = ctx.client.post(f"/prysm/auto-edit/{ctx.aid}/preset", json={"preset": "energetic"})
    assert r.status_code == 200 and r.json()["style"]["pack"] == "comedy" and r.json()["style"]["font_caption"] == "Lora"
    assert "caption_size" not in r.json()["style_overrides"]           # size tweaks are per-preset and reset


def test_revision_is_applied_and_the_sixth_is_refused(ctx):
    finished_state(ctx)
    r = ctx.client.post(f"/prysm/auto-edit/{ctx.aid}/revise", json={"text": "bigger captions"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revisions"][-1]["text"] == "bigger captions" and body["revisions"][-1]["summary"]
    ctx.redis.kv.clear()
    api._LIVE.clear()
    finished_state(ctx, revisions=5)
    assert ctx.client.post(f"/prysm/auto-edit/{ctx.aid}/revise", json={"text": "less zoom"}).status_code == 429


def test_an_unparseable_revision_is_a_422_with_help_and_costs_nothing_without_spark(ctx, monkeypatch):
    finished_state(ctx)
    monkeypatch.setattr(api.revise, "revise_with_spark", lambda *a, **k: api.revise.Diff(resolved=False))
    r = ctx.client.post(f"/prysm/auto-edit/{ctx.aid}/revise", json={"text": "make it cinematic"})
    assert r.status_code == 422 and "bigger captions" in r.json()["detail"]


def test_downloads_are_owner_only_and_missing_files_are_404(ctx):
    finished_state(ctx)
    assert ctx.client.get(f"/prysm/auto-edit/{ctx.aid}/download/mp4").status_code == 404            # no render yet
    ctx.store.put_bytes(f"edits/{ctx.aid}/edl.json", b'{"ok": true}')
    r = ctx.client.get(f"/prysm/auto-edit/{ctx.aid}/download/edl")
    assert r.status_code == 200 and r.json() == {"ok": True}
    keep_original(ctx)
    assert ctx.client.get(f"/prysm/auto-edit/{ctx.aid}/download/original").status_code == 200
    ctx.holder["user"] = OTHER
    ctx.rec.user_id = USER.id
    ctx.holder["rec"] = None                                    # the analysis is not theirs
    assert ctx.client.get(f"/prysm/auto-edit/{ctx.aid}/download/edl").status_code == 404
    assert ctx.client.get(f"/prysm/auto-edit/{ctx.aid}/download/nonsense").status_code == 404


def test_delete_removes_the_edit_and_the_stored_upload(ctx):
    finished_state(ctx)
    keep_original(ctx)
    r = ctx.client.delete(f"/prysm/auto-edit/{ctx.aid}")
    assert r.status_code == 200 and r.json()["deleted"] and r.json()["files"] >= 3
    assert ctx.store.list("edits/") == [] and ctx.store.list("uploads/") == []


def test_retain_upload_keeps_short_videos_only(ctx, tmp_path):
    short = make_clip(tmp_path / "s.mp4", 2.0)
    assert api.retain_upload("u1", "a1", str(short)) is True and ctx.store.exists("uploads/u1/a1/original.mp4")
    assert ctx.store.get_bytes("uploads/u1/a1/original.mp4") == short.read_bytes()   # a byte-for-byte copy: the original is untouched
    long = make_clip(tmp_path / "l.mp4", 95.0, size="90x160", fps=5, audio=False)
    assert api.retain_upload("u1", "a2", str(long)) is False and not ctx.store.exists("uploads/u1/a2/original.mp4")


def test_cost_endpoint_is_hidden_unless_the_founder_or_local_flag(ctx, monkeypatch):
    finished_state(ctx)
    monkeypatch.setattr("app.studio.cost_access.cost_visible", lambda email: False)
    assert ctx.client.get(f"/prysm/auto-edit/{ctx.aid}/cost").status_code == 404
    monkeypatch.setattr("app.studio.cost_access.cost_visible", lambda email: True)
    assert ctx.client.get(f"/prysm/auto-edit/{ctx.aid}/cost").status_code == 200
