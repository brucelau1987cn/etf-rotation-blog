#!/usr/bin/env python3
"""Select A-share ETF candidates from the current formal pool.

Candidate identity is recomputed from the current pool on every run. Previous
recommendations only supply a small continuity bonus and editorial text after a
candidate proves current-day eligibility again.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RECOMMENDATIONS = ROOT / "public/data/garden-recommendations.json"
POOL = ROOT / "public/data/etf-garden-pool.json"
RULE_VERSION = "a-candidate-v1"
DEFAULT_LIMIT = 3
CONTINUITY_BONUS = 1.5
DEFENSIVE_THEME_TOKENS = ("银行", "红利", "低波", "现金流", "国债", "债券", "货币")


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def is_defensive(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get(key) or "") for key in ("theme", "name", "category"))
    return any(token in text for token in DEFENSIVE_THEME_TOKENS)


def valid_levels(row: dict[str, Any]) -> bool:
    price = number(row.get("price"))
    support = number(row.get("support"))
    target = number(row.get("target"))
    stop = number(row.get("stop"))
    if any(value is None or value <= 0 for value in (price, support, target, stop)):
        return False
    assert support is not None and target is not None and stop is not None
    return stop < support < target


def qualifies(row: dict[str, Any]) -> bool:
    if str(row.get("asset_layer") or "rotation") != "rotation":
        return False
    if str(row.get("risk_level") or "") == "高" or not valid_levels(row):
        return False
    strength = str(row.get("strength_level") or "")
    if strength not in {"A", "B", "C"}:
        return False
    support_gap = number(row.get("support_gap"))
    if support_gap is None or not 0 <= support_gap <= 4:
        return False
    raw_checks = row.get("checks")
    checks: dict[str, Any] = raw_checks if isinstance(raw_checks, dict) else {}
    if not checks.get("price_above_ma") or not checks.get("ma_rising"):
        return False
    score = number(row.get("signal_score")) or 0
    try:
        rank = int(row.get("momentum_rank") or 999)
    except (TypeError, ValueError):
        rank = 999
    return bool(checks.get("momentum")) or score >= 55 or rank <= 15


def selection_score(row: dict[str, Any], incumbent: bool) -> float:
    score = number(row.get("signal_score")) or 0
    support_gap = number(row.get("support_gap")) or 4
    strength = str(row.get("strength_level") or "")
    raw_checks = row.get("checks")
    checks: dict[str, Any] = raw_checks if isinstance(raw_checks, dict) else {}
    result = score + max(0, 4 - support_gap) * 2
    result += {"A": 6, "B": 3, "C": 0}.get(strength, 0)
    if checks.get("momentum"):
        result += 8
    if incumbent:
        result += CONTINUITY_BONUS
    return round(result, 2)


def candidate_reason(row: dict[str, Any]) -> list[str]:
    raw_checks = row.get("checks")
    checks: dict[str, Any] = raw_checks if isinstance(raw_checks, dict) else {}
    reasons = [
        f"趋势{row.get('strength_level')}",
        f"信号得分{number(row.get('signal_score')) or 0:.1f}",
        f"动量排名{int(row.get('momentum_rank') or 999)}",
        f"距伏击位{number(row.get('support_gap')) or 0:.1f}%",
        f"风险{row.get('risk_level') or '待确认'}",
    ]
    if checks.get("momentum"):
        reasons.append("动量通过")
    return reasons


def generated_action(row: dict[str, Any]) -> str:
    price = number(row.get("price")) or 0
    support = number(row.get("support")) or 0
    stop = number(row.get("stop")) or 0
    return f"现价{price:.3f}，等待回踩{support:.3f}附近止跌确认；跌破{stop:.3f}取消候场。"


def make_candidate(row: dict[str, Any], previous: dict[str, Any] | None, evaluation_date: str, score: float, rank: int) -> dict[str, Any]:
    proven_incumbent = bool(previous and previous.get("last_qualified_date"))
    previous_item = previous or {}
    action = str(previous_item.get("action") or "") if proven_incumbent else generated_action(row)
    trigger = str(previous_item.get("trigger") or "") if proven_incumbent else action
    candidate_since = str(previous_item.get("candidate_since")) if proven_incumbent and previous_item.get("candidate_since") else evaluation_date
    return {
        "status": "候场",
        "code": str(row.get("code")),
        "name": str(row.get("name") or row.get("quote_name") or row.get("code")),
        "priority": "高" if rank == 1 else "中",
        "trigger": trigger,
        "action": action,
        "signal_state": "当日正式池重选候场",
        "eligibility": "ok",
        "eligibility_reason": "；".join(candidate_reason(row)),
        "price": number(row.get("price")),
        "support": number(row.get("support")),
        "target": number(row.get("target")),
        "stop": number(row.get("stop")),
        "action_level": number(row.get("support")),
        "trigger_level": number(row.get("support")),
        "action_level_label": "伏击位",
        "distance_pct": round(-(number(row.get("support_gap")) or 0), 2),
        "trend_level": row.get("strength_level"),
        "risk_level": row.get("risk_level"),
        "price_date": str(row.get("date") or evaluation_date),
        "level_basis": row.get("level_basis"),
        "model_version": row.get("level_model_version"),
        "data_source": row.get("quote_source"),
        "level_status": "ready",
        "selected_from_pool_date": evaluation_date,
        "selection_score": score,
        "selection_rank": rank,
        "candidate_since": candidate_since,
        "last_qualified_date": evaluation_date,
        "qualified_reason": candidate_reason(row),
        "selection_rule_version": RULE_VERSION,
    }


def select_candidates(pool: dict[str, Any], previous_items: list[dict[str, Any]], limit: int = DEFAULT_LIMIT) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    evaluation_date = str(pool.get("evaluation_date") or pool.get("latest_trade_date") or "")
    if not evaluation_date:
        raise ValueError("pool evaluation date is missing")
    rows = pool.get("all_rows")
    if not isinstance(rows, list):
        raise ValueError("pool all_rows is missing")
    previous_map = {str(item.get("code")): item for item in previous_items if isinstance(item, dict) and item.get("code")}
    eligible: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("code") or not qualifies(row):
            continue
        previous = previous_map.get(str(row["code"]))
        incumbent = bool(previous and previous.get("last_qualified_date"))
        eligible.append((selection_score(row, incumbent), row))
    eligible.sort(key=lambda item: (-item[0], int(item[1].get("momentum_rank") or 999), str(item[1].get("code"))))

    selected_rows: list[tuple[float, dict[str, Any]]] = []
    themes: set[str] = set()
    defensive_count = 0
    for score, row in eligible:
        theme = str(row.get("theme") or row.get("category") or row.get("name") or row.get("code"))
        defensive = is_defensive(row)
        if theme in themes or (defensive and defensive_count >= 1):
            continue
        selected_rows.append((score, row))
        themes.add(theme)
        defensive_count += int(defensive)
        if len(selected_rows) >= max(0, limit):
            break

    selected = [
        make_candidate(row, previous_map.get(str(row["code"])), evaluation_date, score, index)
        for index, (score, row) in enumerate(selected_rows, start=1)
    ]
    previous_codes = [str(item.get("code")) for item in previous_items if isinstance(item, dict) and item.get("code")]
    selected_codes = [item["code"] for item in selected]
    audit = {
        "rule_version": RULE_VERSION,
        "evaluation_date": evaluation_date,
        "evaluated_count": len(rows),
        "eligible_count": len(eligible),
        "selected_count": len(selected),
        "selected_codes": selected_codes,
        "previous_codes": previous_codes,
        "unchanged_from_previous": bool(selected_codes) and selected_codes == previous_codes,
        "continuity_requires_current_qualification": True,
        "defensive_limit": 1,
    }
    return selected, audit


def apply_selection(recommendations: dict[str, Any], pool: dict[str, Any], limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    result = json.loads(json.dumps(recommendations, ensure_ascii=False))
    evaluation_date = str(pool.get("evaluation_date") or pool.get("latest_trade_date") or "")
    if str(result.get("date") or "") != evaluation_date:
        raise ValueError(f"recommendation/pool date mismatch: {result.get('date')!r} != {evaluation_date!r}")
    previous = result.get("plant") if isinstance(result.get("plant"), list) else []
    selected, audit = select_candidates(pool, previous, limit=limit)
    result["plant"] = selected
    result["candidate_selection"] = audit
    return result


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true", help="calculate and validate without writing")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()
    recommendations = json.loads(RECOMMENDATIONS.read_text(encoding="utf-8"))
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    payload = apply_selection(recommendations, pool, limit=args.limit)
    if not args.validate:
        atomic_write(RECOMMENDATIONS, payload)
    print(json.dumps({"status": "validated" if args.validate else "selected", **payload["candidate_selection"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
