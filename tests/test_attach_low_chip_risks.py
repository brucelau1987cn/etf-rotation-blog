import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.attach_low_chip_risks import aggregate_risk, attach_risks, classify_price_risk, fetch_prices, validate_risk_payload, _request_json, _result
import urllib.error


def bars(closes, start=date(2026, 9, 1)):
    return [{"date": (start + timedelta(days=i)).isoformat(), "close": close} for i, close in enumerate(closes)]


def complete_adapters():
    def adapter(_code):
        return {"complete": True, "level": "none", "reasons": [], "source": "mock"}
    return adapter, adapter, adapter


def complete_parts():
    return [
        {"complete": True, "status": "ok", "level": "none", "reasons": [], "source": "qfq daily close", "as_of": "2026-09-21"},
        {"complete": True, "status": "ok", "level": "none", "reasons": [], "source": "mock"},
    ]


def test_price_rules_separate_declines_and_limit_down_by_exchange_and_st():
    result = classify_price_risk(bars(list(range(100, 79, -1))), symbol="000001.SZ")
    assert result["level"] == "watch"
    assert "consecutive_declines_5d" in result["reasons"]
    assert "consecutive_limit_down" not in result["reasons"]

    st_prices = [100.0]
    for _ in range(20): st_prices.append(st_prices[-1] * 0.95)
    st = classify_price_risk(bars(st_prices), symbol="ST000001.SZ")
    assert st["limit_threshold"] == 0.05
    assert "consecutive_limit_down" in st["reasons"]

    bj_prices = [100.0]
    for _ in range(20): bj_prices.append(bj_prices[-1] * 0.70)
    bj = classify_price_risk(bars(bj_prices), symbol="920001.BJ")
    assert bj["limit_threshold"] == 0.30
    assert "consecutive_limit_down" in bj["reasons"]

    for symbol in ("688001.SH", "300001.SZ"):
        prices = [100.0]
        for _ in range(20): prices.append(prices[-1] * 0.80)
        assert classify_price_risk(bars(prices), symbol=symbol)["limit_threshold"] == 0.20


def test_short_term_drawdown_requires_complete_window_and_dated_bars():
    with pytest.raises(ValueError, match="complete 20-bar window"):
        classify_price_risk(bars([100] * 6), symbol="000001.SZ")

    result = classify_price_risk(bars([100] * 19 + [80]), symbol="000001.SZ")
    assert "short_term_drawdown" in result["reasons"]
    assert result["as_of"] == "2026-09-20"


def test_dates_are_deduplicated_sorted_and_capped_by_as_of_with_lag_limit():
    rows = bars([100] * 20)
    rows += [{"date": "2026-09-05", "close": 101}, {"date": "2026-09-21", "close": 99}]
    result = classify_price_risk(rows, symbol="000001.SZ", as_of="2026-09-21")
    assert result["coverage_bars"] == 21
    assert result["as_of"] == "2026-09-21"
    with pytest.raises(ValueError, match="future date"):
        classify_price_risk(bars([100] * 20) + [{"date": "2026-09-22", "close": 100}], as_of="2026-09-21")
    with pytest.raises(ValueError, match="max 7"):
        classify_price_risk(bars([100] * 20, date(2026, 8, 1)), as_of="2026-09-21")

    duplicate = bars([100] * 20)
    duplicate.append({"date": "2026-09-05", "close": 80})
    assert classify_price_risk(duplicate, symbol="000001.SZ")["coverage_bars"] == 20


def test_schema_rejects_empty_object_and_non_string_reasons():
    valid = aggregate_risk([
        {"complete": True, "status": "ok", "level": "none", "reasons": [], "source": "qfq daily close", "as_of": "2026-09-21"},
    ], as_of="2026-09-21")
    validate_risk_payload(valid)
    with pytest.raises(ValueError, match="reasons.*string"):
        bad = dict(valid, reasons=[{"reason": "bad"}])
        validate_risk_payload(bad)
    with pytest.raises(ValueError, match="components"):
        validate_risk_payload(dict(valid, components=[{}]))


