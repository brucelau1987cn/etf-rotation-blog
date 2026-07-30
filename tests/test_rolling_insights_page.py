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
        "空仓继续等待，持仓执行减仓",
        "什么时候买",
        "什么时候卖",
        "120m / 150m 同价确认",
        "收复 ¥19.95 后观察",
        "站稳¥20.44才确认买入",
        "¥19.56 下方维持减仓",
        "¥18.20—18.60",
        "价格作战地图",
        "今日证据链",
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
    assert "@media(max-width:680px)" in styles
    assert "linear-gradient" not in styles
