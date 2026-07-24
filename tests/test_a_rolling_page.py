from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "src" / "pages" / "a-rolling.astro"

def test_energy_page_renders_dual_chain_and_resilient_polling():
    source = PAGE.read_text(encoding="utf-8")
    assert "多空能量传导链" in source
    assert "双向能量传导轨道" in source
    assert "AI 卖出预警实时研判" in source
    assert "fetch('/api/public/v1/rolling-signals')" in source
    assert 'AStockSubnav active="rolling"' in source