def test_aggregate_schema_and_invalid_adapter_are_fail_closed(tmp_path):
    assert aggregate_risk([
        {"complete": True, "status": "ok", "level": "watch", "reasons": ["x"], "source": "qfq daily close", "as_of": "2026-09-21"},
        {"complete": True, "status": "ok", "level": "high", "reasons": ["y"], "source": "mock"},
    ], as_of="2026-09-21")["level"] == "high"

    source = tmp_path / "stocks.json"
    source.write_text(json.dumps({"data_as_of": "2026-09-21", "intersection": ["000001.SZ"]}), encoding="utf-8")
    adapters = complete_adapters()
    result = attach_risks(source, price_fetcher=lambda _code: bars([100] * 20),
                          announcement_adapter=lambda _code: {"complete": True, "level": "none", "reasons": "bad", "source": "mock"},
                          rating_adapter=adapters[1], news_adapter=adapters[2])
    assert result["status"] == "STAGING BLOCKER"
    assert result["payload"]["enrichments"]["000001.SZ"]["risk"]["status"] == "failed"


def test_attach_success_schema_and_dry_run_never_writes(tmp_path):
    source = tmp_path / "stocks.json"
    original = {"data_as_of": "2026-09-21", "intersection": ["000001.SZ"], "enrichments": {"000001.SZ": {}}}
    source.write_text(json.dumps(original), encoding="utf-8")
    adapters = complete_adapters()
    result = attach_risks(source, dry_run=True, price_fetcher=lambda _code: bars([100] * 20, date(2026, 9, 2)),
                          announcement_adapter=adapters[0], rating_adapter=adapters[1], news_adapter=adapters[2])
    assert result["status"] == "dry-run"
    assert json.loads(source.read_text(encoding="utf-8")) == original
    risk = result["payload"]["enrichments"]["000001.SZ"]["risk"]
    assert {"version", "as_of", "status", "source", "freshness", "coverage", "components"} <= risk.keys()


def test_attach_success_writes_and_readback_validates(tmp_path):
    source = tmp_path / "stocks.json"
    source.write_text(json.dumps({"data_as_of": "2026-09-21", "intersection": ["000001.SZ"]}), encoding="utf-8")
    adapters = complete_adapters()
    result = attach_risks(source, price_fetcher=lambda _code: bars([100] * 20, date(2026, 9, 2)),
                          announcement_adapter=adapters[0], rating_adapter=adapters[1], news_adapter=adapters[2])
    assert result["status"] == "ok"
    saved = json.loads(source.read_text(encoding="utf-8"))
    validate_risk_payload(saved["enrichments"]["000001.SZ"]["risk"])


def test_attach_rejects_invalid_persisted_readback(tmp_path, monkeypatch):
    source = tmp_path / "stocks.json"
    source.write_text(json.dumps({"data_as_of": "2026-09-21", "intersection": ["000001.SZ"]}), encoding="utf-8")
    adapters = complete_adapters()

    def corrupt_write(path, _payload):
        path.write_text(json.dumps({"data_as_of": "2026-09-21", "intersection": ["000001.SZ"],
                                    "enrichments": {"000001.SZ": {"risk": {}}}}), encoding="utf-8")

    monkeypatch.setattr("scripts.attach_low_chip_risks._atomic_write", corrupt_write)
    with pytest.raises(ValueError, match="schema invalid"):
        attach_risks(source, price_fetcher=lambda _code: bars([100] * 20, date(2026, 9, 2)),
                     announcement_adapter=adapters[0], rating_adapter=adapters[1], news_adapter=adapters[2])


def test_failure_has_failed_status_and_preserves_formal_file(tmp_path):
    source = tmp_path / "stocks.json"
    original = {"data_as_of": "2026-09-21", "intersection": ["000001.SZ"], "enrichments": {"000001.SZ": {"risk": {"level": "none"}}}}
    source.write_text(json.dumps(original), encoding="utf-8")
    result = attach_risks(source, price_fetcher=lambda _code: [],
                          announcement_adapter=lambda _code: {"complete": False, "error": "unavailable"},
                          rating_adapter=lambda _code: {"complete": False, "error": "unconfigured"},
                          news_adapter=lambda _code: {"complete": False, "error": "unconfigured"})
    assert result["status"] == "STAGING BLOCKER"
    assert result["payload"]["enrichments"]["000001.SZ"]["risk"]["status"] == "failed"
    assert result["payload"]["enrichments"]["000001.SZ"]["risk"]["level"] == "unknown"
    assert json.loads(source.read_text(encoding="utf-8")) == original


