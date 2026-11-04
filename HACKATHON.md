# Hackathon work log

Built during the Meta Global AI Developer Hackathon.

## What is baseline and what is new

- **Baseline** is the commit tagged `pre-hackathon-baseline`: the startup's existing Prysm codebase (backend and website), including its analysis pipeline, its prompts, its niche weights and its scoring logic. See exactly what changed with `git diff pre-hackathon-baseline..HEAD --stat`.
- **New work (this hackathon):** making that original analysis engine run on Meta models instead of Claude, and the layer that does it.
  - `backend/app/studio/` — the Meta layer: Muse Spark shim (`meta_llm_shim.py`, `llm_switch.py`), Meta API client (`meta_client.py`), tolerant JSON parsing (`json_tolerant.py`), real progress (`progress.py`), the hidden spend safety net (`safety.py`), and the extra evidence of Phase 2 (`evidence.py` plus the stage modules, signal models, consensus and live-trend code it reuses).
  - `docs/model-swap.md` — every model call found in the baseline and what replaced it.
  - A small hackathon shell (demo mode, safety net, real progress, hosting files) listed below.
- **Unchanged on purpose:** the analysis prompts, the 10 parameters, the niche weights and the scoring and overall-score logic. The same prompt text is sent; Muse Spark answers it.

Two clarifications on what is and is not new:

- The **research data** used before the hackathon to derive the niche weights (scraped reels and their metrics) is pre-hackathon work and is **not included** in this repo. The weights themselves are in the baseline's `backend/app/core/config.py` and were not changed.
- Secrets, cookies and research data are never committed (`.env` files, `samples/` and the research folders are git-ignored).

## How the original engine runs on Meta models

`AI_PROVIDER=meta` makes every Claude call site import `app/studio/meta_llm_shim.py`, which looks like the `anthropic` SDK (same `messages.create` and `messages.stream`, same exceptions) and is served by Muse Spark 1.3 on the Meta API. The `anthropic` package is not imported and `ANTHROPIC_API_KEY` is not needed. With `AI_PROVIDER` unset the code behaves exactly as the baseline.

Phase 1 is that plain swap. Phase 2 feeds extra evidence into the inputs the original prompts already read (transcript, audio facts, scene facts, embeddings, an extra-signals block). Each part of Phase 2 has its own flag (`EVIDENCE_VOICE_CONSENSUS`, `EVIDENCE_SPARK_AUDIO`, `EVIDENCE_SUBJECTS`, `EVIDENCE_SIGNALS`, `EVIDENCE_MUSIC_ID`) and fails soft.

## Models used

"Existing model / new integration" means the model existed before the hackathon and is used unmodified; what is new is our integration code.

