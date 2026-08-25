from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/weekly-stock-recommendations.json"
A_PAGE = ROOT / "src/pages/a-compass/weekly.astro"
US_PAGE = ROOT / "src/pages/us-compass/weekly.astro"
A_NAV = ROOT / "src/components/AStockSubnav.astro"
US_NAV = ROOT / "src/components/UsSubnav.astro"
SCRIPT = ROOT / "scripts/update_weekly_stock_recommendations.py"


def test_weekly_stock_recommendation_contract_and_initial_lists():
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "weekly-stock-recommendations-v1"
    assert payload["week_of"] == "2026-08-24"
    assert payload["tracking_policy"]["mode"] == "continuous"
    assert payload["tracking_policy"]["start_date"] == "2026-08-24"
    a = payload["markets"]["A"]["items"]
    us = payload["markets"]["US"]["items"]
    assert len(a) == 16
    assert len(us) == 12
    assert [x["symbol"] for x in a] == [
        "600562", "002414", "600363", "300397", "002829", "300747", "600184", "002465",
        "300629", "000547", "601869", "600487", "600522", "601728", "603606", "601899",
    ]
    assert [x["symbol"] for x in us] == [
        "AVAV", "KTOS", "RCAT", "PLTR", "ONDS", "PRZO", "UAVS", "ZENA", "VWAV", "LMT", "GLW", "CIEN",
    ]
    for market, items in (("A", a), ("US", us)):
        for item in items:
            assert item["name"]
            assert item["direction"]
            assert item["added_on"] == "2026-08-24"
            assert item["status"] == "tracking"
            assert isinstance(item["daily"], list)
            assert item["daily"], f"{market} {item['symbol']} must have baseline data"
            assert item["baseline_date"] == "2026-08-24"
            assert item["daily"][0]["date"] == "2026-08-24"
            assert item["daily"][0]["return_since_added_pct"] == 0.0
            assert {"date", "close", "change_pct", "return_since_added_pct"} <= set(item["daily"][-1])


def test_weekly_pages_and_navigation_are_present():
    a_page = A_PAGE.read_text(encoding="utf-8")
    us_page = US_PAGE.read_text(encoding="utf-8")
    for page, market in ((a_page, "A股"), (us_page, "美股")):
        assert "每周推荐" in page
        assert "持续追踪" in page
        assert "加入以来" in page
        assert "weekly-stock-recommendations.json" in page
        assert market in page
    assert "每周推荐" in A_NAV.read_text(encoding="utf-8")
    assert "每周推荐" in US_NAV.read_text(encoding="utf-8")
    assert "/a-compass/weekly/" in A_NAV.read_text(encoding="utf-8")
    assert "/us-compass/weekly/" in US_NAV.read_text(encoding="utf-8")


def test_update_script_exists_and_has_both_market_sources():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "qt.gtimg.cn" in text
    assert "query1.finance.yahoo.com" in text
    assert "atomic_write_json" in text
    assert "return_since_added_pct" in text
