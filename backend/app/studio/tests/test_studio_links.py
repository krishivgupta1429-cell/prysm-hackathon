"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests for STUDIO_ALLOW_LINKS (code default off): with the studio engine on and links off, a link request is
rejected with a clear message before anything is downloaded; the legacy engine and links-on keep the baseline path.
"""
import asyncio

import pytest
from fastapi import HTTPException

from app.core.config import Settings, settings
from app.routers import prysm as router
from app.studio.tests.fake_redis import FakeRedis
from app.studio.tests.test_studio_engine_switch import FakeSession, FakeUser


class Reached(Exception):
    """The fake user lookup raises this; the route turns it into a 401, which shows the request got past the link gate."""


def call_link(monkeypatch, url="https://www.instagram.com/reel/abc123/"):
    async def fake_user(token, db):
        raise Reached()

    monkeypatch.setattr(router, "get_current_user_optional", fake_user)
    monkeypatch.setattr("app.core.cache.redis_client", FakeRedis())
    return asyncio.run(router.analyse_video(video=None, url=url, niche="Fashion", platform="Instagram Reels", brief="", contentGoal="",
                                            intent="competitor", authorization="Bearer x", db=FakeSession(FakeUser(id="u1"), [])))


def test_the_flag_defaults_to_off():
    assert Settings.model_fields["STUDIO_ALLOW_LINKS"].default is False


def test_studio_engine_without_links_rejects_a_link_with_a_clear_message(monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", "studio"); monkeypatch.setattr(settings, "STUDIO_ALLOW_LINKS", False)
    with pytest.raises(HTTPException) as e:
        call_link(monkeypatch)
    assert e.value.status_code == 400 and e.value.detail.startswith("Links are not available in this build")
    assert "studio" not in e.value.detail.lower()


@pytest.mark.parametrize("engine,allow", [("studio", True), ("legacy", False)])
def test_links_still_reach_the_baseline_path_when_allowed_or_on_the_legacy_engine(monkeypatch, engine, allow):
    monkeypatch.setattr(settings, "ANALYSIS_ENGINE", engine); monkeypatch.setattr(settings, "STUDIO_ALLOW_LINKS", allow)
    with pytest.raises(HTTPException) as e:  # past the link gate, on to authentication (the fake user lookup fails: 401)
        call_link(monkeypatch)
    assert e.value.status_code == 401


def test_the_website_hides_the_link_option_in_demo_mode_unless_enabled():
    from pathlib import Path
    root = Path(__file__).resolve().parents[4] / "web" / "src"
    config = (root / "lib" / "config.ts").read_text()
    assert "ALLOW_LINKS: boolean = !STUDIO_MODE || env.VITE_STUDIO_ALLOW_LINKS === '1'" in config
    assert "ALLOW_LINKS &&" in (root / "pages" / "Analyse.tsx").read_text()
    assert "<Navigate to=\"/analyse/upload\"" in (root / "App.tsx").read_text()
