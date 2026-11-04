"""Built during the Meta Global AI Developer Hackathon (new work).

Fonts (catalogue, static-TTF download with an injected fetch, glyph coverage, fallback), style packs, caption chunks / animation / emphasis /
per-word fonts, the creative-director validation, sound effects (every one renders; density limits; mixing under the voice)."""
import json
import re
from pathlib import Path

import pytest

from app.studio.autoedit import captions, creative, edl as E, executor as X, ffx, fonts, packs, sfxlib, verify
from app.studio.autoedit.presets import resolve_style
from app.studio.tests.autoedit_helpers import make_clip, make_inputs, WORDS

BUNDLED = Path(fonts.config.FONTS_DIR)


def fake_fetch(calls):
    """A Google Fonts that serves the bundled Poppins as every family, split by script like the real one."""
    def fetch(url, headers):
        calls.append(url)
        if "fonts.googleapis.com" in url:
            return (b"/* devanagari */\n@font-face {\n font-family: 'X';\n src: url(https://fonts.gstatic.com/s/x/dv.ttf) format('truetype');\n}\n"
                    b"/* latin */\n@font-face {\n font-family: 'X';\n src: url(https://fonts.gstatic.com/s/x/lat.ttf) format('truetype');\n}\n")
        return (BUNDLED / "Poppins-Bold.ttf").read_bytes()
    return fetch


# ---- fonts

def test_the_catalogue_is_searchable_by_name_category_and_script():
    assert len(fonts.families()) > 1500
    assert fonts.search("poppins")[0]["family"] == "Poppins"
    assert all(f["category"] == "handwriting" for f in fonts.search(category="handwriting", limit=20))
    dv = fonts.search(category="devanagari", limit=100)
    assert dv and all(f["devanagari"] for f in dv)
    assert any(f["rounded"] for f in fonts.search(category="rounded", limit=50))
    assert set(fonts.CATEGORIES) == {"display", "sans", "serif", "rounded", "handwriting", "mono", "devanagari"}


def test_a_font_is_downloaded_once_as_a_static_ttf_and_cached(tmp_path):
    calls = []
    cache = fonts.FontCache(fetch=fake_fetch(calls), directory=tmp_path)
    a = cache.get("Poppins", 700, "latin")
    n = len(calls)
    b = cache.get("Poppins", 700, "latin")
    assert a.path == b.path and a.path.exists() and len(calls) == n and a.bold is True and a.name == "Poppins"
    assert "dv.ttf" not in " ".join(calls) and "lat.ttf" in " ".join(calls)          # the latin block was chosen by its subset comment
    cache.get("Poppins", 700, "devanagari")
    assert "dv.ttf" in " ".join(calls)


def test_a_variable_font_is_refused(tmp_path):
    from fontTools.ttLib import TTFont, newTable
    f = TTFont(str(BUNDLED / "Poppins-Bold.ttf"))
    f["fvar"] = newTable("fvar")
    p = tmp_path / "v.ttf"
    f.save(str(p))
    with pytest.raises(ValueError, match="variable"):
        fonts.read_font(p, "X", 400, "latin")


def test_unknown_families_and_missing_scripts_fall_back_to_poppins_with_a_note(tmp_path):
    cache = fonts.FontCache(fetch=fake_fetch([]), directory=tmp_path)
    r = fonts.resolve(cache, "Definitely Not A Font", 700, "Hello")
    assert r.family == "Poppins" and r.latin.name == "Poppins" and any("could not be used" in n for n in r.notes)
    r = fonts.resolve(cache, "Pacifico", 400, "Hello नमस्ते")             # Pacifico has no Devanagari
    assert r.devanagari is not None and r.devanagari.name == "Poppins" and any("devanagari" in n for n in r.notes)
    r = fonts.resolve(cache, "Anton", 400, "Hello")
    assert r.family == "Anton" and not r.notes and r.devanagari is None
    data = latin_only(tmp_path)
    base = fake_fetch([])
    c2 = fonts.FontCache(fetch=lambda u, h: data if "gstatic" in u else base(u, h), directory=tmp_path / "c2")
    r = fonts.resolve(c2, "Hind", 400, "Hello नमस्ते")      # the Devanagari file served is Latin-only: coverage fails -> Poppins for those words
    assert r.devanagari.name == "Poppins" and any("cannot draw" in n for n in r.notes)


