"""Hackathon-built (Meta Global AI Developer Hackathon).

Offline tests (no network) for Stage 1B: tokenizer, alignment, consensus voting and timing,
caption helpers and the SRT writer, ASR scoring, and the audio listeners' retry logic
(driven by fake clients).
"""
import json

import pytest

from app.studio.consensus import (OutTok, Voter, align, build_consensus_from_voters, cut_segments, tokenize,
                                  validate_timing)
from app.studio.eval_asr import caption_words, edit_distance, score
from app.studio.meta_client import GeminiCall, MetaError, WatchCall
from app.studio.report import CaptionCue, ConsensusSegment, ConsensusStage, OnScreenText
from app.studio.stages.audio import build_audio_stage, run_gemini_audio, run_spark_audio
from app.studio.stages.captions import (assemble_cues, format_srt, srt_time, validate_cue, words_subset, wrap_lines,
                                        write_srt)


def words(text, t0=0.0, step=0.4):
    return [{"word": w, "start": round(t0 + i * step, 2), "end": round(t0 + (i + 1) * step, 2)}
            for i, w in enumerate(text.split())]


def segs(text, t0=0.0, step=0.4):
    n = len(text.split())
    return [{"start": t0, "end": round(t0 + n * step, 2), "text": text}]


def consensus(voters, whisper_text, primary="whisper", t0=0.0):
    return build_consensus_from_voters(voters, words(whisper_text, t0), segs(whisper_text, t0), whisper_text,
                                       primary, duration_s=10.0)


def V(name, text, times=None):
    return Voter(name, tokenize(text), times)


# ---------------------------------------------------------------- tokenizer

def test_tokenize_lowercases_and_strips_punctuation():
    assert [t[0] for t in tokenize("It's a pre-draped SARI, from a designer!")] == \
        ["its", "a", "pre", "draped", "sari", "from", "a", "designer"]


def test_tokenize_keeps_devanagari_words_whole_including_vowel_signs():
    toks = tokenize("नैरो सलवार सूट रेड, मुंडियां।")
    assert [t[0] for t in toks] == ["नैरो", "सलवार", "सूट", "रेड", "मुंडियां"]


def test_tokenize_keeps_gurmukhi_and_mixed_scripts():
    assert [t[0] for t in tokenize("ਸਤ ਸ੍ਰੀ ਅਕਾਲ white gold")] == ["ਸਤ", "ਸ੍ਰੀ", "ਅਕਾਲ", "white", "gold"]


# ---------------------------------------------------------------- alignment

def test_align_maps_equal_replaced_inserted_and_deleted_words():
    a = align(["a", "b", "c", "d"], ["a", "x", "c", "extra", "d"])
    assert a.mapped == {0: 0, 1: 1, 2: 2, 3: 4}
    assert a.inserts == {3: [3]}  # "extra" sits in the gap before ref word 3
    b = align(["a", "b", "c"], ["a", "c"])
    assert b.mapped[1] is None and (b.lo, b.hi) == (0, 2)


# ---------------------------------------------------------------- voting

def test_two_voters_outvote_the_reference():
    c = consensus([V("gemini", "he knelt to the ground"), V("spark", "He knelt to the ground"),
                   V("voice_transcribe", "now to the ground")], "he felt to the ground")
    assert c.status == "ok"
    assert c.consensus_text.lower() == "he knelt to the ground"
    low = {w.word: w.confidence for w in c.low_confidence_words}
    assert low == {}  # 2 of 4 voters agree on "knelt" = 0.5, which is not below the threshold
    assert c.segments[0].timing == "whisper"


def test_a_word_nobody_agrees_on_goes_to_the_primary_listener():
    voters = [V("voice_transcribe", "red suit"), V("gemini", "bed suit")]
    assert "red" in consensus(voters, "led suit", primary="voice_transcribe").consensus_text
    assert "led" in consensus(voters, "led suit", primary="whisper").consensus_text
    flagged = consensus(voters, "led suit", primary="whisper").low_confidence_words
    assert [w.word for w in flagged] == ["led"] and flagged[0].votes == {"whisper": "led", "voice_transcribe": "red",
                                                                         "gemini": "bed"}


