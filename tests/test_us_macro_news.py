from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_us_macro.py"
PAGE_COMPONENT = ROOT / "src" / "components" / "UsMacroFundGrid.astro"
PUBLISHER = ROOT / "scripts" / "update_us_etf_garden.py"

spec = importlib.util.spec_from_file_location("generate_us_macro", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_parse_iwencai_news_keeps_title_time_and_original_url():
    payload = {
        "status_code": 0,
        "data": [
            {
                "title": "美国核心PCE同比升至3.3%",
                "summary": "通胀仍高于目标",
                "url": "https://news.example/pce",
                "publish_time": 1785513600,
            },
            {"title": "无链接内容", "summary": "忽略", "url": "", "publish_time": 1785510000},
        ],
    }
    items = module.parse_iwencai_news(payload, "core_pce", limit=2)
    assert len(items) == 1
    assert items[0]["title"] == "美国核心PCE同比升至3.3%"
    assert items[0]["summary"] == "通胀仍高于目标"
    assert items[0]["url"] == "https://news.example/pce"
    assert items[0]["published_at"] == "2026-07-31T12:00:00-04:00"
    assert items[0]["source"] == "同花顺问财"


def test_attach_macro_news_targets_core_pce_and_real_retail(monkeypatch):
    calls = []

    def fake_search(query: str, size: int = 5):
        calls.append(query)
        return [{"title": query, "summary": "摘要", "url": f"https://news.example/{len(calls)}", "published_at": "2026-07-31T10:00:00-04:00", "source": "同花顺问财"}]

    monkeypatch.setattr(module, "iwencai_news", fake_search)
    fundamentals = [
        {"key": "core_pce", "title": "核心PCE"},
        {"key": "real_retail", "title": "实际零售销售"},
        {"key": "core_cpi", "title": "核心CPI"},
    ]
    failures = {}
    module.attach_macro_news(fundamentals, failures)
    assert len(calls) == 2
    assert fundamentals[0]["news"][0]["source"] == "同花顺问财"
    assert fundamentals[1]["news"][0]["url"].startswith("https://news.example/")
    assert "news" not in fundamentals[2]
    assert failures == {}


def test_attach_macro_news_keeps_previous_links_when_search_fails(monkeypatch):
    def fail_search(query: str, size: int = 5):
        raise RuntimeError("temporary outage")

    monkeypatch.setattr(module, "iwencai_news", fail_search)
    fundamentals = [{"key": "core_pce", "title": "核心PCE"}]
    previous = {"fundamentals": [{"key": "core_pce", "news": [{"title": "旧链接", "url": "https://news.example/old", "source": "同花顺问财"}]}]}
    failures = {}
    module.attach_macro_news(fundamentals, failures, previous)
    assert fundamentals[0]["news"][0]["title"] == "旧链接"
    assert "news_core_pce" in failures


def test_macro_cards_render_news_links_and_update_label():
    source = PAGE_COMPONENT.read_text(encoding="utf-8")
    for marker in ("最新解读", "item.news", "news.title", "news.url", "同花顺问财"):
        assert marker in source


def test_us_close_publisher_probes_macro_page_and_json():
    source = PUBLISHER.read_text(encoding="utf-8")
    assert '"https://etf.peekabo.cc/us-macro/"' in source
    assert '"https://etf.peekabo.cc/data/us-macro-dashboard.json"' in source
    assert '"public/data/us-macro-dashboard.json"' in source


def test_live_macro_snapshot_has_news_contract_after_generation():
    payload = json.loads((ROOT / "public/data/us-macro-dashboard.json").read_text(encoding="utf-8"))
    by_key = {item["key"]: item for item in payload["fundamentals"]}
    for key in ("core_pce", "real_retail"):
        assert isinstance(by_key[key].get("news"), list)
        for news in by_key[key]["news"]:
            assert news["title"] and news["url"] and news["source"] == "同花顺问财"
