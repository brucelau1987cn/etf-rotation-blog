#!/usr/bin/env python3
"""Generate the free, read-only US macro risk snapshot used by /us-macro/.

Primary official sources: Federal Reserve Board, New York Fed, Treasury, BLS and Atlanta Fed.
FRED is retained only as a last-resort fallback for series without a reachable primary source.
Failures never overwrite the last good snapshot.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "public/data/us-macro-dashboard.json"
NY = ZoneInfo("America/New_York")
UA = "Mozilla/5.0 ETF-Compass-Macro/1.0"
POLL_DELAY_SECONDS = 2.0
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BEA_CORE_PCE_URL = "https://www.bea.gov/data/personal-consumption-expenditures-price-index-excluding-food-and-energy"
CENSUS_MARTS_URL = "https://www.census.gov/econ_getzippedfile/?programCode=MARTS"
BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
# (Jin10 no longer used for next-release; all release metadata comes from FRED/官方发布页)
FRED_SAHM_URL = "https://fred.stlouisfed.org/series/SAHMREALTIME"

# Release dates are source metadata, separate from each series' observation month.
# Refresh these from the official BLS/Census release schedules when the source advances.
BLS_RELEASES = {
    "employment": {
        "observation_period": "2026-07",
        "date": "2026-08-07",
        "updated_at": "2026-08-07T08:30:00-04:00",
        "next_release": {"time": "2026-09-04T08:30", "star": None, "consensus": None},
    },
    "cpi": {
        "observation_period": "2026-07",
        "date": "2026-08-12",
        "updated_at": "2026-08-12T08:30:00-04:00",
        "next_release": {"time": "2026-09-11T08:30", "star": None, "consensus": None},
    },
}
REAL_RETAIL_RELEASE = {
    "observation_period": "2026-06",
    "date": "2026-07-16",
    "updated_at": "2026-07-16T08:30:00-04:00",
    "next_release": {"time": "2026-08-14T08:30", "star": None, "consensus": None},
}
CORE_PCE_RELEASE = {
    "next_release": {"time": "2026-08-26", "star": None, "consensus": None},
}

FRED_META = {
    "DGS2": ("日频", "%"), "DGS10": ("日频", "%"), "DGS30": ("日频", "%"),
    "T10Y2Y": ("日频", "%"), "SOFR": ("日频", "%"),
    "WALCL": ("周频", "百万美元"), "WTREGEN": ("周频", "百万美元"),
    "RRPONTSYD": ("日频", "十亿美元"), "CPIAUCSL": ("月频", "指数"),
    "CPILFESL": ("月频", "指数"), "PCEPILFE": ("月频", "指数"),
    "UNRATE": ("月频", "%"), "PAYEMS": ("月频", "千人"),
    "SAHMREALTIME": ("月频", "百分点"), "RRSFS": ("月频", "百万实际美元"),
    "GDPNOW": ("季频滚动更新", "% SAAR"),
}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


class TableRowsExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join(" ".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def numeric(raw: Any) -> float:
    text = str(raw).strip().replace(",", "").replace(" ", "")
    if not text or text.lower() == "null":
        raise ValueError("missing numeric value")
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    return float(text)


def previous_year_month(raw_date: str) -> str:
    observed = date.fromisoformat(raw_date)
    return f"{observed.year - 1:04d}-{observed.month:02d}"


def calendar_yoy_pct(rows: list[tuple[str, float]]) -> float | None:
    if not rows:
        return None
    latest_date, latest_value = rows[-1]
    target = previous_year_month(latest_date)
    values = {raw_date[:7]: value for raw_date, value in rows}
    year_ago = values.get(target)
    return round((latest_value / year_ago - 1) * 100, 2) if year_ago else None


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def request_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def yahoo(symbol: str) -> dict[str, Any]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=3mo&interval=1d"
    result = request_json(url)["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    quote = result["indicators"]["quote"][0]
    closes = []
    for stamp, value in zip(timestamps, quote.get("close", [])):
        if value is None:
            continue
        closes.append((datetime.fromtimestamp(stamp, timezone.utc).astimezone(NY).date().isoformat(), float(value)))
    if len(closes) < 6:
        raise RuntimeError(f"{symbol}: insufficient history")
    date, value = closes[-1]
    prev = closes[-2][1]
    week = closes[-6][1]
    return {
        "value": round(value, 4), "date": date,
        "change": round(value - prev, 4),
        "change_pct": round((value / prev - 1) * 100, 2) if prev else None,
        "change_5d_pct": round((value / week - 1) * 100, 2) if week else None,
        "source": "Yahoo Chart API", "symbol": symbol,
    }


def fred(series: str) -> dict[str, Any]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=10) as response:
        text = response.read().decode("utf-8", "replace")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        raw = row.get(series)
        if raw in (None, "", "."):
            continue
        try:
            rows.append((row["DATE"], float(raw)))
        except (KeyError, ValueError):
            continue
    if len(rows) < 2:
        raise RuntimeError(f"{series}: insufficient observations")
    observation_date, value = rows[-1]
    previous = rows[-2][1]
    frequency, unit = FRED_META.get(series, ("按来源", ""))
    result = {
        "value": round(value, 4), "date": observation_date, "change": round(value - previous, 4),
        "previous": round(previous, 4), "source": "FRED", "series": series,
        "frequency": frequency, "unit": unit, "stale": False,
    }
    if frequency == "月频":
        result["change_yoy_pct"] = calendar_yoy_pct(rows)
    if len(rows) >= 4 and frequency == "月频":
        three_month = rows[-4][1]
        result["change_3m_pct"] = round((value / three_month - 1) * 100, 2) if three_month else None
    return result


def fomc_events(today: date) -> list[dict[str, Any]]:
    req = urllib.request.Request(FOMC_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as response:
        html = response.read().decode("utf-8", "replace")
    parser = TextExtractor()
    parser.feed(html)
    text = " ".join(parser.parts)
    marker = f"{today.year} FOMC Meetings"
    if marker not in text:
        raise RuntimeError(f"FOMC calendar missing {today.year}")
    section = text.split(marker, 1)[1].split(f"{today.year - 1} FOMC Meetings", 1)[0]
    months = {name: number for number, name in enumerate(
        ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    )}
    found: list[dict[str, Any]] = []
    for month, start_raw, end_raw in re.findall(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:-(\d{1,2}))?\*?",
        section,
    ):
        start_day = int(start_raw)
        end_day = int(end_raw or start_raw)
        start = date(today.year, months[month], start_day)
        end = date(today.year, months[month], end_day)
        if end < today:
            continue
        found.append({
            "date": end.isoformat(), "start_date": start.isoformat(), "end_date": end.isoformat(), "time_et": "14:00",
            "title": "FOMC利率决议", "importance": "高", "tone": "warning",
            "symbols": ["SPY", "QQQ", "TLT", "XLF"],
            "discipline": "决议前不追高，保留现金应对波动",
            "source": "Federal Reserve", "source_url": FOMC_URL,
        })
    if not found:
        raise RuntimeError("no future FOMC meeting parsed")
    return found[:4]


def nyfed_sofr() -> dict[str, Any]:
    payload = request_json("https://markets.newyorkfed.org/api/rates/secured/sofr/last/2.json")
    rows = payload.get("refRates", [])
    if len(rows) < 2:
        raise RuntimeError("New York Fed SOFR has insufficient observations")
    current, previous = rows[0], rows[1]
    value, prior = numeric(current["percentRate"]), numeric(previous["percentRate"])
    return {
        "value": value, "date": current["effectiveDate"], "change": round(value - prior, 4), "previous": prior,
        "volume_billions": numeric(current["volumeInBillions"]), "source": "Federal Reserve Bank of New York",
        "source_url": "https://www.newyorkfed.org/markets/reference-rates/sofr",
        "series": "SOFR", "frequency": "日频", "unit": "%", "stale": False,
    }


def nyfed_rrp(today: date) -> dict[str, Any]:
    start = date.fromordinal(today.toordinal() - 20).isoformat()
    url = ("https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json"
           f"?startDate={start}&endDate={today.isoformat()}")
    rows = request_json(url).get("repo", {}).get("operations", [])
    rows = sorted((row for row in rows if row.get("operationDate") and row.get("totalAmtAccepted") is not None),
                  key=lambda row: row["operationDate"], reverse=True)
    if len(rows) < 2:
        raise RuntimeError("New York Fed ON RRP has insufficient observations")
    value = numeric(rows[0]["totalAmtAccepted"]) / 1_000_000_000
    previous = numeric(rows[1]["totalAmtAccepted"]) / 1_000_000_000
    return {
        "value": round(value, 4), "date": rows[0]["operationDate"], "change": round(value - previous, 4),
        "previous": round(previous, 4), "source": "Federal Reserve Bank of New York",
        "source_url": "https://www.newyorkfed.org/markets/desk-operations/reverse-repo",
        "series": "ON RRP accepted amount", "frequency": "日频", "unit": "十亿美元", "stale": False,
    }


def fiscal_tga() -> dict[str, Any]:
    url = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/operating_cash_balance"
           "?filter=account_type:eq:Treasury%20General%20Account%20(TGA)%20Closing%20Balance"
           "&sort=-record_date&page%5Bsize%5D=5")
    rows = request_json(url).get("data", [])
    parsed: list[tuple[str, float]] = []
    for row in rows:
        for field in ("close_today_bal", "open_today_bal"):
            try:
                parsed.append((row["record_date"], numeric(row.get(field))))
                break
            except (KeyError, TypeError, ValueError):
                continue
    if len(parsed) < 2:
        raise RuntimeError("Treasury TGA has insufficient observations")
    observation_date, value = parsed[0]; previous = parsed[1][1]
    return {
        "value": value, "date": observation_date, "change": round(value - previous, 4), "previous": previous,
        "source": "U.S. Department of the Treasury Fiscal Data",
        "source_url": "https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/operating-cash-balance",
        "series": "TGA Closing Balance",
        "frequency": "日频", "unit": "百万美元", "stale": False,
    }


def fed_h41_assets() -> dict[str, Any]:
    url = "https://www.federalreserve.gov/releases/h41/current/"
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=25) as response:
        html = response.read().decode("utf-8", "replace")
    text_parser = TextExtractor(); text_parser.feed(html)
    text = " ".join(text_parser.parts)
    release = re.search(r"Release Date:\s*([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", text)
    if not release:
        raise RuntimeError("H.4.1 release date not found")
    released = datetime.strptime(release.group(1), "%B %d, %Y").date()
    observation = released
    while observation.weekday() != 2:  # H.4.1 balance-sheet observation is Wednesday.
        observation = date.fromordinal(observation.toordinal() - 1)
    table_parser = TableRowsExtractor(); table_parser.feed(html)
    candidates = [row for row in table_parser.rows if row and row[0].strip() == "Total assets" and len(row) == 5]
    if not candidates:
        raise RuntimeError("H.4.1 total-assets row not found")
    row = max(candidates, key=lambda candidate: numeric(candidate[2]))
    value = numeric(row[2]); weekly_change = numeric(row[3]); yearly_change = numeric(row[4])
    return {
        "value": value, "date": observation.isoformat(), "change": weekly_change, "previous": value - weekly_change,
        "change_yoy": yearly_change, "release_date": released.isoformat(), "source": "Board of Governors of the Federal Reserve System",
        "source_url": "https://www.federalreserve.gov/releases/h41/",
        "series": "H.4.1 Total assets", "frequency": "周频", "unit": "百万美元", "stale": False,
    }


def parse_fred_sahm_metadata(text: str) -> dict[str, Any]:
    observation = re.search(r"\b([A-Z][a-z]{2})\s+(\d{4}):\s*([-+]?\d+(?:\.\d+)?)", text)
    updated = re.search(
        r"Updated:\s*([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})\s+(\d{1,2}):(\d{2})\s+(AM|PM)\s+(CST|CDT)",
        text,
    )
    next_release = re.search(r"Next Release Date:\s*([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})", text)
    if not observation or not updated or not next_release:
        raise RuntimeError("FRED Sahm metadata is incomplete")
    month_numbers = {name: number for number, name in enumerate(
        ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )}
    observation_period = f"{int(observation.group(2)):04d}-{month_numbers[observation.group(1)]:02d}"
    hour = int(updated.group(4)) % 12 + (12 if updated.group(6) == "PM" else 0)
    offset = "-05:00" if updated.group(7) == "CDT" else "-06:00"
    updated_date = date(int(updated.group(3)), month_numbers[updated.group(1)], int(updated.group(2)))
    updated_at = f"{updated_date.isoformat()}T{hour:02d}:{int(updated.group(5)):02d}:00{offset}"
    next_date = date(int(next_release.group(3)), month_numbers[next_release.group(1)], int(next_release.group(2)))
    return {
        "value": float(observation.group(3)),
        "observation_period": observation_period,
        "updated_at": updated_at,
        "date": updated_date.isoformat(),
        "next_release": {"time": next_date.isoformat(), "star": None, "consensus": None},
    }


def fred_sahm_metadata() -> dict[str, Any]:
    request = urllib.request.Request(FRED_SAHM_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8", "replace")
    parser = TextExtractor(); parser.feed(html)
    return parse_fred_sahm_metadata(" ".join(parser.parts))


def apply_sahm_metadata(official: dict[str, dict[str, Any]], metadata: dict[str, Any]) -> None:
    item = official.get("sahm") or {}
    item.update({
        "value": metadata["value"],
        "date": metadata["date"],
        "observation_period": metadata["observation_period"],
        "updated_at": metadata["updated_at"],
        "next_release": metadata["next_release"],
        "source": "Federal Reserve Bank of St. Louis",
        "source_url": FRED_SAHM_URL,
        "series": "SAHMREALTIME",
        "frequency": "月频",
        "unit": "百分点",
        "stale": False,
    })
    official["sahm"] = item


def apply_bls_release_metadata(official: dict[str, dict[str, Any]]) -> None:
    # 硬编码仅作 fallback：数据源已提供 observation_period/date 时保留真实值，
    # 否则补发布日元数据（防止硬编码过期覆盖真实观察期）。
    for key, release in (
        ("unemployment", BLS_RELEASES["employment"]),
        ("payrolls", BLS_RELEASES["employment"]),
        ("cpi", BLS_RELEASES["cpi"]),
        ("core_cpi", BLS_RELEASES["cpi"]),
    ):
        item = official.get(key)
        if not item or not isinstance(item, dict):
            continue
        for field, value in release.items():
            if not item.get(field):
                item[field] = value


def apply_real_retail_release_metadata(item: dict[str, Any]) -> None:
    for field, value in REAL_RETAIL_RELEASE.items():
        if not item.get(field):
            item[field] = value


def apply_core_pce_release_metadata(item: dict[str, Any]) -> None:
    for field, value in CORE_PCE_RELEASE.items():
        if not item.get(field):
            item[field] = value


def payroll_change(item: dict[str, Any]) -> float | None:
    try:
        return round(float(item["value"]) - float(item["previous"]), 4)
    except (KeyError, TypeError, ValueError):
        return None


def build_fundamental_card(
    key: str, title: str, formatter: str, detail: str, item: dict[str, Any] | None,
) -> dict[str, Any]:
    if not item:
        return {"key": key, "title": title, "value": "数据待更新", "detail": detail, "as_of": None, "frequency": "—", "source": "FRED", "tone": "missing"}
    render_item = dict(item)
    if key == "payrolls":
        render_item["change"] = payroll_change(item)
        render_item["change_10k"] = round(render_item["change"] / 10, 1) if render_item["change"] is not None else None
    try:
        display_value = formatter.format(**render_item)
    except (KeyError, TypeError, ValueError):
        display_value = f"{item.get('value', '—')} {item.get('unit', '')}".strip()
    return {
        "key": key, "title": title, "value": display_value, "detail": detail,
        "as_of": item.get("date"), "observation_period": item.get("observation_period"),
        "updated_at": item.get("updated_at"), "frequency": item.get("frequency", "按来源"),
        "source": item.get("source", "FRED"), "source_url": item.get("source_url"),
        "tone": "warning" if item.get("stale") else "neutral",
        "stale": bool(item.get("stale")), "next_release": item.get("next_release"),
    }


def bls_fallback(today: date) -> dict[str, dict[str, Any]]:
    series_map = {"unemployment": "LNS14000000", "payrolls": "CES0000000001", "cpi": "CUUR0000SA0", "core_cpi": "CUUR0000SA0L1E"}
    body = json.dumps({"seriesid": list(series_map.values()), "startyear": str(today.year - 1), "endyear": str(today.year)}).encode()
    request = urllib.request.Request("https://api.bls.gov/publicAPI/v2/timeseries/data/", data=body, headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = json.load(response)
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS status {payload.get('status')}")
    reverse = {value: key for key, value in series_map.items()}
    output: dict[str, dict[str, Any]] = {}
    unemployment_rows: list[tuple[str, float]] = []
    for block in payload["Results"]["series"]:
        key = reverse.get(block.get("seriesID"))
        if not key:
            continue
        rows = []
        for item in block.get("data", []):
            if not re.fullmatch(r"M\d{2}", item.get("period", "")):
                continue
            try:
                rows.append((f"{item['year']}-{item['period'][1:]}-01", float(item["value"])))
            except (KeyError, TypeError, ValueError):
                continue
        rows.sort()
        if len(rows) < 2:
            continue
        observation_date, value = rows[-1]; previous = rows[-2][1]
        unit = "%" if key == "unemployment" else "千人" if key == "payrolls" else "指数"
        result = {"value": value, "date": observation_date, "observation_period": observation_date[:7],
                  "change": round(value - previous, 4), "previous": previous,
                  "source": "U.S. Bureau of Labor Statistics", "source_url": "https://www.bls.gov/news.release/empsit.nr0.htm",
                  "series": block["seriesID"], "frequency": "月频", "unit": unit, "stale": False}
        if key == "core_cpi":
            result["source_url"] = "https://www.bls.gov/news.release/cpi.nr0.htm"
        if key in {"cpi", "core_cpi"}:
            result["change_yoy_pct"] = calendar_yoy_pct(rows)
        output[key] = result
        if key == "unemployment":
            unemployment_rows = rows
    if len(unemployment_rows) >= 15:
        averages = [(unemployment_rows[i][0], sum(value for _, value in unemployment_rows[i-2:i+1]) / 3) for i in range(2, len(unemployment_rows))]
        current_date, current_average = averages[-1]; prior_low = min(value for _, value in averages[-13:-1])
        current_sahm = current_average - prior_low
        previous_sahm = averages[-2][1] - min(value for _, value in averages[-14:-2]) if len(averages) >= 14 else None
        output["sahm"] = {"value": round(current_sahm, 2), "date": current_date, "change": round(current_sahm - previous_sahm, 2) if previous_sahm is not None else None,
                          "source": "BLS unemployment · calculated Sahm rule", "source_url": "https://fred.stlouisfed.org/series/SAHMREALTIME",
                          "series": "LNS14000000", "frequency": "月频", "unit": "百分点", "stale": False}
    apply_bls_release_metadata(output)
    return output


def bea_core_pce() -> dict[str, Any]:
    """Read BEA's no-key Core PCE indicator page."""
    request = urllib.request.Request(BEA_CORE_PCE_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=25) as response:
        html = response.read().decode("utf-8", "replace")
    parser = TextExtractor(); parser.feed(html); text = " ".join(parser.parts)
    matches = re.findall(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\s+([+-]?\d+(?:\.\d+)?)%",
        text,
    )
    month_numbers = {name: number for number, name in enumerate(
        ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    )}
    rows = sorted(dict((date(int(year), month_numbers[month], 1).isoformat(), float(raw)) for month, year, raw in matches).items())
    if len(rows) < 2:
        raise RuntimeError("BEA Core PCE page has insufficient observations")
    observation_date, value = rows[-1]; previous = rows[-2][1]
    # Extract release date from "Page last modified on M/D/YY"
    release_match = re.search(r"Page last modified on (\d{1,2})/(\d{1,2})/(\d{2})", text)
    release_date = observation_date  # fallback to observation period
    if release_match:
        m, d, y = int(release_match.group(1)), int(release_match.group(2)), int(release_match.group(3))
        release_date = date(2000 + y, m, d).isoformat()
    result = {
        "value": value, "date": release_date, "change": round(value - previous, 2),
        "previous": previous, "change_yoy_pct": value,
        "observation_period": observation_date[:7], "updated_at": release_date + "T00:00:00-04:00",
        "source": "U.S. Bureau of Economic Analysis", "source_url": BEA_CORE_PCE_URL,
        "series": "Core PCE price index · change from month one year ago",
        "frequency": "月频", "unit": "% YoY", "stale": False,
    }
    apply_core_pce_release_metadata(result)
    return result


