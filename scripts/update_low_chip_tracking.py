#!/usr/bin/env python3
"""Track daily price + profit-ratio history for every stock that ever entered the low-chip screen.

Data source:
- Daily prices (qfq): Tencent fqkline, THS, then batched BaoStock
- Daily 收盘获利盘 ratio: iWenCai per-stock query `收盘获利[YYYYMMDD]` (historical dates supported)

Output: public/data/low-chip-tracking.json
  {
    "schema_version": "low-chip-tracking-v1",
    "generated_at": "...",
    "stocks": {
      "600363.SH": {
        "name": "联创光电",
        "first_seen": "2026-08-05",   // first date the stock appeared in a low-chip snapshot
        "last_seen": "2026-08-07",
        "industry": "...",
        "entry_baseline_status": "available",  // missing if no first_seen close
        // entry_halt may record dated suspension evidence for first_seen
        "daily": [
          {"date": "2026-08-05", "close": 24.64, "change_pct": -10.0, "profit_ratio": 12.2},
          ...
        ],
        "latest": {"week": ..., "month": ..., "quarter": ...}  // current 周/月/季线获利 (if available)
      }
    }
  }
"""
from __future__ import annotations

import argparse
import datetime
import http.client
import copy
import hashlib
import json
import math
import os
import random
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from cf_baostock_client import CFBaoStockClient, CFBaoStockError
except ModuleNotFoundError:
    from scripts.cf_baostock_client import CFBaoStockClient, CFBaoStockError

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/low-chip-tracking.json"
HISTORY_DIR = ROOT / "public/data/low-chip-history"
MAX_TRACK_BARS = 20  # 加入后统计窗口：最多 20 个交易日；不足按实际天数
MAX_STORED_BARS = MAX_TRACK_BARS + 1  # 加入日基准 + 加入后的 20 个交易日

