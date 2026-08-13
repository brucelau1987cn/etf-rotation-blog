from __future__ import annotations

import pytest

from scripts.a_share_fundamental_shadow import (
    build_coverage, build_shadow_metric, compute_incremental_returns,
    attach_share_timeline, validate_baostock_workers, normalize_baostock_row,
    load_universe, partition_items, should_relogin,
)


def test_coverage_contract_fails_closed_when_any_symbol_failed():
    coverage = build_coverage(expected=5, succeeded=3, empty=1, failed_symbols=["600000"])
    assert coverage == {
        "expected": 5,
        "succeeded": 3,
        "empty": 1,
        "failed": 1,
        "coverage": 0.8,
        "publishable": False,
        "failed_symbols": ["600000"],
    }


def test_coverage_contract_rejects_inconsistent_counts():
    with pytest.raises(ValueError, match="partition"):
        build_coverage(expected=5, succeeded=5, empty=1, failed_symbols=[])


def test_coverage_contract_treats_empty_as_staging_blocker():
    coverage = build_coverage(expected=2, succeeded=1, empty=1, failed_symbols=[])
    assert coverage["coverage"] == 1.0
    assert coverage["publishable"] is False


def test_shadow_metric_is_observation_only_for_first_twenty_sessions():
    row = build_shadow_metric(
        trade_date="2026-08-13", stock_code="600021", name="上海电力",
        close=14.53, pe_ttm=12.5, pb=1.2, ps_ttm=0.8, pcf_ttm=9.1,
        total_share=1_000_000_000, observation_sessions=10,
    )
    assert row["total_mv"] == 14_530_000_000
    assert row["trade_date"] == "20260813"
    assert row["fundamental_shadow"] == {
        "mode": "shadow_research_only",
        "observation_sessions": 10,
        "minimum_observation_sessions": 10,
        "maturity_sessions": 20,
        "status": "ACCUMULATING",
        "production_weights_changed": False,
        "formal_signal_logic_changed": False,
        "production_role": "audit_only",
    }


def test_shadow_metric_becomes_observed_after_twenty_sessions_without_changing_actions():
    row = build_shadow_metric(
        trade_date="2026-08-13", stock_code="600021", name="上海电力",
        close=14.53, pe_ttm=12.5, pb=1.2, ps_ttm=0.8, pcf_ttm=9.1,
        total_share=1_000_000_000, observation_sessions=20,
    )
    assert row["fundamental_shadow"]["status"] == "OBSERVED"
    assert row["fundamental_shadow"]["production_role"] == "audit_only"


def test_incremental_returns_use_preceding_trading_bar_then_emit_only_new_rows():
    rows = [
        {"date": "2026-08-10", "close": 10.0},
        {"date": "2026-08-11", "close": 11.0},
        {"date": "2026-08-12", "close": 12.1},
    ]
    result = compute_incremental_returns(rows, last_stored_date="2026-08-10")
    assert result == [
        {"date": "2026-08-11", "close": 11.0, "pct_change": 10.0},
        {"date": "2026-08-12", "close": 12.1, "pct_change": 10.0},
    ]


def test_incremental_returns_require_a_preceding_bar():
    with pytest.raises(ValueError, match="preceding trading bar"):
        compute_incremental_returns(
            [{"date": "2026-08-11", "close": 11.0}],
            last_stored_date="2026-08-10",
        )


def test_share_timeline_keeps_dates_before_first_reliable_observation_unavailable():
    prices = [
        {"date": "2021-01-01", "close": 10},
        {"date": "2021-06-01", "close": 11},
        {"date": "2022-01-01", "close": 12},
    ]
    shares = [{"date": "2021-06-01", "total_share": 1_000_000_000}]
    rows = attach_share_timeline(prices, shares)
    assert rows[0]["total_share"] is None
    assert rows[0]["total_mv"] is None
    assert rows[1]["total_share"] == 1_000_000_000
    assert rows[2]["total_mv"] == 12_000_000_000


def test_baostock_worker_limit_caps_independent_processes_at_four():
    assert validate_baostock_workers(1) == 1
    assert validate_baostock_workers(4) == 4
    with pytest.raises(ValueError, match="at most 4 independent processes"):
        validate_baostock_workers(5)
    with pytest.raises(ValueError, match="at least 1"):
        validate_baostock_workers(0)


def test_baostock_row_maps_valuation_fields_and_requires_a_reliable_share_date():
    row = normalize_baostock_row(
        ["2026-08-13", "sh.600021", "14.53", "12.5", "1.2", "0.8", "9.1"],
        total_share=1_000_000_000, share_observed_date="2026-08-01", name="上海电力",
        observation_sessions=10,
    )
    assert row["pe_ttm"] == 12.5
    assert row["total_mv"] == 14_530_000_000
    assert row["share_observed_date"] == "2026-08-01"
    with pytest.raises(ValueError, match="reliable share observation"):
        normalize_baostock_row(
            ["2026-08-13", "sh.600021", "14.53", "12.5", "1.2", "0.8", "9.1"],
            total_share=1_000_000_000, share_observed_date=None, name="上海电力",
            observation_sessions=10,
        )


def test_low_chip_universe_deduplicates_periods_and_excludes_bse(tmp_path):
    path = tmp_path / "pool.json"
    path.write_text('{"periods":{"week":[{"symbol":"600021.SH","name":"上海电力"}],"month":[{"symbol":"600021.SH","name":"上海电力"},{"symbol":"000001.SZ","name":"平安银行"},{"symbol":"920086.BJ","name":"科马材料"}]}}')
    assert load_universe(path) == [
        {"code": "000001", "name": "平安银行", "market": "SZ"},
        {"code": "600021", "name": "上海电力", "market": "SH"},
    ]


def test_baostock_work_is_partitioned_into_at_most_four_persistent_sessions():
    chunks = partition_items(list(range(10)), workers=4)
    assert chunks == [[0, 1, 2], [3, 4, 5], [6, 7], [8, 9]]
    with pytest.raises(ValueError, match="at most 4 independent processes"):
        partition_items(list(range(10)), workers=5)


def test_only_session_expiry_triggers_a_bounded_relogin():
    assert should_relogin("RuntimeError: 用户未登录") is True
    assert should_relogin("no reliable share observation") is False
