"""Built during the Meta Global AI Developer Hackathon (new work).

EDL validation: hard limits, timestamp snapping (never invent a timestamp), filler rules, manual tips for what cannot be done."""
import pytest

from app.studio.autoedit import edl as E
from app.studio.tests.autoedit_helpers import make_inputs


def op(i, type_, group, **params):
    return {"id": f"o{i}", "type": type_, "group": group, "source_step_id": "hook#1", "target_parameter": "Hook & Opening", "reason": "r", "params": params}


def validate(ops, inp=None, **kw):
    return E.validate_edl({"ops": ops}, inp or make_inputs(), **kw)


def test_a_valid_plan_passes_and_gets_the_default_style():
    v = validate([op(1, "loudnorm", "audio"), op(2, "caption_burn", "text"), op(3, "zoom_punch", "zoom", at=4.0, scale=1.1)])
    assert v.ok and len(v.edl.ops) == 3 and v.edl.style["preset"] == "clean" and v.dropped == []


def test_a_timestamp_that_is_not_in_the_analysis_is_refused_and_becomes_a_tip():
    inp = make_inputs(duration=60.0, scene_cuts=[], silences=[], words=[], segments=[], energy_peaks=[], analysis_times=[], fillers=[])
    v = validate([op(1, "zoom_punch", "zoom", at=33.3)], inp)
    assert v.edl.ops == [] and "not a timestamp from the analysis" in v.dropped[0]["reason"]
    assert v.edl.manual_tips and "not applied automatically" in v.edl.manual_tips[0].why_manual


def test_times_snap_to_the_nearest_real_timestamp():
    v = validate([op(1, "zoom_punch", "zoom", at=4.07)])
    assert v.edl.ops[0].params["at"] == 4.0


def test_times_outside_the_video_are_refused():
    v = validate([op(1, "cut_segment", "pacing", start=5.0, end=9.0)])
    assert v.edl.ops == [] and "outside the video" in v.dropped[0]["reason"]


def test_more_than_24_ops_is_a_plan_level_error():
    inp = make_inputs(duration=80.0, scene_cuts=[float(t) for t in range(0, 80)], silences=[], words=[], segments=[], energy_peaks=[], analysis_times=[], fillers=[])
    ops = [op(i, "text_overlay", "text", text=f"label {i}", role="label", anchor="source", start=float(3 * i + 2), end=float(3 * i + 3), position="top") for i in range(26)]
    v = validate(ops, inp)
    assert any("operations; the limit is 24" in e for e in v.errors)


def test_removing_more_than_25_percent_is_a_plan_level_error():
    v = validate([op(1, "cut_segment", "pacing", start=1.3, end=2.0 + 0.1), op(2, "trim", "pacing", start=3.0, end=6.0)])
    assert any("is removed; the limit is 25%" in e for e in v.errors)


def test_removal_is_aligned_to_words_and_cannot_cut_through_one():
    inp = make_inputs()
    a, b, removed = E.align_removal(1.1, 2.4, inp.words, inp.duration)  # starts inside 'um', ends inside 'what'
    assert [w["word"] for w in removed] == ["um", "exactly"]  # 'what' (2.35-2.55) is only 0.05 inside: kept whole
    assert a == 0.85 and b == 2.35                            # widened to the gap edges: end of 'is', start of 'what'


def test_widening_to_the_word_edges_is_capped_so_a_short_cut_never_swallows_a_long_gap():
    words = [{"word": "end", "start": 1.0, "end": 1.5}, {"word": "mumble", "start": 8.0, "end": 8.6}, {"word": "tail", "start": 8.7, "end": 9.2}, {"word": "next", "start": 14.0, "end": 14.5}]
    a, b, removed = E.align_removal(8.0, 9.2, words, 20.0)       # both words go; the gaps on both sides are 6.5 s and 4.8 s wide
    assert [w["word"] for w in removed] == ["mumble", "tail"]
    assert (a, b) == (7.4, 9.8)                                  # 0.6 s of the gap on each side, not 6.5 s and 4.8 s


def test_pad_is_between_60_and_120_ms_and_tiny_cuts_are_skipped():
    assert E.pad_for(1.0, 2.0) == 0.12
    assert 0.06 <= E.pad_for(1.0, 1.25) < 0.12
    assert E.pad_for(1.0, 1.1) is None


