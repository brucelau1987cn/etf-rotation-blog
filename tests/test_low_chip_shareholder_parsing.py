import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


enrich = load_module("enrich_low_chip_stocks", ROOT / "scripts/enrich_low_chip_stocks.py")
fetch = load_module("fetch_low_chip_enrichments", ROOT / "scripts/fetch_low_chip_enrichments.py")


def test_shareholders_from_row_selects_latest_shareholder_period_independent_of_key_order():
    old = "全国社保基金一一八组合, 旧基金"
    new = "乙保险股份有限公司, 香港中央结算有限公司"
    row_a = {
        "前十大流通股东名称(报告期)[20260331]": old,
        "前十大流通股东名称(报告期)[20260630]": new,
    }
    row_b = dict(reversed(list(row_a.items())))
    assert enrich.shareholders_from_row(row_a) == ["乙保险股份有限公司", "香港中央结算有限公司"]
    assert enrich.shareholders_from_row(row_b) == ["乙保险股份有限公司", "香港中央结算有限公司"]


def test_shareholders_from_row_normalizes_delimiters_and_dedupes():
    row = {
        "前十大流通股东名称(报告期)[20260630]":
            "甲公司||乙保险股份有限公司，甲公司, 香港中央结算有限公司"
    }
    assert enrich.shareholders_from_row(row) == ["甲公司", "乙保险股份有限公司", "香港中央结算有限公司"]


def test_classification_excludes_quality_names_from_institutional_tier():
    names = ["全国社保基金一一八组合", "乙保险股份有限公司", "香港中央结算有限公司"]
    quality, institutional = enrich.classify_shareholders(names)
    assert quality == ["全国社保基金一一八组合"]
    assert institutional == ["乙保险股份有限公司", "香港中央结算有限公司"]


def test_report_period_ignores_quote_and_announcement_dates():
    rows = [{
        "前十大流通股东名称(报告期)[20260630]": "甲公司",
        "最新价[20260817]": 10.2,
        "公告日期[20260811]": "20260811",
    }]
    assert fetch.report_period_from_rows(rows) == "20260630"


def test_report_period_returns_empty_without_shareholder_period():
    assert fetch.report_period_from_rows([{"最新价[20260817]": 10.2, "公告日期[20260811]": "20260811"}]) == ""


def test_merge_profile_rows_prefers_validated_individual_top10_and_keeps_batch_industry():
    batch = {
        "股票代码": "600269.SH",
        "所属申万行业": "交通运输--铁路公路--高速公路",
        "前十大流通股东名称(报告期)[20260331]": "旧基金, 旧保险",
    }
    individual = {
        "股票代码": "600269.SH",
        "前十大流通股东名称(报告期)[20260630]": "长城人寿保险股份有限公司, 香港中央结算有限公司",
    }
    merged = enrich.merge_profile_rows(batch, individual)
    assert merged["所属申万行业"] == batch["所属申万行业"]
    assert enrich.shareholders_from_row(merged) == ["长城人寿保险股份有限公司", "香港中央结算有限公司"]
    assert "旧基金" not in enrich.shareholders_from_row(merged)


def test_missing_shareholder_evidence_has_no_valid_period():
    row = {"股票代码": "600269.SH", "所属申万行业": "交通运输"}
    assert fetch.has_top10_names(row) is False
    assert fetch.report_period_from_rows([row]) == ""
