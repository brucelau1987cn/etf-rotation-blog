import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUBNAV = ROOT / "src" / "components" / "RollingSubnav.astro"
PAGE = ROOT / "src" / "pages" / "rolling" / "low-chip.astro"
MODE_NAV = ROOT / "src" / "components" / "LowChipModeNav.astro"
DATA = ROOT / "public" / "data" / "a-low-chip-stocks.json"
HISTORY_INDEX = ROOT / "public" / "data" / "low-chip-history-index.json"
HISTORY_DIR = ROOT / "public" / "data" / "low-chip-history"
ARCHIVE_SCRIPT = ROOT / "scripts" / "archive_low_chip_snapshot.py"


def test_rolling_subnav_links_low_chip_after_a_share():
    text = SUBNAV.read_text(encoding="utf-8")
    labels = ["A股滚动", "低筹码股", "期货滚动", "港股滚动", "美股滚动", "详细解读"]
    positions = [text.index(label) for label in labels]
    assert positions == sorted(positions)
    assert "'/rolling/low-chip/'" in text


def test_low_chip_page_hides_private_screening_strategy():
    page = PAGE.read_text(encoding="utf-8")
    public_source = page + MODE_NAV.read_text(encoding="utf-8")
    data = json.loads(DATA.read_text(encoding="utf-8"))

    assert 'RollingSubnav active="low-chip"' in page
    for private_copy in (
        "收盘获利比例低于3%",
        "周线、月线、季线均低于3%",
        "三个周期同时满足",
        "三周期（周线/月线/季线）",
        "指标：收盘获利比例",
        "阈值：低于3%",
        "周月季收盘获利筛选",
        "ROE ≥ 30%",
        "净利率 ≥ 25%",
        "现金流/净利润 ≥ 20%",
        "毛利率 ≥ 15%",
        "负债率 ≤ 10%",
        "周/月/季交集",
    ):
        assert private_copy not in public_source
    assert "内部模型观察列表" in page
    assert "initialData: lowChipData" not in page
    assert "initialData: initialClientData" in page
    assert "historyIndex: lowChipHistoryIndex" not in page
    assert "historyIndex: clientHistoryIndex" in page
    for escaped_metric in (
        "esc(sm.chip_focus || '—')",
        "esc(sm.main_force || '—')",
        "esc(shrinkLabel(sm.main_force_label))",
        "esc(sm.report_period || '—')",
    ):
        assert escaped_metric in page
    assert "70%筹码集中度" not in page
    assert len(data["data_as_of"]) == 10
    assert data["data_as_of"] == data["generated_at"][:10]
    assert data["threshold"] == 3
    assert data["metric"] == "收盘获利比例"
    assert all(data["periods"][period] for period in ("week", "month", "quarter"))
    # 新股（上市不足90天）被排除
    assert data["filters"]["exclude_new_listing"] is True
    assert data["filters"]["listing_min_days"] == 90
    assert data["filters"]["listing_cutoff"] < data["data_as_of"]
    assert isinstance(data["filters"]["excluded_new_listing"], list)
    assert len(data["intersection_before_filters"]) >= len(data["intersection"])
    assert all(not code.endswith(".BJ") for code in data["intersection"])
    assert all(data["enrichments"][code]["industry"] != "待补充" for code in data["intersection"])
    assert data["financial_filters"]["report_period"]
    assert data["financial_filters"]["roe_min"] == 30
    assert data["financial_filters"]["net_margin_min"] == 25
    assert data["financial_filters"]["cash_profit_ratio_min"] == 20
    assert data["financial_filters"]["gross_margin_min"] == 15
    assert data["financial_filters"]["debt_ratio_max"] == 10
    assert all("financials" in data["enrichments"][code] for code in data["intersection"])
    assert data["shareholder_metrics"]["fields"] == ["股东人数", "较上期变化", "筹码集中度", "十大流通股东", "报告期", "人均流通股", "主力控盘(机构参与度)"]
    assert all("shareholder_metrics" in data["enrichments"][code] for code in data["intersection"])
    # 301677 (欣兴工具) is in today's intersection and is a new listing (no top10)
    # 新股已排除 → intersection 为空，无 enrichment 断言；历史 08-03 存档仍有标的
    hist_0803 = json.loads((HISTORY_DIR / "2026-08-03.json").read_text(encoding="utf-8"))
    baoming = hist_0803["enrichments"]["002992.SZ"]
    assert baoming["theme_concepts"][:3] == ["小米概念", "无人机", "比亚迪概念"]
    assert "小米概念" in baoming["sector_with_theme"] and "无人机" in baoming["sector_with_theme"] and "比亚迪概念" in baoming["sector_with_theme"]

    weekly_codes = {item["symbol"] for item in data["periods"]["week"]}
    monthly_codes = {item["symbol"] for item in data["periods"]["month"]}
    quarterly_codes = {item["symbol"] for item in data["periods"]["quarter"]}
    assert set(data["intersection_before_filters"]) == weekly_codes & monthly_codes & quarterly_codes
    assert set(data["intersection"]) <= set(data["intersection_before_filters"])
    assert all(0 <= item["value"] < 3 for period in data["periods"].values() for item in period)