def test_words_the_reference_missed_need_two_voters_or_the_primary():
    voters = [V("gemini", "pulled out a ring and said"), V("spark", "pulled out a ring and said")]
    assert consensus(voters, "pulled out a ring", primary="whisper").consensus_text.endswith("and said")
    one = [V("gemini", "pulled out a ring and said")]
    assert consensus(one, "pulled out a ring", primary="whisper").consensus_text == "pulled out a ring"  # 1 voter only
    assert consensus(one, "pulled out a ring", primary="gemini").consensus_text.endswith("and said")  # primary's words


def test_consensus_never_contains_a_word_no_voter_produced():
    voters = [V("voice_transcribe", "alpha beta gamma delta"), V("gemini", "alpha bet gamma delta epsilon"),
              V("spark", "alpha beta gam")]
    c = consensus(voters, "alpha bata gamma", primary="voice_transcribe")
    produced = {t[0] for v in voters for t in v.tokens} | {"alpha", "bata", "gamma"}
    assert {t[0] for t in tokenize(c.consensus_text)} <= produced


def test_a_voter_that_does_not_cover_a_position_abstains():
    # Spark only quotes the first words; it must not count as dissent on the rest
    c = consensus([V("gemini", "one two three four five six"), V("spark", "one two")], "one two three four five six")
    assert c.low_confidence_words == [] and c.consensus_text == "one two three four five six"


def test_devanagari_consensus_with_voice_transcribe_primary():
    whisper = "नेरो सलवार सूट रेड मुंडियां"
    voters = [V("voice_transcribe", "नैरो खलवार सूट रेड मुंडयां"), V("gemini", "नेरो सलवार सूट रेड मुंडियां"),
              V("spark", "nero salwar suit red")]  # Roman script: not an Indic voter, takes no part
    c = consensus(voters, whisper, primary="voice_transcribe")
    assert c.status == "ok" and c.segment_modes[0].mode == "indic"
    assert c.segment_modes[0].whisper_role == "timing-only" and "whisper" not in c.voters_used
    assert "spark" not in c.voters_used
    # two Indic voters disagree on every variant: ties go to the primary listener, Voice Transcribe
    assert c.consensus_text == "नैरो खलवार सूट रेड मुंडयां"
    assert c.timing_source == "approximate" and all(s.timing == "approximate" for s in c.segments)


def test_voice_transcribe_primary_spreads_text_across_whisper_segment_times():
    c = build_consensus_from_voters([V("voice_transcribe", "एक दो तीन चार पांच छह सात आठ")],
                                    words("एक दो तीन चार", 0.0), segs("एक दो तीन चार", 0.0),
                                    "एक दो तीन चार", "voice_transcribe", duration_s=10.0)
    assert c.consensus_text == "एक दो तीन चार पांच छह सात आठ"  # primary's insertions are kept
    starts = [s.start_s for s in c.segments]
    assert starts == sorted(starts) and c.segments[0].start_s >= 0 and c.segments[-1].end_s <= 1.7
    assert all(s.timing == "approximate" for s in c.segments)


def test_ocr_voter_only_counts_where_its_time_range_overlaps():
    ocr_wrong = Voter("ocr", tokenize("completely different words"), [(20.0, 22.0)] * 3)
    ocr_right = Voter("ocr", tokenize("he knelt to the ground"), [(0.0, 2.0)] * 5)
    base = [V("gemini", "he knelt to the ground")]
    ref = "he felt to the ground"
    assert "felt" in consensus(base + [ocr_wrong], ref).consensus_text  # only gemini disagrees
    c = consensus([V("voice_transcribe", "he felt to the ground")] + [ocr_right], "he felt to the ground")
    assert c.consensus_text == "he felt to the ground"  # whisper+voice agree (2) over the ocr (1)
    knelt = consensus(base + [ocr_right], ref)
    assert "knelt" in knelt.consensus_text  # gemini + ocr outvote whisper where they overlap in time


def test_missing_whisper_falls_back_to_another_voter_with_approximate_timing():
    c = build_consensus_from_voters([V("voice_transcribe", "hello there friend"), V("gemini", "hello there friend")],
                                    [], [], "", "voice_transcribe", duration_s=3.0)
    assert c.status == "ok" and c.consensus_text == "hello there friend"
    assert c.timing_source == "approximate" and any("whisper had no words" in n for n in c.notes)


