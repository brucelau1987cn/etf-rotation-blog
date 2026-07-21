#!/usr/bin/env python3
"""Refresh the public futures compass fallback snapshot from Sina/AkShare."""
from __future__ import annotations

import json
from futures_compass_data import PUBLIC_SNAPSHOT, atomic_json, fetch_daily_bars, fetch_realtime


def main() -> int:
    daily = fetch_daily_bars()
    payload = fetch_realtime()
    atomic_json(PUBLIC_SNAPSHOT, payload)
    print(json.dumps({
        "status": "ok", "path": "public/data/futures-compass.json",
        "count": payload.get("count"), "generated_at": payload.get("generated_at"),
        "source": payload.get("source"), "daily_rows": daily.get("rows"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
