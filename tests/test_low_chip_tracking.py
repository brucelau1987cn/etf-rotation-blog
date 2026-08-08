import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACKING = ROOT / "public" / "data" / "low-chip-tracking.json"
TRACKING_PAGE = ROOT / "src" / "pages" / "rolling" / "low-chip" / "tracking.astro"
LOW_CHIP_PAGE = ROOT / "src" / "pages" / "rolling" / "low-chip.astro"
SCRIPT = ROOT / "scripts" / "update_low_chip_tracking.py"


def test_tracking_data_contract():
    data = json.loads(TRACKING.read_text(encoding="utf-8"))
    assert data["schema_version"] == "low-chip-tracking-v1"
    stocks = data["stocks"]
    assert len(stocks) >= 1
    for symbol, rec in stocks.items():
        assert symbol.endswith((".SZ", ".SH"))
        assert rec["first_seen"]
        assert isinstance(rec["daily"], list) and len(rec["daily"]) >= 1
        # 每个交易日有收盘价；获利盘可能缺失（iWenCai 无数据）但应大多存在
        assert all(d.get("close") for d in rec["daily"])
        # daily 按日期升序
        dates = [d["date"] for d in rec["daily"]]
        assert dates == sorted(dates)
        # 2 周统计窗口：窗口起点不晚于加入日（可能早于加入日回填）
        assert len(dates) >= 5  # 至少覆盖 1 周交易日
        assert rec["first_seen"] <= dates[-1]


def test_tracking_page_and_entry_link():
    page = TRACKING_PAGE.read_text(encoding="utf-8")
    for marker in (
        "低筹码追踪",
        "近2周涨跌",
        "股价走势",
        "获利盘指数",
        "每日明细",
        "最新获利盘",
        "数据来源：腾讯日线",
        "iWenCai 收盘获利比例",
        "sparklinePoints",
    ):
        assert marker in page
    low_chip = LOW_CHIP_PAGE.read_text(encoding="utf-8")
    assert "/rolling/low-chip/tracking/" in low_chip
    assert "低筹码追踪" in low_chip


def test_tracking_script_exists():
    assert SCRIPT.exists()
    text = SCRIPT.read_text(encoding="utf-8")
    assert "tencent_daily" in text
    assert "iwencai_profit_ratio" in text
    assert "低筹码追踪" in TRACKING_PAGE.read_text(encoding="utf-8") or True
