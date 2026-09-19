#!/usr/bin/env python3
"""Bounded, read-only gates for A-share nightly stage handoffs."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
CHAIN_READY = Path("/root/.hermes/state/a-share-nightly-chain-ready.json")
TRIGGER_STATUS = Path("/root/.hermes/state/a-share-nightly-trigger-status.json")
MANIFEST = Path("/root/.hermes/state/a-share-nightly-pipeline.json")
CHAIN_STAGES = ("precheck", "cache", "fundamental-shadow")


class StaleStateError(RuntimeError):
    """A terminal receipt belongs to an earlier trade date."""


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def chain_ready(payload: dict, trade_date: str) -> bool:
    results = payload.get("results")
    if not isinstance(results, list):
        return False
    stage_results = {
        item.get("stage"): item for item in results if isinstance(item, dict)
    }
    fundamental = payload.get("fundamental")
    coverage = fundamental.get("coverage") if isinstance(fundamental, dict) else None
    return bool(
        payload.get("version") == 2
        and payload.get("requested_stage") == "precheck-cache"
        and payload.get("returncode") == 0
        and payload.get("trade_date") == trade_date
        and payload.get("status") == "ready"
        and str(payload.get("ready_at") or "")[:10] == trade_date
        and set(stage_results) == set(CHAIN_STAGES)
        and all(stage_results[name].get("ok") is True for name in CHAIN_STAGES)
        and isinstance(fundamental, dict)
        and fundamental.get("trade_date") == trade_date
        and isinstance(coverage, dict)
        and coverage.get("publishable") is True
        and coverage.get("failed") == 0
    )


def content_ready(payload: dict, trade_date: str) -> bool:
    status = payload.get("status")
    return bool(
        payload.get("version") == 2
        and payload.get("trade_date") == trade_date
        and str(payload.get("prepared_at") or "")[:10] == trade_date
        and payload.get("phase") == status
        and status in {
            "content_ready", "candidate_validated", "committed",
            "deploy_failed", "published",
        }
    )


def wait_for_stage(
    stage: str,
    *,
    now: datetime | None = None,
    timeout: float = 1200,
    interval: float = 10,
    ready_path: Path = CHAIN_READY,
    trigger_path: Path = TRIGGER_STATUS,
    manifest_path: Path = MANIFEST,
) -> dict:
    current = (now or datetime.now(CN)).astimezone(CN)
    trade_date = current.date().isoformat()
    deadline = time.monotonic() + max(0, timeout)
    last = {}
    while True:
        if stage == "chain":
            last = load_json(ready_path)
            if chain_ready(last, trade_date):
                return last
            trigger = load_json(trigger_path)
            detail = trigger.get("current_stage") or trigger.get("status") or "missing"
        else:
            last = load_json(manifest_path)
            if content_ready(last, trade_date):
                return last
            if (
                last.get("status") in {"published", "blocked"}
                and last.get("trade_date") != trade_date
            ):
                raise StaleStateError(
                    f"stale content state for {trade_date}: "
                    f"status={last.get('status')} trade_date={last.get('trade_date')}"
                )
            detail = last.get("status") or "missing"
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"nightly {stage} state timed out for {trade_date}; last={detail}"
            )
        time.sleep(max(0, interval))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("chain", "content"), required=True)
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--now", help="fixed ISO timestamp for deterministic tests")
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now).astimezone(CN) if args.now else None
    try:
        payload = wait_for_stage(
            args.stage, now=now, timeout=args.timeout, interval=args.interval,
        )
    except StaleStateError as exc:
        print(json.dumps({"status": "stale_state", "reason": str(exc)}, ensure_ascii=False))
        return 76
    except TimeoutError as exc:
        print(json.dumps({"status": "waiting_timeout", "reason": str(exc)}, ensure_ascii=False))
        return 75
    print(json.dumps({
        "status": "ready",
        "stage": args.stage,
        "trade_date": payload.get("trade_date"),
        "source_status": payload.get("status"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
