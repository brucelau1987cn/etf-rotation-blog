from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "investment_research_rigor", ROOT / "scripts/investment_research_rigor.py",
)
assert spec and spec.loader
rigor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rigor)


def test_market_cap_exact_pass_and_fail():
    passed = rigor.verify_market_cap("510", "9.11e9", "4.6461e12", "0.01")
    assert passed["status"] == "pass"
    assert rigor.dec(passed["calculated"]) == rigor.dec("4.6461e12")
    failed = rigor.verify_market_cap("510", "9.11e9", "4e12", "1")
    assert failed["status"] == "fail"


def test_cross_validate_requires_two_sources_and_detects_divergence():
    passed = rigor.cross_validate({"annual_report": "100", "exchange": "100.5"}, "1")
    assert passed["status"] == "pass"
    failed = rigor.cross_validate({"annual_report": "100", "third_party": "110"}, "1")
    assert failed["status"] == "fail"
    try:
        rigor.cross_validate({"one": "100"})
    except ValueError as exc:
        assert "at least two" in str(exc)
    else:
        raise AssertionError("single-source validation must fail closed")


def test_scenario_is_deterministic_and_ordered():
    result = rigor.scenario("100", "5", ["0.15", "0.08", "0"], ["25", "20", "12"], 3)
    assert result["check"] == "three_scenario"
    prices = [float(item["target_price"]) for item in result["scenarios"]]
    assert prices == sorted(prices, reverse=True)
    assert result == rigor.scenario("100", "5", ["0.15", "0.08", "0"], ["25", "20", "12"], 3)
