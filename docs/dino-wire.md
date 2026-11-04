# DINOv2 wired into the real screens (branch `feature/dino-wire`)

The DINOv2 building blocks (`backend/app/studio/dino/`, see `docs/dinov2.md`) are now used by the Instagram import, the analysis, the results page and the Creator DNA page. Glue code: `backend/app/studio/dino/wire.py`. Everything is soft: a failure, a slow service or `DINO_BACKEND=off` only means "no picture embedding" and never blocks an import or an analysis. `DINO_WIRE=0` switches the wiring off completely.

## What changed

| Where | What |
|---|---|
| Instagram import (`instagram/importer.py`) | While a reel's video is on disk (it is deleted right after), `wire.embed_for_import` embeds up to 8 even frames, 90 s limit, beside the transcription. The first failure stops further tries in that import. Embeddings are saved after the import commits (own session); stored embeddings of reels that are no longer imported are removed. The job summary gets `picture_embeddings` and `picture_embeddings_usd` (Modal GPU estimate). |
| Backfill (`POST /instagram/embeddings/backfill`, `GET /instagram/embeddings/status`) | For reels imported earlier: re-lists the account's reels (media links expire), downloads each missing video to a temp folder, embeds, deletes. Same one-job-at-a-time lock as the import; poll `GET /instagram/import/{jobId}`. Cost cap `BACKFILL_COST_CAP_USD` (0.25). Reels Instagram gave no video file for are skipped and not offered again. Button on the Creator DNA page ("Picture matching" card). |
| Analysis (`routers/prysm.py`, one block before `run_full_analysis`, one after, one after the DB save) | `wire.analyse_video` runs beside the analysis (no added wait): mean embedding and the attention strip. The strip is saved on the result as `viewerAttention` (compact JSON); the embedding is saved with `save_analysis_embedding` once the analysis row exists. The auto-edit block is untouched. |
| Results page, Viewer card | `AttentionStrip`: visual change, audio energy, audio change, one bar per half second, each labelled "estimate". Still stretches (picture barely changes) shaded on the first row, quiet dips outlined on the loudness row, plus a plain list. No percentages, no predictions; an unavailable part says why (for example "DINOv2 is off"). With the model off the two audio rows still appear (they need no model). |
| Results page, comparison card | `GET /instagram/closest/{analysisId}` and `ClosestReel`: "Closest to your Reel N", similarity (0 to 1) with a plain band (looks very alike >= 0.8, somewhat alike >= 0.5, quite different), then the top 3 own reels with that reel's real stored numbers in words ("Reached 3,877 accounts (19% above your usual) · Viewers watched on average 69% of it · 27% of views skipped early · 368 likes and 28 comments"). A missing number gives no line. Always the note "Looks similar is about pictures, not results." With fewer than 3 imported reels: the ranking plus "You have fewer than 3 imported reels, so this ranking is a small sample". Reels are numbered like the Creator DNA (newest = Reel 1). Shown inside the Creator DNA comparison card when there is one, else on its own. |
| Disconnect | Also deletes the user's reel embeddings. |
| Stubs (flag off, called by nothing) | `wire.planner_hints_for_edit` (`DINO_PLANNER_HINTS=1`) and `wire.edit_render_check` (`DINO_EDIT_RENDER_CHECK=1`). A test checks that nothing outside `wire.py` references them and that `auto_edit_service.py` does not mention DINO. Wire them after `feature/auto-edit` is merged (see "What Part B must wire" in `docs/dinov2.md`, items 1 and 2). |

The "band" thresholds (0.8 and 0.5) are rough words next to the number, set from the dev fixtures (an edited clip against its own source 0.996, unrelated clips 0.09 to 0.23); they are not calibrated on viewer data.

## Verification (local stack: backend 8781, web 5180, Postgres 55439, Redis 56386)

- Tests: `pytest app/studio/tests` = 605 passed (15 new in `test_dino_wire.py`: strip is labelled estimate with no percentages; plain-language lines; off / failing / slow / disabled model never raises; import embeds each reel and prunes stale ones; failing or off model does not block the import and stops after the first failure; backfill (only missing reels, no-video skipped, cap, model off); ranking, numbering, other users' reels never shown, small sample, every unavailable case; disconnect deletes embeddings; stubs flag-off and unreferenced). Database tests use `IG_TEST_DATABASE_URL`. `web`: `vitest` 5 passed, `npm run build` ok.
- Live, with the deployed Modal service and the Instagram fixture (fake round-number metrics, local DB): a 6-reel import embedded 5 reels (the sixth has no video link) for about $0.0007 of GPU; a real analysis of a 14 s clip stored `viewerAttention` and its embedding and `/instagram/closest` returned the ranking; the backfill added 3 reels after they were deleted; a 2-reel import showed the small-sample note. Screenshots at 1280 px and 390 px: `scripts/screenshot_dino_wire.mjs` (assertions: three rows, "estimate", no percentages, marks drawn, headline, real numbers, honest note, no overflow inside the new cards).
- Strip with marks: a 12 s clip (8 s of real footage, then a held last frame and silence) through the real pipeline gave still stretch 8 to 12 s and quiet dip 8 to 12 s.
- Real `@theprysm7` data, read-only: the account (2 reels with metrics, no embeddings table in that database) was read through `personalise.load` and the same plain-language code; ranking used fixture vectors in memory only. Output, e.g. "Reached 463 accounts (25% below your usual) · Viewers watched on average 13% of it · 66% of views skipped early · 14 likes and 0 comments". Nothing was written to that database and no real reel was embedded (that needs a real download with the token).
- Spend: Modal GPU about $0.003 in total (cap $1); Meta about $0.31 (fixture imports $0.097, one analysis $0.216; cap $0.50).

## Limits (honest)

- Picture similarity says nothing about results; with a handful of reels the ranking is a small sample.
- The strip shows what the video does, not what viewers do. A locked-off shot reads as "still"; a pause in speech reads as a "quiet dip".
- A real-account import and backfill (Instagram media links, real downloads) were not run: only the fixture source and mocked sources. The real-account path uses the same `source.download` the import already used.
- The first embedding after idle costs about 11 s (Modal cold start); the 90 s import limit covers it, and a slower start only means that reel has no embedding until the backfill.
- The results page shows the strip only where the Viewer card shows (Plus plan and the existing source conditions); the shared-results page is unchanged.

## Env

`DINO_WIRE=0` (off; default on), `DINO_PLANNER_HINTS`, `DINO_EDIT_RENDER_CHECK` (stubs, default off); `DINO_BACKEND`, `DINO_MODAL_URL`, `DINO_MODAL_TOKEN` as before. No new migration.
