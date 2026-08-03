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
    assert "收盘获利比例低于3%" in page
    assert "70%筹码集中度" not in page
    assert "周线" in page and "月线" in page and "季线" in page
    assert data["data_as_of"] == "2026-07-31"
    assert data["threshold"] == 3
    assert data["metric"] == "收盘获利比例"
    assert len(data["periods"]["week"]) == 177
    assert len(data["periods"]["month"]) == 582
    assert len(data["periods"]["quarter"]) == 269
    assert len(data["intersection_before_filters"]) == 29
    assert len(data["intersection"]) == 21
    assert all(not code.endswith(".BJ") for code in data["intersection"])
    assert data["filters"]["excluded_bj"] == ["920065.BJ", "920079.BJ", "920083.BJ", "920189.BJ", "920193.BJ", "920808.BJ"]
    assert data["filters"]["excluded_unlock_risk"] == ["688759.SH", "688765.SH"]
    assert all(data["enrichments"][code]["industry"] != "待补充" for code in data["intersection"])
    assert data["financial_filters"] == {
        "report_period": "20251231",
        "roe_min": 30,
        "net_margin_min": 25,
        "cash_profit_ratio_min": 20,
        "gross_margin_min": 15,
        "debt_ratio_max": 10,
        "labels": {
            "roe": "ROE ≥ 30%",
            "net_margin": "净利率 ≥ 25%",
            "cash_profit_ratio": "现金流/净利润 ≥ 20%",
            "gross_margin": "毛利率 ≥ 15%",
            "debt_ratio": "负债率 ≤ 10%",
        },
    }
    assert all("financials" in data["enrichments"][code] for code in data["intersection"])

    weekly_codes = {item["symbol"] for item in data["periods"]["week"]}
    monthly_codes = {item["symbol"] for item in data["periods"]["month"]}
    quarterly_codes = {item["symbol"] for item in data["periods"]["quarter"]}
    assert set(data["intersection_before_filters"]) == weekly_codes & monthly_codes & quarterly_codes
    assert set(data["intersection"]) < set(data["intersection_before_filters"])
    assert all(0 <= item["value"] < 3 for period in data["periods"].values() for item in period)


def test_low_chip_page_uses_horizontal_rows_and_toolbar_pager():
    page = PAGE.read_text(encoding="utf-8")
    data = DATA.read_text(encoding="utf-8")
    for marker in (
        "数据来源：iWenCai",
        "筛选日期：2026-07-31",
        "三个周期同时满足",
        'class="chip-row"',
        'class="chip-toolbar-right"',
        'id="chip-pager"',
        'id="chip-search-input"',
        ".chip-page-num.is-active",
        ".chip-pager[hidden],.chip-row[hidden]",
        "chip-industry",
        "chip-quality",
        "优质股东 ✓",
        ") : null}",
        'data-filter="roe"',
        'data-filter="net-margin"',
        'data-filter="cash-profit"',
        'data-filter="gross-margin"',
        'data-filter="debt-ratio"',
        "activeFilters.has('roe')",
        "activeFilters.has('debt-ratio')",
        "chip-filter-meta",
        "剔除北交所及未来3个月存在限售股解禁",
        "收盘获利比例",
    ):
        assert marker in page
    toolbar_start = page.index('class="chip-toolbar-right"')
    toolbar_end = page.index("</div>\n        </div>", toolbar_start)
    assert toolbar_start < page.index('id="chip-pager"') < toolbar_end
    assert toolbar_start < page.index('id="chip-search-input"') < toolbar_end
    assert page.index('id="chip-pager"') < page.index('id="chip-search-input"')
    assert 'hidden={index >= PAGE_SIZE}' in page
    for stock_name in ("好莱客", "永新光学", "华大九天"):
        assert stock_name in data
    assert "920065.BJ" not in json.loads(data)["intersection"]
    assert "688759.SH" not in json.loads(data)["intersection"]
    assert "gradient" not in page.lower()
    assert "innerHTML" not in page
