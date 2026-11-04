# Auto-edit

Built during the Meta Global AI Developer Hackathon (new work). Everything is behind `AUTO_EDIT_ENABLED` (default **off**) and is offered
only for analyses that came from a **file upload** (never for pasted Instagram links).

From a finished analysis of an uploaded video, Auto-edit produces a post-ready vertical edit: a planner (Muse Spark) turns Prysm's fix
steps into an edit list (EDL), FFmpeg renders it from the original, each group of changes is verified and reverted automatically when it
does not help, and the creator can adjust it (toggles, plain-English requests, presets) and download it.

## What Step 0 found (the design below is adjusted to this)

| Question | Finding | Consequence |
|---|---|---|
| Result format | `parameters[]` with `howToDoIt` = `"Step 1 — … Step 2 — …"` (normalised by `studio/steps.py`), timestamps live in the prose (`0.0s-3.0s`, `at 6.5s`) | Steps are split with `steps.split_steps`; step id = `<param-slug>#<n>`; timestamps are parsed out of the prose and become the "analysis timestamps" the planner may use |
| Uploads | Saved in a temp dir and **deleted when the analysis ends** (`finally: rmtree`) | When the flag is on and the source is an upload, the original is copied into the storage interface (`uploads/<user>/<analysis>/original.mp4`) before the cleanup. The original is never modified |
| Transcript | The baseline calls Groq Whisper **without** word timestamps. `MetaClient.whisper` already asks for `word`+`segment` and Groq returns both (checked on the samples: 112 / 9 / 41 words) | Auto-edit runs its own word-level Whisper call on the stored original (≈ $0.0005); the analysis is not touched |
| Job queue / progress | Redis list + `prysm:progress:<job>` snapshot, polled by the website | Auto-edit uses its own state document (Redis for live polling, storage for durability) with an event trace |
| Meta shim / costs | `MetaClient._chat` charges per call into a ledger; Spark $1.25/$4.25, Wonder $4/$20 per M tokens | A `CostMeter` wraps every paid call and enforces `AUTO_EDIT_MAX_COST_USD` |
| Wonder with video | **Yes**: `muse-wonder-1.0` accepts `video_url` data URLs and hears the audio (5 s probe: 235 tokens in, $0.017) | The critic watches the before and after videos (480 p proxies); frame strips are the fallback |
| Modal | App `prysm-studio-signals`, class `Signals`, `run.remote(frames, proxy, wav, want=["demucs"])` returns vocals and rest stems (22 kHz mono) | `music_duck` uses those stems only to build a duck envelope; the full-quality original audio is what is processed |
| Muse Image / SAM | SAM 3.1 is used in analysis (people/faces, $0.20 / 1000 frames); there is **no Muse Image call** in the repo yet | Phase 4 item (cover frame) would be new tooling |
| FFmpeg on this machine | System ffmpeg 8.0.1 has `loudnorm`, `zoompan`, `acrossfade`, `xfade`, `silencedetect`, `blackdetect`, `freezedetect`, `ebur128` but **no libass and no drawtext (no freetype)**. The pip package `imageio-ffmpeg` ships a static build with libass + drawtext | `AUTO_EDIT_FFMPEG` env, else the first of `ffmpeg` / the imageio build that has the `ass` filter. Production images use the distro ffmpeg (has libass). Devanagari shaping was verified with libass + Poppins |
| Fonts | None bundled | `Poppins` (OFL, Latin + Devanagari) is bundled in `autoedit/fonts/` with its licence |

## Pipeline (v2: keep what helps, and fast)

