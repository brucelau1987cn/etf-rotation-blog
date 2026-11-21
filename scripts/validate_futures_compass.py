#!/usr/bin/env python3
"""Fail closed when the public futures compass snapshot is stale or incomplete."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from futures_compass_data import PUBLIC_SNAPSHOT, validate_public_snapshot, watchlist_codes
except ModuleNotFoundError:
    from scripts.futures_compass_data import PUBLIC_SNAPSHOT, validate_public_snapshot, watchlist_codes

CN = ZoneInfo("Asia/Shanghai")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=PUBLIC_SNAPSHOT)
    parser.add_argument("--now")
    parser.add_argument("--export-watchlist", type=Path, help="export the validated managed configuration")
    parser.add_argument("--watchlist", type=Path, help="authoritative watchlist JSON (array or object with items); overrides managed lookup")
    args = parser.parse_args()
    try:
        payload = json.loads(args.path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "errors": [f"cannot read futures snapshot: {exc}"]}, ensure_ascii=False))
        return 2
    now = datetime.fromisoformat(args.now).astimezone(CN) if args.now else datetime.now(CN)
    watchlist = None
    try:
        if args.watchlist:
            configured = json.loads(args.watchlist.read_text(encoding="utf-8"))
            watchlist = configured.get("items") if isinstance(configured, dict) else configured
            if not isinstance(watchlist, list) or not watchlist or any(not isinstance(row, dict) for row in watchlist):
                raise ValueError("watchlist must be a non-empty array of objects")
        else:
            from futures_compass_data import load_watchlist
            watchlist = load_watchlist()
        watchlist_codes(watchlist)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "errors": [f"cannot read futures watchlist: {exc}"]}, ensure_ascii=False))
        return 2
    errors = validate_public_snapshot(payload, now=now, watchlist=watchlist)
    if not errors and args.export_watchlist:
        from futures_compass_data import export_watchlist
        try:
            export_watchlist(payload, args.export_watchlist, watchlist=watchlist)
        except (ValueError, OSError) as exc:
            errors.append(f"cannot export futures watchlist: {exc}")
    print(json.dumps({"status": "ok" if not errors else "error", "errors": errors}, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
