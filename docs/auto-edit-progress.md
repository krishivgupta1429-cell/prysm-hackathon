# Auto-edit progress log

Branch `feature/auto-edit` (fast-forwarded to `main` first so the step-list format and the cost panel are present). Spend caps for the
whole task: Meta $15, Modal $3.

## Phase 1: the engine (done)

**Works** (all covered by offline tests on a generated clip, and rendered on the saree sample):
- Storage interface, local backend and S3/R2 backend (tested against a fake client), retention sweep, key safety.
- Inputs: probe, word-level Groq Whisper transcript (Hindi / Devanagari / Hinglish words are kept as written), scene cuts, silence map, energy peaks, music flag, analysis timestamps parsed out of the fix steps, conservative filler candidates.
- EDL schema and hard validation (≤ 14 ops, ≤ 25 % removed, ≤ 90 s, text limits, timestamps must exist in the analysis / transcript / cuts and are snapped, fillers must be whole transcript words on the clear list, zoom spacing); anything not executable becomes a manual tip.
- Planner (Spark `muse-spark-1.3`): one call, one retry with the errors, then automatic trimming. Saree sample: 6 ops + 11-14 manual tips, $0.03, 50-60 s.
- Executor: one FFmpeg render from the original (re-encode, never copy): word-boundary cuts with 60-120 ms padding and 40 ms equal-power cross-fades with A/V kept in sync, cold-open teaser + beat, ASS captions with word-by-word highlight retimed to the new timeline (Poppins, OFL, Latin + Devanagari, libass shapes conjuncts), hook / label overlays kept off the face, eased zoom punches about the face (YuNet; reduced or skipped when the face would be cropped), loudnorm two-pass to -14 LUFS with a -1.5 dBTP limit + light denoise, music duck (Demucs on Modal, only when music + speech), 1080x1920 H.264 High yuv420p CRF 18 AAC 192k faststart, blurred-copy padding for non-9:16 sources, per-group render cache.

**Not working / not done yet:** nothing known in Phase 1. Smart reframing is Phase 4.

## Phase 2: proof and undo (done)

**Works:** per group the candidate is rendered, then checked three ways and kept or reverted with the reason:
- objective checks by tools (duration, format, A/V length, black / frozen frames, silence ratio, cuts on word gaps, ≤ 25 % removed, LUFS / true peak, safe-area test on the rendered text pixels, caption sync against a fresh transcript, zoom spacing, face kept in frame);
- a compact AI re-score (before and after in one Spark call, random order, 7 parameters, labelled "AI-estimated");
- a Wonder critic that watches the before and after videos (the API accepts video and hears the audio) with approve / reject and flags.
Keep rule: target +2 or more, nothing scored drops more than 3, objective checks pass, critic approves (unreachable critic = revert, "could not be verified"). Cost cap per edit (default $1.50, revisions included) stops the run and keeps what was already verified. One log line per edit with cost and time.

