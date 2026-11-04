"""Built during the Meta Global AI Developer Hackathon (new work).

The ops the system adds by itself from measurements: captions for speech (and why not), loudness only when it is off target,
dead-air and filler cuts on word boundaries, silence cuts on music reels. Plus the 540x960 verification proxy."""
import pytest

from app.studio.autoedit import defaults, edl as E, executor as X, ffx, verify
from app.studio.autoedit.edl import EDL
from app.studio.autoedit.presets import resolve_style
from app.studio.tests.autoedit_helpers import make_clip, make_inputs

STYLE = resolve_style("clean")


def ids(auto):
    return {o.id: o for o in auto.ops}


def words(*spans):
    return [{"word": f"w{i}", "start": a, "end": b} for i, (a, b) in enumerate(spans)]


def test_captions_are_added_for_any_video_with_speech_in_any_language():
    for lang in ("English", "Hindi", "Hinglish"):
        auto = defaults.build(make_inputs(language=lang, lufs=-14.0, true_peak_db=-3.0, noise_floor_db=-60.0), {}, STYLE)
        assert ids(auto)["auto_captions"].enabled and any("Captions added" in n["text"] for n in auto.notes)


def test_no_captions_without_speech_and_a_reason_is_given():
    auto = defaults.build(make_inputs(words=[], segments=[], fillers=[], has_speech=False, lufs=-14.0, noise_floor_db=-60.0), {}, STYLE)
    assert "auto_captions" not in ids(auto) and any("nothing to caption" in n["text"] for n in auto.notes)


def test_captions_are_left_off_but_kept_as_a_toggle_when_the_creator_already_has_text():
    analysis = {"visionData": {"frames": [{"onScreenText": "hello"}] * 5 + [{"onScreenText": ""}] * 2}}
    auto = defaults.build(make_inputs(lufs=-14.0, noise_floor_db=-60.0), analysis, STYLE)
    cap = ids(auto)["auto_captions"]
    assert cap.enabled is False and any("left off by default" in n["text"] and "71%" in n["text"] for n in auto.notes)


def test_loudness_is_only_added_when_it_is_off_target():
    quiet = defaults.build(make_inputs(lufs=-29.1, true_peak_db=-17.8, noise_floor_db=-60.0), {}, STYLE)
    op = ids(quiet)["auto_loudness"]
    assert op.params["target_lufs"] == -14 and op.params["denoise"] is False and "-29.1" in op.reason
    fine = defaults.build(make_inputs(lufs=-14.2, true_peak_db=-2.0, noise_floor_db=-60.0), {}, STYLE)
    assert "auto_loudness" not in ids(fine) and any("already -14.2 LUFS" in n["text"] for n in fine.notes)
    hot = defaults.build(make_inputs(lufs=-14.0, true_peak_db=-0.2, noise_floor_db=-60.0), {}, STYLE)
    assert "auto_loudness" in ids(hot)


def test_a_noisy_background_adds_a_denoise_even_at_the_right_loudness():
    auto = defaults.build(make_inputs(lufs=-14.0, true_peak_db=-3.0, noise_floor_db=-40.0), {}, STYLE)
    op = ids(auto)["auto_loudness"]
    assert op.params["denoise"] is True and "noise" in op.reason


def test_long_pauses_that_are_really_silent_and_fillers_become_pacing_cuts():
    w = words((0.5, 1.0), (1.1, 1.5), (3.0, 3.4), (3.5, 4.0), (4.1, 4.5))      # a 1.5 s pause after w1
    inp = make_inputs(duration=10.0, words=w, segments=[], fillers=[{"word": "um", "start": 3.5, "end": 4.0}], silences=[{"start": 1.62, "end": 2.9}], scene_cuts=[],
                      energy_peaks=[], analysis_times=[], lufs=-14.0, noise_floor_db=-60.0)
    auto = defaults.build(inp, {}, STYLE)
    pause = ids(auto)["auto_silence1"]
    assert pause.type == "cut_segment" and (pause.params["start"], pause.params["end"]) == (1.62, 2.9) and pause.params["silence"] is True   # the SIGNAL's silence, not the word gap
    assert ids(auto)["auto_filler1"].type == "remove_filler"
    v = E.validate_edl(EDL(ops=auto.ops), inp)
    assert v.ok and {o.group for o in v.edl.ops} == {"captions", "pacing"}


def test_a_long_word_gap_with_no_silence_in_the_signal_is_not_cut():
    w = words((0.5, 1.0), (1.1, 1.5), (3.0, 3.4), (3.5, 4.0), (4.1, 4.5))
    inp = make_inputs(duration=10.0, words=w, segments=[], fillers=[], silences=[], scene_cuts=[], energy_peaks=[], analysis_times=[], lufs=-14.0, noise_floor_db=-60.0)
    assert not any(o.type == "cut_segment" for o in defaults.build(inp, {}, STYLE).ops)      # breath, room tone or quiet speech: not provably dead air