def _csv_section(lines: list[str], title: str) -> list[dict[str, str]]:
    try:
        start = lines.index(title) + 1
    except ValueError as exc:
        raise RuntimeError(f"MARTS section missing: {title}") from exc
    end = start
    while end < len(lines) and lines[end].strip():
        end += 1
    return list(csv.DictReader(io.StringIO("\n".join(lines[start:end]))))


def _bls_cpi_sa(start_year: int, end_year: int) -> dict[str, float]:
    body = json.dumps({"seriesid": ["CUSR0000SA0"], "startyear": str(start_year), "endyear": str(end_year)}).encode()
    request = urllib.request.Request(BLS_API_URL, data=body, headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = json.load(response)
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS CPI status {payload.get('status')}")
    output: dict[str, float] = {}
    for item in payload["Results"]["series"][0].get("data", []):
        if re.fullmatch(r"M\d{2}", item.get("period", "")):
            output[f"{item['year']}-{item['period'][1:]}"] = float(item["value"])
    return output


def census_real_retail() -> dict[str, Any]:
    """Reconstruct real retail sales from Census MARTS SA sales and BLS SA CPI-U."""
    request = urllib.request.Request(CENSUS_MARTS_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=45) as response:
        archive = response.read()
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        member = next((name for name in bundle.namelist() if name.endswith("MARTS-mf.csv")), None)
        if not member:
            raise RuntimeError("MARTS archive missing MARTS-mf.csv")
        lines = bundle.read(member).decode("utf-8-sig", "replace").splitlines()
    categories = _csv_section(lines, "CATEGORIES")
    data_types = _csv_section(lines, "DATA TYPES")
    geographies = _csv_section(lines, "GEO LEVELS")
    periods = _csv_section(lines, "TIME PERIODS")
    data_rows = _csv_section(lines, "DATA")
    cat_idx = next(row["cat_idx"] for row in categories if row["cat_code"] == "44X72")
    dt_idx = next(row["dt_idx"] for row in data_types if row["dt_code"] == "SM")
    geo_idx = next(row["geo_idx"] for row in geographies if row["geo_code"] == "US")
    period_names = {row["per_idx"]: row["per_name"] for row in periods}
    sales: list[tuple[str, float]] = []
    for row in data_rows:
        if (row.get("cat_idx"), row.get("dt_idx"), row.get("et_idx"), row.get("geo_idx"), row.get("is_adj")) != (cat_idx, dt_idx, "0", geo_idx, "1"):
            continue
        raw_period = period_names.get(row.get("per_idx", ""))
        if not raw_period or row.get("val") in {None, "", "NA", "Z"}:
            continue
        observed = datetime.strptime(raw_period, "%b-%Y").date().replace(day=1)
        sales.append((observed.isoformat(), numeric(row["val"])))
    sales.sort()
    if len(sales) < 4:
        raise RuntimeError("MARTS has insufficient adjusted retail observations")
    cpi = _bls_cpi_sa(int(sales[-4][0][:4]), int(sales[-1][0][:4]))
    real_rows: list[tuple[str, float, float, float]] = []
    for observation_date, nominal in sales[-6:]:
        cpi_value = cpi.get(observation_date[:7])
        if cpi_value:
            real_rows.append((observation_date, nominal / cpi_value * 100, nominal, cpi_value))
    if len(real_rows) < 4:
        raise RuntimeError("Census/BLS month alignment has insufficient observations")
    observation_date, value, nominal, cpi_value = real_rows[-1]
    previous = real_rows[-2][1]; three_month = real_rows[-4][1]
    result = {
        "value": round(value, 2), "date": observation_date, "observation_period": observation_date[:7],
        "change": round(value - previous, 2), "previous": round(previous, 2),
        "change_3m_pct": round((value / three_month - 1) * 100, 2),
        "nominal_sales_millions": nominal, "cpi_sa": cpi_value,
        "source": "U.S. Census Bureau MARTS ÷ U.S. BLS CPI-U",
        "source_url": "https://www.census.gov/retail/sales.html",
        "series": "Official-data reconstruction of real retail and food-services sales",
        "frequency": "月频", "unit": "百万实际美元（1982-84=100）", "stale": False,
    }
    apply_real_retail_release_metadata(result)
    return result


def treasury_fallback(today: date) -> dict[str, dict[str, Any]]:
    url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
           f"daily-treasury-rates.csv/{today.year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={today.year}&page&_format=csv")
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=25) as response:
        rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8", "replace"))))
    rows = [row for row in rows if row.get("Date")]; rows.sort(key=lambda row: datetime.strptime(row["Date"], "%m/%d/%Y"))
    if len(rows) < 2:
        raise RuntimeError("Treasury yield curve has insufficient rows")
    output = {}
    for key, column in {"yield_2y": "2 Yr", "yield_10y": "10 Yr", "yield_30y": "30 Yr"}.items():
        value = float(rows[-1][column]); previous = float(rows[-2][column])
        output[key] = {"value": value, "date": datetime.strptime(rows[-1]["Date"], "%m/%d/%Y").date().isoformat(), "change": round(value - previous, 4),
                       "previous": previous, "source": "U.S. Department of the Treasury", "series": column, "frequency": "日频", "unit": "%", "stale": False}
    curve = output["yield_10y"]["value"] - output["yield_2y"]["value"]
    previous_curve = output["yield_10y"]["previous"] - output["yield_2y"]["previous"]
    output["curve_10y2y"] = {"value": round(curve, 4), "date": output["yield_10y"]["date"],
                              "change": round(curve - previous_curve, 4), "source": "U.S. Department of the Treasury", "series": "10Y-2Y", "frequency": "日频", "unit": "%", "stale": False}
    return output