First live run (saree, `clean`): 6 ops planned, audio group kept (ducking + loudnorm, Audio & Music +9 AI-estimated), zoom reverted (+0 on the target), pacing trim reverted by the critic (it cut the creator's burned-in "Comment down…" caption mid-phrase, a real fault the tools alone did not see). Cost $0.30 (Spark $0.12, Wonder $0.17, Modal $0.004), 7 min; re-score and critic now run in parallel.

**Spend so far:** Meta about $0.35, Modal about $0.01.

## Phase 3: API and creator controls (done)

**Works** (API tests with faked DB/Redis, plus the real stack on ports 8779/5178):
- `/prysm/auto-edit/…` (mounted only when `AUTO_EDIT_ENABLED`): `available`, `start`, `status` (live trace), `toggle`, `preset`, `revise`, `download/{mp4|srt|ass|edl|cover|original}`, `DELETE`, founder `cost`. Login required, ownership checked, one running edit per user (Redis lock), 6 starts/hour, 90 s limit, 5 revisions per edit, daily spend cap of the safety net respected and fed.
- Uploads are copied into the storage interface before the analysis cleanup; links never get Auto-edit ("only available for videos you uploaded").
- Presets Clean / Energetic / Minimal (caption size/colour/position, zoom intensity and count, cut aggressiveness, transition).
- Revisions: rules first (bigger/smaller captions, less/more/no zoom, keep the intro, caption position or off, preset, louder/quieter, hook text off, cold open off); anything else is one Spark call that returns a diff; same validation as the planner. Live: "bigger captions" re-rendered in 15 s for about $0.001 and passed the objective checks; "make it feel more cinematic" went to Spark and was turned into the minimal preset (as a diff, for about $0.02); toggling a change off and on re-rendered the subset.
- Results-page card (black and white, inline styles): button, live trace, before/after players, every change with kept/reverted verdict, reason and a toggle, presets, revision box, verification tables labelled AI-estimated, per-group checks, manual tips (first 6, rest folded), downloads, delete. Checked in a real browser at 1280 px and 390 px: no horizontal scroll, no page errors. Nothing renders when the flag is off (routes are not even mounted).
- Founder cost panel shows the Auto-edit rows.

## Phase 4: polish (done, in this order)
1. Beat-synced cuts: librosa beat tracking; for music-driven reels (music and under 35 % speech) cut points snap to a beat within 120 ms (tested on a synthetic click track).
2. Sound effects: whoosh at visible jump cuts and an impact at a cold-open hand-over, synthesised in FFmpeg (no samples, no licences), mixed low (-22 dB), op `sfx` in the audio group.
3. Transitions: the minimal preset dissolves (120 ms video xfade + matching audio cross-fade) into jump cuts; word-level cuts stay hard cuts.
4. Cover frame: a real frame of the final video (the analysis' best-thumbnail moment mapped to the new timeline) with the hook text, downloadable. Muse Image (`muse-image-1.0`, answers `/images/generations` on this key) was probed once (no spend recorded beyond a few cents) but **not used**: a generated cutaway would not be the creator's footage.
5. Smart 9:16 reframing with subject tracking: **not done** (time box). Non-9:16 sources are placed over a blurred copy of themselves.
6. Colour correction / end card: **not done**.

## End-to-end on the three sample videos (through the real API, files in `samples/out/`, untracked)

| | Video-57419 saree (English) | Video-86506 dance (Hindi) | Video-291 acoustic (Hindi) |
|---|---|---|---|
| preset | clean | energetic | clean |
| ops planned / kept / reverted | 7 / 1 / 6 | 5 / 1 / 4 | 7 / 1 / 6 |
| kept | zoom punch (Retention +4) | hook text (Hook +29) | hook text (On-Screen Text +14) |
| reverted, and why | pacing (Hook -5, critic: cut the closing CTA mid-phrase); text (critic: duplicates the creator's own burned-in caption and CTA); audio (+0) | trim (Retention -4); zoom (+0); label text (Audio -16 = estimator noise) | trims/zooms (+0); audio (critic: guitar pumps under the vocal) |
| length before / after | 36.6 / 36.6 s | 21.6 / 21.6 s | 51.4 / 51.4 s |
| loudness before / after | -14.2 / -14.2 LUFS | -14.2 / -14.2 | -14.1 / -14.1 |
| cost of the edit | $0.43 (Spark 0.15, Wonder 0.27) | $0.31 (Spark 0.15, Wonder 0.16) | $0.46 (Spark 0.14, Wonder 0.31, Modal 0.004) |
| time | 409 s (render 83 s, plan 102 s) | 307 s (render 38 s, plan 90 s) | 382 s (render 36 s, plan 67 s) |
| manual tips | 28 | 12 | 8 |

Overall AI-estimated re-score (before -> after): dance Hook 58->78, On-Screen Text 22->78 (but also Audio 60->82 with no audio change: noise); 291 Hook 55->68, On-Screen Text 60->78; saree no change. The samples were already loudness-normalised (-14 LUFS), so loudnorm had nothing to gain and was correctly reverted.
