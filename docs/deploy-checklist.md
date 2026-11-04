# Deployment checklist (Railway + Vercel + Cloudflare R2)

Built during the Meta Global AI Developer Hackathon (new work). Names only: no value is written here, and none belongs in git. Generate secrets locally, paste them into the host's dashboard.

Where each piece runs:

| Piece | Where | What it needs |
|---|---|---|
| Backend (FastAPI, the analysis worker runs inside it) | Railway service, root directory `backend/`, `backend/Dockerfile` | Postgres with pgvector, Redis, the variables below |
| Postgres + pgvector | Railway service | the `vector` extension (the server creates it) |
| Redis | Railway database | nothing else |
| Website | Vercel project, root directory `web/` | `VITE_*` variables |
| Edited videos | Cloudflare R2 bucket | S3 keys, a 7-day lifecycle rule, a CORS rule |
| GPU models (viewer behaviour, signals) | Modal (already deployed in your workspace) | `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` |
| Instagram connect | Meta developer app (hackathon app) | app id/secret, redirect URI |

There is **no separate queue worker process**. The analysis queue is a Redis list drained by a loop that starts inside every backend process (`routers/prysm.py`, `ensure_worker`), and a Redis counter caps how many analyses run at once across all processes (`STUDIO_MAX_CONCURRENT`). Celery is in `requirements.txt` for the older clips/competitor/intelligence features; the demo flow does not use it, so do not create a Celery worker.

## Order of operations (short form; the click-by-click is in the final report of the task that wrote this file)

1. Generate secrets locally (section A).
2. Cloudflare: R2 bucket, API token, CORS rule, lifecycle rule (section D).
3. Railway: project, pgvector Postgres, Redis, backend service, variables, domain (section B).
4. Vercel: project with root `web/`, variables, deploy (section C).
5. Back on Railway: set `FRONTEND_URL`, `CORS_ORIGINS` to the Vercel URL, redeploy.
6. Meta developer console: add the redirect URI (section F).
7. Smoke test (section H).

## A. Secrets to generate (on your computer, never committed)

| Variable | Command |
|---|---|
| `SECRET_KEY` | `openssl rand -hex 32` |
| `JWT_SECRET` | `openssl rand -hex 32` (a different value from `SECRET_KEY`) |
| `TOKEN_ENCRYPTION_KEY` | from `backend/`: `python -m app.studio.instagram.tokencrypt generate`. Keep a copy in your password manager; if it is lost, creators just connect Instagram again. |

## B. Railway

### Services

1. **New Project** > *Empty project*.
2. **Postgres with pgvector.** Railway's stock Postgres does not ship pgvector, so add a service from a Docker image: *+ New* > *Docker Image* > `pgvector/pgvector:pg17`. Variables on that service: `POSTGRES_PASSWORD` (random), `POSTGRES_DB` = `prysm`, `PGDATA` = `/var/lib/postgresql/data/pgdata`. Add a Volume mounted at `/var/lib/postgresql/data`. Name the service `pgvector` (the references below use that name). The image's `postgres` user is a superuser, so the backend can run `CREATE EXTENSION IF NOT EXISTS vector` (it does so on every start).
3. **Redis.** *+ New* > *Database* > *Add Redis*. Name stays `Redis`.
4. **Backend.** *+ New* > *GitHub Repo* > pick the repo (needs the code pushed) > *Settings*:
   * **Root Directory**: `backend`
   * **Builder**: Dockerfile (read from `backend/railway.json`; the file also sets the healthcheck and restart policy)
   * **Start Command**: leave empty. The Dockerfile `CMD` runs `python -m app.studio.db_bootstrap` first (creates the `vector` extension; builds a fresh database and stamps it at the Alembic head, or runs `alembic upgrade head`; the container stops if that fails) and then starts the server with `WEB_CONCURRENCY` processes (default 2).
   * **Healthcheck path**: `/health` (already in `railway.json`; timeout 300 s because the first start creates the tables)
   * **Networking** > *Generate Domain* (this is the backend URL, `https://<name>.up.railway.app`)
   * **Resources**: the analysis downloads and transcodes video and loads audio libraries in each process. Memory has not been measured on Railway. Start with at least 4 GB and `WEB_CONCURRENCY=2`, and watch the *Metrics* tab during the first analyses.
   * No volume needed: uploads and renders use the container's temporary disk and are deleted after the analysis.

