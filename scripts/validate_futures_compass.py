#!/usr/bin/env python3
"""Fail closed when the public futures compass snapshot is stale or incomplete."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from futures_compass_data import PUBLIC_SNAPSHOT, validate_public_snapshot
except ModuleNotFoundError:
    from scripts.futures_compass_data import PUBLIC_SNAPSHOT, validate_public_snapshot

CN = ZoneInfo("Asia/Shanghai")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=PUBLIC_SNAPSHOT)
    parser.add_argument("--now")
    args = parser.parse_args()
    try:
        payload = json.loads(args.path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "errors": [f"cannot read futures snapshot: {exc}"]}, ensure_ascii=False))
        return 2
    now = datetime.fromisoformat(args.now).astimezone(CN) if args.now else datetime.now(CN)
    errors = validate_public_snapshot(payload, now=now)
    print(json.dumps({"status": "ok" if not errors else "error", "errors": errors}, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
