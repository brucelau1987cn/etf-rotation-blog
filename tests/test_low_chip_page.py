import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUBNAV = ROOT / "src" / "components" / "RollingSubnav.astro"
PAGE = ROOT / "src" / "pages" / "rolling" / "low-chip.astro"
DATA = ROOT / "public" / "data" / "a-low-chip-stocks.json"


def test_rolling_subnav_links_low_chip_after_a_share():
    text = SUBNAV.read_text(encoding="utf-8")
    labels = ["A股滚动", "低筹码股", "期货滚动", "港股滚动", "美股滚动", "详细解读"]
    positions = [text.index(label) for label in labels]
    assert positions == sorted(positions)
    assert "'/rolling/low-chip/'" in text


def test_low_chip_page_publishes_week_month_quarter_results():
    page = PAGE.read_text(encoding="utf-8")
    data = json.loads(DATA.read_text(encoding="utf-8"))

    assert 'RollingSubnav active="low-chip"' in page
    assert "70%筹码集中度低于3%" in page
    assert "周线" in page and "月线" in page and "季线" in page
    assert data["data_as_of"] == "2026-07-31"
    assert data["threshold"] == 3
    assert data["metric"] == "70%筹码集中度"
    assert len(data["periods"]["week"]) == 7
    assert len(data["periods"]["month"]) == 2
    assert len(data["periods"]["quarter"]) == 1
    assert data["intersection"] == ["601985.SH"]

    weekly_codes = {item["symbol"] for item in data["periods"]["week"]}
    monthly_codes = {item["symbol"] for item in data["periods"]["month"]}
    quarterly_codes = {item["symbol"] for item in data["periods"]["quarter"]}
    assert quarterly_codes <= monthly_codes <= weekly_codes | {"301551.SZ"}
    assert "601985.SH" in weekly_codes & monthly_codes & quarterly_codes


def test_low_chip_page_uses_compact_tables_and_source_disclosure():
    page = PAGE.read_text(encoding="utf-8")
    data = DATA.read_text(encoding="utf-8")
    for marker in (
        "数据来源：iWenCai",
        "筛选日期：2026-07-31",
        "三个周期同时满足",
        "low-chip-table",
    ):
        assert marker in page
    for stock_name in ("中国核电", "盐田港", "无线传媒"):
        assert stock_name in data
    assert "gradient" not in page.lower()
    assert "innerHTML" not in page
