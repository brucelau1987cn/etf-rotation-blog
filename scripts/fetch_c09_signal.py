#!/usr/bin/env python3
"""Fetch C09 竞价狙击实验室 signals and persist them as JSON.

Endpoints (as of 2026-06-07):
  GET https://nasa-drain-arthritis-figured.trycloudflare.com/api/live-news?ts=<seconds>
  GET https://nasa-drain-arthritis-figured.trycloudflare.com/api/current-signal?ts=<seconds>

Source boundary:
  - This script only reads from the C09 trycloudflare tunnel.
  - C09 is a temporary Cloudflare Quick Tunnel, lifetime ~24-72h.
  - On 4xx/5xx or connection errors, exit 0 with last-known-good JSON untouched
    (do not delete the previous snapshot) and emit a one-line stderr warning.

Output (deterministic):
  public/data/c09-signal.json
    {
      "trade_date", "generated_at", "fetched_at",
      "important_news": [...],        # S/A-grade news
      "sector_stars": [...],          # 6-dim sector ranking
      "top3": [...],                  # ORION TOP3 (empty on non-trading days)
      "status",                      # "ok" | "no_signal" | "stale"
      "source": "c09-trycloudflare"
    }
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Allow override via env var so cron jobs / tests can point at a fresh tunnel
# without code changes. Fall back to the original trycloudflare host.
BASE = os.environ.get("C09_BASE", "https://nasa-drain-arthritis-figured.trycloudflare.com")
# Staleness threshold: trycloudflare tunnels typically live 24-72h, but if we
# can't fetch for >6h the URL is likely dead. Warn loudly so cron readers can
# flag the channel in Telegram.
STALE_HOURS = 6
# When both endpoints are down AND the last snapshot is older than this, exit 1
# so cron error-out and trigger a Telegram alert (one-line stderr is silent).
STALE_FAIL_HOURS = 18
LIVE_NEWS = f"{BASE}/api/live-news"
CURRENT_SIGNAL = f"{BASE}/api/current-signal"
TIMEOUT = 8  # seconds; C09 is on a tunnel, keep it short
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "public" / "data" / "c09-signal.json"


def _ensure_tz(ts: str) -> str:
    """The C09 lab returns naive `YYYY-MM-DD HH:MM:SS` timestamps that we
    know are local +08:00 wall-clock. Append the offset so JS `new Date(...)`
    inside the Astro build (running on UTC CF Pages containers) parses them
    correctly. Empty / already-suffixed strings pass through."""
    if not ts or not isinstance(ts, str):
        return ts
    s = ts.strip()
    # If it already has a +HH:MM / -HH:MM / Z suffix, leave it alone.
    if len(s) >= 6 and s[-6] in "+-" and s[-3] == ":":
        return s
    if s.endswith("Z"):
        return s
    # Otherwise, treat as +08:00 wall clock
    return s + "+0800"


def _get_json(url: str, retries: int = 2) -> dict[str, Any] | None:
    """GET url and return JSON; None on any failure (with stderr warning)."""
    last_err = ""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "etf-blog-c09/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                if resp.status != 200:
                    last_err = f"HTTP {resp.status}"
                    time.sleep(0.5)
                    continue
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # urllib errors, timeouts, JSON errors
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(0.5)
    print(f"[c09] WARN fetch failed: {url} ({last_err})", file=sys.stderr)
    return None


def _filter_a_share_s_news(important_news: list[dict]) -> list[dict]:
    """Keep S/A-grade news that is A-share relevant; sort by rating_rank desc."""
    if not important_news:
        return []
    keep = [
        n for n in important_news
        if n.get("a_share_relevance") and n.get("rating") in ("S", "A")
    ]
    # rating_rank: 5 (top) ... 1 (low). Higher is more important.
    keep.sort(key=lambda n: (-int(n.get("rating_rank") or 0), n.get("datetime", "")))
    return keep


def _normalize_sector_stars(raw: dict | None) -> list[dict]:
    """Pull just the items array from sector_stars payload."""
    if not raw or not isinstance(raw, dict):
        return []
    items = raw.get("items") or []
    # Stable sort by score desc, then name asc for deterministic output
    items_sorted = sorted(items, key=lambda x: (-float(x.get("score") or 0), x.get("name", "")))
    return items_sorted


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc).astimezone()
    fetched_at = now.strftime("%Y-%m-%d %H:%M:%S%z")

    news_raw = _get_json(f"{LIVE_NEWS}?ts=0")
    sig_raw = _get_json(f"{CURRENT_SIGNAL}?ts=0")

    # --- Staleness tracking ----------------------------------------------
    # If we just refreshed, we know the snapshot is fresh. If we couldn't
    # reach C09, read the previous snapshot to compute its age.
    prev_age_hours: float | None = None
    if OUTPUT.exists():
        try:
            prev = json.loads(OUTPUT.read_text(encoding="utf-8"))
            prev_fa = (prev.get("fetched_at") or "").replace("", "")
            if prev_fa:
                # Accept "YYYY-MM-DD HH:MM:SS+0800" or ISO
                try:
                    prev_dt = dt.datetime.fromisoformat(prev_fa)
                except ValueError:
                    prev_dt = dt.datetime.strptime(prev_fa[:19], "%Y-%m-%d %H:%M:%S")
                if prev_dt.tzinfo is None:
                    prev_dt = prev_dt.replace(tzinfo=now.tzinfo)
                prev_age_hours = (now - prev_dt).total_seconds() / 3600.0
        except Exception:
            prev_age_hours = None

    # If both endpoints fail, do not overwrite last-known-good; just warn and exit 0.
    if news_raw is None and sig_raw is None:
        age_msg = f"prev_age={prev_age_hours:.1f}h" if prev_age_hours is not None else "no_prev_snapshot"
        print(f"[c09] WARN both endpoints down; keeping previous snapshot ({age_msg})", file=sys.stderr)
        # Hard fail if previous snapshot is also too old — escalate to cron error.
        if prev_age_hours is not None and prev_age_hours > STALE_FAIL_HOURS:
            print(
                f"[c09] ERROR snapshot stale >{STALE_FAIL_HOURS}h ({prev_age_hours:.1f}h); "
                f"C09 tunnel likely dead, needs a new trycloudflare URL.",
                file=sys.stderr,
            )
            return 1
        return 0

    # Soft staleness warning even on success, so an unattended agent notices.
    if prev_age_hours is not None and prev_age_hours > STALE_HOURS:
        print(
            f"[c09] NOTE prior snapshot was {prev_age_hours:.1f}h old (>{STALE_HOURS}h) "
            f"before this refresh — tunnel may have been flaky.",
            file=sys.stderr,
        )

    important = _filter_a_share_s_news((news_raw or {}).get("important_news", []))
    sector_stars = _normalize_sector_stars((news_raw or {}).get("sector_stars"))
    top3 = (sig_raw or {}).get("top3") or []

    # Trade date: prefer signal payload (more authoritative for the day),
    # fall back to news payload, then today.
    trade_date = (
        (sig_raw or {}).get("trade_date")
        or (news_raw or {}).get("trade_date")
        or now.strftime("%Y-%m-%d")
    )

    if sig_raw is not None and sig_raw.get("status") == "no_signal":
        status = "no_signal"
    elif sig_raw is None:
        status = "stale"
    else:
        status = "ok"

    payload = {
        "source": "c09-trycloudflare",
        "trade_date": trade_date,
        "generated_at": _ensure_tz((sig_raw or news_raw or {}).get("generated_at", "")),
        "fetched_at": fetched_at,
        "status": status,
        "important_news": important,
        "sector_stars": sector_stars,
        "top3": top3,
        "meta": {
            "news_window_start": (news_raw or {}).get("window_start", ""),
            "news_window_end": (news_raw or {}).get("window_end", ""),
            "news_ttl_seconds": (news_raw or {}).get("ttl_seconds", 900),
            "next_refresh_at": _ensure_tz((news_raw or {}).get("next_refresh_at", "")),
            "pool_size": (sig_raw or {}).get("pool_size", 80),
            "mode": (sig_raw or {}).get("mode", ""),
        },
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically: write to .tmp, then rename, so cron readers never see partial JSON.
    tmp = OUTPUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUTPUT)

    # Also mirror to src/data/ for Astro static import (build-time embedding).
    # The c09-pulse.astro page imports this directly so the snapshot is baked
    # into the page HTML at build time, side-stepping `process.cwd()` quirks
    # in Cloudflare Pages build containers.
    src_mirror = ROOT / "src" / "data" / "c09-signal.json"
    src_mirror.parent.mkdir(parents=True, exist_ok=True)
    src_mirror.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Compact one-line summary to stdout for cron logs
    print(
        f"[c09] trade_date={trade_date} status={status} "
        f"s_news={len(important)} sectors={len(sector_stars)} top3={len(top3)} "
        f"saved={OUTPUT.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
