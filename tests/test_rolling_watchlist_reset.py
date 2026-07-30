import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data"

EXPECTED = [
    ("上海电力", "SSE", "600021", "2026-07-28", []),
    ("创新医疗", "SZSE", "002173", "2026-07-24", [
        ("BUY", "1.75h", "1小时45分钟", "2026-07-24 09:30:00+08:00"),
        ("BUY", "4h", "4小时00分钟", "2026-07-27 09:30:00+08:00"),
        ("BUY", "4.5h", "4小时30分钟", "2026-07-24 09:30:00+08:00"),
        ("BUY", "5h", "5小时00分钟", "2026-07-27 09:30:00+08:00"),
        ("BUY", "5.5h", "5小时30分钟", "2026-07-24 09:30:00+08:00"),
    ]),
    ("三安光电", "SSE", "600703", "2026-07-21", []),
    ("深科技", "SZSE", "000021", "2026-07-21", [
        ("SELL", "15m", "15分钟", "2026-07-28 10:45:01+08:00"),
        ("SELL", "10m", "10分钟", "2026-07-28 10:55:00+08:00"),
    ]),
    ("德福科技", "SZSE", "301511", "2026-07-21", [
        ("SELL", "15m", "15分钟", "2026-07-28 10:15:07+08:00"),
        ("SELL", "10m", "10分钟", "2026-07-28 10:45:06+08:00"),
    ]),
    ("民爆光电", "SZSE", "301362", "2026-07-21", [
        ("SELL", "10m", "10分钟", "2026-07-28 10:55:05+08:00"),
    ]),
    ("海光信息", "SSE", "688041", "2026-07-28", []),
    ("东方明珠", "SSE", "600637", "2026-07-28", []),
    ("长鑫科技", "SSE", "688825", "2026-07-28", []),
    ("特斯拉", "NASDAQ", "TSLA", "2026-07-28", []),
    ("中国宏桥", "HKEX", "01378", "2026-07-14", [
        ("BUY", "1.75h", "1小时45分钟", "2026-07-15 14:45:00+08:00"),
        ("BUY", "2h", "2小时00分钟", "2026-07-14 15:30:00+08:00"),
        ("BUY", "2.5h", "5小时30分钟", "2026-07-15 14:30:00+08:00"),
        ("BUY", "3h", "6小时00分钟", "2026-07-23 09:30:00+08:00"),
        ("SELL", "10m", "10分钟", "2026-07-24 09:30:00+08:00"),
    ]),
    ("国民技术", "SZSE", "300077", "2026-07-28", []),
    ("华天科技", "SZSE", "002185", "2026-07-28", []),
    ("澜起科技", "HKEX", "06809", "2026-07-30", [
        ("SELL", "15m", "15m", "2026-07-30T07:15:08.097Z"),
    ]),
]


def load_snapshot(path: str) -> dict:
    return json.loads((DATA / path.removeprefix("/data/")).read_text(encoding="utf-8"))


def test_rolling_watchlist_and_signals_match_authorized_reset():
    config = json.loads((DATA / "a-rolling-instruments.json").read_text(encoding="utf-8"))
    assert [(row["instrument_name"], row["exchange"], row["symbol"]) for row in config["instruments"]] == [
        (name, exchange, symbol) for name, exchange, symbol, _, _ in EXPECTED
    ]

    for (name, exchange, symbol, start_date, expected_events), configured in zip(EXPECTED, config["instruments"]):
        snapshot = load_snapshot(configured["snapshot"])
        assert snapshot["instrument"] == {"instrument_name": name, "exchange": exchange, "symbol": symbol}
        assert snapshot["transmission"]["start_date"] == start_date
        events = [(event["type"], event["code"], event["label"], event["triggered_at"]) for event in snapshot["timeline"]]
        assert events == expected_events
        assert snapshot["transmission"]["buy_count"] == sum(event[0] == "BUY" for event in expected_events)
        assert snapshot["transmission"]["sell_count"] == sum(event[0] == "SELL" for event in expected_events)
        assert snapshot["transmission"]["lit_count"] == len(expected_events)
        assert len(snapshot["cycles"]) == sum(event[0] == "BUY" for event in expected_events)
        assert len(snapshot["sell_chain"]["nodes"]) == sum(event[0] == "SELL" for event in expected_events)
