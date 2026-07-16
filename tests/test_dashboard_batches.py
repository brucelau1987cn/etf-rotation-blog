import copy
import json
from pathlib import Path

from scripts.validate_dashboard_batches import validate


A_ROW = {
    "code": "510300", "name": "沪深300ETF", "date": "2026-07-14", "trade_state": "可持有",
    "strength_level": "A", "risk_level": "低", "price": 4.0, "support": 3.9, "target": 4.3, "stop": 3.7,
}
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
    "etf-garden-pool.json": {
        "evaluation_date": "2026-07-14", "latest_trade_date": "2026-07-14",
        "summary": {"universe_count": 1}, "all_rows": [A_ROW],
    },
    "a-share-mid-macro.json": {
        "version": 1, "generated_at": "2026-07-14 22:05:22 CST", "market": "CN",
        "factors": [{"key": "a"}, {"key": "b"}, {"key": "c"}], "constraint": {"label": "中性"},
    },
    "model-lab/a-share-shadow.json": {
        "latest_trade_date": "2026-07-14", "mode": "shadow_research_only", "production_weights_changed": False,
        "signal_enhancement": {"formal_signal_logic_changed": False, "production_role": "shadow_filter_and_audit_only", "summary": {}, "historical_validation": {}},
    },
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
        payloads["model-lab/a-share-shadow.json"]["latest_trade_date"] = "2026-07-13"
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
