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
check_asset "/js/market-clock.js" "data-market-clock"
check_asset "/js/site-a11y.js" "main-content"
check_asset "/js/token-app.js" "minimax"
check_asset "/js/login-app.js" "change-password"
check_asset "/js/lab-app.js" "a-share-research-audit"
check_asset "/js/blog-post-app.js" "article-toc"

# Cache policy: versioned public JS should be long-lived/immutable.
# Prefer a versioned URL (how pages actually load assets) and retry briefly
# because custom domain edge objects can lag a few seconds after deploy.
js_cache_ok=0
js_immutable_ok=0
attempt=1
while (( attempt <= RETRIES )); do
  js_headers="$(curl -fsSIL -H 'User-Agent: Hermes-Deploy-Probe' -H 'Cache-Control: no-cache' "${BASE_URL}/js/normalize-quote-payload.js?v=${TS}" 2>/dev/null || true)"
  lower="$(printf '%s' "$js_headers" | tr '[:upper:]' '[:lower:]')"
  if printf '%s' "$lower" | grep -Eq 'cache-control:.*max-age=31536000'; then js_cache_ok=1; fi
  if printf '%s' "$lower" | grep -Eq 'cache-control:.*immutable'; then js_immutable_ok=1; fi
  if (( js_cache_ok == 1 && js_immutable_ok == 1 )); then break; fi
  sleep "$RETRY_SLEEP"
  attempt=$((attempt + 1))
done
if (( js_cache_ok == 1 )); then
  echo "OK  /js/* Cache-Control long-lived"
else
  echo "FAIL /js/* Cache-Control missing long max-age"
  printf '%s\n' "$js_headers" | sed -n '1,20p'
  fail=1
fi
if (( js_immutable_ok == 1 )); then
  echo "OK  /js/* Cache-Control immutable"
else
  echo "FAIL /js/* Cache-Control missing immutable"
  fail=1
fi

html_headers="$(curl -fsSIL -H 'User-Agent: Hermes-Deploy-Probe' -H 'Cache-Control: no-cache' "${BASE_URL}/?t=${TS}" 2>/dev/null || true)"
if printf '%s' "$html_headers" | tr '[:upper:]' '[:lower:]' | grep -Eq 'cache-control:.*(max-age=0|no-cache|must-revalidate)'; then
  echo "OK  HTML short/revalidate Cache-Control"
else
  echo "WARN HTML Cache-Control not short; got:"
  printf '%s\n' "$html_headers" | tr '[:upper:]' '[:lower:]' | grep -i 'cache-control' || true
fi

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

check_page "/token/" \
  "/js/token-app.js" \
  "data-token-dashboard" \
  "data-api-base" \
  "btn-refresh"

check_page "/login/" \
  "/js/login-app.js" \
  "login-form" \
  "change-form"

check_page "/lab/" \
  "/js/lab-app.js" \
  "upload-form" \
  "audit-content" \
  "kronos-content"

# Stable historical article using BlogPost layout client.
check_page "/blog/2026-05-31-etf-rotation-framework/" \
  "/js/blog-post-app.js" \
  "article-toc" \
  "article-content"

quote_ok=0
quote_headers=""
attempt=1
while (( attempt <= RETRIES )); do
  quote_json="$(fetch "${BASE_URL}/api/public/v1/quote?symbol=600021&exchange=SSE&t=${TS}" || true)"
  quote_headers="$(curl -fsSIL -H 'User-Agent: Hermes-Deploy-Probe' -H 'Cache-Control: no-cache' \
    "${BASE_URL}/api/public/v1/quote?symbol=600021&exchange=SSE&t=${TS}" 2>/dev/null || true)"
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

# Prefer GET for Functions header reliability; fall back to prior HEAD dump.
quote_hdr_body="$(curl -fsSI -X GET -H 'User-Agent: Hermes-Deploy-Probe' -H 'Cache-Control: no-cache' \
  -D - -o /dev/null "${BASE_URL}/api/public/v1/quote?symbol=600021&exchange=SSE&t=${TS}" 2>/dev/null || true)"
if [[ -z "$quote_hdr_body" ]]; then
  quote_hdr_body="$quote_headers"
fi
quote_hdr_lower="$(printf '%s' "$quote_hdr_body" | tr '[:upper:]' '[:lower:]')"

if printf '%s' "$quote_hdr_lower" | grep -Eq 'x-quote-cache:[[:space:]]*(hit|miss|bypass)'; then
  echo "OK  quote header x-quote-cache present"
else
  echo "FAIL quote header x-quote-cache missing"
  printf '%s\n' "$quote_hdr_body" | sed -n '1,25p'
  fail=1
fi

