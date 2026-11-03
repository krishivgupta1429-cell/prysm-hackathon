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