def test_no_voters_is_an_error_not_a_crash():
    assert build_consensus_from_voters([], [], [], "", None, 5.0).status == "error"


# ---------------------------------------------------------------- segmenting

def _tok(word, start, end, seg=0, source="word"):
    return OutTok(word, word, 1.0, seg, start, end, source, {})


def test_cut_segments_respects_the_caption_limits_and_never_overlaps():
    toks = [_tok(f"word{i}", i * 0.5, i * 0.5 + 0.5) for i in range(40)]
    out = cut_segments(toks, respect_segments=True)
    assert len(out) > 1
    for s, nxt in zip(out, out[1:] + [None]):
        assert s.end_s - s.start_s <= 3.01 and len(s.text) <= 60
        if nxt:
            assert s.end_s <= nxt.start_s + 1e-6


def test_cut_segments_stretches_a_short_last_cue_to_one_second():
    out = cut_segments([_tok("hi", 0.0, 0.3, seg=0), _tok("there", 5.0, 5.3, seg=1)], respect_segments=True)
    assert [round(s.end_s - s.start_s, 2) for s in out] == [1.0, 1.0]


# ---------------------------------------------------------------- captions / SRT

def test_srt_time_formats_milliseconds_and_hours():
    assert srt_time(0) == "00:00:00,000"
    assert srt_time(3661.5) == "01:01:01,500"
    assert srt_time(1.9996) == "00:00:02,000"


def test_srt_writer_output(tmp_path):
    cues = [CaptionCue(index=0, start_s=0.0, end_s=2.5, lines=["He knelt to the ground"], text="x"),
            CaptionCue(index=1, start_s=2.5, end_s=4.0, lines=["and pulled out", "a ring"], text="y")]
    assert format_srt(cues) == ("1\n00:00:00,000 --> 00:00:02,500\nHe knelt to the ground\n\n"
                                "2\n00:00:02,500 --> 00:00:04,000\nand pulled out\na ring\n")
    path = write_srt(cues, tmp_path / "sub" / "captions.srt")
    assert path.read_text(encoding="utf-8") == format_srt(cues)


def test_wrap_lines_balances_and_rejects_the_impossible():
    assert wrap_lines("short line") == ["short line"]
    two = wrap_lines("This is exactly what I mean by stop wearing")
    assert len(two) == 2 and all(len(x) <= 32 for x in two)
    assert wrap_lines("x" * 40) is None
    assert wrap_lines("word " * 20) is None  # over 64 characters


def test_validate_cue_blocks_added_words_and_long_lines():
    src = "he knelt to the ground"
    assert validate_cue(["He knelt to", "the ground."], src, translating=False) is None
    assert "added words" in validate_cue(["He knelt to the ground, sadly"], src, translating=False)
    assert "limit" in validate_cue(["x" * 40], src, translating=False)
    assert "limit" in validate_cue(["a", "b", "c"], src, translating=False)
    assert validate_cue(["Il s'agenouilla"], src, translating=True) is None  # translation may use new words
    assert words_subset("knelt ground", src) and not words_subset("ground ground", src)


def test_assemble_cues_keeps_consensus_timing_and_falls_back_on_bad_model_output():
    segments = [ConsensusSegment(start_s=0.0, end_s=2.0, text="he knelt to the ground", confidence=1, timing="whisper"),
                ConsensusSegment(start_s=2.0, end_s=4.0, text="and said yes", confidence=1, timing="whisper"),
                ConsensusSegment(start_s=4.0, end_s=5.0, text="the end", confidence=1, timing="whisper")]
    proposals = {0: {"lines": ["He knelt to the ground."], "edits": ["capitalised, added full stop"]},
                 1: {"lines": ["and said yes and more"], "edits": []}}  # adds words -> fallback; segment 2 missing
    cues, edits, fallbacks = assemble_cues(segments, proposals, translating=False)
    assert [(c.start_s, c.end_s) for c in cues] == [(0.0, 2.0), (2.0, 4.0), (4.0, 5.0)]
    assert cues[0].lines == ["He knelt to the ground."] and cues[1].lines == ["and said yes"]
    assert cues[2].lines == ["the end"] and fallbacks == 2
    assert any("added words" in e for e in edits) and any("no cue" in e for e in edits)


