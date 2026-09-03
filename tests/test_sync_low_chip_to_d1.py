"""Tests for D1 sync name extraction from low-chip snapshots."""

from __future__ import annotations

from pathlib import Path
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_low_chip_to_d1 import snapshot_metrics, push  # noqa: E402


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
            "year": [{"symbol": "600269.SH", "name": "赣粤高速", "value": 2.2}],
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
    assert by_code["600269"]["year_profit"] == 2.2
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


def test_snapshot_metrics_carries_shareholder_nature_for_history_api():
    payload = {
        "data_as_of": "2026-08-11",
        "intersection": ["600269.SH"],
        "periods": {"week": [{"symbol": "600269.SH", "name": "赣粤高速", "value": 1.1}], "month": [], "quarter": []},
        "enrichments": {"600269.SH": {
            "shareholder_nature_report_period": "20260630",
            "quality_shareholder": False,
            "quality_shareholder_names": [],
            "institutional_shareholder": True,
            "institutional_shareholder_names": ["长城人寿保险股份有限公司", "香港中央结算有限公司"],
            "shareholder_metrics": {},
        }},
    }
    nature = snapshot_metrics(payload)[0]["shareholder_nature"]
    assert nature["report_period"] == "20260630"
    assert nature["institutional_shareholder"] is True
    assert nature["institutional_shareholder_names"] == ["长城人寿保险股份有限公司", "香港中央结算有限公司"]


def test_push_replaces_the_entire_trade_date_membership():
    payload = {
        "data_as_of": "2026-09-03",
        "intersection": ["000001.SZ"],
        "periods": {
            "week": [{"symbol": "000001.SZ", "name": "平安银行", "value": 1.5}],
            "month": [{"symbol": "000001.SZ", "name": "平安银行", "value": 1.4}],
            "quarter": [{"symbol": "000001.SZ", "name": "平安银行", "value": 1.3}],
        },
        "enrichments": {"000001.SZ": {"shareholder_metrics": {}}},
    }
    with mock.patch("sync_low_chip_to_d1.post_metrics", return_value={"ok": True, "inserted": 1, "total": 1}) as post:
        assert push(payload) == 0
    _, kwargs = post.call_args
    assert kwargs["replace_trade_date"] == "20260903"
