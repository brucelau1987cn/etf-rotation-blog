#!/usr/bin/env bash
# Probe production etf-rotation-blog HTML for required live-quote markers.
# Usage:
#   ./scripts/verify_pages_deploy.sh
#   BASE_URL=https://etf.peekabo.cc ./scripts/verify_pages_deploy.sh
set -euo pipefail

BASE_URL="${BASE_URL:-https://etf.peekabo.cc}"
TS="$(date +%s)"
fail=0

check_page() {
  local path="$1"
  shift
  local url="${BASE_URL}${path}?t=${TS}"
  local html
  html="$(curl -fsSL "$url")"
  echo "== ${url}"
  for marker in "$@"; do
    if printf '%s' "$html" | grep -Fq "$marker"; then
      echo "  OK  marker: $marker"
    else
      echo "  FAIL marker missing: $marker"
      fail=1
    fi
  done
}

check_page "/a-rolling/" \
  "normalize-quote-payload.js" \
  "EtfQuote" \
  "/api/public/v1/quote"

check_page "/a-compass/" \
  "normalize-quote-payload.js" \
  "EtfQuote" \
  "/api/public/v1/quote"

check_page "/" \
  "normalize-quote-payload.js" \
  "EtfQuote"

# us-compass ships adapter as hashed /_astro module; probe API path + live status id.
check_page "/us-compass/" \
  "us-live-status" \
  "/api/public/v1/quote"

quote_json="$(curl -fsSL "${BASE_URL}/api/public/v1/quote?symbol=600021&exchange=SSE&t=${TS}")"
if printf '%s' "$quote_json" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
  echo "OK  quote API status=ok"
else
  echo "FAIL quote API response: ${quote_json:0:200}"
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo
  echo "Production probe failed. If git push already happened, run:"
  echo "  npm run build"
  echo "  source ~/.hermes/credentials/cloudflare-pages.env"
  echo "  npx wrangler pages deploy dist --project-name etf-rotation-blog --commit-dirty=true"
  exit 1
fi

echo
echo "All production markers OK."
