"""Built during the Meta Global AI Developer Hackathon (new work).

The Auto-edit run, built for speed and for keeping what helps:
  inputs -> (planner  ||  faces  ||  baseline proxy  ||  Demucs stems) -> the system's own SAFE ops + the planner's ops ->
  proxy renders at 540x960, one per bundle of changes plus one with everything, rendered in parallel ->
  objective checks per bundle  ||  ONE Wonder critic call on the everything-proxy ->
  keep / revert per bundle (SAFE: objective checks + no critic defect; RISKY: critic approval too) ->
  the full-resolution file is rendered ONCE from the kept ops  ||  one informational AI re-score of before / after.
Also toggles, preset changes and revisions (`rerender`). Everything blocking; the API runs it in a worker thread.
State is one JSON document (also written to storage) with a live trace of events for the UI.
"""
import copy
import json
import logging
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.studio.autoedit import config, captions, ffx, verify, planner, defaults, creative, fonts as fonts_mod, packs, faces as facesmod, ducking
from app.studio.autoedit import cover as cover_mod, executor, inputs as inputs_mod
from app.studio.autoedit.edl import EDL, GROUPS, RISK, DEFAULT_TARGET, Op, validate_edl, ops_by_group
from app.studio.autoedit.llm import CostMeter, CostCapExceeded, EditLLM
from app.studio.autoedit.presets import resolve_style
from app.studio.autoedit.storage import Storage, edit_prefix, upload_key
from app.studio.autoedit.timeline import surviving_words

log = logging.getLogger(__name__)

