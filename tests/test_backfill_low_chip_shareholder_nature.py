import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/backfill_low_chip_shareholder_nature.py"
spec = importlib.util.spec_from_file_location("backfill_low_chip_shareholder_nature", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_select_holder_period_uses_latest_period_not_after_snapshot_date():
    rows = [
        {"period": "20260331", "name": "旧基金"},
        {"period": "20260630", "name": "长城人寿保险股份有限公司"},
        {"period": "20260930", "name": "未来基金"},
    ]
    assert mod.select_holder_period(rows, "2026-08-11") == "20260630"
    assert mod.select_holder_period(rows, "2026-04-01") == "20260331"


def test_classify_holder_names_keeps_quality_and_institutional_separate():
    quality, institutional = mod.classify_holder_names([
        "全国社保基金一一八组合",
        "长城人寿保险股份有限公司",
        "香港中央结算有限公司",
        "全国社保基金一一八组合",
    ])
    assert quality == ["全国社保基金一一八组合"]
    assert institutional == ["长城人寿保险股份有限公司", "香港中央结算有限公司"]


def test_apply_nature_updates_only_selected_snapshot_symbols():
    payload = {
        "data_as_of": "2026-08-11",
        "intersection": ["600269.SH"],
        "enrichments": {"600269.SH": {"industry": "交通运输"}},
    }
    evidence = {
        "600269.SH": [
            {"period": "20260331", "name": "旧基金"},
            {"period": "20260630", "name": "长城人寿保险股份有限公司"},
            {"period": "20260630", "name": "香港中央结算有限公司"},
        ],
        "300910.SZ": [{"period": "20260331", "name": "全国社保基金一零九组合"}],
    }
    missing = mod.apply_nature(payload, evidence)
    assert missing == []
    enr = payload["enrichments"]["600269.SH"]
    assert enr["shareholder_nature_report_period"] == "20260630"
    assert enr["quality_shareholder_names"] == []
    assert enr["institutional_shareholder_names"] == ["长城人寿保险股份有限公司", "香港中央结算有限公司"]
    assert "300910.SZ" not in payload["enrichments"]


def test_apply_nature_reports_missing_evidence_fail_closed():
    payload = {
        "data_as_of": "2026-08-14",
        "intersection": ["301632.SZ"],
        "enrichments": {"301632.SZ": {}},
    }
    assert mod.apply_nature(payload, {}) == ["301632.SZ"]


def test_select_evidence_period_falls_back_to_latest_available_before_snapshot():
    rows = [{"period": "20260331", "name": "甲基金"}]
    assert mod.select_evidence_period(rows, "20260731", "2026-08-03") == "20260331"


def test_required_periods_uses_snapshot_shareholder_metrics_period():
    payloads = [(Path("2026-08-11.json"), {
        "intersection": ["600269.SH"],
        "enrichments": {"600269.SH": {"shareholder_metrics": {"report_period": "2026-06-30"}}},
    })]
    assert mod.required_periods(payloads) == {"600269.SH": {"20260630"}}


def test_holder_query_uses_explicit_quarter_report_wording(monkeypatch):
    seen = []
    monkeypatch.setattr(mod, "iwencai", lambda query, limit=30, timeout=90: seen.append(query) or {"datas": []})
    mod.holder_names_for_period("002993.SZ", "20260331")
    assert seen == ["002993 2026年一季报前十大流通股东明细、流通股东名称"]
