"""Built during the Meta Global AI Developer Hackathon (new work).

Tests for the hardening of the Instagram / Creator DNA work: (1) grounding of every claim and of the personalised answers,
(2) encryption of the Instagram token at rest, (3) reels without a video link (thumbnail / caption only). Offline: the model
function is replaced; database tests use the same throwaway Postgres as test_instagram.py and skip when none is reachable.
"""
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.core.config import settings
from app.studio import config
from app.studio.instagram import dna as D
from app.studio.instagram import grounding as G
from app.studio.instagram import importer, oauth, personalise, tokencrypt
from app.studio.instagram.models import CreatorDna, IgAccount, IgFixLink, IgReel
from app.studio.tests.test_instagram import Ask, FakeSource, Redis, aio, dbf, fp, make_user, reel



@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(config, "_dotenv", lambda: {})
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("COACH_PERSONALISE", raising=False)
    monkeypatch.setattr(settings, "FRONTEND_URL", "http://localhost:5177")
    monkeypatch.setattr(settings, "INSTAGRAM_FRONTEND_URL", "")
    tokencrypt._fernet.cache_clear()
    tokencrypt._warned = False


def new_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def mk(n, caption="plain caption", fingerprint="default", wt=0.5, index=1.0, level="full"):
    return {"n": n, "caption": caption, "fingerprint": fp() if fingerprint == "default" else fingerprint, "watch_through": wt,
            "performance_index": index, "analysis_level": level, "media_id": f"m{n}"}


def prep_of(reels):
    p = D.prepare([{**r, "posted_at": f"2026-10-{30 - r['n']:02d}T10:00:00+00:00"} for r in reels])
    return p


# =================================================================== 1. grounding of the Creator DNA

def test_uncited_claim_is_rejected_and_cited_one_kept():
    prep = prep_of([mk(n) for n in range(1, 9)])
    j = {"summary": "", "strongest": [{"claim": "These reels open with a question", "reels": []},
                                      {"claim": "These reels open with a question", "reels": [1, 2]}], "weakest": [], "keep": [], "change": []}
    dna, rejected, checked = D.validate_dna_report(j, prep)
    assert checked == 2 and len(rejected) == 1 and rejected[0]["problems"] == ["cites no reel"]
    assert [c["reels"] for c in dna["strongest"]] == [[1, 2]]
    assert dna["strongest"][0]["media_ids"] == ["m1", "m2"]            # every kept claim points at real imported reels


def test_claim_citing_a_reel_that_was_not_imported_is_rejected_even_when_other_citations_are_real():
    prep = prep_of([mk(n) for n in range(1, 9)])
    j = {"summary": "", "strongest": [{"claim": "These reels open with a question", "reels": [1, 99]}], "weakest": [], "keep": [{"claim": "Open with a question", "reels": [1]}], "change": []}
    dna, rejected, _ = D.validate_dna_report(j, prep)
    assert any("Reel 99" in p for r in rejected for p in r["problems"]) and dna["strongest"] == []


def test_invented_greeting_is_rejected_unless_the_caption_really_has_it():
    reels = [mk(n, caption="plain caption") for n in range(1, 9)]
    claim = {"claim": "These reels start with a friendly 'hey guys' greeting", "reels": [1, 2]}
    dna, rejected, _ = D.validate_dna_report({"summary": "", "strongest": [claim], "weakest": [], "keep": [{"claim": "Open with a question", "reels": [1]}], "change": []},
                                             prep_of(reels))
    assert len(rejected) == 1 and "hey guys" in " ".join(rejected[0]["problems"])
    assert dna["strongest"] == []
    reels[0]["caption"], reels[1]["caption"] = "Hey guys, new video!", "hey guys quick tip"       # now it is in the captions
    dna2, rejected2, _ = D.validate_dna_report({"summary": "", "strongest": [claim], "weakest": [], "keep": [], "change": []}, prep_of(reels))
    assert rejected2 == [] and len(dna2["strongest"]) == 1


def test_abstract_detail_words_need_a_concrete_example_in_the_caption():
    f = G.facts_from([mk(1, caption="plain caption"), mk(2, caption="Follow for more tips")])
    assert G.problems("Reel 1 opens with a greeting", [1], f)
    assert G.problems("Reel 1 ends with a call to action", [1], f)
    assert not G.problems("Reel 2 ends with a call to action", [2], f)


def test_invented_quote_number_speech_music_and_hashtag_are_all_caught():
    f = G.facts_from([mk(1, caption="my morning routine #fitness", wt=0.62, index=1.8, fingerprint=fp(audio="unknown")),
                      mk(2, caption="b", wt=None, index=1.1, fingerprint=fp(audio="unknown"))])
    assert any("quotes" in p for p in G.problems('Reel 1 says "never skip leg day"', [1], f))
    assert any("number" in p for p in G.problems("Reel 1 got 85% watch-through", [1], f))
    assert not G.problems("Reel 1 got 62% watch-through and an index of 1.80", [1], f)                    # real numbers pass
    assert any("speech" in p or "spoken" in p for p in G.problems("In Reel 1 the creator says to stretch first", [1], f))
    assert any("music" in p for p in G.problems("Reel 1 uses a trending sound", [1], f))
    assert any("#" in p for p in G.problems("Reel 1 uses #gymtok", [1], f))
    assert not G.problems("Reel 1 uses #fitness", [1], f)
    assert any("watch-through" in p for p in G.problems("Reel 2 has strong watch-through", [2], f))       # reel 2 has none measured


def test_claim_about_hook_format_or_pacing_must_match_the_cited_fingerprints():
    reels = [mk(n, fingerprint=fp("question", "talking_head", "fast")) for n in range(1, 5)] + \
            [mk(n, fingerprint=fp("visual_only", "montage", "slow")) for n in range(5, 9)]
    f = G.facts_from(reels)
    assert not G.problems("Reels 1, 2 open with a question", [1, 2], f)
    assert any("fingerprints" in p for p in G.problems("Reels 5, 6 open with a question", [5, 6], f))
    assert not G.problems("Reels 5, 6 are slow montages", [5, 6], f)


def test_strongest_claims_must_cite_mostly_top_reels_and_weakest_mostly_bottom_reels():
    f = G.facts_from([mk(n) for n in range(1, 9)])
    assert G.check_claim("These reels open with a question", [1, 2], f, kind="strongest", top=[1, 2, 3], bottom=[7, 8]) == []
    assert G.check_claim("These reels open with a question", [7, 8], f, kind="strongest", top=[1, 2, 3], bottom=[7, 8])
    assert G.check_claim("These reels open with a question", [7, 8], f, kind="weakest", top=[1, 2, 3], bottom=[7, 8]) == []