ttl_line="$(printf '%s' "$quote_hdr_lower" | grep -E 'x-quote-cache-ttl-ms:' | head -n1 || true)"
ttl_ms="$(printf '%s' "$ttl_line" | sed -E 's/.*x-quote-cache-ttl-ms:[[:space:]]*([0-9]+).*/\1/' | tr -cd '0-9')"
if [[ -n "$ttl_ms" ]] && (( ttl_ms >= 1000 && ttl_ms <= 120000 )); then
  echo "OK  quote header x-quote-cache-ttl-ms=${ttl_ms}"
else
  echo "FAIL quote header x-quote-cache-ttl-ms invalid: ${ttl_line:-missing}"
  fail=1
fi

session_line="$(printf '%s' "$quote_hdr_lower" | grep -E 'x-quote-cache-session:' | head -n1 || true)"
session_val="$(printf '%s' "$session_line" | sed -E 's/.*x-quote-cache-session:[[:space:]]*([a-z_]+).*/\1/' | tr -cd 'a-z_')"
case "$session_val" in
  open_cn|open_us|open_overlap|closed|weekend)
    echo "OK  quote header x-quote-cache-session=${session_val}"
    ;;
  *)
    echo "FAIL quote header x-quote-cache-session invalid: ${session_line:-missing}"
    fail=1
    ;;
esac

if [[ -n "$session_val" && -n "$ttl_ms" ]]; then
  expected_min=4000
  expected_max=4000
  case "$session_val" in
    open_cn|open_us|open_overlap) expected_min=4000; expected_max=4000 ;;
    closed) expected_min=30000; expected_max=30000 ;;
    weekend) expected_min=60000; expected_max=60000 ;;
  esac
  if (( ttl_ms >= expected_min && ttl_ms <= expected_max )); then
    echo "OK  quote session/TTL policy match (${session_val}/${ttl_ms})"
  else
    echo "FAIL quote session/TTL mismatch: session=${session_val} ttl=${ttl_ms} expected=${expected_min}"
    fail=1
  fi
fi

# Warm + HIT recheck on a stable key (no changing t=). Cross-isolate may MISS once,
# so allow a few rapid retries within the current session TTL window.
quote_hit_ok=0
quote_hit_layer=""
quote_hit_age_ms=""
stable_quote_url="${BASE_URL}/api/public/v1/quote?symbol=600021&exchange=SSE"
# seed/warm
curl -fsS -H 'User-Agent: Hermes-Deploy-Probe' "$stable_quote_url" >/dev/null 2>&1 || true
hit_attempt=1
while (( hit_attempt <= RETRIES )); do
  hit_headers="$(curl -fsSI -X GET -H 'User-Agent: Hermes-Deploy-Probe' -D - -o /dev/null "$stable_quote_url" 2>/dev/null || true)"
  hit_lower="$(printf '%s' "$hit_headers" | tr '[:upper:]' '[:lower:]')"
  if printf '%s' "$hit_lower" | grep -Eq 'x-quote-cache:[[:space:]]*hit'; then
    quote_hit_ok=1
    quote_hit_layer="$(printf '%s' "$hit_lower" | grep -E 'x-quote-cache-layer:' | head -n1 | sed -E 's/.*x-quote-cache-layer:[[:space:]]*([a-z_]+).*/\1/' | tr -cd 'a-z_')"
    quote_hit_age_ms="$(printf '%s' "$hit_lower" | grep -E 'x-quote-cache-age-ms:' | head -n1 | sed -E 's/.*x-quote-cache-age-ms:[[:space:]]*([0-9]+).*/\1/' | tr -cd '0-9')"
    break
  fi
  sleep 1
  hit_attempt=$((hit_attempt + 1))
done
if (( quote_hit_ok == 1 )); then
  echo "OK  quote cache HIT recheck (layer=${quote_hit_layer:-unknown}, age_ms=${quote_hit_age_ms:-na})"
  if [[ -n "$quote_hit_age_ms" && -n "$ttl_ms" ]]; then
    # age must be non-negative and strictly below current session TTL.
    if (( quote_hit_age_ms >= 0 && quote_hit_age_ms < ttl_ms )); then
      echo "OK  quote cache age_ms within TTL (${quote_hit_age_ms}<${ttl_ms})"
    else
      echo "FAIL quote cache age_ms out of range: age=${quote_hit_age_ms} ttl=${ttl_ms}"
      fail=1
    fi
  else
    echo "FAIL quote cache age_ms missing on HIT"
    fail=1
  fi
  case "$quote_hit_layer" in
    edge|memory)
      echo "OK  quote cache HIT layer=${quote_hit_layer}"
      ;;
    *)
      echo "FAIL quote cache HIT layer invalid: ${quote_hit_layer:-missing}"
      fail=1
      ;;
  esac
else
  echo "FAIL quote cache HIT recheck: expected HIT within ${RETRIES} rapid retries"
  printf '%s\n' "$hit_headers" | sed -n '1,25p'
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
