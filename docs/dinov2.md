# DINOv2 (Part A): Modal function, visual change curve, render check, own-reel matching, audio curves

DINOv2 `facebook/dinov2-base` is an **existing Meta model (Apache 2.0)**. The baseline repo already ran it inside the older `prysm-studio-signals` app (one embedding per second, used by the evidence stage). This branch adds a **new, isolated integration**: its own Modal app, two tables, a visual-change curve, a render-integrity check, own-reel matching and audio attention curves. Nothing existing is edited except one router include, one config block, one model import line, one route in `App.tsx` and `.env.example` lines.

Everything is labelled **estimate**. Nothing is invented: when the model service is off or fails the visual parts say `available: false` with the reason.

## What is where

| Piece | File |
|---|---|
| Modal app `prysm-hackathon-dino` | `backend/app/studio/dino/modal_app.py` |
| Client (`embed_jpegs`, `health`, off/unavailable handling, cost) | `backend/app/studio/dino/client.py` |
| FFmpeg frame sampling (4 fps, 8 even frames, frames at timestamps) | `.../dino/frames.py` |
| Visual change curve + static segments | `.../dino/visual.py` |
| Render integrity | `.../dino/integrity.py`, calibration `.../dino/calibrate.py` |
| Audio curves (energy, change, onsets/beats) | `.../dino/audio.py` |
| Attention strip + planner JSON | `.../dino/attention.py` |
| `embed_reel`, `nearest_reels` | `.../dino/store.py` |
| Tables | `backend/app/models/dino_embedding.py`, migration `s9t0u1v2w3x4` |
| Dev API (`DEV_DINO=1`, login) | `.../dino/router.py` |
| Dev page `/dev/dino` (`VITE_DEV_DINO=1`) | `web/src/studio/DevDino.tsx` |
| Dev fixtures (local DB only) | `backend/scripts/dino_dev_fixtures.py` |
| Tests | `backend/app/studio/tests/test_dino.py` |

## Modal function

- App `prysm-hackathon-dino`: a **GPU class `Dino`** (L4, then A10G; in practice an A10 was given every time) and a **CPU web app `api`**.
- Weights are baked into the image at build time (no Volume). fp16, batches of 32, input resized to a short side of 224 px (aspect kept, multiples of 14, long side at most 392), output = CLS embedding, 768-d, L2-normalised. All frames of one request are resized to the first frame's geometry.
- `GET /health` (no auth, no GPU wake-up) and `POST /embed` with `Authorization: Bearer <token>`, up to 64 JPEGs of at most 1.5 MB. Wrong or missing token: 401; no token configured on the service: 503 (fails closed); GPU failure: 502 with the plain reason. Never a fake vector.
- Settings: `DINO_BACKEND=modal|off` (unset: modal if URL and token are set, else off), `DINO_MODAL_URL`, `DINO_MODAL_TOKEN` (the Modal secret `prysm-hackathon-dino-token` holds the same value as `DINO_TOKEN`). `DINO_MIN_CONTAINERS` / `DINO_SCALEDOWN_S` are read at deploy time.
- Deploy: `modal secret create prysm-hackathon-dino-token DINO_TOKEN=<random>` then `modal deploy app/studio/dino/modal_app.py` from `backend/`.

### Latency and cost (measured 2026-11-04, A10, us-east)

| | |
|---|---|
| Cold (first call, 16 frames) | 10.9 s end to end (container start, model load 1.4 s billed boot, run) |
| Warm, 16 frames | 2.4 s end to end; GPU time 0.04 s (the rest is the HTTP hop, the CPU web app and JSON) |
| Warm, 48 frames (one chunk) | 3 to 4.5 s |
| 60 s video at 4 fps (242 frames, 6 calls) | 17 to 27 s wall time, 0.55 GPU seconds |
| `/health` | 4 s when its CPU container was cold, much less warm |
| Calibration run (1,590 frames, 45 calls) | 5.4 to 6.3 GPU seconds, about $0.002 |