### Backend variables (Railway > backend service > Variables)

*Required* = the server refuses to start without it when `APP_ENV=production` (`app/studio/prod_check.py`, one message that lists every problem, names only).

| Variable | Required | Value / where to get it |
|---|---|---|
| `APP_ENV` | yes | `production` (turns on the startup check, hides `/docs`, production CORS and HSTS) |
| `SECRET_KEY` | yes | section A (32+ characters) |
| `JWT_SECRET` | yes | section A (32+ characters, different from `SECRET_KEY`) |
| `DATABASE_URL` | yes | `postgresql://postgres:${{pgvector.POSTGRES_PASSWORD}}@${{pgvector.RAILWAY_PRIVATE_DOMAIN}}:5432/${{pgvector.POSTGRES_DB}}` (the code converts `postgresql://` for asyncpg) |
| `REDIS_URL` | yes | `${{Redis.REDIS_URL}}` |
| `FRONTEND_URL` | yes | the Vercel production URL, `https://...` (it is also an allowed CORS origin and the link in emails) |
| `CORS_ORIGINS` | no | extra https origins, comma separated (for example a custom domain). Localhost is dropped in production. |
| `TOKEN_ENCRYPTION_KEY` | yes | section A |
| `AI_PROVIDER` | yes | `meta` |
| `META_API_KEY` | yes | the Muse key you use in your local `backend/.env` (do not paste it anywhere else) |
| `META_MODEL`, `META_VISION_MODEL`, `META_SCORING_MODEL`, `META_CHAT_MODEL`, `META_MODEL_MAP` | **no: leave unset** | The code already uses `muse-spark-1.3` for every step. If one of these names a model the key cannot use, Meta answers `HTTP 404 model_not_found`. To pin explicitly, set the first four to `muse-spark-1.3` and leave the map unset. |
| `META_VISION_MODEL`, `META_SCORING_MODEL`, `META_CHAT_MODEL` | no | default `muse-spark-1.3`; leave unset |
| `GROQ_API_KEY` | yes | <https://console.groq.com/keys> (speech transcription) |
| `GEMINI_API_KEY` | recommended | <https://aistudio.google.com/apikey> (audio and trend stages; without it they are skipped, logged as a warning) |
| `AUTO_EDIT_ENABLED` | no | **default off** (`0`). `1` turns on Auto-edit (on-demand edited video for uploads; see section H). The server then also requires the four R2 variables below. |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` | only with `AUTO_EDIT_ENABLED=1` | section D (the start-up check names any that is missing) |
| `AUTO_EDIT_STORAGE` | with Auto-edit on | `s3` (R2). Default `local` keeps files on the container's disk, which is lost at every redeploy; the start-up check warns. |
| `AUTO_EDIT_MAX_COST_USD` | no | spend cap of one edit run, default `1.50` (a real edit cost about $0.10 to $0.30) |
| `AUTO_EDIT_RETENTION_DAYS` | no | days kept in storage, default `7` |
| `AUTO_EDIT_PLANNER_MODEL`, `AUTO_EDIT_CRITIC_MODEL` | no | defaults `muse-spark-1.3` and `muse-wonder-1.0`; leave unset (both are checked at start-up when Auto-edit is on) |
| `AUTO_EDIT_PLANNER_EFFORT`, `AUTO_EDIT_CRITIC_EFFORT` | no | reasoning effort, default `low` |
| `AUTO_EDIT_CRITIC_REQUIRED` | no | default `1`: an unverified group of changes is reverted |
| `AUTO_EDIT_FONT_DIR`, `AUTO_EDIT_STORAGE_DIR`, `AUTO_EDIT_FFMPEG` | no | paths; defaults `/app/data/fonts`, `/app/data/auto_edit` and the container's `ffmpeg`; leave unset |
| `AUTO_EDIT_LEGACY` | no | leave unset: the baseline's automatic edit after every analysis stays off |
| `R2_ENDPOINT`, `R2_BUCKET` | no | optional overrides of the endpoint and bucket derived from `R2_ACCOUNT_ID` and `R2_BUCKET_NAME` |
| `DINO_WIRE` | no | default on; `0` turns off picture matching, "Closest to your Reel N" and the attention strip |
| `DINO_BACKEND`, `DINO_MODAL_URL`, `DINO_MODAL_TOKEN` | for DINOv2 | `modal` plus the URL printed by `modal deploy` of the DINOv2 app and its token. Unset: the DINOv2 features report "unavailable" and nothing breaks. |
| `DINO_PLANNER_HINTS`, `DINO_EDIT_RENDER_CHECK` | **leave unset** | stubs that do nothing today |
| `STUDIO_UNLIMITED` | demo | `1`: no plans, credits or pricing; the cost caps below protect your spend |
| `DEMO_AUTO_VERIFY` | yes (or `BREVO_API_KEY`) | `1`: new accounts are verified at once. Without it and without an email key nobody can verify an account. |
| `STUDIO_ALLOW_LINKS` | no | **Default 0 (off) in code**: unset or empty means off, uploads only. Set `1` to turn on "study any reel" (paste a link): it downloads other people's reels through Apify and yt-dlp, so it also needs `APIFY_API_TOKEN` and `VITE_STUDIO_ALLOW_LINKS=1` on Vercel (the website switch is separate and set at build time; set both or neither). See `docs/link-flow-notes.md`. |
| `APIFY_API_TOKEN` | only with links on | <https://console.apify.com/account/integrations>. Apify bills per run and is not part of the spend cap below; set a monthly limit in the Apify console. |
| `STUDIO_LINK_DAILY_LIMIT` | no | link analyses one user can start per day (default 20; suggested 5 for a public demo) |
| `STUDIO_LINK_DOWNLOAD_TIMEOUT_S` | no | seconds to wait for a reel download before the plain "upload the file instead" message (default 100) |
| `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` | for GPU signals | section E |
| `ENGAGEMENT_BACKEND` | yes with Modal | `modal` (or `heuristic` to skip the GPU models). Never `runpod` (the old worker that silently made up numbers). |
| `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`, `INSTAGRAM_REDIRECT_URI`, `INSTAGRAM_FRONTEND_URL` | only for Instagram | section F. If `INSTAGRAM_APP_ID` is set the other three are required and checked. |
| `FOUNDER_EMAIL` | no | your email: you alone see the per-analysis cost panel |
| `PROMOTE_EMAILS` | no | emails promoted to the top plan at startup; leave empty |
| `TRUSTED_HOSTS` | no | leave it **empty** (no host check). If you set it, it must list the backend hostname **and** Railway's healthcheck hostname (`healthcheck.railway.app`), or the healthcheck is refused and the deploy never goes live. |
| `WEB_CONCURRENCY` | no | server processes, default 2 |
| `STUDIO_MAX_CONCURRENT` | no | analyses running at once across all processes, default 2 |
| `BREVO_API_KEY`, `BREVO_FROM_EMAIL`, `BREVO_FROM_NAME` | no | only if you want real verification emails |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` | no | only for Google sign-in; the redirect is `https://<vercel-url>/oauth/google` and must be in the Google console |
| `ACRCLOUD_HOST`, `ACRCLOUD_ACCESS_KEY`, `ACRCLOUD_ACCESS_SECRET` | no | music identification; skipped when unset |
| `STUDIO_DAILY_SPEND_CAP_USD`, `STUDIO_USER_HOURLY_LIMIT`, `STUDIO_USER_DAILY_LIMIT` | yes | section G |

