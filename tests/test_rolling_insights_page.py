from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_rolling_subnav_has_insights_after_four_markets():
    text = (ROOT / "src/components/RollingSubnav.astro").read_text(encoding="utf-8")
    labels = ["A股滚动", "期货滚动", "港股滚动", "美股滚动", "详细解读"]
    positions = [text.index(label) for label in labels]
    assert positions == sorted(positions)
    assert "'/rolling/insights/'" in text


def test_innovation_medical_report_has_indicator_and_trade_plan_contract():
    page = (ROOT / "src/pages/rolling/insights.astro").read_text(encoding="utf-8")
    for marker in (
        "创新医疗",
        "002173",
        "今日操作结论",
        "次日等待反弹减仓，空仓等待修复确认",
        "什么时候买",
        "什么时候卖",
        "120m / 150m 同价确认",
        "收复 ¥19.56 后观察",
        "放量站稳¥20.36后确认买入",
        "反弹¥19.56—19.81分批减仓",
        "¥18.20—18.60",
        "价格作战地图",
        "收盘证据链",
        "三种走势，三套动作",
        "过热多方降级",
        "低位空方降级",
        "同价节点合并",
        "RollingSubnav active=\"insights\"",
    ):
        assert marker in page


def test_insights_styles_are_responsive_and_card_light():
    styles = (ROOT / "src/styles/rolling-insights.css").read_text(encoding="utf-8")
    assert "grid-template-columns:repeat(4,minmax(0,1fr))" in styles
    assert ".command-board" in styles
    assert ".price-map" in styles
    assert ".trade-sidebar" in styles
    assert ".insight-navigator" in styles
    assert "@media(max-width:680px)" in styles
    assert "linear-gradient" not in styles


def test_insight_navigator_separates_stock_and_trade_date_navigation():
    page = (ROOT / "src/pages/rolling/insights.astro").read_text(encoding="utf-8")
    navigator = (ROOT / "src/components/InsightNavigator.astro").read_text(encoding="utf-8")
    catalog = (ROOT / "src/data/rolling-insights.ts").read_text(encoding="utf-8")

    assert "InsightNavigator" in page
    assert 'currentSymbol="002173"' in page
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
    assert "2026-07-30" in catalog
    assert "/rolling/insights/" in catalog
