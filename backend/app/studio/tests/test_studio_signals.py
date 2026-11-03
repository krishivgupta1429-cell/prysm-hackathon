"""Built during the Meta Global AI Developer Hackathon (new work).

Offline tests (no network, no model downloads, no Modal) for Stage 1C: the before/after deltas, the report
schema, the "skipped on Modal failure" path with a mocked Modal call, the ACRCloud signature against a fixed
example, the ACRCloud response parser and the music ID stage with mocked HTTP.
"""
import sys
import wave

import numpy as np
import pytest
from pydantic import ValidationError

from app.studio import config
from app.studio.pipeline import ALL_STAGES
from app.studio.report import (SCHEMA_VERSION, AudioStage, DemucsResult, GeminiAudioResult, ModelSignal, MusicIdStage,
                               MusicInfo, Report, SignalsStage, SparkAudioResult, Stages, VideoMeta)
from app.studio.signals import acr, deltas, local
from app.studio.signals.deltas import SignalSet, compare, cosine_distance
from app.studio.stages import signals as signals_stage
from app.studio.stages.music_id import agreement, run_music_id


# ---------------------------------------------------------------- deltas

def test_cosine_distance_on_synthetic_vectors():
    v = np.array([1.0, 2.0, 3.0])
    assert cosine_distance(v, v) == pytest.approx(0.0, abs=1e-12)
    assert cosine_distance(v, 5 * v) == pytest.approx(0.0, abs=1e-12)  # scale does not matter
    assert cosine_distance(np.array([1.0, 0.0]), np.array([0.0, 1.0])) == pytest.approx(1.0)
    assert cosine_distance(np.array([1.0, 0.0]), np.array([-1.0, 0.0])) == pytest.approx(2.0)
    with pytest.raises(ValueError):
        cosine_distance(np.zeros(3), v)
    with pytest.raises(ValueError):
        cosine_distance(np.ones(2), np.ones(3))


def _set(**kw):
    base = dict(embeddings={"dinov2": np.array([1.0, 0.0, 0.0]), "clap": np.array([0.0, 1.0, 0.0])},
                voice_to_music_db=6.0, loudness_lufs=-16.0, silence_ratio=0.02, cuts_per_10s=4.0,
                track_id="acr1", track_matched=True)
    base.update(kw)
    return SignalSet(**base)


def test_compare_a_set_with_itself_is_zero():
    out = compare(_set(), _set())
    assert out["cosine_distance"] == {"dinov2": 0.0, "clap": 0.0}
    assert out["audio"] == {"voice_to_music_db": 0.0, "loudness_lufs": 0.0, "silence_ratio": 0.0}
    assert out["cut_rate_change_per_10s"] == 0.0 and out["track_changed"] is False and out["skipped"] == {}


def test_compare_reports_per_model_distance_audio_deltas_cut_rate_and_track_change():
    after = _set(embeddings={"dinov2": np.array([0.0, 1.0, 0.0]), "clap": np.array([0.0, 1.0, 0.0])},
                 voice_to_music_db=2.5, loudness_lufs=-12.0, silence_ratio=0.10, cuts_per_10s=7.5, track_id="acr2")
    out = compare(_set(), after)
    assert out["cosine_distance"] == {"dinov2": 1.0, "clap": 0.0}
    assert out["audio"] == {"voice_to_music_db": -3.5, "loudness_lufs": 4.0, "silence_ratio": 0.08}  # after - before
    assert out["cut_rate_change_per_10s"] == 3.5 and out["track_changed"] is True


def test_compare_track_change_logic_when_a_side_has_no_match_or_no_music_id():
    assert compare(_set(), _set(track_id=None, track_matched=False))["track_changed"] is True  # found -> nothing
    assert compare(_set(track_id=None, track_matched=False), _set(track_id=None, track_matched=False))["track_changed"] is False
    assert compare(_set(), _set(track_id=None, track_matched=None))["track_changed"] is None  # no music ID on one side


def test_compare_skips_models_missing_on_one_side_or_with_a_different_stand_in():
    before = _set(embeddings={"dinov2": np.ones(3), "languagebind_video": np.ones(3)}, stand_ins={})
    after = _set(embeddings={"languagebind_video": np.ones(3), "clap": np.ones(3)},
                 stand_ins={"languagebind_video": "openai/clip-vit-large-patch14"})
    out = compare(before, after)
    assert out["cosine_distance"] == {}
    assert set(out["skipped"]) == {"dinov2", "clap", "languagebind_video"}
    assert "stand-in" in out["skipped"]["languagebind_video"]


