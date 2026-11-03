# Deploying Prysm (new Railway project + new Vercel project)

This deploys the hackathon app on its own: a **new** Railway project for the backend, Postgres and Redis, and a **new** Vercel project for the website. It shares nothing with the startup's production. The backend runs the original Prysm analysis pipeline on Meta models (`AI_PROVIDER=meta`). Every value below is a name only; fill in your own secrets in the dashboards, never in the repo.

Order matters: Railway first (you need its URL), then Vercel (you need its URL), then back to Railway to allow the Vercel origin.

## Part 1: Railway (backend, Postgres, Redis)

1. **New project.** Railway dashboard, *New Project*, *Empty Project*. Name it `prysm-demo`. Use your own account or a team that has nothing to do with the startup's production.
2. **Postgres with pgvector.** The original code stores embeddings in pgvector columns, so use a Postgres that has the `vector` extension: in Railway add the *pgvector* Postgres template (*New*, *Template*, search "pgvector") instead of the plain PostgreSQL database. The migration and `scripts/init.sql` run `CREATE EXTENSION IF NOT EXISTS vector`.
3. **Redis.** *New*, *Database*, *Add Redis*.
4. **Backend service.** *New*, *GitHub Repo*, pick **this hackathon repo** (see "Never do" below), then open the service's *Settings*:
   - **Root Directory:** `backend`
   - **Builder:** Dockerfile (Railway finds `backend/Dockerfile`; it installs ffmpeg, ffprobe and the analysis pipeline's requirements)
   - **Start command:** leave empty to use the Dockerfile's, or set this lighter one (the Redis counters keep the real concurrency limit across processes, so two web workers are plenty):

         sh -c "python -m alembic upgrade head; uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2 --timeout-keep-alive 620 --h11-max-incomplete-event-size 524288"
   - **Healthcheck path:** `/health` (returns `{"status":"ok",...}`); healthcheck timeout 300 s, the first boot loads the analysis libraries.
   - **Resources:** at least 2 GB of memory (ffmpeg plus librosa while an analysis runs).
5. **Volume.** Not needed: the pipeline works in temporary folders and deletes them after each job.
6. **Variables.** Service, *Variables*: add everything in the table below. For `DATABASE_URL` and `REDIS_URL` use Railway references (`${{Postgres.DATABASE_URL}}`, `${{Redis.REDIS_URL}}`); the app converts `postgresql://` to the asyncpg driver itself.
7. **Modal (GPU signal models), once, from your own Modal workspace:**

       cd backend
       modal token new                                       # logs your own Modal account in locally
       modal deploy app/studio/signals/modal_app.py          # deploys the Modal app "prysm-studio-signals" (the app's code name; it is never shown to users)
       modal run app/studio/signals/modal_app.py::download_weights   # fills the weights volume once

   Then create an API token in the Modal dashboard (*Settings*, *API Tokens*) and put it in the Railway variables `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`.
8. **Public URL.** Service, *Settings*, *Networking*, *Generate Domain*. Copy it (call it `RAILWAY_URL`) and check `RAILWAY_URL/health`.

### Backend environment variables

Names only. **Generate** = create a fresh random value for this deployment (for example `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`). **Vendor** = a key from that vendor's dashboard, your own account.

| Variable | Value / source | Purpose |
|---|---|---|
| `AI_PROVIDER` | `meta` | Every Claude call is served by Muse Spark through the shim; `ANTHROPIC_API_KEY` is not needed |
| `STUDIO_ALLOW_LINKS` | `1` | Reel links use the original Apify route (needs `APIFY_API_TOKEN`). Set `0` for uploads only |
| `AUTO_EDIT_ENABLED` | `0` | The baseline auto-edit + R2 upload step stays off in this build |
| `STUDIO_UNLIMITED` | `1` | No plans, tiers or credits: every user gets the complete result; payloads report the top plan |
| `DEMO_AUTO_VERIFY` | `1` | New accounts are verified at once (no email provider needed) |
| `APP_ENV` | `production` | Turns off API docs, enables the production CSP and trusted-host check |
| `SECRET_KEY` | **Generate** | Application secret |
| `JWT_SECRET` | **Generate** | Signs login tokens |
| `DATABASE_URL` | Railway reference | Postgres connection |
| `REDIS_URL` | Railway reference | Job queue, progress, safety-net counters |
| `FRONTEND_URL` | the Vercel URL (step 3 below) | Website URL used in links and CORS |
| `ALLOWED_ORIGINS` | JSON list with the Vercel URL, for example `["https://YOUR-SITE.vercel.app"]` | Origins allowed by CORS (must be a JSON list) |
| `CORS_ORIGINS` | empty, or extra origins comma-separated | Optional extra CORS origins (a custom domain) |
| `TRUSTED_HOSTS` | the Railway host name, for example `YOUR-BACKEND.up.railway.app` | Hosts the API answers to in production (empty: no host check) |
| `CSP_CONNECT_SRC` | empty | Extra `connect-src` entries for the production CSP |
| `FOUNDER_EMAIL` | **empty** | Must stay empty: nobody is promoted or made admin by email |
| `PROMOTE_EMAILS` | **empty** | Must stay empty: no automatic plan promotion |
| `ADMIN_EMAILS` | empty | No extra admins |
| `DEBUG_TOKEN` | **Generate** (or leave empty) | Guards the `/debug/*` endpoints |
| `META_API_KEY` | Vendor (Meta) | Muse Spark, Voice Transcribe and SAM models |
| `GROQ_API_KEY` | Vendor (Groq) | Whisper transcription |
| `GEMINI_API_KEY` | Vendor (Google AI) | Audio listener and live trend search (the baseline's own steps); without it Muse Spark 1.2 listens and the trend block stays empty |
| `ACRCLOUD_HOST` | Vendor (ACRCloud) | Music recognition host |
| `ACRCLOUD_ACCESS_KEY` | Vendor (ACRCloud) | Music recognition |
| `ACRCLOUD_ACCESS_SECRET` | Vendor (ACRCloud) | Music recognition |
| `MODAL_TOKEN_ID` | Vendor (Modal) | Lets the backend call the GPU signal models |
| `MODAL_TOKEN_SECRET` | Vendor (Modal) | Same |
| `APIFY_API_TOKEN` | Vendor (Apify) | Downloads the reel behind a pasted link (original route) |
| `EVIDENCE_VOICE_CONSENSUS`, `EVIDENCE_SPARK_AUDIO`, `EVIDENCE_SUBJECTS`, `EVIDENCE_SIGNALS`, `EVIDENCE_MUSIC_ID` | each `1` (default) or `0` | Switches for the extra evidence parts (reels up to 90 s) |

**Hidden budget safety net** (always active when `AI_PROVIDER=meta`; the defaults are shown, set them only to change them):

| Variable | Default | Purpose |
|---|---|---|
| `STUDIO_DAILY_SPEND_CAP_USD` | `10` | Total real cost of finished analyses per UTC day; at the cap new analyses answer HTTP 429 ("Prysm's demo budget for today is used up. Please try again tomorrow.") |
| `STUDIO_USER_MAX_CONCURRENT` | `1` | Running analyses per user (HTTP 429 "You already have an analysis running. Please wait for it to finish.") |
| `STUDIO_USER_HOURLY_LIMIT` | `10` | Analyses a user can start per hour (HTTP 429, friendly message) |
| `STUDIO_REGISTER_PER_IP_HOURLY` | `5` | Sign-ups per IP per hour (HTTP 429) |
| `STUDIO_MAX_CONCURRENT` | `2` | Analyses running at once across all backend processes (a Redis counter; extra jobs queue and wait) |

**Leave unset on purpose** (the matching feature stays off): `ANTHROPIC_API_KEY` (not needed: ARIA runs on Muse Spark with `META_API_KEY`), `BREVO_API_KEY` (email), `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` (payments answer 503), `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`, `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, `INSTAGRAM_APP_ID` and `INSTAGRAM_APP_SECRET`, `RAPID_API_KEY` (not used), `RUNPOD_*`, `AWS_*`. Railway sets `PORT` itself.

## Part 2: Vercel (website)

1. **Point the rewrites at the backend, before deploying.** On your machine, in the repo:

       web/scripts/set_backend_url.sh https://YOUR-BACKEND.up.railway.app
       git add web/vercel.json web/netlify.toml web/public/_redirects
       git commit -m "deploy: backend URL for the Prysm site"

   The script replaces the placeholder host in `vercel.json`, `netlify.toml` and `public/_redirects` (the SPA fallback is left alone). Push this commit to the repo the Vercel project will build.
2. **New project.** Vercel dashboard, *Add New*, *Project*, import **this hackathon repo** into a **new** project (never an existing one). Settings:
   - **Root Directory:** `web`
   - **Framework Preset:** Vite
   - **Install Command:** `npm install` (default)
   - **Build Command:** `npm run build`
   - **Output Directory:** `dist`
3. **Environment variables** (Project, *Settings*, *Environment Variables*, Production):

   | Variable | Value | Purpose |
   |---|---|---|
   | `VITE_STUDIO_MODE` | `1` | Demo mode: the "Prysm" wordmark and page title are unchanged; no plans, credits, pricing, billing or upgrade prompts; everything unlocked; real stage progress |
   | `VITE_UPLOAD_URL` | `https://YOUR-BACKEND.up.railway.app` | Uploads go straight to the backend, past the host's request-size limit |
   | `VITE_STUDIO_ALLOW_LINKS` | `1` | Shows the reel-link option (pair with `STUDIO_ALLOW_LINKS=1` on the backend; unset hides it) |
   | `VITE_API_URL` | leave unset | Everything else goes through the rewrites in `vercel.json` |
   | `VITE_SUPPORT_EMAIL` | your contact address | Shown on the FAQ and legal pages |
   | `VITE_SOCIAL_X`, `VITE_SOCIAL_LINKEDIN`, `VITE_SOCIAL_INSTAGRAM` | optional | Footer links appear only when set |
   | `VITE_PUBLIC_POSTHOG_KEY`, `VITE_PUBLIC_POSTHOG_HOST`, `VITE_PUBLIC_CLARITY_ID` | leave unset | Analytics stay off |

4. **Deploy**, then copy the site URL (call it `VERCEL_URL`).
5. **Back to Railway:** set `FRONTEND_URL=VERCEL_URL`, `ALLOWED_ORIGINS=["VERCEL_URL"]` and `TRUSTED_HOSTS` if you use it, then redeploy the backend so CORS allows the site.

## Never do

- Never reuse a production value: no production database, Redis, JWT secret, `SECRET_KEY`, API key, token or Modal workspace. Generate or create everything fresh for this deployment.
- Never connect this Railway project or this Vercel project to the startup's repositories, and never import the startup's repos into them.
- Never add the new Railway or Vercel URLs to production OAuth apps (Google, Instagram) or to the production CORS list.
- Never merge this repo back into the startup's repos, or copy the startup's `.env` files here.
- Never commit a value: `.env` files are git-ignored, and the repo only holds the names-only `.env.example` files.

## Post-deploy smoke test

Replace the two URLs. Use a normal browser for the register step if `curl` is rejected as automated access.

1. **Health.** `curl RAILWAY_URL/health` returns `{"status":"ok", ...}` with HTTP 200.
2. **Register.** Open `VERCEL_URL`, click *Get started*, create an account. You are logged in at once (auto-verify) and the page shows no credits, plan or pricing wording.
3. **Upload.** *New analysis*, upload a reel of up to 90 seconds (for example a sample from `samples/`), pick one to three niches, add a one-line description. The processing screen lists the real steps (frames, watching, speech, cross-check, subjects, audio, deeper signals, music, trends, scoring, report) and ticks them off; the first run can take 3 to 4 minutes while the GPU warms up.
4. **Results.** The result shows an overall score and **all 10 parameters** unlocked, the fixes, the audio energy curve, captions, hashtags and a *Models used* panel, with no "upgrade" or "locked" text anywhere. Open *Settings*: there is no plan, credit or billing section.
5. **Links and ARIA.** With `STUDIO_ALLOW_LINKS=1` and `APIFY_API_TOKEN` set, pasting a public Instagram reel link works (the original Apify route); with `STUDIO_ALLOW_LINKS=0` a link request answers HTTP 400 "Reel links are switched off in this deployment". Open the ARIA chat on a result and ask about a parameter: the answer streams in and quotes this video's real scores; an unrelated question is politely declined.
6. **Per-user limit.** Start an upload and, while it runs, start another as the same user: the second answers HTTP 429 "You already have an analysis running. Please wait for it to finish."
7. **Budget 429.** In Railway set `STUDIO_DAILY_SPEND_CAP_USD=0` and redeploy. Start an analysis: it answers HTTP 429 "Prysm's demo budget for today is used up. Please try again tomorrow." and the website shows that message. **Then set the variable back** (default `10`, or remove it) and redeploy.
8. **Clean up.** Register a few test accounts and confirm the sixth sign-up from one network inside an hour answers 429.