GROUP_LABEL = {"hook": "Hook", "pacing": "Dead air and fillers", "captions": "Captions", "audio": "Loudness and noise", "text": "Labels and CTA text",
               "zoom": "Zoom punches", "rhythm": "Cuts that remove speech or follow the beat", "sound": "Sound accents", "polish": "Music ducking and sound effects"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_key(aid: str) -> str:
    return edit_prefix(aid) + "state.json"


def new_state(aid: str, user_id: str, preset: str = "clean") -> dict:
    return {"analysis_id": aid, "user_id": user_id, "status": "queued", "phase": "queued", "created_at": now(), "updated_at": now(),
            "preset": preset, "style_overrides": {}, "revisions": [], "trace": [], "groups": {}, "edl": None, "manual_tips": [],
            "dropped_ops": [], "decisions": [], "critic": None, "baseline": None, "final": None, "cost": CostMeter().to_json(),
            "timing": {"stages": {}}, "error": None, "notes": [], "label": verify.LABEL}


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
        self.stems: tuple[Path, Path] | None = None
        self._stems_future = None
        self.fonts_spec: dict = {}
        self.font_fetch = None   # injectable for tests
        self.t_start = time.time()
        self._lock = threading.RLock()
        self._duck_lock = threading.Lock()
        self._render_locks: dict[str, threading.Lock] = {}
        state.setdefault("timing", {}).setdefault("stages", {})

    # ------------------------------------------------------------------ plumbing
    def close(self):
        if self.owned_work:
            shutil.rmtree(self.work, ignore_errors=True)

    def event(self, kind: str, msg: str, **data):
        with self._lock:
            self.state["trace"].append({"t": now(), "kind": kind, "msg": msg, **data})
            self.save()

    def save(self):
        with self._lock:
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

    @contextmanager
    def stage(self, name: str):
        """Wall-clock seconds of a named stage (stages can overlap each other; each is timed on its own)."""
        t0 = time.time()
        try:
            yield
        finally:
            st = self.state["timing"]["stages"]
            st[name] = round(st.get(name, 0.0) + time.time() - t0, 1)

    def _client(self):
        if self.client is None:
            from app.studio.meta_client import MetaClient
            self.client = MetaClient()
            self.llm.client = self.client
        return self.client

    def _fetch_original(self):
        if not self.src.exists():
            self.storage.get_file(upload_key(self.state["user_id"], self.aid), self.src)

    # ------------------------------------------------------------------ inputs
    def load_inputs(self):
        self._fetch_original()
        ik = edit_prefix(self.aid) + "inputs.json"
        if self.storage.exists(ik):
            self.inp = inputs_mod.Inputs.from_json(json.loads(self.storage.get_bytes(ik)))
            if self.inp.has_audio and (self.inp.lufs is None or self.inp.stats_version < 2):  # saved by an older version: re-measure the audio stats
                self._measure_audio()
                self.storage.put_bytes(ik, json.dumps(self.inp.to_json(), ensure_ascii=False).encode(), "application/json")
        else:
            step_list = planner.extract_steps(self.analysis)
            self.inp = inputs_mod.build_inputs(self.src, self.work / "inputs", self.analysis, self._client(), step_list)
            if self.inp.asr_cost_usd:
                self.meter.add("whisper", "word-level transcript", self.inp.asr_cost_usd)
            self.storage.put_bytes(ik, json.dumps(self.inp.to_json(), ensure_ascii=False).encode(), "application/json")

    def _measure_audio(self):
        try:
            loud = ffx.loudness(self.src)
            self.inp.lufs, self.inp.true_peak_db = loud.get("lufs"), loud.get("true_peak_db")
            wav = self.work / "inputs" / "audio16k.wav"
            wav.parent.mkdir(parents=True, exist_ok=True)
            ffx.extract_wav(self.src, wav)
            ns = inputs_mod.noise_stats(wav)
            self.inp.noise_floor_db, self.inp.speech_level_db = inputs_mod.noise_in_silences(wav, self.inp.silences), ns.get("speech_level_db")
            self.inp.stats_version = 2
        except Exception as e:
            log.info("[AutoEdit] audio stats failed: %s", e)

    def load_faces(self):
        fk = edit_prefix(self.aid) + "faces.json"
        if self.storage.exists(fk):
            self.faces = json.loads(self.storage.get_bytes(fk))
        else:
            times = [round(t, 2) for t in _frange(0.5, self.inp.duration - 0.2, 1.5)]
            self.faces = facesmod.sample_faces(self.src, self.work / "faces", times)
            self.storage.put_bytes(fk, json.dumps(self.faces).encode(), "application/json")

    def style(self) -> dict:
        ov = dict(self.state["style_overrides"])
        if self.fonts_spec:
            ov["fonts"] = self.fonts_spec
        return resolve_style(self.state["preset"], ov)

    def _resolve_fonts(self, edl: EDL | None = None):
        """Download (once) and check the chosen fonts against the real text; Poppins with a note where a font cannot draw it."""
        st = self.state
        style = resolve_style(st["preset"], st["style_overrides"])
        if "font_caption" not in style:
            self.fonts_spec = {}
            return
        words = " ".join(w["word"] for w in self.inp.words)
        head_text = " ".join(o.params.get("text", "") for o in (edl.ops if edl else []) if o.type == "text_overlay") or "Wait for it"
        cache = fonts_mod.FontCache(fetch=self.font_fetch) if self.font_fetch else fonts_mod.FontCache()
        notes = []
        spec = {}
        for role, fam, text, w in (("caption", style["font_caption"], words or "Hello", style.get("caption_weight", 700)),
                                   ("headline", style["font_headline"], head_text, 700)):
            r = fonts_mod.resolve(cache, fam, w, text)
            spec[role] = r.spec()
            notes += r.notes
        self.fonts_spec = spec
        st["style_notes"] = list(dict.fromkeys(notes))
        for n in st["style_notes"]:
            self.event("info", n)

    def edl(self) -> EDL:
        e = EDL.model_validate(self.state["edl"])
        e.style = self.style()
        return e

    # ------------------------------------------------------------------ rendering with a cache
    def render_set(self, edl: EDL, groups: set[str], tag: str, proxy: bool = False) -> tuple[Path, executor.RenderResult | None, str]:
        """Render (or fetch from the cache) the enabled ops of `groups`. Returns (mp4, result-or-None if cached, key).
        Proxies (540x960) live only in the work dir; full-resolution renders are also stored (they are what the creator downloads)."""
        key = executor.render_key(self.aid, edl, groups, proxy)
        out = self.work / "renders" / f"{key}.mp4"
        skey = edit_prefix(self.aid) + f"renders/{key}.mp4"
        with self._lock:
            lock = self._render_locks.setdefault(key, threading.Lock())
        with lock:
            if out.exists():
                return out, None, key
            if not proxy and self.storage.exists(skey):
                self.storage.get_file(skey, out)
                return out, None, key
            needs_duck = any(o.type == "music_duck" and o.enabled and o.group in groups for o in edl.ops)
            if needs_duck:
                self._ensure_duck(edl)
            rr = executor.render(edl, self.inp, self.src, out, self.work / f"w_{tag}_{key[:6]}", groups, faces=self.faces,
                                 duck_wav=self.duck if needs_duck else None, proxy=proxy)
            with self._lock:
                self.state["timing"]["render_s"] = round(self.state["timing"].get("render_s", 0) + rr.seconds, 1)
            if not proxy:
                self.storage.put_file(skey, out, "video/mp4")
            return out, rr, key

    def _start_stems(self):
        """Demucs on Modal (voice vs music) is the slowest single step: start it while the planner is thinking."""
        try:
            self.event("info", "separating voice and music with Demucs on Modal (in the background)")
            self.stems = ducking.separate_on_modal(self.src, self.work / "duck", self.meter)
        except CostCapExceeded:
            raise
        except Exception as e:
            log.info("[AutoEdit] Demucs unavailable: %s", e)
            self.event("info", f"Demucs unavailable ({str(e)[:80]}): music ducking will be skipped")

    def _ensure_duck(self, edl: EDL):
        with self._duck_lock:
            if self.duck is not None:
                return
            if self._stems_future is not None:
                self._stems_future.result()
            dk = edit_prefix(self.aid) + "duck.wav"
            p = self.work / "duck" / "duck.wav"
            if self.storage.exists(dk):
                self.storage.get_file(dk, p)
            else:
                op = next(o for o in edl.ops if o.type == "music_duck")
                p, info = ducking.prepare(self.src, self.work / "duck", self.meter, op.params["duck_db"], self.stems)
                self.storage.put_file(dk, p, "audio/wav")
                self.event("info", f"music ducked under the voice (voice present {info['voice_share']:.0%} of the time)")
            self.duck = p

    # ------------------------------------------------------------------ the full run
    def run(self):
        st = self.state
        st["status"] = "running"
        try:
            self.phase("inputs", "reading the video: speech with word timings, cuts, silences, loudness")
            with self.stage("inputs"):
                self.load_inputs()
            inp = self.inp
            self.event("info", f"{inp.duration:.0f} s, {len(inp.words)} words ({inp.language}), {len(inp.scene_cuts)} cuts, music={inp.has_music}, "
                               f"loudness {inp.lufs if inp.lufs is not None else '?'} LUFS")
            auto = defaults.build(inp, self.analysis, self.style())
            st["decisions"] = auto.notes
            for n in auto.notes:
                self.event("decision", n["text"], area=n["area"])
            self.phase("planning", "planning the edit (the planner runs while the baseline and faces are prepared)")
            with ThreadPoolExecutor(max_workers=6) as pool:
                f_plan = pool.submit(self._plan)
                f_faces = pool.submit(self.load_faces)
                f_base = pool.submit(self._baseline)
                f_creative = pool.submit(self._creative)
                if inp.has_music and inp.has_speech and not inp.music_driven:
                    self._stems_future = pool.submit(self._start_stems)
                res = f_plan.result()
                f_faces.result()
                cr = f_creative.result()
                extra_ops, extra_tips = self._apply_creative(cr, auto)
                v, mnotes = planner.merge_auto(res.edl, auto.ops + extra_ops, inp, self.state["style_overrides"])
                for t in extra_tips:
                    v.edl.manual_tips.append(planner.Tip(**t))
                self._resolve_fonts(v.edl)
                v.edl.style = self.style()
                st["notes"] += res.notes + [n["text"] for n in mnotes]
                edl = v.edl
                st["edl"] = edl.model_dump()
                st["manual_tips"] = [t.model_dump() for t in edl.manual_tips]
                st["dropped_ops"] = res.validation.dropped + v.dropped
                self.event("plan", f"{len(edl.ops)} changes planned ({len(auto.ops)} by the system, {len(edl.ops) - len(auto.ops)} by the planner), "
                                   f"{len(edl.manual_tips)} manual tips", ops=len(edl.ops), tips=len(edl.manual_tips))
                base = f_base.result()
                self.verify_bundles(edl, base, pool)
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

    def _plan(self):
        with self.stage("plan"):
            t0 = time.time()
            res = planner.plan(self.analysis, self.inp, self.llm, self.state["preset"])
            self.state["timing"]["plan_s"] = round(time.time() - t0, 1)
            return res

    def _creative(self):
        with self.stage("creative"):
            return creative.decide(self.analysis, self.inp, self.llm, self.style().get("intensity", "balanced"))

    def _apply_creative(self, cr, auto):
        """Pack, fonts and emphasis from the director into the style overrides (never over a choice the creator already made), and its moments as ops."""
        st = self.state
        ov = st["style_overrides"]
        packs.apply_suggestion(ov, cr.pack)     # the suggested pack AND its own fonts; the model's font ideas stay one-click suggestions
        if "emphasis" not in ov:
            ov["emphasis"] = cr.emphasis_times
        st["style_choice"] = cr.to_json()
        for n in cr.notes:
            self.event("info", n)
        self.event("decision", f"Style pack: {packs.PACKS[cr.pack]['label']}" + (f" ({cr.pack_reason})" if cr.pack_reason else "") +
                   f"; fonts {ov['font_headline']} + {ov['font_caption']}; {len(cr.emphasis_times)} emphasis words", area="style")
        cuts = [o.params["start"] for o in auto.ops if o.type == "cut_segment"]
        return creative.moment_ops(cr, self.inp, cuts)

    def _ref(self):
        return (self.inp.lufs, self.inp.silence_db) if self.inp is not None else None

    def _baseline(self):
        """The 9:16 conversion without edits, as a proxy: the 'before' for the critic and the objective comparisons."""
        with self.stage("baseline"):
            e = EDL(ops=[], style=self.style())
            path, rr, key = self.render_set(e, set(), "base", proxy=True)
            m = verify.measure(path, ref=self._ref())
            self.state["baseline"] = {"metrics": m}
            self.event("info", "baseline ready")
            return path, m

    def _log_line(self):
        st, c = self.state, self.meter.by_service()
        ops = (st.get("edl") or {}).get("ops", [])
        log.info("[AutoEdit] edit analysis=%s status=%s ops_planned=%d ops_kept=%d ops_reverted=%d cost=$%.4f spark=$%.4f wonder=$%.4f modal=$%.4f "
                 "total_s=%.1f render_s=%.1f", self.aid, st["status"], len(ops), sum(1 for o in ops if o.get("enabled")),
                 sum(1 for o in ops if not o.get("enabled")), self.meter.total, c.get("spark", {}).get("usd", 0), c.get("wonder", {}).get("usd", 0),
                 c.get("modal", {}).get("usd", 0), st["timing"].get("total_s", 0), st["timing"].get("render_s", 0))

    # ------------------------------------------------------------------ verification
    def _proxy(self, edl: EDL, groups: set[str], tag: str) -> dict:
        path, rr, key = self.render_set(edl, groups, tag, proxy=True)
        if rr is None:
            rr = _replay(edl, self.inp, groups, self.faces, self.work / f"replay_{tag}")
        return {"path": path, "rr": rr, "m": verify.measure(path, ref=self._ref())}

    def verify_bundles(self, edl: EDL, base: tuple[Path, dict], pool: ThreadPoolExecutor):
        st, inp = self.state, self.inp
        base_path, base_m = base
        by_group = ops_by_group(edl)
        bundles = [g for g in GROUPS if any(o.enabled for o in by_group.get(g, []))]
        for g in GROUPS:
            ops = by_group.get(g) or []
            if not ops:
                continue
            on = any(o.enabled for o in ops)
            st["groups"][g] = {"label": GROUP_LABEL[g], "risk": RISK[g], "status": "pending" if on else "off", "ops": [o.id for o in ops],
                               "reasons": [] if on else [n["text"] for n in st.get("decisions", []) if n["area"] == "captions"] or ["off by default"],
                               "target": DEFAULT_TARGET[g]}
        pruned = self._prune_unclean_cuts(edl)
        if pruned:
            by_group = ops_by_group(edl)
            bundles = [g for g in GROUPS if any(o.enabled for o in by_group.get(g, []))]
            for g in GROUPS:
                gs = st["groups"].get(g)
                if gs and not any(o.enabled for o in by_group.get(g, [])):
                    why = [st["op_reverts"][o.id] for o in by_group.get(g, []) if o.id in st.get("op_reverts", {})]
                    gs.update({"status": "reverted" if why else gs["status"], "reasons": why or gs["reasons"]})
        self.save()
        every = set(bundles)
        if not bundles:
            self.event("info", "no changes are needed for this video")
            self._conclude(edl, set(), base, None, pool)
            return

        # 1. proxies: one per bundle and one with everything, in parallel
        self.phase("proxies", f"rendering {len(bundles) + (1 if len(bundles) > 1 else 0)} verification previews (540x960) in parallel")
        with self.stage("proxies"):
            futs = {g: pool.submit(self._proxy, edl, {g}, g) for g in bundles}
            f_all = pool.submit(self._proxy, edl, every, "all") if len(bundles) > 1 else None
            prox = {g: f.result() for g, f in futs.items()}
            allp = f_all.result() if f_all else prox[bundles[0]]

        # 2. objective checks per bundle || ONE critic call on the everything-proxy
        self.phase("verify", "checking each change with tools, and asking the critic to watch the result")
        crit_bundles = [{"id": g, "risk": RISK[g], "ops": [o.model_dump() for o in by_group[g] if o.enabled]} for g in bundles]

        def critic_call():
            with self.stage("critic"):
                return verify.critique_all(base_path, allp["path"], crit_bundles, self.llm, self.work, "edit")

        f_critic = pool.submit(critic_call)
        f_obj = {g: pool.submit(self._objective, g, edl, base_m, prox[g]) for g in bundles}
        obj = {g: f.result() for g, f in f_obj.items()}
        critic = f_critic.result()
        st["critic"] = {"mode": critic.get("mode"), "verdict": critic.get("verdict"), "notes": critic.get("notes")}
        self.event("critic", f"critic ({critic.get('mode')}): " + (critic.get("notes") or critic.get("verdict", "")), mode=critic.get("mode"))

        # 3. keep or revert each bundle. A cut bundle the critic only faults at a few moments loses just those cuts.
        partial = self._cull_faulty_ops(edl, critic, allp, by_group)
        kept: set[str] = set()
        for g in bundles:
            o = obj[g]
            failures = [f"{c['name']} ({c['detail']})" for c in o["checks"] if not c["ok"] and not c.get("warn")]
            dec = verify.decide_bundle(g, o["ok"], failures, critic)
            applied = set(prox[g]["rr"].ops_applied)
            nothing = not any(op.id in applied for op in by_group[g] if op.enabled)
            if nothing:   # e.g. every zoom was skipped because the face fills the frame: "kept" would be a lie
                dec = {"keep": False, "risk": RISK[g], "reasons": ["nothing was applied: " + ("; ".join(prox[g]["rr"].notes) or "every change in this group was skipped")]}
            gs = st["groups"][g]
            gs.update({"objective": o, "metrics_after": prox[g]["m"], "notes": prox[g]["rr"].notes, "reasons": dec["reasons"],
                       "critic": (critic.get("bundles") or {}).get(g), "status": "kept" if dec["keep"] else ("skipped" if nothing else "reverted")})
            if dec["keep"]:
                kept.add(g)
                if g in partial:
                    gs["reasons"] = gs["reasons"] + [f"partly kept: {len(partial[g])} change(s) were reverted on their own because the critic found a defect at them"]
            self.event("kept" if dec["keep"] else "reverted", f"{'kept' if dec['keep'] else 'reverted'} {GROUP_LABEL[g]} ({RISK[g]}): " + "; ".join(gs["reasons"]), group=g)
        self._conclude(edl, kept, base, (allp if kept == every and not partial else None), pool, prox=prox)

    def _op_times(self, g: str, ops: list[Op], rr) -> dict[str, list[float]]:
        """Where each op of a bundle shows up in the EDITED video (seconds): the join of a cut, the start of a zoom, a sound or an overlay."""
        from app.studio.autoedit.edl import removal_ranges
        out: dict[str, list[float]] = {}
        if g in ("pacing", "rhythm"):
            for o in ops:
                for lo, hi in removal_ranges([o], self.inp):
                    t = rr.timeline.map_time(hi + 0.02)
                    if t is not None:
                        out.setdefault(o.id, []).append(t)
        elif g == "zoom":
            out = {z["id"]: [z["out_at"]] for z in rr.zooms}
        elif g == "sound":
            out = {h["id"]: [h["out_at"]] for h in rr.sfx_hits}
        elif g in ("text", "hook"):
            out = {x["id"]: [x["start"]] for x in rr.overlays}
        return out

    def _cull_faulty_ops(self, edl: EDL, critic: dict, allp: dict, by_group: dict) -> dict[str, list[str]]:
        """For a bundle of several changes whose major critic defects ALL carry a time in the edited video: find the change nearest to each
        defect (within 2 s), switch only those off, and treat the rest as passing the critic. A failed check reverts only that change.
        The remaining changes were in the video the critic watched, and it faulted nothing else, but they are not watched again as a set."""
        done: dict[str, list[str]] = {}
        if critic.get("verdict") != "ok":
            return done
        for g in ("pacing", "rhythm", "zoom", "sound", "text"):
            entry = (critic.get("bundles") or {}).get(g)
            ops = [o for o in by_group.get(g, []) if o.enabled and (o.type in ("trim", "cut_segment", "remove_filler") or g in ("zoom", "sound", "text"))]
            major = [d for d in (entry or {}).get("defects", []) if d["severity"] == "major"]
            if not entry or not major or len(ops) < 2 or any(d.get("at_s") is None for d in major):
                continue
            times = self._op_times(g, ops, allp["rr"])
            culprits: dict[str, dict] = {}
            for d in major:
                best = min(((abs(t - d["at_s"]), oid) for oid, ts in times.items() for t in ts), default=None)
                if best is None or best[0] > 2.0:
                    culprits = {}
                    break
                culprits[best[1]] = d
            if not culprits or len(culprits) >= len(ops):
                continue
            for o in ops:
                if o.id in culprits:
                    o.enabled = False
                    d = culprits[o.id]
                    self.state.setdefault("op_reverts", {})[o.id] = f"the critic found a defect at this change: {d['flag']}: {d['detail']}"
            entry["defects"] = [d for d in entry["defects"] if d not in culprits.values()]
            if entry["verdict"] == "reject":
                entry["verdict"] = "approve"
            done[g] = list(culprits)
            where = ", ".join("%.0f s" % d["at_s"] for d in culprits.values())
            self.event("info", f"{GROUP_LABEL[g]}: reverted {len(culprits)} of {len(ops)} changes individually (critic defects at {where})")
        return done

    def _prune_unclean_cuts(self, edl: EDL) -> list[str]:
        """The 'every cut sits between words' rule, applied BEFORE rendering: a cut whose edge would land inside a word the recogniser heard
        (so a piece of the word would be left, or a word clipped) is switched off on its own, with the reason, instead of failing the
        whole bundle later. Pure arithmetic on the timeline, no rendering."""
        from types import SimpleNamespace
        from app.studio.autoedit.edl import removal_ranges, REMOVING
        dropped: list[str] = []
        for o in edl.ops:   # a removal too short to cut cleanly (a filler squeezed between two words) would change nothing: say so, do not call it "kept"
            if o.enabled and o.type in REMOVING and o.group in ("pacing", "rhythm") and not removal_ranges([o], self.inp):
                o.enabled = False
                why = "left uncut: too short to cut cleanly (the neighbouring words are right next to it, so a clean cut would leave a piece of the word)"
                self.state.setdefault("op_reverts", {})[o.id] = why
                dropped.append(o.id)
                self.event("info", f"{o.id}: {why}")
        for _ in range(8):
            pc = executor.plan_pieces(edl, self.inp, {"pacing", "rhythm"}, self.faces)
            wg = verify.cuts_on_word_gaps(SimpleNamespace(timeline=pc.timeline), self.inp.words, self.inp.silences, verify.exact_edges(edl))
            if wg["ok"]:
                break
            bad = wg["mid_word_cuts"][0]
            culprit = None
            for o in edl.ops:
                if o.enabled and o.type in REMOVING and o.group in ("pacing", "rhythm"):
                    if any(abs(lo - bad["t"]) < 0.08 or abs(hi - bad["t"]) < 0.08 for lo, hi in removal_ranges([o], self.inp)):
                        culprit = o
                        break
            if culprit is None:
                break
            culprit.enabled = False
            why = f"left uncut: the edge of this cut would land inside the word \"{bad['word']}\" at {bad['t']:.2f} s (the recogniser's word timing is not precise enough to cut there safely)"
            self.state.setdefault("op_reverts", {})[culprit.id] = why
            dropped.append(culprit.id)
            self.event("info", f"{culprit.id}: {why}")
        return dropped

    def _objective(self, g: str, edl: EDL, base_m: dict, px: dict) -> dict:
        asr = self._asr(px["path"]) if (g == "captions" and px["rr"].caption_words) else None
        return verify.objective_checks(g, base_m, px["m"], px["rr"], edl, self.inp, self.work / f"geo_{g}", asr_words=asr, faces=self.faces, video=px["path"])

    def _conclude(self, edl: EDL, kept: set[str], base: tuple[Path, dict], kept_proxy: dict | None, pool: ThreadPoolExecutor, prox: dict | None = None):
        """Render the creator's file once from the kept ops; in parallel, the combined-edit checks and the informational AI re-score."""
        st, inp = self.state, self.inp
        base_path, base_m = base
        for o in edl.ops:
            o.enabled = o.enabled and o.group in kept
        st["edl"] = edl.model_dump()
        self.phase("finalising", "rendering the final video once at full resolution")
        with self.stage("final"):
            f_final = pool.submit(self.render_set, edl, kept, "final")
            if kept and kept_proxy is None:  # a subset of the bundles was kept: its preview does not exist yet
                kept_proxy = self._proxy(edl, kept, "kept")
            f_sync = pool.submit(self._combined_sync, edl, kept, kept_proxy) if ("captions" in kept and len(kept) > 1 and kept_proxy) else None
            f_score = pool.submit(self._overall_score, base_path, base_m, kept_proxy, edl, kept) if kept and kept_proxy else None
            final_path, _rr, _key = f_final.result()
            overall = f_score.result() if f_score else None
            sync = f_sync.result() if f_sync else None
        if sync is not None and not sync["ok"]:  # the captions drifted once combined with the cuts: drop them, say why, render again
            gs = st["groups"]["captions"]
            gs["status"], gs["reasons"] = "reverted", [f"captions went out of sync after the cuts were applied ({sync['detail']}): turned off"]
            self.event("reverted", f"reverted Captions: {gs['reasons'][0]}", group="captions")
            kept.discard("captions")
            for o in edl.ops:
                o.enabled = o.enabled and o.group in kept
            st["edl"] = edl.model_dump()
            final_path, _rr, _key = self.render_set(edl, kept, "final2")
        with self.stage("final_checks"):
            final_m = verify.measure(final_path, light=True, ref=self._ref())
        if (final_m["width"], final_m["height"]) != (config.OUT_W, config.OUT_H):
            raise RuntimeError(f"the final render is {final_m['width']}x{final_m['height']}, expected {config.OUT_W}x{config.OUT_H}")
        self.finalise(edl, final_path, final_m, base_m, overall, bool(kept))

    def _combined_sync(self, edl: EDL, kept: set[str], px: dict) -> dict:
        asr = self._asr(px["path"])
        sync = verify.caption_sync(px["rr"].caption_words, asr or [])
        if not sync or sync.get("median_offset_ms") is None:
            return {"ok": True, "detail": "not enough matched words to measure"}
        return {"ok": abs(sync["median_offset_ms"]) <= 150, "detail": f"{sync['median_offset_ms']} ms over {sync['matched_words']} words"}

    def _overall_score(self, base_path: Path, base_m: dict, px: dict, edl: EDL, kept: set[str]) -> dict | None:
        """One informational AI re-score of the original vs the edit (never a veto; labelled AI-estimated and noisy)."""
        try:
            with self.stage("rescore"):
                after_words = [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in surviving_words(px["rr"].timeline, self.inp.words)]
                overall = verify.rescore_pair(base_path, px["path"], base_m, px["m"], self.inp.words, after_words, self.analysis, self.llm, self.work, "overall")
            self.event("score", "AI-estimated re-score (informational only): " + _score_line(overall, None))
            return overall
        except CostCapExceeded:
            raise
        except Exception as e:
            self.event("info", f"the informational AI re-score was not available ({str(e)[:80]})")
            return None

    def _finalise_partial(self):
        """Cost cap hit mid-run: nothing was verified, so only the baseline conversion is delivered."""
        st = self.state
        edl = EDL.model_validate(st["edl"])
        for o in edl.ops:
            o.enabled = False
        st["edl"] = edl.model_dump()
        try:
            self._fetch_original()
            path, _, _ = self.render_set(edl, set(), "final")
            m = verify.measure(path, light=True, ref=self._ref())
            self.finalise(edl, path, m, (st.get("baseline") or {}).get("metrics"), None, False, partial=True)
        except Exception as e:
            log.warning("[AutoEdit] partial finalise failed: %s", e)

    def _asr(self, path: Path) -> list[dict] | None:
        """A fresh word-level transcript of the edited audio (about $0.0005): the caption-sync check compares against it."""
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

    def finalise(self, edl: EDL, final_path: Path, final_m: dict, base_m: dict | None, overall: dict | None, any_kept: bool, partial: bool = False):
        st = self.state
        fkey = edit_prefix(self.aid) + "final.mp4"
        self.storage.put_file(fkey, final_path, "video/mp4")
        enabled = {g for g in GROUPS if any(o.enabled and o.group == g for o in edl.ops)}
        rr = _replay(edl, self.inp, enabled, self.faces)
        style = rr.style or edl.style
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
        st["final"] = {"key": fkey, "metrics": final_m, "overall_scores": overall, "partial": partial, "duration_s": final_m.get("duration_s"),
                       "has_captions": bool(rr.caption_words), "has_overlays": bool(rr.overlays), "style": style}
        st["baseline"] = {**(st.get("baseline") or {}), "metrics": base_m}
        self.save()

    # ------------------------------------------------------------------ toggles / presets / revisions
    def clean_cut(self):
        """Render the same edit with only the safe pause and loudness ops (see clean_cut_ids) to clean.mp4. No model call; `final` is untouched."""
        st = self.state
        cc = st.setdefault("clean_cut", {})
        try:
            cc.update(running=True, error=None)
            self.phase("rendering", "making the clean cut: pauses and loudness only")
            self.load_inputs()
            self.load_faces()
            self._resolve_fonts(EDL.model_validate(st["edl"]) if st.get("edl") else None)
            edl = self.edl()
            keep = clean_cut_ids(edl)
            for o in edl.ops:
                o.enabled = o.id in keep
            if not keep:
                cc.update(running=False, ready=False, error="There was no pause or loudness fix to keep, so a clean cut would be the same as your original.")
                return st
            groups = {o.group for o in edl.ops if o.enabled}
            path, _rr, _key = self.render_set(edl, groups, "clean")
            self.storage.put_file(edit_prefix(self.aid) + "clean.mp4", path, "video/mp4")
            cc.update(running=False, ready=True, ops=len(keep), error=None)
            self.event("info", f"clean cut made: {len(keep)} safe fixes, no overlays")
        except Exception as e:
            log.exception("[AutoEdit] clean cut failed")
            cc.update(running=False, ready=False, error=f"{type(e).__name__}: {str(e)[:200]}")
        finally:
            cc["running"] = False
            self.save()
            self.close()
        return st

    def rerender(self, summary: str, ai_verified: bool = False):
        """Re-render the current EDL (ops' enabled flags + style) and run the objective checks (no AI scoring)."""
        st = self.state
        try:
            st["status"] = "running"
            self.phase("rendering", f"re-rendering: {summary}")
            self.load_inputs()
            self.load_faces()
            self._resolve_fonts(EDL.model_validate(st["edl"]) if st.get("edl") else None)
            edl = self.edl()
            enabled = {g for g in GROUPS if any(o.enabled and o.group == g for o in edl.ops)}
            path, rr, key = self.render_set(edl, enabled, "re")
            if rr is None:
                rr = _replay(edl, self.inp, enabled, self.faces, self.work / "replay_re")
            m = verify.measure(path, ref=self._ref())
            self.phase("verify", "objective checks on the new render")
            asr = self._asr(path)
            checks = []
            for g in sorted(enabled):
                o = verify.objective_checks(g, (st.get("baseline") or {}).get("metrics") or m, m, rr, edl, self.inp, self.work / f"geo_{g}", asr_words=asr, faces=self.faces, video=path)
                checks += [{"group": g, **c} for c in o["checks"]]
            self.finalise(edl, path, m, (st.get("baseline") or {}).get("metrics"), None, False)
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

def clean_cut_ids(edl: EDL) -> set[str]:
    """The ops a "clean cut" keeps: the safe pause tightening and the loudness fix, nothing that draws on the picture or adds sound."""
    return {o.id for o in edl.ops if o.enabled and ((o.type == "cut_segment" and o.id.startswith("auto_silence")) or o.type == "loudnorm")}


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
        ass_path.write_text(captions.build_ass(pc.words, pc.overlays, pc.style, pc.timeline.duration, with_captions=bool(pc.words)), encoding="utf-8")
    return executor.RenderResult(path=Path("."), duration=pc.timeline.duration, timeline=pc.timeline, overlays=pc.overlays, caption_words=pc.words,
                                 zooms=pc.zooms, ass_path=ass_path, notes=pc.notes, ops_applied=pc.applied, style=pc.style)
