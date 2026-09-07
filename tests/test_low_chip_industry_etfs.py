"""Contract tests for mapping low-chip SW level-2 industries to the formal 91-ETF pool."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/attach_low_chip_industry_etfs.py"
SPEC = importlib.util.spec_from_file_location("attach_low_chip_industry_etfs", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
sys.modules["attach_low_chip_industry_etfs"] = mod
SPEC.loader.exec_module(mod)


def pool_rows() -> list[dict]:
    rows = [
        {"code": "512480", "name": "半导体ETF国联安", "theme": "半导体", "category": "行业", "tier": "formal"},
        {"code": "560280", "name": "工程机械ETF广发", "theme": "工程机械", "category": "高端制造与国防", "tier": "formal"},
        {"code": "515260", "name": "电子ETF华宝", "theme": "电子", "category": "行业", "tier": "formal"},
    ]
    rows.extend({
        "code": f"8{i:05d}", "name": f"占位ETF{i}", "theme": f"占位主题{i}",
        "category": "测试", "tier": "formal",
    } for i in range(88))
    rows.append({"code": "999999", "name": "研究ETF", "theme": "半导体", "category": "行业", "tier": "research"})
    return rows


def test_exact_industry_mapping_is_restricted_to_formal_pool():
    rows = mod.match_industry_etfs("半导体", pool_rows())
    assert [row["code"] for row in rows] == ["512480.SH"]
    assert rows[0]["match_type"] == "exact"
    assert rows[0]["source_pool"] == "etf-garden-formal-91"


def test_alias_mapping_uses_pool_product():
    rows = mod.match_industry_etfs("工程机械", pool_rows())
    assert [row["code"] for row in rows] == ["560280.SH"]


def test_uncovered_industry_returns_empty():
    assert mod.match_industry_etfs("家居用品", pool_rows()) == []


def test_fetch_etf_profit_ratios_parses_per_batch(monkeypatch):
    """逐只查询解析: 基金代码带后缀、值转 float、失败跳过。"""
    import subprocess
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        q = cmd[cmd.index("-q") + 1]
        code = q.split(" ")[0]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({
            "code_count": 1, "returned_count": 1,
            "datas": [{"基金代码": f"{code}.SH", "收盘获利[20260904]": 0.211}],
        }))

    monkeypatch.setattr(mod, "subprocess", None)  # 不触发真实 subprocess 属性访问
    import types
    fake_sp = types.SimpleNamespace(run=fake_run)
    monkeypatch.setattr(mod, "subprocess", fake_sp)
    result = mod.fetch_etf_profit_ratios(["560280.SH", "512480.SH"], "20260904")
    assert result == {"560280.SH": 0.211, "512480.SH": 0.211}
    assert len(calls) == 2  # 逐只 = 2 次调用


def test_fetch_etf_profit_ratios_skips_bad_batch(monkeypatch):
    """某只查询 rc!=0 → 跳过不阻塞, 其他正常。"""
    import subprocess, types
    def fake_run(cmd, **kw):
        q = cmd[cmd.index("-q") + 1]
        code = q.split(" ")[0]
        if code == "560280":
            return subprocess.CompletedProcess(cmd, 1, stdout="quota exhausted")
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({
            "code_count": 1, "returned_count": 1,
            "datas": [{"基金代码": "512480.SH", "收盘获利[20260904]": 0.058}],
        }))
    monkeypatch.setattr(mod, "subprocess", types.SimpleNamespace(run=fake_run))
    result = mod.fetch_etf_profit_ratios(["560280.SH", "512480.SH"], "20260904")
    assert result == {"512480.SH": 0.058}


def test_attach_skips_iwencai_without_trade_date(tmp_path):
    """无 data_as_of 的 payload → attach 不调 iWenCai(profit_map 空), 仍写 profit_ratio=None。"""
    payload = {
        "intersection": ["603501.SH"],
        "enrichments": {"603501.SH": {"industry_standard": "SW2021", "industry_level2": {"name": "半导体"}}},
    }
    pool = {"summary": {"universe_count": 91}, "all_rows": pool_rows()}
    data_path = tmp_path / "low-chip.json"
    pool_path = tmp_path / "pool.json"
    data_path.write_text(json.dumps(payload), encoding="utf-8")
    pool_path.write_text(json.dumps(pool), encoding="utf-8")
    result = mod.attach_industry_etfs(data_path, pool_path)
    assert result["profit_filled"] == 0
    written = json.loads(data_path.read_text(encoding="utf-8"))
    assert written["enrichments"]["603501.SH"]["industry_etfs"][0]["profit_ratio"] is None


def test_attach_writes_pool_contract(tmp_path):
    payload = {
        "intersection": ["603501.SH", "603992.SH"],
        "enrichments": {
            "603501.SH": {"industry_standard": "SW2021", "industry_level2": {"name": "半导体"}},
            "603992.SH": {"industry_standard": "SW2021", "industry_level2": {"name": "家居用品"}},
        },
    }
    pool = {"summary": {"universe_count": 91}, "all_rows": pool_rows()}
    data_path = tmp_path / "low-chip.json"
    pool_path = tmp_path / "pool.json"
    data_path.write_text(json.dumps(payload), encoding="utf-8")
    pool_path.write_text(json.dumps(pool), encoding="utf-8")

    result = mod.attach_industry_etfs(data_path, pool_path)
    assert result["stocks"] == 2
    assert result["covered"] == 1
    assert result["pool_count"] == 91
    assert result["profit_filled"] == 0  # 无 data_as_of → 不触发 iWenCai 查询
    written = json.loads(data_path.read_text(encoding="utf-8"))
    semi = written["enrichments"]["603501.SH"]
    home = written["enrichments"]["603992.SH"]
    assert semi["industry_etf_pool_count"] == 91
    assert semi["industry_etf_status"] == "matched"
    assert semi["industry_etfs"][0]["code"] == "512480.SH"
    assert home["industry_etf_status"] == "uncovered"
    assert home["industry_etfs"] == []


def test_attach_marks_missing_sw2021_level2_separately(tmp_path):
    payload = {
        "intersection": ["688568.SH"],
        "enrichments": {"688568.SH": {"industry": "半导体"}},
    }
    pool = {"summary": {"universe_count": 91}, "all_rows": pool_rows()}
    data_path = tmp_path / "low-chip.json"
    pool_path = tmp_path / "pool.json"
    data_path.write_text(json.dumps(payload), encoding="utf-8")
    pool_path.write_text(json.dumps(pool), encoding="utf-8")

    mod.attach_industry_etfs(data_path, pool_path)
    rec = json.loads(data_path.read_text(encoding="utf-8"))["enrichments"]["688568.SH"]
    assert rec["industry_etf_status"] == "industry_missing"
    assert rec["industry_etfs"] == []


def test_attach_rejects_non_91_pool(tmp_path):
    data_path = tmp_path / "low-chip.json"
    pool_path = tmp_path / "pool.json"
    data_path.write_text(json.dumps({"intersection": [], "enrichments": {}}), encoding="utf-8")
    pool_path.write_text(json.dumps({"summary": {"universe_count": 90}, "all_rows": []}), encoding="utf-8")

    try:
        mod.attach_industry_etfs(data_path, pool_path)
    except ValueError as exc:
        assert "exactly 91" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_attach_rejects_summary_actual_pool_mismatch(tmp_path):
    data_path = tmp_path / "low-chip.json"
    pool_path = tmp_path / "pool.json"
    data_path.write_text(json.dumps({"intersection": [], "enrichments": {}}), encoding="utf-8")
    pool_path.write_text(json.dumps({
        "summary": {"universe_count": 91},
        "all_rows": pool_rows()[:-2],
    }), encoding="utf-8")

    try:
        mod.attach_industry_etfs(data_path, pool_path)
    except ValueError as exc:
        assert "actual formal rows" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_attach_rejects_duplicate_formal_pool_rows(tmp_path):
    data_path = tmp_path / "low-chip.json"
    pool_path = tmp_path / "pool.json"
    data_path.write_text(json.dumps({"intersection": [], "enrichments": {}}), encoding="utf-8")
    rows = pool_rows()
    rows.append(dict(rows[0]))
    pool_path.write_text(json.dumps({
        "summary": {"universe_count": 91},
        "all_rows": rows,
    }), encoding="utf-8")

    try:
        mod.attach_industry_etfs(data_path, pool_path)
    except ValueError as exc:
        assert "actual formal rows" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_attach_rejects_blank_code_formal_row(tmp_path):
    data_path = tmp_path / "low-chip.json"
    pool_path = tmp_path / "pool.json"
    data_path.write_text(json.dumps({"intersection": [], "enrichments": {}}), encoding="utf-8")
    rows = pool_rows()
    rows.append({"code": "", "name": "空代码", "theme": "", "tier": "formal"})
    pool_path.write_text(json.dumps({
        "summary": {"universe_count": 91},
        "all_rows": rows,
    }), encoding="utf-8")

    try:
        mod.attach_industry_etfs(data_path, pool_path)
    except ValueError as exc:
        assert "actual formal rows" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_d1_api_persists_and_decodes_industry_etfs():
    source = (ROOT / "functions/api/public/v1/low-chip-metrics.js").read_text(encoding="utf-8")
    migration = (ROOT / "migrations/0020_stock_metrics_industry_etfs.sql").read_text(encoding="utf-8")
    assert "'industry_etfs'" in source
    assert "'industry_etf_status'" in source
    assert "'industry_etf_pool_count'" in source
    assert "JSON.parse(metric.industry_etfs)" in source
    assert "ADD COLUMN industry_etfs TEXT" in migration


def test_low_chip_page_renders_industry_etfs_without_pool_label():
    """行业ETF 直接显示ETF名称与代码, 无"行业ETF"标签与"91只池"说明(2026-09-07)。"""
    page = (ROOT / "src/pages/rolling/low-chip.astro").read_text(encoding="utf-8")
    # 行业ETF 胶囊显示(名称+代码)
    assert "chip-etf-pill" in page
    assert "chip-etf-name" in page
    assert "chip-etf-meta" in page
    assert "renderIndustryEtfSection(row)" in page
    # 无"行业ETF"标签文字、无"91只池"说明
    assert "chip-etf-label" not in page
    assert "行业ETF" not in page
    assert "91只池" not in page
    # 持仓ETF 展示保持关闭
    assert "renderEtfSection" not in page
    assert "持仓ETF" not in page
    assert "暂无公开数据" not in page
