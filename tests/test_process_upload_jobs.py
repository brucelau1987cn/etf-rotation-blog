import importlib.util
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "process_upload_jobs.py"
spec = importlib.util.spec_from_file_location("process_upload_jobs", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

TRADE_DATE = "2026-07-14"


def daily_rows():
    return [
        {"date": TRADE_DATE, "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.1},
        {"date": "2026-07-15", "open": 10.1, "high": 10.6, "low": 10.0, "close": 10.4},
    ]


def job(operation="种花", target="10.000"):
    text = f"行业,代码,名称,目标,对应指数,操作\n测试,510310,测试ETF,{target},测试指数,{operation}\n"
    return {"filename": "2026.07.14_有操作ETF名单_规范版.csv", "trade_date": TRADE_DATE, "csv_text": text}


def run(operation, bars, target="10.000"):
    with patch.object(module, "fetch", side_effect=lambda code, adjust: daily_rows()), patch.object(module, "fetch_m5", return_value=bars):
        return module.backtest(job(operation, target))


def bar(time, low, high, close):
    return {"timestamp": f"20260714{time}", "date": TRADE_DATE, "time": time, "open": close, "close": close, "high": high, "low": low}


def test_buy_side_uses_only_afternoon_and_confirms_on_final_bar():
    result = run("种花", [bar("1125", 8.0, 10.3, 9.0), bar("1305", 9.9, 10.2, 10.0), bar("1500", 9.95, 10.3, 10.1)])
    record = result["records"][0]
    assert record["target_hit"] is True
    assert record["close_confirmed"] is True
    assert record["low"] == 9.9
    assert record["granularity"] == "m5_final"
    assert result["by_category"]["伏击"]["confirmation_samples"] == 1
    assert result["by_category"]["伏击"]["confirmed"] == 1


def test_harvest_side_uses_high_greater_than_target():
    result = run("摘花", [bar("1305", 9.0, 10.2, 9.8), bar("1500", 9.1, 10.1, 9.7)])
    record = result["records"][0]
    assert record["target_hit"] is True
    assert record["close_confirmed"] is None
    assert result["by_category"]["兑现"]["target_hit"] == 1


def test_partial_intraday_keeps_confirmation_pending():
    result = run("种花", [bar("1305", 9.8, 10.1, 10.05), bar("1430", 9.9, 10.2, 10.1)])
    record = result["records"][0]
    assert record["target_hit"] is True
    assert record["close_confirmed"] is None
    assert record["granularity"] == "m5_partial"
    assert result["by_category"]["伏击"]["confirmation_samples"] == 0


def test_daily_fallback_is_coarse_and_never_strict_confirmation():
    result = run("种花", [])
    record = result["records"][0]
    assert record["target_hit"] is True
    assert record["close_confirmed"] is None
    assert record["granularity"] == "daily_fallback"
    assert record["strict_intraday"] is False
    assert result["data_quality"]["daily_fallback"] == 1
    assert result["by_category"]["伏击"]["confirmation_samples"] == 0
