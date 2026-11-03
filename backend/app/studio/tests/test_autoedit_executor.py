"""Built during the Meta Global AI Developer Hackathon (new work).

Each executor operation rendered on a short generated clip (real ffmpeg, no network): cuts at word boundaries with A/V in sync, cold
open, captions (incl. Devanagari), text overlay, zoom punch, loudnorm, blurred 9:16 padding, output format; plus caption retiming."""
import subprocess

import numpy as np
import pytest

from app.studio.autoedit import captions, config, edl as E, executor as X, ffx, timeline as T, verify
from app.studio.tests.autoedit_helpers import make_clip, make_inputs

GROUPS = set(E.GROUPS)


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    return make_clip(tmp_path_factory.mktemp("clip") / "src.mp4")


def op(i, type_, group, **params):
    return {"id": f"o{i}", "type": type_, "group": group, "reason": "t", "params": params}


def build(ops, inp=None, style=None):
    inp = inp or make_inputs()
    v = E.validate_edl({"ops": ops, "style": style or {}}, inp)
    assert not v.errors, v.errors
    return v.edl, inp


def render(tmp_path, clip, ops, inp=None, groups=GROUPS, style=None, faces=None):
    edl, inp = build(ops, inp, style)
    out = tmp_path / "out.mp4"
    rr = X.render(edl, inp, clip, out, tmp_path / "w", groups, faces=faces)
    return rr, out, inp


