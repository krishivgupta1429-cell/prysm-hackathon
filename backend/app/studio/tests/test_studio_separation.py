"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests for the separation clean-ups and demo flags: DEMO_AUTO_VERIFY is off by default and, when on, verifies a new
account at once; PROMOTE_EMAILS and the CORS/CSP settings default to nothing production-specific;
billing answers 503 when Razorpay keys are unset; and no
production identifier is left in the backend or website sources.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.config import Settings, settings
from app.routers import auth, billing

HOUR = 3600


# ---------------------------------------------------------------- demo flags in registration

class Result:
    def scalar_one_or_none(self):
        return None


class RegDB:
    def __init__(self):
        self.added = []

    async def execute(self, stmt):
        return Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass


class NoRedis:
    async def setex(self, *a, **k):
        pass


def register(monkeypatch, demo, brevo, email="demo.user@example.org"):
    monkeypatch.setattr(settings, "DEMO_AUTO_VERIFY", demo)
    monkeypatch.setattr(settings, "BREVO_API_KEY", brevo)
    monkeypatch.setattr(auth, "redis_client", NoRedis())

    async def sent(*a, **k):
        return True

    monkeypatch.setattr(auth, "send_verification_email", sent)
    db = RegDB()
    body = auth.RegisterRequest(email=email, username="demo_user", password="Str0ng!Passw0rd", full_name="Demo")
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
    out = asyncio.run(auth.register(body, request, db))
    return out, db.added[0]


def test_demo_flags_are_off_by_default():
    assert Settings.model_fields["DEMO_AUTO_VERIFY"].default is False


def test_with_email_configured_and_demo_off_a_new_account_waits_for_verification(monkeypatch):
    out, user = register(monkeypatch, demo=False, brevo="set")
    assert not out["is_verified"] and not user.is_verified  # the column default (false) applies on insert
    assert user.credits == settings.FREE_TIER_CREDITS  # the baseline's free tier, unchanged


def test_demo_auto_verify_verifies_at_once(monkeypatch):
    out, user = register(monkeypatch, demo=True, brevo="set")  # even with an email provider configured
    assert out["is_verified"] is True and user.is_verified
    assert user.credits == settings.FREE_TIER_CREDITS  # no separate demo balance any more


def test_the_baselines_no_email_auto_verify_is_unchanged(monkeypatch):
    out, user = register(monkeypatch, demo=False, brevo="")
    assert out["is_verified"] is True and user.credits == settings.FREE_TIER_CREDITS


# ---------------------------------------------------------------- nothing production-specific by default

def test_promote_emails_default_to_nobody():
    assert Settings.model_fields["PROMOTE_EMAILS"].default == ""
    assert Settings.model_fields["CORS_ORIGINS"].default == "" and Settings.model_fields["TRUSTED_HOSTS"].default == ""


def test_cors_origins_come_from_the_environment_settings(monkeypatch):
    import importlib
    from app import main
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", ["http://localhost:3000", "http://localhost:5173"])
    monkeypatch.setattr(settings, "CORS_ORIGINS", "https://demo-one.example, https://demo-two.example/")
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://demo-site.example")
    try:
        main = importlib.reload(main)
        assert main._cors_origins == ["http://localhost:3000", "http://localhost:5173", "https://demo-one.example",
                                      "https://demo-two.example", "https://demo-site.example"]
    finally:
        monkeypatch.undo()
        importlib.reload(main)


def test_billing_answers_503_when_razorpay_keys_are_unset(monkeypatch):
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "")
    with pytest.raises(HTTPException) as e:
        billing._get_razorpay()
    assert e.value.status_code == 503


# ---------------------------------------------------------------- no production identifiers left

def test_no_production_identifier_remains_in_the_sources():
    root = Path(__file__).resolve().parents[4]
    needles = ["railway" + ".app", "theprysm" + ".io", "theprysm" + "77", "theprysm" + "7"]
    skip = {"node_modules", ".venv", "dist", "__pycache__", ".git", "_private", "samples"}
    hits = []
    for base in (root / "backend" / "app", root / "web" / "src", root / "web" / "public"):
        for path in base.rglob("*"):
            if path.is_file() and not skip & set(path.parts) and path.suffix in {".py", ".ts", ".tsx", ".html", ".json", ".toml", ".css", ""}:
                text = path.read_text(errors="ignore")
                hits += [f"{path.relative_to(root)}: {n}" for n in needles if n in text and path.name != Path(__file__).name]
    for name in ("vercel.json", "netlify.toml", "index.html", "package.json"):
        text = (root / "web" / name).read_text(errors="ignore")
        hits += [f"web/{name}: {n}" for n in needles if n in text]
    assert hits == []
