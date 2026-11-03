# LMM-EVQA and Q-Align: feasibility check

Built during the Meta Global AI Developer Hackathon (new work). Checked on 2026-11-04 against the GitHub repos, the paper (arXiv 2508.02516), the Hugging Face API and the challenge page. Nothing here was assumed from the old RunPod comments.

## Decision: PATH B (Qwen2.5-VL zero-shot port, clearly labelled). PATH A is not possible.

**Why:** the trained LMM-EVQA checkpoints are not publicly downloadable in a usable way. The README's "Pre-trained Models" section links two Baidu Yun shares (VideoLLaMA2 and Qwen2.5-VL, each behind an extraction code). They need a Baidu account and Baidu's client; there is no Hugging Face repo, Google Drive link or release asset. The Hugging Face search for "EVQA" and "SnapUGC" returns nothing from these authors. The paper page mentions code only, not weights. So there are no weights the Modal app could fetch, and we do not try to get them through a consumer file-sharing account.

If someone later gets the weights by hand, `evqa_predict` is not built, but the Modal app is laid out so it can be added (see `docs/modal-engagement.md`, "Adding the real model").

## a) LMM-EVQA

| Question | Finding |
|---|---|
| Weights public? | Baidu Yun only (extraction codes in the README). Not on Hugging Face or Drive. Not scriptable. |
| Code licence | Apache 2.0 (repo badge and LICENSE). |
| Base models | VideoLLaMA2-7B audio-visual (the better one, the authors attribute that to the audio) and Qwen2.5-VL-7B (visual and language only). Winning submission: an ensemble of four models (three VideoLLaMA2 variants, one Qwen2.5-VL). |
| Inputs | One video file plus optional title and description text. VideoLLaMA2 pulls the audio from the video itself; `--modal-type av`. Training used the first N frames (`--num_frames`). Qwen2.5-VL: frames and text only. |
| Output | The README gives no output schema. The task is a single engagement score; the repo is scored by SROCC and PLCC. The README does not show ECR or NAWP as two named outputs. **The old RunPod card's "ECR" and "NAWP" labels were therefore never something the real LMM-EVQA code printed**, they came from the RunPod prompt. |
| GPU memory | Not stated. A 7B model in bf16 needs about 16 to 20 GB for the weights; the baseline docstring says 48 GB for the ensemble. Unverified. |
| Weights licence | Not stated by the authors. The fine-tunes inherit the base models' terms. |
| Base-model licences | Qwen2.5-VL-7B-Instruct: Apache 2.0. VideoLLaMA2: Apache 2.0 code; its language and vision/audio parts are built on other models whose terms were not re-checked here. |
| Training data | SnapUGC (Snapchat), released for the challenge. Challenge data is normally research use only. This matters if anyone retrains; we do not. |
| Commercial use | Code: yes (Apache 2.0). Weights: unknown, since they are not accessible. |

What the real model is, for the card's honesty: a model fine-tuned on Snapchat watch data. What PATH B is: the stock Qwen2.5-VL-7B-Instruct asked to guess two numbers. **PATH B is not that model and has no training on watch data.** It is a language model's guess from the frames, so the card says "Rough estimate from frame content, not measured watch data".

## b) Q-Align (q-future/one-align)

| Question | Finding |
|---|---|
| Inference | `AutoModelForCausalLM.from_pretrained("q-future/one-align", trust_remote_code=True, torch_dtype=float16)` then `model.score([...], task_="quality" or "aesthetics", input_="image" or "video")`. Output is on a **1 to 5 scale, higher is better**. The repo wrappers are `QAlignScorer`, `QAlignAestheticScorer` and `QAlignVideoScorer` (video = a list of frames loaded with `load_video`); the AutoModel path needs no repo install. |
| Weights | `q-future/one-align`, Hugging Face, tagged `license:mit`. 2 `.bin` shards, 16.4 GB (fp32 on disk). |
| Code licence | The repo has `LICENSE` and `S-Lab-LICENSE`. **Both files are the same text: S-Lab License 1.0, which permits redistribution and use for non-commercial purposes only; commercial use requires contacting the authors.** The Hugging Face model card says MIT. The two disagree, so treat the code and weights as non-commercial until the authors say otherwise. |
| Base model | mPLUG-Owl2, itself Llama-2 based (Llama 2 community licence applies on top). |
| GPU memory | Not stated. About 8 to 9 GB in fp16, so a 24 GB GPU is plenty. |
| Dependencies | Needs `transformers==4.36.1` (v1.0.x of the code no longer works), `trust_remote_code`, `attn_implementation="eager"`. Needs its own pinned image. |
| Video | Uses a handful of frames (the wrapper takes a list of PIL frames); we send the baseline frame set, up to 8 frames, as the video input. |

## c) Risks and flags

- **Q-Align is non-commercial by licence.** Fine for hackathon evaluation; flagged in the README card text and in HACKATHON.md. Not for a paid product without the authors' permission.
- **Q-Align is a picture-quality signal.** It was trained on image and video quality ratings, not on watch behaviour. We show it as a band and a score, never as a percentage, and it only reaches scoring behind a flag (default off).
- **PATH B numbers are a language model's guess.** The old RunPod worker silently returned 40 and 55 on any error. Ours returns `available=false` with a reason instead.