def test_compare_leaves_missing_audio_numbers_as_none():
    out = compare(_set(voice_to_music_db=None), _set())
    assert out["audio"]["voice_to_music_db"] is None


# ---------------------------------------------------------------- report schema

def test_report_schema_version_and_new_stages():
    assert SCHEMA_VERSION == "1.3"
    st = Stages()
    assert st.signals.status == "skipped" and st.music_id.status == "skipped" and st.music_id.agreement == "unknown"
    r = Report(video=VideoMeta(path="x.mp4", duration_s=10, width=720, height=1280, fps=30))
    assert r.schema_version == "1.3"
    r.stages.signals.cost_usd, r.stages.music_id.cost_usd, r.stages.watch.cost_usd = 0.01, 0.002, 0.05
    assert r.total_cost() == pytest.approx(0.062)
    assert Report.model_validate_json(r.model_dump_json()).stages.signals.cost_usd == 0.01  # round trip


def test_the_report_holds_no_raw_vectors():
    ok = ModelSignal(status="ok", dims=768, l2_norm=1.5, meta={"n_frames": 14, "stand_in": "x", "flag": True})
    assert ok.model_dump()["dims"] == 768
    with pytest.raises(ValidationError):
        ModelSignal(meta={"vector": [0.1, 0.2, 0.3]})  # lists are not allowed in meta
    with pytest.raises(ValidationError):
        ModelSignal(embedding=[0.1] * 768, extra_check=True) if False else ModelSignal(meta={"v": np.zeros(3).tolist()})


# ---------------------------------------------------------------- signals stage: skipped on Modal failure

def _media(tmp_path, seconds=3):
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frame_001.jpg").write_bytes(b"\xff\xd8fake-jpeg")
    proxy, wav = tmp_path / "proxy.mp4", tmp_path / "audio.wav"
    proxy.write_bytes(b"fake-mp4")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * seconds)
    return frames, proxy, wav


def _stub_features(monkeypatch, ok=True):
    from app.studio.report import AudioFeatures, SceneCuts
    monkeypatch.setattr(signals_stage, "audio_features", lambda wav: AudioFeatures(
        status="ok" if ok else "error", error=None if ok else "boom", integrated_loudness_lufs=-17.0, silence_ratio=0.01))
    monkeypatch.setattr(signals_stage, "scene_cuts", lambda video, dur: SceneCuts(
        status="ok" if ok else "error", error=None if ok else "boom", cuts_per_10s=5.0))


def test_modal_failure_marks_the_gpu_models_skipped_with_the_reason_and_never_raises(tmp_path, monkeypatch):
    _stub_features(monkeypatch)
    frames, proxy, wav = _media(tmp_path)

    def broken_modal(payload):
        raise RuntimeError("modal is down")

    stage = signals_stage.run_signals(tmp_path, frames, proxy, wav, 3.0, modal_call=broken_modal)
    assert stage.status == "partial"  # the CPU features still worked
    assert stage.modal.ok is False and "modal is down" in stage.modal.error
    assert set(stage.models) == set(signals_stage.EXPECTED_MODELS)
    for key, m in stage.models.items():
        assert m.status == "skipped" and "Modal call failed" in m.error and "modal is down" in m.error, key
        assert m.dims is None and m.cost_usd == 0.0
    assert stage.demucs.status == "error" or stage.demucs.status == "skipped"
    assert stage.demucs.error and "modal is down" in stage.demucs.error
    assert stage.audio_features.integrated_loudness_lufs == -17.0 and stage.cuts.cuts_per_10s == 5.0
    assert stage.cost_usd == 0.0


def test_when_nothing_works_the_stage_is_an_error_not_a_crash(tmp_path, monkeypatch):
    _stub_features(monkeypatch, ok=False)
    frames, proxy, wav = _media(tmp_path)
    stage = signals_stage.run_signals(tmp_path, frames, proxy, wav, 3.0, modal_call=lambda p: 1 / 0)
    assert stage.status == "error" and "ZeroDivisionError" in stage.error