def test_fillers_must_be_on_the_list_and_exact_words():
    inp = make_inputs()
    ok = validate([op(1, "remove_filler", "pacing", start=1.0, end=1.3, word="um")], inp)
    assert len(ok.edl.ops) == 1
    bad = validate([op(1, "remove_filler", "pacing", start=1.8, end=2.3, word="exactly")], inp)  # a real word, not a filler
    assert bad.edl.ops == [] and "not on the clear-filler list" in bad.dropped[0]["reason"]


def test_cold_open_is_refused_when_the_moment_is_already_in_the_first_3_seconds():
    v = validate([op(1, "cold_open", "hook", start=1.8, end=3.0)])
    assert v.edl.ops == [] and "first 3 s" in v.dropped[0]["reason"]
    v = validate([op(1, "cold_open", "hook", start=4.0, end=5.6)])
    assert len(v.edl.ops) == 1


def test_text_length_limits_and_hook_word_limit():
    long = "x" * 50
    v = validate([op(1, "text_overlay", "hook", text=long, role="hook", anchor="output", end=2.8, position="top")])
    assert v.edl.ops == [] and "limit 42" in v.dropped[0]["reason"]
    v = validate([op(1, "text_overlay", "hook", text="a b c d e f g h i", role="hook", anchor="output", end=2.8)])
    assert v.edl.ops == [] and "more than 8 words" in v.dropped[0]["reason"]
    v = validate([op(1, "text_overlay", "hook", text="Wait for the reveal", role="hook", anchor="output", end=2.8)])
    assert len(v.edl.ops) == 1 and v.edl.ops[0].params["start"] == 0.0


def test_zoom_punches_closer_than_2_5_seconds_are_dropped():
    v = validate([op(1, "zoom_punch", "zoom", at=2.0), op(2, "zoom_punch", "zoom", at=4.0)])
    assert len(v.edl.ops) == 1 and "must be 2.5 s apart" in v.dropped[0]["reason"]


def test_the_system_assigns_each_op_to_its_bundle_whatever_the_planner_said():
    v = validate([op(1, "zoom_punch", "audio", at=4.0), op(2, "caption_burn", "text"), op(3, "loudnorm", "hook"),
                  op(4, "text_overlay", "text", text="Wait for it", role="hook", anchor="output", end=2.8),
                  op(5, "text_overlay", "hook", text="Mid label", role="label", anchor="source", start=3.3, end=4.4, position="top"),
                  op(6, "cut_segment", "zoom", start=1.3, end=1.8)])
    assert {o.id: o.group for o in v.edl.ops} == {"o1": "zoom", "o2": "captions", "o3": "audio", "o4": "hook", "o5": "text", "o6": "pacing"}
    assert [E.risk_of(g) for g in ("hook", "pacing", "captions", "audio")] == ["safe"] * 4
    assert [E.risk_of(g) for g in ("zoom", "text", "rhythm", "polish")] == ["risky"] * 4


def test_a_cut_that_takes_out_spoken_words_is_a_risky_content_cut_but_a_pause_cut_is_safe():
    inp = make_inputs()
    v = validate([op(1, "cut_segment", "pacing", start=3.3, end=4.4), op(2, "cut_segment", "pacing", start=4.4, end=4.9)])   # 1: removes two words; 2: only the 0.5 s gap
    groups = {o.id: o.group for o in v.edl.ops}
    assert groups == {"o1": "rhythm", "o2": "pacing"}
    assert validate([op(3, "remove_filler", "pacing", start=1.0, end=1.3, word="um")]).edl.ops[0].group == "pacing"


def test_a_beat_snapped_cut_on_a_music_driven_reel_is_a_risky_rhythm_op():
    beats = [round(0.5 * i, 3) for i in range(1, 11)]
    inp = make_inputs(duration=6.0, music_driven=True, beats=beats, bpm=120.0, scene_cuts=[], silences=[], energy_peaks=[], analysis_times=[], fillers=[],
                      words=[], segments=[])
    v = validate([op(1, "cut_segment", "pacing", start=1.45, end=2.55)], inp)
    assert v.edl.ops == [] or v.edl.ops[0].group == "rhythm"


def test_music_duck_needs_music_and_speech():
    v = validate([op(1, "music_duck", "audio")])
    assert v.edl.ops == []
    v = validate([op(1, "music_duck", "audio")], make_inputs(has_music=True))
    assert len(v.edl.ops) == 1