def reels_with_invented_greeting():
    rs = [mk(n, fingerprint=fp("question", "talking_head", "fast"), index=2.0, wt=0.7) for n in range(1, 4)]
    rs += [mk(n, fingerprint=fp("visual_only", "montage", "slow"), index=1.0, wt=0.4) for n in range(4, 8)]
    rs += [mk(n, fingerprint=fp("visual_only", "montage", "slow"), index=0.4, wt=0.2) for n in range(8, 11)]
    return rs


def reply(greeting: bool):
    claim = "These reels open with a question" + (" and a 'hey guys' greeting" if greeting else "")
    return {"summary": "Reels 1, 2, 3 open with a question and did best. Reels 8, 9 are slow montages. Keep the question opener.",
            "strongest": [{"claim": claim, "reels": [1, 2, 3]}],
            "weakest": [{"claim": "These reels are slow montages", "reels": [8, 9, 10]}],
            "keep": [{"claim": "Open with a question", "reels": [1]}], "change": [{"claim": "Cut the slow montages", "reels": [9]}]}


class Seq:
    """Model replies in order; counts calls."""
    def __init__(self, *replies):
        self.replies, self.calls, self.prompts = list(replies), 0, []

    async def __call__(self, content, max_tokens, spend, *, system=None):
        self.prompts.append(content[0]["text"])
        r = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        spend.add(0.01)
        return json.dumps(r)


@aio
async def test_a_rejected_claim_triggers_exactly_one_repair_that_names_the_problem(monkeypatch):
    ask = Seq(reply(greeting=True), reply(greeting=False))
    monkeypatch.setattr(D, "_ask", ask)
    out = await D.build_dna(reels_with_invented_greeting(), "reach", [], D.Spend(1))
    dna = out["dna"]
    assert ask.calls == 2 and dna["mode"] == "full"
    assert dna["grounding"] == {"checked": 4, "dropped": 0, "regenerated": True}
    assert "hey guys" not in json.dumps(dna).lower() and "hey guys" not in out["summary"].lower()
    assert "hey guys" in ask.prompts[1] and "rejected" in ask.prompts[1]       # the repair prompt says what was wrong


@aio
async def test_repair_is_tried_once_and_what_still_fails_is_dropped(monkeypatch):
    ask = Seq(reply(greeting=True), reply(greeting=True), reply(greeting=True))
    monkeypatch.setattr(D, "_ask", ask)
    out = await D.build_dna(reels_with_invented_greeting(), "reach", [], D.Spend(1))
    assert ask.calls == 2                                                    # never a third call
    assert out["dna"]["strongest"] == [] and out["dna"]["grounding"]["dropped"] == 1 and out["dna"]["grounding"]["regenerated"] is True
    assert out["dna"]["weakest"]                                             # the grounded claims stay


@aio
async def test_a_clean_answer_makes_no_repair_call(monkeypatch):
    ask = Seq(reply(greeting=False))
    monkeypatch.setattr(D, "_ask", ask)
    out = await D.build_dna(reels_with_invented_greeting(), "reach", [], D.Spend(1))
    assert ask.calls == 1 and out["dna"]["grounding"]["regenerated"] is False


@aio
async def test_no_repair_when_the_cost_cap_is_reached(monkeypatch):
    ask = Seq(reply(greeting=True))
    monkeypatch.setattr(D, "_ask", ask)
    spend = D.Spend(0.005)
    spend.usd = 0.005
    out = await D.build_dna(reels_with_invented_greeting(), "reach", [], spend)
    assert ask.calls == 1 and out["dna"]["grounding"]["regenerated"] is False


@aio
async def test_a_summary_with_an_invented_detail_is_replaced_by_one_built_from_the_grounded_claims(monkeypatch):
    bad = reply(greeting=False)
    bad["summary"] = "Reels 1, 2 open by saying 'welcome back' to the audience."
    monkeypatch.setattr(D, "_ask", Seq(bad))
    out = await D.build_dna(reels_with_invented_greeting(), "reach", [], D.Spend(1))
    assert "welcome" not in out["summary"].lower() and "Reels 1, 2, 3" in out["summary"]


# =================================================================== 1b. grounding of the personalised answer

def answer_facts():
    return G.facts_from([mk(1, caption="plain", fingerprint=fp("question", "talking_head", "fast", audio="unknown"), index=2.0, wt=0.7),
                         mk(2, caption="plain", fingerprint=fp("visual_only", "montage", "slow", audio="unknown"), index=0.5, wt=0.2),
                         mk(3, caption="plain", fingerprint=fp(), index=1.0, wt=0.5)])


def data_for(reels):
    class Row:
        pass
    acct, dna = Row(), Row()
    acct.is_fixture = False
    dna.dna = {"top": [1], "bottom": [2], "mode": "full"}
    return {"account": acct, "dna": dna, "reels": [{**r, "partial": r["analysis_level"] != "full"} for r in reels]}


def an_answer():
    return {"summary": "Your hook is slow. Compared with your Reel 1 this opens with a 'hey guys' greeting. You usually open with a question.",
            "parameters": [{"name": "Hook & Opening", "problem": "The first seconds are slow.", "fix": "Open on the result, like your Reel 1 does with a question.",
                            "whatIsHappening": "Your Reel 2 has the same slow start.", "whyItMatters": "", "whatToChange": "Reel 77 is the model to copy.",
                            "howToDoIt": "", "whatToExpect": ""}]}


def test_check_fields_finds_invented_details_unknown_reels_and_uncited_personal_claims():
    bad = G.check_fields(an_answer(), answer_facts(), personalise.FIELDS)
    found = {b["sentence"]: b["problems"] for b in bad}
    assert any("hey guys" in " ".join(p) for p in found.values())
    assert any(p == ["talks about the creator's own reels without naming one"] for p in found.values())
    assert any("Reel 77" in " ".join(p) for p in found.values())
    assert "Open on the result, like your Reel 1 does with a question." not in found                 # grounded: untouched
    assert "The first seconds are slow." not in found and "Your Reel 2 has the same slow start." not in found


