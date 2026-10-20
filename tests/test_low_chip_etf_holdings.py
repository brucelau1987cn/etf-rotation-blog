"""Contract tests for attach_low_chip_etf_holdings.py.

The data source changed on 2026-10-20: mx-data (东财妙想) was replaced by the
unauthenticated Eastmoney F10 route `PC_HSF10/ShareholderResearch/PageAjax`
(field `jjcg` = 基金持股). The contract under test is unchanged: schema,
sorting, off-exchange filtering, fail-soft behaviour and the atomic write.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import em_data_client  # noqa: E402

SCRIPT = SCRIPTS / "attach_low_chip_etf_holdings.py"
SPEC = importlib.util.spec_from_file_location("attach_low_chip_etf_holdings", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
sys.modules["attach_low_chip_etf_holdings"] = mod
SPEC.loader.exec_module(mod)


def _em_rows(rows_data: list[tuple[str, str, float, float, float]]) -> list[dict]:
    """rows_data: (fund_code, name, shares, hold_value_yuan, nav_ratio)."""
    return [
        {
            "fund_code": code,
            "fund_derivecode": code,
            "fund_name": name,
            "report_date": "2026-06-30",
            "shares": shares,
            "hold_value": value,
            "total_share_ratio": 0.05,
            "free_float_ratio": 0.07,
            "nav_ratio": nav,
            "org_type": "01",
        }
        for code, name, shares, value, nav in rows_data
    ]


def _fake_client(rows: list[dict] | None = None, error: Exception | None = None):
    fake = mock.MagicMock()
    if error is not None:
        fake.fund_holdings = mock.MagicMock(side_effect=error)
    else:
        fake.fund_holdings = mock.MagicMock(return_value=rows or [])
    # main() serialises usage() into its JSON summary, so it must be a real dict.
    fake.usage = mock.MagicMock(
        return_value={"requests": 1, "retries": 0, "failures": 0,
                      "source": "eastmoney-direct"}
    )
    return fake


# ============ parse_em_etf_holdings ============

def test_parse_sorts_by_value_desc():
    out = mod.parse_em_etf_holdings(_em_rows([
        ("1.SH", "A", 1.0, 5e8, 1.0),
        ("2.SZ", "B", 2.0, 10e8, 1.0),
        ("3.SH", "C", 3.0, 7e8, 1.0),
    ]))
    assert [h["code"] for h in out] == ["2.SZ", "3.SH", "1.SH"]
    assert [h["rank"] for h in out] == [1, 2, 3]


def test_parse_skips_off_exchange_funds():
    out = mod.parse_em_etf_holdings(_em_rows([
        ("1.SH", "ETF1", 1.0, 5e8, 1.0),
        ("2.OF", "主动基金", 2.0, 10e8, 1.0),
        ("3.SZ", "ETF3", 3.0, 7e8, 1.0),
    ]))
    assert [h["code"] for h in out] == ["3.SZ", "1.SH"]


def test_parse_returns_empty_when_no_rows():
    assert mod.parse_em_etf_holdings([]) == []


def test_parse_maps_nav_ratio_to_weight_pct():
    out = mod.parse_em_etf_holdings(_em_rows([("510300.SH", "沪深300ETF", 1.0, 5e8, 0.13)]))
    assert out[0]["weight_pct"] == 0.13


def test_parse_converts_holding_value_to_yi():
    out = mod.parse_em_etf_holdings(_em_rows([("510300.SH", "沪深300ETF", 1.0, 5.5e8, 1.0)]))
    assert out[0]["holding_value"] == pytest.approx(5.5, rel=1e-6)


# ============ top_category ============

def test_top_category_returns_etf_when_holdings_present():
    assert mod.top_category([{"code": "1.SH"}]) == "ETF"


def test_top_category_returns_none_when_empty():
    assert mod.top_category([]) is None


# ============ attach_for_one ============

def test_attach_for_one_returns_top_n():
    rows = _em_rows([
        (f"{i}.SH", f"ETF{i}", float(i), (10.0 - i * 0.1) * 1e8, 1.0)
        for i in range(1, 11)
    ])
    mod._get_client = lambda: _fake_client(rows)
    holdings, cat, raw_n, source_ok = mod.attach_for_one("000157.SZ")
    assert len(holdings) == mod.TOP_N_HOLDINGS
    assert raw_n == mod.TOP_N_HOLDINGS
    assert cat == "ETF"
    assert source_ok is True


def test_attach_for_one_failsoft_on_source_error():
    """A transport failure must be reported, not silently turned into "no ETF"."""
    mod._get_client = lambda: _fake_client(
        error=em_data_client.EastmoneyError("gave up after 4 attempts (HTTP 403)")
    )
    holdings, cat, raw_n, source_ok = mod.attach_for_one("000958.SZ")
    assert holdings == []
    assert cat is None
    assert raw_n == 0
    assert source_ok is False, "必须区分「数据源挂了」与「该股无 ETF 持仓」"


def test_attach_for_one_empty_but_source_ok():
    """Genuinely no ETF holding: source_ok stays True."""
    mod._get_client = lambda: _fake_client([])
    holdings, cat, raw_n, source_ok = mod.attach_for_one("000958.SZ")
    assert holdings == []
    assert source_ok is True


# ============ main() ============

def test_main_writes_atomic_json(tmp_path):
    payload = {
        "schema_version": "a-low-profit-v3",
        "data_as_of": "2026-09-02",
        "intersection": ["000157.SZ", "000958.SZ"],
        "enrichments": {
            "000157.SZ": {"name": "中联重科", "industry": "工程机械"},
            "000958.SZ": {"name": "电投产融", "industry": "电力"},
        },
    }
    test_data = tmp_path / "data.json"
    test_data.write_text(json.dumps(payload), encoding="utf-8")

    rows = _em_rows([("510300.SH", "沪深300ETF", 1.0, 5.5e8, 0.13)])
    mod._get_client = lambda: _fake_client(rows)

    import sys as _sys
    _sys.argv = ["attach_low_chip_etf_holdings.py", "--input", str(test_data)]
    mod.main()

    result = json.loads(test_data.read_text(encoding="utf-8"))
    e157 = result["enrichments"]["000157.SZ"]
    assert "etf_holdings" in e157
    e958 = result["enrichments"]["000958.SZ"]
    assert "etf_holdings" in e958


def test_dry_run_does_not_write(tmp_path):
    payload = {
        "schema_version": "a-low-profit-v3",
        "data_as_of": "2026-09-02",
        "intersection": ["000157.SZ"],
        "enrichments": {"000157.SZ": {"name": "x"}},
    }
    test_data = tmp_path / "data.json"
    test_data.write_text(json.dumps(payload), encoding="utf-8")

    rows = _em_rows([("510300.SH", "X", 1.0, 5.0e8, 1.0)])
    mod._get_client = lambda: _fake_client(rows)

    import sys as _sys
    _sys.argv = ["x", "--input", str(test_data), "--dry-run"]
    mod.main()

    result = json.loads(test_data.read_text(encoding="utf-8"))
    assert "etf_holdings" not in result["enrichments"]["000157.SZ"]


def test_main_handles_empty_intersection(tmp_path):
    payload = {"schema_version": "a-low-profit-v3", "data_as_of": "2026-09-02", "intersection": [], "enrichments": {}}
    test_data = tmp_path / "data.json"
    test_data.write_text(json.dumps(payload), encoding="utf-8")
    import sys as _sys
    _sys.argv = ["x", "--input", str(test_data)]
    assert mod.main() == 0


def test_main_fatal_on_missing_input(tmp_path):
    import sys as _sys
    _sys.argv = ["x", "--input", str(tmp_path / "no.json")]
    assert mod.main() == 1