def test_result_longer_than_90_seconds_is_an_error():
    inp = make_inputs(duration=100.0, silences=[], scene_cuts=[])
    v = validate([op(1, "loudnorm", "audio")], inp)
    assert any("limit is 90 s" in e for e in v.errors)


def test_garbage_input_returns_an_error_not_an_exception():
    v = E.validate_edl({"ops": "nope"}, make_inputs())
    assert v.errors and v.edl.ops == []


def test_style_overrides_are_clamped():
    v = validate([op(1, "loudnorm", "audio")], style_overrides={"caption_size": 9999, "zoom_scale": 5})
    assert v.edl.style["caption_size"] == 120 and v.edl.style["zoom_scale"] == 1.15


def test_music_driven_reels_cut_on_the_beat_within_120_ms():
    from app.studio.autoedit import inputs as I
    import numpy as np, soundfile as sf
    sr = 22050
    y = np.zeros(12 * sr, dtype="float32")
    for b in np.arange(0.25, 12, 0.5):  # a click every 0.5 s (120 bpm)
        i = int(b * sr)
        y[i:i + 800] += np.sin(2 * np.pi * 1000 * np.arange(800) / sr) * np.exp(-np.arange(800) / sr * 40)
    import tempfile, os
    p = tempfile.mktemp(suffix=".wav")
    sf.write(p, y, sr)
    beats, bpm = I.track_beats(p)
    os.unlink(p)
    assert 100 < bpm < 140 and len(beats) > 10
    assert I.snap_to_beat(beats[3] + 0.08, beats) == beats[3] and I.snap_to_beat(beats[3] + 0.2, beats) == beats[3] + 0.2  # outside 120 ms: untouched
    inp = make_inputs(duration=12.0, words=[], segments=[], fillers=[], has_speech=False, has_music=True, music_driven=True, beats=beats, bpm=bpm,
                      scene_cuts=[round(beats[8] + 0.06, 2), round(beats[14] - 0.05, 2)], silences=[], energy_peaks=[], analysis_times=[])
    v = E.validate_edl({"ops": [{"id": "o1", "type": "cut_segment", "group": "pacing", "params": {"start": round(beats[8] + 0.06, 2), "end": round(beats[14] - 0.05, 2)}}]}, inp)
    assert v.edl.ops[0].params["start"] == beats[8] and v.edl.ops[0].params["end"] == beats[14]


def test_text_may_not_mention_timestamps_and_overlays_may_not_overlap():
    v = validate([op(1, "text_overlay", "hook", text="wait for the white look at 9s", role="hook", anchor="output", end=2.8)])
    assert v.edl.ops == [] and "timestamps" in v.dropped[0]["reason"]
    v = validate([op(1, "text_overlay", "hook", text="Wait for it", role="hook", anchor="output", end=2.8),
                  op(2, "text_overlay", "text", text="Another label", role="label", anchor="source", start=2.0, end=3.0)])
    assert [o.id for o in v.edl.ops] == ["o1"] and "overlaps another text overlay" in v.dropped[0]["reason"]
    v = validate([op(1, "text_overlay", "hook", text="Wait for it", role="hook", anchor="output", end=2.8),
                  op(2, "text_overlay", "text", text="Later label", role="label", anchor="source", start=3.3, end=4.4)])
    assert len(v.edl.ops) == 2


def test_a_face_in_a_sideways_video_is_found_and_mapped_back(tmp_path):
    import cv2, numpy as np
    from app.studio.autoedit import faces
    from app.studio.autoedit.faces import MODEL
    assert MODEL.exists()
    # no real face image is bundled, so check the rotation mapping with a fake detector: a face at the top-right of the turned picture
    calls = []
    def fake(img):
        calls.append(img.shape)
        return {"cx": 0.8, "cy": 0.2, "w": 0.1, "h": 0.3} if len(calls) == 2 else None   # found only in the clockwise-turned copy
    orig = faces._detect_upright
    faces._detect_upright = fake
    try:
        p = tmp_path / "a.jpg"
        cv2.imwrite(str(p), np.zeros((200, 100, 3), dtype=np.uint8))
        f = faces.detect_in_image(p)
    finally:
        faces._detect_upright = orig
    assert f == {"cx": 0.2, "cy": 0.2, "w": 0.3, "h": 0.1}   # x = v, y = 1 - u after turning back; box width and height swap