# 腾讯 fqkline 双 host：`web.ifzq.gtimg.cn` 会间歇性返 HTTP 501（WAF），
# `ifzq.gtimg.cn` 同一时刻仍正常。单 host 硬编码会把间歇 WAF 变成整步失败
# （追踪 K 线全池回退 BaoStock，慢一个量级）。
TENCENT_KLINE_HOSTS = ("ifzq.gtimg.cn", "web.ifzq.gtimg.cn")

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def chip_list_profit_ratios(symbol: str, start: str | None = None) -> dict[str, float | None]:
    """拉同花顺 chip_list 筹码曲线，复算逐日获利盘（无鉴权、无每日配额）。

    返回 {date(YYYYMMDD): profit_ratio}；失败/无数据返回空 dict，调用方 fallback iWenCai。
    复算公式（skill 证实与官方 closing_profit 吻合 0.002pp）：
        sum(jeton where price <= close) / sum(jeton)

    `start`（YYYY-MM-DD）把查询窗口锚定到追踪起点。同花顺的 chip_list 只返回
    **窗口内最近 40 个交易日**：固定 90 天窗口时，追踪池里 2026-08-26 之前
    入池的标的拿不到早期 bar，只能逐 (标的, 日期) 回退 iWenCai（2026-10-28
    实测需要 300+ 次调用 ≈ 30 分钟，直接撞穿 1800s 超时）。按 first_seen 锚定
    窗口后，同一只标的的早期 bar 一次请求即可覆盖。

    窗口超过 40 个交易日时，同花顺会从窗口尾部截断，所以 start 传早于 40 个
    交易日的值时同样只能拿到最近 40 天——追踪窗口本身就是 21 根 bar，锚定
    first_seen 后始终落在覆盖范围内。
    """
    code = symbol.split(".")[0]
    market = "17" if symbol.endswith(".SH") else "33"
    ms_now = int(time.time() * 1000)
    if start:
        try:
            ms_start = int(datetime.datetime.fromisoformat(start).timestamp() * 1000)
        except ValueError:
            ms_start = ms_now - 90 * 86400 * 1000
    else:
        ms_start = ms_now - 90 * 86400 * 1000  # 90 天窗口，覆盖 20 交易日追踪窗口
    url = (
        f"https://dq.10jqka.com.cn/fuyao/chip_shape_stock_selection/stock/v1/chip_list"
        f"?chip_type=all&stock_code={code}&stock_market={market}"
        f"&start_date={ms_start}&end_date={ms_now}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as response:
            payload = json.loads(response.read())
    except Exception:
        return {}
    date_map = (payload.get("data") or {}).get("list") or {}
    result: dict[str, float | None] = {}
    for date, item in date_map.items():
        if not isinstance(item, dict):
            continue
        close = (item.get("summary") or {}).get("close_price")
        curve = (item.get("curve_data") or {}).get("list") or []
        if close is None or not curve:
            continue
        total = 0.0
        below = 0.0
        for x in curve:
            try:
                jeton = float(x.get("jeton") or 0)
            except (TypeError, ValueError):
                jeton = 0.0
            total += jeton
            try:
                price = float(x.get("price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            if price <= close:
                below += jeton
        result[date] = round(below / total * 100, 2) if total > 0 else None
    return result


def load_history_dates() -> dict[str, list[str]]:
    """Map symbol -> sorted list of dates it appeared in low-chip snapshots.

    阈值变体快照（`YYYY-MM-DD.threshold-X.json`）同样计入：同日阈值收窄时
    原快照会被 canonical 全集替换，旧阈值成员只存在于变体文件里。只 glob
    canonical 会让这些成员从追踪池"失联"——它们留在 stocks 里但永远不会
    再被遍历回填，卡在加入日一根 bar（2026-10-21 实测 002705.SZ 只有
    2026-09-03 一根，K 线回填永远轮不到它）。变体的日期取其文件名里的日期。
    """
    seen: dict[str, list[str]] = {}
    paths = sorted(HISTORY_DIR.glob("????-??-??.json")) + sorted(
        HISTORY_DIR.glob("????-??-??.threshold-*.json")
    )
    for path in paths:
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        day = path.stem[:10]
        try:
            datetime.date.fromisoformat(day)
        except ValueError:
            continue
        for code in snap.get("intersection") or []:
            dates = seen.setdefault(code, [])
            if day not in dates:
                dates.append(day)
    for dates in seen.values():
        dates.sort()
    return seen


def _valid_tencent_row(row: object) -> bool:
    if not isinstance(row, (list, tuple)) or len(row) < 6:
        return False
    try:
        if datetime.date.fromisoformat(row[0]).isoformat() != row[0]:
            return False
        if isinstance(row[2], bool) or isinstance(row[5], bool):
            return False
        close = float(row[2])
        volume = float(row[5])
        return math.isfinite(close) and close > 0 and math.isfinite(volume) and volume > 0
    except (TypeError, ValueError, OverflowError):
        return False


def tencent_daily(
    symbol: str,
    start: str,
    end: str,
    *,
    opener=None,
    sleeper=time.sleep,
    stored: list[dict] | None = None,
    on_target_trade=None,
) -> list[dict]:
    """Daily qfq bars from Tencent with bounded transient-error retries.

    Pitfall (2026-08-11): Tencent fqkline multi-day range queries with a concrete
    end=YYYY-MM-DD often drop the latest session bar (e.g. start=08-05 end=08-11
    returns only through 08-10; even end=08-12 can still miss 08-11 for some
    symbols). Reliable ways to include today:
      - leave end empty: day,{start},,{count},qfq
      - or request a larger open-ended window and clip client-side
    We query with empty end, then clip to the requested [start, end] window.

    Tencent can also return HTTP 200 with a non-zero business code and
    ``data: ""`` during backend/MySQL failures. Retry those responses instead of
    treating the string as the normal data object.
    """
    code = symbol.split(".")[0]
    ex = "sh" if symbol.endswith(".SH") else "sz"
    open_request = opener or urllib.request.urlopen
    retry_delays = (1.0, 2.0, 5.0)
    last_error = "unknown response"
    best_rows: dict[str, float] = {}
    in_window: set[str] = set()
    stored_closes = {row["date"]: float(row["close"]) for row in stored or []}
    for host in TENCENT_KLINE_HOSTS:
        # With usable but stale history, probe the mirror once, then let the
        # independent providers try. Preserve the normal retry budget otherwise.
        attempts = 1 if in_window else len(retry_delays) + 1
        for attempt in range(attempts):
            try:
                # Empty end is required for latest-bar inclusion; count caps payload size.
                # 640 keeps full history since first_seen for any reasonable tracking window.
                r_nonce = f"{random.random():.6f}"
                url = (
                    f"https://{host}/appstock/app/fqkline/get?"
                    f"r={r_nonce}&param={ex}{code},day,{start},,640,qfq"
                )
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with open_request(req, timeout=20) as response:
                    raw_bytes = response.read()
                    if b"=" in raw_bytes and raw_bytes.strip().startswith((b"v_", b"kline_")):
                        raw_bytes = raw_bytes.split(b"=", 1)[1]
                    decoded = json.loads(raw_bytes)
                business_code = decoded.get("code") if isinstance(decoded, dict) else None
                message = decoded.get("msg") if isinstance(decoded, dict) else "invalid JSON root"
                raw_data = decoded.get("data") if isinstance(decoded, dict) else None
                symbol_data = raw_data.get(f"{ex}{code}") if isinstance(raw_data, dict) else None
                candidate_bars = None
                if isinstance(symbol_data, dict):
                    candidate_bars = symbol_data.get("qfqday")
                rows: dict[str, float] = {}
                conflicting_dates: set[str] = set()
                if isinstance(candidate_bars, list):
                    for row in candidate_bars:
                        if _valid_tencent_row(row) and (not end or row[0] <= end):
                            day, close = row[0], float(row[2])
                            if day in rows and rows[day] != close:
                                conflicting_dates.add(day)
                            rows[day] = close
                dates = {day for day in rows if not start or day >= start}
                success = type(business_code) is int and business_code == 0
                if success and end and (not start or end >= start) and on_target_trade is not None:
                    # Unadjusted rows can disprove a halt but cannot supply QFQ prices.
                    raw_day = symbol_data.get("day") if isinstance(symbol_data, dict) else None
                    if end in dates or (isinstance(raw_day, list) and any(
                        _valid_tencent_row(row) and row[0] == end for row in raw_day
                    )):
                        on_target_trade()
                if success and dates:
                    if conflicting_dates:
                        last_error = f"{host}: duplicate Tencent qfq dates disagree"
                    elif stored_closes and not _same_qfq_basis(stored_closes, rows):
                        last_error = f"{host}: Tencent qfq prices incompatible with stored bars"
                    elif not best_rows or _same_qfq_basis(best_rows, rows):
                        best_rows.update(rows)
                        in_window.update(dates)
                    elif best_rows.keys() <= rows.keys():
                        # A single self-contained response can replace an
                        # unwritten history; the stored basis was checked above.
                        best_rows = rows
                        in_window = dates
                    else:
                        last_error = f"{host}: Tencent qfq overlap missing or closes disagree"
                    break
                last_error = (
                    f"{host}: code={business_code}, msg={message!s}, data_type={type(raw_data).__name__}, "
                    f"bars_type={type(candidate_bars).__name__}"
                )
            except (TimeoutError, urllib.error.URLError, json.JSONDecodeError, http.client.HTTPException, OSError) as exc:
                last_error = f"{host}: {type(exc).__name__}: {exc}"
                # 网关/WAF 直接拒绝（非瞬时故障，重试无意义且每次 8s）：立即换 host，
                # 两个 host 都被拒再让出给 BaoStock 备用源，避免 200+ 只标的逐个空转。
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403, 405, 501):
                    break
            if attempt + 1 < attempts:
                sleeper(retry_delays[attempt])
        if in_window and (not end or end in in_window or len(in_window) >= MAX_STORED_BARS):
            break
    if not in_window:
        raise RuntimeError(f"Tencent fqkline unavailable for {symbol} on all hosts: {last_error}")

    bars = []
    prev_close = None
    for date, close in sorted(best_rows.items()):
        if date in in_window:
            chg = round((close - prev_close) / prev_close * 100, 2) if prev_close else None
            bars.append({"date": date, "close": close, "change_pct": chg})
        prev_close = close
    return bars


def confirmed_suspensions(target_day: str) -> dict[str, dict]:
    """Read dated full-session suspensions; missing prices alone prove nothing."""
    day = datetime.date.fromisoformat(target_day).isoformat()
    query = urllib.parse.urlencode({
        "reportName": "RPT_CUSTOM_SUSPEND_DATA_INTERFACE", "columns": "ALL",
        "filter": f'(MARKET="全部")(DATETIME=\'{day}\')',
        "sortColumns": "SUSPEND_START_DATE", "sortTypes": "-1",
        "pageSize": 500, "pageNumber": 1, "source": "WEB", "client": "WEB",
    })
    request = urllib.request.Request(
        f"https://datacenter-web.eastmoney.com/api/data/v1/get?{query}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            # Rows omit DATETIME in the provider's real schema. Bind this body
            # to the exact target-day request and its final HTTPS response URL.
            # A bare archived body carries no such binding on its own.
            final_url = response.geturl()
            expected, actual = urllib.parse.urlsplit(request.full_url), urllib.parse.urlsplit(final_url)
            if (actual.scheme != "https" or actual.netloc != expected.netloc
                    or actual.path != expected.path or actual.fragment
                    or urllib.parse.parse_qs(actual.query, keep_blank_values=True)
                    != urllib.parse.parse_qs(expected.query, keep_blank_values=True)):
                raise RuntimeError("suspension response target-day binding failed")
            payload = json.load(response)
    except (AttributeError, OSError, ValueError, http.client.HTTPException) as exc:
        raise RuntimeError(f"suspension report unavailable or binding missing: {type(exc).__name__}") from exc
    report = payload.get("result") if isinstance(payload, dict) and payload.get("success") is True else None
    if not isinstance(report, dict):
        raise RuntimeError("suspension report missing or incomplete")
    rows = report.get("data")
    if not isinstance(rows, list) or report.get("pages") != 1 or report.get("count") != len(rows):
        raise RuntimeError("suspension report missing or incomplete")
    market_open = datetime.datetime.fromisoformat(f"{day}T09:30:00")
    market_close = datetime.datetime.fromisoformat(f"{day}T15:00:00")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not {
            "SECUCODE", "SUSPEND_START_TIME", "SUSPEND_END_TIME", "PREDICT_RESUME_DATE", "SUSPEND_EXPIRE", "SUSPEND_REASON",
        }.issubset(row):
            continue
        symbol, reason = row["SECUCODE"], row["SUSPEND_REASON"]
        if not isinstance(symbol, str) or not isinstance(reason, str) or not reason.strip():
            continue
        try:
            if "DATETIME" in row and datetime.datetime.fromisoformat(row["DATETIME"]).date().isoformat() != day:
                continue
            start = datetime.datetime.fromisoformat(row["SUSPEND_START_TIME"])
            end = datetime.datetime.fromisoformat(row["SUSPEND_END_TIME"]) if row["SUSPEND_END_TIME"] is not None else None
            resume = datetime.datetime.fromisoformat(row["PREDICT_RESUME_DATE"]) if row["PREDICT_RESUME_DATE"] is not None else None
            if start > market_open or (end is not None and end < market_close) or (resume is not None and resume.date() <= market_open.date()):
                continue
        except (TypeError, ValueError):
            continue
        if end is None and row["SUSPEND_EXPIRE"] != "连续停牌":
            continue
        result[symbol] = {"status": "halted", "target_day": day, "reason": reason.strip(),
                          "source": "eastmoney_suspend_report"}
    return result


def ths_daily(
    symbol: str, start: str, end: str, *, now: datetime.datetime | None = None, on_target_trade=None,
) -> list[dict]:
    """THS 01 (QFQ) historical bars plus a verified same-session close."""
    code = symbol.split(".")[0]
    lookup_start = datetime.date.fromisoformat(start) - datetime.timedelta(days=30)
    target_date = datetime.date.fromisoformat(end)
    rows: dict[str, float] = {}
    conflicting_dates: set[str] = set()

    def fetch(period: str) -> dict:
        request = urllib.request.Request(
            f"https://d.10jqka.com.cn/v6/line/hs_{code}/01/{period}.js",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://stockpage.10jqka.com.cn/"},
        )
        with urllib.request.urlopen(request, timeout=6) as response:
            raw = response.read().decode().strip().rstrip(";")
        prefix = f"quotebridge_v6_line_hs_{code}_01_{period}("
        if not raw.startswith(prefix) or not raw.endswith(")"):
            raise ValueError("unexpected THS JSONP identity")
        payload = json.loads(raw[len(prefix):-1])
        if not isinstance(payload, dict):
            raise ValueError("unexpected THS JSONP payload")
        return payload

    def traded_close(price, volume) -> float | None:
        try:
            if all(not isinstance(v, bool) and math.isfinite(float(v)) and float(v) > 0
                   for v in (price, volume)):
                return float(price)
        except (TypeError, ValueError, OverflowError):
            pass
        return None

    def absorb(ymd: str, price, volume) -> None:
        try:
            if len(ymd) != 8 or not ymd.isascii() or not ymd.isdigit():
                return
            bar_date = datetime.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
            if bar_date.weekday() >= 5:
                return
            day = bar_date.isoformat()
            close = traded_close(price, volume)
            if day > end or close is None or day in conflicting_dates:
                return
            if day in rows and rows[day] != close:
                # Quarantine the date for the entire invocation, including any
                # later duplicate that happens to match one of these closes.
                conflicting_dates.add(day)
                del rows[day]
            else:
                rows[day] = close
        except (TypeError, ValueError, OverflowError):
            return

    annual_error: Exception | None = None
    target_trade_reported = False

    def report_target_trade() -> None:
        nonlocal target_trade_reported
        if not target_trade_reported and on_target_trade is not None and start <= end:
            on_target_trade()
        target_trade_reported = True

    try:
        for year in range(lookup_start.year, int(end[:4]) + 1):
            try:
                data = fetch(str(year)).get("data")
                if not isinstance(data, str):
                    raise ValueError("missing THS history rows")
                for line in data.split(";"):
                    fields = line.split(",")
                    if len(fields) >= 6:
                        absorb(fields[0], fields[4], fields[5])
            except (OSError, TypeError, ValueError, OverflowError, http.client.HTTPException) as exc:
                # Keep usable earlier years and let the independent target quote
                # prove a closed trade even when this year's history is empty.
                annual_error = exc

        shanghai = ZoneInfo("Asia/Shanghai")
        now = now or datetime.datetime.now(shanghai)
        if now.tzinfo is not None:
            now = now.astimezone(shanghai)
        local_day = now.date()
        closed_today = target_date == local_day and now.hour >= 15
        if end in conflicting_dates and (target_date < local_day or closed_today):
            # Valid positive-volume annual rows still prove a completed trade.
            report_target_trade()
        if target_date.weekday() < 5 and end not in rows and closed_today:
            try:
                quote = fetch("today").get(f"hs_{code}")
            except (OSError, TypeError, ValueError, OverflowError, http.client.HTTPException) as exc:
                annual_error = annual_error or exc
            else:
                if (isinstance(quote, dict) and quote.get("1") == end.replace("-", "")
                        and type(quote.get("open")) is int and quote["open"] == 0):
                    clock = str(quote.get("dt", ""))
                    if (len(clock) == 4 and clock.isascii() and clock.isdigit()
                            and 15 <= int(clock[:2]) <= 23 and int(clock[2:]) < 60
                            and int(clock) <= now.hour * 100 + now.minute):
                        close = traded_close(quote.get("11"), quote.get("13"))
                        if close is not None:
                            # Trade evidence is independent of the QFQ basis check.
                            report_target_trade()
                            previous = quote.get("6")
                            anchor = max(rows, default=None)
                            # Only weekends are proven closed here. A matching
                            # price cannot bridge an unverified weekday gap.
                            prior = target_date - datetime.timedelta(days=1)
                            while prior.weekday() >= 5:
                                prior -= datetime.timedelta(days=1)
                            if (end not in conflicting_dates and anchor == prior.isoformat()
                                    and isinstance(previous, (str, int, float))
                                    and not isinstance(previous, bool)
                                    and float(previous) == rows[anchor]):
                                rows[end] = close
    except (OSError, TypeError, ValueError, OverflowError, http.client.HTTPException) as exc:
        annual_error = annual_error or exc

    if not rows and not target_trade_reported:
        detail = type(annual_error).__name__ if annual_error is not None else "missing history rows"
        raise RuntimeError(f"THS qfq unavailable: {detail}") from annual_error

    bars = []
    prev = None
    for day, close in sorted(rows.items()):
        if day >= start:
            bars.append({"date": day, "close": close,
                         "change_pct": round((close / prev - 1) * 100, 2) if prev else None})
        prev = close
    return bars


BAOSTOCK_PY = "/usr/bin/python3"
_BAOSTOCK_QUERY = r'''
import json, sys
import baostock as bs
payload = json.loads(sys.stdin.read())
out = {"ok": False, "rows": [], "error": ""}
login = bs.login()
if login.error_code != "0":
    out["error"] = f"login: {login.error_msg}"
else:
    try:
        rs = bs.query_history_k_data_plus(
            payload["code"], "date,close,tradestatus",
            start_date=payload["start"], end_date=payload["end"],
            frequency="d", adjustflag="2",
        )
        if rs.error_code != "0":
            out["error"] = f"query: {rs.error_msg}"
        else:
            fields = list(rs.fields)
            while rs.next():
                out["rows"].append(dict(zip(fields, rs.get_row_data())))
            out["ok"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        bs.logout()
print(json.dumps(out))
'''

# Batched BaoStock: one login for many symbols. The per-symbol variant pays a
# login + connection setup for every stock; measured 2026-10-28 that is ~1.4s
# each, so 377 blocked symbols cost ~8.5 minutes. One login for 20 symbols
# answers the same 80 bars each in ~1.5s total.
_BAOSTOCK_BATCH_QUERY = r'''
import json, sys
import baostock as bs
payload = json.loads(sys.stdin.read())
out = {"ok": False, "results": {}, "error": ""}
login = bs.login()
if login.error_code != "0":
    out["error"] = f"login: {login.error_msg}"
else:
    try:
        for code in payload["codes"]:
            rows = []
            rs = bs.query_history_k_data_plus(
                code, "date,close,tradestatus,volume",
                start_date=payload["start"], end_date=payload["end"],
                frequency="d", adjustflag="2",
            )
            if rs.error_code == "0":
                fields = list(rs.fields)
                while rs.next():
                    rows.append(dict(zip(fields, rs.get_row_data())))
                if rs.error_code != "0":
                    rows = []
            out["results"][code] = rows
        out["ok"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        bs.logout()
print(json.dumps(out))
'''

BAOSTOCK_BATCH_SIZE = 20


def baostock_daily_batch(
    specs: list[tuple[str, str]], end: str, *, batch_size: int = BAOSTOCK_BATCH_SIZE,
    on_target_trade=None,
) -> dict[str, list[dict]]:
    """BaoStock 前复权日K for many symbols with one login per batch.

    `specs` is [(symbol, start_date)]. Every symbol shares one wide query
    window (the earliest start minus the lookback); each symbol's own start
    filters its rows client-side. Grouping by window instead would give every
    symbol its own login — measured 2026-10-28 that is 377 logins ≈ 8.5
    minutes instead of one login per 20 symbols ≈ 70s.

    Returns {symbol: [{date, close, change_pct}]}. A symbol with no usable row
    maps to an empty list — the caller decides whether that is fatal.
    """
    result: dict[str, list[dict]] = {symbol: [] for symbol, _start in specs}
    if not specs:
        return result
    earliest = min(start for _symbol, start in specs)
    window_start = (datetime.date.fromisoformat(earliest) - datetime.timedelta(days=30)).isoformat()
    specs_by_code = {
        f"{'sh' if symbol.endswith('.SH') else 'sz'}.{symbol.split('.')[0]}": (symbol, start)
        for symbol, start in specs
    }
    codes = list(specs_by_code)

    for offset in range(0, len(codes), batch_size):
        chunk = codes[offset:offset + batch_size]
        try:
            proc = subprocess.run(
                [BAOSTOCK_PY, "-c", _BAOSTOCK_BATCH_QUERY],
                input=json.dumps({"codes": chunk, "start": window_start, "end": end}),
                capture_output=True, text=True, check=False, timeout=300,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"runner exited {proc.returncode}")
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            if not isinstance(payload, dict) or payload.get("ok") is not True or not isinstance(payload.get("results"), dict):
                raise RuntimeError("invalid or unsuccessful runner response")
        except (OSError, subprocess.SubprocessError, IndexError, ValueError, RuntimeError) as exc:
            print(f"  BaoStock batch {offset // batch_size + 1} unavailable: {type(exc).__name__}", flush=True)
            continue
        for code in chunk:
            symbol, start = specs_by_code[code]
            rows = payload["results"].get(code)
            bars, target_traded = _baostock_rows_to_bars(rows, start, end)
            if target_traded and on_target_trade is not None:
                on_target_trade(symbol)
            result[symbol] = bars
    return result


def _baostock_rows_to_bars(rows: object, start: str, end: str) -> tuple[list[dict], bool]:
    closes: dict[str, float] = {}
    conflicting = False
    if not isinstance(rows, list):
        return [], False
    for row in rows:
        if not isinstance(row, dict) or str(row.get("tradestatus")) != "1":
            continue
        day, close, volume = row.get("date"), row.get("close"), row.get("volume")
        if (not isinstance(day, str) or not isinstance(close, (float, int, str))
                or not isinstance(volume, (float, int, str)) or isinstance(close, bool) or isinstance(volume, bool)):
            continue
        try:
            if datetime.date.fromisoformat(day).isoformat() != day or day > end:
                continue
            close_f, volume_f = float(close), float(volume)
            if not all(math.isfinite(value) and value > 0 for value in (close_f, volume_f)):
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        if day in closes and closes[day] != close_f:
            conflicting = True
        closes[day] = close_f
    target_traded = end in closes
    if conflicting:
        return [], target_traded
    bars = []
    prev_close = None
    for day, close in sorted(closes.items()):
        if day >= start:
            change = round((close - prev_close) / prev_close * 100, 2) if prev_close else None
            bars.append({"date": day, "close": close, "change_pct": change})
        prev_close = close
    return bars, target_traded


def baostock_daily(symbol: str, start: str, end: str) -> list[dict]:
    """BaoStock 前复权日K（腾讯 fqkline 被网关 WAF 拦截时的备用源）。

    与 tencent_daily 返回同一结构（date/close/change_pct）。前复权基准同为最新
    交易日，窗口内收盘价与腾讯口径一致。BaoStock 只装在系统解释器里，故通过
    子进程调用（与 FTShare SDK 同一模式），任何解释器下都能用。
    """
    code = symbol.split(".")[0]
    market = "sh" if symbol.endswith(".SH") else "sz"
    lookback = (datetime.date.fromisoformat(start) - datetime.timedelta(days=30)).isoformat()
    proc = subprocess.run(
        [BAOSTOCK_PY, "-c", _BAOSTOCK_QUERY],
        input=json.dumps({"code": f"{market}.{code}", "start": lookback, "end": end}),
        capture_output=True, text=True, check=False, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"baostock runner failed ({proc.returncode}): {(proc.stderr or '')[:200]}")
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"baostock output unparseable: {exc}") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"baostock unavailable: {payload.get('error') or 'unknown'}")
    bars = []
    prev_close = None
    for row in payload.get("rows") or []:
        date = str(row.get("date") or "")
        close = row.get("close")
        if not date or str(row.get("tradestatus") or "") != "1":
            continue
        try:
            close_f = float(close)
        except (TypeError, ValueError):
            continue
        if not start or date >= start:
            chg = round((close_f - prev_close) / prev_close * 100, 2) if prev_close else None
            bars.append({"date": date, "close": close_f, "change_pct": chg})
        prev_close = close_f
    return bars


def cf_baostock_daily(
    symbol: str, start: str, end: str, *, client: CFBaoStockClient | None = None,
) -> list[dict]:
    """Read one low-chip qfq window through the authenticated CF gateway."""
    gateway = client or CFBaoStockClient.from_env()
    lookback = (datetime.date.fromisoformat(start) - datetime.timedelta(days=30)).isoformat()
    rows = gateway.klines([symbol], lookback, end).get(symbol, [])
    bars = []
    prev_close = None
    for row in rows:
        date = str(row.get("date") or "")
        if not date or str(row.get("tradestatus", "1")) != "1":
            continue
        try:
            close_f = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if date >= start:
            change = round((close_f - prev_close) / prev_close * 100, 2) if prev_close else None
            bars.append({"date": date, "close": close_f, "change_pct": change})
        prev_close = close_f
    return bars


def iwencai_profit_ratio(symbol: str, date: str) -> float | None:
    """收盘获利盘 ratio for one symbol at one date (iWenCai historical query).

    Kept as the single-symbol entry point for ad-hoc use; the tracking main
    path goes through iwencai_profit_ratios() so a whole date costs one query.
    """
    return iwencai_profit_ratios([(symbol, date)]).get((symbol, date))


IWENCAI_BATCH_CODES = 30
IWENCAI_BATCH_RETRIES = 3


def iwencai_profit_ratios(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], float | None]:
    """Fetch 收盘获利 for many (symbol, date) pairs with batched queries.

    iWenCai accepts "CODE1、CODE2 … 收盘获利[YYYYMMDD]" and returns one row per
    code, so a whole date's missing bars cost one call instead of one per
    symbol. Measured 2026-10-28: 30 codes resolve in ~7s, while the per-symbol
    fallback cost ~6s each — 340 symbols × 1 missing date was 30+ minutes and
    blew the 1800s budget.

    Returns {(symbol, date): ratio} with None for anything the upstream did
    not answer, so the caller can decide what to do about it.
    """
    result: dict[tuple[str, str], float | None] = {pair: None for pair in pairs}
    if not pairs:
        return result

    by_date: dict[str, list[str]] = {}
    for symbol, date in pairs:
        by_date.setdefault(date, []).append(symbol)

    for date, symbols in by_date.items():
        unique = sorted({s.split(".")[0] for s in symbols})
        for start in range(0, len(unique), IWENCAI_BATCH_CODES):
            chunk = unique[start:start + IWENCAI_BATCH_CODES]
            values = _iwencai_batch_query(chunk, date)
            for symbol in symbols:
                code = symbol.split(".")[0]
                if code in values:
                    result[(symbol, date)] = values[code]
    return result


