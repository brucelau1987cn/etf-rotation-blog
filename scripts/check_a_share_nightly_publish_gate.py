#!/usr/bin/env python3
"""Fail-closed handoff gate for the deterministic 22:30 publisher."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from a_share_nightly_contract import STATE
except ModuleNotFoundError:
    from scripts.a_share_nightly_contract import STATE


CN = ZoneInfo("Asia/Shanghai")


def check_publish_gate(state_path: Path = STATE, now: datetime | None = None) -> dict:
    current = (now or datetime.now(CN)).astimezone(CN)
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"nightly publish gate cannot read manifest: {exc}") from exc
    if payload.get("version") != 2:
        raise RuntimeError("nightly publish gate requires manifest version 2")
    status = payload.get("status")
    recovery_statuses = {"candidate_validated", "committed", "deploy_failed", "published"}
    if payload.get("phase") != status or status not in {"content_ready", *recovery_statuses}:
        raise RuntimeError(
            "nightly publish gate requires content_ready or a publisher recovery status; "
            f"got status={status!r} phase={payload.get('phase')!r}"
        )
    today = current.date().isoformat()
    trade_date = payload.get("trade_date")
    prepared_date = str(payload.get("prepared_at") or "")[:10]
    if trade_date != today or prepared_date != today:
        raise RuntimeError(
            "nightly publish gate requires current date; "
            f"trade_date={trade_date!r} prepared_date={prepared_date!r} current={today!r}"
        )
    return {"status": "ok", "trade_date": trade_date, "manifest_status": status}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--now", help="ISO timestamp for deterministic validation")
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now).astimezone(CN) if args.now else None
    print(json.dumps(check_publish_gate(args.state, now), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())