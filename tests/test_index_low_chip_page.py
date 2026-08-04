from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def test_index_low_chip_subnav_order_and_route():
    subnav = (ROOT / "src/components/RollingSubnav.astro").read_text(encoding="utf-8")
    assert "/rolling/low-chip/" in subnav
    assert "/rolling/index-low-chip/" in subnav
    assert "低筹码指数" in subnav
    assert "低筹码ETF" not in subnav
    assert "/rolling/etf-low-chip/" not in subnav
    assert subnav.index("/rolling/low-chip/") < subnav.index("/rolling/index-low-chip/")


def test_index_low_chip_page_structure():
    page = (ROOT / "src/pages/rolling/index-low-chip.astro").read_text(encoding="utf-8")
    data = json.loads((ROOT / "public/data/index-low-chip.json").read_text(encoding="utf-8"))

    assert data["metric"] == "收盘获利比例"
    assert data["threshold"] in {"低于2%", "低于2.0%"}
    assert data["counts"]["indices"] > 0
    assert len(data["indices"]) == data["counts"]["indices"]
    for row in data["indices"]:
        assert row["code"]
        assert row["name"]
        assert row["profit"] < 2.0

    for marker in [
        "chip-filter-meta",
        "chip-row-top",
        "收盘获利比例低于2%",
        "低筹码指数",
        "is:global",
        'id="chip-pager"',
        'id="chip-search-input"',
        "PAGE_SIZE = 8",
    ]:
        assert marker in page


def test_old_etf_low_chip_removed():
    assert not (ROOT / "src/pages/rolling/etf-low-chip.astro").exists()
    assert not (ROOT / "public/data/etf-low-chip-stocks.json").exists()
    assert not (ROOT / "scripts/refresh_etf_low_chip.py").exists()
    redirects = (ROOT / "public/_redirects").read_text(encoding="utf-8")
    assert "/rolling/etf-low-chip" in redirects
    assert "/rolling/index-low-chip/" in redirects