def _iwencai_batch_query(codes: list[str], date: str) -> dict[str, float]:
    """One iWenCai query for many codes at one date; {} when it cannot answer."""
    ymd = date.replace("-", "")
    query = "、".join(codes) + f" 收盘获利[{ymd}]"
    for attempt in range(IWENCAI_BATCH_RETRIES):
        try:
            r = subprocess.run(
                ["/root/.hermes/scripts/iwencai-market-query", "-q", query, "--limit", str(max(10, len(codes) * 2)), "--timeout", "90"],
                capture_output=True, text=True, check=False, timeout=150,
            )
        except subprocess.TimeoutExpired:
            continue
        if r.returncode != 0:
            continue
        try:
            payload = json.loads(r.stdout)
        except json.JSONDecodeError:
            continue
        rows = payload.get("datas") or []
        if not rows:
            # The upstream occasionally answers a batch query with an empty
            # result set; retry once before giving up on this chunk.
            continue
        values: dict[str, float] = {}
        for row in rows:
            code = str(row.get("股票代码") or "").split(".")[0]
            if not code:
                continue
            for key, value in row.items():
                if "收盘获利" not in key:
                    continue
                try:
                    values[code] = round(float(value), 2)
                except (TypeError, ValueError):
                    continue
                break
        if values:
            return values
    return {}


