# Model swap inventory

Built during the Meta Global AI Developer Hackathon (new work). Baseline = commit tagged `pre-hackathon-baseline`.

Goal: the original Prysm analysis pipeline (same prompts, same scoring and weights, same output) runs on Meta models
instead of Claude, and the existing vendor calls keep working. This page lists every model or vendor call found in the
baseline backend and what happens to it. `AI_PROVIDER=meta` switches the Claude call sites; with `AI_PROVIDER` unset
the baseline behaves exactly as before.

## Claude call sites (all replaced by Muse Spark 1.3 through one shim)

Every one of these used `anthropic.AsyncAnthropic` and now gets `app/studio/meta_llm_shim.py` when `AI_PROVIDER=meta`
(one import line per file, `from app.studio.llm_switch import anthropic`). The `anthropic` package is not imported at all in
that mode. The shim is documented in its header: block translation, reasoning-token headroom, image cap, error mapping, cost.

| Original | What it does | Replacement | File |
|---|---|---|---|
| Claude Sonnet, `analyse_frames_with_claude` | Looks at the extracted frames in order and describes the story, hook and per-frame facts (JSON) | muse-spark-1.3 (vision, up to 18 frames in one request) | `app/services/prysm_analyse_service.py` |
| Claude Sonnet, scoring call A | Parameters 1-5 of the 10-parameter breakdown, summary, caption, hashtags | muse-spark-1.3 | same file, in `run_full_analysis` |
| Claude Sonnet, scoring call B | Parameters 6-10 | muse-spark-1.3 | same file |
| `_claude_with_retry` | Backoff on rate limit / timeout / overloaded | unchanged; the shim raises the same exception classes | same file |
| Claude Sonnet, ARIA chat (`/prysm/chat`, streamed) | Creative strategist grounded in the analysis | muse-spark-1.3 streamed, same SSE format | `app/routers/prysm.py` |
| Claude Sonnet, `chatbot_service` (general chat sessions) | Memory-aware chat, streaming | muse-spark-1.3 | `app/services/chatbot_service.py` |
| Claude Sonnet, `ai_service._chat` | Shared helper for the content strategist, timeline, competitor, viral pattern, creator scraper and clip generation services | muse-spark-1.3 | `app/services/ai_service.py` (callers unchanged) |
| Claude Opus, `vision_service._call_claude_vision` | Older per-frame vision pass used by the intelligence workers | muse-spark-1.3 | `app/services/vision_service.py` |

No other Anthropic usage exists in the backend (searched for `anthropic`, `claude`, `messages.create`, `messages.stream`).

## Other vendors

| Original | What it does | Decision | File |
|---|---|---|---|
| Groq Whisper (`whisper-large-v3-turbo`) | Speech transcript, language, segments | Keep. Phase 2 adds Meta Voice Transcribe as a second voter in a consensus transcript (existing model, new integration) | `prysm_analyse_service.transcribe_audio`, `transcription_service.py` |
| Gemini 2.5 Pro audio listener | Music, mood, voice quality, song guess from the audio | Keep. Phase 2 adds Muse Spark 1.2 as a second audio listener next to it | `prysm_analyse_service.analyse_audio_with_gemini` |
| Gemini 2.5 Flash + Google Search (trends) | Live trend block per niche | Keep; model name verified against the Gemini model list (still served). Note: the key available during the build answered HTTP 402 "prepayment credits are depleted", so live calls fail soft | `app/services/trend_service.py` |
| Apify / yt-dlp link download | Fetches the reel behind a pasted Instagram/YouTube link | Keep as is (original route). No RapidAPI added | `prysm_analyse_service.download_video`, `apify_service.py`, `video_service.py` |
| ACRCloud | Music fingerprint, overrides Gemini's song guess | Keep | `acrcloud_service.py` |
| RunPod engagement (LMM-EVQA) | Predicted watch percentage and 5 s hold | Fails soft to the baseline's own heuristic when its key is missing; Phase 2 fills the slot with the Modal signals | `engagement_service.py` |
| `embedding_service` (DINOv2, V-JEPA 2, CLAP, CLIP / LanguageBind, Demucs, local torch) | Per-video embeddings saved to pgvector columns | Heavy local models are not installed here (`requirements-ml.txt` skipped); the import failure is caught, the step is skipped with one log line. Phase 2 uses the same open models on Modal | `embedding_service.py` |
| Cloudflare R2 (`r2_service`, `research_storage_service`) | Edited-video storage; research artefacts | Fails soft when keys are missing; the edited-video download route answers 503 with a clear message. `research_storage_service` is only used by research scripts | `r2_service.py` |
| `auto_edit_service` | FFmpeg auto-edit after an analysis, uploaded to R2 | Disabled in this build with `AUTO_EDIT_ENABLED=0` (a new auto-edit is a later hackathon feature) | `app/routers/prysm.py` |
| Pinecone / pgvector | Memory vectors | pgvector (local Postgres); Pinecone only when its key is set | `memory_service.py` |
| SAM 3.1 (Meta) | People and objects in the video | New input (Phase 2) | `app/studio/meta_client.py` |
| Meta Voice Transcribe | Second speech recogniser | New input (Phase 2) | `app/studio/meta_client.py` |

## Findings from the live API (measured during the swap)

- **Images per request:** muse-spark-1.3 accepted 1, 6, 12, 18, 24, 32, 48, 72, 100 and 150 images in one request with no
  error. Counting accuracy fell above about 48 images (asked for a count, it answered 54 for 72). The baseline sends at most
  18 frames, so nothing is split; the shim caps a request at `META_MAX_IMAGES_PER_REQUEST` (default 48) and thins frames
  evenly beyond it, logging a warning.
- **Reasoning tokens:** Spark spends hidden reasoning tokens inside `max_tokens` (a 30-token budget came back empty). The
  shim adds `META_REASONING_HEADROOM` (default 6000) and retries once with double the room on an empty reply.
- **System role, multi-turn and streaming** all work in the OpenAI-compatible format.