def test_a_pause_with_music_playing_through_it_is_not_cut_but_real_silence_is():
    w = words((0.5, 1.0), (1.1, 1.5), (3.0, 3.4), (3.5, 4.0))
    music = make_inputs(duration=12.0, words=w, segments=[], fillers=[], has_music=True, silences=[], scene_cuts=[], energy_peaks=[], analysis_times=[], lufs=-14.0, noise_floor_db=-60.0)
    auto = defaults.build(music, {}, STYLE)
    assert not any(o.id.startswith("auto_pause") for o in auto.ops)
    music.silences = [{"start": 4.6, "end": 6.2}]
    auto = defaults.build(music, {}, STYLE)
    assert any(o.id.startswith("auto_silence") for o in auto.ops)


def test_on_a_music_reel_only_real_silence_is_cut_and_it_is_not_beat_snapped_into_the_risky_bundle():
    beats = [round(0.5 * i, 3) for i in range(1, 18)]
    inp = make_inputs(duration=12.0, words=[], segments=[], fillers=[], has_speech=False, has_music=True, music_driven=True, beats=beats, bpm=120.0, scene_cuts=[],
                      energy_peaks=[], analysis_times=[], silences=[{"start": 3.13, "end": 4.78}], lufs=-14.0, noise_floor_db=-60.0)
    auto = defaults.build(inp, {}, STYLE)
    cut = next(o for o in auto.ops if o.id.startswith("auto_silence"))
    v = E.validate_edl(EDL(ops=[cut]), inp)
    assert [o.group for o in v.edl.ops] == ["pacing"]          # SAFE: true silence is not a matter of taste


def test_the_cuts_stay_within_the_removal_limit():
    w = words(*[(i * 3.0, i * 3.0 + 0.5) for i in range(8)])      # a 2.5 s pause after every word
    inp = make_inputs(duration=24.0, words=w, segments=[], fillers=[], silences=[{"start": i * 3.0 + 0.6, "end": i * 3.0 + 2.9} for i in range(7)], scene_cuts=[],
                      energy_peaks=[], analysis_times=[], lufs=-14.0, noise_floor_db=-60.0)
    auto = defaults.build(inp, {}, STYLE)
    assert sum(1 for o in auto.ops if o.type == "cut_segment") >= 2
    v = E.validate_edl(EDL(ops=auto.ops), inp)
    assert v.ok and E.removed_seconds(v.edl.ops, inp) / 24.0 <= 0.25


# ---- the proxy render and the face-aware caption position