def _fake_modal_result(stand_in=False, skip_audio=False):
    def arr(*shape):
        a = np.arange(int(np.prod(shape)), dtype=np.float32).reshape(shape) + 1
        return {"shape": list(shape), "dtype": "float32", "data": a.tobytes()}
    models = {"dinov2": {"status": "ok", "arrays": {"frames": arr(14, 768), "mean": arr(768)}, "meta": {"n_frames": 14}, "seconds": 0.5},
              "vjepa2": {"status": "ok", "arrays": {"embedding": arr(1024)}, "meta": {"clip_frames": 64}, "seconds": 2.0},
              "languagebind_video": {"status": "ok", "arrays": {"embedding": arr(768)},
                                     "meta": {"stand_in": "openai/clip-vit-large-patch14", "video_tower_error": "ImportError"} if stand_in else {},
                                     "seconds": 1.0},
              "languagebind_audio": ({"status": "error", "error": "ImportError: no languagebind", "seconds": 0.1} if skip_audio else
                                     {"status": "ok", "arrays": {"embedding": arr(768)}, "meta": {}, "seconds": 0.3}),
              "encodec": {"status": "ok", "arrays": {"embedding": arr(128)}, "meta": {"codebook_usage_ratio": 0.4}, "seconds": 0.2},
              "clap": {"status": "ok", "arrays": {"embedding": arr(512)}, "meta": {"windows": 2}, "seconds": 0.4},
              "demucs": {"status": "ok", "arrays": {}, "meta": {"vocals_rms_db": -20.0, "rest_rms_db": -26.0, "voice_to_music_db": 6.0},
                         "seconds": 3.0}}
    return {"container": {"cold": True, "gpu": "NVIDIA L4", "calls": 1, "container_age_s": 12.0}, "models": models,
            "stems": {"vocals": b"RIFFvocals", "rest": b"RIFFrest"}, "server_seconds": 8.0}


def test_a_modal_result_is_saved_as_npy_and_only_small_facts_enter_the_report(tmp_path, monkeypatch):
    _stub_features(monkeypatch)
    frames, proxy, wav = _media(tmp_path)
    seen = {}

    def modal(payload):
        seen.update(payload)
        return _fake_modal_result(stand_in=True, skip_audio=True)

    stage = signals_stage.run_signals(tmp_path, frames, proxy, wav, 3.0, modal_call=modal)
    assert seen["want"] == signals_stage.MODAL_WANT and isinstance(seen["proxy"], bytes) and len(seen["frames"]) == 1
    d = stage.models["dinov2"]
    assert d.status == "ok" and d.dims == 768 and d.source == "modal" and d.l2_norm > 0
    assert d.cost_usd == pytest.approx(0.5 * config.MODAL_GPU_USD_PER_S["L4"])
    assert sorted(d.files) == ["signals/dinov2_frames.npy", "signals/dinov2_mean.npy"]
    assert np.load(tmp_path / "signals/dinov2_mean.npy").shape == (768,) and np.load(tmp_path / "signals/dinov2_frames.npy").shape == (14, 768)
    assert stage.models["vjepa2"].dims == 1024 and stage.models["encodec"].meta["codebook_usage_ratio"] == 0.4
    lb = stage.models["languagebind_video"]
    assert lb.stand_in == "openai/clip-vit-large-patch14" and any("stood in" in n for n in stage.notes)
    assert stage.models["languagebind_audio"].status == "error" and "no languagebind" in stage.models["languagebind_audio"].error
    assert stage.demucs.voice_to_music_db == 6.0 and (tmp_path / stage.demucs.stems["rest"]).read_bytes() == b"RIFFrest"
    assert stage.modal.cold is True and stage.modal.gpu == "NVIDIA L4" and stage.modal.server_seconds == 8.0
    assert stage.cost_usd == pytest.approx(8.0 * config.MODAL_GPU_USD_PER_S["L4"]) and stage.status == "partial"
    assert len(stage.model_dump_json()) < 6000 and "RIFF" not in stage.model_dump_json()  # no vectors, no stems


def test_signals_local_runs_the_three_cpu_models_locally_and_asks_modal_for_the_rest(tmp_path, monkeypatch):
    _stub_features(monkeypatch)
    frames, proxy, wav = _media(tmp_path)
    monkeypatch.setattr(local, "run_local", lambda fr, w, want: {m: {"status": "ok", "arrays": {"embedding": np.ones(4, dtype=np.float32), "mean": np.ones(4, dtype=np.float32),
                                                                              "frames": np.ones((2, 4), dtype=np.float32)},
                                                                   "meta": {}, "seconds": 1.0} for m in want})
    seen = {}
    stage = signals_stage.run_signals(tmp_path, frames, proxy, wav, 3.0, use_local=True,
                                      modal_call=lambda p: seen.update(p) or _fake_modal_result())
    assert "dinov2" not in seen["want"] and "clap" not in seen["want"] and "vjepa2" in seen["want"]
    assert stage.models["dinov2"].source == "local" and stage.models["clap"].source == "local" and stage.models["clip"].source == "local"
    assert stage.models["dinov2"].cost_usd == 0.0 and stage.models["vjepa2"].source == "modal"