GPU cost uses `GPU_USD_PER_SECOND` in `client.py` (A10 $0.000306/s, L4 $0.000222/s; approximate list prices, estimate). The real GPU spend of the whole build and all runs was well under $0.01; the CPU web app and the one-time image build are not included in that estimate and were negligible. Cost per analysed 60 s video: about $0.0002 of GPU; the wait is dominated by the payload and the HTTP hops, not the GPU.

## Visual change curve and static segments

`visual_change_curve(video)`: frames at 4 fps, cosine distance between consecutive frames, the larger of the two distances per 0.5 s bin, scored 0 to 1 per video (divided by the video's 95th percentile, with a floor of 0.10 so a nearly still video is not stretched). Label: **"visual change (estimate)"**.

`static_segments`: runs of 0.5 s bins whose **raw** distance is below **0.02** for **1.5 s or longer**. The raw distance is used on purpose, so "nothing changes" means the same in every video.

Evidence for the 0.02 threshold (raw distance per 0.5 s bin):

| Clip | median | 5th percentile | what it is |
|---|---|---|---|
| frozen 4 s inserted in `raw_test.mp4` | 0.0001 | | a held frame: caught (10.0 to 13.5 s; the last bin holds the cut back to motion) |
| `raw_test.mp4` (talking head, moving) | 0.058 | 0.018 | only one static stretch: 58.5 to 60.5 s, the held end frame |
| `dance_before` | 0.080 | 0.025 | none |
| `saree_before` | 0.102 | 0.023 | none |
| `reel291_before` (locked-off shot, a guitarist barely moving) | 0.010 | 0.005 | almost the whole video is static, which is true of the picture |

Limit: "static" means the picture barely changes, not that nothing is happening. A locked-off shot of a person playing or talking reads as static; the planner should treat it as "could use a cut, zoom or cutaway", not as dead air. Camera shake and a cut both read as large change.

## Render integrity

`render_integrity(original, edited, edl=None, timestamps=None)`: for every kept stretch (2 s pieces, 3 sample frames each) the edited frame at time t is compared with the source frame at the matching time (the EDL's enabled `cut_segment` ranges shift later edited times; explicit `(edited_t, source_t)` pairs also work). Segment similarity is the mean cosine similarity of its samples; below **0.85** it is flagged. The EDL is read as plain JSON; the edit engine is not imported.

**This does NOT detect a cut-off face.** A global picture embedding can stay similar when a face is half cropped. It also cannot tell a wrong segment from a right one when the scene looks the same (see below).

### Calibration (three real pairs plus synthetic degradations)

Pairs (read-only copies of `~/prysm-hackathon-edit/samples/out`): `dance`, `saree`, `reel291`, scored with their real EDLs. 55 real segments: median 0.996, 5th percentile 0.954, lowest 0.748. Synthetic cases are made from the "after" videos (first 24 s) and compared with their own source at the same timestamps (35 segments each):

| Case | min | median | max | flagged at 0.85 |
|---|---|---|---|---|
| real pairs | 0.748 | 0.996 | 1.000 | 2 of 55 (explained below) |
| black frames | -0.04 | 0.00 | 0.04 | 100% |
| 3x over-zoom (centre crop) | 0.47 | 0.64 | 0.76 | 100% |
| hard crop, left third | 0.38 | 0.65 | 0.81 | 100% |
| 1.14x zoom held for the whole clip (the planner's punch-in scale) | 0.90 | 0.94 | 0.98 | 0% |
| 1.3x zoom held for the whole clip | 0.82 | 0.91 | 0.97 | 3% (40% at 0.90) |
| wrong segment (source offset by 7 s) | 0.15 | 0.75 | 0.99 | 63% |

Threshold **0.85**: all broken renders of the obvious kind are caught, ordinary punch-ins are not, and the 5th percentile of real segments (0.954) leaves a margin of 0.1. Lowering to 0.80 would still pass the real pairs but miss some hard crops (97% caught); raising to 0.90 starts flagging legitimate 1.3x zooms (40%).

Two honest findings:
- **Wrong segments are only caught when the scenes differ.** In `reel291` (one locked-off shot) a source offset of 7 s still scores 0.96, so a wrong segment there is invisible to this check. Offsets in `dance` (mean 0.75) and `saree` (mean 0.43) were mostly caught.
- **The two real segments below 0.85 are in `saree`, after its cut.** The first 30 s of the render match their source frame for frame (0.99+). After the cut, the render sits 3.0 s later in the source than the EDL says (1.9 s), checked by searching for the best-matching source frame: 30.5 s in the render best matches source 33.5 s (similarity 0.92), not the 32.4 s the EDL implies (0.64). So the checker reported a real disagreement between the EDL file and the rendered video (or a stale EDL file); the render itself is not broken. The saree sample files were also rewritten by the edit worktree while this was being built, so the saree numbers may change when they are regenerated. The sample set is small (three pairs, one of them with a cut); recalibrate when more pairs exist (`python -m app.studio.dino.calibrate <samples> <work> out.json`).

## Own-reel matching

- `embed_reel(media)`: up to 8 evenly spaced frames, mean vector (L2-normalised) for storage.
- Tables (new only, `pgvector vector(768)`): `reel_visual_embedding` (`user_id`, `media_id` string, `mean_vector`, `frame_count`, `created_at`; unique per user and media id; **no foreign key into any `ig_*` table**) and `analysis_visual_embedding` (`analysis_id`, `mean_vector`, `frame_count`, `created_at`). No ANN index: the query filters by user first (tens to hundreds of rows), where an exact scan is faster.
- `nearest_reels(db, vector, user_id, k)`: own reels only, ranked by cosine similarity, each with the metrics already stored for that reel, joined by media id string at query time from `ig_reel` / `ig_account` (raw SQL; columns `permalink, posted_at, duration_s, like_count, comments_count, performance_index, performance_basis, watch_through`). Missing table or row: the reel is listed with `metrics: null` and the reason. Stored `NULL` stays `null`; nothing is estimated. **This relies on the column names of the Instagram branch** (checked against `feature/instagram-login` on 2026-11-04); re-check after that branch changes.
- Limit: "looks similar" is about pictures. Similar-looking reels can perform very differently; the page says so.
- Dev check: with fixture reels (real DINOv2 embeddings of sample clips stored under `fixture_*` ids, fake round-number metrics, local DB only) the edited `dance_after` clip ranks `fixture_dance` first at 0.996, the others at 0.09 to 0.23. No real `@theprysm7` reels or other worktree's database were touched.

## Audio curves (no model, no download, no EnCodec)

EnCodec's licence is CC-BY-NC (non-commercial): the new code does not import or call it (a test checks). Beats use librosa (ISC licence, already in `requirements.txt`); everything else is FFmpeg and NumPy.

- `audio_energy_curve`: RMS loudness in dBFS per 0.5 s (floor -80). Scores 0 to 1 per video (5th to 95th percentile, at least a 6 dB span).
- `audio_change_curve`: spectral flux per 0.5 s (sum of positive changes of the log-magnitude spectrum, 1024-point FFT, 16 ms hop), scored 0 to 1 against the video's 95th percentile with a floor of 8 flux units (a -60 dBFS noise floor gives about 2, speech 10 to 14, music 40 to 70), so silence or a steady tone stays near 0.
- `beat_onsets`: onset times (librosa, `delta=0.3`, 0.1 s minimum gap: the defaults gave about 4 onsets per second on speech) and beats. **Beats are reported only when the onset strength is periodic** (autocorrelation at 0.3 to 1.0 s lags of at least 0.2; click track at 120 bpm 0.93, dance clip 0.69, saree 0.25, speech 0.05 to 0.11). Otherwise `beats: []` and a note "no steady beat found". Tempo can come out at half or double the felt tempo.
- No audio track: each part is `{"available": false, "reason": "no audio track"}`.
- Tests use synthetic audio: silence, steady tone, a sudden loud hit (energy, change and onset all land on the hit), a 120 bpm click track, and noise.

## Dev API and page

All behind `DEV_DINO=1` (router not mounted otherwise) and a login:

- `GET /dino/status`; `POST /dino/curve` (video) returns the **attention strip**: `visual_change`, `audio_energy`, `audio_change` on one 0.5 s grid, the detail objects, usage, and `planner`.
- `POST /dino/integrity` (`before`, `after`, optional `edl` JSON).
- `POST /dino/embed/{analysis_id}` stores an analysis' embedding (needed before `/nearest`); `GET /dino/nearest/{analysis_id}`.
- `/dev/dino` (dev server with `VITE_DEV_DINO=1`): the three strips with static segments shaded and onset ticks, per-segment integrity bars with the threshold and flagged segments hollow, and the nearest own reels. Checked at 1280 px and 390 px wide.

### Planner JSON (for the edit planner, Part B)

```json
{"label": "planner hints (estimate)",
 "available": {"visual": true, "audio": true},
 "static_stretches": [{"start": 58.5, "end": 60.5, "duration": 2.0}],
 "loudness_dips": [{"start": 57.5, "end": 60.5, "min_db": -44.4, "below_median_db": 11.1}],
 "onset_times": [0.08, 0.43, 0.7],
 "beat_times": [],
 "tempo_bpm": null}
```
Times are seconds of the original video. A list is empty when its source is unavailable; `available` says which sources ran. Loudness dips are stretches of 1 s or more at least 8 dB under the video's median; a pause in speech is a dip too.

## Real run on `~/Desktop/raw_test.mp4` (60.4 s, 1620x1080, with audio)

242 frames, 121 bins. Visual change: median raw distance 0.058, 95th percentile 0.151 (scale), static stretch 58.5 to 60.5 s (held last frame). Loudness: about -30 dBFS typical; dip 57.5 to 60.5 s (-44 dB). Audio flux 95th percentile 14 (speech). Onsets: 51; beats: none (confidence 0.07, speech). The page shows these as three strips.

## Honest limits (summary)

- All curves are estimates of change, not of interest or quality.
- DINOv2 is a global picture descriptor: no face, pose or text awareness; a cut-off face can pass the render check.
- Wrong segments in repetitive or locked-off scenes pass the render check.
- Calibration is on three pairs (one with a cut) plus synthetic degradations, so thresholds are rough; the saree sample's EDL and render disagree after the cut.
- Matching ranks by looks, not by results.
- The Modal GPU class scales to zero: the first call after idle costs about 11 s.
- Own-reel metrics come from another branch's tables by column name.

## What Part B must wire after the merges

1. **Planner**: feed `planner.static_stretches`, `loudness_dips`, `onset_times` and `beat_times` to the edit planner (for example: suggest a punch-in, cut or cutaway inside a static stretch; land zoom punches on onsets or beats; do not cut inside a loudness dip that is a wanted pause). Call `attention_strip(video)` once per analysis.
2. **Edit verifier**: after the render, call `render_integrity(original, edited, edl)`; block or warn on `ok == false`, show the flagged segments. Treat a mismatch right after a cut as "the EDL and the render disagree" first (see the saree finding). Add a face check for the cut-off-face case.
3. **Comparison card**: call `embed_reel` for the analysis video, `save_analysis_embedding`, and show `nearest_reels` next to the results with the label "looks similar to your own reel (estimate)".
4. **Viewer card / own-reel import**: when the Instagram import downloads a reel, call `embed_reel` and `save_reel_embedding(user_id, media_id)`; re-check the `ig_reel` column names used by `ig_metrics_lookup`.
5. **Migrations**: this branch's migration `s9t0u1v2w3x4` (first written as `n4o5p6q7r8s9`, the same id the Instagram branch used; renumbered at merge) has `down_revision = 'm3n4o5p6q7r8'`, like the other hackathon branches' migrations; after merging the merge revision `t0u1v2w3x4y5` joins them (already done on main) and `alembic upgrade head`.
6. Decide the licence review for EnCodec (existing baseline code, not used by the new code).