def parse_gdpnow_text(text: str) -> dict[str, Any]:
    value_match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*%+\s+\w[\w\s-]{0,40}GDPNow Estimate", text)
    date_match = re.search(r"Updated:\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", text)
    next_match = re.search(r"Next update:\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", text)
    if not value_match or not date_match:
        raise RuntimeError("GDPNow value or update date not found")
    result = {"value": float(value_match.group(1)), "date": datetime.strptime(date_match.group(1), "%B %d, %Y").date().isoformat(), "change": 0,
            "source": "Federal Reserve Bank of Atlanta", "source_url": "https://www.atlantafed.org/cqer/research/gdpnow",
            "series": "GDPNow", "frequency": "季频滚动更新", "unit": "% SAAR", "stale": False}
    if next_match:
        next_date = datetime.strptime(next_match.group(1), "%B %d, %Y").date()
        result["next_release"] = {"time": next_date.isoformat(), "star": None, "consensus": None}
    return result


def gdpnow_fallback() -> dict[str, Any]:
    url = "https://www.atlantafed.org/research-and-data/data/gdpnow"
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=25) as response:
        html = response.read().decode("utf-8", "replace")
    parser = TextExtractor(); parser.feed(html)
    return parse_gdpnow_text(" ".join(parser.parts))


# fund key → FRED series page for next-release date (U.S. government source).
FRED_NEXT_RELEASE_MAP = {
    "unemployment": "UNRATE", "payrolls": "PAYEMS",
    "core_cpi": "CPILFESL", "core_pce": "PCEPILFE", "real_retail": "RRSFS",
}