def test_local_fallback_is_skipped_with_a_reason_when_torch_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)  # makes `import torch` fail
    out = local.run_local([b"x"], None, ["dinov2", "clip", "clap", "vjepa2"])
    assert set(out) == {"dinov2", "clip", "clap"}  # vjepa2 is Modal-only
    assert all(r["status"] == "skipped" and "torch" in r["error"] for r in out.values())


def _res(models, server=5.0, cold=False, gpu="NVIDIA L4"):
    return {"container": {"cold": cold, "gpu": gpu, "calls": 1}, "models": models, "stems": {"rest": b"x"}, "server_seconds": server}


def _ok(meta=None):
    return {"status": "ok", "arrays": {"embedding": {"shape": [2], "dtype": "float32", "data": b"\x00" * 8}}, "meta": meta or {}, "seconds": 1.0}


def test_merge_uses_the_real_languagebind_towers_when_they_ran():
    main = _res({"dinov2": _ok(), "clip": _ok()})
    lb = _res({"languagebind_video": _ok({"tower": "v"}), "languagebind_audio": _ok({"tower": "a"})}, server=3.0, cold=True)
    out = signals_stage.merge_languagebind(main, lb, None, use_lb=True)
    assert "clip" not in out["models"] and out["models"]["languagebind_video"]["meta"] == {"tower": "v"}
    assert out["models"]["languagebind_audio"]["status"] == "ok"
    assert out["server_seconds"] == 8.0 and out["container"]["cold"] is True and out["stems"] == {"rest": b"x"}
    assert out["container"]["cold_main"] is False and out["container"]["cold_lb"] is True  # reported per container


def test_merge_falls_back_to_clip_as_a_labelled_stand_in_when_the_video_tower_fails_or_the_call_dies():
    main = _res({"clip": _ok({"n_frames": 14})})
    lb = _res({"languagebind_video": {"status": "error", "error": "KeyError: None", "seconds": 1.0},
               "languagebind_audio": {"status": "error", "error": "ImportError", "seconds": 1.0}})
    out = signals_stage.merge_languagebind(main, lb, None, use_lb=True)
    v = out["models"]["languagebind_video"]
    assert v["status"] == "ok" and v["meta"]["stand_in"] == "openai/clip-vit-large-patch14" and "KeyError" in v["meta"]["video_tower_error"]
    assert out["models"]["languagebind_audio"]["error"] == "ImportError"
    dead = signals_stage.merge_languagebind(main, None, "RuntimeError: lb container died", use_lb=True)
    assert dead["models"]["languagebind_video"]["meta"]["video_tower_error"].endswith("lb container died")
    assert "lb container died" in dead["models"]["languagebind_audio"]["error"]
    assert signals_stage.merge_languagebind(_res({"dinov2": _ok()}), None, None, use_lb=False)["models"].keys() == {"dinov2"}


def test_call_modal_runs_both_classes_in_parallel_and_survives_a_languagebind_failure(monkeypatch):
    modal = pytest.importorskip("modal")
    calls = []

    class FakeCls:
        def __init__(self, name):
            self.name = name

        def __call__(self):
            return self

        @property
        def run(self):
            outer = self

            class R:
                def remote(self, **kw):
                    calls.append((outer.name, sorted(kw)))
                    if outer.name == config.MODAL_LB_CLASS_NAME:
                        raise RuntimeError("languagebind container crashed")
                    return _res({"dinov2": _ok(), "clip": _ok()})
            return R()

    monkeypatch.setattr(modal.Cls, "from_name", staticmethod(lambda app, name: FakeCls(name)))
    payload = {"frames": [b"f"], "proxy": b"p", "wav": b"w", "want": ["dinov2", "languagebind"]}
    out = signals_stage.call_modal(payload)
    assert {c[0] for c in calls} == {config.MODAL_CLASS_NAME, config.MODAL_LB_CLASS_NAME}
    assert out["models"]["languagebind_video"]["meta"]["stand_in"] == "openai/clip-vit-large-patch14"
    assert "crashed" in out["models"]["languagebind_audio"]["error"] and "dinov2" in out["models"]


