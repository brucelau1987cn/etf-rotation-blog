from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_rolling_subnav_has_insights_after_four_markets():
    text = (ROOT / "src/components/RollingSubnav.astro").read_text(encoding="utf-8")
    labels = ["A股滚动", "期货滚动", "港股滚动", "美股滚动", "详细解读"]
    positions = [text.index(label) for label in labels]
    assert positions == sorted(positions)
    assert "'/rolling/insights/'" in text


def test_daily_rolling_reports_cover_latest_dates():
    page = (ROOT / "src/pages/rolling/insights.astro").read_text(encoding="utf-8")
    component = (ROOT / "src/components/RollingDailyInsightReport.astro").read_text(encoding="utf-8")
    data = (ROOT / "src/data/rolling-daily-insights.ts").read_text(encoding="utf-8")
    hist21 = (ROOT / "src/pages/rolling/insights/2026-08-21.astro").read_text(encoding="utf-8")
    for marker in (
        "8月21日滚动信号收盘复盘",
        "8月20日滚动信号收盘复盘",
        "8月19日滚动信号收盘复盘",
        "8月18日滚动信号收盘复盘",
        "8月17日滚动信号收盘复盘",
        "8月14日滚动信号收盘复盘",
        "8月13日滚动信号收盘复盘",
        "8月12日滚动信号收盘复盘",
        "8月11日滚动信号收盘复盘",
        "8月10日滚动信号收盘复盘",
        "8月7日滚动信号收盘复盘",
        "8月6日滚动信号收盘复盘",
        "8月5日滚动信号收盘复盘",
        "今日操作结论",
        "什么时候买",
        "什么时候卖",
        "今日信号表",
        "逐标的计划",
        "买入条件",
        "卖出纪律",
        'RollingSubnav active="insights"',
        "白银现货",
        "创新医疗",
        "德福科技",
        "海光信息",
        "上海电力",
        "东方明珠",
        "特斯拉",
        "中国宏桥",
        "澜起科技",
    ):
        assert marker in page + component + data + hist21
    assert "2026-08-24" in data and "2026-08-21" in data and "2026-08-20" in data and "2026-08-19" in data and "2026-08-18" in data and "2026-08-17" in data and "2026-08-14" in data
    assert "rollingDailyReports['2026-08-24']" in page
    # Extract just the 2026-08-24 entry (from pos of its start to pos of next date entry)
    # Using regex to find the exact start/end of the 2026-08-24 block
    import re
    m24 = re.search(r"'2026-08-24': \{\n", data)
    m21 = re.search(r"'2026-08-21': \{", data)
    m20 = re.search(r"'2026-08-20': \{", data)
    latest_block = data[m24.start():m21.start()]
    assert latest_block.count("name: '") == 3
    assert latest_block.count("validation: 'confirmed'") == 3
    assert latest_block.count("validation: 'reclaimed'") == 0
    assert latest_block.count("validation: 'mixed'") == 0
    assert latest_block.count("validation: 'watch'") == 0
    assert "德福科技" in latest_block
    assert "创新医疗" in latest_block
    assert "澜起科技" in latest_block
    assert "多方已确认" not in latest_block
    assert "空方已确认" in latest_block
    assert "/rolling/insights/2026-08-21/" in data
    assert "/rolling/insights/2026-08-20/" in data
    assert "/rolling/insights/2026-08-19/" in data
    assert "/rolling/insights/2026-08-18/" in data
    assert "/rolling/insights/2026-08-17/" in data
    assert "/rolling/insights/2026-08-14/" in data
    assert "/rolling/insights/2026-08-13/" in data
    assert "/rolling/insights/2026-08-12/" in data
    assert "/rolling/insights/2026-08-11/" in data
    assert "/rolling/insights/2026-08-10/" in data
    assert "/rolling/insights/2026-08-07/" in data
    assert (ROOT / "src/pages/rolling/insights/2026-08-21.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-08-20.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-08-19.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-08-18.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-08-17.astro").exists()
    assert (ROOT / "src/pages/rolling/insights/2026-08-14.astro").exists()


def test_insights_styles_are_responsive_and_card_light():
    styles = (ROOT / "src/styles/rolling-insights.css").read_text(encoding="utf-8")
    assert "grid-template-columns:repeat(4,minmax(0,1fr))" in styles
    assert ".command-board" in styles
    assert ".price-map" in styles
    assert ".trade-sidebar" in styles
    assert ".insight-navigator" in styles
    assert "@media(max-width:680px)" in styles
    assert "linear-gradient" not in styles


def test_insight_navigator_is_daily_only_after_merge():
    page = (ROOT / "src/pages/rolling/insights.astro").read_text(encoding="utf-8")
    navigator = (ROOT / "src/components/InsightNavigator.astro").read_text(encoding="utf-8")
    catalog = (ROOT / "src/data/rolling-daily-insights.ts").read_text(encoding="utf-8")
    legacy = (ROOT / "src/data/rolling-insights.ts").read_text(encoding="utf-8")
    redirects = (ROOT / "public/_redirects").read_text(encoding="utf-8")

    assert "RollingDailyInsightReport" in page
    for marker in (
        "代码 / 名称 / 首字母",
        "交易日",
        "上一只",
        "下一只",
        "上一交易日分析",
        "下一交易日分析",
        "data-insight-search",
        "data-insight-date",
    ):
        assert marker in navigator
    assert "2026-08-24" in catalog
    assert "2026-08-21" in catalog
    assert "2026-08-20" in catalog
    assert "2026-08-19" in catalog
    assert "2026-08-18" in catalog
    assert "2026-08-17" in catalog
    assert "2026-08-14" in catalog
    assert "/rolling/insights/" in catalog
    assert "rollingInsightArticles: RollingInsightArticle[] = []" in legacy
    assert "/rolling/insights/2026-08-24 /rolling/insights/ 301" in redirects
    assert "/rolling/insights/2026-08-24/ /rolling/insights/ 301" in redirects
    assert "/rolling/insights/2026-08-21 /rolling/insights/2026-08-21/ 301" in redirects
    assert "/rolling/insights/2026-08-21/ /rolling/insights/2026-08-21/ 301" in redirects
    assert "/rolling/insights/2026-08-20 /rolling/insights/2026-08-20/ 301" in redirects
    assert "/rolling/insights/2026-08-19 /rolling/insights/2026-08-19/ 301" in redirects
    assert "/rolling/insights/2026-08-19/ /rolling/insights/ 301" in redirects
    assert "/rolling/insights/2026-08-18 /rolling/insights/2026-08-18/ 301" in redirects
    assert "/rolling/insights/2026-08-17 /rolling/insights/2026-08-17/ 301" in redirects
    assert "/rolling/insights/2026-08-14 /rolling/insights/2026-08-14/ 301" in redirects
