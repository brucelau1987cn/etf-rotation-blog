from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src/pages/a-rolling.astro"
FIXTURE = ROOT / "public/data/a-rolling-signals.json"


def test_energy_fixture_has_complete_canonical_cycle_contract():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    codes = [row["cycle_code"] for row in payload["cycles"]]
    assert payload["schema_version"] == "a-rolling-energy-v2"
    assert len(codes) == len(set(codes)) == 34
    assert codes[:5] == ["PRE", "A1", "A2", "A3", "A4"]
    assert codes[-3:] == ["F5", "F6", "G"]
    assert payload["cycles"][22]["timeframe_minutes"] == 370
    assert payload["transmission"]["lit_count"] == 34


def test_energy_page_replaces_nine_cycle_matrix_with_transmission_board():
    source = PAGE.read_text(encoding="utf-8")
    assert "34周期能量传导图" in source
    assert "AI卖出信号提醒" in source
    assert "当前没有卖出提醒" in source
    assert "买方能量传导" in source
    assert "九周期矩阵" not in source
    assert "周期加权倾向" not in source
    assert "短、中、长周期" not in source
    assert "CODES = ['PRE','A1'" in source
    assert "payload.cycles.length !== 34" in source


def test_energy_page_discloses_demo_basis_and_keeps_resilient_refresh():
    source = PAGE.read_text(encoding="utf-8")
    assert "跨日期最近信号覆盖，尚未确认为同一轮连续传导" in source
    assert "页面不生成仓位或交易指令" in source
    assert "fetch('/api/public/v1/rolling-signals'" in source
    assert "window.setInterval(refresh, 60000)" in source
    assert "if (inFlight || document.hidden) return" in source
    assert "实时检查失败，继续保留当前有效快照" in source
    assert "escapeHtml" in source
    assert 'AStockSubnav active="rolling"' in source
