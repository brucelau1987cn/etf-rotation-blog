"""Tests for the controlled historical D1 industry-ETF backfill verifier."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/backfill_low_chip_industry_etfs_d1.py"
SPEC = importlib.util.spec_from_file_location("backfill_low_chip_industry_etfs_d1", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
sys.modules["backfill_low_chip_industry_etfs_d1"] = mod
SPEC.loader.exec_module(mod)


def test_validate_results_accepts_exact_mapping():
    expected = {
        "603501": {
            "industry_etfs": [{
                "code": "512480.SH",
                "name": "半导体ETF国联安",
                "theme": "半导体",
                "category": "行业",
                "match_type": "exact",
                "source_pool": "etf-garden-formal-91",
            }],
            "industry_etf_status": "matched",
            "industry_etf_pool_count": 91,
        },
        "603992": {
            "industry_etfs": [],
            "industry_etf_status": "uncovered",
            "industry_etf_pool_count": 91,
        },
    }
    actual = [
        {"stock_code": code, **record}
        for code, record in expected.items()
    ]
    assert mod.validate_results("20260904", expected, actual) == []


def test_validate_results_reports_missing_and_wrong_pool_candidate():
    expected = {
        "603501": {
            "industry_etfs": [{
                "code": "512480.SH",
                "name": "半导体ETF国联安",
                "theme": "半导体",
                "category": "行业",
                "match_type": "exact",
                "source_pool": "etf-garden-formal-91",
            }],
            "industry_etf_status": "matched",
            "industry_etf_pool_count": 91,
        },
        "688568": {
            "industry_etfs": [],
            "industry_etf_status": "industry_missing",
            "industry_etf_pool_count": 91,
        },
    }
    actual = [{
        "stock_code": "603501",
        "industry_etfs": [{"code": "999999.SH", "source_pool": "other"}],
        "industry_etf_status": "matched",
        "industry_etf_pool_count": 90,
    }]
    errors = mod.validate_results("20260904", expected, actual)
    assert any("603501" in error for error in errors)
    assert any("688568" in error for error in errors)


def test_validate_results_rejects_invalid_candidate_elements():
    candidate = {"code": "512480.SH", "name": "半导体ETF国联安", "theme": "半导体", "category": "行业", "match_type": "exact", "source_pool": "etf-garden-formal-91"}
    expected = {"603501": {
        "industry_etfs": [candidate],
        "industry_etf_status": "matched",
        "industry_etf_pool_count": 91,
    }}
    actual = [{"stock_code": "603501", **expected["603501"], "industry_etfs": [candidate, None]}]
    assert mod.validate_results("20260904", expected, actual) == ["20260904/603501: industry_etfs mismatch"]


def test_validate_results_rejects_invalid_elements_on_both_sides():
    invalid = {
        "industry_etfs": [None],
        "industry_etf_status": "matched",
        "industry_etf_pool_count": 91,
    }
    expected = {"603501": invalid}
    actual = [{"stock_code": "603501", **invalid}]
    assert mod.validate_results("20260904", expected, actual) == ["20260904/603501: industry_etfs mismatch"]


def test_validate_results_rejects_candidate_order_changes():
    candidates = [
        {"code": "159883.SZ", "name": "医疗器械ETF永赢", "theme": "医疗器械", "category": "医药健康", "match_type": "exact", "source_pool": "etf-garden-formal-91"},
        {"code": "512170.SH", "name": "医疗ETF华宝", "theme": "医疗", "category": "行业", "match_type": "parent", "source_pool": "etf-garden-formal-91"},
    ]
    expected = {"688198": {
        "industry_etfs": candidates,
        "industry_etf_status": "matched",
        "industry_etf_pool_count": 91,
    }}
    actual = [{"stock_code": "688198", **expected["688198"], "industry_etfs": list(reversed(candidates))}]
    assert mod.validate_results("20260904", expected, actual) == ["20260904/688198: industry_etfs mismatch"]
