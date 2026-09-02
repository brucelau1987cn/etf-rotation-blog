import importlib.util
import json
import types
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


def test_aggregate_top10_detail_accepts_current_name_shape_and_excludes_historical_exit():
    rows = [
        {"股票代码": "600269.SH", "名称": "历史退出股东", "持股变动类型": "新出"},
        {"股票代码": "600269.SH", "名称": "长城人寿保险股份有限公司-自有资金",
         "排名": 2.0, "公告日期": "20260811", "截止日期": "20260630"},
        {"股票代码": "600269.SH", "名称": "香港中央结算有限公司",
         "排名": 6.0, "公告日期": "20260811", "报告期[20260630]": "2026年中报"},
    ]
    detail = fetch.aggregate_top10_detail("600269.SH", rows, "20260630")
    assert detail == {
        "股票代码": "600269.SH",
        "前十大流通股东名称(报告期)[20260630]": "长城人寿保险股份有限公司-自有资金, 香港中央结算有限公司",
    }


def test_select_latest_top10_report_row_uses_latest_explicit_period():
    rows = [{
        "股票代码": "603262.SH",
        "前十大流通股东名称(报告期)[20250930]": "旧股东",
        "前十大流通股东名称(报告期)[20251231]": "年报股东",
        "前十大流通股东名称(报告期)[20260331]": "最新股东甲, 最新股东乙",
    }]
    assert fetch.select_latest_top10_report_row("603262.SH", rows) == {
        "股票代码": "603262.SH",
        "前十大流通股东名称(报告期)[20260331]": "最新股东甲, 最新股东乙",
    }


def test_fetch_never_writes_synthetic_latest_shareholder_period_and_fails_closed():
    source = (ROOT / "scripts/fetch_low_chip_enrichments.py").read_text(encoding="utf-8")
    # 核心防造假契约（不可退化）：绝不合成占位报告期。
    assert "period or 'latest'" not in source
    assert 'period or "latest"' not in source
    # 展示型附加字段（行业/股东名单）缺失走软跳过、badge 默认 false，
    # 由 50bd377「股东/财务/行业附加数据缺失不再阻断发布」批准；
    # 入池（周/月/季交集）与 unlock 风险仍严格 fail-closed。
    assert "top10 shareholder names unavailable" in source
    assert "shareholder names without report period" in source


def test_fetch_ftshare_holders_maps_to_iwencai_compatible_field(monkeypatch):
    fake_stdout = json.dumps({
        "000157.SZ": {"前十大流通股东名称(报告期)[20260630]": "香港中央结算, 湖南兴湘投资, 长沙中联和一盛"},
    }, ensure_ascii=False)

    def fake_run(_cmd, **kwargs):
        return types.SimpleNamespace(returncode=0, stdout=fake_stdout, stderr="")

    monkeypatch.setattr(fetch.subprocess, "run", fake_run)
    result = fetch.fetch_ftshare_holders(["000157.SZ"])
    assert result == {
        "000157.SZ": {"前十大流通股东名称(报告期)[20260630]": "香港中央结算, 湖南兴湘投资, 长沙中联和一盛"},
    }


def test_fetch_ftshare_holders_fails_soft_on_subprocess_error(monkeypatch):
    def fake_run(_cmd, **kwargs):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="sdk missing")

    monkeypatch.setattr(fetch.subprocess, "run", fake_run)
    assert fetch.fetch_ftshare_holders(["000157.SZ"]) == {}


def test_fetch_ftshare_holders_returns_empty_for_empty_codes(monkeypatch):
    called = []

    def fake_run(_cmd, **kwargs):
        called.append(1)
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(fetch.subprocess, "run", fake_run)
    assert fetch.fetch_ftshare_holders([]) == {}
    assert called == []  # 空输入不触发 subprocess
