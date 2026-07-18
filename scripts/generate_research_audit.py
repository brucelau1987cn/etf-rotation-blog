#!/usr/bin/env python3
"""Generate a deterministic, research-only audit sidecar for ETF Compass.

The artifact is observational. It never mutates production signals, levels,
weights, positions, or paper-trading state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKTEST = ROOT / "public/data/etf-garden-backtest.json"
DEFAULT_POOL = ROOT / "public/data/etf-garden-pool.json"
DEFAULT_TURNOVER = ROOT / "data/local/etf-turnover-history.json"
DEFAULT_OUT = ROOT / "public/data/model-lab/a-share-research-audit.json"
SCHEMA_VERSION = "research_audit_v1"
PROVENANCE = {
    "schema_version": SCHEMA_VERSION,
    "source": "frozen historical direction records + current frozen action pool",
    "adjustment": "qfq for historical returns; raw executable levels remain separate",
    "granularity": "daily direction history; current pool snapshot",
    "execution_basis": "T signal → next-session close direction evaluation",
    "cost_model": {
        "applied": False,
        "reason": "方向标签验证不构造组合成交；账户级评价必须另行计入佣金、最低收费与滑点",
    },
}

FINGERPRINT_FIELDS = (
    "target_date", "actual_trade_date", "side", "code", "name",
    "prev_close", "target_close", "next_close", "day_ret_pct",
    "next1_ret_pct", "next3_ret_pct", "day_hit", "next1_hit",
    "next3_hit", "excursion_hit",
)
POOL_VOLATILE_FIELDS = {"generated_at", "updated_at", "refreshed_at"}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True,
    )
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


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )


def canonical_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [
        {key: row.get(key) for key in FINGERPRINT_FIELDS}
        for row in records
    ]
    return sorted(selected, key=_canonical_json)


def dataset_fingerprint(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    canonical = canonical_records(records)
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    dates = [
        str(row.get("actual_trade_date") or row.get("target_date"))
        for row in canonical
        if row.get("actual_trade_date") or row.get("target_date")
    ]
    return {
        "algorithm": "sha256",
        "value": hashlib.sha256(encoded).hexdigest(),
        "record_count": len(canonical),
        "as_of": max(dates, default=None),
        "canonical_fields": list(FINGERPRINT_FIELDS),
        "volatile_fields_excluded": ["generated_at"],
    }


def _without_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_volatile(item)
            for key, item in value.items()
            if key not in POOL_VOLATILE_FIELDS
        }
    if isinstance(value, list):
        return [_without_volatile(item) for item in value]
    return value


def pool_fingerprint(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    canonical = [_without_volatile(row) for row in rows]
    canonical.sort(key=_canonical_json)
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    dates = [str(row["date"]) for row in canonical if row.get("date")]
    return {
        "algorithm": "sha256", "value": hashlib.sha256(encoded).hexdigest(),
        "row_count": len(canonical), "as_of": max(dates, default=None),
        "canonical_scope": "complete_action_pool_rows",
        "volatile_fields_excluded": sorted(POOL_VOLATILE_FIELDS),
    }


def combined_fingerprint(
    records: Iterable[dict[str, Any]], rows: Iterable[dict[str, Any]],
    provenance: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    history = dataset_fingerprint(records)
    action_pool = pool_fingerprint(rows)
    payload = {
        "historical_direction_sha256": history["value"],
        "current_action_pool_sha256": action_pool["value"],
        "provenance": provenance or PROVENANCE,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest(), history, action_pool


def _directional_return(row: dict[str, Any], field: str = "next1_ret_pct") -> float | None:
    value = finite(row.get(field))
    if value is None:
        return None
    if row.get("side") == "red":
        return value
    if row.get("side") == "green":
        return -value
    return None


def _sample_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = [value for row in records if (value := _directional_return(row)) is not None]
    if not values:
        return {"count": 0, "hit_rate_pct": None, "average_directional_return_pct": None}
    return {
        "count": len(values),
        "hit_rate_pct": round(sum(value > 0 for value in values) / len(values) * 100, 2),
        "average_directional_return_pct": round(sum(values) / len(values), 4),
    }


def walk_forward_evaluation(
    records: list[dict[str, Any]], train_dates: int = 10, test_dates: int = 5,
) -> dict[str, Any]:
    if train_dates < 2 or test_dates < 1:
        raise ValueError("walk-forward requires train_dates>=2 and test_dates>=1")
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        trade_date = row.get("actual_trade_date") or row.get("target_date")
        if trade_date:
            by_date.setdefault(str(trade_date), []).append(row)
    dates = sorted(by_date)
    folds: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    cursor = 0
    index = 0
    while cursor + train_dates + test_dates <= len(dates):
        train_window = dates[cursor:cursor + train_dates]
        purged_dates = train_window[-1:]
        train = train_window[:-1]
        test = dates[cursor + train_dates:cursor + train_dates + test_dates]
        train_rows = [row for day in train for row in by_date[day]]
        test_rows = [row for day in test for row in by_date[day]]
        train_metrics = _sample_metrics(train_rows)
        test_metrics = _sample_metrics(test_rows)
        base = {
            "index": index,
            "train_start": train[0], "train_end": train[-1],
            "purged_dates": purged_dates, "label_horizon_sessions": 1,
            "test_start": test[0], "test_end": test[-1],
            "train": train_metrics, "test": test_metrics,
        }
        if not train_metrics["count"] or not test_metrics["count"]:
            skipped.append({**base, "reason": "训练段或样本外段没有可评估的T+1方向收益"})
        else:
            base["hit_rate_degradation_pp"] = round(
                train_metrics["hit_rate_pct"] - test_metrics["hit_rate_pct"], 2,
            )
            folds.append(base)
        cursor += test_dates
        index += 1
    if not folds:
        return {
            "status": "insufficient_history", "folds": [], "skipped_folds": skipped,
            "aggregate": {"fold_count": 0, "oos_count": 0, "oos_hit_rate_pct": None,
                          "oos_average_directional_return_pct": None,
                          "positive_fold_consistency_pct": None,
                          "average_hit_rate_degradation_pp": None},
            "methodology": "固定顺序前向分折；当前样本未进行参数优化。",
        }
    oos_rows: list[dict[str, Any]] = []
    for fold in folds:
        for day in dates:
            if fold["test_start"] <= day <= fold["test_end"]:
                oos_rows.extend(by_date[day])
    oos = _sample_metrics(oos_rows)
    positive_folds = sum(
        fold["test"]["average_directional_return_pct"] > 0 for fold in folds
    )
    return {
        "status": "evaluated",
        "configuration": {
            "train_trade_dates": train_dates, "purge_trade_dates": 1,
            "test_trade_dates": test_dates,
            "step_trade_dates": test_dates, "target": "T+1方向收益",
        },
        "folds": folds,
        "skipped_folds": skipped,
        "aggregate": {
            "fold_count": len(folds), "oos_count": oos["count"],
            "oos_hit_rate_pct": oos["hit_rate_pct"],
            "oos_average_directional_return_pct": oos["average_directional_return_pct"],
            "positive_fold_consistency_pct": round(positive_folds / len(folds) * 100, 2),
            "average_hit_rate_degradation_pp": round(
                sum(fold["hit_rate_degradation_pp"] for fold in folds) / len(folds), 2,
            ),
        },
        "methodology": (
            "按交易日顺序使用互不重叠的样本外窗口；每折在训练末端剔除一个T+1标签跨度，"
            "训练段仅作为稳定性基线，没有可调参数时不虚构优化结果。"
        ),
        "limitation": "这是历史方向标签的前向分折验证，不代表模拟盘或真实账户收益。",
    }


def execution_audit(pool: dict[str, Any]) -> dict[str, Any]:
    raw_rows = pool.get("all_rows")
    rows_available = isinstance(raw_rows, list)
    rows = list(raw_rows) if rows_available else []
    latest = pool.get("latest_trade_date")
    invalid_levels = 0
    stale = 0
    unknown_data = 0
    check_failures: Counter[str] = Counter()
    check_unknowns: Counter[str] = Counter()
    risk_flags: Counter[str] = Counter()
    trade_states: Counter[str] = Counter()
    for row in rows:
        support, target, stop = (finite(row.get(key)) for key in ("support", "target", "stop"))
        if (
            support is None or target is None or stop is None
            or not (target > support > stop)
        ):
            invalid_levels += 1
        if latest and row.get("date") != latest:
            stale += 1
        if finite(row.get("price")) is None or not row.get("quote_source") or not row.get("kline_source"):
            unknown_data += 1
        for name, passed in (row.get("checks") or {}).items():
            if passed is False:
                check_failures[str(name)] += 1
            elif passed is None:
                check_unknowns[str(name)] += 1
        for flag in row.get("risk_flags") or []:
            risk_flags[str(flag)] += 1
        if row.get("trade_state"):
            trade_states[str(row["trade_state"])] += 1

    def known(count: int, scope: str) -> dict[str, Any]:
        return {"status": "known", "count": count, "scope": scope}

    def unknown(scope: str, reason: str) -> dict[str, Any]:
        return {"status": "unknown", "count": None, "scope": scope, "reason": reason}

    current_scope = "current_pool"
    if rows_available:
        invalid_blocker = known(invalid_levels, current_scope)
        market_blocker = known(unknown_data, current_scope)
        stale_blocker = (
            known(stale, current_scope) if latest
            else unknown(current_scope, "当前池缺少latest_trade_date")
        )
    else:
        reason = "当前池缺少all_rows数组"
        invalid_blocker = unknown(current_scope, reason)
        market_blocker = unknown(current_scope, reason)
        stale_blocker = unknown(current_scope, reason)

    runtime = pool.get("strict_intraday_audit")
    runtime = runtime if isinstance(runtime, dict) else {}

    def runtime_blocker(key: str, missing_reason: str) -> dict[str, Any]:
        value = runtime.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return known(value, "strict_intraday_upload_jobs")
        return unknown("strict_intraday_upload_jobs", missing_reason)

    return {
        "row_count": len(rows) if rows_available else None, "latest_trade_date": latest,
        "blockers": {
            "invalid_levels": invalid_blocker,
            "stale_rows": stale_blocker,
            "unknown_market_data": market_blocker,
            "pending_close_confirmation": runtime_blocker(
                "pending_close_confirmation", "静态研究快照不包含运行时上传任务存储",
            ),
            "missing_strict_5m_bars": runtime_blocker(
                "missing_strict_5m_bars", "静态研究快照不包含逐任务5分钟覆盖明细",
            ),
        },
        "gate_failure_counts": dict(sorted(check_failures.items())),
        "gate_unknown_counts": dict(sorted(check_unknowns.items())),
        "risk_flag_counts": dict(sorted(risk_flags.items())),
        "trade_state_counts": dict(sorted(trade_states.items())),
        "policy": "unknown保持unknown；只有当前静态池可核验字段计入已知数量。",
    }


def turnover_decay_poc(rows: list[dict[str, Any]], bins: int = 40) -> dict[str, Any]:
    if bins < 2:
        raise ValueError("bins must be at least 2")
    ordered = sorted(rows, key=lambda row: str(row.get("trade_date") or row.get("date") or ""))
    if len(ordered) < 2:
        return {"status": "blocked", "quality": "unknown", "reason": "历史行不足"}
    parsed: list[tuple[float, float, float, float, float]] = []
    for row in ordered:
        open_price, high, low, close, volume, turnover = (
            finite(row.get(key))
            for key in ("open", "high", "low", "close", "volume", "turnover_rate")
        )
        if open_price is None or high is None or low is None or close is None or volume is None:
            return {"status": "blocked", "quality": "unknown", "reason": "OHLCV缺失或非有限数值"}
        if not (high >= max(open_price, close) > 0 and low <= min(open_price, close) and low > 0 and volume > 0):
            return {"status": "blocked", "quality": "invalid", "reason": "OHLCV包络或正值校验失败"}
        if turnover is None:
            return {"status": "blocked", "quality": "unknown", "reason": "turnover_rate缺失"}
        if not 0 <= turnover <= 100:
            return {"status": "blocked", "quality": "invalid", "reason": "turnover_rate超出0—100%"}
        parsed.append((high, low, close, volume, turnover))
    global_low = min(row[1] for row in parsed)
    global_high = max(row[0] for row in parsed)
    if global_high <= global_low:
        return {"status": "blocked", "quality": "invalid", "reason": "价格区间无宽度"}
    width = (global_high - global_low) / bins
    chips = [0.0] * bins
    for high, low, _close, volume, turnover in parsed:
        decay = 1 - turnover / 100
        chips = [value * decay for value in chips]
        start = max(0, min(bins - 1, int((low - global_low) / width)))
        end = max(0, min(bins - 1, int((high - global_low) / width)))
        allocation = volume / (end - start + 1)
        for index in range(start, end + 1):
            chips[index] += allocation
    total = sum(chips)
    poc_index = max(range(bins), key=chips.__getitem__)
    poc = global_low + (poc_index + 0.5) * width
    close = parsed[-1][2]
    zones = sorted(range(bins), key=chips.__getitem__, reverse=True)[:3]
    return {
        "status": "evaluated", "quality": "research_only", "bins": bins,
        "poc": round(poc, 6),
        "poc_side": "support" if poc < close else "resistance" if poc > close else "neutral",
        "current_close": round(close, 6),
        "chip_total": round(total, 6),
        "top_zones": [
            {"price": round(global_low + (index + 0.5) * width, 6),
             "weight_pct": round(chips[index] / total * 100, 4)}
            for index in zones
        ],
        "recurrence": "chips_t = chips_(t-1) × (1-turnover_rate) + 当日区间分布成交量",
    }


def chip_sidecar(path: Path) -> dict[str, Any]:
    caveat = "ETF申购赎回与份额变化会削弱筹码含义；本模块只作研究旁路，不产生动作。"
    if not path.exists():
        return {
            "status": "blocked", "quality": "unknown", "source": "local_turnover_cache",
            "reason": "历史换手率缓存尚未建立", "eligible_symbols": 0,
            "blocked_symbols": None, "items": [], "caveat": caveat,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in payload.get("rows") or []:
        if row.get("symbol"):
            grouped.setdefault(str(row["symbol"]), []).append(row)
    items = []
    blocked = 0
    for symbol in sorted(grouped):
        result = turnover_decay_poc(grouped[symbol])
        if result["status"] == "evaluated":
            items.append({"symbol": symbol, **result})
        else:
            blocked += 1
    return {
        "status": "evaluated" if items else "blocked",
        "quality": "research_only" if items else "unknown",
        "source": "local_turnover_cache", "eligible_symbols": len(items),
        "blocked_symbols": blocked, "items": items, "caveat": caveat,
    }


def build_payload(backtest: dict[str, Any], pool: dict[str, Any], turnover_path: Path,
                  generated_at: str | None = None) -> dict[str, Any]:
    records = list(backtest.get("records") or [])
    pool_rows = list(pool.get("all_rows") or [])
    combined_value, history_fingerprint, action_fingerprint = combined_fingerprint(
        records, pool_rows, PROVENANCE,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "shadow_research_only",
        "production_rules_changed": False,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "algorithm": "sha256", "value": combined_value,
            "record_count": history_fingerprint["record_count"],
            "pool_row_count": action_fingerprint["row_count"],
            "as_of": action_fingerprint["as_of"] or history_fingerprint["as_of"],
            "components": {
                "historical_direction_records": history_fingerprint,
                "current_action_pool": action_fingerprint,
            },
            "provenance": PROVENANCE,
        },
        "walk_forward": walk_forward_evaluation(records),
        "execution_audit": execution_audit(pool),
        "chip_poc": chip_sidecar(turnover_path),
        "promotion_gate": {
            "status": "shadow_only",
            "requirements": [
                "扩大严格盘中样本", "补齐历史换手率和份额变化", "加入账户级真实成本",
                "样本外稳定后由人工确认生产变更",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest", type=Path, default=DEFAULT_BACKTEST)
    parser.add_argument("--pool", type=Path, default=DEFAULT_POOL)
    parser.add_argument("--turnover", type=Path, default=DEFAULT_TURNOVER)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--generated-at")
    args = parser.parse_args()
    backtest = json.loads(args.backtest.read_text(encoding="utf-8"))
    pool = json.loads(args.pool.read_text(encoding="utf-8"))
    payload = build_payload(backtest, pool, args.turnover, args.generated_at)
    atomic_write_json(args.out, payload)
    print(
        f"research audit generated: records={payload['dataset']['record_count']} "
        f"folds={payload['walk_forward']['aggregate']['fold_count']} "
        f"chip={payload['chip_poc']['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
