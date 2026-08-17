import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/enrich_low_chip_stocks.py"
spec = importlib.util.spec_from_file_location("enrich_low_chip_stocks", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_shareholders_from_row_accepts_report_period_field():
    row = {"前十大流通股东名称(报告期)[20260630]": "甲公司, 乙保险股份有限公司, 香港中央结算有限公司"}
    assert mod.shareholders_from_row(row) == ["甲公司", "乙保险股份有限公司", "香港中央结算有限公司"]


def test_shareholder_detail_query_requests_report_period_top10_names():
    fetch_script = (Path(__file__).resolve().parents[1] / "scripts/fetch_low_chip_enrichments.py").read_text(encoding="utf-8")
    assert "最新完整报告期十大流通股东明细" in fetch_script
    assert "前十大流通股东名称" in fetch_script
    assert "has_top10_names" in fetch_script
    assert "report_period_from_rows" in fetch_script
    assert '前十大流通股东名称" +' in fetch_script
    assert "missing top10 shareholder names after per-symbol fallback" in fetch_script


def test_shareholder_classification_finds_quality_and_institutional_terms_from_report_period_field():
    row = {
        "前十大流通股东名称(报告期)[20260630]":
            "全国社保基金一一八组合, 乙保险股份有限公司, 香港中央结算有限公司"
    }
    quality, institutional = mod.classify_shareholders(mod.shareholders_from_row(row))
    assert quality == ["全国社保基金一一八组合"]
    assert institutional == ["乙保险股份有限公司", "香港中央结算有限公司"]
