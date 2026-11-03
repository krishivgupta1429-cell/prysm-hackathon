# Defects seen in the first local results: causes and fixes

Built during the Meta Global AI Developer Hackathon (new work). Evidence: the saree-reel result (the latest local run of Video-57419, saved in the local database), its backend log lines, and three production results made by the original Claude pipeline.

## 1. Overall score too low (fixed)

The baseline niche weights add up to 1.0 across all ten parameters, but only six parameters are scored, so the six weights add up to about 0.6 and the weighted sum topped out near 61. Parameters averaging 69 gave 42. The production results use weights that add up to 100 over the six scored parameters (19/17/16/16/16/16) and their stored overall equals the weighted mean, so the production behaviour was normalised. `app/studio/score_math.py` now normalises the six weights to 1 for both the overall and the "after fixes" projection. Test: six parameters at 70 give 70 for every niche.

## 2. Step cut off mid-word (fixed)

Saree result, Trend Alignment: `Step 1 — In your editing app, mark your dance section from 'd`, and its "what to expect" was empty.

Cause: the token budget, not a sanitiser. Call A asked for `max_tokens=7000`; the shim added 6,000 tokens of headroom for Spark's hidden reasoning (13,000 in all). The log line for that call reads `12044 in / 13000 out (9604 reasoning)`: reasoning used 9,604 of the 13,000 tokens, leaving about 3,400 for the answer, so the reply stopped inside the Trend Alignment `howToDoIt` string (`finish_reason: length`). The baseline parser logged `JSON parse failed (Unterminated string ...) using json_repair` and json_repair quietly closed the object, which is why the result looked complete apart from the cut text. The shim only retried when the reply was completely empty.

Fix: (a) the shim now also retries when the reply stops at the token limit, with doubled headroom (up to two retries), and keeps the wasted attempt in the usage and cost; (b) scoring calls start with 10,000 tokens of headroom (`META_SCORING_HEADROOM`); (c) a log warning names any parameter field that still looks cut off. The baseline validators, `max_length` clips and quote handling were checked and tested with apostrophes, curly quotes and quoted phrases: they do not cut text (tests in `test_truncation.py`).

## 3. Rate limiter 429 after about 6 minutes (fixed)

Cause: the baseline middleware called `INCR` then `EXPIRE` on every request, so each request pushed the counter's expiry back another 60 s. A client that kept polling never saw the counter reset: after 120 polls (3 s apart is about 6 minutes) every request got 429. The per-route "status" rule (120 a minute) had the same flaw. Fix: the status poll is exempt from the global per-IP limit, keeps its own 120/min per-user limit, and every window is now fixed from its first request. Tests reproduce the old behaviour and pass with the fix.

## 4. Where do the repeated "before you post" ending and the generic Visual Quality step come from? (reported, prompts not changed)

**"Before you post" in every fix: a prompt rule, followed literally by the model.** For a raw upload the baseline tone block says: `Use "before you post" language: "Fix this before you post" NOT "In your next video"` (prompt text near line 1923 of `prysm_analyse_service.py`), and the upload block adds `Frame everything as a pre-post checklist` (near lines 2074 and 2197). In the saree result the phrase appears 11 times, in the one-sentence `fix` of 9 of the 10 parameters (the original Claude pipeline's three production results use it 0 to 2 times each). So the rule is in the prompt; Spark obeys it more literally than Claude did. The style addendum (off by default) tells the model not to repeat the same closing; the prompt itself is untouched.

**"Export in vertical full-screen with no black bars" for a vertical reel: partly a model habit, partly the baseline's own post-processing.**
- The Visual Quality prompt block asks for "resolution, lighting, framing, watermarks, borders, aspect ratio" and gives `howToDoIt` only as "step-by-step" (no specific instruction, unlike the 3-step recipes of the other parameters). With nothing concrete to write about, the model falls back on a generic technical step about borders and aspect ratio even when the reel is already full-screen vertical (contradiction check, rule 14, is not enforced by anything).
- The baseline validator `_validate_suggestions` (check 4, timestamp de-duplication) deletes any step whose timestamp was already used by another parameter unless the step contains that parameter's domain words. In this run it skipped Visual Quality steps at 10 s and 12 s ("claimed by Retention & Pacing"), which left one generic step. It also removed steps from On-Screen Text (3 s, 24 s, 8 s), Replay Value (0 s) and Call to Action (0 s). So thin step lists come from two causes together: the model writing short lists when the prompt says "step-by-step" or "3 concrete steps", and the validator removing the specific ones. The style addendum asks for at least 3 steps per parameter, but it cannot undo the validator; a change to the validator would be a change to baseline logic and was not made.
