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
        "创新医疗 002173",
        "VOL＋MA20＋MACD＋RSI共振",
        "筹码峰与主力动向",
        "¥18.20—18.60",
        "¥19.95—20.30",
        "¥22.70—22.80",
        "¥19.46 / ¥18.18",
        "多方过热过滤",
        "空方衰竭过滤",
        "同价节点合并",
        "RollingSubnav active=\"insights\"",
    ):
        assert marker in page


def test_insights_styles_are_responsive_and_card_light():
    styles = (ROOT / "src/styles/rolling-insights.css").read_text(encoding="utf-8")
    assert "grid-template-columns:repeat(4,minmax(0,1fr))" in styles
    assert "@media(max-width:680px)" in styles
    assert "linear-gradient" not in styles