@aio
async def test_ungrounded_sentences_are_rewritten_once_and_a_failed_rewrite_is_dropped(monkeypatch):
    calls = []

    async def fake_ask(content, max_tokens, spend, *, system=None):
        calls.append(content[0]["text"])
        spend.add(0.01)
        # rewrites sentence 0 correctly; sentence 1 stays invented; the rest get no answer
        return json.dumps({"fixes": [{"id": 0, "text": "Compared with your Reel 1, this opens more slowly."},
                                     {"id": 1, "text": "You usually open with a 'hey guys' greeting."}]})
    monkeypatch.setattr(D, "_ask", fake_ask)
    result = an_answer()
    reels = [mk(1, fingerprint=fp("question", "talking_head", "fast", audio="unknown")), mk(2), mk(3)]
    stats = await personalise.ground_result(data_for(reels), result, D.Spend(1))
    assert len(calls) == 1 and stats["regenerated"] is True
    assert "Compared with your Reel 1, this opens more slowly." in result["summary"]
    assert "hey guys" not in result["summary"] and "You usually" not in result["summary"] and "Reel 77" not in json.dumps(result)
    assert result["parameters"][0]["whatToChange"] == personalise.PLACEHOLDER                      # emptied field is explained, never blank
    assert result["parameters"][0]["fix"].startswith("Open on the result")                          # grounded text stays
    assert G.check_fields(result, G.facts_from(reels), personalise.FIELDS) == []                    # nothing ungrounded remains
    assert stats["rewritten"] == 1 and stats["dropped"] >= 2


