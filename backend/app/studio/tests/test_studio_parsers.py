"""Hackathon-built (Meta Global AI Developer Hackathon).

Offline tests (no network) for the Studio parsers: SAM delta strings, model JSON fence
stripping, language routing, and the track reducer built on top of the SAM parser.
Run from backend/: .venv/bin/python -m pytest app/studio/tests
"""
import pytest

from app.studio import config
from app.studio.meta_client import parse_sam_deltas
from app.studio.report import SubjectsStage, WatchOutput
from app.studio.stages.speech import indic_ratio, normalise_language, route_primary
from app.studio.stages.subjects import build_track, subjects_status
from app.studio.stages.watch import parse_watch_json, parse_watch_output

# Two frames of one object plus frame 5 of a second object (mask data is opaque and not decoded).
SAM_SAMPLE = (
    "<0f>0<|box;x1=79;y1=179;x2=295;y2=719;w=406;h=720;c=0.9609756469726562|>"
    "<|mask;x=0;y=0;c=0.9609756469726562;data=541,217,!!!!%]BVR=+L@Haw^E|>\n"
    "<1f>0<|box;x1=69;y1=176;x2=310;y2=719;w=406;h=720;c=0.9609756469726562|>"
    "<|mask;x=0;y=0;c=0.9609756469726562;data=544,242,!!!!%$BEmYYO&ZPtj7h|>\n"
    "<5f>1<|box;x1=10;y1=20;x2=110;y2=220;w=406;h=720;c=0.5|>\n"
)

WATCH_JSON = ('{"hook": {"what_happens": "girl turns", "attention_rating": 7, "rating_reason": "eye contact"},'
              ' "timeline": [{"start_s": 0, "end_s": 3, "what_happens": "intro"}], "format_type": "Talking-Head",'
              ' "niche_guess": {"niche": "fashion", "confidence": 0.8}, "cta_present": false}')


# ---------------------------------------------------------------- SAM delta parser

def test_sam_parser_extracts_frames_objects_boxes_and_confidence():
    dets = parse_sam_deltas(SAM_SAMPLE)
    assert [(d.frame, d.obj) for d in dets] == [(0, 0), (1, 0), (5, 1)]
    first = dets[0]
    assert (first.x1, first.y1, first.x2, first.y2) == (79, 179, 295, 719)
    assert (first.w, first.h) == (406, 720)
    assert first.conf == pytest.approx(0.96097, abs=1e-4)
    assert dets[2].conf == 0.5  # a detection without a mask section still parses


def test_sam_parser_ignores_text_without_detections():
    assert parse_sam_deltas("") == []
    assert parse_sam_deltas("no detections here") == []


def test_build_track_summarises_detections():
    # 3 s clip at 10 fps = 30 frames; object 0 seen in frames 0-1 only
    track = build_track("person", [d for d in parse_sam_deltas(SAM_SAMPLE) if d.obj == 0], duration_s=3.0)
    assert track.detected_frames == 2
    assert track.screen_time_ratio == pytest.approx(2 / 30, abs=1e-3)
    assert track.visible_in_first_3s is True
    assert track.first_seen_s == 0.0
    assert track.presence_per_second == [1, 0, 0]
    assert track.box_timeline[0].t == 0.0
    assert 0 <= track.safe_area.x1 < track.safe_area.x2 <= 1
    assert track.safe_area.y2 == pytest.approx(719 / 720, abs=1e-3)


def test_build_track_with_no_detections_is_empty_not_an_error():
    track = build_track("face", [], duration_s=2.0)
    assert track.detected_frames == 0 and track.safe_area is None and track.first_seen_s is None
    assert track.presence_per_second == [0, 0]


# ---------------------------------------------------------------- JSON fence stripping

@pytest.mark.parametrize("text", [
    WATCH_JSON,
    f"```json\n{WATCH_JSON}\n```",
    f"```\n{WATCH_JSON}\n```",
    f"Here is the analysis:\n{WATCH_JSON}\nHope that helps!",
    f"  \n```JSON\n{WATCH_JSON}```  ",
])
def test_json_parser_strips_fences_and_prose(text):
    assert parse_watch_json(text)["hook"]["attention_rating"] == 7


@pytest.mark.parametrize("text", ["", "not json at all", "[1, 2, 3]", "{broken: json"])
def test_json_parser_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_watch_json(text)


def test_watch_output_normalises_lenient_model_values():
    out = parse_watch_output(WATCH_JSON)
    assert out.format_type == "talking head"  # "Talking-Head" normalised
    assert out.hook.attention_rating == 7
    assert out.niche_guess.confidence == 0.8


