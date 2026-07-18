import copy
import json
from pathlib import Path

from scripts.generate_research_audit import build_payload
from scripts.validate_dashboard_batches import validate


A_ROW = {
    "code": "510300", "name": "沪深300ETF", "date": "2026-07-14", "trade_state": "可持有",
    "strength_level": "A", "risk_level": "低", "price": 4.0, "support": 3.9, "target": 4.3, "stop": 3.7,
    "quote_source": "fixture", "kline_source": "fixture", "checks": {}, "risk_flags": [],
}
A_POOL = {
    "evaluation_date": "2026-07-14", "latest_trade_date": "2026-07-14",
    "summary": {"universe_count": 1}, "all_rows": [A_ROW],
}
BACKTEST = {"records": [{
    "target_date": "2026-07-14", "actual_trade_date": "2026-07-14",
    "side": "red", "code": "510300", "name": "沪深300ETF",
    "prev_close": 3.9, "target_close": 4.0, "next_close": 4.02,
    "day_ret_pct": 2.56, "next1_ret_pct": 0.5, "next3_ret_pct": 1.0,
    "day_hit": True, "next1_hit": True, "next3_hit": True, "excursion_hit": True,
}]}
RESEARCH_AUDIT = build_payload(BACKTEST, A_POOL, Path("/definitely/missing-turnover.json"), "2026-07-14T22:10:00+08:00")
US_ROW = {
    "symbol": "SPY", "name": "SPY", "trade_state": "可持有", "strength_level": "A", "risk_level": "低",
    "price": 600.0, "support": 590.0, "target": 630.0, "stop": 570.0,
}
FIXTURES = {
    "garden-recommendations.json": {
        "date": "2026-07-14", "applies_to": "2026-07-14", "level_data_as_of": "2026-07-14",
        "updated_at": "2026-07-14 22:00 CST", "stage": "22:00夜间最终版", "market_state": "防御",
        "position": "权益0%-10%", "summary": "测试", "plant": [{
            "code": "510300", "name": "沪深300ETF", "status": "候场", "action": "等待确认", "price_date": "2026-07-14",
            "risk_level": "低", "price": 4.0, "support": 3.9, "target": 4.3, "stop": 3.7, "level_status": "ready",
        }], "harvest": [], "watch": [],
    },
    "etf-garden-pool.json": A_POOL,
    "etf-garden-backtest.json": BACKTEST,
    "a-share-mid-macro.json": {
        "version": 1, "generated_at": "2026-07-14 22:05:22 CST", "market": "CN",
        "factors": [{"key": "a"}, {"key": "b"}, {"key": "c"}], "constraint": {"label": "中性"},
    },
    "model-lab/a-share-shadow.json": {
        "latest_trade_date": "2026-07-14", "mode": "shadow_research_only", "production_weights_changed": False,
        "signal_enhancement": {"formal_signal_logic_changed": False, "production_role": "shadow_filter_and_audit_only", "summary": {}, "historical_validation": {}, "coverage": {"symbols_at_least_260": 89}, "feature_parameters": {}},
    },
    "model-lab/a-share-kronos-shadow.json": {
        "latest_trade_date": "2026-07-14", "mode": "shadow_research_only",
        "production_weights_changed": False, "formal_signal_logic_changed": False,
        "production_role": "display_and_audit_only",
        "data_basis": {"adjustment": "qfq", "is_final": True, "universe": "formal_rotation"},
        "forecast_definition": {"horizon_sessions": 5, "future_sessions": ["2026-07-15", "2026-07-16", "2026-07-17", "2026-07-20", "2026-07-21"]},
        "coverage": {"expected_symbols": 1, "predicted_symbols": 1, "failed_symbols": []},
        "items": [{
            "symbol": "510300", "name": "沪深300ETF", "as_of": "2026-07-14", "close": 4.0,
            "steps": [
                {"session": i, "date": day, "open": 4.0, "high": 4.1, "low": 3.9, "close": 4.02}
                for i, day in enumerate(["2026-07-15", "2026-07-16", "2026-07-17", "2026-07-20", "2026-07-21"], 1)
            ],
            "five_day": {"predicted_close": 4.02, "predicted_return_pct": 0.5, "path_high_pct": 2.5, "path_low_pct": -2.5},
            "quality": {"raw_ohlc_valid": True, "raw_errors": []},
        }],
    },
    "model-lab/a-share-research-audit.json": RESEARCH_AUDIT,
    "us-etf-garden.json": {
        "date": "2026-07-13", "updated_at": "2026-07-13T18:31:00-04:00", "stage": "美股收盘版",
        "session_state": "closed", "market_regime": {"state": "risk-off"},
        "flower_signals": {"ready_plant": [{
            **US_ROW, "signal": "候场", "trade_date": "2026-07-13",
        }], "plant": [], "ready_harvest": [], "harvest": [], "exit": []},
    },
    "us-etf-pool.json": {
        "model_date": "2026-07-13", "quote_trade_date": "2026-07-13", "session_state": "closed", "rows": [US_ROW],
    },
    "us-macro-dashboard.json": {
        "version": 2, "generated_at": "2026-07-13T18:31:54-04:00", "risk": {"label": "中性"},
        "market": {"spy": {"date": "2026-07-13"}}, "data_quality": {"failed": 0},
    },
}


