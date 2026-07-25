#!/usr/bin/env bash
# Probe production etf-rotation-blog HTML for required live-quote markers.
# Usage:
#   ./scripts/verify_pages_deploy.sh
#   BASE_URL=https://etf.peekabo.cc ./scripts/verify_pages_deploy.sh
set -euo pipefail

BASE_URL="${BASE_URL:-https://etf.peekabo.cc}"
TS="$(date +%s)"
fail=0

fetch() {
  local url="$1"
  curl -fsSL \
    -H 'Cache-Control: no-cache' \
    -H 'Pragma: no-cache' \
    -H 'User-Agent: HermesPagesProbe/1.0' \
    "$url"
}

check_page() {
  local path="$1"
  shift
  local url="${BASE_URL}${path}?t=${TS}"
  local html
  html="$(fetch "$url")"
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

check_asset() {
  local path="$1"
  local url="${BASE_URL}${path}?t=${TS}"
  local body
  body="$(fetch "$url")"
  echo "== asset ${url}"
  if printf '%s' "$body" | grep -Fq "$2"; then
    echo "  OK  asset contains: $2"
  else
    echo "  FAIL asset missing: $2"
    fail=1
  fi
}

# Shared browser assets (source of truth for adapter / poll helper)
check_asset "/js/normalize-quote-payload.js" "EtfQuote"
check_asset "/js/etf-live-poll.js" "startLivePoll"

# Page HTML only needs to reference the shared scripts + page-specific hooks.
check_page "/a-rolling/" \
  "/js/normalize-quote-payload.js" \
  "/js/etf-live-poll.js" \
  "/api/public/v1/quote"

check_page "/a-compass/" \
  "/js/normalize-quote-payload.js" \
  "/js/etf-live-poll.js" \
  "/api/public/v1/quote"

check_page "/" \
  "/js/normalize-quote-payload.js" \
  "/js/etf-live-poll.js"

check_page "/a-momentum/" \
  "/js/normalize-quote-payload.js" \
  "/js/etf-live-poll.js" \
  "/api/public/v1/quote"

check_page "/futures-compass/" \
  "/js/normalize-quote-payload.js" \
  "/js/etf-live-poll.js" \
  "nf_AU0" \
  "/data/futures-compass.json"

# us-compass uses shared IIFE adapter + live status markers.
check_page "/us-compass/" \
  "us-live-status" \
  "data-us-live-card" \
  "/js/normalize-quote-payload.js" \
  "/js/etf-live-poll.js" \
  "/api/public/v1/quote"

quote_json="$(fetch "${BASE_URL}/api/public/v1/quote?symbol=600021&exchange=SSE&t=${TS}")"
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
