#!/usr/bin/env python3
"""Deterministic preparation gate for the A-share 22:00 content phase."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
STATE = Path("/root/.hermes/state/a-share-nightly-pipeline.json")
CN = ZoneInfo("Asia/Shanghai")


def run_json(command: list[str]) -> dict:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if not result.stdout.strip():
        raise RuntimeError((result.stderr or f"empty output: {command}")[-1000:])
    payload = json.loads(result.stdout)
    if result.returncode not in {0, 2}:
        raise RuntimeError(f"command failed rc={result.returncode}: {payload}")
    return payload


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare(now: datetime | None = None, state_path: Path = STATE) -> dict:
    current = now or datetime.now(CN)
    gate = run_json(["python3", "scripts/check_a_share_cron_gate.py", "--stage", "22:00"])
    if gate.get("decision") != "run":
        payload = {
            "version": 1,
            "status": gate.get("decision"),
            "phase": "prepare",
            "trade_date": gate.get("qfq_date"),
            "prepared_at": current.isoformat(),
            "gate": gate,
        }
        atomic_write(state_path, payload)
        return payload

    pool = json.loads((ROOT / "public/data/etf-garden-pool.json").read_text(encoding="utf-8"))
    shadow = json.loads((ROOT / "public/data/model-lab/a-share-shadow.json").read_text(encoding="utf-8"))
    errors = []
    if shadow.get("mode") != "shadow_research_only":
        errors.append("shadow mode must be shadow_research_only")
    if shadow.get("production_weights_changed") is not False:
        errors.append("production_weights_changed must be false")
    enhancement = shadow.get("signal_enhancement")
    if not isinstance(enhancement, dict):
        errors.append("signal_enhancement is required")
    elif enhancement.get("formal_signal_logic_changed") is not False or enhancement.get("production_role") != "shadow_filter_and_audit_only":
        errors.append("signal_enhancement must remain audit-only")
    if int(shadow.get("rotation_universe_count") or 0) < 82:
        errors.append("shadow rotation universe below 82")
    if pool.get("latest_trade_date") != gate.get("qfq_date"):
        errors.append("pool latest_trade_date differs from gate qfq_date")
    if int((pool.get("summary") or {}).get("valid_count") or 0) < 82:
        errors.append("formal pool valid coverage below 82")
    if errors:
        payload = {
            "version": 1,
            "status": "blocked",
            "phase": "prepare",
            "trade_date": gate.get("qfq_date"),
            "prepared_at": current.isoformat(),
            "errors": errors,
            "gate": gate,
        }
        atomic_write(state_path, payload)
        return payload

    payload = {
        "version": 1,
        "status": "prepared",
        "phase": "prepare",
        "trade_date": gate.get("qfq_date"),
        "prepared_at": current.isoformat(),
        "gate": gate,
        "expected_stage": "22:00夜间最终版",
        "content_files": [
            f"src/content/blog/{gate.get('qfq_date')}.md",
            "public/data/garden-recommendations.json",
            "public/data/a-share-mid-macro.json",
        ],
        "snapshot_files": [
            "public/data/etf-garden-pool.json",
            "public/data/model-lab/a-share-shadow.json",
        ],
    }
    atomic_write(state_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        assert ROOT.joinpath("scripts/check_a_share_cron_gate.py").exists()
        print("prepare_a_share_nightly self-test: OK")
        return 0
    result = prepare(state_path=args.state)
    if result.get("status") == "prepared":
        return 0
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "idempotent" else 2


if __name__ == "__main__":
    raise SystemExit(main())
