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


## Round 2: keep what helps, and make it fast (done)

Goal: the first round kept 1 of 5-7 planned changes per video (the output looked like the original), took 5-7 minutes, and the AI re-score
(noisy: "Audio +22" with no audio change) was the main veto. Changes:

1. **Keep policy.** Ops belong to bundles by type. SAFE (hook, pacing = real silence + clear fillers, captions, audio = loudness/denoise) are kept when the objective checks pass and the critic reports no major defect. RISKY (zoom, label/CTA text, cuts that remove speech or follow the beat, ducking + sound effects) need an explicit critic approval and no failed check. The AI re-score is informational only. A cut bundle the critic faults at one moment loses only that cut. Every change shows its reason; reverted RISKY ones are toggles.
2. **Captions** are added by the system for any video with speech (English / Hindi / Hinglish), retimed after cuts, sync-checked, kept off the face; left off with a visible reason when the creator already has text (60 %+ of frames) or there is no speech.
3. **Speed.** `reasoning_effort=low` for planner and critic, the system adds the measurable SAFE ops itself (so the planner plans less), planner / faces / baseline / Demucs in parallel, 540x960 previews rendered in parallel (one per bundle + one with everything), ONE critic call, one overall re-score overlapped with the single full-size render.
4. **Robustness found while testing** (each has a test): cuts are exact where the signal is silent (word times across held or sung words are unreliable); a cut that would land inside a recognised word or is too short to cut cleanly is dropped before rendering instead of failing a whole bundle; widening a cut to word edges is capped at 0.6 s (a 1.9 s cut had become 8 s); captions chunks no longer overlap (libass stacked them and pushed one up); a bundle where every change was skipped is "skipped", not "kept"; audio shorter than the picture is padded; the A/V-length check is relative to the source; frozen-picture check by total duration; noise floor only from measured silences (music is not noise); the recogniser gets a levelled, de-noised copy of a quiet or noisy recording; hook text with no known face goes to the top band.

### Timings (seconds, end to end through the API, real models)

| | saree 37 s | dance 22 s | Video-291 51 s | raw talk (TTS) 31 s | degraded 291 54 s |
|---|---|---|---|---|---|
| before (round 1) | 409 | 307 | 382 | n/a | n/a |
| after (round 2) | 103 | 85 | 43 | 65 | 107 |
| of which planner | 102 -> 31 | 90 -> 26 | 67 -> 18 | 17 | 21 |
| critic | 4 calls, 131 s -> 1 call, 21 s | 4 calls, 90 s -> 15 s | 4 calls, 150 s -> 10 s | 16 | 28 |
| previews / full-size render | 17-24 / 28 | 8 / 10 | 3 / 10 | 16 / 16 | 32 / 26 |
| cost | $0.43 -> $0.13 | $0.31 -> $0.08 | $0.46 -> $0.12 | $0.10 | $0.14 |

(The 150 s target for a 45 s video is met with a wide margin; the slowest, the 54 s degraded clip at 107 s, is dominated by 7 previews rendering at once and a 28 s critic. Timings vary by ±15 s between runs: the model calls are the noisy part.)

### Kept / reverted per video (round 2, same API, final run)