def test_watch_output_clamps_rating_and_parses_clock_times():
    out = WatchOutput.model_validate({
        "hook": {"what_happens": "x", "attention_rating": 11.4, "rating_reason": "y"},
        "timeline": [{"start_s": "0:03", "end_s": "0:05.5", "what_happens": "z"}],
        "format_type": "something odd"})
    assert out.hook.attention_rating == 10
    assert (out.timeline[0].start_s, out.timeline[0].end_s) == (3.0, 5.5)
    assert out.format_type == "other"


def test_watch_output_requires_hook_and_timeline():
    with pytest.raises(ValueError):
        parse_watch_output('{"setting": "a room"}')


# ---------------------------------------------------------------- language routing

def test_routing_devanagari_goes_to_voice_transcribe():
    assert route_primary("नैरो सलवार सूट रेड मुंडियां", "", "hi", True, True)[0] == "voice_transcribe"


def test_routing_gurmukhi_goes_to_voice_transcribe():
    assert route_primary("ਸਤ ਸ੍ਰੀ ਅਕਾਲ ਜੀ", "hello", "en", True, True)[0] == "voice_transcribe"


@pytest.mark.parametrize("lang", ["hi", "Hindi", "pa", "Urdu", "bn", "ta", "te", "mr", "gu"])
def test_routing_whisper_indic_language_goes_to_voice_transcribe(lang):
    assert route_primary("romanised text", "romanised text", lang, True, True)[0] == "voice_transcribe"


def test_routing_english_goes_to_whisper():
    assert route_primary("He knelt to the ground", "He knelt to the ground", "English", True, True)[0] == "whisper"


def test_routing_ignores_a_stray_indic_fragment_in_english_speech():
    vt = "Now to the ground and pulled pulled out a ring and said, मेरी नजर दे,"
    assert indic_ratio(vt) < 0.3
    assert route_primary(vt, "He knelt to the ground and pulled out a ring", "English", True, True)[0] == "whisper"


def test_routing_falls_back_when_the_preferred_engine_failed():
    assert route_primary("", "hello there", "English", vt_ok=False, whisper_ok=True)[0] == "whisper"
    assert route_primary("नमस्ते दोस्तों", "", "hi", vt_ok=True, whisper_ok=False)[0] == "voice_transcribe"
    primary, why = route_primary("", "नमस्ते", "hi", vt_ok=False, whisper_ok=True)
    assert primary == "whisper" and "fell back" in why
    assert route_primary("", "", None, False, False)[0] is None


def test_normalise_language_handles_names_and_codes():
    assert normalise_language("Hindi") == "hi" and normalise_language("pa") == "pa"
    assert normalise_language(None) is None


# ---------------------------------------------------------------- subjects status

def test_subjects_status_ok_when_no_track_failed():
    assert subjects_status({}) == ("ok", None)


def test_subjects_status_partial_when_only_face_failed():
    status, msg = subjects_status({"face": "sam: stream reported an error: boom"})
    assert status == "partial"
    assert "person track ok" in msg and "face track failed" in msg and "boom" in msg


def test_subjects_status_error_when_person_failed_even_if_face_worked():
    status, msg = subjects_status({"person": "HTTP 400: bad"})
    assert status == "error" and "person track failed" in msg


def test_subjects_status_error_when_both_failed_and_message_names_both():
    status, msg = subjects_status({"person": "a", "face": "b"})
    assert status == "error" and "person track failed: a" in msg and "face track failed: b" in msg


def test_report_model_accepts_partial_status():
    assert SubjectsStage(status="partial", error="x").status == "partial"


# ---------------------------------------------------------------- config

def test_default_watch_model_is_spark_1_3(monkeypatch):
    monkeypatch.setattr(config, "get_env", lambda name: None)
    assert config.watch_model() == "muse-spark-1.3"


def test_sam_fps_precedence_and_validation(monkeypatch):
    monkeypatch.setattr(config, "get_env", lambda name: None)
    assert config.sam_fps() == 10  # default
    monkeypatch.setattr(config, "get_env", lambda name: "5" if name == "STUDIO_SAM_FPS" else None)
    assert config.sam_fps() == 5  # env
    assert config.sam_fps(2) == 2  # CLI override beats env
    for bad in (0, -1, 120):
        with pytest.raises(ValueError):
            config.sam_fps(bad)
    monkeypatch.setattr(config, "get_env", lambda name: "fast")
    with pytest.raises(ValueError):
        config.sam_fps()


def test_build_track_respects_a_lower_sam_fps():
    # at 5 fps, frame 5 is t=1.0 s; 2 s clip = 10 frames
    dets = [d for d in parse_sam_deltas(SAM_SAMPLE) if d.frame in (0, 5)]
    track = build_track("person", dets, duration_s=2.0, fps=5)
    assert track.screen_time_ratio == pytest.approx(2 / 10)
    assert track.presence_per_second == [1, 1]