@aio
async def test_a_failing_model_call_drops_ungrounded_sentences_instead_of_keeping_them(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(D, "_ask", boom)
    result = an_answer()
    stats = await personalise.ground_result(data_for([mk(1), mk(2), mk(3)]), result, D.Spend(1))
    assert "hey guys" not in json.dumps(result) and "Reel 77" not in json.dumps(result) and stats["rewritten"] == 0


@aio
async def test_an_answer_with_nothing_ungrounded_makes_no_model_call(monkeypatch):
    async def never(*a, **k):
        raise AssertionError("no call expected")
    monkeypatch.setattr(D, "_ask", never)
    result = {"summary": "Open on the result. Your Reel 1 does that with a question.", "parameters": [{"problem": "Slow start.", "fix": "Cut the intro."}]}
    stats = await personalise.ground_result(data_for([mk(1, fingerprint=fp("question", "talking_head", "fast")), mk(2)]), result, D.Spend(1))
    assert stats == {"checked": 1, "dropped": 0, "rewritten": 0, "regenerated": False}


def test_fail_closed_strips_every_sentence_about_the_creators_reels_without_a_model():
    result = an_answer()
    n = personalise.strip_personal(result)
    blob = json.dumps(result)
    assert n >= 4 and "Reel" not in blob and "You usually" not in blob
    assert result["parameters"][0]["problem"] == "The first seconds are slow."


def test_the_addendum_lists_only_known_facts_and_says_when_a_reel_is_thumbnail_only():
    data = data_for([mk(1, fingerprint=fp("question", "talking_head", "fast")), mk(2, level="thumbnail_caption", wt=None)])
    data["dna"].sample_size = 2
    data["dna"].dna = {"top": [1], "bottom": [2], "mode": "full", "strongest": [{"claim": "Open with a question", "reels": [1]}]}
    text_ = personalise.addendum(data)
    assert "ONLY known facts" in text_ and "Reel 1: hook question" in text_
    assert "Reel 2: read from thumbnail and caption only" in text_ and "watch-through" in text_


# =================================================================== 2. token encryption

def test_token_round_trip_prefix_and_plain_tokens_stay_readable(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", new_key())
    stored = tokencrypt.encrypt("IGQ-secret-token")
    assert stored.startswith("enc:v1:") and "IGQ-secret-token" not in stored
    assert tokencrypt.decrypt(stored) == "IGQ-secret-token"
    assert tokencrypt.decrypt("legacy-plain-token") == "legacy-plain-token"       # written before encryption existed
    assert tokencrypt.encrypt(stored) == stored                                      # never encrypted twice
    assert tokencrypt.encrypt(None) is None and tokencrypt.decrypt(None) is None


def test_wrong_or_missing_key_fails_safely_without_leaking(monkeypatch, caplog):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", new_key())
    stored = tokencrypt.encrypt("IGQ-secret-token")
    other = new_key()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", other)
    with pytest.raises(tokencrypt.TokenCryptoError) as e:
        tokencrypt.decrypt(stored)
    assert "IGQ-secret-token" not in str(e.value) and other not in str(e.value)
    col = tokencrypt.EncryptedToken()
    with caplog.at_level(logging.DEBUG):
        assert col.process_result_value(stored, None) is None                        # reads as "not connected", never raises
    assert "IGQ-secret-token" not in caplog.text and other not in caplog.text and stored not in caplog.text
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY")
    tokencrypt._fernet.cache_clear()
    with pytest.raises(tokencrypt.TokenCryptoError):
        tokencrypt.decrypt(stored)


def test_an_invalid_key_is_rejected_without_echoing_it(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "not-a-fernet-key-value")
    with pytest.raises(tokencrypt.TokenCryptoError) as e:
        tokencrypt.encrypt("tok")
    assert "not-a-fernet-key-value" not in str(e.value)


def test_key_rotation_new_key_encrypts_and_old_key_still_decrypts(monkeypatch):
    old, new = new_key(), new_key()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", old)
    stored_old = tokencrypt.encrypt("tok-1")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", f"{new},{old}")
    assert tokencrypt.decrypt(stored_old) == "tok-1"
    stored_new = tokencrypt.encrypt("tok-2")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", new)
    assert tokencrypt.decrypt(stored_new) == "tok-2"


def test_without_a_key_development_keeps_plain_text_with_one_warning_and_production_refuses(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        assert tokencrypt.encrypt("dev-token") == "dev-token"
        assert tokencrypt.encrypt("dev-token") == "dev-token"
    assert caplog.text.count("TOKEN_ENCRYPTION_KEY is not set") == 1 and "dev-token" not in caplog.text
    monkeypatch.setattr(settings, "APP_ENV", "production")
    with pytest.raises(tokencrypt.TokenCryptoError) as e:
        tokencrypt.encrypt("prod-token")
    assert "prod-token" not in str(e.value)


@aio
async def test_the_database_holds_only_the_encrypted_token_and_the_app_still_reads_it(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", new_key())
    async with dbf() as factory:
        from app.models.user import User
        uid = await make_user(factory)
        async with factory() as db:
            await oauth.save_connection(db, uid, token="IGQ-live-token", ig_user_id="ig1", username="me", account_type="BUSINESS",
                                        expires_in=3600 * 24 * 60, scopes="a,b")
            raw = (await db.execute(text("SELECT instagram_access_token FROM users WHERE id = :i"), {"i": uid})).scalar()
            assert raw.startswith("enc:v1:") and "IGQ-live-token" not in raw
        async with factory() as db:
            user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
            assert user.instagram_access_token == "IGQ-live-token"                       # transparent for the rest of the app
            assert await oauth.ensure_fresh_token(db, user) == "IGQ-live-token"
            await oauth.disconnect(db, uid)
            assert (await db.execute(text("SELECT instagram_access_token FROM users WHERE id = :i"), {"i": uid})).scalar() is None


@aio
async def test_a_plain_token_from_before_encryption_is_used_and_encrypted_on_first_use(monkeypatch):
    async with dbf() as factory:
        from app.models.user import User
        uid = await make_user(factory)
        async with factory() as db:                                                      # saved while no key was configured
            await oauth.save_connection(db, uid, token="IGQ-old-plain", ig_user_id="ig1", username="me", account_type="BUSINESS",
                                        expires_in=3600 * 24 * 60, scopes="a,b")
            assert (await db.execute(text("SELECT instagram_access_token FROM users WHERE id = :i"), {"i": uid})).scalar() == "IGQ-old-plain"
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", new_key())
        async with factory() as db:
            user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
            assert user.instagram_access_token == "IGQ-old-plain"                        # still readable
            assert await oauth.ensure_fresh_token(db, user) == "IGQ-old-plain"
        async with factory() as db:
            raw = (await db.execute(text("SELECT instagram_access_token FROM users WHERE id = :i"), {"i": uid})).scalar()
            assert raw.startswith("enc:v1:") and "IGQ-old-plain" not in raw


@aio
async def test_the_migration_helpers_encrypt_existing_tokens_and_can_go_back(monkeypatch):
    async with dbf() as factory:
        uid = await make_user(factory)
        async with factory() as db:
            await db.execute(text("UPDATE users SET instagram_access_token = NULL"))                 # rows left by other tests
            await db.execute(text("UPDATE users SET instagram_access_token = 'IGQ-migrate-me' WHERE id = :i"), {"i": uid})
            await db.commit()
        with pytest.raises(tokencrypt.TokenCryptoError):                                 # no key: the migration must not pretend
            async with factory() as db:
                await (await db.connection()).run_sync(tokencrypt.encrypt_existing)
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", new_key())
        async with factory() as db:
            n = await (await db.connection()).run_sync(tokencrypt.encrypt_existing)
            await db.commit()
        assert n >= 1
        async with factory() as db:
            raw = (await db.execute(text("SELECT instagram_access_token FROM users WHERE id = :i"), {"i": uid})).scalar()
            assert raw.startswith("enc:v1:")
            assert await (await db.connection()).run_sync(tokencrypt.encrypt_existing) == 0   # idempotent
            await (await db.connection()).run_sync(tokencrypt.decrypt_existing)
            await db.commit()
        async with factory() as db:
            assert (await db.execute(text("SELECT instagram_access_token FROM users WHERE id = :i"), {"i": uid})).scalar() == "IGQ-migrate-me"


# =================================================================== 3. reels without a video link

class ThumbSource(FakeSource):
    """Reels 1..n have no video file. Even-numbered ones still have a thumbnail; odd ones have nothing."""
    async def thumbnail(self, raw):
        return b"\xff\xd8jpeg" if int(raw["id"][1:]) % 2 == 0 else None


@aio
async def test_import_marks_reels_read_without_video_and_never_judges_hook_pacing_or_watch_through(monkeypatch):
    seen = {}

    async def fake_ask(content, max_tokens, spend, *, system=None):
        spend.add(0.002)
        prompt = content[-1]["text"]
        if "Return JSON with exactly these keys" in prompt:
            seen.setdefault("prompts", []).append(prompt)
            return json.dumps({"hook_type": "question", "format": "talking_head", "pacing": "fast", "audio_type": "speech",
                               "on_screen_text": True, "topic": "t"})
        return json.dumps({"summary": "", "strongest": [], "weakest": [], "keep": [], "change": []})
    monkeypatch.setattr(D, "_ask", fake_ask)
    async with dbf() as factory:
        uid = await make_user(factory)
        redis = Redis()
        async with factory() as db:
            await oauth.save_connection(db, uid, token="tok", ig_user_id="ig1", username="me", account_type="BUSINESS", expires_in=3600 * 24 * 60,
                                        scopes="a,b", is_fixture=True)
        summary = await importer.run_import(redis=redis, session_factory=factory, user_id=uid, count=8, job_id="j1", source=ThumbSource(8))
        assert summary["partial"] == 8
        async with factory() as db:
            acct = (await db.execute(select(IgAccount).where(IgAccount.user_id == uid))).scalar_one()
            assert acct.last_import_summary["partial"] == 8
            rows = (await db.execute(select(IgReel).where(IgReel.account_id == acct.id))).scalars().all()
            levels = {r.media_id: r.analysis_level for r in rows}
            assert {levels[f"m{i}"] for i in (0, 2, 4, 6)} == {"thumbnail_caption"} and {levels[f"m{i}"] for i in (1, 3, 5, 7)} == {"caption_only"}
            for r in rows:
                assert r.watch_through is None and r.duration_s is None
                assert all(r.fingerprint[k] == "unknown" for k in ("hook_type", "format", "pacing", "audio_type"))
                assert "No watch-through" in r.note and "not judged" in r.note
            data = await personalise.load(db, uid)
            assert all(r["partial"] and r["partial_reason"] and r["analysis_level"] != "full" for r in data["reels"])
            assert "thumbnail" in data["reels"][0]["partial_reason"] or "caption" in data["reels"][0]["partial_reason"]
    assert all("You cannot judge the opening seconds, format, pacing or audio" in p for p in seen["prompts"])           # the model is told it only has a still


@aio
async def test_a_mix_of_full_and_partial_reels_is_reported_honestly_in_the_dna_header(monkeypatch):
    reels = reels_with_invented_greeting()
    for r in reels[8:]:
        r["analysis_level"], r["fingerprint"] = "thumbnail_caption", D.mark_partial(r["fingerprint"], "thumbnail_caption")
        r["watch_through"] = None
    monkeypatch.setattr(D, "_ask", Seq({"summary": "Reels 1, 2, 3 open with a question.", "strongest": [{"claim": "These reels open with a question", "reels": [1, 2, 3]}],
                                         "weakest": [{"claim": "These reels are slow montages", "reels": [4, 5, 6]}], "keep": [], "change": []}))
    out = await D.build_dna(reels, "reach", [], D.Spend(1))
    assert out["dna"]["partial_reels"] == [9, 10]
    assert "8 were read from the video; 2 only from the thumbnail and caption" in out["summary"]
    assert "no watch-through" in out["summary"]


def test_a_claim_about_hooks_or_pacing_cannot_rest_on_a_thumbnail_only_reel():
    part = D.mark_partial(fp("question", "talking_head", "fast"), "thumbnail_caption")
    f = G.facts_from([mk(1, fingerprint=part, level="thumbnail_caption", wt=None), mk(2, fingerprint=fp("question", "talking_head", "fast"))])
    assert G.problems("Reel 1 opens with a question", [1], f)                                  # unknown hook: not supported
    assert not G.problems("Reel 2 opens with a question", [2], f)
    assert G.problems("Reel 1 has strong watch-through", [1], f)


def test_comparison_ignores_reels_whose_hook_and_pacing_were_not_judged():
    reels = [mk(1, fingerprint=fp("question", "talking_head", "fast")), mk(2, fingerprint=D.mark_partial(fp("question"), "thumbnail_caption"), level="thumbnail_caption"),
             mk(3, fingerprint=fp("visual_only", "montage", "slow"))]
    out = personalise.compare(fp("question", "talking_head", "fast"), reels, [1, 2], [3])
    hook = next(o for o in out if o["field"] == "hook_type")
    assert hook["top_total"] == 1 and hook["top_reels"] == [1]                                   # reel 2 does not count either way


def test_legacy_rows_without_a_level_are_derived_from_duration_and_frames():
    class Row:
        def __init__(self, level, dur, frames):
            self.analysis_level, self.duration_s, self.frames_used = level, dur, frames
    assert personalise.level_of(Row("thumbnail_caption", None, 1)) == "thumbnail_caption"
    assert personalise.level_of(Row(None, 12.0, 6)) == "full"
    assert personalise.level_of(Row(None, None, 1)) == "thumbnail_caption"
    assert personalise.level_of(Row(None, None, 0)) == "caption_only"


# =================================================================== phase 1: limits and honesty

@aio
async def test_one_or_two_reels_get_the_breakdown_only_and_no_pattern_claim(monkeypatch):
    async def never(*a, **k):
        raise AssertionError("no model call for fewer than 3 reels")
    monkeypatch.setattr(D, "_ask", never)
    for n in (1, 2):
        out = await D.build_dna([mk(i, fingerprint=fp(), wt=0.5) | {"posted_at": f"2026-10-{30 - i:02d}T10:00:00+00:00"} for i in range(1, n + 1)],
                                "reach", [], D.Spend(1))
        d = out["dna"]
        assert d["mode"] == "breakdown" and d["reason"] == "Patterns need at least 3 reels."
        assert d["shared"] == [] and d["strongest"] == [] and d["weakest"] == [] and d["keep"] == [] and d["change"] == []
        assert f"Based on your {n} most recent imported reel" in out["summary"] and "Patterns need at least 3 reels." in out["summary"]
        assert "not proof" not in out["summary"] and "same hook" not in out["summary"]
        assert personalise.usable({"dna": type("R", (), {"dna": d})()}) is False                  # never used to personalise answers


@aio
async def test_three_reels_show_the_dna_with_the_count_in_the_header(monkeypatch):
    async def never(*a, **k):
        raise AssertionError("no model call below 5 reels")
    monkeypatch.setattr(D, "_ask", never)
    out = await D.build_dna([mk(i, fingerprint=fp()) | {"posted_at": f"2026-10-{30 - i:02d}T10:00:00+00:00"} for i in range(1, 4)], "reach", [], D.Spend(1))
    assert out["dna"]["mode"] == "descriptive" and out["summary"].startswith("Based on your 3 most recent imported reels")
    assert out["dna"]["shared"]                                                                    # "3 of 3 reels use the same hook: ..."


def test_import_count_limits_are_1_to_30_with_a_default_of_10():
    assert (importer.MIN_COUNT, importer.MAX_COUNT, importer.DEFAULT_COUNT) == (1, 30, 10)
    assert [importer.clamp_count(x) for x in (0, 1, 2, 30, 31, "7", None, "x")] == [1, 1, 2, 30, 30, 7, 10, 10]


@aio
async def test_count_reels_counts_only_reels_in_one_call_and_stops_at_the_limit():
    import httpx
    from app.studio.instagram import graph
    calls = []

    def handler(request):
        calls.append(str(request.url))
        data = [{"id": str(i), "media_type": "VIDEO", "media_product_type": "REELS" if i % 2 == 0 else "FEED"} for i in range(100)]
        return httpx.Response(200, json={"data": data, "paging": {"next": "https://graph.instagram.com/next"}})
    src = graph.InstagramSource("tok", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await src.count_reels(30) == (50, False) and len(calls) == 1                           # 50 reels in the first page: stop there
    few = graph.InstagramSource("tok", httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": [{"id": "1", "media_type": "VIDEO", "media_product_type": "REELS"},
                                                      {"id": "2", "media_type": "IMAGE", "media_product_type": "FEED"}]}))))
    assert await few.count_reels(30) == (1, True)                                                  # the whole account has one reel


