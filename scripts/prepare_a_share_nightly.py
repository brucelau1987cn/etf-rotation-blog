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
    from a_share_nightly_contract import (
        DEPLOYMENT_MARKER_FILE, PATH_SHADOW_FILE, SNAPSHOT_FILES, STATE, file_hashes,
        nightly_content_files, nightly_lock,
    )
    from generate_research_audit import DEFAULT_TURNOVER, build_payload
    from validate_dashboard_batches import validate_research_audit
except ModuleNotFoundError:  # imported as scripts.prepare_a_share_nightly in tests
    from scripts.a_share_nightly_contract import (
        DEPLOYMENT_MARKER_FILE, PATH_SHADOW_FILE, SNAPSHOT_FILES, STATE, file_hashes,
        nightly_content_files, nightly_lock,
    )
    from scripts.generate_research_audit import DEFAULT_TURNOVER, build_payload
    from scripts.validate_dashboard_batches import validate_research_audit

ROOT = Path(__file__).resolve().parents[1]
CN = ZoneInfo("Asia/Shanghai")


def run_json(command: list[str]) -> dict:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=180)
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


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        capture_output=True, check=True, timeout=30,
    ).stdout.strip()


def ensure_current_main() -> str:
    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=ROOT, text=True, capture_output=True,
            check=check, timeout=120,
        )

    branch = git("branch", "--show-current").stdout.strip()
    if branch != "main":
        raise RuntimeError(f"nightly prepare requires main branch, got {branch!r}")
    if git("diff", "--cached", "--quiet", check=False).returncode != 0:
        raise RuntimeError("nightly prepare requires a clean git index")
    git("fetch", "origin", "main")
    head = git("rev-parse", "HEAD").stdout.strip()
    remote = git("rev-parse", "origin/main").stdout.strip()
    if head != remote:
        raise RuntimeError(
            f"nightly prepare requires HEAD == origin/main: head={head[:12]} remote={remote[:12]}"
        )
    return head


def generate_research_audit(
    backtest: dict, pool: dict, generated_at: str,
) -> tuple[dict, str | None]:
    try:
        return build_payload(backtest, pool, DEFAULT_TURNOVER, generated_at), None
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {}, f"research audit generator unavailable: {exc}"


def generate_macro_snapshot() -> dict:
    """Refresh the A-share macro snapshot before the 22:00 content phase."""
    result = subprocess.run(
        ["python3", "scripts/generate_a_share_mid_macro.py"], cwd=ROOT,
        text=True, capture_output=True, check=False, timeout=240,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "macro generator failed")[-2000:])
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("macro generator returned empty output")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid macro generator output: {exc}") from exc


def validate_macro_snapshot(payload: dict, trade_date: str | None) -> list[str]:
    errors: list[str] = []
    if payload.get("version") != 2:
        errors.append("macro snapshot version must be 2")
    if str(payload.get("generated_at") or "")[:10] != trade_date:
        errors.append("macro generated_at differs from gate qfq_date")
    framework = payload.get("framework") or []
    if len(framework) != 6:
        errors.append("macro framework requires six dimensions")
        return errors
    expected = {
        "monetary_liquidity", "credit_impulse", "growth_cycle",
        "inflation_margin", "external_fx", "market_liquidity",
    }
    if {item.get("key") for item in framework if isinstance(item, dict)} != expected:
        errors.append("macro framework dimension keys are incomplete")
    observation_count = 0
    for dimension in framework:
        observations = dimension.get("observations") or []
        if not observations:
            errors.append(f"macro dimension {dimension.get('key')} has no concrete observations")
            continue
        observation_count += len(observations)
        for item in observations:
            value = item.get("value")
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                errors.append(f"macro observation {item.get('key')} has a non-finite value")
            if not item.get("as_of") or str(item.get("as_of")) > str(trade_date):
                errors.append(f"macro observation {item.get('key')} has an invalid observation date")
            if not item.get("source") or not item.get("detail"):
                errors.append(f"macro observation {item.get('key')} lacks source or detail")
    if observation_count < 15:
        errors.append(f"macro concrete observation coverage below 15: {observation_count}")
    constraint = payload.get("constraint") or {}
    if constraint.get("headwind_level") not in {0, 1, 2, 3}:
        errors.append("macro headwind_level must be between 0 and 3")
    factors = payload.get("factors") or []
    if len(factors) != 3 or any(factor.get("status") != "ok" for factor in factors):
        errors.append("macro daily risk gate requires three available factors")
    return errors


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
    kronos_path = ROOT / PATH_SHADOW_FILE
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
    macro_generation: dict = {}
    if not errors:
        try:
            macro_generation = generate_macro_snapshot()
            macro_path = ROOT / "public/data/a-share-mid-macro.json"
            macro_snapshot = json.loads(macro_path.read_text(encoding="utf-8"))
            errors.extend(validate_macro_snapshot(macro_snapshot, gate.get("qfq_date")))
            if macro_generation.get("status") != "ok":
                errors.append("macro generator status is not ok")
            if macro_generation.get("failures"):
                errors.append(f"macro generator reported failures: {macro_generation['failures']}")
        except (OSError, ValueError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
            errors.append(f"macro snapshot generation failed: {exc}")
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
    trade_date = str(gate.get("qfq_date"))
    base_commit = git_head()
    generation_id = f"a-share-{trade_date}-{current.strftime('%Y%m%dT%H%M%S%z')}"
    atomic_write(ROOT / DEPLOYMENT_MARKER_FILE, {
        "schema_version": 1,
        "generation_id": generation_id,
        "trade_date": trade_date,
        "base_commit": base_commit,
        "mode": "a_share_nightly_static",
    })
    payload = {
        "version": 2,
        "status": "prepared",
        "phase": "prepared",
        "generation_id": generation_id,
        "trade_date": trade_date,
        "prepared_at": current.isoformat(),
        "base_commit": base_commit,
        "gate": gate,
        "expected_stage": "22:00夜间最终版",
        "content_files": list(nightly_content_files(trade_date)),
        "snapshot_files": list(SNAPSHOT_FILES),
        "snapshot_hashes": file_hashes(ROOT, SNAPSHOT_FILES),
        "macro_refresh": {
            "status": macro_generation.get("status"),
            "headwind_level": macro_generation.get("headwind_level"),
            "label": macro_generation.get("label"),
            "failures": macro_generation.get("failures") or {},
        },
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
    with nightly_lock():
        ensure_current_main()
        result = prepare(state_path=args.state)
    if result.get("status") == "prepared":
        return 0
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "idempotent" else 2


if __name__ == "__main__":
    raise SystemExit(main())