# ---------------------------------------------------------------- eval scoring

def test_edit_distance_and_score():
    assert edit_distance(["a", "b", "c"], ["a", "x", "c"]) == 1
    assert score(["a", "b", "c", "d"], ["a", "b", "c", "d"]) == 1.0
    assert score(["a", "b", "c", "d"], ["a", "b"]) == 0.5
    assert score([], ["a"]) is None
    assert score(["a"], ["x", "y", "z"]) < 0  # a very wrong transcript can go below zero


def test_caption_words_orders_by_time_and_drops_repeats():
    items = [OnScreenText(text="by stop wearing", start_s=1.0, end_s=2.0),
             OnScreenText(text="This is exactly", start_s=0.0, end_s=1.0),
             OnScreenText(text="by stop wearing", start_s=2.0, end_s=3.0)]
    assert caption_words(items) == ["this", "is", "exactly", "by", "stop", "wearing"]
    assert caption_words(items, until_s=1.0) == ["this", "is", "exactly"]


# ---------------------------------------------------------------- audio listeners (fake clients)

SPARK_OK = json.dumps({"quoted_words": "he knelt to the ground", "music_present": True, "language": "English",
                       "voice_music_balance": "voice over music", "quality_problems": []})
GEMINI_OK = json.dumps({"transcript": "he knelt to the ground", "language": "English", "speech_ratio": 0.8,
                        "music": {"present": True, "description": "pop", "mood": "romantic", "energy_0_10": 6},
                        "voice_quality": {"clarity_0_10": 8, "noise": "none", "issues": []},
                        "av_sync": {"ok": True, "notes": "in sync"}, "silence_segments": [], "loudness_issues": "",
                        "hook_audio": "sung line"})


class FakeSpark:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def watch_video(self, path, prompt, model):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return WatchCall(text=answer, tokens_in=100, tokens_out=50, cost_usd=0.001, seconds=0.1, model=model)


def test_spark_audio_retries_no_audio_then_succeeds(tmp_path):
    client = FakeSpark(["NO AUDIO", "I hear nothing useful", SPARK_OK])
    res = run_spark_audio(client, tmp_path / "p.mp4", attempts=3)
    assert res.status == "ok" and res.attempts == 3 and res.quoted_words == "he knelt to the ground"
    assert res.cost_usd == pytest.approx(0.003) and res.usage.input_tokens == 300


def test_spark_audio_gives_up_after_three_attempts_with_the_attempt_count(tmp_path):
    client = FakeSpark(["NO AUDIO", "NO AUDIO", "NO AUDIO", SPARK_OK])
    res = run_spark_audio(client, tmp_path / "p.mp4", attempts=3)
    assert res.status == "error" and res.attempts == 3 and client.calls == 3
    assert "NO AUDIO" in res.error and "3 attempts" in res.error


class FakeGemini:
    def __init__(self, plan):
        self.plan, self.calls = plan, []

    def gemini(self, model, prompt, path, mime, retries=None):
        self.calls.append((model, mime))
        result = self.plan[model]
        if isinstance(result, Exception):
            raise result
        return GeminiCall(text=result, model=model, tokens_in=1000, tokens_out=200, thinking_tokens=300,
                          cost_usd=0.01, seconds=0.2)


def test_gemini_falls_back_to_flash_and_records_it(tmp_path):
    proxy = tmp_path / "p.mp4"
    proxy.write_bytes(b"x" * 1000)
    client = FakeGemini({"gemini-2.5-pro": MetaError("gemini: HTTP 404: model not found"), "gemini-2.5-flash": GEMINI_OK})
    res = run_gemini_audio(client, proxy, None)
    assert res.status == "ok" and res.model_used == "gemini-2.5-flash" and res.fallback_used is True
    assert res.media_sent == "video" and res.av_sync.ok is True and res.loudness_issues == []
    assert res.thinking_tokens == 300 and res.cost_is_approximate is True


