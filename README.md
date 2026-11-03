# Prysm

Prysm analyses a short-form video (an Instagram Reel) and tells the creator, in plain language, what to fix before they post. It scores the video on 10 parameters (hook, retention, shareability, audio, trend alignment, visual quality, on-screen text, save potential, replay value, call to action) and gives each one a problem, a fix and numbered steps. A creator can then ask ARIA, a chat assistant grounded in that analysis, how to improve it.

Built for the Meta Global AI Developer Hackathon: the existing Prysm analysis engine now runs on Meta models (Muse Spark, Voice Transcribe, SAM 3.1) instead of Claude. What is baseline and what is new: [HACKATHON.md](HACKATHON.md). Model by model: [docs/model-swap.md](docs/model-swap.md).

## Architecture in 10 lines

1. `web/` (React + Vite) uploads a video or a reel link to `backend/` (FastAPI) and polls for real stage progress.
2. A job goes onto a Redis queue; a worker runs `run_full_analysis` in `app/services/prysm_analyse_service.py` (the original pipeline, unchanged prompts).
3. FFmpeg extracts up to 18 frames and the audio; links are downloaded with Apify (yt-dlp fallback).
4. In parallel: Muse Spark 1.3 watches the frames, Groq Whisper transcribes, Gemini listens, ACRCloud identifies the music, trends are fetched.
5. Extra evidence (each part has a flag): Meta Voice Transcribe + Whisper consensus, Muse Spark 1.2 as second listener, SAM 3.1 subjects, signal models on Modal (DINOv2, V-JEPA 2, LanguageBind, EnCodec, CLAP, Demucs).
6. Two Muse Spark 1.3 scoring calls produce the 10 parameters; plain code computes the weighted overall score.
7. Every Claude call goes through `app/studio/meta_llm_shim.py` (an `anthropic`-compatible shim) when `AI_PROVIDER=meta`; the `anthropic` package is not imported.
8. Results are saved to Postgres, cached in Redis, shareable by link; ARIA streams from Muse Spark.
9. A hidden safety net (daily spend cap `STUDIO_DAILY_SPEND_CAP_USD`, default 10 USD; per-user limits) protects vendor spend; there are no plans or credits in demo mode.
10. Heavy GPU models run on Modal; nothing heavy runs in the backend.

## Run it locally

Requirements: Python 3.11, Node 20+, ffmpeg and ffprobe, PostgreSQL with the `pgvector` extension, Redis.

```bash
# Backend
cd backend
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-studio.txt   # requirements-ml.txt is not needed (GPU models run on Modal)
cp .env.example .env        # fill in the keys (names only in the example); see "Environment" below
createdb prysm && psql prysm -c "CREATE EXTENSION IF NOT EXISTS vector;"   # or any Postgres you point DATABASE_URL at
.venv/bin/python -m uvicorn app.main:app --port 8000

# Website (second terminal)
cd web
npm install
cp .env.example .env.local  # set VITE_STUDIO_MODE=1 and VITE_API_URL=http://localhost:8000
npm run dev
```

Register on the site (with `DEMO_AUTO_VERIFY=1` no email is needed), upload a reel of up to 5 minutes (extra evidence runs on reels up to 90 seconds), pick one to three niches, add a one-line description and watch the real progress until the 10-parameter result appears.

Tests: `cd backend && .venv/bin/python -m pytest app` (offline, no network, database or Redis needed). Website build: `cd web && npm run build`.

## Environment (names only)

Backend `backend/.env` (see `backend/.env.example`): `AI_PROVIDER` (`meta`), `META_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `ACRCLOUD_HOST`, `ACRCLOUD_ACCESS_KEY`, `ACRCLOUD_ACCESS_SECRET`, `APIFY_API_TOKEN` (reel links), `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` (or `modal token new`), `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `JWT_SECRET`, `ALLOWED_ORIGINS`, `FRONTEND_URL`, and the demo switches `STUDIO_UNLIMITED`, `DEMO_AUTO_VERIFY`, `STUDIO_ALLOW_LINKS`, `AUTO_EDIT_ENABLED`. `ANTHROPIC_API_KEY` is not needed.

Website `web/.env.local` (see `web/.env.example`): `VITE_STUDIO_MODE`, `VITE_STUDIO_ALLOW_LINKS`, `VITE_API_URL`, `VITE_UPLOAD_URL`, `VITE_SUPPORT_EMAIL`, optional `VITE_SOCIAL_*`.

Features whose keys are unset stay off and fail soft: email, payments, Google sign-in, Instagram connect, RunPod engagement, R2 storage, the private embedding models.

## Deploying

New Railway project (Postgres + Redis + backend) and a new Vercel project for the website: [DEPLOY.md](DEPLOY.md).
