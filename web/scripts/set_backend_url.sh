#!/usr/bin/env bash
# Built during the Meta Global AI Developer Hackathon (new work).
# Points the host's API proxy rewrites (vercel.json, netlify.toml, public/_redirects) at your backend.
# Usage: scripts/set_backend_url.sh https://your-backend.example.com
set -euo pipefail
url="${1:-}"
case "$url" in
  http://*|https://*) ;;
  *) echo "usage: $0 <backend url, e.g. https://api.example.com>" >&2; exit 1 ;;
esac
url="${url%/}"
cd "$(dirname "$0")/.."
# Only the proxy destinations carry a host; the SPA fallback to /index.html has none and is left alone.
sed -E -i.bak "/destination|^[[:space:]]*to = |^\/[a-z]/ s#https?://[^/\" ]+#${url}#g" vercel.json netlify.toml public/_redirects
rm -f vercel.json.bak netlify.toml.bak public/_redirects.bak
echo "Backend URL set to ${url} in vercel.json, netlify.toml and public/_redirects."
echo "For direct uploads past the host's body limit also set VITE_UPLOAD_URL=${url} when building."
