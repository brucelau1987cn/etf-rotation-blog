from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_low_chip_base.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_low_chip_threshold", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_week_month_quarter_queries_use_strict_one_point_five_percent_threshold():
    module = load_module()

    assert [label for label, _, _ in module.PERIODS] == ["week", "month", "quarter"]
    assert all("收盘获利小于1.5%" in query for _, _, query in module.PERIODS)
    assert all("不超过1.5%" not in query for _, _, query in module.PERIODS)


def test_published_threshold_is_one_point_five_percent(monkeypatch, tmp_path):
    module = load_module()
    setattr(module, "DATA", tmp_path / "low-chip.json")

    rows_by_prefix = {
        "周线收盘获利": [
            {"股票代码": "000001.SZ", "股票简称": "平安银行", "周线收盘获利[20260903]": 1.4999},
            {"股票代码": "000002.SZ", "股票简称": "万科A", "周线收盘获利[20260903]": 1.5},
        ],
        "月线收盘获利": [
            {"股票代码": "000001.SZ", "股票简称": "平安银行", "月线收盘获利[20260903]": 1.2},
            {"股票代码": "000002.SZ", "股票简称": "万科A", "月线收盘获利[20260903]": 1.3},
        ],
        "季线收盘获利": [
            {"股票代码": "000001.SZ", "股票简称": "平安银行", "季线收盘获利[20260903]": 1.1},
            {"股票代码": "000002.SZ", "股票简称": "万科A", "季线收盘获利[20260903]": 1.4},
        ],
    }

    def fake_paginate(query):
        for prefix, rows in rows_by_prefix.items():
            if prefix in query:
                return rows, len(rows)
        return [], 0

    monkeypatch.setattr(module, "paginate", fake_paginate)
    assert module.main() == 0

    import json

    payload = json.loads(getattr(module, "DATA").read_text(encoding="utf-8"))
    assert payload["threshold"] == 1.5
    assert payload["intersection"] == ["000001.SZ"]
    assert all(item["symbol"] != "000002.SZ" for item in payload["periods"]["week"])


# ============ pagination must never silently truncate ============

def test_paginate_walks_past_ten_pages_while_has_more_is_true(monkeypatch):
    """2026-10-22: a broad selloff pushed week/month past 1000 rows. The old
    `if p > 10: break` returned 1000 rows with no signal, and the published
    pool looked complete while being truncated."""
    module = load_module()
    pages = {n: {"datas": [{"股票代码": f"{n:03d}000.SZ"}], "has_more": n < 12} for n in range(1, 13)}
    seen = []

    def fake_fetch(query, page):
        seen.append(page)
        return pages[page]

    monkeypatch.setattr(module, "fetch", fake_fetch)
    rows, count = module.paginate("q")
    assert seen == list(range(1, 13))
    assert count == 12


def test_paginate_raises_instead_of_returning_a_truncated_pool(monkeypatch):
    """Hitting the runaway cap must fail closed — a truncated screen would
    silently publish a wrong pool."""
    module = load_module()
    monkeypatch.setattr(module, "MAX_PAGES", 3)
    monkeypatch.setattr(module, "fetch", lambda q, p: {"datas": [{}], "has_more": True})

    with pytest.raises(SystemExit) as excinfo:
        module.paginate("q")
    assert "truncated pool" in str(excinfo.value)


def test_max_pages_is_far_above_any_real_result_set():
    """The cap only bounds a runaway loop; a real selloff exceeds 1000 rows."""
    module = load_module()
    assert module.MAX_PAGES >= 100
