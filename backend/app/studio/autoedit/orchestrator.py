"""Built during the Meta Global AI Developer Hackathon (new work).

The Auto-edit run: inputs -> plan -> for each group render, verify, keep or revert -> final render and downloads.
Also toggles, preset changes and revisions (`rerender`). Everything blocking; the API runs it in a worker thread.
State is one JSON document (also written to storage) with a live trace of events for the UI.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from app.studio.autoedit import config, captions, ffx, verify, planner, faces as facesmod, ducking
from app.studio.autoedit import cover as cover_mod, executor, inputs as inputs_mod
from app.studio.autoedit.edl import EDL, GROUPS, DEFAULT_TARGET, Op, validate_edl, ops_by_group
from app.studio.autoedit.llm import CostMeter, CostCapExceeded, EditLLM
from app.studio.autoedit.presets import resolve_style
from app.studio.autoedit.storage import Storage, edit_prefix, upload_key
from app.studio.autoedit.timeline import surviving_words

log = logging.getLogger(__name__)

GROUP_LABEL = {"hook": "Hook", "pacing": "Pacing", "zoom": "Zoom punches", "text": "Captions and text", "audio": "Audio"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_key(aid: str) -> str:
    return edit_prefix(aid) + "state.json"


def new_state(aid: str, user_id: str, preset: str = "clean") -> dict:
    return {"analysis_id": aid, "user_id": user_id, "status": "queued", "phase": "queued", "created_at": now(), "updated_at": now(),
            "preset": preset, "style_overrides": {}, "revisions": [], "trace": [], "groups": {}, "edl": None, "manual_tips": [],
            "dropped_ops": [], "baseline": None, "final": None, "cost": CostMeter().to_json(), "timing": {}, "error": None,
            "notes": [], "label": verify.LABEL}


class Runner:
    """One edit's working context. `emit` is called after every state change (the API publishes the state for polling)."""

    def __init__(self, state: dict, analysis: dict, storage: Storage, llm: EditLLM | None = None, meter: CostMeter | None = None,
                 client=None, publish=None, work_dir: Path | None = None):
        self.state, self.analysis, self.storage = state, analysis, storage
        self.meter = meter or CostMeter.from_json(state.get("cost"))
        self.client = client
        self.llm = llm or EditLLM(self.meter, client)
        self.publish = publish or (lambda s: None)
        self.owned_work = work_dir is None
        self.work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="autoedit_"))
        self.aid = state["analysis_id"]
        self.src = self.work / "original.mp4"
        self.inp: inputs_mod.Inputs | None = None
        self.faces: list[dict] = []
        self.duck: Path | None = None
        self.t_start = time.time()

    # ------------------------------------------------------------------ plumbing
    def close(self):
        if self.owned_work:
            shutil.rmtree(self.work, ignore_errors=True)

    def event(self, kind: str, msg: str, **data):
        self.state["trace"].append({"t": now(), "kind": kind, "msg": msg, **data})
        self.save()

    def save(self):
        self.state["updated_at"] = now()
        self.state["cost"] = self.meter.to_json()
        self.state["timing"]["elapsed_s"] = round(time.time() - self.t_start, 1)
        try:
            self.storage.put_bytes(state_key(self.aid), json.dumps(self.state, ensure_ascii=False).encode(), "application/json")
        except Exception as e:  # a storage hiccup must not kill the edit
            log.warning("[AutoEdit] could not save state: %s", e)
        self.publish(self.state)

    def phase(self, name: str, msg: str):
        self.state["phase"] = name
        self.event("phase", msg)

    def _client(self):
        if self.client is None:
            from app.studio.meta_client import MetaClient
            self.client = MetaClient()
            self.llm.client = self.client
        return self.client

    def _fetch_original(self):
        if not self.src.exists():
            self.storage.get_file(upload_key(self.state["user_id"], self.aid), self.src)

    # ------------------------------------------------------------------ inputs and plan
    def load_inputs(self):
        self._fetch_original()
        ik = edit_prefix(self.aid) + "inputs.json"
        if self.storage.exists(ik):
            self.inp = inputs_mod.Inputs.from_json(json.loads(self.storage.get_bytes(ik)))
        else:
            step_list = planner.extract_steps(self.analysis)
            self.inp = inputs_mod.build_inputs(self.src, self.work / "inputs", self.analysis, self._client(), step_list)
            if self.inp.asr_cost_usd:
                self.meter.add("whisper", "word-level transcript", self.inp.asr_cost_usd)
            self.storage.put_bytes(ik, json.dumps(self.inp.to_json(), ensure_ascii=False).encode(), "application/json")
        fk = edit_prefix(self.aid) + "faces.json"
        if self.storage.exists(fk):
            self.faces = json.loads(self.storage.get_bytes(fk))
        else:
            times = [round(t, 2) for t in _frange(0.5, self.inp.duration - 0.2, 1.5)]
            self.faces = facesmod.sample_faces(self.src, self.work / "faces", times)
            self.storage.put_bytes(fk, json.dumps(self.faces).encode(), "application/json")

    def style(self) -> dict:
        return resolve_style(self.state["preset"], self.state["style_overrides"])

    def edl(self) -> EDL:
        e = EDL.model_validate(self.state["edl"])
        e.style = self.style()
        return e

    # ------------------------------------------------------------------ rendering with a cache
    def render_set(self, edl: EDL, groups: set[str], tag: str) -> tuple[Path, executor.RenderResult | None, str]:
        """Render (or fetch from the cache) the enabled ops of `groups`. Returns (mp4, result-or-None if cached, key)."""
        key = executor.render_key(self.aid, edl, groups)
        out = self.work / "renders" / f"{key}.mp4"
        skey = edit_prefix(self.aid) + f"renders/{key}.mp4"
        rr = None
        if out.exists():
            return out, None, key
        if self.storage.exists(skey):
            self.storage.get_file(skey, out)
            return out, None, key
        needs_duck = any(o.type == "music_duck" and o.enabled and o.group in groups for o in edl.ops)
        if needs_duck:
            self._ensure_duck(edl)
        rr = executor.render(edl, self.inp, self.src, out, self.work / f"w_{tag}", groups, faces=self.faces, duck_wav=self.duck if needs_duck else None)
        self.state["timing"]["render_s"] = round(self.state["timing"].get("render_s", 0) + rr.seconds, 1)
        self.storage.put_file(skey, out, "video/mp4")
        return out, rr, key

    def _ensure_duck(self, edl: EDL):
        if self.duck is not None:
            return
        dk = edit_prefix(self.aid) + "duck.wav"
        p = self.work / "duck" / "duck.wav"
        if self.storage.exists(dk):
            self.storage.get_file(dk, p)
        else:
            op = next(o for o in edl.ops if o.type == "music_duck")
            self.event("info", "separating voice and music with Demucs on Modal")
            p, info = ducking.prepare(self.src, self.work / "duck", self.meter, op.params["duck_db"])
            self.storage.put_file(dk, p, "audio/wav")
            self.event("info", f"music ducked under the voice (voice present {info['voice_share']:.0%} of the time)")
        self.duck = p

    # ------------------------------------------------------------------ the full run
    def run(self):
        st = self.state
        st["status"] = "running"
        try:
            self.phase("inputs", "reading the video: speech with word timings, cuts, silences")
            self.load_inputs()
            self.event("info", f"{self.inp.duration:.0f} s, {len(self.inp.words)} words ({self.inp.language}), {len(self.inp.scene_cuts)} cuts, music={self.inp.has_music}")
            self.phase("planning", "planning the edit from Prysm's fix steps")
            t0 = time.time()
            res = planner.plan(self.analysis, self.inp, self.llm, st["preset"])
            st["timing"]["plan_s"] = round(time.time() - t0, 1)
            edl = res.edl
            st["edl"] = edl.model_dump()
            st["manual_tips"] = [t.model_dump() for t in edl.manual_tips]
            st["dropped_ops"] = res.validation.dropped
            st["notes"] += res.notes
            self.event("plan", f"{len(edl.ops)} changes planned, {len(edl.manual_tips)} manual tips", ops=len(edl.ops), tips=len(edl.manual_tips))
            self.verify_groups(edl)
        except CostCapExceeded as e:
            st["status"], st["error"] = "stopped", str(e)
            self.event("error", f"stopped: {e}")
            if st.get("edl") and not st.get("final"):
                self._finalise_partial()
        except Exception as e:
            log.exception("[AutoEdit] run failed")
            st["status"], st["error"] = "failed", f"{type(e).__name__}: {str(e)[:300]}"
            self.event("error", st["error"])
        else:
            st["status"] = "done"
            self.phase("done", "finished")
        finally:
            st["timing"]["total_s"] = round(time.time() - self.t_start, 1)
            self.save()
            self._log_line()
            self.close()
        return st

    def _log_line(self):
        st, c = self.state, self.meter.by_service()
        groups = st.get("groups", {})
        ops = (st.get("edl") or {}).get("ops", [])
        log.info("[AutoEdit] edit analysis=%s status=%s ops_planned=%d ops_kept=%d ops_reverted=%d cost=$%.4f spark=$%.4f wonder=$%.4f modal=$%.4f "
                 "total_s=%.1f render_s=%.1f", self.aid, st["status"], len(ops), sum(1 for o in ops if o.get("enabled")),
                 sum(1 for o in ops if not o.get("enabled")), self.meter.total, c.get("spark", {}).get("usd", 0), c.get("wonder", {}).get("usd", 0),
                 c.get("modal", {}).get("usd", 0), st["timing"].get("total_s", 0), st["timing"].get("render_s", 0))

    def verify_groups(self, edl: EDL):
        st = self.state
        inp = self.inp
        by_group = ops_by_group(edl)
        base_edl = copy.deepcopy(edl)
        self.phase("baseline", "preparing the baseline (9:16 without edits)")
        base_path, base_rr, base_key = self.render_set(base_edl, set(), "base")
        base_m = verify.measure(base_path)
        st["baseline"] = {"metrics": base_m}
        self.event("info", "baseline ready")
        kept: set[str] = set()
        cur_path, cur_m, cur_words = base_path, base_m, inp.words
        cur_rr = base_rr
        any_kept = False
        for g in GROUPS:
            ops = by_group.get(g) or []
            if not ops:
                continue
            gs = st["groups"][g] = {"label": GROUP_LABEL[g], "status": "running", "ops": [o.id for o in ops], "reasons": [], "target": _target(ops)}
            self.phase(f"render:{g}", f"rendering group: {GROUP_LABEL[g]}")
            try:
                cand_path, rr, key = self.render_set(edl, kept | {g}, g)
                if rr is None:  # cached render: recompute the retimed pieces (cheap, no ffmpeg)
                    rr = _replay(edl, inp, kept | {g}, self.faces, self.work / f'replay_{g}')
                cand_m = verify.measure(cand_path)
                asr_after = self._asr(cand_path)
                self.phase(f"verify:{g}", f"verifying group: {GROUP_LABEL[g]}")
                obj = verify.objective_checks(g, cur_m, cand_m, rr, edl, inp, self.work / f"geo_{g}", asr_words=asr_after, faces=self.faces)
                gs.update({"objective": obj, "metrics_before": cur_m, "metrics_after": cand_m, "notes": rr.notes})
                self.save()
                failures = [f"{c['name']} ({c['detail']})" for c in obj["checks"] if not c["ok"]]
                scores = critic = None
                if obj["ok"]:
                    ops_json = [o.model_dump() for o in ops]
                    with ThreadPoolExecutor(max_workers=2) as pool:  # the re-score (Spark) and the critic (Wonder) are independent
                        f_score = pool.submit(verify.rescore_pair, cur_path, cand_path, cur_m, cand_m, cur_words, asr_after or [], self.analysis, self.llm, self.work, g)
                        f_critic = pool.submit(verify.critique, cur_path, cand_path, g, ops_json, self.llm, self.work, g)
                        errs = []
                        try:
                            scores = f_score.result()
                        except CostCapExceeded:
                            raise
                        except Exception as e:  # a failed re-score means the group cannot be verified: it is reverted below
                            errs.append(f"the AI re-score failed ({type(e).__name__}: {str(e)[:100]})")
                        critic = f_critic.result()
                    if scores:
                        self.event("score", f"AI-estimated re-score for {GROUP_LABEL[g]}: " + _score_line(scores, gs["target"]), group=g)
                    for m in errs:
                        self.event("info", m, group=g)
                    self.event("critic", f"critic ({critic['mode']}): {critic['verdict']}", group=g)
                else:
                    self.event("verify", f"objective checks failed for {GROUP_LABEL[g]}; no AI calls spent on it", group=g)
                gs.update({"scores": scores, "critic": critic})
                dec = verify.decide(g, gs["target"], scores, obj["ok"], failures, critic)
                gs["reasons"] = dec["reasons"]
                if dec["keep"]:
                    gs["status"] = "kept"
                    kept.add(g)
                    cur_path, cur_m, cur_words, cur_rr, any_kept = cand_path, cand_m, asr_after or cur_words, rr, True
                    self.event("kept", f"kept {GROUP_LABEL[g]}: " + "; ".join(dec["reasons"]), group=g)
                else:
                    gs["status"] = "reverted"
                    self.event("reverted", f"reverted {GROUP_LABEL[g]}: " + "; ".join(dec["reasons"]), group=g)
            except CostCapExceeded:
                gs["status"] = "skipped"
                gs["reasons"] = ["the cost cap was reached before this group could be verified"]
                raise
            except Exception as e:
                log.exception("[AutoEdit] group %s failed", g)
                gs["status"] = "reverted"
                gs["reasons"] = [f"could not be rendered or verified ({type(e).__name__}: {str(e)[:120]})"]
                self.event("reverted", f"reverted {GROUP_LABEL[g]}: {gs['reasons'][0]}", group=g)
            finally:
                self.save()
        # final = the last kept state
        for o in edl.ops:
            o.enabled = o.group in kept
        st["edl"] = edl.model_dump()
        self.finalise(edl, cur_path, cur_m, base_path, base_m, any_kept)

    def _finalise_partial(self):
        """Cost cap hit mid-run: groups verified so far stay, everything else is off."""
        st = self.state
        edl = EDL.model_validate(st["edl"])
        kept = {g for g, v in st["groups"].items() if v.get("status") == "kept"}
        for o in edl.ops:
            o.enabled = o.group in kept
        st["edl"] = edl.model_dump()
        try:
            self._fetch_original()
            path, _, _ = self.render_set(edl, kept, "final")
            m = verify.measure(path)
            self.finalise(edl, path, m, None, (st.get("baseline") or {}).get("metrics"), bool(kept), partial=True)
        except Exception as e:
            log.warning("[AutoEdit] partial finalise failed: %s", e)

    def _asr(self, path: Path) -> list[dict] | None:
        """A fresh word-level transcript of the edited audio (about $0.0005): what the re-score sees and the caption-sync check uses."""
        if not self.inp.has_audio:
            return None
        try:
            wav = self.work / f"{path.stem}.asr.wav"
            ffx.extract_wav(path, wav)
            r = self._client().whisper(wav)
            self.meter.add("whisper", "edited-video transcript", r.cost_usd)
            return inputs_mod.clean_words(r.words, 9999)
        except Exception as e:
            log.info("[AutoEdit] ASR of edit failed: %s", e)
            return None

    def finalise(self, edl: EDL, final_path: Path, final_m: dict, base_path: Path | None, base_m: dict | None, any_kept: bool, partial: bool = False):
        st = self.state
        self.phase("finalising", "saving the result")
        fkey = edit_prefix(self.aid) + "final.mp4"
        self.storage.put_file(fkey, final_path, "video/mp4")
        enabled = {g for g in GROUPS if any(o.enabled and o.group == g for o in edl.ops)}
        rr = _replay(edl, self.inp, enabled, self.faces)
        style = edl.style
        if rr.caption_words:
            self.storage.put_bytes(edit_prefix(self.aid) + "captions.srt", captions.to_srt(rr.caption_words, style).encode("utf-8"), "text/plain")
        ass_text = captions.build_ass(rr.caption_words, rr.overlays, style, rr.duration, with_captions=bool(rr.caption_words))
        if rr.caption_words or rr.overlays:
            self.storage.put_bytes(edit_prefix(self.aid) + "captions.ass", ass_text.encode("utf-8"), "text/plain")
        self.storage.put_bytes(edit_prefix(self.aid) + "edl.json", edl.model_dump_json(indent=2).encode(), "application/json")
        try:
            cov = cover_mod.make(final_path, edl, rr.timeline, self.analysis, self.work / "cover", self.work / "cover.jpg")
            self.storage.put_file(edit_prefix(self.aid) + "cover.jpg", cov, "image/jpeg")
        except Exception as e:  # a cover is a nicety
            log.info("[AutoEdit] cover not made: %s", e)
        overall = None
        if any_kept and base_path is not None and not partial:
            try:
                after_words = self._asr(final_path) or self.inp.words
                overall = verify.rescore_pair(base_path, final_path, base_m, final_m, self.inp.words, after_words, self.analysis, self.llm, self.work, "overall")
                self.event("score", "overall AI-estimated re-score: " + _score_line(overall, None))
            except CostCapExceeded:
                raise
            except Exception as e:
                self.event("info", f"overall re-score unavailable ({str(e)[:80]})")
        st["final"] = {"key": fkey, "metrics": final_m, "overall_scores": overall, "partial": partial, "duration_s": final_m.get("duration_s"),
                       "has_captions": bool(rr.caption_words), "has_overlays": bool(rr.overlays), "style": style}
        st["baseline"] = {**(st.get("baseline") or {}), "metrics": base_m}
        self.save()

    # ------------------------------------------------------------------ toggles / presets / revisions
    def rerender(self, summary: str, ai_verified: bool = False):
        """Re-render the current EDL (ops' enabled flags + style) and run the objective checks (no AI scoring)."""
        st = self.state
        try:
            st["status"] = "running"
            self.phase("rendering", f"re-rendering: {summary}")
            self.load_inputs()
            edl = self.edl()
            enabled = {g for g in GROUPS if any(o.enabled and o.group == g for o in edl.ops)}
            path, rr, key = self.render_set(edl, enabled, "re")
            if rr is None:
                rr = _replay(edl, self.inp, enabled, self.faces, self.work / "replay_re")
            m = verify.measure(path)
            self.phase("verify", "objective checks on the new render")
            asr = self._asr(path)
            checks = []
            for g in sorted(enabled):
                o = verify.objective_checks(g, (st.get("baseline") or {}).get("metrics") or m, m, rr, edl, self.inp, self.work / f"geo_{g}", asr_words=asr, faces=self.faces)
                checks += [{"group": g, **c} for c in o["checks"]]
            self.finalise(edl, path, m, None, (st.get("baseline") or {}).get("metrics"), False)
            st["final"]["objective_checks"] = checks
            st["final"]["not_rescored"] = True
            st["final"]["summary"] = summary
            failed = [c for c in checks if not c["ok"]]
            self.event("info", f"re-rendered: {summary}; objective checks: {len(checks) - len(failed)}/{len(checks)} pass" + ("" if ai_verified else " (not re-scored by AI)"))
            st["status"] = "done"
        except CostCapExceeded as e:
            st["status"], st["error"] = "done", None
            self.event("error", f"not applied: {e}")
        except Exception as e:
            log.exception("[AutoEdit] rerender failed")
            st["status"], st["error"] = "failed", f"{type(e).__name__}: {str(e)[:300]}"
            self.event("error", st["error"])
        finally:
            st["timing"]["total_s"] = round(time.time() - self.t_start, 1)
            self.save()
            self.close()
        return st


