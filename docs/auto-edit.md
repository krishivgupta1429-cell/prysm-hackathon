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

## Pipeline

```
stored original ─▶ inputs (probe, word transcript, scene cuts, silence map, energy peaks, music flag)
                ─▶ planner (Spark) ─▶ EDL (validated, snapped to real timestamps, manual tips for the rest)
                ─▶ for each group in order hook → pacing → zoom → text → audio:
                       render candidate = base + kept groups + this group      (one FFmpeg render from the original)
                       verify: objective checks · compact re-score (before vs after) · Wonder critic
                       keep or revert (with the reason)
                ─▶ final = last kept state ─▶ download / revise / toggle / preset
```

### Storage (`autoedit/storage.py`)
`Storage` interface (`put_file`, `get_file`, `put_bytes`, `get_bytes`, `exists`, `delete`, `delete_prefix`, `list`, `url`).
`LocalStorage` (default, under `AUTO_EDIT_STORAGE_DIR`) and `S3Storage` (boto3; env `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`; unit-tested against a fake client). `AUTO_EDIT_RETENTION_DAYS` (default 7) deletes edit files after that long;
the creator can delete everything with `DELETE /prysm/auto-edit/{analysis_id}`.

### EDL (`autoedit/edl.py`)
Operations: `trim`, `cut_segment`, `remove_filler`, `cold_open`, `text_overlay`, `caption_burn`, `zoom_punch`, `loudnorm`, `music_duck`
plus a `style` block. Every op has `id`, `type`, `group` (`hook|pacing|audio|text|zoom`), `source_step_id`, `target_parameter`,
`reason`, `params`. Times are **source-video seconds**; the executor maps them onto the new timeline. Hard rules (the same code
validates planner output and revisions): at most 14 ops; every time inside the duration; at most 25 % of the video removed; result at
most 90 s; text length limits; zoom punches at least 2.5 s apart; every timestamp must be within 0.25 s of a timestamp that exists in
the analysis text, the transcript (word/segment edges), the detected scene cuts or the silence map, and is snapped to it. Fillers must
be on the clear-filler list and exactly match a transcript word. Anything that fails becomes a **manual tip** instead of an op; a plan
that breaks a global rule is sent back to the planner once with the errors.

### Executor (`autoedit/executor.py`)
One FFmpeg render from the original, always re-encoded (never stream copy):
- keep-segments from trim / cuts / fillers, cut at word boundaries with 60–120 ms padding; audio joined with 40 ms equal-power cross-fades (segments are extended by the fade length so A/V stay in sync);
- cold open: a 1–3 s strongest moment (analysis timestamp + audio-energy peak) placed first as a teaser, a short beat, then the video from the start (skipped when the moment is already in the first 3 s);
- captions + text overlays as one ASS file (libass; Poppins; word-by-word highlight; max 2 lines; kept inside Instagram's safe area x 80–960, y 290–1500 at 1080×1920), retimed to the new timeline;
- zoom punches: eased `scale`+`crop` 1.0 → 1.08–1.15 over 0.25 s about the face centre (OpenCV Haar face detection, centre fallback), alternating, ≥ 2.5 s apart;
- audio: high-pass + light `afftdn` denoise + two-pass `loudnorm` to −14 LUFS with a −1.5 dBTP limit; `music_duck` only when music is detected and Demucs (Modal) is reachable;
- output 1080×1920, H.264 High, yuv420p, CRF 18, AAC 192k, `+faststart`. Non-9:16 sources are placed over a blurred copy of themselves.
- renders are cached per (source, enabled ops, style) so groups can be reverted and toggled without redoing work.

### Verification (`autoedit/verify.py`)
1. **Objective** (tools, not an LLM): duration, integrated loudness and true peak (`ebur128`), silence ratio, cut count and average segment length, seconds of on-screen text, caption legibility and safe-area check (captions are rendered on black and the real pixel bounding boxes are measured), black / frozen frames, A/V length match.
2. **Compact re-score** (Spark, `AUTO_EDIT_PLANNER_MODEL`): both versions in the same call (random order) with 8 frames and the ASR transcript of each; 7 parameters, labelled **AI-estimated**. The baseline's full 2×7k-token scoring prompts need the Gemini/Modal evidence and cost about 5× more per run, so a compact prompt over the same parameter names is used; before and after always come from the same call, which removes most of the drift between separate runs. It is an estimate, not a measurement.
3. **Critic** (Wonder, `AUTO_EDIT_CRITIC_MODEL`): watches the before and after videos and returns approve / reject per group with reasons and flags (text over face, cut mid-word, caption timing, audio pumping, tone mismatch).

A group is **kept** only if the targeted parameter improves by ≥ 2, no scored parameter drops by more than 3, objective checks pass and the critic approves; otherwise it is reverted and the reason is recorded. If the critic cannot be reached the group is reverted ("could not be verified"). Total cost per edit is capped by `AUTO_EDIT_MAX_COST_USD` (default 1.50, revisions included); on overrun the run stops, keeps what was already verified and says so.

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
| `AUTO_EDIT_PLANNER_MODEL` | `muse-spark-1.3` | planner, revision parser and compact re-score |
| `AUTO_EDIT_CRITIC_MODEL` | `muse-wonder-1.0` | critic |
| `AUTO_EDIT_MAX_COST_USD` | `1.50` | per-edit spend cap (revisions included) |
| `AUTO_EDIT_RETENTION_DAYS` | `7` | how long files are kept |
| `AUTO_EDIT_STORAGE` | `local` | `local` or `s3` |
| `AUTO_EDIT_STORAGE_DIR` | `backend/data/auto_edit` | local storage root |
| `AUTO_EDIT_FFMPEG` | auto | ffmpeg binary with libass |
| `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | – | S3/R2 backend |

(See HACKATHON.md for the baseline files that were touched and the founder checklist for Cloudflare R2.)