def test_gpu_price_matches_the_reported_gpu_and_defaults_to_l4():
    assert signals_stage.gpu_price("NVIDIA L4") == config.MODAL_GPU_USD_PER_S["L4"]
    assert signals_stage.gpu_price("Tesla T4") == config.MODAL_GPU_USD_PER_S["T4"]
    assert signals_stage.gpu_price(None) == config.MODAL_GPU_USD_PER_S["L4"]


def test_modal_app_model_ids_match_the_config_and_the_stages_are_registered():
    modal_app = pytest.importorskip("app.studio.signals.modal_app")
    assert modal_app.MODEL_IDS == dict(config.SIGNAL_MODEL_IDS)
    assert modal_app.APP_NAME == config.MODAL_APP_NAME and modal_app.VOLUME_NAME == config.MODAL_VOLUME_NAME
    assert "signals" in ALL_STAGES and "music_id" in ALL_STAGES


# ---------------------------------------------------------------- ACRCloud

def test_acr_signature_matches_a_fixed_example():
    # computed independently: printf 'POST\n/v1/identify\ntest-access-key\naudio\n1\n1700000000' \
    #   | openssl dgst -sha1 -hmac test-access-secret -binary | base64
    assert acr.sign("test-access-key", "test-access-secret", "1700000000") == "Mg53J3jzxfq7QUv54h6/9j8aZ20="
    assert acr.sign("test-access-key", "test-access-secret", "1700000001") != "Mg53J3jzxfq7QUv54h6/9j8aZ20="
    assert acr.sign("test-access-key", "another-secret", "1700000000") != "Mg53J3jzxfq7QUv54h6/9j8aZ20="


MATCH = {"status": {"msg": "Success", "code": 0, "version": "1.0"},
         "metadata": {"timestamp_utc": "2026-11-03 10:00:00", "music": [
             {"title": "Low Score Song", "score": 40, "acrid": "low", "artists": [{"name": "Nobody"}]},
             {"title": "Love Story", "artists": [{"name": "Taylor Swift"}, {"name": "Guest"}], "album": {"name": "Fearless"},
              "label": "Big Machine", "release_date": "2008-09-12", "acrid": "abc123", "score": 100, "play_offset_ms": 41500,
              "duration_ms": 235000, "external_ids": {"isrc": "USCJY0803456", "upc": ""},
              "external_metadata": {"spotify": {"track": {"id": "1vrd6UOGamcKNGnSHJQlSt"}}, "youtube": {"vid": "8xg3vE8Ie_E"}}}]}}
NO_MATCH = {"status": {"msg": "No result", "code": 1001, "version": "1.0"}}


def test_acr_parser_reads_the_best_match_with_all_fields():
    code, msg, t = acr.parse_response(MATCH)
    assert (code, msg) == (0, "Success")
    assert t.title == "Love Story" and t.artists == ["Taylor Swift", "Guest"] and t.album == "Fearless"
    assert t.label == "Big Machine" and t.release_date == "2008-09-12" and t.acrid == "abc123" and t.score == 100
    assert t.play_offset_ms == 41500 and t.duration_ms == 235000
    assert t.external_ids == {"spotify": "1vrd6UOGamcKNGnSHJQlSt", "youtube": "8xg3vE8Ie_E", "isrc": "USCJY0803456"}


def test_acr_parser_no_match_and_errors_return_the_code_and_message_but_no_track():
    assert acr.parse_response(NO_MATCH) == (1001, "No result", None)
    assert acr.parse_response({"status": {"msg": "invalid signature", "code": 3014}}) == (3014, "invalid signature", None)
    assert acr.parse_response({}) == (None, None, None)
    assert acr.parse_response({"status": {"code": 0, "msg": "Success"}, "metadata": {"music": []}})[2] is None


def test_window_placement_rules():
    assert acr.window_starts(5.9) == [] and acr.window_starts(6.0) == [0.0] and acr.window_starts(12.0) == [0.0]
    assert acr.window_starts(14.1) == [0.0, 1.05] and acr.window_starts(36.6) == [0.0, 12.3]  # start and middle
    assert acr.window_starts(45.0) == [0.0, 16.5] and len(acr.window_starts(46.0)) == 3  # 3 windows over 45 s
    assert acr.window_starts(80.0) == [0.0, 34.0, 68.0]


