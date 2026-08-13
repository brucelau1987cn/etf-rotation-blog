from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = ROOT / "src" / "pages" / "index.astro"
COMPASS = ROOT / "src" / "pages" / "a-compass.astro"


def test_home_page_compacts_long_decision_prose():
    source = HOME.read_text(encoding="utf-8")
    for marker in (
        "compactHomeMarketState",
        "compactHomeSummary",
        "compactHomeMainline",
        "aMarketStateShort",
        "aSummaryShort",
        "aMainlineShort",
        "verdict-full",
        "展开完整结论",
    ):
        assert marker in source
    assert "{garden.market_state}</h3>" not in source
    assert "{garden.summary}</p>" in source  # full text still available inside details
    assert "{aSummaryShort}" in source
    assert "{aMarketStateShort}" in source
    assert "{aMainlineShort}" in source


def test_a_compass_compacts_long_night_market_state():
    source = COMPASS.read_text(encoding="utf-8")
    assert "Night/intraday long prose" in source
    assert "text.length > 48" in source
    assert "通信|电网|稀土" in source
    assert "14:30尾盘" in source
    assert "data-stage-label" in source
    assert "{data.stage}" in source


def test_a_compass_removes_redundant_archive_navigation_panel():
    source = COMPASS.read_text(encoding="utf-8")
    assert 'class="links-panel card"' not in source
    assert "完整过程、历史命中与策略验证保留独立归档。" not in source