def test_fetch_prices_passes_data_as_of_to_history_loader(monkeypatch):
    import scripts.attach_low_chip_touchstone as touchstone
    calls = []
    monkeypatch.setattr(touchstone, "load_history", lambda code, end: calls.append((code, end)) or [])
    assert fetch_prices("000001.SZ", "2026-09-18") == []
    assert calls == [("000001.SZ", "2026-09-18")]


def test_failed_components_are_unknown_and_not_counted_as_coverage(tmp_path):
    source = tmp_path / "stocks.json"
    source.write_text(json.dumps({"data_as_of": "2026-09-21", "intersection": ["000001.SZ"]}), encoding="utf-8")
    adapters = complete_adapters()
    result = attach_risks(
        source,
        dry_run=True,
        price_fetcher=lambda _code, _as_of: bars([100] * 19),
        announcement_adapter=lambda _code: (_ for _ in ()).throw(TimeoutError("slow")),
        rating_adapter=adapters[1],
        news_adapter=adapters[2],
    )
    risk = result["payload"]["enrichments"]["000001.SZ"]["risk"]
    assert risk["status"] == "failed"
    assert risk["components"][0]["source"] == "price"
    assert risk["components"][0]["level"] == "unknown"
    assert risk["components"][1]["source"] == "announcement"
    assert risk["components"][1]["level"] == "unknown"
    assert result["coverage"]["sources"]["price"]["coverage"] == 0
    assert result["coverage"]["sources"]["price"]["failed"] == 1
    assert result["coverage"]["sources"]["announcement"]["coverage"] == 0
    assert result["coverage"]["sources"]["announcement"]["failed"] == 1


def test_slow_adapter_is_hard_terminated(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.attach_low_chip_risks.SOURCE_BUDGET_SECONDS", 0.05)
    source = tmp_path / "stocks.json"
    source.write_text(json.dumps({"data_as_of": "2026-09-21", "intersection": ["000001.SZ"]}), encoding="utf-8")
    def slow(_code, **_kwargs):
        time.sleep(1)
    result = attach_risks(source, dry_run=True, price_fetcher=lambda *_args, **_kwargs: bars([100] * 20),
                          announcement_adapter=slow, rating_adapter=lambda *_a, **_k: {"complete": False, "error": "x"},
                          news_adapter=lambda *_a, **_k: {"complete": False, "error": "x"})
    assert result["status"] == "STAGING BLOCKER"
    assert "exceeded" in result["payload"]["enrichments"]["000001.SZ"]["risk"]["reasons"][0]


def test_schema_semantics_reject_inconsistent_status_and_coverage():
    valid = aggregate_risk(complete_parts(), as_of="2026-09-21")
    with pytest.raises(ValueError, match="failed component"):
        validate_risk_payload(dict(valid, status="ok", coverage={"components": 1, "complete": 0}, components=[dict(valid["components"][0], complete=False, status="failed", level="unknown")]))
    with pytest.raises(ValueError, match="coverage.complete"):
        validate_risk_payload(dict(valid, coverage={"components": 2, "complete": 0}))


def test_schema_rejects_forged_top_level_risk_semantics():
    valid = aggregate_risk([
        {"complete": True, "status": "ok", "level": "high", "reasons": ["duplicate", "x"], "source": "qfq daily close", "as_of": "2026-09-21"},
        {"complete": True, "status": "ok", "level": "watch", "reasons": ["duplicate", "y"], "source": "mock"},
    ], as_of="2026-09-21")
    with pytest.raises(ValueError, match="top-level level"):
        validate_risk_payload(dict(valid, level="watch"))
    with pytest.raises(ValueError, match="component union"):
        validate_risk_payload(dict(valid, reasons=["duplicate"]))
    with pytest.raises(ValueError, match="price freshness"):
        validate_risk_payload(dict(valid, freshness={"price_as_of": "2026-09-20"}))
    with pytest.raises(ValueError, match="future"):
        validate_risk_payload(dict(valid, as_of="2026-09-20"))
