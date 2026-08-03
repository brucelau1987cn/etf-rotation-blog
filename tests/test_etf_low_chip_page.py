from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_etf_low_chip_subnav_order_and_route():
    subnav = (ROOT / "src/components/RollingSubnav.astro").read_text(encoding="utf-8")
    assert "/rolling/low-chip/" in subnav
    assert "/rolling/etf-low-chip/" in subnav
    assert subnav.index("/rolling/low-chip/") < subnav.index("/rolling/etf-low-chip/")


def test_etf_low_chip_page_structure():
    page = (ROOT / "src/pages/rolling/etf-low-chip.astro").read_text(encoding="utf-8")
    data = __import__("json").loads((ROOT / "public/data/etf-low-chip-stocks.json").read_text(encoding="utf-8"))

    assert data["metric"] == "跟踪指数收盘获利比例（ETF 无独立筹码分布，以跟踪指数代理）"
    assert data["threshold"] == "低于2%" or data["threshold"] == "低于2.0%"
    assert data["counts"]["low_profit_indices"] > 0
    assert data["counts"]["matched_etfs"] > 0
    assert len(data["etfs"]) == data["counts"]["matched_etfs"]
    for row in data["etfs"]:
        assert row["code"].endswith((".SH", ".SZ"))
        assert row["track_index"]
        assert row["index_profit"] < 2.0
        assert row["price"] is not None
        assert row["change_percent"] is not None

    for marker in [
        "chip-filter-meta",
        "chip-row-top",
        "chip-shr-head",
        "chip-shr-body",
        "基金规模",
        "指数获利",
        "T+0",
        "跟踪指数收盘获利比例低于2%",
        "代理口径",
        "is:global",
        "data-scale",
    ]:
        assert marker in page


def test_etf_low_chip_pager_and_search_wireup():
    page = (ROOT / "src/pages/rolling/etf-low-chip.astro").read_text(encoding="utf-8")
    assert 'id="chip-pager"' in page
    assert 'id="chip-page-nums"' in page
    assert 'id="chip-search-input"' in page
    assert "chip-page-num" in page  # JS-created pager buttons
    assert "PAGE_SIZE = 8" in page
    assert "scale-10" in page
    assert "t0" in page