def resolve_entry_snapshot(history_dir: Path, first_seen: str, symbol: str) -> Path:
    """Resolve the immutable snapshot that originally admitted a tracked symbol.

    Same-day threshold tightening may replace the canonical date snapshot and remove
    older members. A threshold-suffixed copy preserves their original join evidence.
    """
    canonical = history_dir / f"{first_seen}.json"
    candidates = [canonical, *sorted(history_dir.glob(f"{first_seen}.threshold-*.json"))]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            snapshot = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        enrichments = snapshot.get("enrichments")
        if isinstance(enrichments, dict) and symbol in enrichments:
            return candidate
    return canonical


def load_entry_enrichment(entry_snapshot: Path, symbol: str) -> tuple[dict, dict]:
    """Load immutable join-date evidence; missing/corrupt evidence blocks refresh."""
    try:
        snapshot = json.loads(entry_snapshot.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"missing or invalid join-date snapshot for {symbol}: {entry_snapshot.name}") from exc
    enrichments = snapshot.get("enrichments")
    if not isinstance(enrichments, dict) or symbol not in enrichments:
        raise RuntimeError(f"missing join-date enrichment for {symbol}: {entry_snapshot.name}")
    entry_enrichment = enrichments[symbol]
    if not isinstance(entry_enrichment, dict):
        raise RuntimeError(f"invalid join-date enrichment for {symbol}: {entry_snapshot.name}")
    entry_metrics = entry_enrichment.get("shareholder_metrics") or (snapshot.get("shareholder_metrics") or {}).get(symbol) or {}
    return entry_enrichment, entry_metrics


