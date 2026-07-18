from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "generate_research_audit", ROOT / "scripts/generate_research_audit.py",
)
assert spec and spec.loader
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def record(day: str, code: str = "510300", side: str = "red", value: float = 1.0):
    return {
        "target_date": day,
        "actual_trade_date": day,
        "side": side,
        "code": code,
        "name": code,
        "prev_close": 1.0,
        "target_close": 1.0,
        "next_close": 1.01,
        "day_ret_pct": 0.0,
        "next1_ret_pct": value,
        "next3_ret_pct": value,
        "day_hit": True,
        "next1_hit": value > 0,
        "next3_hit": value > 0,
        "excursion_hit": True,
        "generated_at": "volatile",
    }


def test_fingerprint_is_deterministic_and_excludes_volatile_fields():
    rows = [record("2026-01-02", "510300", value=1.0), record("2026-01-02", "510300", value=-1.0)]
    first = audit.dataset_fingerprint(rows)
    changed = [dict(rows[1], generated_at="later"), dict(rows[0], generated_at="later")]
    second = audit.dataset_fingerprint(changed)
    assert first["value"] == second["value"]
    assert first["record_count"] == 2
    assert "generated_at" not in first["canonical_fields"]


def test_pool_fingerprint_covers_actions_weights_and_full_row_order():
    rows = [
        {"date": "2026-01-02", "code": "510300", "action": "等待", "suggested_weight": 0.1, "reason": "a"},
        {"date": "2026-01-02", "code": "510300", "action": "伏击", "suggested_weight": 0.2, "reason": "b"},
    ]
    original = audit.pool_fingerprint(rows)
    assert original["value"] == audit.pool_fingerprint(list(reversed(rows)))["value"]
    assert original["value"] == audit.pool_fingerprint([dict(rows[0], generated_at="later"), rows[1]])["value"]
    assert original["value"] != audit.pool_fingerprint([dict(rows[0], action="兑现"), rows[1]])["value"]
    assert original["value"] != audit.pool_fingerprint([dict(rows[0], suggested_weight=0.3), rows[1]])["value"]


def test_combined_fingerprint_binds_provenance():
    rows = [record("2026-01-02")]
    pool = [{"date": "2026-01-02", "code": "510300", "action": "等待"}]
    original, _, _ = audit.combined_fingerprint(rows, pool, audit.PROVENANCE)
    changed = {**audit.PROVENANCE, "execution_basis": "different"}
    modified, _, _ = audit.combined_fingerprint(rows, pool, changed)
    assert original != modified


def test_walk_forward_has_disjoint_oos_windows_and_no_lookahead():
    start = date(2026, 1, 1)
    rows = [record((start + timedelta(days=i)).isoformat(), value=1 if i % 2 else -1) for i in range(20)]
    result = audit.walk_forward_evaluation(rows, train_dates=10, test_dates=5)
    assert result["status"] == "evaluated"
    folds = result["folds"]
    assert len(folds) == 2
    oos_windows = []
    for fold in folds:
        purge_date = fold["purged_dates"][0]
        assert fold["train_end"] < purge_date < fold["test_start"]
        assert fold["label_horizon_sessions"] == 1
        window = {
            (start + timedelta(days=i)).isoformat()
            for i in range(20)
            if fold["test_start"] <= (start + timedelta(days=i)).isoformat() <= fold["test_end"]
        }
        oos_windows.append(window)
    assert oos_windows[0].isdisjoint(oos_windows[1])
    assert result["aggregate"]["oos_count"] == 10


def test_walk_forward_preserves_insufficient_history():
    result = audit.walk_forward_evaluation([record("2026-01-01")], train_dates=10, test_dates=5)
    assert result["status"] == "insufficient_history"
    assert result["aggregate"]["oos_hit_rate_pct"] is None


def test_execution_audit_keeps_runtime_blockers_unknown():
    pool = {
        "latest_trade_date": "2026-01-02",
        "all_rows": [{
            "date": "2026-01-01", "price": None, "quote_source": None,
            "kline_source": None, "support": 1.0, "target": 0.9, "stop": 0.8,
            "checks": {"momentum": False, "strict_5m": None}, "risk_flags": ["数据风险"],
            "trade_state": "观察",
        }],
    }
    result = audit.execution_audit(pool)
    assert result["blockers"]["invalid_levels"]["count"] == 1
    assert result["blockers"]["stale_rows"]["count"] == 1
    assert result["blockers"]["unknown_market_data"]["count"] == 1
    assert result["blockers"]["pending_close_confirmation"]["count"] is None
    assert result["blockers"]["missing_strict_5m_bars"]["status"] == "unknown"
    assert result["gate_failure_counts"] == {"momentum": 1}
    assert result["gate_unknown_counts"] == {"strict_5m": 1}


def test_execution_audit_missing_inputs_remain_unknown_and_runtime_counts_can_be_known():
    missing = audit.execution_audit({})
    assert missing["row_count"] is None
    assert all(item["status"] == "unknown" and item["count"] is None for item in missing["blockers"].values())

    runtime = audit.execution_audit({
        "latest_trade_date": "2026-01-02", "all_rows": [],
        "strict_intraday_audit": {"pending_close_confirmation": 2, "missing_strict_5m_bars": 3},
    })
    assert runtime["blockers"]["pending_close_confirmation"] == {
        "status": "known", "count": 2, "scope": "strict_intraday_upload_jobs",
    }
    assert runtime["blockers"]["missing_strict_5m_bars"]["count"] == 3


def test_turnover_decay_recurrence_and_missing_turnover_gate():
    rows = [
        {"date": "2026-01-01", "open": 9.4, "high": 10, "low": 9, "close": 9.5, "volume": 100, "turnover_rate": 10},
        {"date": "2026-01-02", "open": 9.5, "high": 10, "low": 9, "close": 9.6, "volume": 100, "turnover_rate": 10},
    ]
    result = audit.turnover_decay_poc(rows, bins=4)
    assert result["status"] == "evaluated"
    assert result["chip_total"] == 190.0
    blocked = audit.turnover_decay_poc([dict(row, turnover_rate=None) for row in rows], bins=4)
    assert blocked["status"] == "blocked"
    assert blocked["quality"] == "unknown"
    assert blocked["reason"] == "turnover_rate缺失"


def test_payload_schema_and_production_freeze(tmp_path):
    backtest = {"records": [record("2026-01-01")]}
    pool = {"latest_trade_date": "2026-01-01", "all_rows": []}
    payload = audit.build_payload(
        backtest, pool, tmp_path / "missing-turnover.json", "2026-01-02T00:00:00+00:00",
    )
    assert payload["schema_version"] == "research_audit_v1"
    assert payload["mode"] == "shadow_research_only"
    assert payload["production_rules_changed"] is False
    assert payload["dataset"]["algorithm"] == "sha256"
    assert payload["dataset"]["pool_row_count"] == 0
    changed_pool = {"latest_trade_date": "2026-01-01", "all_rows": [{"date": "2026-01-01", "code": "510300", "price": 1.0}]}
    changed = audit.build_payload(
        backtest, changed_pool, tmp_path / "missing-turnover.json", "2026-01-02T00:00:00+00:00",
    )
    assert changed["dataset"]["value"] != payload["dataset"]["value"]
    assert payload["dataset"]["provenance"] == audit.PROVENANCE
    assert payload["dataset"]["provenance"]["cost_model"]["applied"] is False
    assert payload["chip_poc"]["status"] == "blocked"
    assert payload["chip_poc"]["blocked_symbols"] is None