| Model / service | Origin | What it does in Prysm |
|---|---|---|
| **Muse Spark 1.3** `muse-spark-1.3` | Meta model | Watches the extracted frames (vision), writes the 10-parameter scoring (two calls), the summary, caption and hashtags, and powers ARIA chat |
| **Muse Spark 1.2** `muse-spark-1.2` | Meta model | Second audio listener next to Gemini (and the fallback when Gemini is unavailable) |
| **Meta Voice Transcribe** `muse-voice-transcribe-1.0` | Meta model | Second speech recogniser; votes with Groq Whisper in a consensus transcript |
| **SAM 3.1** `sam-3.1` | Meta model | Tracks people and faces on screen: screen time, first appearance, gaps |
| **Groq Whisper** `whisper-large-v3-turbo` | existing model / new integration | Speech transcript with timestamps (the baseline's own step, now one voter in a consensus) |
| **Gemini 2.5 Pro** | existing model / new integration | Audio listener with synced frames (the baseline's own step; kept) |
| **Gemini 2.5 Flash + Google Search** | existing model / new integration | Live trend block (the baseline's own step; kept) |
| **ACRCloud** | existing service / new integration | Music recognition: the baseline's call, plus a second pass over 12 s windows and the Demucs "rest" stem |
| **DINOv2** `facebook/dinov2-base` | existing model / new integration | Visual embedding and frame-to-frame visual change (Modal GPU) |
| **V-JEPA 2** `facebook/vjepa2-vitl-fpc64-256` | existing model / new integration | Motion / clip embedding (Modal GPU) |
| **LanguageBind** `LanguageBind/LanguageBind_Video_FT`, `LanguageBind_Audio_FT` | existing model / new integration | Joint embedding and how well the audio fits the picture (Modal GPU; CLIP stands in if the video tower cannot run and the panel says so) |
| **EnCodec** `facebook/encodec_24khz` | existing model / new integration | Audio-complexity signal (Modal GPU) |
| **CLAP** `laion/clap-htsat-fused` | existing model / new integration | Audio embedding (Modal GPU) |
| **Demucs** `htdemucs` | existing model / new integration | Separates voice from music; voice-to-music loudness ratio; its "rest" stem feeds music ID (Modal GPU) |
| **Qwen2.5-VL-7B-Instruct** `Qwen/Qwen2.5-VL-7B-Instruct` | existing model / new integration | "Predicted Viewer Behaviour": asked for a hook-hold and watch-rate guess from up to 8 frames (stock model, **not** trained on watch data; Modal GPU) |
| **Q-Align** `q-future/one-align` | existing model / new integration | "Visual quality signal": picture quality (video) and aesthetics, 1 to 5 (Modal GPU) |

The results page has a "Models used" panel that lists these for each analysis, with the label above and "not available for this video" when a part did not run.

**Q-Align is non-commercial.** Its code carries the S-Lab licence (`LICENSE` and `S-Lab-LICENSE` are the same text: non-commercial use only, otherwise contact the authors) while the Hugging Face card says MIT; treat it as non-commercial until the authors say otherwise (`docs/evqa-feasibility.md`). It is here for hackathon evaluation.

Licence check (made earlier against each model card and repo, before Q-Align was added): no non-commercial licence was found. EnCodec's weights and CLIP ViT-L/14 declare no licence on their hub cards (the code is MIT); confirm before commercial use.

## Modified baseline files

Every baseline file changed, and why. Everything except the model call sites is behind a hackathon flag whose default keeps the baseline behaviour, or removes a hard-coded identifier of the original deployment.

| File | Change |
|---|---|
| `backend/app/services/prysm_analyse_service.py` | Claude import goes through `llm_switch` (Muse Spark when `AI_PROVIDER=meta`); Spark and Wonder prices and `_resp_model` in the cost tracker; tolerant JSON extraction plus one repair retry (meta mode only); `progress_cb` stage reporting; the extra-evidence task and the places its results are merged into existing inputs (`whisper_data`, `gemini_audio`, `extra_signals`, embeddings); `modelsUsed` in the result. **Overall score and the after-fixes projection computed over the six scored parameters with their weights normalised to 1 (`app/studio/score_math.py`; see "Changes to baseline behaviour").** New optional `style_addendum` parameter of `build_scoring_prompts` (empty by default: the prompt text is byte-for-byte the baseline's), `DEBUG_SAVE_INPUTS` hook, cut-off-text log warning, per-analysis `_cost.breakdown`. **Prompt text and the 10 parameters are untouched.** |
| `backend/app/services/engagement_service.py` | `ENGAGEMENT_BACKEND` dispatch (`modal` / `runpod` / `heuristic`), a Modal path that returns `available=false` with a reason on any failure, an `available` flag and a `qalign` key on every result, the context text for the sources `evqa` and `qwen_zero_shot` (a rough guess: percentages are not to be quoted), the zero-shot guess stays out of the scoring prompt unless `ENGAGEMENT_ZEROSHOT_EVIDENCE=1` (it barely moves with the frames), Q-Align as scoring evidence only behind `ENGAGEMENT_QALIGN_EVIDENCE`. The heuristic with no signals now answers `available=false` instead of the invented 45 / 55 (the pipeline calls it with no signals, so every local result used to show exactly 45 / 55). The RunPod path is unchanged. |
| `backend/app/services/prysm_analyse_service.py` (second change) | Two lines: the Modal cost estimate of the engagement call goes into the cost breakdown. |
| `backend/app/services/ai_service.py`, `chatbot_service.py`, `vision_service.py` | One import line: the `anthropic` package, or the Muse Spark shim when `AI_PROVIDER=meta`. |
| `backend/app/routers/prysm.py` | The cost breakdown is removed from the analysis payloads (`/dashboard/{id}`, public `/shared/{id}`) and served only by a new `/dashboard/{id}/cost` for the founder or local testing; chat client and ARIA through the same import switch; hidden safety net (daily cap, per-user concurrency and hourly limit, global slot), `progress` in the status response, unlimited mode (no credit gate or deductions, top plan reported), link flag, auto-edit flag (`AUTO_EDIT_ENABLED`), 503 message when R2 is not configured, friendly in-chat message if Spark fails. |
| `backend/app/routers/auth.py`, `billing.py`, `creator.py` | Demo auto-verify, sign-up limit per IP, unlimited-mode plan/credits view, Razorpay answers 503 when unconfigured. |
| `backend/app/routers/admin.py` | Share URL base comes from `FRONTEND_URL` instead of a hard-coded production site. |
| `backend/app/core/config.py` | New settings (`AI_PROVIDER`, `AUTO_EDIT_ENABLED`, `COACH_STYLE`, `DEBUG_SAVE_INPUTS`, `STUDIO_*` incl. `STUDIO_EXPOSE_COST`, `DEMO_AUTO_VERIFY`, `CORS_ORIGINS`, `TRUSTED_HOSTS`, `CSP_CONNECT_SRC`, `PROMOTE_EMAILS`, `META_MODE`); `extra=ignore`; production URLs and emails removed from defaults. |
| `backend/app/core/middleware.py` | Rate limiter: the analysis status poll is exempt from the global per-IP limit (it keeps its own per-user limit of 120 a minute) and every window is fixed from its first request instead of being pushed back by each request (see "Changes to baseline behaviour"). |
| `backend/app/main.py` | `DEV_BAKEOFF=1` (local only) mounts the bake-off review endpoints; promoted emails, CORS origins, trusted hosts and CSP read from settings; Razorpay CSP entries only when its key is set. |
| `backend/app/services/r2_service.py` | Fails soft: logs once when R2 keys are missing. |
| `backend/app/services/email_service.py` | Email footer uses `FRONTEND_URL`; production social links removed. |
| `backend/Dockerfile`, `backend/.env.example` | Installs `requirements-studio.txt`; names of every new variable. |
| `web/vite.config.ts` | `VITE_PORT` and `VITE_PROXY_TARGET` so a second local stack can run next to the default one. |
| `web/index.html` | Production og:url and static Razorpay script tag removed. |
| `web/src/lib/api.ts`, `plans.ts`, `razorpay.ts` | No hard-coded production backend URL; plan checks pass in demo mode; Razorpay loads on demand. |
| `web/src/App.tsx`, `components/{FAQ,FeatureDemos,Footer,Hero,HowItWorks,Navbar,PrismProcessing}.tsx` | Demo mode (`VITE_STUDIO_MODE=1`): no pricing, credits or upgrade copy; contact and social links from `VITE_*`; real stage progress on the processing screen. |
| `web/src/pages/{Analyse,AnalyseLink,AnalyseUpload,Chat,Cookies,Dashboard,Index,Login,Privacy,Register,Settings,Terms}.tsx` | Same demo-mode changes; contact email from `VITE_SUPPORT_EMAIL`. |
| `web/src/pages/Results.tsx` | Free-plan locking off in demo mode; Models used panel; the cost panel (rendered only with `VITE_SHOW_COST_PANEL=1` or for the founder); the Predicted Viewer Behaviour card for the new sources (`ViewerBehaviourCard`) next to the untouched RunPod card, and the `engagementPrediction` type widened (`available`, `reason`, `qalign`, nullable numbers). |
| `backend/app/routers/auth.py` (second change) | The three Instagram routes keep their paths but call `app/studio/instagram` (two scopes instead of five, state checked before the code is exchanged, access-type token check, refresh, Disconnect deletes every derived row). |
| `backend/app/main.py` (second change) | Mounts the new `/instagram` router (two lines). |
| `backend/app/routers/prysm.py` (second change) | Loads the creator's DNA addendum before the analysis and saves the "compare to your reels" block after it (both skipped, and fail soft, for users without a DNA); the personalised sentences are grounded in the imported reels (rewritten once or dropped, and removed entirely if the check itself fails). |
| `backend/app/services/prysm_analyse_service.py` (third change) | One new optional parameter `creator_dna_addendum` (default empty: prompts unchanged), appended after the style addendum. |
| `backend/.env.example` | Names of the Instagram and Creator DNA variables, and `TOKEN_ENCRYPTION_KEY`. |
| `backend/app/models/user.py` (second change) | One line: `users.instagram_access_token` now uses the `EncryptedToken` column type (Fernet; same column, same name, plain values still readable). |
| `backend/app/main.py` (third change) | Two lines at the start of the lifespan: `tokencrypt.startup_check()`, which makes a production server refuse to start without a valid `TOKEN_ENCRYPTION_KEY`. |
| `web/src/pages/Results.tsx` (third change) | The viewer-behaviour card also renders when the prediction came back empty (no `source`), so the measured numbers still show. |
| `backend/requirements.txt` | `cryptography>=42` named explicitly (it was already installed through `python-jose[cryptography]`). |
| `web/src/App.tsx`, `web/src/pages/Settings.tsx`, `web/src/pages/Results.tsx` | `/creator-dna` route; the Settings Instagram block links to it (was localhost-only, five scopes); Results renders the measured viewer-behaviour card and the comparison card. |
| `web/vite.config.ts`, `web/vercel.json`, `web/netlify.toml`, `web/public/_redirects` | `/instagram` proxy entries. |

New files outside `backend/app/studio/`: `backend/.dockerignore`, `backend/pytest.ini`, `backend/requirements-studio.txt`, `web/.env.example`, `web/scripts/set_backend_url.sh`, `web/src/lib/config.ts`, `web/src/studio/progress.ts`, `web/src/studio/CostPanel.tsx`, `web/src/studio/CreatorDna.tsx`, `web/src/studio/CreatorDnaCard.tsx`, `backend/alembic/versions/n4o5p6q7r8s9_add_instagram_creator_dna_tables.py`, `docs/instagram-login.md`, `scripts/style_stats.py`, `scripts/rescore.py`, `scripts/bakeoff.py`, `scripts/meta_cost_check.md`, `docs/model-swap.md`, `docs/swap-check.md`, `docs/style-stats.md`, `docs/swap-defects.md`, `docs/bakeoff*.md`, `README.md`, `DEPLOY.md`, this file.

New for Predicted Viewer Behaviour (branch `feature/modal-evqa`): `backend/app/studio/engagement_modal_app.py` (the Modal app `prysm-hackathon-engagement`), `backend/app/studio/engagement_modal.py` (the backend client), `backend/app/studio/tests/test_engagement.py`, `web/src/studio/ViewerBehaviourCard.tsx`, `scripts/engagement_warmup.py`, `scripts/engagement_bakeoff.py`, `docs/evqa-feasibility.md`, `docs/modal-engagement.md`, `docs/engagement-bakeoff.md`.

New in `backend/app/studio/` since the swap: `score_math.py` (overall score), `quality.py` (cut-off text and style checks), `coach_style.md` + `coach_style.py` (the optional style addendum), `rescore.py` (replay of the scoring calls from saved inputs), `cost_breakdown.py` and `cost_access.py` (the cost breakdown and who may see it), and per-group model/headroom/effort switches in `meta_llm_shim.py`.

## Demo shell and separation from production

- `STUDIO_UNLIMITED=1`: no plans or credits (every payload reports the top plan and an unlimited balance); the real cost is still recorded on every analysis.
- Hidden budget safety net (Redis counters, expiring keys): daily spend cap, one running analysis per user, hourly limit per user, sign-ups per IP, global concurrency. Messages never mention credits.
- `DEMO_AUTO_VERIFY=1`: no email provider needed. `VITE_STUDIO_MODE=1`: no pricing or billing UI; the "Prysm" wordmark and title are unchanged.
- This repo is deployed on its own: own database, Redis, hosts, secrets and Modal workspace (see `DEPLOY.md`). No production value is used or copied.

## Changes to baseline behaviour (hackathon repo only)

These two are changes to how the baseline behaves, not just to where its model calls go. They are in this repo only; the pre-hackathon deployment is unchanged.

1. **Overall score.** The baseline niche weights add up to 1.0 over all ten parameters, but only six are scored, so the six scored weights add up to about 0.6 and the overall topped out near 61 (six parameters at 70 gave 42). The weights of the six scored parameters are now normalised to sum to 1, and the "after fixes" projection uses the same normalisation (`backend/app/studio/score_math.py`; the displayed weights are the normalised ones). The original production results use weights that sum to 100 over the six scored parameters and their stored overall is the weighted mean, so this restores the production behaviour (checked in `docs/style-stats.md`). Test: six parameters at 70 give an overall of 70 for every niche.
2. **Rate limiter.** The baseline's global per-IP limiter pushed the expiry of its counter back on every request, so a client that polled the job status every 3 s hit HTTP 429 after about 6 minutes and stayed blocked. The status poll is now exempt from the global limit (its own limit of 120 requests a minute per user stays) and every window is fixed from its first request. Test: 30 minutes of polling every 3 s is never limited; other routes still limit and recover.

Also fixed in the hackathon layer: replies that Spark's hidden reasoning cut off at the token limit (a step ended mid-word) are retried with more room, and the wasted attempt stays in the cost; `docs/swap-defects.md` has the cause and the answers about the repeated "before you post" endings.

## Options added for the model comparison

- `META_VISION_MODEL`, `META_SCORING_MODEL`, `META_CHAT_MODEL` (default `muse-spark-1.3`; Wonder is `muse-wonder-1.0`). Vision = calls with images, scoring = the text-only scoring/writing calls, chat = streamed ARIA. Headroom and reasoning effort can be set per group (`META_<GROUP>_HEADROOM`, `META_<GROUP>_REASONING_EFFORT`); scoring defaults to 10,000 tokens of headroom.
- `COACH_STYLE=1` appends `backend/app/studio/coach_style.md` to the two scoring prompts as an addendum (default off; the prompt text itself is never edited). The guide is a de-identified pattern guide learned from three production results (`docs/style-stats.md`).
- `DEBUG_SAVE_INPUTS=1` saves the exact scoring inputs once per analysis; `scripts/rescore.py` replays only the scoring calls with a chosen model and style; `scripts/bakeoff.py` runs the blind comparison (`docs/bakeoff.md`, key in `docs/bakeoff-key.md`, metrics in `docs/bakeoff-metrics.md`).

## Cost visibility and the spend cap

- Every analysis stores a cost breakdown (`_cost.breakdown`): Muse Spark / Wonder calls per group with tokens in and out including hidden reasoning tokens (Meta bills reasoning as output), the Spark 1.2 audio listener, Voice Transcribe, SAM 3.1, Gemini, Modal GPU seconds (an estimate: seconds x `MODAL_USD_PER_GPU_SECOND`), ACRCloud and Apify as per-call flags, and the total. One `[JobCost]` line per job goes to the backend log.
- It is shown on the results page only when `VITE_SHOW_COST_PANEL=1` (local) or the logged-in user is the founder (`VITE_FOUNDER_EMAIL`), and the backend only serves it to the founder (`FOUNDER_EMAIL`) or while `STUDIO_EXPOSE_COST=1` (local). It is never in the normal analysis payload or on a public share link.
- `scripts/meta_cost_check.md` explains how to compare one analysis with Meta's usage dashboard (balance before and after).
- **`STUDIO_DAILY_SPEND_CAP_USD`** (the hidden safety net): total real cost of all analyses and chat messages per UTC day before new analyses are refused. Code default **10**; the local test stack sets **25**. See `DEPLOY.md` for the other safety-net limits.

## Local bake-off review page

`/dev/bakeoff` on the local dev server shows the saved blind variants (X/Y/Z per video) through the real results page, with "My pick" per video (kept in localStorage), "Copy my picks" and a "Reveal key" button that then shows model, style, cost and time. It exists only with `VITE_DEV_BAKEOFF=1` on the Vite dev server (`import.meta.env.DEV`, so it is never in a production build) and `DEV_BAKEOFF=1` on the backend, whose endpoints (`backend/app/studio/dev_bakeoff.py`) refuse any non-local request and return no model, cost, time or token data until the reveal call. Both flags are off in the `.env.example` files. Baseline files touched: `backend/app/main.py` (flag-gated router), `backend/app/core/config.py` (the flag), `web/src/App.tsx` (flag-gated route), `web/src/pages/Results.tsx` (optional `injected` prop used only by that page).
## Predicted Viewer Behaviour on Modal (branch `feature/modal-evqa`)

The card that showed Hook Hold Rate (ECR) and Average Watch Rate (NAWP) used a RunPod worker. That worker never ran the trained LMM-EVQA: it loaded stock Qwen2.5-VL-7B and asked it for two numbers, and on any error it silently answered 40 and 55. The trained LMM-EVQA weights are only on Baidu Yun, so they cannot be used here (`docs/evqa-feasibility.md`).

- What replaces it: the same Qwen2.5-VL zero-shot guess served from Modal (no silent fallback; failure is "Prediction unavailable"), labelled "Rough estimate from frame content, not measured watch data", plus Q-Align as a separate "Visual quality signal" (a band and a 1 to 5 score, never a percentage, off the scoring path unless `ENGAGEMENT_QALIGN_EVIDENCE=1`). The bake-off found the zero-shot numbers nearly constant (hook hold 70 on all three videos), so they do not reach the scoring prompt by default.
- How it behaved: `docs/engagement-bakeoff.md`. How to deploy, warm up and check spend: `docs/modal-engagement.md`.

## Instagram connect and Creator DNA (branch `feature/instagram-login`)

A logged-in creator connects their own Instagram (Business or Creator) after signing in with email or Google, picks how many recent reels to import (5 to 30), and gets a Creator DNA: per-reel fingerprints plus a plain-language summary of what their strongest and weakest reels share, every claim citing reels. New analyses are then personalised with that DNA, and "Predicted Viewer Behaviour" shows the creator's measured watch-through, skip rate and reach instead of the near-constant zero-shot Qwen numbers. Details, limits, the Meta app checklist and costs are in `docs/instagram-login.md`. New code is in `backend/app/studio/instagram/`; fixture mode (`INSTAGRAM_FIXTURE=1`, localhost only) lets the whole flow run without Instagram. The new `ig_*` and `creator_dna` tables are in migration `n4o5p6q7r8s9`; no baseline table is altered. The only change to the Muse Spark shim is two optional per-call arguments (`reasoning_effort`, `headroom`).

Demo-readiness (same branch): import limits 1-30 capped by the reels the account has, breakdown only for 1-2 reels; a redesigned `/creator-dna` page in the app's own look (`web/src/studio/creator/`); plain-language numbers ("Reach 25% below your usual"); hook words (first 12 seconds through Voice Transcribe, `hook.py`) in the fingerprint, the DNA and the grounding check; "Did my fix work?" (`closeloop.py`, table `ig_fix_link`); a production startup check for `TOKEN_ENCRYPTION_KEY`. Migrations `p6q7r8s9t0u1`, `q7r8s9t0u1v2`, `r8s9t0u1v2w3`. Deployment checklist: `docs/instagram-login.md` section 7b.

Hardening (same branch): **grounding** (`grounding.py`: every claim and every personalised sentence about the creator's reels must cite imported reels and contain no invented detail such as a greeting, quote, number or sound; one repair call, then drop), **token encryption** at rest (`tokencrypt.py`, `TOKEN_ENCRYPTION_KEY`; migration `o5p6q7r8s9t0` also adds `ig_reel.analysis_level` and encrypts stored tokens), and **reels without a video link** shown as "not fully analysed" (thumbnail and caption only, no watch-through, no hook/format/pacing judgement). Details in `docs/instagram-login.md` section 5.
