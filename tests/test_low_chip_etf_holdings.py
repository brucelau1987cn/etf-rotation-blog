"""Contract tests for attach_low_chip_etf_holdings.py (mx-data 替代 iWenCai 后的版本).

Mock mx_data.MXData.query + parse_result, verify schema, sorting, fail-soft,
per-record atomic write contract.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/attach_low_chip_etf_holdings.py"
SPEC = importlib.util.spec_from_file_location("attach_low_chip_etf_holdings", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
sys.modules["attach_low_chip_etf_holdings"] = mod
SPEC.loader.exec_module(mod)


def _fake_mx_tables(stock_code: str, rows_data: list[tuple[str, str, float, float]]) -> list[dict]:
    """rows_data: list of (fund_code, fund_name, shares_wan, value_yi) tuples."""
    fund_codes = [r[0] for r in rows_data]
    sheet_name = f"恒瑞医药({stock_code})基金持股统计(2026-06-30)"
    return [{
        "sheet_name": sheet_name,
        "rows": [
            {"date": "基金简称", **dict(zip(fund_codes, [r[1] for r in rows_data]))},
            {"date": "持流通股数量(股)", **dict(zip(fund_codes, [f"{r[2]}万" for r in rows_data]))},
            {"date": "持流通股市值(元)", **dict(zip(fund_codes, [f"{r[3]}亿" for r in rows_data]))},
        ],
    }]


def _mock_mx_client(tables_per_code: dict[str, list[dict]] | None = None,
                    err: str | None = None):
    """Build a fake MXData mock."""
    mock_mx = mock.MagicMock()
    tables_per_code = tables_per_code or {}

    def fake_parse_result(r):
        if err is not None:
            return [], [], 0, err
        first_key = next(iter(tables_per_code), "")
        return tables_per_code.get(first_key, []), [], 0, None

    mock_mx.parse_result = fake_parse_result
    mock_mx.query = mock.MagicMock(return_value={"status": 0})
    return mock_mx


def _install_fake_mx(tables_per_code: dict[str, list[dict]] | None = None,
                     err: str | None = None):
    """Patch mod._get_mx to return a fake client."""
    fake = _mock_mx_client(tables_per_code, err)
    return mock.patch.object(mod, "_get_mx", return_value=fake)


# ============ parse_mx_etf_holdings ============

def test_parse_mx_etf_holdings_sorts_by_value_desc():
    tables = _fake_mx_tables("000157.SZ", [
        ("1.SH", "A", 1.0, 5.0),
        ("2.SZ", "B", 2.0, 10.0),
        ("3.SH", "C", 3.0, 7.0),
    ])
    out = mod.parse_mx_etf_holdings(tables, "000157.SZ")
    assert [h["code"] for h in out] == ["2.SZ", "3.SH", "1.SH"]
    assert [h["rank"] for h in out] == [1, 2, 3]


def test_parse_mx_etf_holdings_skips_of_funds():
    tables = _fake_mx_tables("000157.SZ", [
        ("1.SH", "ETF1", 1.0, 5.0),
        ("2.OF", "主动基金", 2.0, 10.0),  # 场外, skip
        ("3.SZ", "ETF3", 3.0, 7.0),
    ])
    out = mod.parse_mx_etf_holdings(tables, "000157.SZ")
    assert [h["code"] for h in out] == ["3.SZ", "1.SH"]


def test_parse_mx_etf_holdings_returns_empty_when_no_matching_sheet():
    tables = _fake_mx_tables("999999.SH", [])  # different stock
    out = mod.parse_mx_etf_holdings(tables, "000157.SZ")
    assert out == []


# ============ top_category ============

def test_top_category_returns_etf_when_holdings_present():
    assert mod.top_category([{"code": "1.SH"}]) == "ETF"


def test_top_category_returns_none_when_empty():
    assert mod.top_category([]) is None


# ============ attach_for_one ============

def test_attach_for_one_returns_top_n():
    tables = _fake_mx_tables("000157.SZ", [
        (f"{i}.SH", f"ETF{i}", float(i), 10.0 - i * 0.1) for i in range(1, 11)
    ])
    mod._get_mx = lambda: _mock_mx_client({"000157": tables})  # key is bare code
    holdings, cat, raw_n = mod.attach_for_one("000157.SZ")
    assert len(holdings) == mod.TOP_N_HOLDINGS  # top N
    assert raw_n == 5  # attach_for_one reports after top-N truncation
    assert cat == "ETF"


def test_attach_for_one_failsoft_on_parse_error():
    mod._get_mx = lambda: _mock_mx_client(err="解析失败")
    holdings, cat, raw_n = mod.attach_for_one("000958.SZ")
    assert holdings == []
    assert cat is None
    assert raw_n == 0


def test_attach_for_one_retries_on_rate_limit():
    """code=112 限流应触发退避重试."""
    fake = mock.MagicMock()
    fake.parse_result = mock.MagicMock(side_effect=[
        ([], [], 0, "状态码 112 - 请求频率过高"),
        (_fake_mx_tables("000157.SZ", [("1.SH", "X", 1.0, 5.0)]), [], 1, None),
    ])
    mod._get_mx = lambda: fake
    with mock.patch("time.sleep"):  # 跳过实际等待
        holdings, cat, raw_n = mod.attach_for_one("000157.SZ")
    assert len(holdings) == 1
    assert fake.parse_result.call_count == 2


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

    tables_157 = _fake_mx_tables("000157.SZ", [("510300.SH", "沪深300ETF", 100.0, 5.5)])
    tables_958: list = []

    fake = mock.MagicMock()
    def fake_parse_result(_r):
        import re
        return tables_157, [], 0, None  # 第一个有数据，后续覆盖不到——简化逻辑：永远返回 157 表
    fake.parse_result = lambda r: (tables_157, [], 0, None)
    mod._get_mx = lambda: fake

    import sys as _sys
    _sys.argv = ["attach_low_chip_etf_holdings.py", "--input", str(test_data)]
    with mock.patch("time.sleep"):
        mod.main()

    result = json.loads(test_data.read_text(encoding="utf-8"))
    e157 = result["enrichments"]["000157.SZ"]
    assert "etf_holdings" in e157
    e958 = result["enrichments"]["000958.SZ"]
    # 第二次会重复拿同一份 mock 数据，写入也有 holdings
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
    tables = _fake_mx_tables("000157.SZ", [("510300.SH", "X", 1.0, 5.0)])

    fake = mock.MagicMock()
    fake.parse_result = lambda r: (tables, [], 0, None)
    mod._get_mx = lambda: fake

    import sys as _sys
    _sys.argv = ["x", "--input", str(test_data), "--dry-run"]
    with mock.patch("time.sleep"):
        mod.main()

    result = json.loads(test_data.read_text(encoding="utf-8"))
    # dry-run: etf_holdings 不应被写入
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
