#!/usr/bin/env bash
# Probe production etf-rotation-blog HTML for required live-quote markers.
# Usage:
#   ./scripts/verify_pages_deploy.sh
#   BASE_URL=https://etf.peekabo.cc ./scripts/verify_pages_deploy.sh
set -euo pipefail

BASE_URL="${BASE_URL:-https://etf.peekabo.cc}"
TS="$(date +%s)"
fail=0
RETRIES="${PROBE_RETRIES:-3}"
RETRY_SLEEP="${PROBE_RETRY_SLEEP:-2}"

fetch() {
  local url="$1"
  local attempt=1
  local body=""
  while (( attempt <= RETRIES )); do
    if body="$(curl -fsSL \
      -H 'Cache-Control: no-cache' \
      -H 'Pragma: no-cache' \
      -H 'User-Agent: HermesPagesProbe/1.0' \
      "${url}&r=${attempt}" 2>/dev/null || true)"; then
      if [[ -n "$body" ]]; then
        printf '%s' "$body"
        return 0
      fi
    fi
    sleep "$RETRY_SLEEP"
    attempt=$((attempt + 1))
  done
  return 1
}

has_all_markers() {
  local html="$1"
  shift
  local marker
  for marker in "$@"; do
    if ! printf '%s' "$html" | grep -Fq "$marker"; then
      return 1
    fi
  done
  return 0
}

check_page() {
  local path="$1"
  shift
  local url="${BASE_URL}${path}?t=${TS}"
  local html=""
  local attempt=1
  local ok=0
  echo "== ${url}"
  while (( attempt <= RETRIES )); do
    if html="$(fetch "$url")"; then
      if has_all_markers "$html" "$@"; then
        ok=1
        break
      fi
    fi
    sleep "$RETRY_SLEEP"
    attempt=$((attempt + 1))
  done

  if (( ok == 1 )); then
    for marker in "$@"; do
      echo "  OK  marker: $marker"
    done
  else
    for marker in "$@"; do
      if printf '%s' "$html" | grep -Fq "$marker"; then
        echo "  OK  marker: $marker"
      else
        echo "  FAIL marker missing: $marker"
        fail=1
      fi
    done
  fi
}

check_asset() {
  local path="$1"
  local needle="$2"
  local url="${BASE_URL}${path}?t=${TS}"
  local body=""
  local attempt=1
  echo "== asset ${url}"
  while (( attempt <= RETRIES )); do
    if body="$(fetch "$url")"; then
      if printf '%s' "$body" | grep -Fq "$needle"; then
        echo "  OK  asset contains: $needle"
        return 0
      fi
    fi
    sleep "$RETRY_SLEEP"
    attempt=$((attempt + 1))
  done
  echo "  FAIL asset missing: $needle"
  fail=1
}

# Shared browser assets (source of truth for adapter / poll helper)
check_asset "/js/normalize-quote-payload.js" "EtfQuote"
check_asset "/js/etf-live-poll.js" "startLivePoll"

# Page HTML only needs to reference the shared scripts + page-specific hooks.
check_page "/a-rolling/" \
  "/js/normalize-quote-payload.js" \
  "/js/etf-live-poll.js" \
  "/js/a-rolling-app.js" \
  "buy-cells-container" \
  "stat-current-code"

check_asset "/js/a-rolling-app.js" "fetchStockQuote"

check_page "/a-compass/" \
  "/js/normalize-quote-payload.js" \
  "/js/etf-live-poll.js" \
  "/js/a-compass-app.js" \
  "data-live-card"

check_asset "/js/a-compass-app.js" "EDGE_QUOTE_URL"

check_page "/" \
  "/js/normalize-quote-payload.js" \
  "/js/etf-live-poll.js" \
  "/js/home-live-app.js" \
  "home-live-price" \
  "?v="

check_asset "/js/home-live-app.js" "home-live-price"
check_asset "/js/home-live-app.js" "EtfLivePoll"

check_page "/a-momentum/" \
  "/js/normalize-quote-payload.js" \
  "/js/etf-live-poll.js" \
  "/js/momentum-shared.js" \
  "/js/a-momentum-app.js" \
  "etf-body" \
  "metric-regime"

check_asset "/js/a-momentum-app.js" "EDGE_QUOTE_URL"
check_asset "/js/us-momentum-app.js" "renderMatrix"

check_page "/futures-compass/" \
  "/js/normalize-quote-payload.js" \
  "/js/etf-live-poll.js" \
  "/js/futures-compass-app.js" \
  "data-code" \
  "refresh-button"

check_asset "/js/futures-compass-app.js" "SNAPSHOT_URL"
check_asset "/js/futures-compass-app.js" "EDGE_QUOTE_URL"
check_asset "/js/futures-compass-app.js" "nf_AU0"

# us-compass uses shared IIFE adapter + live status markers.
check_page "/us-compass/" \
  "us-live-status" \
  "data-us-live-card" \
  "/js/normalize-quote-payload.js" \
  "/js/etf-live-poll.js" \
  "/js/us-compass-app.js"

check_asset "/js/us-compass-app.js" "US_LIVE_URL"

check_page "/us-momentum/" \
  "/js/momentum-shared.js" \
  "/js/us-momentum-app.js" \
  "us-momentum-main" \
  "hero-pool-size"

quote_ok=0
attempt=1
while (( attempt <= RETRIES )); do
  quote_json="$(fetch "${BASE_URL}/api/public/v1/quote?symbol=600021&exchange=SSE&t=${TS}" || true)"
  if printf '%s' "$quote_json" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    quote_ok=1
    break
  fi
  sleep "$RETRY_SLEEP"
  attempt=$((attempt + 1))
done

if (( quote_ok == 1 )); then
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
