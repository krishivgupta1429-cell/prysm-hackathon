"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests (no network) for the creator inputs of the analysis: niche parsing, the baseline-style weight
lookup, the creator-context evidence item (unverified, never evidence), description_fit validation, the
analysis stage without model niche detection, and the perception cache by video hash.
"""
import asyncio
import json

import pytest

from app.studio import config, perception_cache
from app.studio.analysis import niches
from app.studio.analysis.creator import (CALIBRATED, HOST_WEIGHTS_LABEL, NICHE_NOTES, PROFILE_FOR, WEBSITE_NICHES, CreatorContext,
                                         canonical_name, parse_niches, resolve_niche, weights_by_id, weights_for)
from app.studio.analysis.evidence import build_evidence_pack
from app.studio.analysis.schema import DescriptionFit, EvidenceRef, Parameter
from app.studio.analysis.score import build_prompt
from app.studio.analysis.validate import validate_description_fit, validate_parameters
from app.studio.pipeline import analyze_video
from app.studio.stages.analysis import run_analysis
from app.studio.tests.test_studio_analysis import FakeClient, make_report, scoring_json


def with_fit(verdict="matches", ids=("E3",), note="the video is about sarees", **scores):
    data = json.loads(scoring_json(scores or None, evidence_id="E3"))  # E2 is the creator line in a creator pack
    data["description_fit"] = {"verdict": verdict, "note": note, "evidence": [{"id": i, "quote": ""} for i in ids]}
    return json.dumps(data)


# ---------------------------------------------------------------- parsing

def test_the_website_niche_string_becomes_one_to_three_niches_primary_first():
    assert parse_niches("Fashion, Lifestyle") == ["Fashion", "Lifestyle"]
    assert parse_niches("fashion,LIFESTYLE,food,travel") == ["Fashion", "Lifestyle", "Food"]  # canonical spelling, max 3
    assert parse_niches("Comedy, comedy, COMEDY") == ["Comedy"]
    assert parse_niches(["Dance", "Music"]) == ["Dance", "Music"]
    assert parse_niches("") == [] and parse_niches(None) == [] and parse_niches(" , ,") == []
    assert parse_niches("Health & Wellness, DIY & Crafts") == ["Health & Wellness", "DIY & Crafts"]
    assert parse_niches("General") == ["General"]  # the baseline accepts any text
    with pytest.raises(ValueError, match="unknown niche"):
        parse_niches("General", strict=True)
    assert canonical_name("talking_head_education") == "Education" and canonical_name("bollywood") == "Bollywood"


def test_every_website_niche_has_a_note_and_a_profile_for_the_per_parameter_notes():
    assert len(WEBSITE_NICHES) == 26 and WEBSITE_NICHES[0] == "Comedy" and WEBSITE_NICHES[-1] == "Other"
    for name in WEBSITE_NICHES:
        assert NICHE_NOTES[name] and PROFILE_FOR[name] in niches.NICHES, name
        assert len(NICHE_NOTES[name].split(". ")) <= 2


def test_creator_context_primary_extras_and_cleaning():
    c = CreatorContext.from_inputs("Fashion,Lifestyle", "  A saree\x00 try-on\n with   tips ")
    assert (c.primary, c.extras, c.description) == ("Fashion", ["Lifestyle"], "A saree try-on with tips")
    assert CreatorContext.from_inputs("", "").primary == "Other"
    assert len(CreatorContext.from_inputs("Fashion", "x" * 900).description) == 300
    assert "creator" not in c.summary().lower() and "primary: Fashion" in c.summary()


# ---------------------------------------------------------------- weights follow the baseline lookup

def test_calibrated_niches_use_their_profile_and_everything_else_the_default():
    for name, key in {"Comedy": "comedy", "Dance": "dance", "Fitness": "fitness"}.items():
        weights, label = weights_for(name)
        assert weights == niches.NICHES[key].weights and label == niches.BASELINE_LABEL
    assert weights_for("bollywood")[0] == niches.NICHES["bollywood_indian_creator"].weights == weights_for("IndianCreator")[0]
    for name in ("Fashion", "Lifestyle", "Education", "Sports", "General"):  # not calibrated: default weights
        weights, label = weights_for(name)
        assert weights == {p: round(1 / 6, 4) for p in niches.SCORED_IDS} and label == niches.FLAT_LABEL
    assert set(CALIBRATED) == {"comedy", "dance", "fitness", "bollywood", "indiancreator"}  # the baseline's keys


def test_the_host_apps_own_weights_replace_ours_and_names_map_to_ids():
    baseline_default = {"Hook & Opening": 0.19, "Retention & Pacing": 0.17, "Audio & Music": 0.16,
                        "Visual Quality & Originality": 0.16, "Trend Alignment": 0.16, "Shareability": 0.16}
    ids = weights_by_id(baseline_default)
    assert ids == {"hook": 0.19, "retention": 0.17, "audio": 0.16, "visual": 0.16, "trend": 0.16, "shareability": 0.16}
    weights, label = weights_for("Fashion", ids)
    assert weights == ids and label == HOST_WEIGHTS_LABEL


def test_resolve_niche_uses_the_primary_niche_and_keeps_extras_out_of_the_weights():
    c = CreatorContext.from_inputs("Beauty,Comedy", "makeup tips")
    n = resolve_niche(c)
    assert n.id == "Beauty" and n.weights == {p: round(1 / 6, 4) for p in niches.SCORED_IDS}  # Beauty is not calibrated: Comedy is only context
    assert n.judging_notes == niches.NICHES["fashion_beauty"].judging_notes and n.description.startswith("Show the transformation")
    assert resolve_niche(CreatorContext.from_inputs("Comedy,Beauty", "")).weights == niches.NICHES["comedy"].weights


# ---------------------------------------------------------------- the creator context is unverified

def test_the_creator_item_is_in_the_pack_labelled_unverified_but_never_citable():
    c = CreatorContext.from_inputs("Fashion,Lifestyle", "Saree styling tips for a wedding guest look")
    pack = build_evidence_pack(make_report(), c)
    item = next(i for i in pack.items if i.source == "creator context")
    assert pack.creator_item_id == item.id == "E2"
    assert "CREATOR-PROVIDED, UNVERIFIED" in item.text and "Fashion, Lifestyle" in item.text and "wedding guest" in item.text
    assert item.id in pack.ids() and item.id not in pack.citable_ids()
    assert build_evidence_pack(make_report()).creator_item_id is None  # no creator, no item


def test_citing_the_creator_line_is_dropped_and_leaves_the_parameter_unsupported():
    pack = build_evidence_pack(make_report(), CreatorContext.from_inputs("Fashion", "a saree video"))
    only_creator = Parameter(id="hook", score=90, confidence="high", evidence=[EvidenceRef(id=pack.creator_item_id)])
    mixed = Parameter(id="audio", score=70, confidence="high", evidence=[EvidenceRef(id=pack.creator_item_id), EvidenceRef(id="E3")])
    out, flags = validate_parameters([only_creator, mixed], pack)
    by = {p.id: p for p in out}
    assert by["hook"].unsupported and not by["hook"].evidence
    assert [e.id for e in by["audio"].evidence] == ["E3"] and by["audio"].confidence == "low" and not by["audio"].unsupported
    assert sum(f.code == "creator_context_not_evidence" for f in flags) == 2


def test_the_prompt_labels_the_context_unverified_and_extra_niches_are_context_only():
    c = CreatorContext.from_inputs("Fashion,Lifestyle,Food", "Saree styling tips")
    pack = build_evidence_pack(make_report(), c)
    prompt = build_prompt(pack, resolve_niche(c), c)
    assert "CREATOR DESCRIPTION (unverified, creator-provided, not evidence): Saree styling tips" in prompt
    assert "(context only, they do not change the scoring weights): Lifestyle, Food" in prompt
    assert "Never cite it" in prompt and '"description_fit"' in prompt and "NICHE FOCUS: Fashion" in prompt
    no_desc = CreatorContext.from_inputs("Fashion", "")
    assert '"description_fit": {"verdict"' not in build_prompt(build_evidence_pack(make_report(), no_desc), resolve_niche(no_desc), no_desc)
    assert "CREATOR" not in build_prompt(build_evidence_pack(make_report()), niches.get_niche("food"))  # the CLI path is unchanged


# ---------------------------------------------------------------- description_fit

def test_description_fit_follows_the_same_evidence_rule():
    pack = build_evidence_pack(make_report(), CreatorContext.from_inputs("Fashion", "a saree video"))
    ok, flags = validate_description_fit(DescriptionFit(verdict="matches", note="n", evidence=[EvidenceRef(id="E3"), EvidenceRef(id="E999")]), pack)
    assert [e.id for e in ok.evidence] == ["E3"] and not ok.unsupported
    assert [f.code for f in flags] == ["unknown_evidence_id"] and flags[0].parameter == "description_fit"
    creator_only, flags2 = validate_description_fit(DescriptionFit(verdict="off", evidence=[EvidenceRef(id=pack.creator_item_id)]), pack)
    assert creator_only.unsupported and creator_only.evidence == [] and {f.code for f in flags2} == {"unknown_evidence_id", "unsupported"}
    assert validate_description_fit(None, pack) == (None, [])
    assert DescriptionFit(verdict="MATCH").verdict == "matches" and DescriptionFit(verdict="who knows").verdict == "partial"


# ---------------------------------------------------------------- the stage without model niche detection

def test_with_a_creator_the_model_is_not_asked_for_a_niche_and_weights_come_from_the_host(tmp_path):
    client = FakeClient(scoring=[with_fit(), with_fit()])  # no niche answers queued: a niche call would fail
    creator = CreatorContext.from_inputs("Fashion,Lifestyle", "A saree try-on with styling tips")
    host = {"hook": 0.5, "retention": 0.1, "audio": 0.1, "visual": 0.1, "trend": 0.1, "shareability": 0.1}
    stage = run_analysis(make_report(), client, tmp_path / "a.json", creator=creator, weights_override=host)
    assert stage.status == "ok" and stage.niche == "Fashion" and stage.niche_source == "creator" and stage.niche_confidence == 1.0
    assert stage.niches == ["Fashion", "Lifestyle"] and len(client.calls) == 2 and all("Classify" not in c["prompt"] for c in client.calls)
    assert stage.overall_score == round(sum(70 * w for w in host.values()), 1) == 70.0
    data = json.loads((tmp_path / "a.json").read_text())
    assert data["niche"]["source"] == "creator" and data["niche_weights"]["weights"] == host
    assert data["creator_context"]["extra_niches_context_only"] == ["Lifestyle"] and "unverified" in data["creator_context"]["status"]
    assert data["description_fit"]["verdict"] == "matches" and stage.description_fit == {
        "verdict": "matches", "note": "the video is about sarees", "evidence": ["E3"], "unsupported": False}


def test_a_missing_or_unsupported_description_fit_is_flagged(tmp_path):
    creator = CreatorContext.from_inputs("Fashion", "a saree video")
    missing = run_analysis(make_report(), FakeClient(scoring=[scoring_json()]), tmp_path / "a.json", creator=creator, repeat=1)
    assert any("description_fit: missing" in f for f in missing.flags) and missing.description_fit is None
    bad = run_analysis(make_report(), FakeClient(scoring=[with_fit(ids=("E2",))]), tmp_path / "b.json", creator=creator, repeat=1)
    assert bad.description_fit["unsupported"] is True and any("description_fit: unsupported" in f for f in bad.flags)  # E2 = creator line
    no_desc = run_analysis(make_report(), FakeClient(scoring=[scoring_json()]), tmp_path / "c.json",
                           creator=CreatorContext.from_inputs("Food", ""), repeat=1)
    assert not any("description_fit" in f for f in no_desc.flags)  # nothing to judge without a description


def test_without_a_creator_the_cli_path_still_detects_the_niche(tmp_path):
    client = FakeClient(niche=[json.dumps({"niche": "food", "confidence": 0.9})], scoring=[scoring_json()])
    stage = run_analysis(make_report(), client, tmp_path / "a.json", repeat=1)
    assert stage.niche == "food" and stage.niche_source == "model" and len(client.calls) == 2


# ---------------------------------------------------------------- the perception cache

def test_the_cache_stores_and_returns_a_perception_report_by_hash_and_options(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"video bytes")
    sha = perception_cache.video_sha256(video)
    assert sha == perception_cache.video_sha256(video) and len(sha) == 64
    key = perception_cache.options_key(watch="m", sam_fps=10, captions_lang=None, skip=[], signals_local=False)
    assert key != perception_cache.options_key(watch="m", sam_fps=5, captions_lang=None, skip=[], signals_local=False)
    assert perception_cache.load(tmp_path, sha, key) is None
    report = make_report()
    perception_cache.save(tmp_path, sha, key, report)
    loaded = perception_cache.load(tmp_path, sha, key)
    assert loaded is not None and loaded.stages.watch.hook.what_happens == report.stages.watch.hook.what_happens
    assert perception_cache.load(tmp_path, sha, "another-key") is None  # different options: not reused
    video.write_bytes(b"different bytes")
    assert perception_cache.video_sha256(video) != sha


def test_a_report_with_a_failed_core_stage_is_never_cached_or_reused(tmp_path):
    bad = make_report()
    bad.stages.watch.status = "error"
    perception_cache.save(tmp_path, "abc", "k", bad)
    assert not (tmp_path / "_cache" / "abc").exists()
    ok = make_report()
    perception_cache.save(tmp_path, "abc", "k", ok)
    (tmp_path / "_cache" / "abc" / "report.json").write_text(bad.model_dump_json())
    assert perception_cache.load(tmp_path, "abc", "k") is None
    (tmp_path / "_cache" / "abc" / "report.json").write_text("{not json")
    assert perception_cache.load(tmp_path, "abc", "k") is None


def test_a_cached_video_reruns_only_the_analysis_when_the_niche_changes(tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"the same video")
    sha = perception_cache.video_sha256(video)
    key = perception_cache.options_key(watch="muse-spark-1.3", sam_fps=config.sam_fps(None), captions_lang=None, skip=[], signals_local=False)
    perception_cache.save(tmp_path, sha, key, make_report())
    events = []
    client = FakeClient(scoring=[scoring_json()])
    result = asyncio.run(analyze_video(video, tmp_path, ["muse-spark-1.3"], client=client, repeat=1,
                                       creator=CreatorContext.from_inputs("Comedy", ""), progress=lambda s, st: events.append((s, st))))
    assert result.cached is True and len(result.reports) == 1 and len(client.calls) == 1  # one scoring call, nothing else
    report = result.reports[0]
    assert report.video.path == str(video) and report.stages.analysis.status == "ok" and report.stages.analysis.niche == "Comedy"
    assert (tmp_path / "reel" / "analysis.json").exists()
    assert [e for e in events if e[1] == "cached"] == [(s, "cached") for s in
                                                        ("watch", "speech", "subjects", "audio", "signals", "consensus", "captions", "music_id")]
    assert events[-2:] == [("analysis", "running"), ("analysis", "ok")]
