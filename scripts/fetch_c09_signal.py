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

BASE = "https://nasa-drain-arthritis-figured.trycloudflare.com"
LIVE_NEWS = f"{BASE}/api/live-news"
CURRENT_SIGNAL = f"{BASE}/api/current-signal"
TIMEOUT = 8  # seconds; C09 is on a tunnel, keep it short
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "public" / "data" / "c09-signal.json"


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

    # If both endpoints fail, do not overwrite last-known-good; just warn and exit 0.
    if news_raw is None and sig_raw is None:
        print("[c09] both endpoints down; keeping previous snapshot", file=sys.stderr)
        return 0

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
        "generated_at": (sig_raw or news_raw or {}).get("generated_at", ""),
        "fetched_at": fetched_at,
        "status": status,
        "important_news": important,
        "sector_stars": sector_stars,
        "top3": top3,
        "meta": {
            "news_window_start": (news_raw or {}).get("window_start", ""),
            "news_window_end": (news_raw or {}).get("window_end", ""),
            "news_ttl_seconds": (news_raw or {}).get("ttl_seconds", 900),
            "next_refresh_at": (news_raw or {}).get("next_refresh_at", ""),
            "pool_size": (sig_raw or {}).get("pool_size", 80),
            "mode": (sig_raw or {}).get("mode", ""),
        },
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically: write to .tmp, then rename, so cron readers never see partial JSON.
    tmp = OUTPUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUTPUT)

    # Compact one-line summary to stdout for cron logs
    print(
        f"[c09] trade_date={trade_date} status={status} "
        f"s_news={len(important)} sectors={len(sector_stars)} top3={len(top3)} "
        f"saved={OUTPUT.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
