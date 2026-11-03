# Predicted Viewer Behaviour: bake-off on three videos

Built during the Meta Global AI Developer Hackathon (new work). Run on 2026-11-04 with `scripts/engagement_bakeoff.py`. The analysis pipeline was not re-run: frames were cut with FFmpeg (3 in the first 3 s, 5 spread over the rest), the heuristic was fed the saved signals of earlier local runs, and the models ran on Modal (`docs/modal-engagement.md`).

**Which backends:** PATH A (the real LMM-EVQA) was not possible, its weights are on Baidu Yun only (`docs/evqa-feasibility.md`). So the comparison is the Qwen2.5-VL zero-shot port (the new `qwen_zero_shot` source), Q-Align, and the baseline heuristic.

**Which videos:** the saree reel is Video-57419 (confirmed by its first frame), and Video-86506 is the "dance" run. Those are the only two videos in the local samples, so the third is `Video-291.mp4` from the Desktop (51 s, no saved analysis, so no heuristic and no parameter scores for it).

## Results

Each model ran twice per video. Every repeat gave identical numbers (greedy decoding, no sampling), so the second value is not shown.

| Video | Length | Qwen zero-shot: hook hold (ECR) | Qwen zero-shot: watch rate (NAWP) | Q-Align quality (1 to 5) | Q-Align aesthetics (1 to 5) | Heuristic ECR / NAWP | Saved scores: Hook / Retention / Visual |
|---|---|---|---|---|---|---|---|
| Saree reel (57419) | 36.7 s | 70 | 85 | 4.32 (Good) | 2.69 | 52.5 / 39.6 | 66 / 62 / 76 |
| Video-86506 | 21.7 s | 70 | 65 | 3.99 (Good) | 2.18 | 53.8 / 41.0 | 52 / 60 / 74 |
| Video-291 | 51.5 s | 70 | 65 | 3.74 (Good) | 2.74 | not available | not available |

Timing and cost (Modal, estimates from the container's own seconds at `modal billing rates`; the idle tail before scale-down is not in them):

| | Cold (both containers start from nothing) | Warm |
|---|---|---|
| Wall time per prediction | 37.7 s | 4.3 to 5.9 s |
| GPU seconds (Qwen on an A10G, Q-Align on an A10) | 47 | 3.9 |
| Estimated cost | $0.014 | $0.0012 |

The Modal bill for the whole task (billing report, this app, today): **$0.54**, which includes the weight download, two attempts that ran out of GPU memory, and the test and probe calls.

## Reading it honestly

**The Qwen zero-shot numbers are not informative.** It says 70% hook hold for all three videos, and 65% watch rate for two of the three. The one that differs (85 on the saree reel) is the only sign of response to the content.

A prompt-sensitivity check on the same frames (one extra run of the Qwen call per row, ad hoc, same Modal app; ECR / NAWP):

| Change to the input | Saree reel (ECR / NAWP) | Video-86506 | Video-291 |
|---|---|---|---|
| Baseline input | 70 / 85 | 70 / 65 | 70 / 65 |
| Niche label "Gaming" instead of the real one | **20 / 45** | 70 / 65 | 70 / 65 |
| Frames in reverse order | 70 / 85 | 70 / 65 | 70 / 65 |
| Told the video is 15 s long | 80 / 75 | 70 / 65 | **40 / 60** |
| Different frame times (8 s, 14 s... instead of 6 s, 12 s...) | 90 / 75 | not run | not run |

So the output is driven mostly by the words in the prompt (the niche, the stated duration) and not by what is in the frames. Reversing the frames changes nothing, while calling a saree reel "Gaming" moves it from 70% to 20%, and shifting the frame times moved the same video from 70% to 90% hook hold. That is what a language model's default guess looks like, not a measurement of watch behaviour. The numbers look more precise than they are, and the old RunPod worker (same model, same prompt) had the same weakness.

**Direction against the parameter scores.** Only two videos have saved scores (and those scores are Muse Spark's own judgement, not measured results; they were produced while the pipeline was still feeding it the constant 45 / 55):

- Retention: saree 62 vs 60. Qwen NAWP 85 vs 65 agrees on direction, Heuristic NAWP 39.6 vs 41.0 does not (a difference of 1.4 points, which is nothing).
- Hook: saree 66 vs 52. Qwen ECR is a tie (70 vs 70), so it says nothing. The heuristic points the wrong way (52.5 vs 53.8), again by a tiny margin.
- Visual quality: saree 76 vs 74. Q-Align quality 4.32 vs 3.99 and aesthetics 2.69 vs 2.18 agree.

Two videos is too few to call any of this a result. It only shows that nothing is wildly backwards.

**Q-Align is the only part that moves with the pictures**, and it is consistent: same value on repeats, spread of 3.7 to 4.3 across three different videos, and it agrees in direction with the independent visual-quality score. But it says "Good" for all three, and 3.7 to 4.3 on a 5-point scale is a narrow spread, so treat it as a coarse picture-quality check. It does not say anything about whether people will keep watching.

**The heuristic** (vision score plus audio hook energy, mapped to a fixed range) barely varies either (52.5 vs 53.8). In the actual pipeline it is worse than that: the pipeline calls it with no signals at all, so the baseline returned exactly 45 and 55 for every video (every saved local result shows this). That is why the heuristic with no signals now answers "unavailable".

## What this means for the product

- The card is labelled for what it is ("Rough estimate from frame content, not measured watch data") and shows "Prediction unavailable" with no bars when the call fails.
- Because the guess is nearly constant and the baseline scoring prompt says the Hook and Retention scores "MUST align" with ECR and NAWP, the zero-shot numbers are **kept out of the scoring prompt by default** (`ENGAGEMENT_ZEROSHOT_EVIDENCE=1` turns them back on, as a rough risk hint only). Q-Align is also off the scoring path unless `ENGAGEMENT_QALIGN_EVIDENCE=1`.
- To get a prediction that means something, the trained LMM-EVQA weights are needed (an owner of the Baidu shares could host them), or a model fitted to real watch data. Neither was in scope, and nothing here was trained.
- Qwen runs at about 250 x 450 pixels per frame (about 144 visual tokens each) because a 24 GB card ran out of memory at higher resolution. The old RunPod worker ran at the model's default (much larger) resolution on a bigger card. The prompt-sensitivity result above suggests resolution is not the limit, but it was not tested.
