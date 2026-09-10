from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_a_momentum_renders_isolated_pattern_research_panel():
    component = (ROOT / "src/components/AMomentumSideStack.astro").read_text(encoding="utf-8")
    page = (ROOT / "src/pages/a-momentum.astro").read_text(encoding="utf-8")
    assert "pattern_research" in component
    assert "形态研究影子层" in component
    assert "生产权重、仓位和交易动作保持不变" in component
    assert "<AMomentumSideStack />" in page
