# Instagram connect and Creator DNA

Built during the Meta Global AI Developer Hackathon (new work). Read on 2026-11-04 from Meta's developer documentation (links at the end). Items marked **unverified** could not be confirmed from the docs and need a test with a real account.

## 1. What the baseline did

The baseline already had an Instagram connection in `backend/app/routers/auth.py` (`/auth/instagram`, `/auth/instagram/callback`, `DELETE /auth/instagram`) and a localhost-only block in `web/src/pages/Settings.tsx`.

| | Baseline |
|---|---|
| Flavour | **Instagram API with Instagram Login** ("Business Login for Instagram"): `www.instagram.com/oauth/authorize`, `api.instagram.com/oauth/access_token`, `graph.instagram.com`. No Facebook Page is involved. |
| Scopes requested | five: `instagram_business_basic`, `_manage_messages`, `_manage_comments`, `_content_publish`, `_manage_insights`. Far more than reading reels needs. |
| Redirect URI | one value from `INSTAGRAM_REDIRECT_URI` (the backend callback URL), sent identically to the authorize and the code-exchange calls. After the callback the browser goes back to `INSTAGRAM_FRONTEND_URL` or `FRONTEND_URL` + `/settings?instagram=connected`. |
| CSRF state | `secrets.token_urlsafe(32)` stored in Redis as `ig_oauth_state:<state>` -> user id, 10 min, single use. |
| Token | code -> short-lived token -> long-lived token (60 days) -> `GET /me?fields=id,username`. Stored **in plaintext** in `users.instagram_access_token`, with `instagram_user_id` and `instagram_username`. Never refreshed. |
| Disconnect | set the token and user id to NULL (the username stayed). Nothing else existed to delete. |
| Defects found | the state was checked **after** the code had been exchanged; the consent text was a single line; the long-lived exchange silently fell back to the short-lived token. |

## 2. What changed

* Scopes cut to the two needed: `instagram_business_basic` and `instagram_business_manage_insights`.
* The state is validated first and consumed once (`GETDEL`) before anything is exchanged.
* The token is stored exactly where the baseline stored it. The expiry and refresh bookkeeping live in a new table (`ig_account`), not on `users`.
* A consent screen (`/creator-dna`) comes before the redirect, and Disconnect deletes the token **and** every derived row.
* Instagram is a **connect** step after login. Sign-up and login stay email and Google.
* The token is **encrypted at rest** (Fernet, `TOKEN_ENCRYPTION_KEY`), see section 5.
* Every claim in the Creator DNA and every sentence of a personalised answer that talks about the creator's reels is **checked against the imported reels** and rewritten once or dropped, see section 5.
* Reels Instagram gives no video file for (licensed audio) are **marked as not fully analysed** everywhere they appear, see section 5.
* The new code is in `backend/app/studio/instagram/`; the three baseline routes now call it.

## 3. Meta's API: what is available

### Authorisation (Instagram Login)

* Authorise: `https://www.instagram.com/oauth/authorize?client_id=<IG app id>&redirect_uri=<uri>&response_type=code&scope=<comma list>&state=<state>`.
* Code (valid 1 hour, single use) -> `POST https://api.instagram.com/oauth/access_token` (`client_id`, `client_secret`, `grant_type=authorization_code`, `redirect_uri`, `code`) -> short-lived token (1 hour) and the Instagram-scoped user id. The redirect may append `#_` to the code; strip it.
* Long-lived: `GET https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret=...&access_token=<short>` -> 60 days. Server side only (it carries the app secret).
* Refresh: `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=<long>` -> new 60 days. The token must be at least 24 hours old, not expired, and have `instagram_business_basic`. A token not refreshed within 60 days is dead and the creator must connect again. We refresh when fewer than 14 days are left (checked when the Creator DNA page or an import is opened).
* Account types: Instagram **Business or Creator** (professional) accounts. A personal account cannot use this API. A linked Facebook Page is not needed.
* Access level: **Standard access is enough while the app only serves people who have a role on the app** (admin, developer, or an Instagram tester). Serving other people needs Advanced access = App Review + Business Verification. The hackathon demo therefore works with the founder's own account and added testers only.

### Reels list

`GET https://graph.instagram.com/me/media?fields=id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count&limit=<n>` (newest first, cursor pagination through `paging.next`). Keep the items whose `media_product_type` is `REELS`.

