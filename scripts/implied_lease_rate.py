#!/usr/bin/env python3
"""Implied precious-metal lease rate from COMEX futures + USD Treasury yields.

lease_proxy(T) ≈ r_USD(T) − (1/T) * ln(F/S)

This is a market-implied holding-cost proxy, not an LBMA/Kitco official lease quote.
"""

from __future__ import annotations

import json
import math
import re
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.request import Request, urlopen

CN_TZ = timezone(timedelta(hours=8))
MONTH_NAME_TO_NUM = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
# COMEX delivery month codes
GOLD_MONTH_CODES = "GJMQVZ"   # Feb Apr Jun Aug Oct Dec
SILVER_MONTH_CODES = "HKNUZ"  # Mar May Jul Sep Dec
TENORS = (
    ("1M", 30, "1M"),
    ("3M", 91, "3M"),
    ("6M", 182, "6M"),
    ("1Y", 365, "1Y"),
)


def _fetch_text(url: str, timeout: int = 25) -> str:
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; etf-compass-implied-lease/1.0)",
        "Accept": "*/*",
    })
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def lease_proxy(spot: float, forward: float, years: float, usd_rate_pct: float) -> float | None:
    """Annualized implied lease proxy in percent."""
    if spot is None or forward is None or years is None:
        return None
    if spot <= 0 or forward <= 0 or years <= 0:
        return None
    if usd_rate_pct is None:
        return None
    forward_yield = math.log(forward / spot) / years * 100.0
    return usd_rate_pct - forward_yield


def parse_contract_expiry(short_name: str | None, fallback_year: int | None = None) -> date | None:
    """Parse Yahoo shortName like 'Gold Dec 26' → expiry proxy date."""
    if not short_name:
        return None
    m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2})", short_name)
    if not m:
        return None
    mon = MONTH_NAME_TO_NUM[m.group(1)]
    yr = 2000 + int(m.group(2))
    if fallback_year and yr < fallback_year - 1:
        return None
    # COMEX metals last-trade is late in the delivery month; use day 27 as stable proxy.
    day = min(27, monthrange(yr, mon)[1])
    return date(yr, mon, day)