| Video | kept | reverted or off, and why |
|---|---|---|
| saree (English, creator's own captions) | loudness (-14.2 -> -13.8 LUFS, peak -0.9 -> -1.5), a cut of the unclear tail (36.7 -> 33.9 s, RISKY, critic approved), ducking + sfx | captions off (83 % of frames show the creator's text); label/CTA text (critic: duplicates the creator's own CTA); zoom (skipped: the face fills the frame) |
| dance (sideways source, 2 words) | hook text, label text, 2 zoom punches, ducking | no captions (2 words); one cut left uncut (edge inside a word) |
| Video-291 (Hindi, creator's own lyrics burned in) | 3 zoom punches | captions off (100 % of frames show text); loudness left alone (already -14.1); hook text reverted (critic: repeats the creator's own line) |
| raw talk (TTS speech + noise, -25.6 LUFS, 46 % silence) | hook text, 2 silence cuts (30.6 -> 27.3 s), captions (60 words, median offset 20 ms), loudness + denoise (-25.7 -> -14.1 LUFS, peak -13.1 -> -1.7), 3 zoom punches | label text (critic: repeats the caption); 3 filler cuts left uncut (recogniser times too imprecise or too short) |
| degraded 291 (-29.1 LUFS, noise, 12 % silence, 2 inserted 1.5 s silences, no text) | hook text, silence cuts (54.4 -> 48.7 s, silence 12 % -> 0 %), loudness + denoise (-29.2 -> -14.0 LUFS, peak -17.8 -> -1.5), label text, zoom | captions (critic: the recogniser misses much of the sung chorus and garbles the end) |

`~/Desktop/raw_test.mp4` does not exist on this machine, so the raw-footage test used two generated clips (in `samples/inputs_degraded/`, untracked): the degraded copy above and a spoken clip made with macOS `say` (um / uh fillers, 1.2-2 s pauses, noise bed, -25.6 LUFS) over the same footage. They are synthetic: the picture is a musician, not a speaker.

### Output review (frames, ASS file, objective checks)
* Caption sync on the raw talk: median offset 20 ms over 58 words; captions inside the safe area; after the overlap fix every chunk sits at the same height.
* Clicks at cuts: no spikes at any join (3 joins on the degraded clip, 2 on the raw talk).
* Zoom: all faces stayed inside the crop (analytic check); a zoom is skipped or reduced when the face is large.
* Text over the face: overlays moved off the face where one was detected; in the sideways dance sample no face is detected at the start, so the hook text now defaults to the top band (checked on a frame).

### Honest remaining problems
* Fillers are the weak spot: all 3 TTS fillers were refused (the recogniser's word times abut their neighbours), so filler removal rarely fires on clean TTS-like speech. Real footage with natural gaps should do better; not tested on a real talking head.
* A recogniser that cannot hear a sung chorus gives partial captions; the critic catches it and the bundle is reverted, but there is then no caption at all.
* The critic is one model's opinion (and cannot hear in frame-strip fallback); on 4 of 5 videos it only flagged duplicated text. Its per-moment times are used to drop single cuts; the remaining cuts are not watched again as a set.
* Cuts that remove speech are RISKY: the saree's tail cut (6 s of the closing call to action area) was approved by the critic; a creator may still prefer to keep it (toggle).
* Spend this round (Meta): about $3.5 including two analyses (~$0.7), ~20 edit runs ($0.08-0.17 each) and probes; Modal about $0.05 (Demucs ~$0.005 per music edit).


## Round 3: real raw clip, Google Fonts + style packs, retention-driven edits, synthesised sound, UI, critic (done)

Main test: `samples/inputs/raw_test.mp4` (real, 60.5 s, landscape 1620x1080, spoken, -31 LUFS), plus a Hinglish spoken clip (macOS `say` voice Lekha over the same footage), the degraded Video-291 and the three original samples. Backend tests 633+ pass, web build passes, secret scan clean.

**Built (phase by phase, each committed)**
1. *Filler / dead-air.* Pauses are found from the signal at a level that follows the recording (`ffx.silence_threshold`), shortened to a natural beat and cut exactly; a pitch tracker looks for um/uh-like sounds only where no recognised word explains the audio; a stretch with sound but no words is re-transcribed on its own; the creator-captions rule ignores a logo. The same silence level (shifted by the gain) measures before and after.
2. *Fonts and packs.* All of Google Fonts (1959 families, open licences, static TTF downloaded on first use, glyph coverage checked, Poppins fallback with a visible note, per-script files so Hindi words use the family's Devanagari file), 7 style packs, Spark picks pack + 3 fonts + emphasis words, captions in 3-5 word chunks with pop / bounce / fade / slide and accent emphasis.
3. *Retention and sound.* The director call also turns the retention findings into one fix per drop point (sound accent, zoom, caption emphasis, jump cut / trim) with the finding recorded; density limits (1.5 s between effects, 1 sound per 3 s, 2/3/5 per 10 s by intensity); 12 effects synthesised by FFmpeg (ring, shimmer, riser, impact, glitch, scratch, womp-womp, boing, toot, whoosh, pop, ding), calibrated to sit 24 / 22 / 20 dB under the voice with sidechain ducking; swap, remove, global switch; no music.
4. *UI and critic.* Pack buttons, searchable font picker (categories, live preview), suggested fonts with reasons, intensity, SFX switch, per-change reasons and "Answers: ...", effect swap. The critic also judges caption readability, text over the face, sound masking speech, too many effects and the wrong sound for the moment; a defect with a time reverts only the change nearest it (cuts, zooms, sounds, labels).

**Results (real API, files in `samples/out/`, untracked)**

| Video | Kept | Reverted / off, and why | Time | Cost |
|---|---|---|---|---|
| **raw_test.mp4** (real, 60.5 s) | everything, 24 changes in 7 bundles: hook text, 14 pause cuts (60.5 -> 54.3 s, silence share 29 % -> 10 %), captions (145 words, median offset 20 ms), loudness -31.0 -> -14.1 LUFS (peak -12.7 -> -1.5 dBTP), label text, 3 zooms (faces kept), 1 sound accent (pop) | nothing; critic notes one mis-heard caption word ("weight" for "vague") | 93 s | $0.17 |
| Hinglish spoken (30.8 s) | all 11: hook, 4 pauses (30.8 -> 26.2 s), Hindi captions in Baloo 2 + Hind, loudness -25.2 -> -14.1, zoom, 1 sound accent | none; critic notes "hook" heard as "भूट" | 62 s | $0.11 |
| degraded 291 (54.4 s, -29 LUFS) | hook, 5 pause cuts (-> 46.7 s, silence 17 % -> 0), loudness -29.2 -> -14.0, labels, zoom | captions (critic: nothing captioned for 17 s of singing) | 96 s | $0.16 |
| Video-291 (Hindi lyrics) | pacing, loudness (already -14), zoom | captions (critic: the chorus was transcribed as other words) | 96 s | $0.16 |
| saree (own captions) | loudness, one label, zoom, ducking | captions off (creator's own); one label (duplicates their CTA); speech cut (jarring); sound accent (a comic scratch on elegant posing: wrong sound) | 130 s | $0.14 |
| dance (sideways) | hook, label, zoom | captions (critic: Tamil / Punjabi lyrics garbled) | 76 s | $0.11 |

Timings: round 2 was 43-107 s; round 3 is 62-130 s (more work: the director call runs in parallel with the planner, 7-8 bundles, sound, per-word fonts). Part of this round was measured while an unrelated process held the machine at load 45-56, which inflated those runs (one raw-clip run took 411 s); the figures above are from runs at load under 25. The raw 60 s landscape clip is the slow case (blurred backdrop, two 1620x1080 decodes per preview); the backdrop is now blurred at 1/8 size.

**Spend this round:** Meta about $4.7 of $6 (two analyses ~$0.65 and ~27 edit runs at $0.08-0.17), Modal about $0.1 of $1.

**Honest remaining problems**
* Fillers: the real raw clip has no filler word the transcript shows (tested Whisper with and without a priming prompt: none, and the prompt made it invent "hmm, um" on music, so it is not used), and no um/uh-like sound the pitch tracker could find. The improvement that fires on the real clip is pause tightening (14 cuts). Hesitation detection is tested on a synthetic tone only.
* Captions on sung or noisy-lyric videos are wrong or missing (Whisper cannot transcribe a song); the critic catches it and reverts them (4 of 6 videos), so those videos get no captions. One mis-heard word in a speech video is a note, not a revert.
* Sound effects: only 0-1 per video were kept; I cannot listen, so the levels are checked by measurement (calibrated loudness, peak < -0.5 dBFS), and the critic's "wrong sound" judgement is the only taste check. The critic reverted a comic scratch on an elegant saree pose.
* Font fallback is visible but the picker can still choose a family whose Latin file does not match its Devanagari file in weight; max-fonts is fixed at 2 slots (headline, caption), not creator-adjustable.
* A landscape source is still shown over a blurred copy of itself (no smart reframing).
* The silence share is a tool measurement relative to the file's loudness; after a high-pass and denoise it can rise a little for reasons other than cuts (Hinglish clip: 36 % -> 21 % after cutting, but a noisy bed is classed differently once filtered).