* There is **no duration field** on media. We read the duration from the downloaded file (ffprobe) while extracting frames.
* `media_url` is a short-lived signed CDN link. It is **omitted when the reel uses licensed or library audio** (even for the owner's own reel). Then we fall back to `thumbnail_url` (one frame) and the reel has no duration, no watch-through, and a weaker fingerprint. The UI says so.
* The page for IG Media says `caption` and `media_product_type` are "Facebook Login only". Whether `graph.instagram.com` serves them is **unverified** (we have no real account yet); the importer asks for the full field list first and, on a 400, retries with a smaller list and filters reels by `media_type=VIDEO` instead.

### Reel insights

`GET https://graph.instagram.com/<media-id>/insights?metric=<list>` (permission `instagram_business_manage_insights`).

| Metric | Meaning | Status |
|---|---|---|
| `reach` | accounts that saw the reel | estimated |
| `views` | plays | in development |
| `likes`, `comments`, `shares`, `saved` | interactions | stable |
| `total_interactions` | sum of the above | in development |
| `ig_reels_avg_watch_time` | average watch time (ms) | stable |
| `ig_reels_video_view_total_time` | total watch time (ms) | in development |
| `reels_skip_rate` | share of views that left in the first moments | estimated, in development |

Rules that shape the code:

* **Missing data comes back as an empty `data` array, not as 0.** We store `NULL` and mark the metric "not available". A real 0 is stored as 0.
* Data can **lag up to 48 hours**, so a reel younger than that can honestly have no insights yet.
* A metric that cannot be served makes the whole multi-metric request fail with an "unknown error". The importer asks for all metrics at once and, if that fails, asks for each metric on its own.
* Counts cover organic activity only.
* The `total_*` variants exist only for Facebook Login and are not used.

### Rate limits

Business Use Case rate limiting: **calls in 24 hours = 4800 x the number of impressions the account's content had in the last 24 hours**, counted per app and app user over a rolling 24 hours. One import of N reels makes 1 to 3 list calls, N insight calls (up to 8N if every metric needs its own call), N media downloads from the CDN (not Graph calls) and 1 token refresh. A small account has a small budget, so the importer stops cleanly on a rate-limit error (HTTP 429, or error codes 4, 17, 32, 613), keeps what it has and says so.

## 4. Redirect URIs and localhost

* The `redirect_uri` must match a value in the app dashboard (Instagram > API setup with Instagram login > Business login settings > "OAuth redirect URIs") **exactly**, and the same string must go to the authorize and to the code-exchange call. The dashboard may append a trailing slash; copy what it shows.
* Meta's pages do not say whether `http://localhost` is accepted. **Unverified for Instagram Login.** The only direct report we found (a developer blog) could not register an `http://` URI for Business Login and used an HTTPS tunnel. Older Facebook Login guides say localhost is exempt in development mode. Treat plain `http://localhost` as **not working** until the founder tries it.
* **Plan**: for local development with a real account, run an HTTPS tunnel to the backend (`cloudflared tunnel --url http://localhost:8778` or `ngrok http 8778`), register `https://<tunnel-host>/auth/instagram/callback`, and set `INSTAGRAM_REDIRECT_URI` to it. `INSTAGRAM_FRONTEND_URL` stays `http://localhost:5177` (the callback only redirects the browser there). For the deployed site register `https://<backend-host>/auth/instagram/callback`. A tunnel host that changes needs a new dashboard entry each time; a reserved ngrok or cloudflared hostname avoids that.
* Without an HTTPS URI the real connect cannot be tested; `INSTAGRAM_FIXTURE=1` covers everything else.

## 5. What is built

| Piece | Where |
|---|---|
| Connect flow, state in Redis, long-lived token, refresh (<14 days left), Disconnect | `backend/app/studio/instagram/oauth.py`; the three baseline routes in `routers/auth.py` call it |
| Reels list, insights (all metrics in one call, else one by one; empty = `None`), temporary download | `graph.py`, `frames.py` |
| Import job (5 to 30 reels, default 10), live progress through `app/studio/progress.py`, one import per user at a time | `importer.py`, `router.py` (`/instagram/*`) |
| Tables `ig_account`, `ig_reel`, `ig_reel_insight`, `creator_dna` | `models.py`, migration `n4o5p6q7r8s9` (the app also creates them at start-up, like every table) |
| Column `ig_reel.analysis_level`; encrypts tokens already stored | migration `o5p6q7r8s9t0` (run `alembic upgrade head`; set `TOKEN_ENCRYPTION_KEY` first if a token is already stored) |
| Columns `ig_account.profile_picture_url`, `ig_reel.thumbnail_url` (links only, no image is stored) | migration `p6q7r8s9t0u1` |
| Hook words: `ig_reel.hook_words`, `hook_words_status` | migration `q7r8s9t0u1v2`; `hook.py`, `frames.read_reel` |
| "Did my fix work?": table `ig_fix_link` | migration `r8s9t0u1v2w3`; `closeloop.py`; routes `/instagram/analyses`, `/instagram/links` |
| Token encryption at rest | `tokencrypt.py`, used by the `users.instagram_access_token` column type |
| Grounding of claims and of personalised answers | `grounding.py` (pure checks), `dna.py` (`validate_dna_report`, one repair call), `personalise.py` (`ground_result`, `strip_personal`) |
| performance_index (reach, else views, divided by the median of the imported reels), watch_through (average watch time / duration) | `metrics.py` |
| Fingerprints (one Spark call per reel on about 6 frames + caption) and the Creator DNA (one call; every claim cites reels, uncited or invented citations are dropped in code) | `dna.py` (`META_DNA_MODEL`, default `muse-spark-1.3`) |
| Personalised analysis: addendum on both scoring prompts (`COACH_PERSONALISE`, default on for users who have a DNA), the "How this compares to your last N reels" block saved on the result | `personalise.py`, one new optional parameter `creator_dna_addendum` of `run_full_analysis` |
| Web: `/creator-dna` page, results cards, Settings link | `web/src/studio/CreatorDna.tsx`, `CreatorDnaCard.tsx`, `ViewerBehaviourCard.tsx` |

Behaviour worth knowing:

* **Missing metrics.** Each metric is stored per reel as a value or `NULL` plus an `available` flag. The page lists the metrics Instagram returned nothing for, and a reel without insights says why. The index falls back from reach to views; the watch-through is empty when the watch time or the duration is unknown (a reel with no `media_url` has no duration). No number is ever filled in.
* **Edge cases.** Fewer than 5 reels: no model call, and the text says there are too few to call anything a pattern. Fewer than 4 reels with a usable index: no ranking, and it says so. A reel with an index above 3 (three times the account median) is flagged as an outlier: the summary says medians were used and the model is told not to build a habit on it.
* **Cost control.** Per import, fingerprints stop at 85 percent of `DNA_COST_CAP_USD` (default 0.60), leaving room for the summary; the cost goes into the daily spend counter of the safety net (`STUDIO_DAILY_SPEND_CAP_USD`) and is shown on the DNA record. The comparison block of a new analysis costs about one small text call and is added to that analysis' cost.
* **Viewer behaviour card.** The zero-shot Qwen percentages (near 70 percent for every video) are no longer shown. With imported data the card shows the creator's own measured watch-through, skip rate and typical reach, labelled "measured from your account". Without it, a line links to the connect flow. `VITE_SHOW_QWEN_CARD=1` brings the Qwen bars back for testing, still labelled "Rough estimate from frame content, not measured watch data". `ENGAGEMENT_ZEROSHOT_EVIDENCE` stays off.
* **Disconnect** deletes the token, the account row, all reels, insights and the DNA, and removes the comparison block from the user's earlier saved analyses. It does not touch the analyses themselves.
* **Token encryption.** The token stays in `users.instagram_access_token`, but the column type now encrypts on write and decrypts on read, so no other code changes. Fernet (AES-128-CBC with an HMAC) with the key in `TOKEN_ENCRYPTION_KEY`; the stored form is `enc:v1:<token>`. Several comma-separated keys work for rotation (the first encrypts, all decrypt). A token saved before this existed is read as it is, and is encrypted by the migration or, at the latest, the first time it is used. Without a key, development keeps working with a plain token (one warning); in production or staging (`APP_ENV`) the token is **not stored** rather than stored in plain text. A token that cannot be decrypted (key lost or changed) reads as "not connected", so the creator just connects again, and loading the user never fails. Neither the key nor a token is ever logged or put in an error message (tests check this). Generate a key with `python -m app.studio.instagram.tokencrypt generate`.
* **Grounding.** A claim is accepted only when (1) it cites reels that exist in the import, (2) it contains no invented detail, and (3) what it says about hook, format or pacing matches the fingerprints of the reels it cites. "Invented detail" means: a quoted phrase, hashtag, @name or link that is not in the cited reels' captions; a greeting, sign-off, catchphrase or call to action that is not in a cited caption (a vague word like "greeting" needs a real example in a cited caption); something said or played ("says", narration, music, a sound) when nothing shows speech or music in the cited reels (the fingerprint's audio type is "unknown" unless obvious; there is no transcript of imported reels, so spoken words must be in the caption); watch-through, skip rate or retention for reels with no measured watch-through; and numbers that are not the cited reels' own numbers. "Strongest" claims must cite mostly top reels, "weakest" mostly bottom reels. In the DNA, rejected claims trigger **one** repair call that lists what was rejected and why; the better answer is kept and whatever still fails is dropped (no third call; skipped if the cost cap is reached). Each kept claim stores the reel numbers and the Instagram media ids it cites, and the DNA records how many claims were checked, dropped and whether it was repaired. In a **personalised answer** (the summary and each parameter's text), every sentence that mentions "Reel N" is checked the same way, and a sentence that talks about the creator's own reels without naming one ("you usually...", "your last reels...") is rejected. Rejected sentences go to one rewrite call that sees only the known facts; a rewrite that still fails, or any failure of the call, drops the sentence (a field left empty says the comparison was removed). If grounding itself errors, every sentence about the creator's reels is removed (fail closed). The addendum given to the analysis now lists the only known facts per reel (hook, format, pacing, index, watch-through) and says everything else is unknown. Limits: the check is rule-based, so it catches the invented details listed above, not every possible wrong inference; a claim can still be a weak reading of a small sample, which is why every answer says "patterns, not proof".
* **Reels without a video link.** When Instagram gives no `media_url` (licensed audio) the reel is read from its thumbnail and caption (`analysis_level` = `thumbnail_caption`), or from the caption alone if there is no thumbnail either (`caption_only`). Such a reel has no duration, so **no watch-through**, and its hook, format, pacing and audio are set to "unknown" whatever the model answered, because one still image cannot show them. It can still be ranked by its reach or views. The UI says so wherever it appears: a "THUMBNAIL + CAPTION ONLY, NOT FULLY ANALYSED" badge and the reason on the reel row, "watch-through: none (no video)", a "Not fully analysed: Reel N" notice on the DNA card, a count "7 of 8 reels fully analysed from video; 1 from thumbnail and caption only", a sentence in the DNA summary header, a note in the import result, and a line on the results-page comparison card. The comparison and the claims do not count those reels for hook, format or pacing, and the measured watch-through average leaves them out (it says how many reels have none).

