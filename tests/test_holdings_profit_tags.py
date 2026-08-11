"""Tests for holdings page ETF profit-ratio tags and 5-day collapse."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOLDINGS_PAGE = ROOT / "src" / "pages" / "futures-compass" / "holdings.astro"
APP_JS = ROOT / "public" / "js" / "jin10-holdings-app.js"
INVENTORY_SCRIPT = ROOT / "scripts" / "update_precious_inventory.py"
INVENTORY_JSON = ROOT / "public" / "data" / "precious-inventory.json"


def test_holdings_page_has_profit_tag_containers():
    page = HOLDINGS_PAGE.read_text(encoding="utf-8")
    assert 'id="gold-profit-tags"' in page
    assert 'id="silver-profit-tags"' in page
    assert 'profit-ratio-tags' in page
    assert 'profit-ratio-tag' in page


def test_app_js_renders_profit_tags_and_collapse():
    js = APP_JS.read_text(encoding="utf-8")
    assert "loadProfitTags" in js
    assert "INVENTORY_API" in js
    assert "etf_profit" in js
    assert "renderProfitTags" in js
    assert "COLLAPSE_AFTER" in js
    assert "holdings-toggle" in js
    assert "展开全部" in js
    assert "收起明细" in js


def test_update_script_fetches_etf_profit():
    text = INVENTORY_SCRIPT.read_text(encoding="utf-8")
    assert "fetch_etf_profit_ratios" in text
    assert "518880" in text  # 黄金ETF华安
    assert "161226" in text  # 国投白银LOF
    assert "etf_profit" in text


def test_inventory_json_has_etf_profit_assets():
    data = json.loads(INVENTORY_JSON.read_text(encoding="utf-8"))
    ep = (data.get("data") or {}).get("etf_profit") or {}
    assets = ep.get("assets") or {}
    assert "gold" in assets and "silver" in assets
    gold = assets["gold"]
    silver = assets["silver"]
    # values may be None but the structure must exist; current run expected OK
    assert gold.get("ok") is True
    assert silver.get("ok") is True
    assert "day" in gold and "week" in gold and "month" in gold
    assert "day" in silver and "week" in silver and "month" in silver
