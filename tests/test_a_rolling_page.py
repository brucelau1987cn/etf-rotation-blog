from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src/pages/a-rolling.astro"
FIXTURE = ROOT / "public/data/a-rolling-signals.json"


def test_energy_fixture_has_complete_canonical_cycle_contract():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    codes = [row["cycle_code"] for row in payload["cycles"]]
    assert payload["schema_version"] == "a-rolling-energy-v3"
    assert len(codes) == len(set(codes)) == 34
    assert codes[:5] == ["PRE", "A1", "A2", "A3", "A4"]
    assert codes[-3:] == ["F5", "F6", "G"]
    assert payload["cycles"][22]["timeframe_minutes"] == 370
    assert payload["transmission"]["lit_count"] == 19


def test_energy_page_renders_dual_chain_and_resilient_polling():
    source = PAGE.read_text(encoding="utf-8")
    assert "多空能量传导链" in source
    assert "双向能量传导轨道" in source
    assert "AI 卖出预警实时研判" in source
    assert "fetch('/api/public/v1/rolling-signals')" in source
    assert "toggle-view-btn" in source
    assert "compact-mode" in source
    assert 'AStockSubnav active="rolling"' in source