def filter_liquid_curve(rows: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Keep near-term continuous curve; drop stale/illiquid outliers."""
    cleaned = [
        r for r in rows
        if r.get("price") and r.get("expiry") and r["expiry"] > today + timedelta(days=3)
    ]
    cleaned.sort(key=lambda r: r["expiry"])
    if not cleaned:
        return []
    good = [cleaned[0]]
    for r in cleaned[1:]:
        prev = good[-1]["price"]
        # Hard reject sharp dump (stale Yahoo print)
        if r["price"] < prev * 0.97:
            continue
        # Hard reject jump too large for successive COMEX months
        if r["price"] > prev * 1.09:
            continue
        # Beyond ~14 months only keep if still smooth
        if (r["expiry"] - today).days > 430 and r["price"] > good[0]["price"] * 1.20:
            continue
        good.append(r)
    return good


def pick_contract(rows: list[dict[str, Any]], target_days: int, today: date) -> dict[str, Any] | None:
    """Pick liquid contract closest to target tenor; skip ultra-front used as spot."""
    if not rows:
        return None
    # Prefer non-front when available so F/S has positive tenor.
    candidates = rows[1:] if len(rows) > 1 else rows
    best = None
    best_diff = 1e18
    for r in candidates:
        days = (r["expiry"] - today).days
        if days < 10:
            continue
        diff = abs(days - target_days)
        if diff < best_diff:
            best_diff = diff
            best = r
    return best


def interpolate_usd_rate(usd: dict[str, float], years: float) -> float:
    """Piecewise-linear interpolate Treasury curve for actual tenor years."""
    points = [
        (30 / 365.25, usd["1M"]),
        (91 / 365.25, usd["3M"]),
        (182 / 365.25, usd["6M"]),
        (365 / 365.25, usd["1Y"]),
    ]
    if years <= points[0][0]:
        return points[0][1]
    if years >= points[-1][0]:
        return points[-1][1]
    for i in range(1, len(points)):
        t0, r0 = points[i - 1]
        t1, r1 = points[i]
        if t0 <= years <= t1:
            w = (years - t0) / (t1 - t0)
            return r0 + w * (r1 - r0)
    return points[-1][1]


def build_metal_tenors(
    rows: list[dict[str, Any]],
    usd: dict[str, float],
    today: date,
) -> dict[str, Any] | None:
    liquid = filter_liquid_curve(rows, today)
    if not liquid:
        return None
    spot = liquid[0]
    tenors: list[dict[str, Any]] = []
    for label, target_days, usd_key in TENORS:
        fwd = pick_contract(liquid, target_days, today)
        if not fwd:
            continue
        years = (fwd["expiry"] - today).days / 365.25
        # Use actual-tenor interpolated USD rate; keep named bucket for display.
        r_named = usd[usd_key]
        r_usd = interpolate_usd_rate(usd, years)
        rate = lease_proxy(spot["price"], fwd["price"], years, r_usd)
        if rate is None:
            continue
        tenors.append({
            "tenor": label,
            "target_days": target_days,
            "days_to_expiry": (fwd["expiry"] - today).days,
            "years": round(years, 4),
            "rate": round(rate, 3),
            "usd_rate": round(r_usd, 3),
            "usd_bucket": usd_key,
            "usd_bucket_rate": r_named,
            "spot_symbol": spot["symbol"],
            "spot_name": spot.get("name"),
            "spot_price": spot["price"],
            "forward_symbol": fwd["symbol"],
            "forward_name": fwd.get("name"),
            "forward_price": fwd["price"],
            "forward_expiry": fwd["expiry"].isoformat(),
            "fwd_over_spot": round(fwd["price"] / spot["price"], 6),
        })
    if not tenors:
        return None
    by_tenor = {t["tenor"]: t["rate"] for t in tenors}
    return {
        "front": {
            "symbol": spot["symbol"],
            "name": spot.get("name"),
            "price": spot["price"],
            "expiry": spot["expiry"].isoformat(),
        },
        "tenors": tenors,
        "rate_1m": by_tenor.get("1M"),
        "rate_3m": by_tenor.get("3M"),
        "rate_6m": by_tenor.get("6M"),
        "rate_1y": by_tenor.get("1Y"),
        "contracts_used": len(liquid),
    }


def fetch_yahoo_contract(symbol: str) -> dict[str, Any] | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
    raw = json.loads(_fetch_text(url, timeout=20))
    result = (raw.get("chart") or {}).get("result") or []
    if not result:
        return None
    meta = result[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    name = meta.get("shortName") or meta.get("longName") or ""
    expiry = parse_contract_expiry(name)
    if price is None or expiry is None:
        return None
    return {
        "symbol": symbol,
        "price": float(price),
        "name": name,
        "expiry": expiry,
        "exchange": meta.get("fullExchangeName") or "COMEX",
    }


def fetch_comex_curve(metal: str, years: tuple[int, ...] = (26, 27, 28)) -> list[dict[str, Any]]:
    codes = GOLD_MONTH_CODES if metal == "gold" else SILVER_MONTH_CODES
    prefix = "GC" if metal == "gold" else "SI"
    rows: list[dict[str, Any]] = []
    for y in years:
        for code in codes:
            symbol = f"{prefix}{code}{y}.CMX"
            try:
                row = fetch_yahoo_contract(symbol)
                if row:
                    rows.append(row)
            except Exception:
                continue
    return rows


def fetch_treasury_nominal_curve(year: int | None = None) -> dict[str, Any]:
    y = year or datetime.now(CN_TZ).year
    url = (
        "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
        f"daily-treasury-rates.csv/{y}/all?type=daily_treasury_yield_curve"
        f"&field_tdr_date_value={y}&_format=csv"
    )
    csv = _fetch_text(url, timeout=30)
    lines = [ln for ln in csv.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        raise RuntimeError("empty treasury yield CSV")
    headers = [h.strip().strip('"') for h in lines[0].split(",")]
    # Prefer latest non-empty row
    for line in lines[1:]:
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) < len(headers):
            continue
        row = {headers[i]: parts[i] for i in range(len(headers))}
        try:
            return {
                "date": row.get("Date") or row.get("date"),
                "1M": float(row["1 Mo"]),
                "3M": float(row["3 Mo"]),
                "6M": float(row["6 Mo"]),
                "1Y": float(row["1 Yr"]),
                "source": "us_treasury_yield_curve",
            }
        except (KeyError, ValueError):
            continue
    raise RuntimeError("no valid treasury yield row")


def compute_implied_lease(
    gold_rows: list[dict[str, Any]],
    silver_rows: list[dict[str, Any]],
    usd: dict[str, Any],
    today: date | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """Pure computation from already-fetched inputs (unit-testable)."""
    today = today or datetime.now(CN_TZ).date()
    usd_rates = {
        "1M": float(usd["1M"]),
        "3M": float(usd["3M"]),
        "6M": float(usd["6M"]),
        "1Y": float(usd["1Y"]),
    }
    gold = build_metal_tenors(gold_rows, usd_rates, today)
    silver = build_metal_tenors(silver_rows, usd_rates, today)
    ok = bool(gold or silver)
    headline = None
    if gold and gold.get("rate_1m") is not None:
        headline = gold["rate_1m"]
    elif silver and silver.get("rate_1m") is not None:
        headline = silver["rate_1m"]
    return {
        "ok": ok,
        "source": "implied_lease",
        "method": "comex_forward_proxy",
        "label": "隐含租赁利率（期货曲线估算）",
        "formula": "lease ≈ r_USD(T) − (1/T)*ln(F/S)",
        "as_of": today.isoformat(),
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
        "usd_curve": {
            "date": usd.get("date"),
            "source": usd.get("source") or "us_treasury_yield_curve",
            "rates": usd_rates,
        },
        "gold": gold,
        "silver": silver,
        "headline_rate": headline,
        "headline_metal": "gold" if gold and gold.get("rate_1m") is not None else ("silver" if silver else None),
        "note": (
            "基于 COMEX 期货曲线与美国国债收益率估算的隐含租赁/持有成本 proxy；"
            "不是 LBMA/Kitco 官方 lease 报价。含展期、保证金与便利收益噪声，白银波动更大。"
        ),
        "error": None if ok else "no liquid COMEX curve",
    }


def fetch_implied_lease(today: date | None = None) -> dict[str, Any]:
    """Live fetch + compute. Network side effects."""
    today = today or datetime.now(CN_TZ).date()
    try:
        usd = fetch_treasury_nominal_curve(today.year)
    except Exception as e:
        return {
            "ok": False,
            "source": "implied_lease",
            "method": "comex_forward_proxy",
            "error": f"treasury: {e}",
        }
    try:
        gold_rows = fetch_comex_curve("gold")
        silver_rows = fetch_comex_curve("silver")
    except Exception as e:
        return {
            "ok": False,
            "source": "implied_lease",
            "method": "comex_forward_proxy",
            "error": f"comex: {e}",
            "usd_curve": usd,
        }
    return compute_implied_lease(
        gold_rows=gold_rows,
        silver_rows=silver_rows,
        usd=usd,
        today=today,
    )


if __name__ == "__main__":
    out = fetch_implied_lease()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