def test_low_chip_history_archive_and_query_ui():
    page = PAGE.read_text(encoding="utf-8")
    index = json.loads(HISTORY_INDEX.read_text(encoding="utf-8"))
    assert ARCHIVE_SCRIPT.exists()
    assert index["schema_version"] == "a-low-chip-history-index-v1"
    assert index["latest"] == index["dates"][0]
    assert index["latest"] == json.loads(DATA.read_text(encoding="utf-8"))["data_as_of"]
    assert len(index["dates"]) >= 1
    for day in index["dates"]:
        path = HISTORY_DIR / f"{day}.json"
        assert path.exists(), day
        snap = json.loads(path.read_text(encoding="utf-8"))
        assert snap["data_as_of"] == day
        assert "intersection" in snap
    for marker in (
        'id="chip-history-calendar-btn"',
        'id="chip-history-calendar"',
        'id="chip-calendar-grid"',
        'id="chip-calendar-month-prev"',
        'id="chip-calendar-month-next"',
        'class="chip-calendar-count"',
        'data-calendar-date={item.date}',
        'data-calendar-count={item.intersection_count}',
        'aria-label="选择历史筛选日期"',
        'id="chip-history-prev"',
        'id="chip-history-next"',
        "历史日期",
        "low-chip-history-index.json",
        "/api/public/v1/low-chip-metrics?date=",  # D1-backed history query
        "loadDate",
        "renderCalendar",
        "item.intersection_count > 0 ? ' has-results' : ' is-zero'",
        "有标的日期",
        "historyIndex",
    ):
        assert marker in page
    assert 'id="chip-history-date"' not in page
    assert ".chip-section{margin-top:1rem;overflow:visible}" in page


def test_low_chip_page_uses_horizontal_rows_and_toolbar_pager():
    page = PAGE.read_text(encoding="utf-8")
    data = DATA.read_text(encoding="utf-8")
    for marker in (
        "数据来源：同花顺",
        "观察日期：{lowChipData.data_as_of}",
        "内部模型观察列表",
        'class="chip-row"',
        'class="chip-toolbar-right"',
        'id="chip-pager"',
        'id="chip-search-input"',
        ".chip-page-num.is-active",
        ".chip-pager[hidden],.chip-row[hidden]",
        "chip-industry",
        "chip-theme",
        "chip-metric-asof",
        "chip-asof-val",
        "chip-quality",
        "优质股东 ✓",
        "机构股东 ●",
        "institutionalShareholder",
        ".chip-quality,.chip-institutional",
        "themeConcept",
        "查询日期股价",
        "chip-row-top",
        "chip-row-shareholders",
        "chip-shr-head",
        "chip-shr-body",
        "股东人数",
        "股东变化",
        "主力控盘",
        "筹码集中度",
        "十大流通股东",
    ):
        assert marker in page
    toolbar_start = page.index('class="chip-toolbar-right"')
    toolbar_end = page.index("</div>\n        </div>", toolbar_start)
    assert toolbar_start < page.index('id="chip-pager"') < toolbar_end
    assert toolbar_start < page.index('id="chip-search-input"') < toolbar_end
    assert page.index('id="chip-pager"') < page.index('id="chip-search-input"')
    assert "今日无符合标的" in page
    assert all(not code.endswith(".BJ") for code in json.loads(data)["intersection"])
    assert "gradient" not in page.lower()
