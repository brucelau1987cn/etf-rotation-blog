#!/usr/bin/env bash
# One-shot production release for etf-rotation-blog + edge-quote-api dual-live.
#
# Default flow:
#   1) edge-quote-api: deploy Worker secondary + verify dual-live
#   2) blog: sync:quote -> deploy:pages
#   3) blog: verify:pages (includes Pages + Worker probes)
#
# Usage:
#   bash scripts/release_dual_live.sh
#   bash scripts/release_dual_live.sh --skip-worker
#   bash scripts/release_dual_live.sh --verify-only
#   bash scripts/release_dual_live.sh --skip-tests
set -euo pipefail

BLOG_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EDGE_ROOT="${EDGE_QUOTE_ROOT:-$(cd "$BLOG_ROOT/../edge-quote-api" 2>/dev/null && pwd || true)}"
PAGES_ENV="${CLOUDFLARE_PAGES_ENV:-$HOME/.hermes/credentials/cloudflare-pages.env}"
GLOBAL_ENV="${CLOUDFLARE_GLOBAL_ENV:-$HOME/.hermes/credentials/cloudflare-global.env}"

SKIP_WORKER=0
VERIFY_ONLY=0
SKIP_TESTS=0

for arg in "$@"; do
  case "$arg" in
    --skip-worker) SKIP_WORKER=1 ;;
    --verify-only) VERIFY_ONLY=1 ;;
    --skip-tests) SKIP_TESTS=1 ;;
    -h|--help)
      sed -n '1,20p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "FAIL missing command: $1" >&2
    exit 1
  }
}

need_cmd npm
need_cmd npx
need_cmd curl
need_cmd bash

echo "== release dual-live"
echo "blog: $BLOG_ROOT"
echo "edge: ${EDGE_ROOT:-missing}"
echo "skip_worker=$SKIP_WORKER verify_only=$VERIFY_ONLY skip_tests=$SKIP_TESTS"

if (( VERIFY_ONLY == 0 )); then
  if (( SKIP_WORKER == 0 )); then
    if [[ -z "${EDGE_ROOT:-}" || ! -d "$EDGE_ROOT" ]]; then
      echo "FAIL edge-quote-api root not found (set EDGE_QUOTE_ROOT)" >&2
      exit 1
    fi
    if [[ -f "$GLOBAL_ENV" ]]; then
      # shellcheck disable=SC1090
      set -a; source "$GLOBAL_ENV"; set +a
      # Global key path for Workers; avoid Pages token overriding.
      if [[ -n "${CLOUDFLARE_API_KEY:-}" && -n "${CLOUDFLARE_EMAIL:-}" ]]; then
        unset CLOUDFLARE_API_TOKEN || true
      fi
    fi
    echo
    echo "== [1/4] edge dual-live deploy"
    (
      cd "$EDGE_ROOT"
      if (( SKIP_TESTS == 0 )); then
        OFFLINE=1 npm test
      fi
      npm run deploy:dual
    )
  else
    echo
    echo "== [1/4] skip worker deploy (--skip-worker)"
  fi

  echo
  echo "== [2/4] sync quote handler into blog Pages Functions"
  (
    cd "$BLOG_ROOT"
    npm run sync:quote
  )

  echo
  echo "== [3/4] build + deploy Cloudflare Pages"
  if [[ ! -f "$PAGES_ENV" ]]; then
    echo "FAIL missing pages credentials: $PAGES_ENV" >&2
    exit 1
  fi
  (
    cd "$BLOG_ROOT"
    # shellcheck disable=SC1090
    set -a; source "$PAGES_ENV"; set +a
    if (( SKIP_TESTS == 0 )); then
      npm test
    fi
    npm run deploy:pages
  )
else
  echo
  echo "== verify-only: skip deploy steps"
fi

echo
echo "== [4/4] production probe (Pages + Worker)"
(
  cd "$BLOG_ROOT"
  if (( SKIP_WORKER == 1 )); then
    SKIP_WORKER_PROBE=1 npm run verify:pages
  else
    npm run verify:dual
  fi
)

echo
echo "Release complete."
echo "  primary: https://etf.peekabo.cc/api/public/v1/quote"
echo "  secondary: https://edge-quote-api.brucelau1987.workers.dev"
echo "  site: https://etf.peekabo.cc/"
