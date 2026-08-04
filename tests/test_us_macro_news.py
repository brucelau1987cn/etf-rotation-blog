from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE_COMPONENT = ROOT / "src" / "components" / "UsMacroFundGrid.astro"
PUBLISHER = ROOT / "scripts" / "update_us_etf_garden.py"


def test_macro_cards_render_update_label_and_no_news_block():
    source = PAGE_COMPONENT.read_text(encoding="utf-8")
    assert "更新日" in source
    for marker in ("最新解读", "item.news", "news.title", "news.url", "同花顺问财"):
        assert marker not in source


def test_us_close_publisher_probes_macro_page_and_json():
    source = PUBLISHER.read_text(encoding="utf-8")
    assert '"https://etf.peekabo.cc/us-macro/"' in source
    assert '"https://etf.peekabo.cc/data/us-macro-dashboard.json"' in source
    assert '"public/data/us-macro-dashboard.json"' in source


def test_live_macro_snapshot_has_no_news_contract():
    payload = json.loads((ROOT / "public/data/us-macro-dashboard.json").read_text(encoding="utf-8"))
    assert all("news" not in item for item in payload["fundamentals"])