def test_gemini_sends_the_wav_and_marks_av_sync_not_assessed_when_the_proxy_is_too_big(tmp_path, monkeypatch):
    from app.studio import config
    monkeypatch.setattr(config, "GEMINI_MAX_VIDEO_BYTES", 10)
    proxy, wav = tmp_path / "p.mp4", tmp_path / "a.wav"
    proxy.write_bytes(b"x" * 100)
    wav.write_bytes(b"w" * 5)
    client = FakeGemini({"gemini-2.5-pro": GEMINI_OK})
    res = run_gemini_audio(client, proxy, wav)
    assert client.calls == [("gemini-2.5-pro", "audio/wav")]
    assert res.media_sent == "wav" and res.av_sync.notes == "not assessed" and res.av_sync.ok is None


def test_gemini_retries_unparseable_json_once_and_errors_when_both_models_fail(tmp_path):
    proxy = tmp_path / "p.mp4"
    proxy.write_bytes(b"x")
    client = FakeGemini({"gemini-2.5-pro": "not json", "gemini-2.5-flash": "still not json"})
    res = run_gemini_audio(client, proxy, None)
    assert res.status == "error" and res.attempts == 4 and "unparseable" in res.error


def test_audio_stage_is_partial_when_one_listener_fails_and_error_when_both_do(tmp_path):
    from app.studio.report import GeminiAudioResult, SparkAudioResult
    ok_spark = SparkAudioResult(status="ok", cost_usd=0.01, seconds=3.0)
    bad_gemini = GeminiAudioResult(status="error", error="boom", cost_usd=0.0, seconds=5.0)
    stage = build_audio_stage(ok_spark, bad_gemini)
    assert stage.status == "partial" and "gemini: boom" in stage.error and stage.seconds == 5.0
    assert build_audio_stage(SparkAudioResult(status="error", error="a"), bad_gemini).status == "error"


def test_foreign_script_words_are_dropped_per_word_but_the_voters_other_words_still_vote():
    # Spark quotes English then Hindi; the reference is Latin, so only the Devanagari tail is dropped
    spark = V("spark", "he knelt to the ground ओह मेरी हर मनमानी")
    c = consensus([spark, V("gemini", "he knelt to the ground")], "he felt to the ground")
    assert c.consensus_text == "he knelt to the ground"  # spark + gemini outvote whisper's "felt"
    assert any("spark: dropped 4 word(s) written in devanagari" in n for n in c.notes)
    assert "spark" in c.voters_used


def test_primary_in_another_script_than_whisper_becomes_the_reference():
    # Whisper heard Latin phonetics for Hindi lyrics; Voice Transcribe (primary) wrote Devanagari
    c = consensus([V("voice_transcribe", "मेरी हर मनमानी बस तुम तक")], "my mercy is all about you",
                  primary="voice_transcribe")
    assert c.consensus_text == "मेरी हर मनमानी बस तुम तक" and c.ref_voter == "voice_transcribe"
    assert any("used as the reference" in n for n in c.notes) and c.timing_source == "approximate"
    assert c.segments[0].start_s >= 0 and c.segments[-1].end_s <= 10


def test_a_word_only_one_voter_heard_is_not_fully_confident_when_others_were_available():
    c = consensus([V("gemini", "one two"), V("voice_transcribe", "one two")], "one two three four five six")
    by_word = {w.word: w.confidence for w in c.low_confidence_words}
    assert c.consensus_text.startswith("one two three four") and "three" in by_word and by_word["three"] == pytest.approx(1 / 3, abs=0.01)
    assert all(s.confidence < 1 for s in c.segments)


def test_an_inserted_word_repeating_its_neighbour_is_not_added_as_a_stutter():
    voters = [V("gemini", "red suit suit and more"), V("spark", "red suit suit and more")]
    c = consensus(voters, "red suit and more", primary="whisper")
    assert c.consensus_text == "red suit and more"


def test_a_hallucinated_word_is_dropped_when_three_voters_covered_the_position_without_it():
    voters = [V("voice_transcribe", "so keep the designer"), V("gemini", "so keep the designer"),
              V("spark", "so keep the designer")]
    # whisper alone inserted "I'm going to go" which all three others skip
    c = consensus(voters, "so keep I'm going to go the designer", primary="whisper")
    assert c.consensus_text == "so keep the designer"
    # but with only two covering voters the primary's word survives (flagged low confidence)
    two = consensus(voters[:2], "so keep I'm going to go the designer", primary="whisper")
    assert "going" in two.consensus_text and any(w.word == "going" for w in two.low_confidence_words)