# =================================================================== phase 2: plain language and real data

def test_plain_language_for_the_performance_index():
    from app.studio.instagram import metrics as M
    assert M.plain_vs_usual(0.75, "reach")["label"] == "Reach 25% below your usual"
    assert M.plain_vs_usual(1.25, "reach")["label"] == "Reach 25% above your usual"
    assert M.plain_vs_usual(1.247, "reach")["label"] == "Reach 25% above your usual"
    assert M.plain_vs_usual(1.02, "reach") == {"pct": 2, "direction": "same", "label": "Reach about your usual"}
    assert M.plain_vs_usual(0.5, "views")["label"] == "Views 50% below your usual"
    assert M.plain_vs_usual(None, "reach") is None


@aio
async def test_load_returns_every_real_metric_thumbnail_and_plain_language_per_reel():
    async with dbf() as factory:
        uid = await make_user(factory)
        async with factory() as db:
            acct = await oauth.save_connection(db, uid, token="t", ig_user_id="ig1", username="me", account_type="BUSINESS", expires_in=3600,
                                               scopes="a", profile_picture_url="https://cdn.example/p.jpg")
            r = IgReel(account_id=acct.id, media_id="m1", caption="hello", thumbnail_url="https://cdn.example/t.jpg", performance_index=0.75,
                       performance_basis="reach", duration_s=20.0)
            db.add(r)
            await db.flush()
            from app.studio.instagram.models import IgReelInsight
            db.add(IgReelInsight(reel_id=r.id, metric="reach", value=463.0, available=True))
            db.add(IgReelInsight(reel_id=r.id, metric="saved", value=0.0, available=True))          # a real zero stays 0
            db.add(IgReelInsight(reel_id=r.id, metric="reels_skip_rate", value=None, available=False))
            await db.commit()
        async with factory() as db:
            data = await personalise.load(db, uid)
            x = data["reels"][0]
            assert x["insights"]["reach"] == 463.0 and x["insights"]["saved"] == 0.0 and x["insights"]["reels_skip_rate"] is None
            assert x["insights"]["views"] is None                                                       # never fetched: no data, not 0
            assert x["thumbnail_url"] == "https://cdn.example/t.jpg" and x["vs_usual"]["label"] == "Reach 25% below your usual"
            assert data["account"].profile_picture_url == "https://cdn.example/p.jpg"


