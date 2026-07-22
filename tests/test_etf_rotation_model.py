#!/usr/bin/env python3
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_etf_rotation_pool as gen
import generate_garden_pool as garden


def approx(value: float, expected: float, tol: float = 1e-6) -> None:
    assert abs(value - expected) <= tol, f"expected {expected}, got {value}"


def test_calc_slope_momentum_detects_quality_trend() -> None:
    smooth_up = [1.0 * (1.01 ** i) for i in range(20)]
    choppy = [1, 1.03, 0.99, 1.04, 1.0, 1.05, 1.01, 1.06, 1.02, 1.07, 1.03, 1.08, 1.04, 1.09, 1.05, 1.10, 1.06, 1.11, 1.07, 1.12]
    down = [1.2 * (0.995 ** i) for i in range(20)]

    smooth_score = gen.calc_slope_momentum(smooth_up)
    choppy_score = gen.calc_slope_momentum(choppy)
    down_score = gen.calc_slope_momentum(down)

    assert smooth_score > 0
    assert smooth_score > choppy_score
    assert down_score < 0


def test_score_row_combines_dual_momentum_and_risk_adjustment() -> None:
    strong = {
        "ret5": 4.0,
        "slope20_score": 1.2,
        "slope60_score": 0.4,
        "checks": {"price_above_ma": True, "ma20_above_ma60": True},
        "volume_ratio": 1.35,
        "close_position": 0.85,
        "relative_strength": 2.0,
        "chip_ice_score": 72,
        "risk_penalty": 0,
    }
    weak = {
        "ret5": -1.0,
        "slope20_score": -0.2,
        "slope60_score": -0.1,
        "checks": {"price_above_ma": False, "ma20_above_ma60": False},
        "volume_ratio": 0.8,
        "close_position": 0.35,
        "relative_strength": -1.0,
        "chip_ice_score": 35,
        "risk_penalty": 8,
    }

    strong_score = gen.score_signal(strong)
    weak_score = gen.score_signal(weak)

    assert strong_score >= 80
    assert weak_score < 50
    assert strong_score > weak_score


def test_decide_action_and_regime_are_actionable() -> None:
    buy = gen.decide_action({"signal_score": 86, "momentum_rank": 2, "status": "core", "risk_flags": []})
    hold = gen.decide_action({"signal_score": 72, "momentum_rank": 5, "status": "core", "risk_flags": []})
    exit_action = gen.decide_action({"signal_score": 44, "momentum_rank": 16, "status": "watch", "risk_flags": ["跌破20日线"]})

    assert buy == "加仓"
    assert hold == "持有"
    assert exit_action == "退出"

    offensive = gen.detect_market_regime([{"signal_score": 80}] * 8 + [{"signal_score": 60}] * 2)
    defensive = gen.detect_market_regime([{"signal_score": 75}, {"signal_score": 62}, {"signal_score": 45}])

    assert offensive["state"] == "进攻"
    assert defensive["state"] in {"防御", "极弱"}


def test_garden_trading_agent_decision_outputs_debate_and_cooldown() -> None:
    strong = {
        "ret3": 3.0,
        "ret5": 6.0,
        "ret20": 12.0,
        "slope20_score": 0.8,
        "slope60_score": 0.3,
        "volume_ratio": 1.45,
        "close_position": 0.82,
        "status": "core",
        "risk_flags": [],
        "checks": {"price_above_ma": True, "ma_rising": True},
    }
    decision = garden.trading_agent_decision(strong)

    assert decision["signal_score"] >= 70
    assert decision["action"] in {"可持有", "回踩候选", "观察"}
    assert decision["agent_bull"]
    assert decision["agent_bear"]
    assert "组合经理" in decision["agent_scores"]

    overheated = {**strong, "ret5": 12.0, "close_position": 0.96}
    hot_decision = garden.trading_agent_decision(overheated)
    assert hot_decision["cooldown_state"] == "止盈观察"
    assert "高位过热" in hot_decision["risk_flags"]
    assert hot_decision["trade_state"] == "观察"
    assert hot_decision["action"] == "观察"


def test_parse_js_object_array_extracts_youth_pool_items() -> None:
    raw = "[{name:`纳指 ETF`,code:`159501`,exchange_code:`XSHE`,asset_type:`海外`}]"
    assert gen.parse_js_object_array(raw) == [
        {"name": "纳指 ETF", "code": "159501", "exchange_code": "XSHE", "asset_type": "海外"}
    ]


def test_parse_stock_api_klines_normalizes_rows() -> None:
    rows = gen.parse_stock_api_klines(
        [
            {"date": "2026-06-04", "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 1000, "source": "tencent"},
            {"date": "2026-06-03", "close": 1.0, "source": "tencent"},
            {"date": "2026-06-02", "close": None},
        ]
    )

    assert [x["date"] for x in rows] == ["2026-06-03", "2026-06-04"]
    assert rows[-1]["volume"] == 1000.0
    assert rows[-1]["source"] == "tencent"


if __name__ == "__main__":
    test_parse_js_object_array_extracts_youth_pool_items()
    test_parse_stock_api_klines_normalizes_rows()
    test_calc_slope_momentum_detects_quality_trend()
    test_score_row_combines_dual_momentum_and_risk_adjustment()
    test_decide_action_and_regime_are_actionable()
    test_garden_trading_agent_decision_outputs_debate_and_cooldown()
    print("ok")
