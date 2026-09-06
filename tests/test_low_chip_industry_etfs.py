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
    assert result == {"stocks": 2, "covered": 1, "pool_count": 91}
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


def test_low_chip_page_renders_industry_etfs_from_91_pool():
    page = (ROOT / "src/pages/rolling/low-chip.astro").read_text(encoding="utf-8")
    assert "industryEtfs:" in page
    assert "renderIndustryEtfSection(row)" in page
    assert "行业ETF · 91只池" in page
    assert "行业待补充" in page
    assert "映射尚未计算" in page
    assert "industryEtfs: Array.isArray(row.industry_etfs)" in page