@aio
async def test_import_keeps_the_thumbnail_link_and_the_profile_picture(monkeypatch):
    async def fake_ask(content, max_tokens, spend, *, system=None):
        spend.add(0.001)
        return json.dumps({"hook_type": "question", "format": "talking_head", "pacing": "fast", "audio_type": "speech", "on_screen_text": True, "topic": "t"})
    monkeypatch.setattr(D, "_ask", fake_ask)

    class PicSource(FakeSource):
        async def profile_picture(self):
            return "https://cdn.example/me.jpg"
    async with dbf() as factory:
        uid = await make_user(factory)
        async with factory() as db:
            await oauth.save_connection(db, uid, token="tok", ig_user_id="ig1", username="me", account_type="BUSINESS", expires_in=3600, scopes="a", is_fixture=True)
        await importer.run_import(redis=Redis(), session_factory=factory, user_id=uid, count=3, job_id="j", source=PicSource(3))
        async with factory() as db:
            acct = (await db.execute(select(IgAccount).where(IgAccount.user_id == uid))).scalar_one()
            assert acct.profile_picture_url == "https://cdn.example/me.jpg"
            assert {r.thumbnail_url for r in (await db.execute(select(IgReel).where(IgReel.account_id == acct.id))).scalars()} == {"t"}


def test_real_skip_rate_is_a_percent_and_the_fixture_matches():
    from app.studio.instagram import fixtures
    vals = [fixtures._insights(i)["reels_skip_rate"] for i in range(1, 12)]
    assert all(5 <= v <= 80 for v in vals)                          # percent like Instagram's 59.1, not a 0..1 fraction


# =================================================================== phase 3: hook words

class FakeVoice:
    """Stands in for MetaClient: only .transcribe(wav)."""
    def __init__(self, transcript="Stop scrolling. Here is why your reels flop", ms=12000, cost=0.0006, boom=False):
        self.transcript, self.ms, self.cost, self.boom, self.calls = transcript, ms, cost, boom, 0

    def transcribe(self, wav):
        self.calls += 1
        if self.boom:
            raise RuntimeError("network down")
        return type("V", (), {"transcript": self.transcript, "audio_duration_ms": self.ms, "cost_usd": self.cost})()


@aio
async def test_transcribe_hook_cuts_the_first_12_seconds_and_returns_the_words(tmp_path, monkeypatch):
    from app.studio.instagram import hook as H
    cut = []

    async def fake_cut(video, wav, seconds=H.HOOK_SECONDS):
        cut.append(seconds)
        Path(wav).write_bytes(b"x" * 2000)
        return True
    monkeypatch.setattr(H, "cut_audio", fake_cut)
    voice = FakeVoice()
    r = await H.transcribe_hook("reel.mp4", str(tmp_path), client=voice)
    assert cut == [12] and voice.calls == 1
    assert (r.status, r.text, r.cost_usd) == ("transcribed", "Stop scrolling. Here is why your reels flop", 0.0006)
    assert (await H.transcribe_hook("reel.mp4", str(tmp_path), client=FakeVoice(transcript="  ", cost=0.0006))).status == "no_speech"
    assert (await H.transcribe_hook("reel.mp4", str(tmp_path), client=FakeVoice(boom=True))).status == "unknown"      # never raises


@aio
async def test_a_video_without_an_audio_track_is_no_speech_and_makes_no_call(tmp_path, monkeypatch):
    from app.studio.instagram import hook as H

    async def no_audio(video, wav, seconds=12):
        return False
    monkeypatch.setattr(H, "cut_audio", no_audio)
    voice = FakeVoice()
    assert (await H.transcribe_hook("reel.mp4", str(tmp_path), client=voice)).status == "no_speech" and voice.calls == 0


from pathlib import Path   # noqa: E402  (used above)


@aio
async def test_import_stores_hook_words_adds_them_to_the_fingerprint_and_leaves_videoless_reels_unknown(monkeypatch):
    from app.studio.instagram import hook as H
    seen = {"prompts": []}

    async def fake_ask(content, max_tokens, spend, *, system=None):
        spend.add(0.002)
        seen["prompts"].append(content[-1]["text"])
        return json.dumps({"hook_type": "question", "format": "talking_head", "pacing": "fast", "audio_type": "music_only", "on_screen_text": True, "topic": "t"})
    monkeypatch.setattr(D, "_ask", fake_ask)

    async def fake_transcribe(video, tmp, client=None):
        return H.HookResult("transcribed", "Stop scrolling right now", 0.0006, 12.0)
    monkeypatch.setattr(H, "transcribe_hook", fake_transcribe)

    class VideoSource(FakeSource):
        async def download(self, raw, dest):
            if int(raw["id"][1:]) % 2:
                return False                                              # odd reels: no video link
            import subprocess
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", dest], check=True)
            return True

        async def thumbnail(self, raw):
            return b"\xff\xd8jpeg"
    async with dbf() as factory:
        uid = await make_user(factory)
        redis = Redis()
        async with factory() as db:
            await oauth.save_connection(db, uid, token="tok", ig_user_id="ig1", username="me", account_type="BUSINESS", expires_in=3600, scopes="a", is_fixture=True)
        summary = await importer.run_import(redis=redis, session_factory=factory, user_id=uid, count=4, job_id="jh", source=VideoSource(4))
        assert summary["status"] == "done" and summary["hook_words_read"] == 2 and summary["transcribed_reels"] == 2
        assert summary["transcribe_usd"] == pytest.approx(0.0012)
        async with factory() as db:
            acct = (await db.execute(select(IgAccount).where(IgAccount.user_id == uid))).scalar_one()
            rows = {r.media_id: r for r in (await db.execute(select(IgReel).where(IgReel.account_id == acct.id))).scalars()}
            for k in ("m0", "m2"):
                assert rows[k].hook_words == "Stop scrolling right now" and rows[k].hook_words_status == "transcribed"
                assert rows[k].fingerprint["hook_words"] == "Stop scrolling right now" and rows[k].fingerprint["audio_type"] == "speech"
            for k in ("m1", "m3"):
                assert rows[k].hook_words is None and rows[k].hook_words_status == "unknown" and rows[k].fingerprint["hook_words"] is None
    assert any("Stop scrolling right now" in p for p in seen["prompts"])           # the model sees the real words