def _wav(path, seconds, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(b"\x00\x00" * rate * seconds)
    return path


def test_wav_window_cuts_twelve_seconds_and_reports_the_real_bounds(tmp_path):
    wav = _wav(tmp_path / "a.wav", 20)
    data, s, e = acr.wav_window(wav, 12.3, 12)
    assert (s, round(e, 2)) == (12.3, 20.0)  # clipped at the end of the file
    with wave.open(__import__("io").BytesIO(data), "rb") as w:
        assert w.getframerate() == 16000 and w.getnframes() == 20 * 16000 - int(12.3 * 16000)


class FakeIdentify:
    """Stands in for acr.identify: returns scripted responses and records what it was asked."""
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, sample, host, key, secret):
        self.calls.append(len(sample))
        return 200, self.responses.pop(0)


@pytest.fixture
def acr_env(monkeypatch):
    monkeypatch.setattr(config, "acr_credentials", lambda: ("identify.example.test", "key", "secret"))


def _signals_with_stem(tmp_path, with_stem=True):
    s = SignalsStage(status="ok")
    if with_stem:
        _wav(tmp_path / "signals" / "stems" / "rest.wav", 20, rate=22050) if (tmp_path / "signals" / "stems").mkdir(parents=True, exist_ok=True) is None else None
        s.demucs = DemucsResult(status="ok", stems={"rest": "signals/stems/rest.wav"}, voice_to_music_db=3.0)
    return s


def _audio(gemini=None, spark=None):
    a = AudioStage()
    if gemini is not None:
        a.gemini = GeminiAudioResult(status="ok", music=MusicInfo(present=gemini))
    if spark is not None:
        a.spark = SparkAudioResult(status="ok", music_present=spark)
    return a


def test_music_id_matches_on_the_mix_and_records_the_source_window_and_agreement(tmp_path, acr_env):
    wav = _wav(tmp_path / "audio.wav", 40)
    fake = FakeIdentify([MATCH])
    st = run_music_id(wav, 40.0, _signals_with_stem(tmp_path), _audio(gemini=True, spark=True), tmp_path, identify_fn=fake)
    assert st.status == "ok" and st.matched is True and st.source == "mix" and st.track.title == "Love Story"
    assert len(fake.calls) == 1 and fake.calls[0] > 12 * 16000 * 2  # a 12 s, 16 kHz, 16-bit window
    assert [(w.source, w.acr_code, w.matched) for w in st.windows] == [("mix", 0, True)]
    assert st.agreement == "true" and st.heard_music == {"gemini": True, "spark": True}


def test_music_id_retries_on_the_demucs_rest_stem_when_the_mix_does_not_match(tmp_path, acr_env):
    wav = _wav(tmp_path / "audio.wav", 40)
    fake = FakeIdentify([NO_MATCH, NO_MATCH, MATCH])  # two mix windows miss, the first stem window hits
    st = run_music_id(wav, 40.0, _signals_with_stem(tmp_path), _audio(), tmp_path, identify_fn=fake)
    assert st.matched is True and st.source == "rest_stem" and st.track.acrid == "abc123"
    assert [(w.source, w.matched) for w in st.windows] == [("mix", False), ("mix", False), ("rest_stem", True)]
    assert st.agreement == "unknown"  # neither listener said anything about music


def test_a_no_match_is_status_ok_with_matched_false_and_the_acr_code(tmp_path, acr_env):
    wav = _wav(tmp_path / "audio.wav", 40)
    fake = FakeIdentify([NO_MATCH] * 4)
    st = run_music_id(wav, 40.0, _signals_with_stem(tmp_path), _audio(gemini=False, spark=False), tmp_path, identify_fn=fake)
    assert st.status == "ok" and st.matched is False and st.track is None and st.source is None
    assert len(st.windows) == 4 and {(w.acr_code, w.acr_msg) for w in st.windows} == {(1001, "No result")}
    assert st.agreement == "true" and "no music" in st.agreement_notes  # nothing found, and no music was heard