def write_fixtures(root: Path, mutate=None):
    payloads = copy.deepcopy(FIXTURES)
    if mutate:
        mutate(payloads)
    for name, payload in payloads.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")


def test_consistent_cross_market_batches_pass(tmp_path):
    write_fixtures(tmp_path)
    result = validate(tmp_path)
    assert result.status == "ok"
    assert result.errors == []
    assert result.batches["a_share"]["date"] == "2026-07-14"
    assert result.batches["us"]["date"] == "2026-07-13"


def test_a_share_final_mixed_batch_is_blocked(tmp_path):
    write_fixtures(tmp_path, lambda p: p["etf-garden-pool.json"].update(latest_trade_date="2026-07-13"))
    result = validate(tmp_path)
    assert result.status == "error"
    assert any("A-share baseline batch mismatch" in error or "A 22:00 final stage" in error for error in result.errors)


def test_a_share_intraday_allows_previous_final_baseline(tmp_path):
    def mutate(payloads):
        payloads["garden-recommendations.json"].update(stage="14:30尾盘操作版", level_data_as_of="2026-07-13")
        payloads["garden-recommendations.json"]["plant"].append({
            **payloads["garden-recommendations.json"]["plant"][0], "code": "510500", "price_date": "2026-07-13",
        })
        payloads["etf-garden-pool.json"]["latest_trade_date"] = "2026-07-13"
        payloads["etf-garden-pool.json"]["all_rows"][0]["date"] = "2026-07-13"
        payloads["model-lab/a-share-shadow.json"]["latest_trade_date"] = "2026-07-13"
        payloads["model-lab/a-share-kronos-shadow.json"]["latest_trade_date"] = "2026-07-13"
        payloads["model-lab/a-share-kronos-shadow.json"]["items"][0]["as_of"] = "2026-07-13"
        payloads["model-lab/a-share-research-audit.json"] = build_payload(
            payloads["etf-garden-backtest.json"], payloads["etf-garden-pool.json"],
            Path("/definitely/missing-turnover.json"), "2026-07-14T14:30:00+08:00",
        )
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert result.status == "ok"


def test_us_macro_mixed_batch_is_blocked(tmp_path):
    def mutate(payloads):
        payloads["us-macro-dashboard.json"]["market"]["spy"]["date"] = "2026-07-10"
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert result.status == "error"
    assert any("US batch mismatch" in error for error in result.errors)


def test_missing_file_is_blocked(tmp_path):
    write_fixtures(tmp_path)
    (tmp_path / "a-share-mid-macro.json").unlink()
    result = validate(tmp_path)
    assert result.status == "error"
    assert "missing file" in result.errors[0]


def test_missing_signal_enhancement_is_blocked(tmp_path):
    def mutate(payloads):
        payloads["model-lab/a-share-shadow.json"].pop("signal_enhancement")
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert result.status == "error"
    assert any("signal_enhancement is required" in error for error in result.errors)


def test_non_finite_enhancement_value_is_blocked(tmp_path):
    def mutate(payloads):
        payloads["model-lab/a-share-shadow.json"]["signal_enhancement"]["summary"] = {"bad": float("nan")}
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert result.status == "error"
    assert any("non-finite" in error for error in result.errors)


