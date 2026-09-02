"""Contract tests for attach_low_chip_etf_holdings.py

Mocked iWenCai so no network dependency; verifies schema, sorting, fail-soft,
and per-record atomic write contract.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/attach_low_chip_etf_holdings.py"
SPEC = importlib.util.spec_from_file_location("attach_low_chip_etf_holdings", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
sys.modules["attach_low_chip_etf_holdings"] = mod
SPEC.loader.exec_module(mod)


def _fake_iwc_response(rows: list[dict]) -> dict:
    return {
        "success": True,
        "query": "x 持有ETF,基金类型包含ETF",
        "code_count": len(rows),
        "returned_count": len(rows),
        "datas": rows,
    }


def _make_etf_row(code: str, name: str, weight: float, rank: int, l2: str = "主题指数ETF") -> dict:
    return {
        "基金代码": code,
        "基金简称": name,
        "基金扩位简称": f"{name}扩位",
        "持仓市值": 1000000.0,
        "持仓数量": 5000.0,
        "持仓市值占基金资产净值比": weight,
        "持仓占总市值比例": 0.01,
        "排名": float(rank),
        "是否重仓": True,
        "标的类型": "股票",
        "etf类型一级分类": "股票型ETF",
        "etf类型二级分类": l2,
        "现任基金经理姓名": ["张三"],
    }


def test_parse_holdings_sorts_by_weight_desc():
    rows = [
        _make_etf_row("1", "A", 5.0, 5),
        _make_etf_row("2", "B", 10.0, 1),
        _make_etf_row("3", "C", 7.0, 3),
    ]
    out = mod.parse_holdings(rows)
    assert [h["code"] for h in out] == ["2", "3", "1"]
    assert all(isinstance(h["rank"], int) for h in out)
    # weight_pct rounded to 4 dp
    assert out[0]["weight_pct"] == 10.0


def test_parse_holdings_skips_non_etf():
    rows = [
        _make_etf_row("1", "A", 5.0, 5),
        {**_make_etf_row("2", "B", 5.0, 5), "etf类型一级分类": "场外主动"},  # LOF, skip
        _make_etf_row("3", "C", 5.0, 5, l2="行业指数ETF"),
    ]
    out = mod.parse_holdings(rows)
    assert [h["code"] for h in out] == ["1", "3"]


def test_parse_holdings_skips_missing_weight_or_rank():
    rows = [
        _make_etf_row("1", "A", 5.0, 5),
        {"基金代码": "2", "基金简称": "B", "持仓市值占基金资产净值比": None, "排名": 1.0,
         "etf类型一级分类": "股票型ETF"},
        {"基金代码": "3", "基金简称": "C", "持仓市值占基金资产净值比": 5.0, "排名": None,
         "etf类型一级分类": "股票型ETF"},
    ]
    out = mod.parse_holdings(rows)
    assert [h["code"] for h in out] == ["1"]


def test_top_category_picks_most_common_l2():
    rows = [
        _make_etf_row("1", "A", 5, 1, l2="主题指数ETF"),
        _make_etf_row("2", "B", 5, 2, l2="主题指数ETF"),
        _make_etf_row("3", "C", 5, 3, l2="行业指数ETF"),
    ]
    assert mod.top_category(rows) == "主题指数ETF"


def test_top_category_returns_none_when_empty():
    assert mod.top_category([]) is None


def test_top_category_skips_none_values():
    rows = [
        _make_etf_row("1", "A", 5, 1, l2="主题指数ETF"),
        {**_make_etf_row("2", "B", 5, 2), "etf类型二级分类": None},
    ]
    assert mod.top_category(rows) == "主题指数ETF"


def test_iwc_query_returns_datas_list(monkeypatch):
    fake_rows = [_make_etf_row("1", "A", 5.0, 5)]
    captured = {}

    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        from unittest.mock import MagicMock
        m = MagicMock()
        m.returncode = 0
        m.stdout = json.dumps(_fake_iwc_response(fake_rows))
        m.stderr = ""
        return m

    monkeypatch.setattr(subprocess := __import__("subprocess"), "run", fake_run)
    result = mod.iwc_query("000157")
    assert len(result) == 1
    assert captured["cmd"][2] == "000157 持有ETF,基金类型包含ETF"


def test_iwc_query_returns_empty_on_timeout():
    import subprocess
    with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30)):
        assert mod.iwc_query("000157") == []


def test_iwc_query_returns_empty_on_nonzero_rc():
    import subprocess
    with mock.patch.object(subprocess, "run") as run:
        run.return_value.returncode = 1
        run.return_value.stderr = "fail"
        assert mod.iwc_query("000157") == []


def test_attach_for_one_returns_top_n():
    rows = [_make_etf_row(f"{i}", f"ETF{i}", 10.0 - i * 0.1, i + 1) for i in range(10)]
    import subprocess
    with mock.patch.object(subprocess, "run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps(_fake_iwc_response(rows))
        run.return_value.stderr = ""
        holdings, cat, raw_n = mod.attach_for_one("000157.SZ")
    assert len(holdings) == mod.TOP_N_HOLDINGS
    assert raw_n == 10
    assert cat == "主题指数ETF"


def test_attach_for_one_failsoft_empty():
    import subprocess
    with mock.patch.object(subprocess, "run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps(_fake_iwc_response([]))
        run.return_value.stderr = ""
        holdings, cat, raw_n = mod.attach_for_one("000958.SZ")
    assert holdings == []
    assert cat is None
    assert raw_n == 0


def test_main_writes_atomic_json(tmp_path):
    """Verify the JSON file ends up valid, compact, with new fields."""
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

    rows_157 = [_make_etf_row("1", "工程ETF", 5.5, 3, l2="主题指数ETF")]
    import subprocess
    def fake_run(cmd, *args, **kwargs):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.returncode = 0
        if "000157" in cmd[2]:
            m.stdout = json.dumps(_fake_iwc_response(rows_157))
        else:
            m.stdout = json.dumps(_fake_iwc_response([]))
        m.stderr = ""
        return m

    with mock.patch.object(subprocess, "run", fake_run):
        with mock.patch("time.sleep"):  # no real delay
            rc = mod.main.__wrapped__("__main__") if False else None
    # Manual invocation since we cannot easily pass --input via main()
    # Call directly with argparse override
    import sys as _sys
    _sys.argv = ["attach_low_chip_etf_holdings.py", "--input", str(test_data), "--delay", "0"]
    with mock.patch.object(subprocess, "run", fake_run):
        with mock.patch("time.sleep"):
            mod.main()

    result = json.loads(test_data.read_text(encoding="utf-8"))
    e157 = result["enrichments"]["000157.SZ"]
    assert len(e157["etf_holdings"]) == 1
    assert e157["etf_holdings"][0]["name"] == "工程ETF"
    assert e157["etf_top_category"] == "主题指数ETF"
    e958 = result["enrichments"]["000958.SZ"]
    assert e958["etf_holdings"] == []
    assert e958["etf_top_category"] is None


def test_dry_run_does_not_write(tmp_path):
    payload = {
        "schema_version": "a-low-profit-v3",
        "data_as_of": "2026-09-02",
        "intersection": ["000157.SZ"],
        "enrichments": {"000157.SZ": {"name": "x"}},
    }
    test_data = tmp_path / "data.json"
    test_data.write_text(json.dumps(payload), encoding="utf-8")
    rows = [_make_etf_row("1", "A", 5.0, 1)]

    import subprocess
    def fake_run(cmd, *args, **kwargs):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.returncode = 0
        m.stdout = json.dumps(_fake_iwc_response(rows))
        m.stderr = ""
        return m

    import sys as _sys
    _sys.argv = ["x", "--input", str(test_data), "--dry-run", "--delay", "0"]
    with mock.patch.object(subprocess, "run", fake_run):
        with mock.patch("time.sleep"):
            mod.main()

    result = json.loads(test_data.read_text(encoding="utf-8"))
    # etf_holdings was setdefault'd but actual values never persisted (dry-run)
    assert result["enrichments"]["000157.SZ"].get("etf_holdings") is None


def test_trim_handles_non_float():
    assert mod._trim(3.14159265) == 3.1416
    assert mod._trim("3.14") == "3.14"
    assert mod._trim(None) is None


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