def latin_only(tmp_path) -> bytes:
    from fontTools import subset
    from fontTools.ttLib import TTFont
    opts = subset.Options()
    ss = subset.Subsetter(opts)
    font = TTFont(str(BUNDLED / "Poppins-Bold.ttf"))
    ss.populate(unicodes=range(0x20, 0x7F))
    ss.subset(font)
    p = tmp_path / "latin.ttf"
    font.save(str(p))
    return p.read_bytes()


def test_glyph_coverage_is_checked_against_the_real_text(tmp_path):
    data = latin_only(tmp_path)
    base = fake_fetch([])
    cache = fonts.FontCache(fetch=lambda u, h: data if "gstatic" in u else base(u, h), directory=tmp_path / "c")
    f = cache.get("Anton", 400, "latin")
    assert f.covers("Hello world") and not f.covers("नमस्ते")


def test_scripts_are_detected():
    assert fonts.scripts_in("Hello") == {"latin"} and fonts.scripts_in("नमस्ते let's go") == {"latin", "devanagari"}


# ---- packs and style

def test_every_pack_font_exists_in_the_catalogue_and_hinglish_fonts_support_devanagari():
    fams = fonts.families()
    for name, p in packs.PACKS.items():
        assert p["headline"] in fams and p["caption"] in fams, name
    assert "devanagari" in fams[packs.PACKS["hinglish"]["caption"]]["s"] and "devanagari" in fams[packs.PACKS["hinglish"]["headline"]]["s"]


def test_a_pack_sets_fonts_accent_and_animation_and_the_creator_overrides_win():
    st = resolve_style("clean", {"pack": "fitness"})
    assert (st["font_headline"], st["font_caption"], st["animation"], st["highlight_color"]) == ("Bebas Neue", "Oswald", "slide", "39FF14")
    st = resolve_style("clean", {"pack": "fitness", "font_caption": "Lora", "animation": "pop", "intensity": "punchy", "sfx": False})
    assert (st["font_caption"], st["animation"], st["intensity"], st["sfx"], st["max_fonts"]) == ("Lora", "pop", "punchy", False, 2)
    assert resolve_style("clean", {"pack": "nope"})["preset"] == "clean" and "font_caption" not in resolve_style("clean", {"pack": "nope"})


def test_default_pack_follows_language_and_niche():
    assert packs.default_pack("Hindi", "Dance")[0] == "hinglish" and packs.default_pack("English", "Fitness")[0] == "fitness"
    assert packs.default_pack("English", "Fashion")[0] == "editorial" and packs.default_pack("English", "Zzz")[0] == "clean"


# ---- captions

def W(spec):
    return [{"word": w, "start": s, "end": e, "src_start": s, "seg": 0} for w, s, e in spec]


def test_chunks_are_3_to_5_words_with_at_most_two_lines():
    words = W([(f"w{i}", i * 0.3, i * 0.3 + 0.25) for i in range(23)])
    chunks = captions.chunk_words(words, 78)
    assert all(len(c) <= 5 for c in chunks) and sum(len(c) for c in chunks) == 23
    assert all(len(c) >= 3 for c in chunks[:-1])
    for c in chunks:
        assert len(captions.split_lines([w["word"] for w in c], 78)) <= 2


def test_a_pause_or_sentence_end_closes_a_short_chunk():
    words = W([("Hi", 0, 0.2), ("there.", 0.25, 0.5), ("Today", 1.4, 1.7), ("we", 1.75, 1.85), ("start", 1.9, 2.2)])
    assert [[w["word"] for w in c] for c in captions.chunk_words(words, 78)] == [["Hi", "there."], ["Today", "we", "start"]]


def test_the_first_event_of_a_chunk_pops_in_and_later_ones_do_not():
    words = W([(f"w{i}", i * 0.3, i * 0.3 + 0.25) for i in range(4)])
    ev = captions.caption_events(words, resolve_style("clean", {"pack": "bold_hype"}))
    assert "\\fscx70" in ev[0]["text"] and all("\\fscx70" not in e["text"] for e in ev[1:])
    assert "\\fad" in captions.caption_events(words, resolve_style("clean", {"pack": "clean"}))[0]["text"]
    assert not captions.caption_events(words, resolve_style("clean", {"animation": "none"}))[0]["text"].startswith("{\\fscx")


def test_emphasis_words_are_bigger_and_in_the_accent_colour():
    words = W([("this", 0, 0.2), ("is", 0.25, 0.4), ("HUGE", 0.45, 0.8), ("news", 0.85, 1.1)])
    st = resolve_style("clean", {"pack": "bold_hype", "emphasis": [0.45]})
    ev = captions.caption_events(words, st)
    big = int(st["caption_size"] * 1.22)
    assert all(f"\\fs{big}" in e["text"] and "HUGE" in e["text"] for e in ev)


