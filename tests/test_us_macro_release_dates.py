from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_us_macro.py"

spec = importlib.util.spec_from_file_location("generate_us_macro_dates", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_bls_release_dates_are_explicit_and_series_specific():
    assert module.BLS_RELEASES["employment"] == {
        "observation_period": "2026-06",
        "date": "2026-07-02",
        "updated_at": "2026-07-02T08:30:00-04:00",
        "next_release": {"time": "2026-08-07T08:30", "star": None, "consensus": None},
    }
    assert module.BLS_RELEASES["cpi"] == {
        "observation_period": "2026-06",
        "date": "2026-07-14",
        "updated_at": "2026-07-14T08:30:00-04:00",
        "next_release": {"time": "2026-08-12T08:30", "star": None, "consensus": None},
    }


def test_apply_bls_release_metadata_separates_observation_from_update_date():
    official = {
        "unemployment": {"value": 4.2, "date": "2026-06-01"},
        "payrolls": {"value": 158984.0, "date": "2026-06-01"},
        "core_cpi": {"value": 336.882, "date": "2026-06-01"},
    }

    module.apply_bls_release_metadata(official)

    assert official["unemployment"]["date"] == "2026-07-02"
    assert official["payrolls"]["date"] == "2026-07-02"
    assert official["core_cpi"]["date"] == "2026-07-14"
    assert official["unemployment"]["observation_period"] == "2026-06"
    assert official["core_cpi"]["next_release"]["time"] == "2026-08-12T08:30"


def test_payroll_card_formats_level_change_in_thousands():
    item = {"value": 158984.0, "previous": 158927.0}
    assert module.payroll_change(item) == 57.0


def test_real_retail_release_metadata_uses_census_dates():
    item = {"date": "2026-06-01", "change_3m_pct": 1.23}

    module.apply_real_retail_release_metadata(item)

    assert item["date"] == "2026-07-16"
    assert item["observation_period"] == "2026-06"
    assert item["next_release"]["time"] == "2026-08-14T08:30"


def test_fundamental_preserves_observation_and_updated_metadata():
    item = {
        "value": 4.2,
        "date": "2026-07-02",
        "observation_period": "2026-06",
        "updated_at": "2026-07-02T08:30:00-04:00",
        "frequency": "月频",
        "source": "BLS",
        "stale": False,
    }

    card = module.build_fundamental_card("unemployment", "失业率", "{value:.1f}%", "detail", item)

    assert card["as_of"] == "2026-07-02"
    assert card["observation_period"] == "2026-06"
    assert card["updated_at"] == "2026-07-02T08:30:00-04:00"


def test_year_over_year_change_matches_calendar_month_when_a_month_is_missing():
    rows = [
        ("2025-05-01", 100.0),
        ("2025-06-01", 101.0),
        ("2025-07-01", 102.0),
        ("2025-08-01", 103.0),
        ("2025-09-01", 104.0),
        # October is absent and must not shift the 12-month comparison.
        ("2025-11-01", 106.0),
        ("2025-12-01", 107.0),
        ("2026-01-01", 108.0),
        ("2026-02-01", 109.0),
        ("2026-03-01", 110.0),
        ("2026-04-01", 111.0),
        ("2026-05-01", 112.0),
        ("2026-06-01", 113.0),
    ]

    assert module.calendar_yoy_pct(rows) == 11.88


def test_core_pce_and_gdpnow_use_official_next_dates_without_invented_times():
    pce = {}
    module.apply_core_pce_release_metadata(pce)
    assert pce["next_release"]["time"] == "2026-08-26"

    text = "5.0% Initial Third-Quarter GDPNow Estimate for 2026:Q3 Updated: July 30, 2026 Next update: August 03, 2026"
    result = module.parse_gdpnow_text(text)
    assert result["next_release"]["time"] == "2026-08-03"


def test_core_cpi_card_matches_bls_one_decimal_release_precision():
    card = module.build_fundamental_card(
        "core_cpi", "核心CPI", "同比 {change_yoy_pct:.1f}%", "detail",
        {"change_yoy_pct": 2.59, "date": "2026-07-14", "source": "BLS", "stale": False},
    )
    assert card["value"] == "同比 2.6%"