# ------------------------------------------------------------------ helpers

def _frange(a: float, b: float, step: float):
    t = a
    while t <= b:
        yield t
        t += step


def _target(ops: list[Op]) -> str:
    """The parameter a group is judged on: the one most of its ops name, if the re-score covers it, else the group's default."""
    counts: dict[str, int] = {}
    for o in ops:
        if o.target_parameter in verify.RESCORE_PARAMS:
            counts[o.target_parameter] = counts.get(o.target_parameter, 0) + 1
    if counts:
        return max(counts, key=lambda k: counts[k])
    return DEFAULT_TARGET.get(ops[0].group, "")


def _score_line(scores: dict, target: str | None) -> str:
    b, a = scores.get("before", {}), scores.get("after", {})
    keys = [target] if target and target in a and target in b else [k for k in a if k in b]
    return ", ".join(f"{k} {b[k]}->{a[k]}" for k in keys) + " (AI-estimated)"


def _replay(edl: EDL, inp: inputs_mod.Inputs, groups: set[str], faces: list[dict] | None = None, work: Path | None = None) -> executor.RenderResult:
    """The retimed pieces of a render (timeline, captions, overlays, zooms) without running ffmpeg, for cached renders and downloads.
    With `work` the subtitle file is written too (the safe-area check needs it)."""
    pc = executor.plan_pieces(edl, inp, groups, faces)
    ass_path = None
    if work is not None and (pc.words or pc.overlays):
        work.mkdir(parents=True, exist_ok=True)
        ass_path = work / "captions.ass"
        ass_path.write_text(captions.build_ass(pc.words, pc.overlays, edl.style, pc.timeline.duration, with_captions=bool(pc.words)), encoding="utf-8")
    return executor.RenderResult(path=Path("."), duration=pc.timeline.duration, timeline=pc.timeline, overlays=pc.overlays, caption_words=pc.words,
                                 zooms=pc.zooms, ass_path=ass_path, notes=pc.notes, ops_applied=pc.applied)