def fetch_current_year_profit(codes: list[str]) -> dict[str, float | None]:
    """Fetch current year-line profit for tracked symbols in bounded batches.

    年线为可选字段：iWenCai quota 耗尽 / 上游异常 / 非 JSON 响应均 fail-soft，
    返回全 None，绝不阻塞 tracking（日线 + 获利盘核心字段已在前序步骤拿到）。
    """
    values: dict[str, float | None] = {code: None for code in codes}
    for start in range(0, len(codes), 20):
        batch = codes[start:start + 20]
        query = "、".join(code.split(".")[0] for code in batch) + " 年线收盘获利"
        try:
            result = subprocess.run(
                ["/root/.hermes/scripts/iwencai-market-query", "-q", query, "--limit", "100", "--timeout", "90"],
                capture_output=True, text=True, check=False, timeout=120,
            )
        except subprocess.TimeoutExpired:
            print(f"WARNING: iWenCai year-profit query timeout for batch {start}-{start+len(batch)}; year_profit 留空", file=sys.stderr)
            continue
        if result.returncode != 0:
            # 常见为 quota 耗尽（所有 key 轮流失败）。年线可选 → 不阻塞。
            print(f"WARNING: iWenCai year-profit query failed rc={result.returncode}; year_profit 留空: {result.stdout.strip()[:120]}", file=sys.stderr)
            continue
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            print(f"WARNING: iWenCai year-profit query returned non-JSON (quota/upstream); year_profit 留空: {result.stdout.strip()[:120]}", file=sys.stderr)
            continue
        rows = payload.get("datas") or []
        field = next((key for row in rows for key in row if key.startswith("年线收盘获利[")), None)
        if not field:
            continue
        for row in rows:
            code = row.get("股票代码")
            if code not in values:
                continue
            try:
                values[code] = round(float(row.get(field)), 4)
            except (TypeError, ValueError):
                values[code] = None
    return values