# ---------------------------------------------------------------- fix 1: indic alignment

def test_indic_mode_three_devanagari_voters_beat_whisper_latin_and_keep_whisper_timing():
    whisper = "my mercy is all about you the words of your life"  # Latin phonetics for Hindi lyrics
    voters = [V("voice_transcribe", "ओह मेरी हर मनमानी बस तुम तक बातें बचकानी"),
              V("gemini", "ओ मेरी हर मनमानी बस तुम तक बातें बचकानी"),
              V("spark", "ओह मेरी हर मनमानी बस तुम तक बातें बचकानी")]
    c = build_consensus_from_voters(voters, words(whisper, 4.0, 0.4), segs(whisper, 4.0, 0.4), whisper,
                                    primary="whisper", duration_s=10.0)
    assert c.status == "ok"
    assert c.consensus_text == "ओह मेरी हर मनमानी बस तुम तक बातें बचकानी"  # Devanagari; 2 of 3 agree on ओह
    assert not any("a" <= ch <= "z" for ch in c.consensus_text)  # none of whisper's Latin words
    assert [m.mode for m in c.segment_modes] == ["indic"] and c.segment_modes[0].whisper_role == "timing-only"
    assert "3 voters" in c.segment_modes[0].reason and "whisper is timing-only" in c.segment_modes[0].reason
    assert "whisper" not in c.voters_used and set(c.voters_used) == {"voice_transcribe", "gemini", "spark"}
    # whisper keeps the timing: every cue sits inside the whisper segment (4.0 - 8.4 s)
    assert all(4.0 <= s.start_s and s.end_s <= 8.45 and s.mode == "indic" and s.whisper_role == "timing-only"
               for s in c.segments)
    assert any("whisper segment 0: timing-only" in n for n in c.notes)


def test_two_indic_voters_are_enough_but_one_is_not():
    whisper = "my mercy is all about you"
    two = [V("gemini", "मेरी हर मनमानी बस तुम तक"), V("spark", "मेरी हर मनमानी बस तुम तक")]
    assert consensus(two, whisper).segment_modes[0].mode == "indic"
    one = consensus([V("gemini", "मेरी हर मनमानी बस तुम तक")], whisper)
    assert one.segment_modes[0].mode == "latin" and "fewer than 2" in one.segment_modes[0].reason


def test_skeleton_is_the_indic_voter_that_agrees_most_with_the_others():
    whisper = "x y z a b c d e"
    voters = [V("voice_transcribe", "एक दो तीन चार पांच छह सात आठ"),
              V("gemini", "एक दो तीन चार पांच छह सात आठ"),
              V("spark", "एक दो चार पांच")]  # the outlier
    c = consensus(voters, whisper, primary="spark")  # the primary listener is not the skeleton: agreement decides
    assert c.consensus_text == "एक दो तीन चार पांच छह सात आठ"
    assert any("skeleton=voice_transcribe" in n or "skeleton=gemini" in n for n in c.notes)


def test_mixed_english_then_hindi_is_decided_per_whisper_segment():
    # seg 0: correct English; seg 1: Whisper's Latin phonetics for what is sung in Hindi
    w = words("he knelt to the ground", 0.0, 0.4) + words("oh my mercy is all about you", 2.4, 0.4)
    segments = [{"start": 0.0, "end": 2.0, "text": "he knelt to the ground"},
                {"start": 2.4, "end": 5.2, "text": "oh my mercy is all about you"}]
    hindi = "ओह मेरी हर मनमानी बस तुम तक"
    voters = [V("spark", f"he knelt to the ground {hindi}"), V("gemini", f"he knelt to the ground {hindi}"),
              V("voice_transcribe", "he felt to the ground")]
    c = build_consensus_from_voters(voters, w, segments, "x", primary="whisper", duration_s=6.0)
    assert [m.mode for m in c.segment_modes] == ["latin", "indic"]
    assert c.consensus_text == f"he knelt to the ground {hindi}"
    modes = [s.mode for s in c.segments]
    assert modes[0] == "latin" and modes[-1] == "indic" and modes == sorted(modes, key=["latin", "indic"].index)
    assert all(s.end_s <= 2.05 for s in c.segments if s.mode == "latin")  # english cues keep whisper's own times
    assert all(2.4 <= s.start_s and s.end_s <= 5.25 for s in c.segments if s.mode == "indic")
    assert "latin" in c.segment_modes[0].reason or "Indic" in c.segment_modes[0].reason
    assert c.timing_source == "mixed"