def test_music_id_without_a_stem_only_tries_the_mix(tmp_path, acr_env):
    wav = _wav(tmp_path / "audio.wav", 40)
    fake = FakeIdentify([NO_MATCH, NO_MATCH])
    st = run_music_id(wav, 40.0, _signals_with_stem(tmp_path, with_stem=False), _audio(), tmp_path, identify_fn=fake)
    assert st.matched is False and len(st.windows) == 2 and all(w.source == "mix" for w in st.windows)


def test_music_id_auth_errors_are_errors_and_stop_immediately(tmp_path, acr_env):
    wav = _wav(tmp_path / "audio.wav", 40)
    fake = FakeIdentify([{"status": {"msg": "invalid signature", "code": 3014}}, MATCH])
    st = run_music_id(wav, 40.0, _signals_with_stem(tmp_path), _audio(), tmp_path, identify_fn=fake)
    assert st.status == "error" and "3014" in st.error and len(st.windows) == 1 and st.matched is None
    assert "secret" not in (st.error or "")


def test_music_id_transport_errors_are_errors_without_secrets(tmp_path, acr_env):
    wav = _wav(tmp_path / "audio.wav", 40)

    def boom(sample, host, key, secret):
        raise acr.AcrError("ACRCloud request failed after 2 attempts (ReadTimeout)")

    st = run_music_id(wav, 40.0, _signals_with_stem(tmp_path), _audio(), tmp_path, identify_fn=boom)
    assert st.status == "error" and "ReadTimeout" in st.error and "secret" not in st.error


def test_music_id_is_skipped_for_short_reels_missing_credentials_and_missing_audio(tmp_path, acr_env, monkeypatch):
    wav = _wav(tmp_path / "audio.wav", 5)
    short = run_music_id(wav, 5.0, SignalsStage(), _audio(), tmp_path, identify_fn=FakeIdentify([]))
    assert short.status == "skipped" and "shorter than 6" in short.error
    assert run_music_id(None, 20.0, SignalsStage(), _audio(), tmp_path, identify_fn=FakeIdentify([])).status == "skipped"
    monkeypatch.setattr(config, "acr_credentials", lambda: (None, None, None))
    missing = run_music_id(wav, 20.0, SignalsStage(), _audio(), tmp_path, identify_fn=FakeIdentify([]))
    assert missing.status == "skipped" and "ACRCLOUD" in missing.error


def test_music_id_uses_three_windows_for_reels_over_45_seconds(tmp_path, acr_env):
    wav = _wav(tmp_path / "audio.wav", 60)
    fake = FakeIdentify([NO_MATCH] * 3)
    st = run_music_id(wav, 60.0, _signals_with_stem(tmp_path, with_stem=False), _audio(), tmp_path, identify_fn=fake)
    assert [w.start_s for w in st.windows] == [0.0, 24.0, 48.0]


def test_agreement_with_gemini_and_spark():
    assert agreement(True, {"gemini": True, "spark": None})[0] == "true"
    assert agreement(True, {"gemini": False, "spark": False})[0] == "false"
    assert agreement(False, {"gemini": False, "spark": False})[0] == "true"
    assert agreement(False, {"gemini": True, "spark": False})[0] == "unknown"  # music heard, just not identified
    assert agreement(True, {"gemini": None, "spark": None})[0] == "unknown"
    assert agreement(None, {"gemini": True, "spark": True})[0] == "unknown"


# ---------------------------------------------------------------- pipeline guard

def test_a_hung_stage_times_out_without_blocking_the_run_or_process_exit():
    import asyncio
    import threading
    import time
    from app.studio.pipeline import _guard

    release = threading.Event()

    def hung():
        release.wait(30)  # stands in for a Modal call that never returns
        return "late"

    async def run():
        t0 = time.time()
        out = await _guard("signals", 0.3, hung, lambda msg, secs: ("error", msg))
        return out, time.time() - t0

    out, elapsed = asyncio.run(run())  # returns without waiting for the thread
    assert out[0] == "error" and "timed out" in out[1] and elapsed < 2
    worker = next(t for t in threading.enumerate() if t.name == "studio-stage")
    assert worker.daemon is True  # an abandoned stage cannot keep the process alive
    release.set()


def test_the_guard_passes_results_and_exceptions_through():
    import asyncio
    from app.studio.pipeline import _guard

    async def run():
        ok = await _guard("x", 5, lambda: 42, lambda m, s: "err")
        bad = await _guard("x", 5, lambda: 1 / 0, lambda m, s: m)
        return ok, bad

    ok, bad = asyncio.run(run())
    assert ok == 42 and "ZeroDivisionError" in bad