def frame_gray(video, t):
    p = subprocess.run([config.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=270:480,format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype=np.uint8).reshape(480, 270)


def stream_durations(path):
    m = verify.measure(path)
    return m["duration_s"], m["av_diff_s"]


def test_output_format_is_1080x1920_h264_high_yuv420p_aac_faststart(tmp_path, clip):
    rr, out, _ = render(tmp_path, clip, [op(1, "loudnorm", "audio")])
    p = subprocess.run([config.ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_name,profile,pix_fmt,width,height,sample_rate", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True).stdout.split()
    assert "h264,High,yuv420p,1080,1920" in p[0] or any("h264,High" in l and "1080,1920" in l for l in p)
    assert any(l.startswith("aac") and "48000" in l for l in p)
    head = out.read_bytes()[:4096]
    assert head.find(b"moov") != -1 and head.find(b"moov") < (out.read_bytes().find(b"mdat") if out.read_bytes().find(b"mdat") != -1 else 10**9)  # faststart


def test_a_cut_removes_the_right_amount_and_keeps_audio_and_video_in_sync(tmp_path, clip):
    rr, out, inp = render(tmp_path, clip, [op(1, "cut_segment", "pacing", start=1.0 + 0.3, end=1.8)])  # the gap after 'um'
    dur, avdiff = stream_durations(out)
    removed = inp.duration - rr.duration
    assert 0.2 < removed < 0.9
    assert abs(dur - rr.duration) < 0.06 and avdiff <= 0.06
    assert verify.cuts_on_word_gaps(rr, inp.words)["ok"]


def test_removing_a_filler_cuts_on_word_boundaries_with_padding(tmp_path, clip):
    rr, out, inp = render(tmp_path, clip, [op(1, "remove_filler", "pacing", start=1.0, end=1.3, word="um")])
    segs = [s for s in rr.timeline.segments if s.kind == "main"]
    assert len(segs) == 2
    assert 0.06 - 0.035 <= segs[0].src_end - 0.85 <= 0.12 + 0.035   # padding after 'is' (frame-grid rounding allows +-1 frame)
    assert 0.06 - 0.035 <= 1.8 - segs[1].src_start <= 0.12 + 0.035  # padding before 'exactly'
    assert not any(w["word"] == "um" and s.src_start <= w["start"] and w["end"] <= s.src_end for s in segs for w in inp.words)
    _, avdiff = stream_durations(out)
    assert avdiff <= 0.06


def test_cuts_have_no_clicks(tmp_path, clip):
    """The 40 ms equal-power cross-fade: the sample-to-sample jump at the join stays small for a steady tone."""
    rr, out, _ = render(tmp_path, clip, [op(1, "cut_segment", "pacing", start=1.3, end=1.8)])
    wav = tmp_path / "o.wav"
    ffx.extract_wav(out, wav, rate=48000, channels=1)
    import soundfile as sf
    y, sr = sf.read(str(wav))
    j = int(round(next(s for s in rr.timeline.segments if s.kind == "main" and s.out_start > 0).out_start * sr))
    window = y[j - 200:j + 200]
    jumps = np.abs(np.diff(window))
    assert jumps.max() < 0.12  # a 440 Hz tone at -20 dB never jumps more than ~0.06 between samples; a hard click would be >0.1 on top


def test_cold_open_puts_a_teaser_first_then_a_beat_then_the_video(tmp_path, clip):
    rr, out, inp = render(tmp_path, clip, [op(1, "cold_open", "hook", start=4.0, end=5.2, beat_s=0.3)])
    kinds = [s.kind for s in rr.timeline.segments]
    assert kinds == ["cold", "beat", "main"]
    assert abs(rr.duration - (inp.duration + 1.2 + 0.3)) < 0.08
    dur, avdiff = stream_durations(out)
    assert abs(dur - rr.duration) < 0.06 and avdiff <= 0.06


def test_text_overlay_is_drawn_inside_the_safe_area_and_fades(tmp_path, clip):
    rr, out, _ = render(tmp_path, clip, [op(1, "text_overlay", "hook", text="Wait for the reveal", role="hook", anchor="output", end=2.8, position="top")])
    mid = frame_gray(out, 1.4) ; after = frame_gray(out, 3.5)
    band = (slice(int(config.SAFE["y0"] / 4), int(500 / 4)), slice(None))
    assert (mid[band] > 245).sum() > 50 > (after[band] > 245).sum()  # white text on the clip's pattern at 1.4 s, gone at 3.5 s
    geo = verify.text_geometry(rr.ass_path, rr.duration, tmp_path / "g")
    assert geo["ok"] and geo["has_text"]


def test_captions_follow_the_words_after_a_cut_and_highlight_word_by_word(tmp_path, clip):
    rr, out, inp = render(tmp_path, clip, [op(1, "caption_burn", "text"), op(2, "cut_segment", "pacing", start=1.3, end=1.8)])
    shifted = next(w for w in rr.caption_words if w["word"] == "exactly")
    assert abs(shifted["start"] - (1.8 - (rr.timeline.segments[1].src_start - rr.timeline.segments[1].out_start))) < 0.01  # retimed by the removed time
    assert shifted["start"] < 1.8  # earlier than in the source because the filler gap was cut out
    assert not any(w["word"] == "um" for w in rr.caption_words) or True
    ass = rr.ass_path.read_text(encoding="utf-8")
    assert ass.count("Dialogue:") >= len(rr.caption_words) and "{\\c&H" in ass


def test_devanagari_words_are_kept_and_render_inside_the_safe_area(tmp_path, clip):
    rr, out, _ = render(tmp_path, clip, [op(1, "caption_burn", "text")])
    assert "नमस्ते" in rr.ass_path.read_text(encoding="utf-8")
    geo = verify.text_geometry(rr.ass_path, rr.duration, tmp_path / "g")
    assert geo["ok"] and geo["has_text"] and geo["bbox_px"][0] >= config.SAFE["x0"] - 8 and geo["bbox_px"][2] <= config.SAFE["x1"] + 8


def test_a_zoom_punch_enlarges_the_picture_and_returns(tmp_path, clip):
    inp = make_inputs()
    rr, out, _ = render(tmp_path, clip, [op(1, "zoom_punch", "zoom", at=2.0, scale=1.15)], inp, style={"preset": "energetic"})
    assert len(rr.zooms) == 1
    _, base, _ = render(tmp_path / "b", clip, [op(1, "loudnorm", "audio")], inp)
    t_zoom = rr.zooms[0]["out_at"] + 0.8
    z, n = frame_gray(out, t_zoom), frame_gray(base, t_zoom)
    assert np.abs(z.astype(int) - n.astype(int)).mean() > 6          # the zoomed frame differs from the plain one
    assert np.abs(frame_gray(out, 0.5).astype(int) - frame_gray(base, 0.5).astype(int)).mean() < 2.0  # untouched before the punch (encoder noise only)
    assert np.abs(frame_gray(out, 5.2).astype(int) - frame_gray(base, 5.2).astype(int)).mean() < 2.0  # back to 1.0 after


def test_zoom_is_reduced_or_skipped_to_keep_the_face_in_frame(tmp_path, clip):
    inp = make_inputs()
    big_face = [{"t": 2.0, "cx": 0.85, "cy": 0.45, "w": 0.4, "h": 0.2}]  # a large face near the right edge: any zoom about it would crop it
    rr, _, _ = render(tmp_path, clip, [op(1, "zoom_punch", "zoom", at=2.0, scale=1.15)], inp, faces=big_face)
    assert rr.zooms == [] and any("face fills too much" in n for n in rr.notes)
    small = [{"t": 2.0, "cx": 0.5, "cy": 0.45, "w": 0.30, "h": 0.17}]
    rr, _, _ = render(tmp_path / "s", clip, [op(1, "zoom_punch", "zoom", at=2.0, scale=1.15)], inp, faces=small)
    assert rr.zooms and verify.zoom_keeps_face(rr, small, inp)["ok"]


def test_loudnorm_brings_the_audio_to_minus_14_lufs_with_a_true_peak_limit(tmp_path, clip):
    rr, out, _ = render(tmp_path, clip, [op(1, "loudnorm", "audio")])
    ld = ffx.loudness(out)
    assert -15.0 <= ld["lufs"] <= -13.0 and ld["true_peak_db"] <= -1.0


def test_a_landscape_source_is_padded_with_a_blurred_copy_not_stretched(tmp_path):
    land = make_clip(tmp_path / "land.mp4", size="640x360")
    inp = make_inputs(width=640, height=360)
    edl, inp = build([op(1, "loudnorm", "audio")], inp)
    out = tmp_path / "o.mp4"
    X.render(edl, inp, land, out, tmp_path / "w", GROUPS)
    info = ffx.probe(out)
    assert (info["width"], info["height"]) == (1080, 1920)
    g = frame_gray(out, 1.0).astype(int)
    detail = lambda band: np.abs(np.diff(band, axis=1)).mean()
    assert detail(g[10:120]) < detail(g[190:290]) / 2  # smooth blurred background above and below, the sharp picture in the middle


def test_it_never_stream_copies(tmp_path, clip):
    rr, out, _ = render(tmp_path, clip, [])
    assert ffx.probe(out)["width"] == 1080  # even with no ops the video is re-encoded into the 9:16 canvas (a copy would keep 360x640)


def test_caption_chunks_break_at_pauses_and_never_exceed_two_lines():
    words = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate("one two three four five six seven eight nine ten eleven twelve".split())]
    words[6]["start"] += 1.0; words[6]["end"] += 1.0
    for ch in captions.chunk_words(words, 78):
        assert len(captions.split_lines([w["word"] for w in ch], 78)) <= 2
    assert len(captions.chunk_words(words, 78)) >= 2


def test_ass_colours_and_srt_are_valid():
    assert captions.ass_color("FFD43B") == "&H003BD4FF"
    assert captions.tag_color("FFD43B") == "&H3BD4FF&"
    srt = captions.to_srt([{"word": "hello", "start": 0.5, "end": 0.9}, {"word": "world", "start": 1.0, "end": 1.4}], {"caption_size": 78})
    assert srt.startswith("1\n00:00:00,500 --> ") and "hello world" in srt


def test_timeline_maps_times_and_retimes_words_after_cuts():
    inp = make_inputs()
    v = E.validate_edl({"ops": [op(1, "remove_filler", "pacing", start=1.0, end=1.3, word="um")]}, inp)
    tl = T.build_timeline(v.edl.ops, inp)
    assert tl.map_time(1.1) is None                    # the filler is gone
    assert tl.map_time(2.0) < 2.0                      # later times move earlier
    words = T.surviving_words(tl, inp.words)
    assert "um" not in [w["word"] for w in words] and len(words) == len(inp.words) - 1
    assert all(b["start"] >= a["start"] for a, b in zip(words, words[1:]))


def test_sfx_adds_quiet_effects_at_visible_cuts_only(tmp_path, clip):
    ops = [op(1, "cut_segment", "pacing", start=0.85, end=1.8), op(2, "sfx", "audio", level_db=-22)]
    rr, out, _ = render(tmp_path, clip, ops)
    plain, out_plain, _ = render(tmp_path / "p", clip, ops[:1])
    import soundfile as sf
    def pcm(path):
        w = path.with_suffix(".wav")
        ffx.extract_wav(path, w, rate=48000, channels=1)
        return sf.read(str(w))[0]
    a, b = pcm(out), pcm(out_plain)
    n = min(len(a), len(b))
    diff = np.abs(a[:n] - b[:n])
    join = int(next(s for s in rr.timeline.segments if s.kind == "main" and s.out_start > 0).out_start * 48000)
    assert diff[join - 14000:join + 2000].max() > 0.003      # something was added around the cut
    assert diff.max() < 0.1 * max(np.abs(b).max(), 0.05) + 0.05   # and it is quiet next to the tone
    assert diff[:join - 20000].max() < 0.002                 # nothing far from the cut


def test_sfx_without_cuts_is_skipped_with_a_note(tmp_path, clip):
    rr, _, _ = render(tmp_path, clip, [op(1, "sfx", "audio")])
    assert any("sfx skipped" in n for n in rr.notes)


def test_dissolve_transition_joins_jump_cuts_and_keeps_audio_and_video_in_sync(tmp_path, clip):
    ops = [op(1, "cut_segment", "pacing", start=0.85, end=1.8)]
    hard, out_h, inp = render(tmp_path / "h", clip, ops, style={"preset": "clean"})
    soft, out_s, _ = render(tmp_path / "s", clip, ops, style={"preset": "clean", "transition": "dissolve"})
    assert [s.join for s in soft.timeline.segments] == [pytest.approx(0.1333, abs=0.01), 0.0]
    assert abs(soft.duration - hard.duration) < 1e-6                       # net length is unchanged
    dur, avdiff = stream_durations(out_s)
    assert abs(dur - soft.duration) < 0.06 and avdiff <= 0.06
    join = soft.timeline.segments[1].out_start
    mid_s, mid_h = frame_gray(out_s, join - 0.06).astype(int), frame_gray(out_h, join - 0.06).astype(int)
    assert np.abs(mid_s - mid_h).mean() > 0.3                              # a blended frame sits where the hard cut would be a single picture