# FRED blocks browser-ish UAs on /series pages; curl UA works.
FRED_PAGE_UA = "curl/8.5.0"


def fred_next_release(series: str) -> dict | None:
    """Fetch the 'Next Release Date' from the official FRED series page."""
    url = f"https://fred.stlouisfed.org/series/{series}"
    request = urllib.request.Request(url, headers={"User-Agent": FRED_PAGE_UA})
    with urllib.request.urlopen(request, timeout=20) as response:
        html = response.read().decode("utf-8", "replace")
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    match = re.search(r"Next Release Date:\s*([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})", text)
    if not match:
        return None
    month_numbers = {name: number for number, name in enumerate(
        ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )}
    next_date = date(int(match.group(3)), month_numbers[match.group(1)], int(match.group(2)))
    return {"time": next_date.isoformat(), "star": None, "consensus": None}


def attach_next_release(official: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Attach next scheduled release (time/star/consensus) to matching official items.

    Source: official FRED series pages (U.S. government), no Jin10 dependency.
    """
    failures: dict[str, str] = {}
    for key, series in FRED_NEXT_RELEASE_MAP.items():
        item = official.get(key)
        if not item or not isinstance(item, dict):
            continue
        try:
            next_release = fred_next_release(series)
            if next_release:
                item["next_release"] = next_release
        except Exception as exc:
            failures[f"next_release_{key}"] = str(exc)
    return failures


def flag_past_next_release(official: dict[str, dict[str, Any]], today: date) -> dict[str, str]:
    """门禁：官方指标 next_release 已过期即记失败，禁止静默展示过期"下次更新". """
    stale: dict[str, str] = {}
    for key, item in official.items():
        if not isinstance(item, dict):
            continue
        nr = item.get("next_release")
        if isinstance(nr, dict) and nr.get("time"):
            t = str(nr["time"])[:10]
            if t < today.isoformat():
                stale[f"next_release_past_{key}"] = f"{t} 已过期"
    return stale


def level(score: int) -> tuple[str, str]:
    if score >= 7: return "danger", "危险"
    if score >= 5: return "tight", "紧张"
    if score >= 3: return "slightly-tight", "略紧"
    return "loose", "低风险"


def main() -> None:
    failures: dict[str, str] = {}
    market: dict[str, dict[str, Any]] = {}
    official: dict[str, dict[str, Any]] = {}
    now = datetime.now(NY)
    previous_snapshot = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    yahoo_symbols = {
        "vix": "^VIX", "move": "^MOVE", "yield_10y_proxy": "^TNX", "yield_2y_proxy": "2YY=F",
        "spy": "SPY", "rsp": "RSP", "hyg": "HYG", "lqd": "LQD",
        "dollar": "DX-Y.NYB", "oil": "USO", "gold": "GLD", "copper": "COPX",
    }
    for index, (key, symbol) in enumerate(yahoo_symbols.items()):
        try: market[key] = yahoo(symbol)
        except Exception as exc: failures[key] = str(exc)
        if index < len(yahoo_symbols) - 1:
            time.sleep(POLL_DELAY_SECONDS)
    for source_key, loader in {
        "bls": lambda: bls_fallback(now.date()),
        "treasury_yields": lambda: treasury_fallback(now.date()),
        "bea_core_pce": lambda: {"core_pce": bea_core_pce()},
        "census_real_retail": lambda: {"real_retail": census_real_retail()},
    }.items():
        try:
            official.update(loader())
        except Exception as exc:
            failures[source_key] = str(exc)
        time.sleep(POLL_DELAY_SECONDS)
    for key, loader in {
        "sofr": nyfed_sofr,
        "rrp": lambda: nyfed_rrp(now.date()),
        "tga": fiscal_tga,
        "fed_assets": fed_h41_assets,
        "gdpnow": gdpnow_fallback,
    }.items():
        try:
            official[key] = loader()
        except Exception as exc:
            failures[f"{key}_primary"] = str(exc)
        time.sleep(POLL_DELAY_SECONDS)

    fred_available = True
    fred_series = {
        "yield_2y": "DGS2", "yield_10y": "DGS10", "yield_30y": "DGS30", "curve_10y2y": "T10Y2Y", "sofr": "SOFR",
        "fed_assets": "WALCL", "tga": "WTREGEN", "rrp": "RRPONTSYD",
        "cpi": "CPIAUCSL", "core_cpi": "CPILFESL", "core_pce": "PCEPILFE",
        "unemployment": "UNRATE", "payrolls": "PAYEMS", "sahm": "SAHMREALTIME",
        "real_retail": "RRSFS", "gdpnow": "GDPNOW",
    }
    for key, series in fred_series.items():
        if key in official:
            continue
        if not fred_available:
            failures[key] = "FRED本轮不可达；等待下轮更新"
            continue
        try:
            official[key] = fred(series)
        except Exception as exc:
            failures[key] = str(exc)
            if isinstance(exc, (TimeoutError, urllib.error.URLError)):
                fred_available = False
        time.sleep(POLL_DELAY_SECONDS)
    for key, item in previous_snapshot.get("official", {}).items():
        if key not in official:
            official[key] = {**item, "stale": True}
            failures.pop(key, None)
    for key, item in previous_snapshot.get("market", {}).items():
        if key not in market:
            market[key] = {**item, "stale": True}
    try:
        apply_sahm_metadata(official, fred_sahm_metadata())
    except Exception as exc:
        previous_sahm = previous_snapshot.get("official", {}).get("sahm")
        if previous_sahm:
            official["sahm"] = {**previous_sahm, "stale": False}
        else:
            failures["sahm_metadata"] = str(exc)
    apply_bls_release_metadata(official)
    if "real_retail" in official:
        apply_real_retail_release_metadata(official["real_retail"])
    if "core_pce" in official:
        apply_core_pce_release_metadata(official["core_pce"])
    # Attach next scheduled release dates from Jin10 calendar API. Official schedules below win.
    nr_failures = attach_next_release(official)
    failures.update(nr_failures)
    apply_bls_release_metadata(official)
    if "real_retail" in official:
        apply_real_retail_release_metadata(official["real_retail"])
    if "core_pce" in official:
        apply_core_pce_release_metadata(official["core_pce"])

    def business_days_old(raw_date: str | None) -> int:
        if not raw_date:
            return 999
        observed = date.fromisoformat(raw_date)
        if observed >= now.date():
            return 0
        return sum(1 for ordinal in range(observed.toordinal() + 1, now.date().toordinal() + 1)
                   if date.fromordinal(ordinal).weekday() < 5)

    daily_official = {"sofr", "rrp", "tga", "yield_2y", "yield_10y", "yield_30y", "curve_10y2y"}
    for key, item in official.items():
        if item.get("stale"):
            continue
        age = business_days_old(item.get("date"))
        if key in daily_official:
            limit = 3
        elif key == "fed_assets":
            limit = 8
        elif key == "gdpnow":
            limit = 10
        elif item.get("frequency") == "月频":
            limit = 55  # Observation dates are period dates; releases can lag by several weeks.
        else:
            limit = 35
        item["stale"] = age > limit
        item["age_business_days"] = age
    for item in market.values():
        if not item.get("stale"):
            age = business_days_old(item.get("date"))
            item["stale"] = age > 3
            item["age_business_days"] = age

    def val(group: dict[str, dict[str, Any]], key: str) -> float | None:
        item = group.get(key)
        if item and item.get("stale"):
            return None
        return float(item["value"]) if item and math.isfinite(float(item["value"])) else None

    vix = val(market, "vix")
    y10 = val(official, "yield_10y") or val(market, "yield_10y_proxy")
    y2 = val(official, "yield_2y") or val(market, "yield_2y_proxy")
    y30 = val(official, "yield_30y")
    curve = val(official, "curve_10y2y")
    if curve is None and y10 is not None and y2 is not None:
        curve = round(y10 - y2, 4)
    curve_10y30y = y30 - y10 if y10 is not None and y30 is not None else None
    score = 0
    notes = []
    if vix is not None:
        score += 4 if vix >= 30 else 3 if vix >= 25 else 2 if vix >= 20 else 0
        notes.append(f"VIX {vix:.1f}")
    if y10 is not None:
        score += 2 if y10 >= 5 else 1 if y10 >= 4.5 else 0
        notes.append(f"10Y {y10:.2f}%")
    if curve is not None and curve < 0:
        score += 1
    hyg = market.get("hyg", {}).get("change_5d_pct")
    lqd = market.get("lqd", {}).get("change_5d_pct")
    credit_relative = round(float(hyg) - float(lqd), 2) if hyg is not None and lqd is not None else None
    if credit_relative is not None and credit_relative <= -1.5:
        score += 2
    spy = val(market, "spy"); rsp = val(market, "rsp")
    breadth_relative = None
    if market.get("spy", {}).get("change_5d_pct") is not None and market.get("rsp", {}).get("change_5d_pct") is not None:
        breadth_relative = round(float(market["rsp"]["change_5d_pct"]) - float(market["spy"]["change_5d_pct"]), 2)
    risk_key, risk_label = level(score)
    sofr_value = val(official, "sofr")
    fed_assets_value = val(official, "fed_assets")
    tga_value = val(official, "tga")
    rrp_value = val(official, "rrp")
    liquidity_proxy = (fed_assets_value - tga_value - rrp_value * 1000
                       if fed_assets_value is not None and tga_value is not None and rrp_value is not None else None)

    dimensions = [
        {
            "key": "volatility", "title": "波动率", "state": "危险" if vix and vix >= 30 else "升温" if vix and vix >= 20 else "稳定",
            "tone": "danger" if vix and vix >= 30 else "warning" if vix and vix >= 20 else "positive",
            "headline": f"VIX {vix:.1f}" if vix is not None else "VIX N/A",
            "detail": f"日变动 {market.get('vix', {}).get('change_pct', '—')}% · 股票波动风险",
            "impact": "高Beta ETF减仓优先" if vix and vix >= 25 else "不额外限制正常持仓",
            "symbols": ["SPY", "QQQ", "IWM", "ARKK"], "as_of": market.get("vix", {}).get("date"),
        },
        {
            "key": "rates", "title": "利率与曲线", "state": "承压" if y10 and y10 >= 4.5 else "中性",
            "tone": "warning" if y10 and y10 >= 4.5 else "neutral",
            "headline": f"10Y {y10:.2f}%" if y10 is not None else "10Y N/A",
            "detail": f"2Y {y2:.2f}% · 30Y {y30:.2f}% · 2s10s {curve:+.2f}% · 10s30s {curve_10y30y:+.2f}%" if y2 is not None and y30 is not None and curve is not None and curve_10y30y is not None else "收益率数据不完整",
            "impact": "成长ETF禁止追高" if y10 and y10 >= 4.5 else "估值压力暂不升级",
            "symbols": ["QQQ", "XLK", "SMH", "TLT"], "as_of": (official.get("yield_10y") or market.get("yield_10y_proxy") or {}).get("date"),
        },
        {
            "key": "liquidity", "title": "资金与流动性",
            "state": "数据待更新" if liquidity_proxy is None or sofr_value is None else "观察",
            "tone": "missing" if liquidity_proxy is None or sofr_value is None else "neutral",
            "headline": f"SOFR {sofr_value:.2f}%" if sofr_value is not None else "官方数据待更新",
            "detail": (f"净流动性代理 {liquidity_proxy / 1_000_000:.2f}万亿美元 · Fed周频 / TGA与ON RRP日频"
                       if liquidity_proxy is not None else "纽约联储 / 美联储H.4.1 / 财政部数据不完整"),
            "impact": "仅作风险预算闸门，不替代ETF价格触发",
            "symbols": ["SPY", "QQQ", "TLT"], "as_of": official.get("sofr", {}).get("date"),
        },
        {
            "key": "credit", "title": "信用与广度", "state": "收缩" if credit_relative is not None and credit_relative < -1 else "稳定",
            "tone": "warning" if credit_relative is not None and credit_relative < -1 else "positive",
            "headline": f"HYG/LQD 5日 {credit_relative:+.2f}pp" if credit_relative is not None else "HYG/LQD N/A",
            "detail": f"RSP相对SPY 5日 {breadth_relative:+.2f}pp" if breadth_relative is not None else "等权广度数据不完整",
            "impact": "小盘与高Beta降低仓位" if credit_relative is not None and credit_relative < -1 else "信用风险未明显扩散",
            "symbols": ["IWM", "XBI", "ARKK", "KRE"], "as_of": market.get("hyg", {}).get("date"),
        },
    ]

    def fundamental(key: str, title: str, formatter: str, detail: str) -> dict[str, Any]:
        return build_fundamental_card(key, title, formatter, detail, official.get(key))

    def liquidity_card(key: str, title: str, scale: float, suffix: str, detail: str) -> dict[str, Any]:
        card = fundamental(key, title, "{value}", detail)
        item = official.get(key)
        if item and item.get("value") is not None:
            card["value"] = f"{float(item['value']) / scale:.2f}{suffix}"
        return card

    liquidity_components = [
        liquidity_card("sofr", "SOFR", 1, "%", "纽约联储担保隔夜融资利率（Secured Overnight Financing Rate）：美国银行与机构以国债为抵押的隔夜拆借利率，是美联储体系内最重要的短期资金价格基准，影响房贷、企业融资与货币市场定价。"),
        liquidity_card("fed_assets", "Fed总资产", 1_000_000, "万亿美元", "美联储H.4.1周度资产负债表"),
        liquidity_card("tga", "财政部TGA", 100, "亿美元", "财政部TGA（Treasury General Account）：美国财政部在美联储的现金账户余额。余额上升表示财政部从市场回笼资金、收紧流动性；下降则表示释放资金、增加市场流动性。"),
        liquidity_card("rrp", "ON RRP", 0.1, "亿美元", "ON RRP（隔夜逆回购）：货币基金等机构将闲置资金存入美联储隔夜逆回购工具的实际使用量。使用量越高说明市场闲置资金越多、流动性越充裕；使用量骤降通常是资金开始流出避险工具、投向风险资产的信号。"),
    ]

    fundamentals = [
        fundamental("sahm", "萨姆规则", "{value:.2f}pp", "≥0.50pp才触发衰退信号"),
        fundamental("unemployment", "失业率", "{value:.1f}%", "就业温度计，不用单月波动机械交易"),
        fundamental("payrolls", "非农就业", "{change_10k:+.1f}万人", "较前月就业人数变化"),
        fundamental("core_cpi", "核心CPI", "同比 {change_yoy_pct:.1f}%", "剔除食品与能源后的价格趋势"),
        fundamental("core_pce", "核心PCE", "同比 {change_yoy_pct:.2f}%", "美联储重点通胀口径"),
        fundamental("real_retail", "实际零售销售", "3月 {change_3m_pct:+.2f}%", "消费动能的三个月变化"),
        fundamental("gdpnow", "GDPNow", "{value:.2f}%", "当前季度实际GDP年化即时估计"),
    ]

    impacts = []
    if y10 is not None:
        impacts.append({"driver": "10Y收益率偏高" if y10 >= 4.5 else "利率压力温和", "benefit": ["XLF" if y10 >= 4.5 else "QQQ"], "pressure": ["QQQ", "ARKK", "TLT"] if y10 >= 4.5 else ["UUP"], "discipline": "成长方向只等伏击位，不追高" if y10 >= 4.5 else "不改变正常伏击纪律"})
    if credit_relative is not None:
        impacts.append({"driver": "信用风险收缩" if credit_relative < -1 else "信用环境稳定", "benefit": ["TLT", "GLD"] if credit_relative < -1 else ["IWM", "XBI"], "pressure": ["IWM", "ARKK", "KRE"] if credit_relative < -1 else [], "discipline": "高Beta仓位减半" if credit_relative < -1 else "维持正常仓位上限"})
    if breadth_relative is not None:
        impacts.append({"driver": "等权落后" if breadth_relative < -1 else "上涨扩散", "benefit": ["SPY", "QQQ"] if breadth_relative < -1 else ["RSP", "IWM"], "pressure": ["RSP", "IWM"] if breadth_relative < -1 else [], "discipline": "警惕指数强、内部弱" if breadth_relative < -1 else "轮动参与度改善"})

    now = datetime.now(NY)
    try:
        events = fomc_events(now.date())
    except Exception as exc:
        failures["fomc_calendar"] = str(exc)
        previous = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
        events = [event for event in previous.get("events", []) if event.get("end_date", "") >= now.date().isoformat()]
    failures.update(flag_past_next_release(official, now.date()))
    payload = {
        "version": 2, "generated_at": now.isoformat(), "timezone": "America/New_York",
        "risk": {"key": risk_key, "label": risk_label, "score": score, "headline": " · ".join(notes[:2]) or "核心数据暂缺", "equity_constraint": "暂停新增伏击" if score >= 7 else "新增伏击减半" if score >= 5 else "禁止追高、按关键位执行" if score >= 3 else "允许正常伏击与持仓"},
        "dimensions": dimensions, "liquidity_components": liquidity_components, "fundamentals": fundamentals, "impacts": impacts[:3],
        "events": events,
        "market": market, "official": official,
        "data_quality": {"failed": len(failures), "failures": failures, "note": "免费公开源；卡片显示各来源的发布/更新日期，观察期单独保存。"},
        "sources": ["Yahoo Chart API", "Federal Reserve Bank of New York", "Board of Governors H.4.1", "U.S. Treasury Fiscal Data", "U.S. Department of the Treasury", "U.S. Bureau of Labor Statistics", "Federal Reserve Bank of Atlanta GDPNow", "Federal Reserve FOMC Calendar", "FRED (fallback only)"],
    }
    if len(dimensions) < 4 or (not market and not official):
        raise RuntimeError("insufficient macro data")
    atomic_write(OUTPUT, payload)
    print(json.dumps({"risk": risk_label, "score": score, "market": len(market), "official": len(official), "failed": len(failures)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
