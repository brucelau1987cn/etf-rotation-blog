"""Unit tests for implied lease rate pure math (no network)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from implied_lease_rate import (  # noqa: E402
    carry_within_band,
    compute_implied_lease,
    filter_liquid_curve,
    lease_proxy,
    parse_contract_expiry,
    select_spot_leg,
)


def test_lease_proxy_basic():
    # ln(1.01)/0.25 * 100 ≈ 3.98; r=4.0 → lease ≈ 0.02
    rate = lease_proxy(100.0, 101.0, 0.25, 4.0)
    assert rate is not None
    assert abs(rate - 0.0201) < 0.05


def test_lease_proxy_rejects_bad_inputs():
    assert lease_proxy(0, 101, 0.25, 4) is None
    assert lease_proxy(100, 101, 0, 4) is None
    assert lease_proxy(100, -1, 0.25, 4) is None


def test_parse_contract_expiry():
    assert parse_contract_expiry("Gold Dec 26") == date(2026, 12, 27)
    assert parse_contract_expiry("Silver Sep 26") == date(2026, 9, 27)
    assert parse_contract_expiry(None) is None


def test_filter_liquid_curve_drops_dump():
    today = date(2026, 8, 11)
    rows = [
        {"symbol": "A", "price": 100.0, "expiry": date(2026, 9, 27), "name": "A"},
        {"symbol": "B", "price": 100.5, "expiry": date(2026, 12, 27), "name": "B"},
        {"symbol": "C", "price": 90.0, "expiry": date(2027, 3, 27), "name": "C dump"},
        {"symbol": "D", "price": 101.2, "expiry": date(2027, 6, 27), "name": "D"},
    ]
    good = filter_liquid_curve(rows, today)
    symbols = [r["symbol"] for r in good]
    assert "C" not in symbols
    assert symbols[0] == "A"
    assert "B" in symbols


def test_compute_implied_lease_gold_and_silver():
    today = date(2026, 8, 11)
    usd = {"date": "08/10/2026", "1M": 3.79, "3M": 3.89, "6M": 4.00, "1Y": 4.04, "source": "test"}
    gold_rows = [
        {"symbol": "GCQ26.CMX", "price": 4375.5, "name": "Gold Aug 26", "expiry": date(2026, 8, 27)},
        {"symbol": "GCV26.CMX", "price": 4399.4, "name": "Gold Oct 26", "expiry": date(2026, 10, 27)},
        {"symbol": "GCZ26.CMX", "price": 4432.5, "name": "Gold Dec 26", "expiry": date(2026, 12, 27)},
        {"symbol": "GCG27.CMX", "price": 4467.8, "name": "Gold Feb 27", "expiry": date(2027, 2, 27)},
        {"symbol": "GCQ27.CMX", "price": 4559.0, "name": "Gold Aug 27", "expiry": date(2027, 8, 27)},
    ]
    silver_rows = [
        {"symbol": "SIU26.CMX", "price": 65.06, "name": "Silver Sep 26", "expiry": date(2026, 9, 27)},
        {"symbol": "SIZ26.CMX", "price": 65.775, "name": "Silver Dec 26", "expiry": date(2026, 12, 27)},
        {"symbol": "SIH27.CMX", "price": 66.5, "name": "Silver Mar 27", "expiry": date(2027, 3, 27)},
        {"symbol": "SIN27.CMX", "price": 67.75, "name": "Silver Jul 27", "expiry": date(2027, 7, 27)},
    ]
    out = compute_implied_lease(gold_rows, silver_rows, usd, today=today, fetched_at="2026-08-11T00:00:00Z")
    assert out["ok"] is True
    assert out["method"] == "comex_forward_proxy"
    assert out["gold"]["rate_1m"] is not None
    assert out["gold"]["rate_1y"] is not None
    assert out["silver"]["rate_1m"] is not None
    # Sanity: rates are in a plausible band for proxy (not official lease)
    for metal in ("gold", "silver"):
        for key in ("rate_1m", "rate_3m", "rate_6m", "rate_1y"):
            val = out[metal][key]
            if val is not None:
                assert -20 < val < 20
    assert "不是 LBMA/Kitco" in out["note"] or "proxy" in out["note"]


def test_compute_implied_lease_empty_fail():
    today = date(2026, 8, 11)
    usd = {"date": "08/10/2026", "1M": 3.79, "3M": 3.89, "6M": 4.00, "1Y": 4.04}
    out = compute_implied_lease([], [], usd, today=today)
    assert out["ok"] is False


# ── Spot-leg selection / cross-leg plausibility (2026-10-09 regression) ──
# The thin front month (GCV26, 13 lots) printed a level inconsistent with the
# active month, driving gold 1M to -1.202%. These tests pin the guards.

def _row(symbol, price, expiry, volume=None, market_time=None):
    row = {"symbol": symbol, "price": price, "name": symbol, "expiry": expiry}
    if volume is not None:
        row["volume"] = volume
    if market_time is not None:
        row["market_time"] = market_time
    return row


def test_select_spot_leg_advances_past_thin_front():
    today = date(2026, 10, 9)
    rows = [
        _row("GCV26.CMX", 4176.6, date(2026, 10, 27), volume=13),
        _row("GCZ26.CMX", 4210.0, date(2026, 12, 27), volume=55704),
        _row("GCG27.CMX", 4243.7, date(2027, 2, 27), volume=1709),
    ]
    picked = select_spot_leg(rows, today)
    assert picked["leg"]["symbol"] == "GCZ26.CMX"
    assert picked["rejected"]["symbol"] == "GCV26.CMX"
    assert "front volume 13" in picked["reason"]


def test_select_spot_leg_keeps_liquid_front():
    today = date(2026, 10, 9)
    rows = [
        _row("SIZ26.CMX", 60.645, date(2026, 12, 27), volume=10376),
        _row("SIH27.CMX", 61.385, date(2027, 3, 27), volume=651),
    ]
    picked = select_spot_leg(rows, today)
    assert picked["leg"]["symbol"] == "SIZ26.CMX"
    assert picked["rejected"] is None


def test_select_spot_leg_rejects_stale_front_print():
    today = date(2026, 10, 9)
    fresh = int(datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc).timestamp())
    stale = int(datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    rows = [
        _row("GCV26.CMX", 4176.6, date(2026, 10, 27), volume=500, market_time=stale),
        _row("GCZ26.CMX", 4210.0, date(2026, 12, 27), volume=55704, market_time=fresh),
    ]
    picked = select_spot_leg(rows, today)
    assert picked["leg"]["symbol"] == "GCZ26.CMX"
    assert "old" in picked["reason"]


def test_select_spot_leg_returns_none_when_no_leg_qualifies():
    today = date(2026, 10, 9)
    rows = [
        _row("GCV26.CMX", 4176.6, date(2026, 10, 27), volume=1),
        _row("GCZ26.CMX", 4210.0, date(2026, 12, 27), volume=2),
    ]
    picked = select_spot_leg(rows, today)
    assert picked["leg"] is None
    assert "no acceptable spot leg" in picked["reason"]


def test_carry_within_band_rejects_distorted_pair():
    # 4176.6 → 4225.8 over 0.2163y is ~5.4%/yr, inside band;
    # the same spot against a jumped forward is not.
    assert carry_within_band(4176.6, 4225.8, 0.2163, "gold") is True
    assert carry_within_band(4176.6, 4600.0, 0.2163, "gold") is False
    assert carry_within_band(0, 100, 0.25, "gold") is False
    # silver has a wider band than gold
    assert carry_within_band(60.0, 62.0, 0.25, "silver") is True


def test_thin_front_no_longer_produces_negative_gold_rate():
    """End-to-end: the 2026-10-09 inputs must not reproduce -1.202%."""
    today = date(2026, 10, 9)
    usd = {"date": "10/08/2026", "1M": 4.14, "3M": 4.23, "6M": 4.30, "1Y": 4.44}
    thin_front = [
        _row("GCV26.CMX", 4176.6, date(2026, 10, 27), volume=13),
        _row("GCZ26.CMX", 4225.8, date(2026, 12, 27), volume=55704),
        _row("GCJ27.CMX", 4286.1, date(2027, 4, 27), volume=644),
    ]
    out = compute_implied_lease(thin_front, [], usd, today=today, fetched_at="2026-10-09T00:00:00Z")
    gold = out["gold"]
    assert gold is not None
    # spot leg advanced to the liquid month
    assert gold["front"]["symbol"] == "GCZ26.CMX"
    assert gold["rate_1m"] is not None
    assert gold["rate_1m"] > 0


def test_degraded_metals_reported_when_spot_leg_unusable():
    today = date(2026, 10, 9)
    usd = {"date": "10/08/2026", "1M": 4.14, "3M": 4.23, "6M": 4.30, "1Y": 4.44}
    all_thin = [
        _row("GCV26.CMX", 4176.6, date(2026, 10, 27), volume=1),
        _row("GCZ26.CMX", 4225.8, date(2026, 12, 27), volume=2),
    ]
    out = compute_implied_lease(all_thin, [], usd, today=today)
    assert "gold" in out["degraded_metals"]
