"""Tests for D1 sync name extraction from low-chip snapshots."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_low_chip_to_d1 import snapshot_metrics  # noqa: E402


def test_snapshot_metrics_name_from_periods_not_enrichments():
    payload = {
        "data_as_of": "2026-08-11",
        "intersection": ["600269.SH", "002992.SZ"],
        "periods": {
            "week": [
                {"symbol": "600269.SH", "name": "赣粤高速", "value": 1.1, "price": 3.83, "change_percent": -1.2},
            ],
            "month": [
                {"symbol": "002992.SZ", "name": "宝明科技", "value": 0.5, "price": 32.15, "change_percent": 0.1},
            ],
            "quarter": [],
        },
        "enrichments": {
            # name intentionally missing — real snapshots store names only on period rows
            "600269.SH": {
                "industry": "交通运输",
                "shareholder_metrics": {"shareholder_count": 49523, "price": 3.83},
            },
            "002992.SZ": {
                "industry": "电子",
                "shareholder_metrics": {"shareholder_count": 12228, "price": 32.15},
            },
        },
    }
    rows = snapshot_metrics(payload)
    by_code = {r["stock_code"]: r for r in rows}
    assert by_code["600269"]["stock_name"] == "赣粤高速"
    assert by_code["002992"]["stock_name"] == "宝明科技"
    assert by_code["600269"]["week_profit"] == 1.1
    assert by_code["002992"]["month_profit"] == 0.5
    assert {row["trade_date"] for row in rows} == {"20260811"}


def test_snapshot_metrics_prefers_period_over_enrichment_name():
    payload = {
        "data_as_of": "2026-08-03",
        "intersection": ["002993.SZ"],
        "periods": {
            "week": [{"symbol": "002993.SZ", "name": "奥海科技", "value": 0.7, "price": 33.87}],
            "month": [],
            "quarter": [],
        },
        "enrichments": {
            "002993.SZ": {
                "name": "旧名",
                "shareholder_metrics": {"shareholder_count": 1, "price": 33.87},
            }
        },
    }
    rows = snapshot_metrics(payload)
    assert rows[0]["stock_name"] == "奥海科技"