* **Import limits.** 1 to 30 reels (default 10). The page asks Instagram once, cached 10 minutes, how many reels the account has (`GET /instagram/available`, pages of 100 with only the type fields, stopping at 30) and shows "You have N reels available"; the slider and the typed number stop at the lower of 30 and that count, and the default is 10 or the maximum if fewer. With 1 or 2 reels you get the per-reel breakdown and "Patterns need at least 3 reels", with no pattern claim of any kind and no model call. From 3 reels the DNA card appears, headed "Based on N reels" (3-4 reels: what they have in common, worded as early observations; 5 and up: strongest / weakest / keep / change from the model).
* **The page.** Built in the app's own look (cream background, General Sans and Manrope, the border and primary tokens, shadcn components). Account card (picture, @handle, Connected, last imported time) with a small menu (Re-import, Disconnect with a confirm dialog); import card with estimated time and per-reel progress; Creator DNA card where each pattern appears once with chips for the reels it comes from (a chip opens that reel); reel cards with thumbnail, date, caption cut at a word with "Show full caption", plain-language reach, every metric Instagram returned (views, reach, likes, comments, saves, shares, average watch time, skip rate, watch-through; "no data yet" when empty, never 0), hook words, and the "not fully analysed" badge. Loading, empty and error states; every control works from the keyboard; the layout was checked at 390 px.
* **Plain language.** The index (reach or views divided by the median of the imported reels) is shown as "Reach 25% below your usual" or "about your usual" (within 5 percent), explained once above the reel list. Instagram reports the skip rate as a percent (59.1 means 59.1 percent); the page shows it as such. Captions are cut by visible character (an emoji is never split) and rendered with an emoji font stack; the earlier broken emoji came from cutting captions in the middle of a surrogate pair.
* **Hook words.** During the import, for each reel that has a video file, the first 12 seconds of audio are cut with ffmpeg (16 kHz mono WAV, inside the temporary folder that is deleted right after) and sent to Meta Voice Transcribe through the same `MetaClient.transcribe` the analysis pipeline uses. Only the text is stored (`hook_words`, with status `transcribed`, `no_speech` or `unknown`). Reels without a video link stay `unknown`. The words go into the fingerprint prompt (a transcribed reel's audio type becomes "speech"), into the DNA prompt and the addendum as "first spoken words", and into the grounding validator: a quote, greeting or "says..." claim is supported only when it is in the caption or the hook words. Voice Transcribe is billed by audio length: about $0.0003 for 6 seconds measured, so about **$0.0006 per reel** for 12 seconds. It is skipped once the import cost cap is reached.
* **Did my fix work?** On the Creator DNA page the creator picks one of their saved Prysm analyses and the reel they posted after it. The page suggests the likely reel by date (posted after the analysis, within 21 days) and length (against the analysed video's length). They tick which advice they applied (the top priorities and the fixes of low-scoring parameters) and whether they posted the auto-edited version. The link is stored by the reel's Instagram media id, so a re-import (which replaces the reel rows) keeps it, and Disconnect deletes it. The result compares the linked reel with the median of the creator's **other** imported reels: reach (or views) in plain words, watch-through, saves and shares per 1,000 reached, and skip rate (lower is better), each marked better, about the same or worse than usual, or "no data yet"; a real 0 counts as a value. The wording never says the fix worked. It always shows how many other reels the usual is based on, that Instagram numbers can lag up to 48 hours (and says so specially for a reel posted in the last 48 hours), and that one reel is not proof.
* **Results page.** "How this compares to your last N reels" now shows the new video's first words, the creator's strongest and weakest reels in plain language (reach against their usual, watch-through, and how each reel opens), and the style match with counts and reel numbers. The personalised advice is told to cite the specific reel for a fix its reels support, and every sentence about their reels is still run through the grounding check.

### Fixture mode (development only)

`INSTAGRAM_FIXTURE=1` replaces Instagram with 12 generated reels (small videos made on the fly, so download, frame extraction and deletion run for real) with plausible insights. Built-in cases: reel 1 is under 48 hours old (no insights), reel 4 has no watch-time metrics, reel 9 is a viral outlier, reels 6 and 12 have no `media_url` (the licensed-audio case), reels 2 and 5 have long captions with emoji. The fixture videos are silent, so its hook words are deterministic fake ones (the talking reels "say" their on-screen line, b-roll has none); the real Voice Transcribe path was checked separately on a spoken test clip. It is refused unless `APP_ENV` is not production, the frontend URL is on localhost and the request comes from the same machine. Captions start with "[FIXTURE]", the account is flagged, the page shows a black banner and a FIXTURE tag on every reel, and the results cards say "test data".

### Tests

`backend/app/studio/tests/test_instagram_hardening.py` (31 tests): grounding (an uncited claim, a claim citing a reel that was not imported, an invented greeting, quote, number, hashtag, speech and music claim, a claim that contradicts the fingerprints, the one-repair rule, the cost-cap rule, personalised sentences rewritten or dropped, fail closed), token encryption (round trip, plain tokens still readable and encrypted on first use, wrong or missing key, invalid key, rotation, production refusal, no key or token in logs, the database holding only the encrypted value, the migration helpers) and reels without a video link (levels stored, hook/format/pacing unknown, no watch-through, header and comparison). The file now also covers the 1-30 limits, the breakdown for 1-2 reels, the reel count call, plain-language numbers, hook words (cut, transcribe, store, cost cap, grounding), the close-the-loop numbers and link lifecycle, and the production startup check (55 tests in total). The browser check `scripts/e2e_instagram_fixture.mjs` has 71 checks. Earlier tests in `backend/app/studio/tests/test_instagram.py` (38 tests): OAuth state (single use, forged state never reaches Instagram), scopes, code exchange, token storage and refresh, insights with missing and refused metrics, rate-limit and bad-token handling, import with missing metrics (no videos left in the temp folder), re-import, the performance index with an outlier, the DNA edge cases, the fixture guard and the disconnect deletion. The database tests use a throwaway database (`IG_TEST_DATABASE_URL`, default the local stack on port 55436) and skip when none is reachable.

### What a real account still has to confirm

Nothing here has talked to Instagram: the hackathon app's credentials were not in `backend/.env`. Still to check with a real Business or Creator account: that the redirect URI is accepted, that `/me/media` returns `media_product_type` and `caption` through `graph.instagram.com`, which insights the account and app status really return, and that `media_url` downloads work.

## 6. Privacy

The Instagram access token is encrypted at rest (section 5). 
What we read: the connected account's own reels (permalink, caption, time, counts) and their insights. What we store: those numbers, six frames' worth of **derived** fingerprints and the DNA summary; never the videos or the frames. Reel videos are downloaded to a temporary folder only to cut about six frames, and the file is deleted straight after. Frames of those reels are sent to the Meta model API (Muse Spark) for the fingerprint. Disconnect deletes the token and every derived row (`ig_account`, `ig_reel`, `ig_reel_insight`, `creator_dna`).

No scraped research data and no other creator's reels are used anywhere in this feature.

## 7. Meta app setup checklist (for the founder)

Use the **hackathon** Meta app only, never the production one.

1. <https://developers.facebook.com> > My Apps > **Create app**, type **Business**. Name it for the hackathon.
2. In the app dashboard add the product **Instagram** and open **API setup with Instagram login**. Copy the **Instagram app ID** and **Instagram app secret** shown there (they are not the top-level app ID and secret) into `backend/.env` as `INSTAGRAM_APP_ID` and `INSTAGRAM_APP_SECRET`.
3. In **Business login settings** add the **OAuth redirect URIs**. They must be HTTPS and must match `INSTAGRAM_REDIRECT_URI` character for character (watch the trailing slash):
   * local: your tunnel, `https://<tunnel-host>/auth/instagram/callback` (see section 4), pointing at the backend on port 8778 (or 8000);
   * deployed: `https://<backend-host>/auth/instagram/callback`.
4. Check that the permissions `instagram_business_basic` and `instagram_business_manage_insights` are listed for the app. Standard access is enough for accounts that have a role on the app; no App Review is needed for the demo.
5. **App roles > Roles > Instagram Testers > Add**: enter the Instagram username of the account to connect. Then, **in the Instagram app** (or instagram.com): Settings > Website permissions (or Apps and websites) > **Tester invites** > accept. Until the invite is accepted the login fails. The app can stay in Development mode.
6. The Instagram account must be **Business or Creator** (Settings > Account type and tools > Switch to professional account). A personal account cannot connect.
7. Environment (names only; see `backend/.env.example`):

| Variable | Meaning |
|---|---|
| `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET` | from step 2 |
| `INSTAGRAM_REDIRECT_URI` | exact HTTPS callback from step 3 |
| `INSTAGRAM_FRONTEND_URL` | where the browser returns, e.g. `http://localhost:5177` (falls back to `FRONTEND_URL`) |
| `INSTAGRAM_FIXTURE` | `1` only on a developer machine for fake data; leave `0` or unset otherwise |
| `META_DNA_MODEL`, `DNA_COST_CAP_USD` | fingerprint/DNA model (default `muse-spark-1.3`) and the per-import cost cap (default 0.60) |
| `TOKEN_ENCRYPTION_KEY` | Fernet key that encrypts the Instagram token in the database. **Required in production** (without it the token is not stored). Create one with `python -m app.studio.instagram.tokencrypt generate`; keep it in the secret store, not in git. Losing it means creators have to connect again |
| `COACH_PERSONALISE` | `0` switches personalisation off; unset or `1` leaves it on for users with a DNA |
| `VITE_SHOW_QWEN_CARD` (web) | `1` shows the old Qwen estimate in the viewer-behaviour card for testing |

8. Run `alembic upgrade head` on the database (adds `ig_reel.analysis_level` and encrypts any token already stored; set `TOKEN_ENCRYPTION_KEY` first).
9. For a hosted site, make sure `/instagram/*` is proxied to the backend like `/auth/*` (`vercel.json`, `netlify.toml` and `public/_redirects` now carry it; `web/scripts/set_backend_url.sh` rewrites it too).
10. Test: sign in with email or Google, open Settings > Instagram and Creator DNA (or `/creator-dna`), read the consent screen, continue to Instagram, accept, choose how many reels, Import.

Going beyond the people who have a role on the app (any creator) needs Advanced access: App Review for both permissions plus Business Verification. That is outside this hackathon build.

## 7b. Deploying: the exact checklist

Environment of the **backend** (names only; values from your secret store):

| Variable | Value |
|---|---|
| `APP_ENV` | `production` |
| `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET` | the **Instagram** app ID and secret of the hackathon Meta app (API setup with Instagram login), not the top-level ones |
| `INSTAGRAM_REDIRECT_URI` | `https://<your-deployed-backend-host>/auth/instagram/callback` (HTTPS, no trailing slash unless you register one) |
| `INSTAGRAM_FRONTEND_URL` | `https://<your-deployed-website>` (where the browser returns after Instagram) |
| `TOKEN_ENCRYPTION_KEY` | **required in production**: the server refuses to start without a valid key (and refuses `INSTAGRAM_FIXTURE=1`). `python -m app.studio.instagram.tokencrypt generate`; keep it in the secret store; losing it only means creators connect again |
| `INSTAGRAM_FIXTURE` | `0` (or unset) |
| `META_API_KEY` | needed for the fingerprints, the DNA and Voice Transcribe of the hook words (and the analysis itself) |
| `META_DNA_MODEL`, `DNA_COST_CAP_USD`, `COACH_PERSONALISE` | optional (defaults `muse-spark-1.3`, 0.60, on) |

Web: `/instagram/*` must be proxied to the backend like `/auth/*` (already in `vercel.json`, `netlify.toml`, `public/_redirects`; `web/scripts/set_backend_url.sh` rewrites it). Database: `alembic upgrade head` (set `TOKEN_ENCRYPTION_KEY` first). The backend host needs `ffmpeg` and `ffprobe` (frames and hook words).

Meta console, to add the deployed redirect URI (hackathon app only):

1. <https://developers.facebook.com> > My Apps > the hackathon app > **Instagram** > **API setup with Instagram login**.
2. Open **3. Set up Instagram business login** > **Business login settings**.
3. Under **OAuth redirect URIs** press **Add URI**, paste exactly the value of `INSTAGRAM_REDIRECT_URI`, press **Save**. It must match character for character: scheme, host, path, and any trailing slash.
4. Keep the app in **Development** mode for the demo: only accounts with a role on the app can connect. **App roles > Roles > Instagram Testers > Add** the demo account, and accept the invite in Instagram (Settings > Apps and websites > Tester invites). Going live for any creator needs App Review of both permissions and Business Verification, plus the Deauthorize callback and Data deletion request URLs, which this build does not implement (a creator removes Prysm in Prysm with Disconnect, which deletes everything; removing it in Instagram does not notify us).
5. Check the deployment: open `/creator-dna`, read the consent screen, connect, import 3 reels, and look at the DNA, the reel cards and "Did my fix work?".

## 8. Cost of an import (measured in fixture mode with Muse Spark 1.3)

12 reels with 6 frames each cost **$0.096** in total (13 model calls: 12 fingerprints at about $0.0073 plus one summary at about $0.01). That gives about **$0.08 for 10 reels** and about **$0.23 for 30 reels** (about 31 calls; the 30-reel figure is extrapolated from the 12-reel run, which took about a minute). The cap of $0.60 per import is never reached at 30. A later analysis adds one small text call (about $0.003 to $0.01) for the comparison block, and a longer prompt of a few hundred tokens to the two scoring calls.

**Hook words** add about $0.0006 per reel that has a video (Voice Transcribe, 12 seconds), so about $0.006 for 10 reels and $0.018 for 30. **Grounding adds little.** A clean DNA answer needs no extra call. When a claim is rejected, the single repair call costs about one summary call (about $0.01): an 8-reel fixture import with a repair cost $0.105 (10 calls) against $0.074 (9 calls) without. In a personalised analysis the rewrite call, only when a sentence is rejected, cost about $0.007, next to the comparison call (about $0.004) and the analysis itself (about $0.27 in the test run). The cost cap (`DNA_COST_CAP_USD`) also gates the repair call.

## 9. Sources (read 2026-11-04)

* Instagram API with Instagram Login: <https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login>
* Business Login for Instagram: <https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/business-login>
* IG Media reference and insights: <https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media> and `.../reference/instagram-media/insights`
* Platform overview (rate limits, access levels): <https://developers.facebook.com/documentation/instagram-platform/overview>
