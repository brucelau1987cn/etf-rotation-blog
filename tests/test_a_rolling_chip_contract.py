from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_rolling_chip_payload_uses_same_calculator_change_field():
    component = (ROOT / "src/components/ARollingEnergyMatrix.astro").read_text()
    assert "chip.profit_ratio_change_pp" in component
    assert "chip.profit_ratio - chip.yesterday_profit" not in component


def test_chip_refresh_script_reads_v2_endpoint_fields():
    script = (ROOT / "scripts/update_a_rolling_chip.py").read_text()
    assert '"adjust": "qfq"' in script
    assert '"limit": 90' in script
    assert '"profit_ratio_change_pp"' in script
    assert 'latest["profit_ratio_pct"]' in script
    assert 'latest["concentration_90_pct"]' in script
    assert 'latest["average_cost"]' in script


def test_pages_sync_copies_chip_route_and_helper():
    script = (ROOT / "scripts/sync_edge_quote.mjs").read_text()
    assert "chipRouteTarget" in script
    assert "chipHelperTarget" in script
    assert "from './_chip.js'" in script
