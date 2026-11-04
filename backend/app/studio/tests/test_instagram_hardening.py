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
from app.studio.instagram.models import CreatorDna, IgAccount, IgReel
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