def test_a_proxy_is_540x960_and_the_final_is_1080x1920(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", tone_db=-20)
    inp = make_inputs()
    edl = E.validate_edl({"ops": [{"id": "c", "type": "caption_burn", "params": {}}, {"id": "z", "type": "zoom_punch", "params": {"at": 2.0, "scale": 1.1}}]}, inp).edl
    groups = {"captions", "zoom"}
    px = X.render(edl, inp, clip, tmp_path / "p.mp4", tmp_path / "wp", groups, proxy=True)
    fl = X.render(edl, inp, clip, tmp_path / "f.mp4", tmp_path / "wf", groups)
    mp, mf = verify.measure(px.path, light=True), verify.measure(fl.path, light=True)
    assert (mp["width"], mp["height"]) == (540, 960) and (mf["width"], mf["height"]) == (1080, 1920)
    assert abs(mp["duration_s"] - mf["duration_s"]) < 0.1 and px.caption_words and px.zooms
    assert X.render_key("a", edl, groups, proxy=True) != X.render_key("a", edl, groups)


def test_captions_move_off_a_face_that_sits_in_the_lower_caption_band(tmp_path):
    inp = make_inputs()
    edl = E.validate_edl({"ops": [{"id": "c", "type": "caption_burn", "params": {}}]}, inp).edl
    low_face = [{"t": t, "cx": 0.5, "cy": 0.72, "w": 0.3, "h": 0.2} for t in (0.5, 1.5, 2.5, 3.5, 4.5)]
    pc = X.plan_pieces(edl, inp, {"captions"}, low_face)
    assert pc.style["caption_position"] != "lower" and any("captions moved" in n for n in pc.notes)
    top_face = [{**f, "cy": 0.3} for f in low_face]
    assert X.plan_pieces(edl, inp, {"captions"}, top_face).style["caption_position"] == "lower"


def test_a_click_at_a_join_is_found_and_clean_cuts_are_not(tmp_path):
    import numpy as np
    import soundfile as sf
    from app.studio.autoedit.timeline import Segment, Timeline
    sr = 16000
    t = np.arange(sr * 4) / sr
    y = 0.3 * np.sin(2 * np.pi * 220 * t).astype("float32")
    y[2 * sr:] += 0.4                             # a hard level step at 2.0 s = a click
    wav = tmp_path / "k.wav"
    sf.write(str(wav), y, sr)
    clip = tmp_path / "k.mp4"
    ffx.run(["-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=4", "-i", str(wav), "-c:v", "libx264", "-c:a", "pcm_s16le", "-shortest", str(clip.with_suffix('.mkv'))])
    seg = lambda a, b, o: Segment(src_start=a, src_end=b, out_start=o, kind="main")
    tl = Timeline(segments=[seg(0, 2, 0.0), seg(2, 4, 2.0)])

    class RR:
        timeline = tl
    out = verify.cut_clicks(clip.with_suffix(".mkv"), RR, tmp_path)
    assert out["ok"] is False and out["at"] == [2.0]
    tl2 = Timeline(segments=[seg(0, 1, 0.0), seg(1, 4, 1.0)])
    RR.timeline = tl2
    assert verify.cut_clicks(clip.with_suffix(".mkv"), RR, tmp_path)["ok"] is True


def test_a_silence_cut_is_exact_even_when_a_word_timestamp_spans_the_silence(tmp_path):
    """Sung or held words get long timestamps that run through the silence after them; the signal, not the word time, decides."""
    w = [{"word": "chal", "start": 1.0, "end": 9.0}, {"word": "ab", "start": 9.2, "end": 9.6}]
    inp = make_inputs(duration=12.0, words=w, segments=[], fillers=[], has_music=True, music_driven=True, beats=[], silences=[{"start": 4.0, "end": 6.0}],
                      scene_cuts=[], energy_peaks=[], analysis_times=[], lufs=-14.0, noise_floor_db=-60.0)
    auto = defaults.build(inp, {}, STYLE)
    cut = next(o for o in auto.ops if o.id.startswith("auto_silence"))
    v = E.validate_edl(EDL(ops=[cut]), inp)
    (lo, hi), = E.removal_ranges(v.edl.ops, inp)
    assert 4.0 < lo <= 4.15 and 5.85 <= hi < 6.0          # the silence minus 60-120 ms of room tone on each side, not the whole word
    clip = make_clip(tmp_path / "c.mp4", seconds=12.0)
    rr = X.render(v.edl, inp, clip, tmp_path / "o.mp4", tmp_path / "w", {"pacing"})
    assert verify.cuts_on_word_gaps(rr, inp.words)["ok"] is False          # by the words alone this looks like a cut through 'chal'
    assert verify.cuts_on_word_gaps(rr, inp.words, inp.silences)["ok"] is True


def test_caption_chunks_never_overlap_so_libass_does_not_stack_and_raise_them():
    from app.studio.autoedit import captions
    words = [{"word": w, "start": s, "end": e} for w, s, e in [("Then", 9.0, 9.4), ("beans.", 10.8, 11.17), ("And", 11.12, 11.5), ("this", 11.5, 11.8), ("is", 11.8, 12.0)]]
    ev = captions.caption_events(words, STYLE)
    for a in ev:
        for b in ev:
            if a["chunk"] < b["chunk"]:
                assert a["end"] <= b["start"] + 1e-9          # chunk 1 ('And this is') starts at 11.07; 'beans.' is cut there, not 11.17


def test_a_removal_too_short_to_cut_is_switched_off_with_a_reason_not_reported_as_kept(tmp_path):
    from app.studio.autoedit import orchestrator as O, storage as ST
    from app.studio.autoedit.llm import CostMeter
    words = [{"word": "my", "start": 5.46, "end": 5.5}, {"word": "Um.", "start": 5.5, "end": 5.62}, {"word": "morning", "start": 5.57, "end": 5.92}]
    inp = make_inputs(duration=8.0, words=words, segments=[], fillers=[{"word": "Um.", "start": 5.5, "end": 5.62}], silences=[], scene_cuts=[], energy_peaks=[], analysis_times=[])
    edl = E.validate_edl({"ops": [{"id": "f", "type": "remove_filler", "params": {"start": 5.5, "end": 5.62, "word": "Um."}}]}, inp).edl
    r = O.Runner(O.new_state("a", "u"), {}, ST.LocalStorage(tmp_path / "s"), meter=CostMeter(), work_dir=tmp_path / "w")
    r.inp, r.faces = inp, []
    assert r._prune_unclean_cuts(edl) == ["f"] and edl.ops[0].enabled is False and "too short" in r.state["op_reverts"]["f"]
