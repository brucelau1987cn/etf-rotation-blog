from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src/pages/a-rolling.astro"
FIXTURE = ROOT / "public/data/a-rolling-signals-demo.json"


def test_rolling_fixture_has_complete_unique_cycle_contract():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected = ["10m", "30m", "1h", "2h", "3h", "4h", "5h", "6h", "1D"]
    actual = [row["timeframe"] for row in payload["signals"]]

    assert payload["schema_version"] == "a-rolling-public-v1"
    assert payload["mode"] in {"demo", "live"}
    assert actual == expected
    assert len(actual) == len(set(actual)) == 9
    assert all(row["direction"] in {"BUY", "SELL", "UNKNOWN", "CONFLICT"} for row in payload["signals"])
    assert all(row["latest_signal_at"] for row in payload["signals"])


def test_rolling_terminal_keeps_high_cycle_weight_and_filters_explicit():
    source = PAGE.read_text(encoding="utf-8")

    assert "'10m': 1" in source
    assert "'6h': 5" in source
    assert "'1D': 7" in source
    assert source.index("'10m': 1") < source.index("'1D': 7")
    assert "高周期权重更高" in source
    assert "6小时 / 日线" in source

    filters = re.findall(r'data-filter="(all|buy|sell|latest)"', source)
    assert filters == ["all", "buy", "sell", "latest"]
    assert 'role="status" aria-live="polite"' in source
    assert "row.hidden = !matches" in source
    assert "group.hidden = !group.querySelector('.signal-row:not([hidden])')" in source


def test_rolling_terminal_uses_one_signal_dom_and_discloses_demo_boundary():
    source = PAGE.read_text(encoding="utf-8")

    assert source.count('class="signal-list"') == 1
    assert "mobile-signals" not in source
    assert "signal-table" not in source
    assert "public/data/a-rolling-signals.json" in source
    assert "a-rolling-signals-demo.json" not in source
    assert "fetch('/api/public/v1/rolling-signals'" in source
    assert "window.setInterval(refresh, 60000)" in source
    assert "if (inFlight || document.hidden) return" in source
    assert "实时检查失败，继续保留当前有效快照" in source
    assert "演示隔离模式" in source
    assert "最后有效快照" in source
    assert "不生成仓位、价格或交易指令" in source
    assert "周期加权倾向" in source
    assert 'AStockSubnav active="rolling"' in source