def load_existing() -> dict:
    if not DATA.exists():
        return {"schema_version": "low-chip-tracking-v1", "generated_at": "", "stocks": {}}
    try:
        payload = json.loads(DATA.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"invalid existing tracking data: {DATA}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("stocks"), dict):
        raise RuntimeError(f"invalid existing tracking data: {DATA}")
    return payload


def normalize_daily(rows: object, first_seen: str) -> list[dict]:
    """Keep unique, finite, join-date-onward stored rows in chronological order."""
    by_date: dict[str, dict] = {}
    if not isinstance(rows, list):
        return []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = str(row.get("date") or "")
        try:
            if datetime.date.fromisoformat(date).isoformat() != date or isinstance(row.get("close"), bool):
                continue
            close = float(row.get("close"))
        except (TypeError, ValueError, OverflowError):
            continue
        if date < first_seen or not math.isfinite(close) or close <= 0:
            continue
        clean = dict(row)
        for key in ("change_pct", "profit_ratio"):
            value = clean.get(key)
            if value is None:
                continue
            try:
                if not math.isfinite(float(value)):
                    clean[key] = None
            except (TypeError, ValueError):
                clean[key] = None
        by_date[date] = clean
    return [by_date[date] for date in sorted(by_date)]


def atomic_write_json(path: Path, payload: dict) -> None:
    """Durably replace a JSON file from a temporary sibling."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise OSError(f"{path} already replaced; directory durability unconfirmed") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _tracking_window(dates: set[str], first_seen: str) -> list[str]:
    # The optional entry bar is separate from the 20 post-entry closes.
    return sorted(dates)[:MAX_TRACK_BARS + (first_seen in dates)]


def _is_confirmed_halt(evidence: object, day: str) -> bool:
    return (isinstance(evidence, dict) and evidence.get("status") == "halted"
            and evidence.get("target_day") == day and evidence.get("source") == "eastmoney_suspend_report"
            and isinstance(evidence.get("reason"), str) and bool(evidence["reason"].strip()))


def _window_complete(dates: set[str], first_seen: str, entry_halt: object) -> bool:
    # Keep in sync with the host-local publisher's independent coverage gate.
    # Callers supply unique, validated bar dates bounded by first_seen and the target day.
    if first_seen in dates:
        return len(dates) == MAX_STORED_BARS
    return len(dates) == MAX_TRACK_BARS and _is_confirmed_halt(entry_halt, first_seen)


def _has_target_or_complete(rec: dict, fetched: list[dict], target_day: str) -> bool:
    stored = {row["date"] for row in rec["daily"]}
    first_seen = rec["first_seen"]
    window = set(_tracking_window(stored | {row["date"] for row in fetched}, first_seen))
    return target_day in window or (
        stored <= window and _window_complete(window, first_seen, rec.get("entry_halt"))
    )


def _same_qfq_basis(left: dict[str, float], right: dict[str, float]) -> bool:
    overlap = left.keys() & right.keys()
    return bool(overlap) and all(left[day] == right[day] for day in overlap)


def _merge_prices(stored: list[dict], accepted: list[dict], incoming: list[dict]) -> list[dict]:
    """Add prices only on a verified common QFQ basis; never rewrite stored bars."""
    old = {row["date"]: row for row in stored}
    staged = {row["date"]: row for row in accepted}
    new = {row["date"]: row for row in incoming}
    reference = staged | old
    if not (new.keys() - reference.keys()):
        return accepted

    def comparable(left: dict[str, dict], right: dict[str, dict]) -> bool:
        return _same_qfq_basis({d: float(r["close"]) for d, r in left.items()},
                               {d: float(r["close"]) for d, r in right.items()})

    if not reference or comparable(reference, new):
        combined = new | staged
    elif staged.keys() <= new.keys() and (not old or comparable(old, new)):
        # A consistent single-source replacement for *unwritten* prices is
        # safe when it includes every staged date and agrees with stored bars.
        combined = new
    else:
        raise ValueError("QFQ overlap missing or closes disagree")
    return [combined[day] for day in sorted(combined.keys() - old.keys())]


def _qfq_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def validated_qfq_rebase(symbol: str, rec: dict, incoming: object, provider: str,
                         target_day: str, contract: object) -> tuple[list[dict], dict]:
    """Validate an explicitly audited, hash-bound single-response replacement.

    Contracts are injected by the caller after bounded, batched evidence collection.
    This function performs zero I/O; ordinary tracking keeps exact-overlap policy.
    required_dates is the independently audited whole tracking window (including
    intermediate trading sessions), bound to this exact provider response.
    """
    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise ValueError(f"QFQ rebase rejected: {reason}")

    def date(value: object) -> str:
        require(isinstance(value, str), "invalid date")
        try:
            require(datetime.date.fromisoformat(value).isoformat() == value, "invalid date")
        except (TypeError, ValueError) as exc:
            raise ValueError("QFQ rebase rejected: invalid date") from exc
        return value

    require(isinstance(contract, dict), "explicit contract missing")
    require(contract.get("schema") == "low-chip-qfq-rebase-v1", "contract schema")
    require(provider in ("Tencent", "THS", "BaoStock") and contract.get("provider") == provider,
            "single provider binding")
    require(contract.get("symbol") == symbol and contract.get("adjustment") == "qfq", "symbol/basis binding")
    first = date(rec["first_seen"])
    target = date(target_day)
    previous = date(contract.get("previous_basis_as_of"))
    basis = date(contract.get("basis_as_of"))
    require(first <= previous < basis == target, "basis interval")
    stored = rec.get("daily")
    require(isinstance(stored, list) and bool(stored), "stored history missing")
    require(normalize_daily(stored, first) == stored and len(normalize_daily(stored, first)) == len(stored),
            "stored rows invalid")
    require(contract.get("stored_sha256") == _qfq_digest(stored), "stale stored history")
    require(previous == max(row["date"] for row in stored), "stored cutoff")
    prior_basis = rec.get("price_basis")
    if prior_basis is not None:
        require(isinstance(prior_basis, dict) and prior_basis.get("adjustment") == "qfq"
                and date(prior_basis.get("as_of")) <= previous, "stored basis provenance")
    require(isinstance(incoming, list) and bool(incoming), "empty response")
    require(contract.get("prices_sha256") == _qfq_digest(incoming), "response binding")
    clean = normalize_daily(incoming, first)
    dates = [row["date"] for row in clean]
    require(len(clean) == len(incoming) and dates == [row.get("date") for row in incoming]
            and len(dates) == len(set(dates)) and all(day <= target for day in dates), "invalid/duplicate response rows")
    old = {row["date"]: row for row in stored}
    require(len(old) == len(stored) and old.keys() <= set(dates), "stored date lost")
    require(max(old) <= previous, "stored bars after basis date")
    required = contract.get("required_dates")
    require(isinstance(required, list) and bool(required), "required window missing")
    require(all(date(day) >= first for day in required) and required == sorted(set(required))
            and required == dates, "whole-window coverage")
    require(dates == _tracking_window(set(dates), first), "window exceeds cap")
    require(target in dates or _window_complete(set(dates), first, rec.get("entry_halt")), "target/window missing")
    require(any(float(row["close"]) != float(old[row["date"]]["close"])
                for row in clean if row["date"] in old), "unchanged overlap")
    evidence = contract.get("corporate_actions")
    require(isinstance(evidence, dict) and evidence.get("source") == "baostock_adjust_factor"
            and evidence.get("symbol") == symbol and evidence.get("start") == previous
            and evidence.get("end") == basis and evidence.get("error_code") == "0"
            and evidence.get("complete") is True, "corporate action envelope")
    actions = evidence.get("rows")
    require(isinstance(actions, list) and bool(actions), "corporate action missing")
    expected_code = ("sh." if symbol.endswith(".SH") else "sz.") + symbol.split(".")[0]
    action_dates = []
    for action in actions:
        require(isinstance(action, dict) and action.get("code") == expected_code, "corporate action symbol")
        action_day = date(action.get("dividOperateDate"))
        require(previous < action_day <= basis, "corporate action outside basis interval")
        for field in ("foreAdjustFactor", "backAdjustFactor", "adjustFactor"):
            value = action.get(field)
            try:
                require(not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0,
                        "invalid action factor")
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("QFQ rebase rejected: invalid action factor") from exc
        action_dates.append(action_day)
    require(len(action_dates) == len(set(action_dates)), "duplicate corporate actions")
    result = []
    prev = None
    for row in clean:
        day, close = row["date"], float(row["close"])
        # Every old dated observation and extra row-level evidence survives.
        out = {**old.get(day, {}), "date": day, "close": close,
               "change_pct": round((close - prev) / prev * 100, 2) if prev else None}
        if day not in old:
            out["profit_ratio"] = None
        result.append(out)
        prev = close
    provenance = {"provider": provider, "adjustment": "qfq", "as_of": basis,
                  "rebase": copy.deepcopy(contract), "previous_basis": copy.deepcopy(prior_basis)}
    return result, provenance


def main(*, target_day: str | None = None, skip_iwencai: bool = False,
         qfq_rebase_contracts: dict[str, dict] | None = None) -> int:
    now = datetime.datetime.now(ZoneInfo("Asia/Shanghai"))
    local_day = now.date().isoformat()
    today = datetime.date.fromisoformat(target_day).isoformat() if target_day is not None else local_day
    unclosed = today == local_day and now.hour < 15
    if today > local_day or (unclosed and target_day is not None):
        print(f"tracking target session has not closed: {today}; preserving prior file", flush=True)
        return 1
    seen = {symbol: [d for d in dates if d <= today] for symbol, dates in load_history_dates().items()}
    seen = {symbol: dates for symbol, dates in seen.items() if dates}
    existing = load_existing()
    stocks = existing.get("stocks", {})
    if any(str(row.get("date") or "") > today for rec in stocks.values()
           for row in (rec.get("daily") or []) if isinstance(row, dict)):
        raise RuntimeError("tracking input contains bars after target day; use a dated baseline")
    # (symbol, date) pairs that 同花顺 could not answer; resolved in one
    # batched iWenCai pass after the loop.
    pending_iwencai: list[tuple[str, str]] = []
    pending_prices: list[tuple[str, str]] = []
    bar_source: dict[str, list[dict]] = {}
    target_reported: set[str] = set()
    # Explicit, precollected evidence only: zero extra queries per tracking stock.
    contracts = copy.deepcopy(qfq_rebase_contracts) if qfq_rebase_contracts is not None else {}
    if not isinstance(contracts, dict) or any(not isinstance(c, dict) for c in contracts.values()):
        raise ValueError("invalid QFQ rebase contract map")
    rebased: set[str] = set()
    original_entry: dict[str, dict] = {}
    original_daily: dict[str, list[dict]] = {}

    def needs_prices(symbol: str, remaining_providers: tuple[str, ...]) -> bool:
        # Coverage and explicit rebase completion are independent obligations.
        pending_contract = (symbol in contracts and symbol not in rebased
                            and contracts[symbol].get("provider") in remaining_providers)
        return pending_contract or not _has_target_or_complete(
            stocks[symbol], bar_source.get(symbol, []), today)

    def accept_prices(symbol: str, start: str, raw_bars: object, source: str) -> None:
        bars = [row for row in normalize_daily(raw_bars, start) if row["date"] <= today]
        if any(row["date"] == today for row in bars):
            target_reported.add(symbol)
        try:
            if symbol in contracts and contracts[symbol].get("provider") == source:
                # Rebase the exact one-provider response before normalizing or
                # combining it with another source. Failure leaves the old series.
                rec = stocks[symbol]
                candidate_rec = {**rec, "daily": original_daily[symbol]}
                replacement, provenance = validated_qfq_rebase(
                    symbol, candidate_rec, raw_bars, source, today, contracts[symbol])
                rec["daily"] = replacement
                rec["price_basis"] = provenance
                bar_source[symbol] = []
                rebased.add(symbol)
            else:
                bar_source[symbol] = _merge_prices(stocks[symbol]["daily"], bar_source.get(symbol, []), bars)
        except ValueError as exc:
            print(f"  {symbol}: {source} {exc}", flush=True)

    for symbol in sorted(stocks.keys() | seen.keys()):
        dates = seen.get(symbol)
        if not dates:
            previous = stocks[symbol]
            dates = [previous["first_seen"], previous.get("last_seen") or previous["first_seen"]]
        first = min(dates)
        last = max(dates)
        rec = stocks.setdefault(symbol, {"name": "", "first_seen": first, "last_seen": last, "industry": "", "daily": []})
        if symbol in contracts:
            original_daily[symbol] = copy.deepcopy(rec.get("daily", []))
            original_entry[symbol] = {key: copy.deepcopy(rec[key]) for key in
                                      ("entry_features", "entry_financials", "entry_technical", "entry_halt")
                                      if key in rec}
        # 更新 first/last_seen（保留最早加入日）
        rec["first_seen"] = min(rec.get("first_seen") or first, first)
        rec["last_seen"] = last
        rec["daily"] = normalize_daily(rec.get("daily"), rec["first_seen"])
        rec.pop("coverage", None)  # Do not carry a previous session's halt forward.
        first_seen = rec["first_seen"]
        stored_dates = {row["date"] for row in rec["daily"]}
        has_baseline = first_seen in stored_dates
        if has_baseline or not _is_confirmed_halt(rec.get("entry_halt"), first_seen):
            rec.pop("entry_halt", None)
        rec["entry_baseline_status"] = "available" if has_baseline else "missing"

        # Preserve the legacy cap for records with a real entry bar.
        if has_baseline and len(stored_dates) > MAX_STORED_BARS:
            rec["daily"] = rec["daily"][:MAX_STORED_BARS]
            stored_dates = {row["date"] for row in rec["daily"]}
        rec["tracking_complete"] = _window_complete(stored_dates, first_seen, rec.get("entry_halt"))
        if rec["tracking_complete"] and symbol not in contracts:
            continue
        if unclosed:
            print(f"tracking target session has not closed: {today}; preserving prior file", flush=True)
            return 1
        try:
            tencent_rebase = symbol in contracts and contracts[symbol].get("provider") == "Tencent"
            bars = tencent_daily(symbol, rec["first_seen"], today, stored=None if tencent_rebase else rec["daily"],
                                 on_target_trade=lambda: target_reported.add(symbol))
        except RuntimeError as exc:
            print(f"  {symbol}: tencent daily unavailable ({exc})", flush=True)
            bars = []
        accept_prices(symbol, rec["first_seen"], bars, "Tencent")
        if needs_prices(symbol, ("THS", "BaoStock")):
            pending_prices.append((symbol, rec["first_seen"]))
        time.sleep(0.2)

    # Check both price fallbacks even for suspended symbols: historical bars
    # can be backfilled, and an actual target-day trade takes precedence.
    if pending_prices:
        unresolved = []

        def fetch_ths(spec: tuple[str, str]) -> tuple[list[dict], bool]:
            symbol, start = spec
            reported = False

            def note_trade() -> None:
                nonlocal reported
                reported = True

            try:
                bars = ths_daily(symbol, start, today, on_target_trade=note_trade)
            except RuntimeError as exc:
                print(f"  {symbol}: {exc}", flush=True)
                bars = []
            return bars, reported

        with ThreadPoolExecutor(max_workers=4) as pool:
            for (symbol, start), (bars, reported) in zip(pending_prices, pool.map(fetch_ths, pending_prices)):
                if reported:
                    target_reported.add(symbol)
                accept_prices(symbol, start, bars, "THS")
                if needs_prices(symbol, ("BaoStock",)):
                    unresolved.append((symbol, start))
        print(f"THS fallback coverage: {len(pending_prices) - len(unresolved)}/{len(pending_prices)}", flush=True)
        pending_prices = unresolved
    if pending_prices:
        batched = baostock_daily_batch(pending_prices, today, on_target_trade=target_reported.add)
        for symbol, start in pending_prices:
            accept_prices(symbol, start, batched.get(symbol), "BaoStock")
        unresolved = [(s, start) for s, start in pending_prices
                      if not _has_target_or_complete(stocks[s], bar_source.get(s, []), today)]
        print(
            f"batched baostock fallback coverage: {len(pending_prices) - len(unresolved)}/{len(pending_prices)}",
            flush=True,
        )
        pending_prices = unresolved

    if set(contracts) != rebased:
        print(f"QFQ rebase contracts unresolved: {', '.join(sorted(set(contracts) - rebased))}; preserving prior file", flush=True)
        return 1

    halts = {}
    if any(symbol not in target_reported for symbol, _ in pending_prices):
        try:
            halts = confirmed_suspensions(today)
        except RuntimeError as exc:
            print(str(exc), flush=True)
    failed = []
    for symbol, _ in pending_prices:
        evidence = halts.get(symbol)
        if symbol not in target_reported and _is_confirmed_halt(evidence, today):
            rec = stocks[symbol]
            rec["coverage"] = evidence
            if today == rec["first_seen"]:
                rec["entry_halt"] = evidence
        else:
            failed.append(symbol)
    if failed or not stocks:
        print(f"tracking coverage missing for {today}: {', '.join(failed) or 'empty pool'}; preserving prior file", flush=True)
        return 1

    for symbol, rec in sorted(stocks.items()):
        stored = {row["date"]: row for row in rec["daily"]}
        new = {row["date"]: row for row in bar_source.get(symbol, [])}
        target_bars = []
        prev_close = None
        window = _tracking_window(stored.keys() | new.keys(), rec["first_seen"])
        if not stored.keys() <= set(window):
            print(f"  {symbol}: window would discard stored bars; preserving prior file", flush=True)
            return 1
        if rec["first_seen"] in window:
            rec["entry_baseline_status"] = "available"
            rec.pop("entry_halt", None)
        for day in window:
            row = stored[day] if day in stored else new[day]
            close = float(row["close"])
            if day not in stored:
                change = round((close - prev_close) / prev_close * 100, 2) if prev_close else None
                target_bars.append({**row, "change_pct": change})
            prev_close = close
        # Derive completion from the exact window that will be persisted.
        rec["tracking_complete"] = _window_complete(set(window), rec["first_seen"], rec.get("entry_halt"))
        if symbol in rebased:
            # A validated replacement already owns every price in this window.
            # Enrich only new dated observations; all historical ratios survive.
            new_days = set(window) - {row["date"] for row in original_daily[symbol]}
            if new_days:
                profit_map = chip_list_profit_ratios(symbol, start=rec["first_seen"])
                for row in rec["daily"]:
                    if row["date"] in new_days:
                        pr = profit_map.get(row["date"].replace("-", ""))
                        row["profit_ratio"] = pr
                        if pr is None:
                            pending_iwencai.append((symbol, row["date"]))
            continue
        # 获利盘优先走同花顺 chip_list 曲线复算（无鉴权、无每日配额），失败 fallback iWenCai。
        # 窗口锚定 first_seen：同花顺只返回窗口内最近 40 个交易日，固定 90 天窗口
        # 会漏掉早期入池标的的 bar，导致逐 (标的, 日期) 回退 iWenCai，
        # 2026-10-28 实测 300+ 次调用 ≈ 30 分钟，直接撞穿 1800s 超时。
        if not target_bars:
            continue
        profit_map = chip_list_profit_ratios(symbol, start=rec.get("first_seen") or None)
        for bar in target_bars:
            pr = profit_map.get(bar["date"].replace("-", ""))
            if pr is None:
                # Queue the gap; the whole batch is resolved after the loop
                # below. A per-symbol call here is what blew the time budget.
                pending_iwencai.append((symbol, bar["date"]))
            rec["daily"].append({**bar, "profit_ratio": pr})

        rec["daily"].sort(key=lambda x: x["date"])

    # 同花顺覆盖不到的 (标的, 日期) 用 iWenCai 批量补齐：一次查询覆盖同一天的
    # 全部标的（实测 30 只 ≈ 7s），逐只查询则是每只 ≈ 6s。
    if pending_iwencai and not skip_iwencai:
        resolved = iwencai_profit_ratios(pending_iwencai)
        still_missing = 0
        for sym, day in pending_iwencai:
            value = resolved.get((sym, day))
            if value is None:
                still_missing += 1
                continue
            for row in stocks.get(sym, {}).get("daily", []):
                if row.get("date") == day:
                    row["profit_ratio"] = value
                    break
        print(
            f"iWenCai batch: {len(pending_iwencai) - still_missing}/{len(pending_iwencai)} 补齐，"
            f"{still_missing} 仍缺失",
            flush=True,
        )

    # 补名称/行业（从历史快照的 enrichments 或 periods）
    snaps = sorted((p for p in HISTORY_DIR.glob("????-??-??.json") if p.stem <= today), reverse=True)
    name_map: dict[str, str] = {}
    industry_map: dict[str, str] = {}
    for p in snaps:
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        enr = s.get("enrichments") or {}
        for sym, e in enr.items():
            industry_map.setdefault(sym, e.get("industry", ""))
        for period_rows in s.get("periods", {}).values():
            for row in period_rows:
                name_map.setdefault(row["symbol"], row.get("name", ""))
    for sym, r in stocks.items():
        r["name"] = r.get("name") or name_map.get(sym, "")
        r["industry"] = r.get("industry") or industry_map.get(sym, "")
        if sym in rebased:
            for key in ("entry_features", "entry_financials", "entry_technical", "entry_halt"):
                if key in original_entry[sym]:
                    r[key] = copy.deepcopy(original_entry[sym][key])
                else:
                    r.pop(key, None)
            continue
        entry_snapshot = resolve_entry_snapshot(HISTORY_DIR, r["first_seen"], sym)
        entry_enrichment, entry_metrics = load_entry_enrichment(entry_snapshot, sym)
        r["entry_features"] = {
            "quality_shareholder": None if entry_enrichment.get("quality_shareholder") is None else bool(entry_enrichment["quality_shareholder"]),
            "quality_shareholder_names": list(entry_enrichment.get("quality_shareholder_names") or []),
            "institutional_shareholder": None if entry_enrichment.get("institutional_shareholder") is None else bool(entry_enrichment["institutional_shareholder"]),
            "institutional_shareholder_names": list(entry_enrichment.get("institutional_shareholder_names") or []),
            "chip_focus": entry_metrics.get("chip_focus") or "",
            "main_force": entry_metrics.get("main_force"),
            "main_force_label": entry_metrics.get("main_force_label") or "",
        }
        r["entry_financials"] = dict(entry_enrichment.get("financials") or {})
        r["entry_technical"] = dict(entry_enrichment.get("technical") or {})

    if not skip_iwencai:
        current_year_profit = fetch_current_year_profit(sorted(stocks))
        for sym, r in stocks.items():
            r["year_profit"] = current_year_profit.get(sym)

    # generated_at 只表示「本文件被写出的时刻」，不等于「数据覆盖到哪个交易日」。
    # 16:00 主链的 refresh_low_chip_ftshare_industry.py 会重写 tracking 的
    # generated_at（它做行业字段刷新，不碰 K 线），于是 16:40 追踪 cron 用
    # generated_at 判断幂等时会误判「已回填到今天」→ 当天 K 线永不落库
    # （2026-10-20 事故：追踪最新 bar 停在 10-19）。
    # data_through = 实际写入的最后一根 K 线日期，才是幂等判据。
    data_through = max(
        (d["date"] for rec in stocks.values() for d in (rec.get("daily") or []) if d.get("date")),
        default="",
    )
    atomic_write_json(DATA, {
        "schema_version": "low-chip-tracking-v1",
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_through": data_through,
        "stocks": stocks,
    })
    print(json.dumps({"stocks": len(stocks), "total_bars": sum(len(r["daily"]) for r in stocks.values())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-day", help="closed trading session (YYYY-MM-DD); default: Shanghai date")
    parser.add_argument("--skip-iwencai", action="store_true", help="skip iWenCai calls and preserve existing year-profit fields")
    options = parser.parse_args()
    sys.exit(main(target_day=options.target_day, skip_iwencai=options.skip_iwencai))