@aio
async def test_import_does_not_transcribe_once_the_cost_cap_is_reached(monkeypatch):
    from app.studio.instagram import hook as H
    calls = []

    async def fake_transcribe(video, tmp, client=None):
        calls.append(1)
        return H.HookResult("transcribed", "words words", 0.0006, 12.0)
    monkeypatch.setattr(H, "transcribe_hook", fake_transcribe)
    monkeypatch.setenv("DNA_COST_CAP_USD", "0")

    async def fake_ask(content, max_tokens, spend, *, system=None):
        spend.add(0.01)
        return "{}"
    monkeypatch.setattr(D, "_ask", fake_ask)

    class VideoSource(FakeSource):
        async def download(self, raw, dest):
            import subprocess
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", dest], check=True)
            return True
    async with dbf() as factory:
        uid = await make_user(factory)
        async with factory() as db:
            await oauth.save_connection(db, uid, token="tok", ig_user_id="ig1", username="me", account_type="BUSINESS", expires_in=3600, scopes="a", is_fixture=True)
        # a cost cap of 0 is already reached before the first reel is read: nothing is sent to Voice Transcribe
        await importer.run_import(redis=Redis(), session_factory=factory, user_id=uid, count=2, job_id="jc", source=VideoSource(2))
    assert calls == []


def test_grounding_allows_quotes_and_speech_claims_that_match_the_hook_words_and_rejects_the_rest():
    f = G.facts_from([mk(1, caption="plain", fingerprint=fp(audio="unknown")) | {"hook_words": "Hey guys, stop scrolling right now"},
                      mk(2, caption="plain", fingerprint=fp(audio="unknown")) | {"hook_words": None}])
    assert not G.problems('Reel 1 opens with "stop scrolling right now"', [1], f)               # really said
    assert not G.problems("Reel 1 starts with a hey guys greeting", [1], f)                    # greeting is in the hook words
    assert not G.problems("In Reel 1 the creator says to stop scrolling", [1], f)              # speech is evidenced by the words
    assert G.problems('Reel 1 opens with "welcome back to my channel"', [1], f)                # not said
    assert G.problems('Reel 2 opens with "stop scrolling right now"', [2], f)                  # reel 2 has no hook words
    assert G.problems("In Reel 2 the creator says to stop scrolling", [2], f)                  # unknown stays unsupported


def test_the_synthesis_rows_and_the_addendum_carry_the_real_hook_words_or_say_unknown():
    prep = prep_of([mk(n) | {"hook_words": "Stop scrolling" if n == 1 else None} for n in range(1, 6)])
    p = D._synth_prompt(prep)
    assert 'first spoken words: "Stop scrolling"' in p and "first spoken words: unknown" in p
    data = data_for([mk(1) | {"hook_words": "Stop scrolling", "vs_usual": {"label": "Reach 25% above your usual"}}, mk(2)])
    data["dna"].sample_size = 2
    data["dna"].dna = {"top": [1], "bottom": [2], "mode": "full"}
    t = personalise.addendum(data)
    assert 'first spoken words "Stop scrolling"' in t and "reach 25% above your usual" in t


def test_the_fixture_has_deterministic_fake_hook_words_for_talking_reels_only():
    from app.studio.instagram import fixtures
    src = fixtures.FixtureSource()
    talk = src.fixture_hook_words({"_fixture_index": 1})
    assert talk.status == "transcribed" and talk.text and talk.cost_usd == 0.0
    assert src.fixture_hook_words({"_fixture_index": 0}).status == "no_speech"                 # b-roll: nothing spoken


# =================================================================== phase 4: did my fix work?

from datetime import datetime, timedelta, timezone  # noqa: E402,F811
from app.studio.instagram import closeloop  # noqa: E402

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def lr(n, days_after=None, dur=20.0, wt=0.5, reach=1000.0, saved=10.0, shares=5.0, skip=50.0, posted=None):
    return {"n": n, "media_id": f"m{n}", "posted_at": posted or (T0 + timedelta(days=days_after if days_after is not None else n)), "duration_s": dur,
            "watch_through": wt, "permalink": f"https://x/{n}", "caption": "c",
            "insights": {"reach": reach, "views": None if reach is None else reach * 1.5, "saved": saved, "shares": shares, "reels_skip_rate": skip}}


def test_advice_items_are_the_top_priorities_plus_fixes_of_weak_parameters_without_duplicates():
    a = {"top_priorities": ["Open on the result", "open on the result", "Cut the intro"],
         "parameters": [{"name": "Hook", "score": 40, "fix": "Use a question hook"}, {"name": "Audio", "score": 90, "fix": "Keep the audio"},
                        {"name": "Text", "score": 30, "advisory": True, "fix": "Add captions"}, {"name": "Pace", "score": 55, "fix": "Cut the intro"}]}
    assert [x["text"] for x in closeloop.advice_items(a)] == ["Open on the result", "Cut the intro", "Use a question hook"]
    assert [x["id"] for x in closeloop.advice_items(a)] == ["a0", "a1", "a2"]


def test_suggest_matches_prefers_the_reel_posted_soon_after_with_about_the_same_length():
    reels = [lr(1, days_after=-5), lr(2, days_after=2, dur=40.0), lr(3, days_after=3, dur=22.0), lr(4, days_after=30, dur=22.0), lr(5, posted=None) | {"posted_at": None}]
    out = closeloop.suggest_matches(T0, 22.0, reels)
    assert [x["n"] for x in out] == [3, 2] and out[0]["likely"] is True and out[1]["likely"] is False       # before the analysis, 30 days later and undated are out
    assert any("length matches" in r for r in out[0]["reasons"]) and any("length differs" in r for r in out[1]["reasons"])
    assert closeloop.suggest_matches(None, 22.0, reels) == []
    assert closeloop.suggest_matches(T0, None, [lr(1, days_after=2)])[0]["likely"] is True            # date only: close enough to suggest


