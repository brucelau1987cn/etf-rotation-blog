#!/usr/bin/env python3
"""Generate compact, versioned browser payloads from full research snapshots."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data"
SCHEMA_VERSION = "a-compass-dashboard-v1"
CONTRACT_URL = "/schemas/a-compass-dashboard.schema.json"

A_FIELDS = (
    "code", "name", "type", "theme", "status", "price", "ret5", "ret20",
    "close_position", "signal_score", "strength_level", "trading_risk_score",
    "trade_state", "action", "cooldown_state", "risk_flags", "risk_level",
    "agent_bull", "agent_bear", "agent_scores",
)
SEMANTIC_FIELDS = (
    "run_date", "evaluation_date", "latest_trade_date", "summary", "market_regime",
    "realtime_scope", "snapshot_scope", "all_rows",
)


def pick(row: dict, fields: tuple[str, ...]) -> dict:
    return {field: row.get(field) for field in fields}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def dashboard_batch_id(payload: dict[str, Any]) -> str:
    semantic = {field: payload.get(field) for field in SEMANTIC_FIELDS}
    return hashlib.sha256(canonical_bytes(semantic)).hexdigest()


def build_payload(source: dict[str, Any]) -> dict[str, Any]:
    semantic = {
        "run_date": source.get("run_date"),
        "evaluation_date": source.get("evaluation_date"),
        "latest_trade_date": source.get("latest_trade_date"),
        "summary": source.get("summary") or {},
        "market_regime": source.get("market_regime") or {},
        "realtime_scope": source.get("realtime_scope") or [],
        "snapshot_scope": source.get("snapshot_scope") or [],
        "all_rows": [pick(row, A_FIELDS) for row in source.get("all_rows", [])],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "batch_id": dashboard_batch_id(semantic),
        "contract_url": CONTRACT_URL,
        "generated_at": source.get("generated_at"),
        **semantic,
    }


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    source = json.loads((DATA / "etf-garden-pool.json").read_text(encoding="utf-8"))
    payload = build_payload(source)
    output = DATA / "a-compass-dashboard.json"
    write_atomic(output, payload)
    print(json.dumps({
        "status": "ok", "path": str(output.relative_to(ROOT)),
        "rows": len(payload["all_rows"]), "batch_id": payload["batch_id"],
        "bytes": output.stat().st_size,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
