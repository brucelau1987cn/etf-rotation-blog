#!/usr/bin/env python3
"""Generate compact browser payloads from the full research snapshots."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data"

A_FIELDS = (
    "code", "name", "type", "theme", "status", "price", "ret5", "ret20",
    "close_position", "signal_score", "strength_level", "trading_risk_score",
    "trade_state", "action", "cooldown_state", "risk_flags", "risk_level",
    "agent_bull", "agent_bear", "agent_scores",
)


def pick(row: dict, fields: tuple[str, ...]) -> dict:
    return {field: row.get(field) for field in fields}


def write_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    source = json.loads((DATA / "etf-garden-pool.json").read_text(encoding="utf-8"))
    rows = [pick(row, A_FIELDS) for row in source.get("all_rows", [])]
    payload = {
        "generated_at": source.get("generated_at"),
        "run_date": source.get("run_date"),
        "evaluation_date": source.get("evaluation_date"),
        "latest_trade_date": source.get("latest_trade_date"),
        "summary": source.get("summary") or {},
        "market_regime": source.get("market_regime") or {},
        "realtime_scope": source.get("realtime_scope") or [],
        "snapshot_scope": source.get("snapshot_scope") or [],
        "all_rows": rows,
    }
    output = DATA / "a-compass-dashboard.json"
    write_atomic(output, payload)
    print(json.dumps({"status": "ok", "path": str(output.relative_to(ROOT)), "rows": len(rows), "bytes": output.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    main()
