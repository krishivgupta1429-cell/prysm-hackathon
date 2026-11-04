# Meta relay: reach Meta from a machine where the key works

Built during the Meta Global AI Developer Hackathon (new work).

**Why.** Meta answers the key with an empty model list and `404 model_not_found` when the request comes from some cloud servers (Railway), but accepts it from a home connection. The relay (`scripts/meta_relay.py`) runs on your Mac and forwards the deployed backend's Meta calls from there.

```
Railway backend  ->  https://<tunnel>.trycloudflare.com/v1  ->  your Mac (relay, holds the Meta key)  ->  https://api.meta.ai/v1
```

The relay forwards `/v1/*` unchanged (method, query, body, streaming replies, status codes). It adds `Authorization` from `META_API_KEY` in `backend/.env` only when the caller sent none, and refuses (401) every request without the header `X-Relay-Secret: <RELAY_SECRET>`. It logs only `METHOD /path -> status`; no keys, headers, bodies or query strings.

## 1. Run it on the Mac

```bash
cd ~/prysm-hackathon/backend
export RELAY_SECRET=$(openssl rand -hex 32)      # keep this value: Railway needs the same one
echo "$RELAY_SECRET"                             # copy it now (it is a secret of yours, do not share it)
.venv/bin/python ../scripts/meta_relay.py --port 8790
```

It listens on `127.0.0.1:8790`. Check: `curl -s -o /dev/null -w "%{http_code}\n" localhost:8790/v1/models` prints `401` (no secret), and with `-H "X-Relay-Secret: $RELAY_SECRET"` it lists the models.

## 2. Expose it with a Cloudflare quick tunnel

```bash
cloudflared tunnel --url http://localhost:8790
```

It prints a URL like `https://something-random.trycloudflare.com`. That is your tunnel URL. A quick tunnel gets a **new URL every time it starts**, so when you restart `cloudflared` you must update `META_BASE_URL` on Railway. The Mac must stay awake with both the relay and `cloudflared` running while people use the site.

## 3. Railway variables (backend service)

| Name | Value |
|---|---|
| `META_BASE_URL` | `<tunnel url>/v1`, for example `https://something-random.trycloudflare.com/v1` |
| `RELAY_SECRET` | the same value as on the Mac |
| `AI_PROVIDER` | `meta` (unchanged) |

`META_API_KEY` is **not needed** on Railway while `META_BASE_URL` points at a relay (the relay holds the key; production start-up then asks for `RELAY_SECRET` instead). If it is set anyway, it is sent along and wins over the relay's key. Leave `META_MODEL`, `META_VISION_MODEL`, `META_SCORING_MODEL`, `META_CHAT_MODEL` and `META_MODEL_MAP` unset (see `docs/deploy-checklist.md`).

After redeploying, the Railway log shows `[Models] startup check: vision=muse-spark-1.3, ...` when the relay works, and a `[Models] WARNING` line naming the problem when it does not.

## How the backend uses it

* `META_BASE_URL` (default `https://api.meta.ai/v1`) is read by `backend/app/studio/config.py`; every Meta call (chat, streaming, files, speech, the start-up model check) goes through it.
* When it points anywhere else, the backend sends `X-Relay-Secret: <RELAY_SECRET>` on each call. The secret is **never** sent to Meta itself (only to a custom `META_BASE_URL`).

## Limits

* Quick tunnels have no uptime promise and the URL changes on restart. For anything lasting, use a named Cloudflare tunnel or a small always-on server.
* Everything the site asks of Meta passes through your Mac and home connection: it adds a hop (a few hundred ms) and stops working when the Mac sleeps.
* The relay forwards only to `https://api.meta.ai/v1`. Other services (Groq, Gemini, Modal) are not relayed.
* Anyone who has the tunnel URL **and** the secret can spend your Meta quota, so keep the secret private.