```
stored original ─▶ inputs (probe, word transcript, scene cuts, silence map, loudness + noise floor, energy peaks, music flag)
   ├─▶ the system's own SAFE ops, from measurements (defaults.py): captions, dead-air / filler cuts, loudness / denoise
   ├─▶ planner (Spark, reasoning_effort=low): hook text, cold open, zoom punches, labels, stumble cuts, effects     ┐ in parallel
   ├─▶ face boxes for the whole video                                                                              │ (the planner is the
   ├─▶ baseline 540x960 preview                                                                                    │  long pole, ~20 s)
   └─▶ Demucs stems on Modal (only when music + speech)                                                            ┘
merge (the system's ops first) ─▶ validated EDL ─▶ every op belongs to a BUNDLE (hook, pacing, captions, audio | text, zoom, rhythm, polish)
   ─▶ cuts whose edge would land inside a recognised word are dropped up front (no render needed)
   ─▶ 540x960 preview per bundle + one with everything, rendered in parallel
   ─▶ objective checks per bundle   ||   ONE critic call (Wonder, reasoning_effort=low) that watches original vs everything-preview
   ─▶ keep / revert per bundle (SAFE vs RISKY rule below); a cut bundle the critic faults at one moment loses only that cut
   ─▶ the creator's 1080x1920 file is rendered ONCE from the kept ops   ||   one informational AI re-score of before / after
   ─▶ download / revise / toggle / preset (a toggle re-renders full size and re-runs the objective checks)
```

### Keep policy: SAFE vs RISKY
Each op is assigned to a bundle by its type (`edl.bundle_for`); whatever a planner writes in `group` is ignored. A bundle is kept or reverted as one unit.

| Bundle | Class | Contains |
|---|---|---|
| `hook` | SAFE | cold open, hook text |
| `pacing` | SAFE | cuts of real silence (the silence detector decides, not the transcript), clear fillers, stumbles, all on word boundaries and within the limits |
| `captions` | SAFE | burned-in word-highlight captions, retimed after cuts |
| `audio` | SAFE | loudness to -14 LUFS, only when the measured loudness is off target; denoise only when a noise floor is measured in the silences |
| `text` | RISKY | label / CTA overlays (often repeat what the creator already shows) |
| `zoom` | RISKY | zoom punches |
| `rhythm` | RISKY | beat-synced cuts (a cut moved onto a beat) |
| `polish` | RISKY | music ducking, sound effects |

* **SAFE** bundles are kept when every objective check passes and the critic reports no *major* defect (minor notes are shown). If the critic cannot be reached they are kept on the objective checks alone, and the reason says so.
* **RISKY** bundles are kept only when no objective check fails **and** the critic explicitly approves. Unreachable critic = off.
* The **AI re-score is informational only** (one call per edit, labelled "AI-estimated, noisy"). It never keeps or reverts anything.
* Every bundle and every change shows its verdict and reason. Reverted RISKY changes are ordinary toggles: tick one and it is rendered full size and re-checked by the tools.
* A cut bundle with several cuts where the critic faults only some moments (each defect carries a time in the edit): the cuts nearest those moments are switched off on their own and the rest are kept. The remaining cuts were in the video the critic watched but are not watched again as a set.

### What the system adds by itself (`defaults.py`, from measurements)
* **Captions** for any video with speech (English, Hindi, Hinglish). Left off (but present as a toggle, with the reason) when 60 % or more of the analysed frames already show the creator's own text; not added when fewer than 3 words were recognised. Retimed after cuts (`surviving_words`), checked against a fresh transcript of the edited audio, and moved to another position when they would sit on the face in more than 20 % of the shots.
* **Dead air**: a pause between words is cut only where the silence detector (-35 dB) measures real silence inside the word gap (word timestamps are only good to ~0.1-0.2 s); real silence of 0.8 s or more anywhere is cut; fillers only when they are whole transcript words from the clear list. Music-driven reels cut only real silence. At most 8 cuts and 80 % of the 25 % removal limit.
* **Loudness**: added only when the measured loudness is more than 1.5 LU from -14 or the true peak is above -1 dBTP. A file already at -14 LUFS is left alone ("audio left alone" is shown as a decision). Denoise is added only when speech is present and the noise floor measured inside the silences (at least 1 s of them) is above -50 dBFS, so music is never mistaken for noise.
* The recogniser is fed a levelled, de-noised copy when the recording is quiet (< -21 LUFS) or noisy, because a raw quiet recording made it write English for a Hindi song.

