# Predicted Viewer Behaviour on Modal (Qwen2.5-VL zero-shot + Q-Align)

Built during the Meta Global AI Developer Hackathon (new work). Why this is not the real LMM-EVQA: `docs/evqa-feasibility.md`. How it behaved on real videos: `docs/engagement-bakeoff.md`.

## What runs

Modal app **`prysm-hackathon-engagement`** (separate from `prysm-studio-signals`), file `backend/app/studio/engagement_modal_app.py`, Volume `prysm-hackathon-engagement-cache` (weights, so a cold start does not download them).

| Class | Model | GPU | What it returns |
|---|---|---|---|
| `QwenZeroShot.predict` | stock `Qwen/Qwen2.5-VL-7B-Instruct`, asked for `{"nawp","ecr"}` (a port of the old RunPod worker's prompt, greedy decoding) | L4 24 GB (falls back to A10G 24 GB) | `nawp`, `ecr` (0 to 100), the raw reply, timing. Any unparsable or out-of-range reply raises; there is no 40/55 fallback. |
| `QAlign.score` | `q-future/one-align` | L4 (falls back to T4 16 GB, then A10G) | video `quality` (frames as a clip) and mean per-frame `aesthetic`, both 1 to 5 |

No `evqa_predict`: the trained weights are not downloadable. If someone later obtains them, add a third class in the same file (a 48 GB class, the VideoLLaMA2 and Qwen2.5-VL fine-tunes from the Baidu shares, with title, description and the video bytes as inputs) and return `source: "evqa"`. The backend, the card and the tests already know that source.

Containers scale to zero. `ENGAGEMENT_SCALEDOWN_S` (default 240) is how long a container stays up after its last call, and `ENGAGEMENT_MIN_CONTAINERS` (default 0) keeps containers warm. Both are read when you **deploy**.

## Deploy

From `backend/`, with Modal credentials (`modal token new`, or `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` in `backend/.env`):

```
modal run app/studio/engagement_modal_app.py::download_weights     # once; CPU only, about 33 GB into the Volume
modal deploy app/studio/engagement_modal_app.py
```

Pins: Qwen runs at about 250 x 450 px per frame (`ENGAGEMENT_QWEN_MAX_PIXELS`, read at deploy) because a 24 GB card ran out of memory at higher resolution. Qwen image `torch==2.5.1`, `transformers==4.49.0`, `qwen-vl-utils==0.0.10`; Q-Align image `torch==2.1.2`, `transformers==4.36.1` (the version its README names), `trust_remote_code`. Model revisions default to `main`; set `ENGAGEMENT_QWEN_REVISION` and `ENGAGEMENT_QALIGN_REVISION` to a commit hash to pin them.

## Before a demo (warm-up)

```
cd backend && python ../scripts/engagement_warmup.py              # boots both GPU classes with a blank frame
python ../scripts/engagement_warmup.py --repeat 5 --every 180     # keep them up for about 15 minutes
```

Or deploy with `ENGAGEMENT_MIN_CONTAINERS=1` for the whole demo (about $0.80 an hour per class, so $1.60 an hour for both) and redeploy with 0 afterwards.

## Backend settings (`backend/.env`)

| Variable | Meaning |
|---|---|
| `ENGAGEMENT_BACKEND` | `modal`, `runpod` or `heuristic`. Unset: `modal` when Modal credentials exist, else `heuristic`. |
| `ENGAGEMENT_ZEROSHOT_EVIDENCE` | `1` gives the zero-shot ECR/NAWP guess to the scoring step as a soft risk hint. Default off, because the guess barely moves with the frames (`docs/engagement-bakeoff.md`) and the baseline prompt says the Hook and Retention scores must align with it. |
| `ENGAGEMENT_QALIGN_EVIDENCE` | `1` adds a one-line Q-Align picture-quality note to the scoring evidence. Default off. Q-Align is never turned into a watch percentage. |
| `ENGAGEMENT_USD_PER_GPU_SECOND` | One rate for the cost estimate (falls back to `MODAL_USD_PER_GPU_SECOND`). Unset: per-GPU rates, L4 $0.000222, A10G $0.000306, T4 $0.000164 per second. |
| `ENGAGEMENT_CALL_TIMEOUT_S` | Per-call wait, default 420 (covers a cold start). |

If the Modal call fails, the card shows "Prediction unavailable" with the reason, and nothing about it reaches the scoring prompt. `heuristic` with no signals is also "unavailable" (the baseline used to return 45 and 55 here).

The scoring prompts (baseline text, unchanged) say the Hook and Retention scores "MUST align" with ECR and NAWP. With `ENGAGEMENT_ZEROSHOT_EVIDENCE=1`, the context for `qwen_zero_shot` says these are a rough guess, not to be quoted, a soft hint only (`build_engagement_context`). Without it (the default) the scoring prompt gets no engagement block from this source.

## Log line and cost

One line per call: `[Engagement] backend=modal latency=… cold|warm gpu_seconds=… est_cost=$… (estimate at $…/GPU-s)`. The estimate counts the seconds the methods ran, plus the boot on a cold call; it leaves out the idle scaledown tail (up to `ENGAGEMENT_SCALEDOWN_S` per container). The estimate also goes into the analysis cost breakdown as "Predicted viewer behaviour + Q-Align (Modal GPU, estimate)".

## Check Modal spend

```
modal billing report --for today -r h --json     # per app; look for "prysm-hackathon-engagement"
modal billing summary                            # month so far
modal billing rates                              # current GPU rates
```

Or the Modal dashboard, Usage, filtered to the app.