# ---------------------------------------------------------------- fix 2: caption timing

def test_a_hallucinated_tail_word_does_not_lend_its_timestamp_to_the_next_kept_word():
    # Whisper heard "I'm going to go and make myself" at 30 s (music); the others heard "comment" just before "down"
    w = words("falling apart", 20.0, 0.5) + words("im going to go and make myself", 30.1, 0.4) \
        + words("down to know the designer", 33.7, 0.36)
    segments = [{"start": 20.0, "end": 35.5, "text": "falling apart im going to go and make myself down to know the designer"}]
    tail = "falling apart comment down to know the designer"
    voters = [V("voice_transcribe", tail), V("gemini", tail), V("spark", tail),
              Voter("ocr", tokenize("comment down to know the designer"), [(34.5, 36.5)] * 6)]
    c = build_consensus_from_voters(voters, w, segments, "x", primary="whisper", duration_s=40.0)
    assert c.consensus_text == tail  # the seven hallucinated words are dropped
    cue = next(s for s in c.segments if "comment" in s.text)
    assert cue.start_s >= 33.0  # not 30.1, the time of the hallucinated word it replaced
    assert cue.text.endswith("down to know the designer") and cue.end_s <= 35.6
    first = next(s for s in c.segments if "falling" in s.text)
    assert first.end_s <= 23.0 and "comment" not in first.text  # no sentence-splitting pair spanning the gap
    assert c.warnings == [] or all("under 0.8" in w_ for w_ in c.warnings)


def test_unreliable_cue_times_fall_back_to_the_whisper_segment_bounds():
    toks = [OutTok("a", "a", 1, 3, None, None, "interp", {}, timeless=True),
            OutTok("b", "b", 1, 3, None, None, "interp", {}, timeless=True)]
    for t, (a, b) in zip(toks, [(10.0, 10.2), (10.2, 10.4)]):
        t.start, t.end = a, b  # guessed times
    out = cut_segments(toks, respect_segments=True, seg_bounds=[(0, 1), (1, 2), (2, 3), (11.0, 12.5)])
    assert (out[0].start_s, out[0].end_s) == (11.0, 12.5)


def test_short_cues_are_merged_into_a_neighbour_when_they_fit_two_lines_and_three_seconds():
    toks = [_tok("so", 0.0, 0.3, seg=0), _tok("keep", 0.3, 0.6, seg=0),  # 0.6 s cue
            _tok("the", 0.7, 1.0, seg=1), _tok("attention", 1.0, 1.8, seg=1), _tok("on", 1.8, 2.2, seg=1)]
    out = cut_segments(toks, respect_segments=True)
    assert [s.text for s in out] == ["so keep the attention on"]
    assert (out[0].start_s, out[0].end_s) == (0.0, 2.2)


def test_short_cues_are_not_merged_when_the_result_is_too_long_too_wide_or_bridges_a_pause():
    # merged cue would be 4.5 s
    long_ = cut_segments([_tok("hi", 0.0, 0.3, seg=0), _tok("there", 0.5, 4.5, seg=1)], respect_segments=True)
    assert [s.text for s in long_] == ["hi", "there"]
    # a 2 s pause between them
    pause = cut_segments([_tok("hi", 0.0, 0.3, seg=0), _tok("there", 2.3, 3.6, seg=1)], respect_segments=True)
    assert len(pause) == 2
    # too wide for two lines of 32 characters
    wide = cut_segments([_tok("x" * 30, 0.0, 0.5, seg=0), _tok("y" * 30, 0.5, 1.0, seg=0),
                         _tok("z" * 20, 1.0, 1.4, seg=1)], respect_segments=True)
    assert all(len(s.text) <= 64 for s in wide)
    # a cue never bridges a pause over 1 s even inside one Whisper segment
    gap = cut_segments([_tok("one", 0.0, 1.2, seg=0), _tok("two", 3.0, 4.2, seg=0)], respect_segments=True)
    assert [s.text for s in gap] == ["one", "two"]


