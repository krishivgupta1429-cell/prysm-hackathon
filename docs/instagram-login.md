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

## 5. Privacy

What we read: the connected account's own reels (permalink, caption, time, counts) and their insights. What we store: those numbers, six frames' worth of **derived** fingerprints and the DNA summary; never the videos or the frames. Reel videos are downloaded to a temporary folder only to cut about six frames, and the file is deleted straight after. Frames of those reels are sent to the Meta model API (Muse Spark) for the fingerprint. Disconnect deletes the token and every derived row (`ig_account`, `ig_reel`, `ig_reel_insight`, `creator_dna`).

No scraped research data and no other creator's reels are used anywhere in this feature.

## 6. Sources (read 2026-11-04)

* Instagram API with Instagram Login: <https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login>
* Business Login for Instagram: <https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/business-login>
* IG Media reference and insights: <https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media> and `.../reference/instagram-media/insights`
* Platform overview (rate limits, access levels): <https://developers.facebook.com/documentation/instagram-platform/overview>
