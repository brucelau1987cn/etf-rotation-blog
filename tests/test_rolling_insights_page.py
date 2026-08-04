from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_rolling_subnav_has_insights_after_four_markets():
    text = (ROOT / "src/components/RollingSubnav.astro").read_text(encoding="utf-8")
    labels = ["A股滚动", "期货滚动", "港股滚动", "美股滚动", "详细解读"]
    positions = [text.index(label) for label in labels]
    assert positions == sorted(positions)
    assert "'/rolling/insights/'" in text


def test_daily_rolling_reports_cover_july_30_31_and_august_3():
    page = (ROOT / "src/pages/rolling/insights.astro").read_text(encoding="utf-8")
    component = (ROOT / "src/components/RollingDailyInsightReport.astro").read_text(encoding="utf-8")
    data = (ROOT / "src/data/rolling-daily-insights.ts").read_text(encoding="utf-8")
    july30 = (ROOT / "src/pages/rolling/insights/2026-07-30.astro").read_text(encoding="utf-8")
    july31 = (ROOT / "src/pages/rolling/insights/2026-07-31.astro").read_text(encoding="utf-8")
    for marker in (
        "8月3日滚动信号收盘复盘",
        "7月31日滚动信号收盘复盘",
        "7月30日滚动信号收盘复盘",
        "今日操作结论",
        "什么时候买",
        "什么时候卖",
        "今日信号表",
        "买入条件",
        "卖出纪律",
        'RollingSubnav active="insights"',
    ):
        assert marker in page + component + data + july30 + july31
    for marker in (
        "深科技",
        "华天科技",
        "德福科技",
        "东方明珠",
        "三安光电",
        "海光信息",
        "长鑫科技",
        "中国宏桥",
        "澜起科技H股",
        "白银现货",
        "白银期货",
        "特斯拉",
        "创新医疗",
    ):
        assert marker in data
    assert "2026-08-03" in data and "2026-07-31" in data and "2026-07-30" in data
    assert "/rolling/insights/2026-07-31/" in data
    assert "/rolling/insights/2026-07-30/" in data
    assert "002173" in data
    # single-stock pages removed from catalog
    assert "cxyl" not in data
    assert not (ROOT / "src/pages/rolling/insights/2026-08-04.astro").exists()


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
    assert "2026-08-03" in catalog
    assert "2026-07-31" in catalog
    assert "2026-07-30" in catalog
    assert "/rolling/insights/" in catalog
    assert "rollingInsightArticles: RollingInsightArticle[] = []" in legacy
    assert "/rolling/insights/2026-08-04 /rolling/insights/ 301" in redirects