class Cue:
    def __init__(self, a, b):
        self.start_s, self.end_s = a, b
        self.text = "x"


def test_validator_warns_on_out_of_order_overlapping_short_and_long_cues():
    assert validate_timing([Cue(0, 2), Cue(2, 4.5 - 0.5)]) == []
    w = validate_timing([Cue(5, 6), Cue(1, 2)])
    assert any("out of order" in x for x in w)
    w = validate_timing([Cue(0, 2), Cue(1.5, 3)])
    assert len(w) == 1 and "overlaps" in w[0]
    w = validate_timing([Cue(0, 0.5), Cue(1, 5)])
    assert any("under 0.8" in x for x in w) and any("longer than 3.5" in x for x in w)
    w = validate_timing([Cue(0, 0.79)])
    assert len(w) == 1
    assert validate_timing([Cue(0, 0.8), Cue(0.8, 4.3)]) == []  # boundaries are allowed


def test_the_validator_reports_through_the_consensus_and_caption_stages(tmp_path):
    from app.studio.stages.captions import run_captions
    bad = ConsensusStage(status="ok", segments=[
        ConsensusSegment(start_s=0.0, end_s=0.5, text="short one", confidence=1, timing="whisper"),
        ConsensusSegment(start_s=0.4, end_s=2.0, text="overlapping", confidence=1, timing="whisper")])

    class NoEditor:
        def complete(self, prompt, model):
            raise MetaError("no editor in this test")

    cap = run_captions(NoEditor(), bad, [], tmp_path / "c.srt")
    assert cap.status == "partial" and len(cap.cues) == 2  # deterministic captions still produced
    assert any("under 0.8" in x for x in cap.warnings) and any("overlaps" in x for x in cap.warnings)


def test_a_word_only_one_of_two_indic_voters_produced_is_flagged_low_confidence():
    voters = [V("voice_transcribe", "एक दो तीन चार"), V("gemini", "पांच छह सात आठ")]  # they never agree
    c = consensus(voters, "x y z w", primary="voice_transcribe")
    assert c.segment_modes[0].mode == "indic" and c.consensus_text == "एक दो तीन चार"
    assert [w.word for w in c.low_confidence_words] == ["एक", "दो", "तीन", "चार"]


def test_a_coincidental_vote_inside_a_hallucinated_run_neither_survives_with_a_wrong_time_nor_splits_the_cue():
    # the real 57419 tail: Whisper hallucinated words around 31 s; Voice Transcribe happens to share "and",
    # and writes an unrelated word ("on") where Whisper has "make"; the others say "comment down to know..."
    w = words("falling apart", 20.0, 0.5) + words("im going to go and make myself im going", 30.1, 0.4) \
        + words("down to know the designer", 33.7, 0.36)
    segments = [{"start": 20.0, "end": 35.5, "text": "x"}]
    voters = [V("voice_transcribe", "falling apart on and on to know the designer"),
              V("gemini", "falling apart comment down to know the designer"),
              V("spark", "falling apart comment down to know the designer")]
    c = build_consensus_from_voters(voters, w, segments, "x", primary="whisper", duration_s=40.0)
    assert "make" not in c.consensus_text and "going" not in c.consensus_text  # hallucinated words are gone
    cue = next(s for s in c.segments if "comment" in s.text)
    assert cue.start_s >= 32.5 and cue.text.endswith("down to know the designer")  # right before "down", not at 30-31 s
    assert not any(30.0 <= s.start_s <= 32.4 for s in c.segments)


def test_spelling_variants_are_not_contradictions_but_unrelated_words_are():
    from app.studio.consensus import _unrelated
    assert not _unrelated("मनमानी", "मन") and not _unrelated("नैरो", "नेरो") and not _unrelated("red", "bed")
    assert _unrelated("make", "on") and _unrelated("going", "comment")