def test_devanagari_words_use_the_devanagari_file_and_latin_words_the_latin_one():
    spec = {"caption": {"name": "Hind", "bold": True, "dv_name": "Hind Devanagari", "dv_bold": False}}
    ev = captions.caption_events(W([("hello", 0, 0.3), ("नमस्ते", 0.35, 0.8), ("friends", 0.85, 1.2)]), resolve_style("clean", {"fonts": spec}))
    t = ev[-1]["text"]
    assert "\\fnHind Devanagari\\b0}नमस्ते{\\fnHind\\b1}" in t and "hello" in t
    ass = captions.build_ass(W([("hello", 0, 0.3)]), [{"text": "Wait नमस्ते", "start": 0, "end": 2, "position": "top", "role": "hook"}],
                             resolve_style("clean", {"fonts": {**spec, "headline": {"name": "Baloo 2", "bold": True, "dv_name": "Baloo 2", "dv_bold": True}}}), 3.0)
    assert "Style: Caption,Hind," in ass and "Style: HookTop,Baloo 2," in ass and "\\fnBaloo 2\\b1}नमस्ते" in ass


# ---- the creative director

INP = make_inputs()


def test_a_valid_reply_becomes_a_pack_three_fonts_emphasis_and_moments():
    raw = {"pack": "bold_hype", "pack_reason": "fast reveal", "fonts": [{"family": "Anton", "role": "headline", "reason": "punchy"},
                                                                        {"family": "Montserrat", "role": "caption", "reason": "readable"},
                                                                        {"family": "Bangers", "role": "headline", "reason": "alt"}],
           "emphasis": [2, 3, 10], "moments": [{"t": 4.0, "kind": "curiosity", "fix": "sfx", "finding": "sag at 4 s", "why": "reveal"},
                                              {"t": 4.5, "kind": "shock", "fix": "sfx"}, {"t": 99, "kind": "shock", "fix": "sfx"}]}
    eng = make_inputs(words=[{"word": w, "start": s_, "end": e} for w, s_, e in WORDS if w.isascii()], segments=[])
    c = creative.validate(raw, {"niche": "Fashion"}, eng)
    assert c.pack == "bold_hype" and (c.headline, c.caption) == ("Anton", "Montserrat") and len(c.font_suggestions) == 3
    assert len(c.emphasis_times) <= int(len(eng.words) * 0.15) and c.emphasis_times
    assert [m["t"] for m in c.moments] == [4.0]            # 4.5 is under 1.5 s after 4.0; 99 is not a real time


def test_bad_packs_unknown_fonts_and_non_devanagari_captions_for_hindi_are_corrected():
    raw = {"pack": "wild", "fonts": [{"family": "Not A Font", "role": "caption"}, {"family": "Pacifico", "role": "caption"}, {"family": "Anton", "role": "headline"}]}
    c = creative.validate(raw, {"niche": "Dance"}, INP)         # INP has Devanagari words
    assert c.pack in packs.PACKS and c.caption is None and c.headline == "Anton" and any("no Devanagari" in n for n in c.notes)


def test_emphasis_is_capped_at_15_percent_and_spaced():
    inp = make_inputs(words=[{"word": f"w{i}", "start": i * 0.4, "end": i * 0.4 + 0.3} for i in range(40)], duration=20.0, segments=[], fillers=[])
    c = creative.validate({"pack": "clean", "emphasis": list(range(40))}, {}, inp)
    assert len(c.emphasis_times) <= 6 and any("capped" in n for n in c.notes)


def test_moments_become_ops_one_fix_each_and_cut_fixes_become_tips_when_nothing_answers_them():
    c = creative.Creative("clean", "", moments=[{"t": 4.0, "kind": "curiosity", "fix": "sfx", "finding": "f1", "why": "w"},
                                               {"t": 5.6, "kind": "shock", "fix": "zoom", "finding": "f2", "why": "w"},
                                               {"t": 7.0, "kind": "transition", "fix": "jump_cut", "finding": "f3", "why": "w"}])
    ops, tips = creative.moment_ops(c, INP, existing_cut_times=[])
    assert [(o.type, o.finding) for o in ops] == [("sfx_hit", "f1"), ("zoom_punch", "f2")] and len(tips) == 1
    ops, tips = creative.moment_ops(c, INP, existing_cut_times=[6.8])
    assert not tips


def test_a_failing_model_call_falls_back_without_stopping_the_edit():
    class Boom:
        def json_call(self, *a, **k):
            raise RuntimeError("down")
    c = creative.decide({"niche": "Fitness"}, INP, Boom())
    assert c.pack in packs.PACKS and c.moments == [] and not c.from_model and c.notes