def test_invalid_kronos_snapshot_is_blocked(tmp_path):
    def mutate(payloads):
        snapshot = payloads["model-lab/a-share-kronos-shadow.json"]
        snapshot["items"][0]["steps"][0]["close"] = float("nan")
        snapshot["items"].append(copy.deepcopy(snapshot["items"][0]))
        snapshot["coverage"]["predicted_symbols"] = 2
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert result.status == "error"
    assert any("Kronos" in error for error in result.errors)


def test_invalid_research_audit_is_blocked(tmp_path):
    def mutate(payloads):
        audit = payloads["model-lab/a-share-research-audit.json"]
        audit["mode"] = "production"
        audit["dataset"]["value"] = "short"
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert result.status == "error"
    assert any("research-audit" in error for error in result.errors)


def test_research_audit_unknown_count_and_provenance_tamper_are_blocked(tmp_path):
    def mutate(payloads):
        audit = payloads["model-lab/a-share-research-audit.json"]
        audit["execution_audit"]["blockers"]["pending_close_confirmation"]["count"] = 0
        audit["dataset"]["provenance"]["execution_basis"] = "tampered"
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert any("unknown count must be null" in error for error in result.errors)
    assert any("provenance" in error for error in result.errors)


def test_research_audit_xss_and_derived_metric_tampering_are_blocked(tmp_path):
    attack = '<img src=x onerror=alert(document.domain)>'
    def mutate(payloads):
        audit = payloads["model-lab/a-share-research-audit.json"]
        audit["walk_forward"]["aggregate"]["oos_count"] = attack
        audit["execution_audit"]["blockers"]["invalid_levels"]["count"] = 0
        audit["chip_poc"]["eligible_symbols"] = 999
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert result.status == "error"
    assert any("oos_count must be a non-negative integer" in error for error in result.errors)
    assert any("derived payload differs" in error for error in result.errors)


def test_research_audit_rejects_unpurged_t_plus_one_fold(tmp_path):
    def mutate(payloads):
        template = payloads["etf-garden-backtest.json"]["records"][0]
        payloads["etf-garden-backtest.json"]["records"] = [
            {**template, "target_date": f"2026-06-{index:02d}", "actual_trade_date": f"2026-06-{index:02d}"}
            for index in range(1, 21)
        ]
        audit = build_payload(
            payloads["etf-garden-backtest.json"], payloads["etf-garden-pool.json"],
            Path("/definitely/missing-turnover.json"), "2026-07-14T22:10:00+08:00",
        )
        audit["walk_forward"]["folds"][0]["purged_dates"] = [audit["walk_forward"]["folds"][0]["test_start"]]
        payloads["model-lab/a-share-research-audit.json"] = audit
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert any("purge/no-lookahead" in error for error in result.errors)


def test_invalid_stage_and_status_are_blocked(tmp_path):
    def mutate(payloads):
        payloads["garden-recommendations.json"]["stage"] = "自由文本阶段"
        payloads["garden-recommendations.json"]["plant"][0]["status"] = "随便买"
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert any("invalid stage" in error for error in result.errors)
    assert any("invalid status" in error for error in result.errors)


def test_executable_level_order_is_blocked(tmp_path):
    def mutate(payloads):
        payloads["garden-recommendations.json"]["plant"][0]["stop"] = 4.1
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert any("requires stop < support" in error for error in result.errors)


def test_explicit_invalid_level_is_allowed(tmp_path):
    def mutate(payloads):
        item = payloads["garden-recommendations.json"]["plant"][0]
        item.update({"support": 3.9, "target": 3.8, "stop": -1, "level_status": "invalid", "level_invalid_reason": "bad provider levels"})
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert result.status == "ok"


def test_duplicate_pool_code_is_blocked(tmp_path):
    def mutate(payloads):
        payloads["etf-garden-pool.json"]["all_rows"].append(copy.deepcopy(A_ROW))
        payloads["etf-garden-pool.json"]["summary"]["universe_count"] = 2
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert any("duplicate codes" in error for error in result.errors)