### Speed
Target: 150 s or less for a 45 s video. Measured: 52-93 s on the sample videos (see docs/auto-edit-progress.md for before / after per stage). What changed: the planner and the critic run with `reasoning_effort=low`; the planner only plans what needs judgement (the system handles captions, loudness, dead air); the planner, faces, the baseline preview and Demucs run in parallel; verification previews are 540x960 and rendered in parallel (one per bundle plus one with everything); one critic call per edit; one re-score per edit, overlapped with the final render; the full-size file is rendered once. Stage timings are recorded in `state.timing.stages`.

### Storage (`autoedit/storage.py`)
`Storage` interface (`put_file`, `get_file`, `put_bytes`, `get_bytes`, `exists`, `delete`, `delete_prefix`, `list`, `url`).
`LocalStorage` (default, under `AUTO_EDIT_STORAGE_DIR`) and `S3Storage` (boto3; env `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`; unit-tested against a fake client). `AUTO_EDIT_RETENTION_DAYS` (default 7) deletes edit files after that long;
the creator can delete everything with `DELETE /prysm/auto-edit/{analysis_id}`.

### EDL (`autoedit/edl.py`)
Operations: `trim`, `cut_segment`, `remove_filler`, `cold_open`, `text_overlay`, `caption_burn`, `zoom_punch`, `loudnorm`, `music_duck`, `sfx`
plus a `style` block. Every op has `id`, `type`, `group` (the bundle, assigned by the system from the type: `hook|pacing|captions|audio|text|zoom|rhythm|polish`), `source_step_id`, `target_parameter`,
`reason`, `params`. Times are **source-video seconds**; the executor maps them onto the new timeline. Hard rules (the same code
validates planner output and revisions): at most 24 ops (the planner adds at most 8; the rest are the system's own); every time inside the duration; at most 25 % of the video removed; result at
most 90 s; text length limits; zoom punches at least 2.5 s apart; every timestamp must be within 0.25 s of a timestamp that exists in
the analysis text, the transcript (word/segment edges), the detected scene cuts or the silence map, and is snapped to it. Fillers must
be on the clear-filler list and exactly match a transcript word. Anything that fails becomes a **manual tip** instead of an op; a plan
that breaks a global rule is sent back to the planner once with the errors.

### Executor (`autoedit/executor.py`)
One FFmpeg render from the original, always re-encoded (never stream copy):
- keep-segments from trim / cuts / fillers, cut at word boundaries (or, for real silence, exactly where the detector measured it) with 60–120 ms padding; a cut is widened to the neighbouring words' edges by at most 0.6 s, so a 2 s cut next to a long stretch without speech stays about 2 s; audio joined with 40 ms equal-power cross-fades (segments are extended by the fade length so A/V stay in sync);
- cold open: a 1–3 s strongest moment (analysis timestamp + audio-energy peak) placed first as a teaser, a short beat, then the video from the start (skipped when the moment is already in the first 3 s);
- captions + text overlays as one ASS file (libass; Poppins; word-by-word highlight; max 2 lines; kept inside Instagram's safe area x 80–960, y 290–1500 at 1080×1920), retimed to the new timeline;
- zoom punches: eased `scale`+`crop` 1.0 → 1.08–1.15 over 0.25 s about the face centre (OpenCV Haar face detection, centre fallback), alternating, ≥ 2.5 s apart;
- audio: high-pass + light `afftdn` denoise + two-pass `loudnorm` to −14 LUFS with a −1.5 dBTP limit; `music_duck` only when music is detected and Demucs (Modal) is reachable;
- audio is padded with silence to the picture's length (a source whose audio is shorter ends together);
- output 1080×1920, H.264 High, yuv420p, CRF 18 (`veryfast`), AAC 192k, `+faststart`. `proxy=True` renders the same edit at 540×960, CRF 27, AAC 96k: that is what the checks and the critic look at;
- caption events never overlap between chunks (libass would stack them and push the later one up). Non-9:16 sources are placed over a blurred copy of themselves.
- renders are cached per (source, enabled ops, style) so groups can be reverted and toggled without redoing work.

### Verification (`autoedit/verify.py`)
1. **Objective checks** (tools, not an LLM), per bundle on its 540x960 preview: duration, 9:16 format, A/V length (against the source's own), black and frozen picture (by total duration, with tolerance), cuts on word gaps (or inside measured silence), no more than 25 % removed, silence not increased, loudness and true peak, no audible clicks at the cuts (a warning, never a veto), text inside Instagram's safe area (the subtitles are rendered on black and the real pixels measured), caption size, captions off the face, caption sync against a fresh transcript of the edited audio, zoom gaps and the whole face kept inside the zoom.
2. **Critic** (Wonder, `AUTO_EDIT_CRITIC_MODEL`): ONE call per edit. It watches and hears the original and the everything-preview and returns, per bundle, approve / reject plus defects, each with a flag, a severity (major / minor), a time in the edit and a detail. Flags: text over face, cut mid-word, caption timing, audio pumping, click at cut, zoom crops face, duplicates the creator's text, jarring cut, tone mismatch. Falls back to frame strips when the API refuses video (then it cannot judge audio).
3. **Overall AI re-score** (Spark): before and after in one call, 7 parameters, labelled AI-estimated, informational only.

Total cost per edit is capped by `AUTO_EDIT_MAX_COST_USD` (default 1.50, revisions included); on overrun the run stops and delivers the baseline conversion only (nothing was verified) and says so.

### API (`/prysm/auto-edit/…`, login required)
`GET /available` · `POST /{analysis_id}/start` · `GET /{analysis_id}/status` · `POST /{analysis_id}/toggle` · `POST /{analysis_id}/revise` · `POST /{analysis_id}/preset` · `GET /{analysis_id}/download/{mp4|srt|ass|edl|original}` · `DELETE /{analysis_id}`. One running edit per user, 90 s video limit, per-user hourly limit, at most 5 revisions per edit.

### Presets
`clean`, `energetic`, `minimal`: caption size / colour / position, zoom intensity and cut aggressiveness.

### Revisions
A deterministic parser handles the common requests (`bigger captions`, `less zoom`, `keep the intro`, `no zoom`, `faster cuts` …) at no cost; anything else goes to a Spark call that returns an EDL/style diff. The diff goes through the same validation, then a re-render and the objective checks (revisions are not re-scored by AI; the table says so).

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `AUTO_EDIT_ENABLED` | off | master switch (backend routes, retention of the upload, UI) |
| `AUTO_EDIT_PLANNER_MODEL` | `muse-spark-1.3` | planner, revision parser and overall re-score |
| `AUTO_EDIT_PLANNER_EFFORT` | `low` | `reasoning_effort` for the planner and re-score (minimal / low / medium / high; empty = model default) |
| `AUTO_EDIT_CRITIC_EFFORT` | `low` | `reasoning_effort` for the critic |
| `AUTO_EDIT_CRITIC_MODEL` | `muse-wonder-1.0` | critic |
| `AUTO_EDIT_MAX_COST_USD` | `1.50` | per-edit spend cap (revisions included) |
| `AUTO_EDIT_RETENTION_DAYS` | `7` | how long files are kept |
| `AUTO_EDIT_STORAGE` | `local` | `local` or `s3` |
| `AUTO_EDIT_STORAGE_DIR` | `backend/data/auto_edit` | local storage root |
| `AUTO_EDIT_FFMPEG` | auto | ffmpeg binary with libass |
| `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | – | S3/R2 backend |

(See HACKATHON.md for the baseline files that were touched and the founder checklist for Cloudflare R2.)