# ---- sound effects

@pytest.mark.parametrize("effect", sorted(sfxlib.EFFECTS))
def test_every_effect_synthesises_without_clipping_and_matches_its_calibrated_loudness(effect):
    import subprocess
    chain, d = sfxlib.EFFECTS[effect]
    p = subprocess.run([ffx.config.ffmpeg_bin(), "-hide_banner", "-nostdin", "-f", "lavfi", "-i", chain, "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-300:]
    tail = p.stderr[p.stderr.rfind("Summary:"):]
    peak = float(re.search(r"Peak:\s+(-?[0-9.]+) dBFS", tail).group(1))
    assert peak <= -0.5
    if effect != "pop":     # too short for an integrated loudness
        lufs = float(re.search(r"I:\s+(-?[0-9.]+) LUFS", tail).group(1))
        assert abs(lufs - sfxlib.NOMINAL_LUFS[effect]) < 1.0


def test_effects_sit_20_to_24_db_under_the_voice_by_intensity():
    for inten, depth in (("subtle", 24), ("balanced", 22), ("punchy", 20)):
        g = sfxlib.gain_db("ring", -16.0, inten)
        assert abs((sfxlib.NOMINAL_LUFS["ring"] + g) - (-16.0 - depth)) < 0.1


def test_each_moment_kind_maps_to_its_own_effects():
    assert sfxlib.effect_for("curiosity") == "ring" and sfxlib.effect_for("funny", "womp") == "womp" and sfxlib.effect_for("funny", "ring") == "scratch"
    assert sfxlib.effect_for("shock") == "impact" and sfxlib.effect_for("transition") == "whoosh" and sfxlib.effect_for("emphasis") == "pop"


def sfx(i, at, kind="emphasis", effect=None):
    return {"id": f"s{i}", "type": "sfx_hit", "params": {"at": at, "kind": kind, **({"effect": effect} if effect else {})}}


def test_density_limits_gap_per_sound_and_per_ten_seconds():
    inp = make_inputs(duration=30.0, words=[], segments=[], fillers=[], scene_cuts=[float(t) for t in range(1, 30)], silences=[], energy_peaks=[], analysis_times=[])
    v = E.validate_edl({"ops": [sfx(i, t) for i, t in enumerate((2, 3, 5, 8, 11, 14))]}, inp)
    assert [o.params["at"] for o in v.edl.ops] == [2.0, 5.0, 8.0, 14.0]       # 3.0 is under 3 s after 2.0; 11.0 would be a 4th effect in 10 s (balanced allows 3)
    assert sum("closer than 1.5 s" in d["reason"] for d in v.dropped) == 1 and sum("more than 3 effects in 10 s" in d["reason"] for d in v.dropped) == 1
    v2 = E.validate_edl({"ops": [sfx(0, 2), sfx(1, 3.8)]}, inp)
    assert len(v2.edl.ops) == 1 and "more than one sound effect within 3 s" in v2.dropped[0]["reason"]
    v = E.validate_edl({"ops": [{"id": f"z{i}", "type": "zoom_punch", "params": {"at": float(t), "scale": 1.1}} for i, t in enumerate((2, 5, 8, 11))],
                        "style": {"preset": "clean", "intensity": "subtle"}}, inp)
    assert len(v.edl.ops) < 4 and any("more than 2 effects in 10 s" in d["reason"] for d in v.dropped)


def test_sound_effects_render_ducked_under_the_voice_and_the_global_switch_turns_them_off(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", tone_db=-12)
    inp = make_inputs(lufs=-20.0)
    edl = E.validate_edl({"ops": [sfx(1, 2.0, "curiosity", "ring"), sfx(2, 5.2, "shock")]}, inp).edl
    on = X.render(edl, inp, clip, tmp_path / "on.mp4", tmp_path / "w1", {"sound"})
    assert [h["effect"] for h in on.sfx_hits] == ["ring", "impact"] and ffx.probe(on.path)["has_audio"]
    off_style = resolve_style("clean", {"sfx": False})
    edl.style = off_style
    off = X.render(edl, inp, clip, tmp_path / "off.mp4", tmp_path / "w2", {"sound"})
    assert off.sfx_hits == [] and off.ops_applied == []
    a, b = verify.measure(on.path, light=True), verify.measure(off.path, light=True)
    assert a["lufs"] >= b["lufs"] and abs(a["duration_s"] - b["duration_s"]) < 0.05
