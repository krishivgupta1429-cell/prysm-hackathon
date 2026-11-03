# Checking the app's cost against Meta's usage dashboard

Built during the Meta Global AI Developer Hackathon (new work).

Goal: confirm that the dollar figure the app records for one analysis matches what Meta actually billed, in particular that
Muse Spark's hidden **reasoning tokens** are billed as output tokens and are therefore inside the app's number.

## What the app counts

- Meta's chat API returns `usage.completion_tokens` **including** `completion_tokens_details.reasoning_tokens`. The shim prices
  `completion_tokens` (so reasoning is charged at the output rate) and keeps the reasoning count next to it. A unit test
  (`test_reasoning_tokens_are_part_of_the_output_tokens_and_the_cost`) pins this.
- A reply cut off by the token budget is retried; both attempts are billed, and both are in the number.
- The breakdown is in the result's `_cost.breakdown` (founder / local only) and in one backend log line per job:
  `grep "\[JobCost\]" samples/_local/backend.log`.
- Spark/Wonder prices (per 1M tokens): Spark 1.3 and 1.2 $1.25 in / $4.25 out, Wonder $4.00 in / $20.00 out (`backend/app/studio/config.py`).
  Voice Transcribe $0.18 per audio hour, SAM 3.1 $0.20 per 1,000 frames. Modal GPU is an estimate (GPU seconds x a rate; set
  `MODAL_USD_PER_GPU_SECOND` to change it). ACRCloud and Apify are per-call fees that the breakdown only flags.

## Procedure (about 10 minutes, one analysis, no one else using the Meta key)

1. Open Meta's usage / billing dashboard for the API key's account and note the **balance** (and, if shown, the per-model token
   counters) **before**. Write down the time.
2. Make sure nothing else is calling the key (no other analyses, no chat, no `scripts/rescore.py`).
3. Run exactly one analysis through the website or `samples/_local/run_job.py` (local stack: http://localhost:5174).
4. Wait for the dashboard to refresh (it can lag a few minutes) and note the balance **after**.
5. Compare:
   - `balance before - balance after` should equal the sum of the **Meta** parts of the breakdown: Spark 1.3 vision + scoring,
     Spark 1.2 audio, Voice Transcribe and SAM 3.1 (not Gemini, Groq, Modal, ACRCloud or Apify, which are billed elsewhere).
   - If the dashboard shows tokens per model: output tokens for `muse-spark-1.3` should equal the app's vision + scoring
     `tokens_out`, **and** that figure already contains the reasoning tokens. If the dashboard's output figure is smaller by about
     the reasoning count, Meta is NOT billing reasoning and the app overestimates by `reasoning_tokens x $4.25 / 1M`.
6. A difference under about 3% is rounding and pricing lag. Larger gaps: look for retries (`reply cut off by the token budget` in
   the log) and for calls made outside the analysis (ARIA chat, probes).

## Fill in

| | Before | After | Difference |
|---|---|---|---|
| Dashboard balance (USD) | | | |
| App: Meta parts of the breakdown (USD) | | | |
| Dashboard output tokens, muse-spark-1.3 | | | |
| App: vision + scoring `tokens_out` | | | |