Leave these **unset** in production (the server refuses to start if the dev switches are on): `DEBUG`, `DEV_BAKEOFF`, `DEV_DINO`, `STUDIO_EXPOSE_COST`, `DEBUG_SAVE_INPUTS`, `INSTAGRAM_FIXTURE`. Also unset: `DINO_*` (DINOv2 is a developer page only, it is not part of the analysis), `RUNPOD_*`, `STRIPE_*`, `RAZORPAY_*`, `CELERY_*`, `ANTHROPIC_API_KEY` (not used when `AI_PROVIDER=meta`).

Railway sets `PORT` itself.

## C. Vercel (website)

1. *Add New* > *Project* > import the same GitHub repo.
2. **Root Directory**: `web`. **Framework preset**: Vite. **Build command**: `npm run build`. **Output directory**: `dist`. **Install command**: `npm install`. Use Node 20 or newer (24 works locally).
3. **Environment variables** (Production). They are baked in at build time, so change them and redeploy:

| Variable | Required | Value |
|---|---|---|
| `VITE_STUDIO_MODE` | yes | `1` (demo: no pricing, credits or upgrade prompts) |
| `VITE_API_URL` | yes | the Railway backend URL, `https://<name>.up.railway.app`, no trailing slash. All API calls go there directly; `web/vercel.json` is only the single-page-app fallback. |
| `VITE_UPLOAD_URL` | yes | the same URL (video uploads skip Vercel's 4.5 MB request limit) |
| `VITE_SUPPORT_EMAIL` | recommended | your contact address (otherwise `support@example.com` shows) |
| `VITE_STUDIO_ALLOW_LINKS` | no | `1` only together with the backend's `STUDIO_ALLOW_LINKS=1` (off: no "Paste a link" card, no "Reel Breakdowns" tab, `/analyse/link` goes to the upload page) |
| `VITE_FOUNDER_EMAIL` | no | your email, to show the cost panel to you only |
| `VITE_SOCIAL_X`, `VITE_SOCIAL_LINKEDIN`, `VITE_SOCIAL_INSTAGRAM` | no | footer links; hidden when unset |
| `VITE_PUBLIC_POSTHOG_KEY`, `VITE_PUBLIC_POSTHOG_HOST`, `VITE_PUBLIC_CLARITY_ID` | no | analytics; off when unset |

Never set on Vercel: `VITE_DEV_BAKEOFF`, `VITE_DEV_DINO`, `VITE_SHOW_COST_PANEL`, `VITE_SHOW_QWEN_CARD`, `VITE_PROXY_TARGET`, `VITE_PORT`. The dev pages are compiled out of a production build in any case (checked: the bundle contains no bake-off or DINOv2 page).

`web/vercel.proxy.example.json` is the old setup that proxied `/auth`, `/prysm` ... through Vercel. It is not used: `/chat` is both a page and an API prefix, so a refresh on `/chat` would have gone to the backend. `web/public/_redirects` and `web/netlify.toml` are for Netlify and are ignored by Vercel.

4. *Deploy*. Copy the production URL back into Railway (`FRONTEND_URL`, and `CORS_ORIGINS` for any extra domain) and redeploy the backend.

## D. Cloudflare R2

What goes in R2: only the edited video made after an **upload** analysis (key `edited/<analysis id>/output.mp4`, served through a 24-hour presigned link). Original uploads are not stored.

1. Cloudflare dashboard > *R2 Object Storage* > *Create bucket* (any name, for example `prysm-demo-videos`, location automatic). Do not enable public access.
2. *R2* > *Manage R2 API Tokens* > *Create API token* > permission **Object Read & Write**, scoped to that bucket. Copy the *Access Key ID* and *Secret Access Key* (shown once). The *Account ID* is on the R2 overview page.
3. Backend variables: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`.
4. Bucket > *Settings* > **CORS policy** > add:
   ```json
   [{ "AllowedOrigins": ["https://<your-vercel-url>"], "AllowedMethods": ["GET", "HEAD"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 3600 }]
   ```
   (the browser only plays and downloads the presigned video; nothing is uploaded from the browser)
5. Bucket > *Settings* > **Object lifecycle rules** > *Add rule*: name `delete-after-7-days`, prefix empty (or `edited/`), action *Delete uploaded objects* after **7 days**.

## E. Modal (GPU models)

The two Modal apps are already deployed in your workspace: `prysm-hackathon-engagement` (viewer behaviour) and `prysm-studio-signals` (signals). The backend calls them with the Modal SDK, so it needs only a token pair, no URL.

1. Modal dashboard > *Settings* > *API Tokens* > *New Token*. Copy the Token ID and Token Secret.
2. Backend variables: `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `ENGAGEMENT_BACKEND=modal`.
3. Before a public demo, warm the containers so the first visitor does not wait for a cold start: `docs/modal-engagement.md` ("Before a demo"). Containers scale to zero.
4. Set a spend limit on the Modal workspace (*Settings* > *Usage and billing*).
5. DINOv2 (`DINO_MODAL_URL`, `DINO_MODAL_TOKEN`, `DEV_DINO`) is a developer page only. Do not set any of them in production.

## F. Meta / Instagram (optional; the analysis works without it)

Use the **hackathon** Meta app only, never the production one. Full background: `docs/instagram-login.md` (section 7 and 7b).

1. <https://developers.facebook.com> > My Apps > the hackathon app > *Instagram* > *API setup with Instagram login*. Copy the **Instagram** app ID and secret (not the top-level ones) into `INSTAGRAM_APP_ID` and `INSTAGRAM_APP_SECRET`.
2. Backend variables: `INSTAGRAM_REDIRECT_URI` = `https://<railway-backend-host>/auth/instagram/callback` (https, exact, no trailing slash) and `INSTAGRAM_FRONTEND_URL` = the Vercel URL.
3. In the console: *Business login settings* > *OAuth redirect URIs* > *Add URI* > paste exactly the value of `INSTAGRAM_REDIRECT_URI` > *Save*.
4. Keep the app in *Development* mode. *App roles* > *Roles* > *Instagram Testers* > add the demo account and accept the invite inside Instagram. Only accounts with a role on the app can connect until you pass Meta's App Review (outside this build).
5. `TOKEN_ENCRYPTION_KEY` (section A) is required in production whether or not Instagram is used.

The callback goes straight to the backend (`/auth/instagram/callback`), so it works without the Vercel proxy. Note: `docs/instagram-login.md` 7b still says to proxy `/instagram/*` through `vercel.json`; with `VITE_API_URL` set that is no longer needed.

## G. Cost caps

Measured cost of one analysis with Muse Spark 1.3 is about $0.25 (one live check: $0.2502 in total, of which the model calls were about $0.22). The three caps below are required and checked at startup. Suggested public-demo values:

| Variable | Suggested | What it does |
|---|---|---|
| `STUDIO_DAILY_SPEND_CAP_USD` | `10` | stops new analyses for the rest of the UTC day once the recorded cost passes it (about 40 analyses). Polite message, HTTP 429. |
| `STUDIO_USER_HOURLY_LIMIT` | `3` | analyses one user can start per hour |
| `STUDIO_USER_DAILY_LIMIT` | `8` | analyses one user can start per UTC day (about $2 per user per day at most) |
| `STUDIO_USER_MAX_CONCURRENT` | `1` (default) | one running analysis per user |
| `STUDIO_REGISTER_PER_IP_HOURLY` | `5` (default) | sign-ups per IP per hour |
| `STUDIO_MAX_CONCURRENT` | `2` (default) | analyses at once across all processes |
| `DNA_COST_CAP_USD` | `0.60` (default) | cost cap of one Instagram Creator DNA import |

Caps live in Redis counters. If you recreate Redis, the day's total restarts at zero. They protect the cost of analyses; they do not cap Modal GPU time, Groq or Gemini usage on their own, so also set billing limits in the Modal, Groq and Google consoles, and a budget on the Meta account.

## H. Smoke test after the first deploy

1. Railway > backend > *Deployments* > logs show `[db] state: fresh`, `[db] fresh database: tables created, stamped at head`, then `Application startup complete`. On a later deploy: `[db] state: versioned`, `[db] alembic upgrade head done`.
2. `https://<backend>/health` returns `{"status":"ok", ...}`. `https://<backend>/docs` returns 404 (hidden in production). `https://<backend>/dev/bakeoff` returns 404.
3. Open the Vercel URL, register, upload a short video, wait for the results (a few minutes). In the browser's network tab the calls go to the Railway URL with no CORS error.
4. The log line `[JobCost] total=...` shows the cost of the run. Open the results page: the edited video link works (needs R2).
5. Hit the limits on purpose once (a fourth analysis within an hour) and see the polite 429 message.

## Not verified from here

Written and tested locally, not run on Railway, Vercel or Cloudflare: the Railway variable-reference syntax (`${{...}}`), the Docker-image Postgres service, the R2 CORS and lifecycle screens (their labels change), and the memory a Railway plan needs. Treat the first deploy as the test; the startup message names exactly what is missing.

## Meta model check (what the logs tell you)

At start-up the backend asks Meta (free `GET /v1/models`) which models the key can use and logs one line, e.g. `[Models] startup check: vision=muse-spark-1.3, scoring=muse-spark-1.3, chat=muse-spark-1.3`. A configured model the key cannot use is logged as `[Models] WARNING: the <step> model <id> (from <VARIABLE NAME>) is not in this key's model list; using <id>` (names only, never the key). Behaviour:

* A missing plain Muse Spark model is replaced by the newest plain Muse Spark model the key has (never a `-contributor` one). If there is none, every analysis is refused in the first seconds with "We couldn't start the analysis. Please try again in a few minutes." and the log says which variable set the bad model.
* A step that errors is retried once. The vision step then fails the analysis clearly (no hollow report); optional steps show "(could not run)" instead of "skipped".
* The check is repeated at the start of each analysis (cached for 5 minutes); it only spends money (about $0.001) when a model is missing from the list and needs confirming.

## H. Auto-edit and DINOv2 in the Railway container (checked on the merged code)

Checked by building `backend/Dockerfile` (Python 3.11.18 on Debian) and running inside it:

* **FFmpeg:** the `ffmpeg` package installed by the Dockerfile is 7.1 with `libass` and `libfreetype`, and has the `ass` (captions) and `drawtext` filters, so no extra system package is needed. Auto-edit picks it up as `/usr/bin/ffmpeg`.
* **Fonts:** Google Fonts are downloaded at first use (the container needs outbound access to `fonts.googleapis.com`; a real download of "Lobster" worked) into `/app/data/fonts`. The container runs as root, so that path and `/app/data/auto_edit` are writable. Both are **ephemeral**: fonts are simply downloaded again after a redeploy, but retained originals and edited videos are not, which is why `AUTO_EDIT_STORAGE=s3` (R2) is needed. A bundled Poppins is the fallback if a download fails.
* **No GPU on Railway:** Auto-edit and DINOv2 do not need one in the container. Music ducking (Demucs) and the DINOv2 embeddings run on **Modal** (needs `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`, and for DINOv2 `DINO_MODAL_URL` and `DINO_MODAL_TOKEN`). Without Modal, ducking is skipped and DINOv2 shows "unavailable".
* **Models:** every Auto-edit model call (planner, critic, speech) goes through `MetaClient` and `META_BASE_URL`, so it follows the relay (`docs/meta-relay.md`) like the rest. It makes no Anthropic call. Its Whisper step uses Groq (`GROQ_API_KEY`, already required).
* **Database:** the merge adds no migration; there is one Alembic head (`t0u1v2w3x4y5`). A clean database builds and stamps at it.
