import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODE_NAV = ROOT / "src" / "components" / "LowChipModeNav.astro"
ETF_PAGE = ROOT / "src" / "pages" / "rolling" / "low-chip" / "etf.astro"
ETF_DATA = ROOT / "public" / "data" / "etf-low-chip-pool.json"


def test_mode_nav_links_etf_pool_page():
    text = MODE_NAV.read_text(encoding="utf-8")
    assert "'etf'" in text
    assert "href=\"/rolling/low-chip/etf/\"" in text
    assert "active === 'etf'" in text


def test_etf_page_is_separate_from_stock_pool_data():
    text = ETF_PAGE.read_text(encoding="utf-8")
    assert "etf-low-chip-pool.json" in text
    assert "a-low-chip-stocks.json" not in text
    assert "研究观察" in text
    # research scope: must not reference compass weights/positions/actions
    assert "不改变罗盘权重、仓位或交易动作" in text


def test_etf_pool_snapshot_contract_when_present():
    if not ETF_DATA.exists():
        return  # snapshot is produced by the refresh script; contract checked after write
    data = json.loads(ETF_DATA.read_text(encoding="utf-8"))
    gate = data["gate"]
    assert gate["profit_threshold_pct"] == 1.5
    assert gate["scale_min_yi"] == 1.0
    assert gate["amount_avg_20d_min_wan"] == 1000.0
    assert data["counts"]["pool"] == len(data["rows"])
    for row in data["rows"]:
        assert row["periods_hit"], row["code"]
        assert row["scale_yi"] >= gate["scale_min_yi"]
        assert row["amount_avg_20d_wan"] >= gate["amount_avg_20d_min_wan"]
        for key in row["periods_hit"]:
            assert 0 <= row["profit"][key] < gate["profit_threshold_pct"]
    assert not set(data["liquidity_unknown"]) & {r["code"] for r in data["rows"]}
