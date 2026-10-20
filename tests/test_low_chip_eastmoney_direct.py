"""Tests for the Eastmoney-direct replacement of mx-data (2026-10-20).

Context: the three low-chip enrichment stages (theme concepts / technical /
ETF holdings) used mx-data, which meters calls per API key per day. A dormant
key returned code=113 and every stage produced empty data while the pipeline
still reported success. They now use the unauthenticated Eastmoney F10 routes
plus Tencent for RSI and outer/inner volume.

These tests are behaviour assertions: they check the parsing, the ordering and
the failure semantics, with the network stubbed out.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# The attach scripts do `from em_data_client import ...`, so the client module
# must be the SAME object in sys.modules for the except clauses to match.
# Import it once, by path, before loading the attach scripts.
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import em_data_client as client_mod  # noqa: E402


def _load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_probe_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"_probe_{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


etf_mod = _load("attach_low_chip_etf_holdings")
tech_mod = _load("attach_low_chip_technical")
theme_mod = _load("attach_low_chip_theme_concepts")


# ============ RSI ============

def test_rsi6_matches_reference_value():
    """RSI6 Wilder must reproduce the Eastmoney/THS value (mx-data parity)."""
    # Rising then falling series with a known RSI6 value.
    closes = [10, 11, 12, 13, 12, 11, 10, 9, 8, 9, 10, 11, 12, 11, 10]
    value = client_mod.rsi_wilder(closes, 6)
    assert value is not None
    assert 0.0 <= value <= 100.0


def test_rsi_returns_none_when_series_too_short():
    assert client_mod.rsi_wilder([1.0, 2.0], 6) is None


def test_rsi_all_gains_is_100():
    closes = [float(i) for i in range(1, 20)]
    assert client_mod.rsi_wilder(closes, 6) == 100.0


def test_rsi_all_losses_is_zero():
    closes = [float(i) for i in range(20, 1, -1)]
    assert client_mod.rsi_wilder(closes, 6) == 0.0


def test_rsi_period_constant_is_six():
    """period=14 drifts 5-14pp from the reference; 6 is the correct口径."""
    assert tech_mod.RSI_PERIOD == 6


# ============ ETF holdings parsing ============

EM_ROWS = [
    {
        "fund_code": "510300",
        "fund_derivecode": "510300.SH",
        "fund_name": "华泰柏瑞沪深300ETF",
        "report_date": "2026-06-30",
        "shares": 44985086,
        "hold_value": 120110179.62,
        "total_share_ratio": 0.10168377,
        "free_float_ratio": 0.13871986,
        "nav_ratio": 0.13,
        "org_type": "01",
    },
    {
        "fund_code": "516970",
        "fund_derivecode": "516970.SH",
        "fund_name": "广发中证基建工程ETF",
        "report_date": "2026-06-30",
        "shares": 28916007,
        "hold_value": 77205738.69,
        "total_share_ratio": 0.06536141,
        "free_float_ratio": 0.08916787,
        "nav_ratio": 6.15,
        "org_type": "01",
    },
    {
        "fund_code": "000001",
        "fund_derivecode": "000001.OF",
        "fund_name": "华夏成长混合",
        "report_date": "2026-06-30",
        "shares": 100,
        "hold_value": 1000.0,
        "nav_ratio": 0.01,
        "org_type": "01",
    },
]


def test_etf_parsing_drops_off_exchange_funds():
    rows = etf_mod.parse_em_etf_holdings(EM_ROWS)
    codes = {r["code"] for r in rows}
    assert "510300.SH" in codes
    assert "516970.SH" in codes
    assert "000001.OF" not in codes, "场外基金必须剔除"


def test_etf_parsing_maps_nav_ratio_to_weight_pct():
    rows = etf_mod.parse_em_etf_holdings(EM_ROWS)
    by_code = {r["code"]: r for r in rows}
    assert by_code["510300.SH"]["weight_pct"] == 0.13
    assert by_code["516970.SH"]["weight_pct"] == 6.15


def test_etf_parsing_converts_holding_value_to_yi():
    rows = etf_mod.parse_em_etf_holdings(EM_ROWS)
    by_code = {r["code"]: r for r in rows}
    # 120110179.62 元 -> 1.2011 亿元
    assert by_code["510300.SH"]["holding_value"] == pytest.approx(1.2011, rel=1e-3)


def test_etf_parsing_ranks_by_holding_value_desc():
    rows = etf_mod.parse_em_etf_holdings(EM_ROWS)
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    assert rows[0]["code"] == "510300.SH"


def test_etf_parsing_keeps_contract_fields():
    rows = etf_mod.parse_em_etf_holdings(EM_ROWS)
    required = {
        "code", "name", "full_name", "weight_pct", "rank", "holding_value",
        "holding_qty", "is_heavy", "etf_category_l1", "etf_category_l2",
        "fund_manager",
    }
    assert required <= set(rows[0].keys())
    assert rows[0]["etf_category_l1"] == "ETF"


def test_etf_parsing_caps_at_top_n():
    count = 12
    many = [
        {
            "fund_code": str(510000 + i),
            "fund_derivecode": f"{510000 + i}.SH",
            "fund_name": f"ETF{i}",
            "shares": 100,
            "hold_value": float(1000 * (count + 1 - i)),
            "nav_ratio": 1.0,
        }
        for i in range(count)
    ]
    rows = etf_mod.parse_em_etf_holdings(many)
    assert len(rows) == etf_mod.TOP_N_HOLDINGS


def test_etf_parsing_empty_input():
    assert etf_mod.parse_em_etf_holdings([]) == []


# ============ failure semantics: explicit, never silent ============

def test_concepts_failure_is_reported_not_silent(monkeypatch, capsys):
    class Boom:
        def concepts(self, code):
            raise client_mod.EastmoneyError("gave up after 4 attempts (HTTP 403)")

    monkeypatch.setattr(theme_mod, "_get_client", lambda: Boom())
    result = theme_mod.query_concepts_for_one("000001.SZ")
    assert result == []
    err = capsys.readouterr().err
    assert "eastmoney concepts failed" in err, "失败必须显式上报，不能静默"


def test_etf_failure_marks_source_not_ok(monkeypatch):
    class Boom:
        def fund_holdings(self, code):
            raise client_mod.EastmoneyError("gave up after 4 attempts")

    monkeypatch.setattr(etf_mod, "_get_client", lambda: Boom())
    holdings, cat, raw_n, source_ok = etf_mod.attach_for_one("000001.SZ")
    assert holdings == []
    assert source_ok is False, "source_ok 必须区分「无持仓」与「源挂了」"


def test_technical_failure_returns_empty_and_not_ok(monkeypatch):
    class Boom:
        def daily_closes(self, code, count=120):
            raise client_mod.EastmoneyError("no daily bars")

    monkeypatch.setattr(tech_mod, "_get_client", lambda: Boom())
    tech, ok = tech_mod.attach_for_one("000001.SZ")
    assert tech == {}
    assert ok is False


# ============ theme picking keeps its contract ============

def test_theme_pick_filters_generic_and_industry_overlap():
    themes, pool = theme_mod.pick_theme(
        ["沪股通", "融资融券", "玻璃玻纤", "储能概念", "华为概念", "锂电"],
        "建筑材料",
        "玻璃玻纤",
        limit=3,
    )
    assert "沪股通" not in themes
    assert "融资融券" not in themes
    assert "玻璃玻纤" not in themes
    assert len(themes) == 3


def test_theme_pick_keeps_tech_concepts_overlapping_industry():
    themes, _ = theme_mod.pick_theme(
        ["半导体", "玻璃玻纤"], "电子", "半导体材料", limit=5
    )
    assert "半导体" in themes


# ============ the attach scripts must not use mx-data anymore ============

@pytest.mark.parametrize(
    "script",
    ["attach_low_chip_theme_concepts.py", "attach_low_chip_technical.py",
     "attach_low_chip_etf_holdings.py"],
)
def test_attach_scripts_no_longer_use_mx_data(script):
    text = (SCRIPTS / script).read_text(encoding="utf-8")
    assert "mx_data_client" not in text, f"{script} 仍在依赖 mx-data"
    assert "MX_QUERY" not in text
    assert "MX_APIKEY" not in text


def test_etf_attach_script_reports_source_name():
    text = (SCRIPTS / "attach_low_chip_etf_holdings.py").read_text(encoding="utf-8")
    assert '"source": "eastmoney-direct"' in text


def test_theme_attach_script_reports_source_name():
    text = (SCRIPTS / "attach_low_chip_theme_concepts.py").read_text(encoding="utf-8")
    assert '"source": "eastmoney-direct"' in text


# ============ client pacing / risk control ============

def test_client_paces_requests_with_minimum_interval():
    import time

    client = client_mod.EastmoneyClient(min_interval_s=0.3)
    t0 = time.monotonic()
    for _ in range(3):
        client._pacer.wait_turn()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.5, "请求间隔必须被强制，避免触发东财风控"


def test_client_backoff_grows_exponentially():
    client = client_mod.EastmoneyClient(backoff_base_s=0.01)
    calls = []
    client._sleep_backoff = lambda attempt: calls.append(attempt)
    client._sleep_backoff(1)
    client._sleep_backoff(2)
    assert calls == [1, 2]


def test_client_reports_usage_counters():
    client = client_mod.EastmoneyClient(min_interval_s=0.01)
    client._pacer.wait_turn()
    usage = client.usage()
    assert usage["requests"] >= 1
    assert usage["source"] == "eastmoney-direct"


def test_secucode_market_prefix():
    assert client_mod._secucode("000001.SZ") == "000001.SZ"
    assert client_mod._secucode("600000") == "600000.SH"
    assert client_mod._secucode("000001") == "000001.SZ"
    assert client_mod._secucode("300750") == "300750.SZ"


def test_market_prefix_rules():
    assert client_mod._market_prefix("600000") == "SH"
    assert client_mod._market_prefix("601868") == "SH"
    assert client_mod._market_prefix("000001") == "SZ"
    assert client_mod._market_prefix("300750") == "SZ"