def test_before_after_compares_with_the_median_of_the_other_reels_in_plain_words():
    others = [lr(i, wt=0.40, reach=1000, saved=10, shares=5, skip=60) for i in range(1, 6)]
    linked = lr(9, wt=0.55, reach=1250, saved=30, shares=2, skip=45)
    r = closeloop.before_after(linked, others, now=T0 + timedelta(days=30))
    rows = {x["key"]: x for x in r["rows"]}
    assert r["basedOn"] == 5 and r["lagging"] is False
    assert rows["reach_vs_usual"]["plainText"] == "Reach 25% above your usual" and rows["reach_vs_usual"]["direction"] == "better"
    assert rows["watch_through"]["direction"] == "better" and rows["watch_through"]["valueText"] == "55%" and rows["watch_through"]["usualText"] == "40%"
    assert rows["saves_per_reach"]["valueText"] == "24.0" and rows["saves_per_reach"]["usualText"] == "10.0" and rows["saves_per_reach"]["direction"] == "better"
    assert rows["shares_per_reach"]["direction"] == "worse"                                               # 1.6 vs 5.0 per 1,000
    assert rows["skip_rate"]["direction"] == "better"                                                     # lower skip rate is better
    assert (r["better"], r["worse"], r["compared"]) == (4, 1, 5)
    assert any("not proof" in n for n in r["notes"]) and any("5 imported reels" in n for n in r["notes"])


def test_before_after_never_invents_numbers_and_flags_lag_and_tiny_samples():
    others = [lr(1, wt=None, reach=1000)]
    young = lr(9, wt=None, reach=None, saved=None, shares=None, skip=None, posted=T0)
    young["insights"] = {k: None for k in young["insights"]}
    r = closeloop.before_after(young, others, now=T0 + timedelta(hours=10))
    assert r["lagging"] is True and all(x["direction"] == "none" and x["note"] for x in r["rows"]) and r["compared"] == 0
    assert any("48 hours" in n for n in r["notes"]) and any("rough guide" in n for n in r["notes"])
    none = closeloop.before_after(lr(2), [], now=T0 + timedelta(days=30))
    assert all(x["direction"] == "none" for x in none["rows"]) and none["basedOn"] == 0
    assert {x["key"]: x for x in none["rows"]}["watch_through"]["note"] == "no usual to compare with yet"
    zero = closeloop.before_after(lr(3, saved=0.0), [lr(4, saved=0.0)], now=T0 + timedelta(days=30))
    assert {x["key"]: x for x in zero["rows"]}["saves_per_reach"]["direction"] == "same"                  # a real 0 vs a real 0


@aio
async def test_links_survive_a_reimport_are_replaced_per_analysis_and_deleted_on_disconnect(monkeypatch):
    async def fake_ask(content, max_tokens, spend, *, system=None):
        spend.add(0.001)
        return json.dumps({"hook_type": "question", "format": "talking_head", "pacing": "fast", "audio_type": "speech", "on_screen_text": True, "topic": "t"})
    monkeypatch.setattr(D, "_ask", fake_ask)
    async with dbf() as factory:
        uid = await make_user(factory)
        aid = uuid.uuid4()
        async with factory() as db:
            await db.execute(text("INSERT INTO prysm_analyses (id, user_id, created_at, overall_score, summary, top_priorities, parameters, raw_result) "
                                  "VALUES (:i, :u, :c, 63, 'Strong angle', CAST(:tp AS JSON), CAST(:pa AS JSON), CAST(:rr AS JSON))"),
                             {"i": aid, "u": uid, "c": datetime.now(timezone.utc) - timedelta(days=20), "tp": json.dumps(["Open on the result", "Cut the intro"]),
                              "pa": json.dumps([{"name": "Hook", "score": 40, "fix": "Use a question hook"}]), "rr": json.dumps({"energyCurve": {"duration": 22.0}})})
            await oauth.save_connection(db, uid, token="tok", ig_user_id="ig1", username="me", account_type="BUSINESS", expires_in=3600, scopes="a", is_fixture=True)
            await db.commit()
        await importer.run_import(redis=Redis(), session_factory=factory, user_id=uid, count=5, job_id="jl", source=FakeSource(5))
        async with factory() as db:
            lst = await closeloop.list_analyses(db, uid)
            assert len(lst) == 1 and lst[0]["duration_s"] == 22.0 and [a["text"] for a in lst[0]["advice"]] == ["Open on the result", "Cut the intro", "Use a question hook"]
            link = await closeloop.save_link(db, uid, lst[0], "m2", ["Open on the result", "Cut the intro"], used_auto_edit=False)
            again = await closeloop.save_link(db, uid, lst[0], "m3", ["Cut the intro"], used_auto_edit=False)          # linking again replaces
            data = await personalise.load(db, uid)
            out = await closeloop.links_with_results(db, uid, data["reels"])
            assert len(out) == 1 and out[0]["mediaId"] == "m3" and out[0]["applied"] == ["Cut the intro"] and out[0]["id"] == str(again.id) != str(link.id)
            assert out[0]["result"]["basedOn"] == 4 and out[0]["reel"]["n"] >= 1
        await importer.run_import(redis=Redis(), session_factory=factory, user_id=uid, count=5, job_id="jl2", source=FakeSource(5))    # re-import replaces every reel row
        async with factory() as db:
            data = await personalise.load(db, uid)
            out = await closeloop.links_with_results(db, uid, data["reels"])
            assert len(out) == 1 and out[0]["reel"] is not None and out[0]["result"] is not None                       # the link found its reel again by media id
            counts = await oauth.disconnect(db, uid)
            assert counts["links"] == 1
            assert (await db.execute(select(IgFixLink).where(IgFixLink.user_id == uid))).scalars().all() == []


def test_new_video_hook_words_come_from_the_first_12_seconds_of_the_analysis_transcript():
    words = [{"word": w, "start": float(i)} for i, w in enumerate("Stop scrolling right now because your hook is flopping badly today my friend we fix it".split())]
    out = personalise.new_hook_words({"audioAnalysis": {"words": words}})
    assert out.startswith("Stop scrolling right now") and len(out.split()) == 12 and "fix it" not in out            # words after 12 s are left out
    assert personalise.new_hook_words({"audioAnalysis": {"hookText": "Thank you"}}) == "Thank you"
    assert personalise.new_hook_words({"audioAnalysis": {"hookText": "Hi"}}) is None and personalise.new_hook_words({}) is None


def test_the_comparison_card_lists_the_cited_reels_in_plain_language():
    reels = [mk(1) | {"hook_words": "Stop scrolling", "vs_usual": {"label": "Reach 25% above your usual"}},
             mk(2) | {"hook_words": None, "vs_usual": {"label": "Reach 40% below your usual"}, "partial": True}]
    data = {"reels": [{**r, "partial": r.get("partial", False)} for r in reels]}
    out = personalise.cited_reels(data, [1], [2])
    assert [(o["n"], o["group"], o["vsUsual"], o["hookWords"], o["partial"]) for o in out] == [
        (1, "strongest", "Reach 25% above your usual", "Stop scrolling", False), (2, "weakest", "Reach 40% below your usual", None, True)]
