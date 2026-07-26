from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "src" / "pages" / "rolling.astro"
MATRIX = ROOT / "src" / "components" / "ARollingEnergyMatrix.astro"
ALERTS = ROOT / "src" / "components" / "ARollingAiAlerts.astro"
APP = ROOT / "public" / "js" / "a-rolling-app.js"


def test_energy_page_renders_multi_market_rolling_shell_and_resilient_polling():
    source = PAGE.read_text(encoding="utf-8")
    matrix = MATRIX.read_text(encoding="utf-8")
    alerts = ALERTS.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    assert "多标的双向能量传导" in matrix
    assert "AI 卖出预警实时研判" in alerts
    assert "RollingSubnav" in source
    assert 'data-market="a"' in source
    assert "/api/public/v1/rolling-signals" in app
    assert "startMarketPoll" in app
    assert "calendarMarket" in app
