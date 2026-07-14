from scripts.generate_us_etf_garden import flower_signals


def row(**overrides):
    base = {
        "symbol": "SPY",
        "name": "SPDR S&P 500 ETF Trust",
        "theme": "美国宽基",
        "price": 100.0,
        "support": 98.0,
        "target": 104.0,
        "stop": 95.0,
        "strength_level": "A",
        "trend_score": 80.0,
        "risk_level": "低",
        "trade_state": "可持有",
        "ret20": 5.0,
        "relative_spy20": 0.0,
        "trade_date": "2026-07-14",
        "day_low": 97.0,
        "day_high": 101.0,
        "momentum_pass": True,
    }
    base.update(overrides)
    return base


def test_partial_previous_snapshot_is_ignored():
    signals = flower_signals([row()], {"SPY": {"symbol": "SPY"}})
    assert signals["exit"] == []
    assert signals["harvest"] == []
    assert signals["plant"] == []
    assert [item["symbol"] for item in signals["ready_plant"]] == ["SPY"]


def test_complete_previous_snapshot_still_triggers_exit():
    previous = {"SPY": {"symbol": "SPY", "support": 99.0, "target": 105.0, "stop": 97.5}}
    signals = flower_signals([row(day_low=97.0)], previous)
    assert [item["symbol"] for item in signals["exit"]] == ["SPY"]
    assert signals["exit"][0]["trigger_level"] == 97.5
