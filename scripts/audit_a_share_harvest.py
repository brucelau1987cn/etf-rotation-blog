#!/usr/bin/env python3
"""Requalify tracked A-share harvest items against the current formal 91-row pool."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RECOMMENDATIONS = ROOT / "public/data/garden-recommendations.json"
POOL = ROOT / "public/data/etf-garden-pool.json"
RULE_VERSION = "a-share-harvest-audit-v1"
EXPECTED_POOL_SIZE = 91


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def row_fingerprint(row: dict[str, Any]) -> str:
    fields = {
        key: row.get(key) for key in (
            "code", "date", "trade_state", "strength_level", "risk_level",
            "signal_score", "trading_risk_score", "target_gap", "ret20",
        )
    }
    raw = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def qualified_reason(row: dict[str, Any]) -> str | None:
    state = str(row.get("trade_state") or "")
    if state in {"退出", "禁止追高"}:
        return f"当前池交易状态={state}"
    target_gap = finite(row.get("target_gap"))
    if target_gap is not None and 0 <= target_gap <= 8:
        return f"距兑现位{target_gap:.2f}%"
    risk = str(row.get("risk_level") or "")
    if risk in {"中", "高"}:
        return f"当前池风险={risk}"
    ret20 = finite(row.get("ret20"))
    if ret20 is not None and ret20 >= 15:
        return f"20日涨幅{ret20:.2f}%"
    return None


def apply_harvest_audit(recommendations: dict[str, Any], pool: dict[str, Any]) -> dict[str, Any]:
    rows = pool.get("all_rows")
    raw_summary = pool.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    if not isinstance(rows, list) or len(rows) != EXPECTED_POOL_SIZE or summary.get("universe_count") != EXPECTED_POOL_SIZE:
        raise RuntimeError("harvest audit requires the complete 91-row formal pool")
    pool_date = str(pool.get("evaluation_date") or pool.get("latest_trade_date") or "")
    if not pool_date or str(recommendations.get("date") or "") != pool_date:
        raise RuntimeError("harvest audit requires recommendation and pool dates to match")
    row_map = {str(row.get("code") or ""): row for row in rows if isinstance(row, dict)}
    source = recommendations.get("harvest")
    if not isinstance(source, list):
        raise RuntimeError("recommendations harvest must be an array")

    selected: list[dict[str, Any]] = []
    removed: list[str] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "")
        row = row_map.get(code)
        reason = qualified_reason(row) if row else None
        if not row or not reason or str(row.get("date") or pool_date) != pool_date:
            if code:
                removed.append(code)
            continue
        enriched = dict(item)
        enriched.update({
            "selected_from_pool_date": pool_date,
            "last_qualified_date": pool_date,
            "qualified_reason": reason,
            "harvest_rule_version": RULE_VERSION,
            "source_fingerprint": row_fingerprint(row),
            "pool_trade_state": row.get("trade_state"),
            "pool_strength_level": row.get("strength_level"),
            "pool_signal_score": row.get("signal_score"),
        })
        selected.append(enriched)

    recommendations["harvest"] = selected
    recommendations["harvest_selection"] = {
        "rule_version": RULE_VERSION,
        "selection_mode": "current_tracked_harvest_requalification",
        "selected_from_pool_date": pool_date,
        "evaluated_count": len(source),
        "selected_count": len(selected),
        "selected_codes": [item["code"] for item in selected],
        "removed_codes": removed,
    }
    return recommendations


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recommendations", type=Path, default=RECOMMENDATIONS)
    parser.add_argument("--pool", type=Path, default=POOL)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    recommendations = json.loads(args.recommendations.read_text(encoding="utf-8"))
    pool = json.loads(args.pool.read_text(encoding="utf-8"))
    result = apply_harvest_audit(recommendations, pool)
    if not args.validate:
        atomic_write(args.recommendations, result)
    print(json.dumps({"status": "ok", "selected": result["harvest_selection"]["selected_codes"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
