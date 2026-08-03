from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_futures_compass_briefing.py"

spec = importlib.util.spec_from_file_location("generate_futures_compass_briefing", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_next_cffex_delivery_rolls_after_current_month_expiry():
    result = module.next_cffex_delivery(date(2026, 8, 22))
    assert result["month"] == "2026-09"
    assert result["date"] == "2026-09-18"
    assert result["weekday"] == "周五"


def test_next_cffex_delivery_keeps_current_month_before_expiry():
    result = module.next_cffex_delivery(date(2026, 8, 2))
    assert result["month"] == "2026-08"
    assert result["date"] == "2026-08-21"


def test_build_policy_items_filters_jin10_news_by_futures_assets():
    news = [
        {"time": "2026-08-02 09:00", "title": "工信部发布多晶硅行业绿色发展政策", "url": "https://example/policy"},
        {"time": "2026-08-02 08:00", "title": "游戏行业新品发布", "url": "https://example/game"},
    ]
    items = module.build_policy_items(news)
    assert len(items) == 1
    assert items[0]["scope"] == "多晶硅"
    assert items[0]["source"] == "金十数据"


def test_build_fed_watch_prefers_recent_high_importance_calendar_items():
    rows = [
        {"time": "2026-08-07 20:30", "star": 5, "title": "美国7月非农就业人口", "previous": "5.7", "consensus": "9", "actual": None, "impact": None},
        {"time": "2026-07-30 20:30", "star": 3, "title": "美国6月核心PCE物价指数月率", "previous": "0.3", "consensus": "0.2", "actual": "0.1", "impact": "利多"},
        {"time": "2026-07-31 20:30", "star": 4, "title": "加拿大5月GDP月率", "actual": "0.3", "impact": "利空"},
        {"time": "2026-07-28 09:30", "star": 2, "title": "中国工业企业利润", "actual": "15.1"},
    ]
    result = module.build_fed_watch(rows, today=date(2026, 8, 2))
    assert len(result["latest"]) == 2
    assert result["latest"][0]["event"] == "美国7月非农就业人口"
    assert "预期 9" in result["latest"][0]["result"]
    assert result["source"] == "金十数据 API"


def test_build_briefing_combines_dynamic_delivery_and_jin10_data():
    payload = module.build_briefing(
        today=date(2026, 8, 22),
        calendar_rows=[{"time": "2026-08-26 20:30", "star": 4, "title": "美国7月核心PCE物价指数年率", "previous": "3.3", "consensus": "3.2", "actual": None}],
        news_rows=[{"time": "2026-08-22 10:00", "title": "新能源材料产业政策支持碳酸锂", "url": "https://example/lithium"}],
    )
    assert payload["index_delivery"]["date"] == "2026-09-18"
    assert payload["industry_policy"][0]["scope"] == "碳酸锂"
    assert payload["fed_watch"]["latest"][0]["event"] == "美国7月核心PCE物价指数年率"
