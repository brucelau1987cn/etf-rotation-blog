#!/usr/bin/env python3
"""Deterministic preparation gate for the A-share 22:00 content phase."""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from generate_research_audit import DEFAULT_TURNOVER, build_payload
    from validate_dashboard_batches import validate_research_audit
except ModuleNotFoundError:  # imported as scripts.prepare_a_share_nightly in tests
    from scripts.generate_research_audit import DEFAULT_TURNOVER, build_payload
    from scripts.validate_dashboard_batches import validate_research_audit

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


def generate_research_audit(
    backtest: dict, pool: dict, generated_at: str,
) -> tuple[dict, str | None]:
    try:
        return build_payload(backtest, pool, DEFAULT_TURNOVER, generated_at), None
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {}, f"research audit generator unavailable: {exc}"


def validate_kronos_snapshot(payload: dict, trade_date: str | None) -> list[str]:
    errors = []
    if payload.get("mode") != "shadow_research_only" or payload.get("production_weights_changed") is not False:
        errors.append("Kronos snapshot must remain shadow_research_only with unchanged production weights")
    if payload.get("formal_signal_logic_changed") is not False or payload.get("production_role") != "display_and_audit_only":
        errors.append("Kronos snapshot must remain display-and-audit only")
    if payload.get("latest_trade_date") != trade_date:
        errors.append("Kronos latest_trade_date differs from gate qfq_date")
    basis = payload.get("data_basis") or {}
    if basis.get("adjustment") != "qfq" or basis.get("is_final") is not True or basis.get("universe") != "formal_rotation":
        errors.append("Kronos snapshot requires final qfq formal_rotation data")
    definition = payload.get("forecast_definition") or {}
    if definition.get("horizon_sessions") != 5 or len(definition.get("future_sessions") or []) != 5:
        errors.append("Kronos snapshot requires five future sessions")
    coverage = payload.get("coverage") or {}
    items = payload.get("items") or []
    if coverage.get("expected_symbols") != 89 or coverage.get("predicted_symbols") != 89 or len(items) != 89:
        errors.append("Kronos snapshot requires 89/89 coverage")
    symbols = [item.get("symbol") for item in items if isinstance(item, dict)]
    if len(symbols) != len(set(symbols)):
        errors.append("Kronos snapshot contains duplicate symbols")
    for item in items:
        if not isinstance(item, dict) or item.get("as_of") != trade_date or len(item.get("steps") or []) != 5:
            errors.append("Kronos snapshot contains stale or malformed items")
            break
        values = [step.get(field) for step in item["steps"] for field in ("open", "high", "low", "close")]
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
            errors.append("Kronos snapshot contains non-finite predictions")
            break
    return errors


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
    backtest = json.loads((ROOT / "public/data/etf-garden-backtest.json").read_text(encoding="utf-8"))
    shadow = json.loads((ROOT / "public/data/model-lab/a-share-shadow.json").read_text(encoding="utf-8"))
    kronos_path = ROOT / "public/data/model-lab/a-share-kronos-shadow.json"
    kronos = json.loads(kronos_path.read_text(encoding="utf-8")) if kronos_path.exists() else {}
    research_audit: dict = {}
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
    elif int((enhancement.get("coverage") or {}).get("symbols_at_least_260") or 0) < 82:
        errors.append("signal_enhancement 260-bar coverage below 82")
    if int(shadow.get("rotation_universe_count") or 0) < 82:
        errors.append("shadow rotation universe below 82")
    if pool.get("latest_trade_date") != gate.get("qfq_date"):
        errors.append("pool latest_trade_date differs from gate qfq_date")
    if int((pool.get("summary") or {}).get("valid_count") or 0) < 82:
        errors.append("formal pool valid coverage below 82")
    errors.extend(validate_kronos_snapshot(kronos, gate.get("qfq_date")))
    if not errors:
        research_audit, research_error = generate_research_audit(backtest, pool, current.isoformat())
        if research_error:
            errors.append(f"research audit generation failed: {research_error}")
        else:
            validate_research_audit(errors, research_audit, backtest, pool)
            research_dataset = research_audit.get("dataset") or {}
            if research_dataset.get("as_of") != pool.get("latest_trade_date"):
                errors.append("research audit as_of differs from pool latest_trade_date")
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

    atomic_write(ROOT / "public/data/model-lab/a-share-research-audit.json", research_audit)
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
            "public/data/etf-garden-backtest.json",
            "public/data/etf-garden-pool.json",
            "public/data/model-lab/a-share-shadow.json",
            "public/data/model-lab/a-share-kronos-shadow.json",
            "public/data/model-lab/a-share-research-audit.json",
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
