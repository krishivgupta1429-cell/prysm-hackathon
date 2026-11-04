"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests for the Phase 2 evidence: each part is behind its own flag, the consensus transcript keeps the shape the
original prompt reads, Spark's listener only fills what the original audio listener left empty, a failed part changes
nothing, and the Models used rows carry the right labels.
"""
import numpy as np
import pytest

from app.studio import evidence as ev
from app.studio.report import (AudioStage, ConsensusSegment, ConsensusStage, DemucsResult, ModelSignal, MusicIdStage, MusicTrack,
                               SceneCuts, SignalsStage, SparkAudioResult, SpeechStage, SubjectsStage, SubjectTrack,
                               VoiceTranscribeResult, WhisperResult)


def consensus(text="hello world again", segs=((0.0, 1.5, "hello world"), (1.5, 3.0, "again"))):
    return ConsensusStage(status="ok", consensus_text=text, voters_used=["voice_transcribe", "whisper"],
                          segments=[ConsensusSegment(start_s=a, end_s=b, text=t, confidence=0.9, timing="whisper") for a, b, t in segs])


BASE_WHISPER = {"transcript": "hello word", "language": "English", "hasSpeech": True, "segments": [{"start": 0, "end": 2, "text": "hello word"}],
                "verbalCTA": "None detected", "hookText": "hello word"}


# ---------------------------------------------------------------- flags

def test_every_part_is_on_unless_its_flag_says_off(monkeypatch):
    for v in ev.FLAGS.values():
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(ev.config, "get_env", lambda name: None)
    assert all(ev.enabled_parts().values())
    monkeypatch.setattr(ev.config, "get_env", lambda name: "0" if name == "EVIDENCE_SUBJECTS" else None)
    assert ev.enabled_parts() == {"voice": True, "spark_audio": True, "subjects": False, "signals": True, "music": True}


def test_with_everything_off_nothing_runs_and_the_result_is_empty(monkeypatch):
    import asyncio
    monkeypatch.setattr(ev.config, "get_env", lambda name: "0")
    out = asyncio.run(ev.collect_evidence("/no/such.mp4", "/tmp/x"))
    assert out.speech is None and out.subjects is None and out.signals is None and out.notes == []


# ---------------------------------------------------------------- transcript

def test_consensus_replaces_the_whisper_transcript_in_the_shape_the_prompt_reads():
    e = ev.Evidence(consensus=consensus(), speech=SpeechStage(status="ok", whisper=WhisperResult(status="ok")))
    out = ev.merge_transcript(BASE_WHISPER, e)
    assert out["transcript"] == "hello world again" and out["hasSpeech"] is True
    assert out["segments"] == [{"start": 0.0, "end": 1.5, "text": "hello world"}, {"start": 1.5, "end": 3.0, "text": "again"}]
    assert out["hookText"] == "hello world again" and out["transcriptSources"] == ["voice_transcribe", "whisper"]
    assert out["verbalCTA"] == "None detected" and BASE_WHISPER["transcript"] == "hello word"  # the input is not mutated


@pytest.mark.parametrize("e", [ev.Evidence(), ev.Evidence(consensus=ConsensusStage(status="error", error="x")),
                               ev.Evidence(consensus=ConsensusStage(status="ok", consensus_text="  "))])
def test_a_missing_or_failed_consensus_leaves_the_original_transcript_alone(e):
    assert ev.merge_transcript(BASE_WHISPER, e) == BASE_WHISPER


# ---------------------------------------------------------------- audio listeners

def spark(**kw):
    return AudioStage(status="ok", spark=SparkAudioResult(status="ok", quoted_words="hello there", music_present=True,
                                                         voice_music_balance="voice on top", quality_problems=["hiss"], **kw))


def test_spark_fills_the_audio_keys_when_gemini_gave_nothing():
    out, text = ev.merge_audio({}, ev.Evidence(audio=spark()))
    assert out["hasMusic"] is True and out["speechSummary"] == "hello there" and out["voiceQuality"] == "voice on top" and text == ""
    assert out["listener"] == "muse-spark-1.2"


def test_a_song_the_acrcloud_call_confirmed_survives_the_spark_fill():
    out, _ = ev.merge_audio({"songTitle": "Tooh", "hasMusic": True, "songConfirmed": True}, ev.Evidence(audio=spark()))
    assert out["songTitle"] == "Tooh" and out["songConfirmed"] is True


def test_with_gemini_present_spark_is_a_second_opinion_in_text_and_changes_no_key():
    g = {"hasMusic": False, "voiceQuality": "clear"}
    out, text = ev.merge_audio(g, ev.Evidence(audio=spark()))
    assert out == g and "SECOND AUDIO LISTENER" in text and "hiss" in text


def test_a_failed_spark_listener_changes_nothing():
    e = ev.Evidence(audio=AudioStage(status="error", spark=SparkAudioResult(status="error")))
    assert ev.merge_audio({"x": 1}, e) == ({"x": 1}, "")


# ---------------------------------------------------------------- music id

def test_the_second_music_pass_only_applies_when_the_first_found_nothing():
    m = MusicIdStage(status="ok", matched=True, track=MusicTrack(title="Tooh", artists=["A", "B"], score=90))
    e = ev.Evidence(music=m)
    assert ev.merge_music({}, False, e)["songTitle"] == "Tooh" and ev.merge_music({}, False, e)["songConfirmed"] is True
    assert ev.merge_music({"songTitle": "Other"}, True, e) == {"songTitle": "Other"}
    assert ev.merge_music({}, False, ev.Evidence(music=MusicIdStage(status="ok", matched=False))) == {}


# ---------------------------------------------------------------- subjects and signals text

def test_subjects_text_states_facts_and_gaps():
    person = SubjectTrack(prompt="person", detected_frames=10, object_count=1, first_seen_s=0.0, screen_time_ratio=0.8,
                          visible_in_first_3s=True, mean_box_area_ratio=0.4, presence_per_second=[1, 1, 0, 0, 0, 1, 1])
    text = ev.subjects_text(ev.Evidence(subjects=SubjectsStage(status="ok", person=person)))
    assert "SAM 3.1" in text and "80% of the video" in text and "visible in the first 3 seconds" in text and "2-5s" in text
    assert ev.subjects_text(ev.Evidence()) == "" and ev.subjects_text(ev.Evidence(subjects=SubjectsStage(status="error"))) == ""


def test_signals_text_reports_cuts_voice_to_music_and_visual_change(tmp_path):
    sig_dir = tmp_path / "signals"
    sig_dir.mkdir()
    frames = np.array([[1, 0, 0], [1, 0.01, 0], [0, 1, 0], [0, 1, 0.01]], dtype=np.float32)
    np.save(sig_dir / "dinov2_frames.npy", frames)
    s = SignalsStage(status="ok", cuts=SceneCuts(status="ok", cut_times_s=[1.0, 2.0], avg_shot_length_s=1.5),
                     models={"dinov2": ModelSignal(status="ok", dims=3)}, demucs=DemucsResult(status="ok", voice_to_music_db=6.0))
    text = ev.signals_text(ev.Evidence(signals=s, run_dir=tmp_path))
    assert "Scene cuts: 2 at 1.0s, 2.0s" in text and "biggest jump" in text and "at 2s" in text
    assert "+6.0 dB" in text and "voice clearly on top" in text
    assert ev.signals_text(ev.Evidence(signals=SignalsStage(status="error"))) == ""


def test_embeddings_come_from_the_modal_files_only_when_the_dimensions_match(tmp_path):
    d = tmp_path / "signals"
    d.mkdir()
    np.save(d / "dinov2_mean.npy", np.ones(768, dtype=np.float32))
    np.save(d / "clap.npy", np.ones(100, dtype=np.float32))  # wrong size for the 512-d column
    s = SignalsStage(status="ok", models={"dinov2": ModelSignal(status="ok"), "clap": ModelSignal(status="ok")})
    out = ev.embeddings_from_signals(ev.Evidence(signals=s, run_dir=tmp_path))
    assert list(out) == ["visual"] and len(out["visual"]) == 768


# ---------------------------------------------------------------- Models used labels

def test_models_used_labels_meta_models_and_existing_models_correctly():
    parts = {k: True for k in ev.FLAGS}
    e = ev.Evidence(parts=parts, speech=SpeechStage(status="ok", voice_transcribe=VoiceTranscribeResult(status="ok")),
                    audio=spark(), subjects=SubjectsStage(status="ok"), signals=SignalsStage(
                        status="ok", models={"dinov2": ModelSignal(status="ok")}, demucs=DemucsResult(status="ok")),
                    music=MusicIdStage(status="ok", matched=False))
    rows = {r["model"]: r for r in ev.models_used(e, whisper_ok=True, gemini_ok=False, acr_found=False, trends_ok=False)}
    for meta_model in ("Muse Spark 1.3", "Muse Spark 1.2", "Meta Voice Transcribe", "SAM 3.1"):
        assert rows[meta_model]["note"].startswith("Meta model"), meta_model
    for existing in ("Groq Whisper large-v3-turbo", "Gemini 2.5 Pro", "ACRCloud", "DINOv2", "V-JEPA 2", "LanguageBind", "EnCodec", "CLAP",
                     "Demucs", "Gemini 2.5 Flash + Google Search"):
        assert rows[existing]["note"].startswith("existing model / new integration"), existing
    assert rows["Gemini 2.5 Pro"]["status"] == "unavailable" and rows["DINOv2"]["status"] == "ok" and rows["V-JEPA 2"]["status"] == "unavailable"


def test_models_used_without_evidence_still_lists_the_core_models():
    rows = ev.models_used(None, whisper_ok=True, gemini_ok=True, acr_found=True, trends_ok=True)
    assert {"Muse Spark 1.3", "Groq Whisper large-v3-turbo", "Gemini 2.5 Pro", "ACRCloud"} <= {r["model"] for r in rows}


# ---------------------------------------------------------------- the music step never stays grey

def test_the_music_step_is_reported_as_skipped_when_no_evidence_part_runs(monkeypatch):
    import asyncio
    monkeypatch.setattr(ev.config, "get_env", lambda name: "0" if name in ev.FLAGS.values() else None)
    seen = []
    asyncio.run(ev.collect_evidence("/nonexistent.mp4", "/tmp", progress=lambda stage, status: seen.append((stage, status))))
    assert ("music_id", "skipped") in seen


def test_the_music_step_is_skipped_when_the_evidence_stops_early():
    import asyncio
    seen = []
    # the file does not exist, so the evidence stops at the probe: the music step must still be closed out
    asyncio.run(ev.collect_evidence("/nonexistent.mp4", "/tmp", progress=lambda stage, status: seen.append((stage, status))))
    assert ("music_id", "skipped") in seen
