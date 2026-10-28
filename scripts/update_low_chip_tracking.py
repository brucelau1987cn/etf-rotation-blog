#!/usr/bin/env python3
"""Track daily price + profit-ratio history for every stock that ever entered the low-chip screen.

Data source:
- Daily OHLCV (qfq): Tencent fqkline API (web.ifzq.gtimg.cn)
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

import datetime
import http.client
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
import urllib.request
from pathlib import Path

try:
    from cf_baostock_client import CFBaoStockClient, CFBaoStockError
except ModuleNotFoundError:
    from scripts.cf_baostock_client import CFBaoStockClient, CFBaoStockError

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/low-chip-tracking.json"
HISTORY_DIR = ROOT / "public/data/low-chip-history"
MIN_TRACK_DAYS = 1  # 至少 1 天数据才展示（刚加入当天即开始记录）
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
        datetime.date.fromisoformat(str(row[0]))
        close = float(row[2])
        return math.isfinite(close) and close > 0
    except (TypeError, ValueError):
        return False


def tencent_daily(
    symbol: str,
    start: str,
    end: str,
    *,
    opener=None,
    sleeper=time.sleep,
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
    payload = None
    for host in TENCENT_KLINE_HOSTS:
        for attempt in range(len(retry_delays) + 1):
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
                    candidate_bars = symbol_data.get("qfqday") or symbol_data.get("day")
                valid_rows = [row for row in candidate_bars if _valid_tencent_row(row)] if isinstance(candidate_bars, list) else []
                in_window_rows = [
                    row for row in valid_rows
                    if (not start or str(row[0]) >= start) and (not end or str(row[0]) <= end)
                ]
                valid_bars = bool(in_window_rows)
                if business_code == 0 and valid_bars:
                    payload = symbol_data
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
            if attempt < len(retry_delays):
                sleeper(retry_delays[attempt])
        if payload is not None:
            break
    if payload is None:
        raise RuntimeError(f"Tencent fqkline unavailable for {symbol} on all hosts: {last_error}")

    kl = payload.get("qfqday") or payload.get("day") or []
    bars = []
    prev_close = None
    for row in kl:
        # [date, open, close, high, low, volume]
        if not _valid_tencent_row(row):
            continue
        date, _, close, _, _, _ = row[:6]
        if start and date < start:
            # Keep prev_close so first in-window change_pct is still correct.
            try:
                prev_close = float(close)
            except (TypeError, ValueError):
                pass
            continue
        if end and date > end:
            continue
        try:
            close_f = float(close)
        except (TypeError, ValueError):
            continue
        chg = None
        if prev_close:
            chg = round((close_f - prev_close) / prev_close * 100, 2)
        bars.append({"date": date, "close": close_f, "change_pct": chg})
        prev_close = close_f
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
                code, "date,close,tradestatus",
                start_date=payload["start"], end_date=payload["end"],
                frequency="d", adjustflag="2",
            )
            if rs.error_code == "0":
                fields = list(rs.fields)
                while rs.next():
                    rows.append(dict(zip(fields, rs.get_row_data())))
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
    codes: list[str] = []
    for symbol, _start in specs:
        code = symbol.split(".")[0]
        market = "sh" if symbol.endswith(".SH") else "sz"
        codes.append(f"{market}.{code}")
    start_by_code = {
        f"{'sh' if symbol.endswith('.SH') else 'sz'}.{symbol.split('.')[0]}": start
        for symbol, start in specs
    }

    for offset in range(0, len(codes), batch_size):
        chunk = codes[offset:offset + batch_size]
        proc = subprocess.run(
            [BAOSTOCK_PY, "-c", _BAOSTOCK_BATCH_QUERY],
            input=json.dumps({"codes": chunk, "start": window_start, "end": end}),
            capture_output=True, text=True, check=False, timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"baostock runner failed ({proc.returncode}): {(proc.stderr or '')[:200]}")
        try:
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"baostock output unparseable: {exc}") from exc
        if not payload.get("ok"):
            raise RuntimeError(f"baostock unavailable: {payload.get('error') or 'unknown'}")
        for code, rows in (payload.get("results") or {}).items():
            start = start_by_code.get(code)
            if start is None:
                continue
            for symbol, symbol_start in specs:
                bare = symbol.split(".")[0]
                market = "sh" if symbol.endswith(".SH") else "sz"
                if f"{market}.{bare}" == code:
                    result[symbol] = _baostock_rows_to_bars(rows, symbol_start)
                    break
    return result


def _baostock_rows_to_bars(rows: list[dict], start: str) -> list[dict]:
    bars: list[dict] = []
    prev_close = None
    for row in rows or []:
        date = str(row.get("date") or "")
        close = row.get("close")
        if not date or str(row.get("tradestatus") or "1") != "1":
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
    """收盘获利盘 ratio for one symbol at one date (iWenCai historical query)."""
    ymd = date.replace("-", "")
    q = f"{symbol.split('.')[0]} 收盘获利[{ymd}]"
    r = subprocess.run(
        ["/root/.hermes/scripts/iwencai-market-query", "-q", q, "--limit", "2", "--timeout", "45"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return None
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    for row in d.get("datas") or []:
        if str(row.get("股票代码") or "").split(".")[0] != symbol.split(".")[0]:
            continue
        for k, v in row.items():
            if "收盘获利" in k:
                try:
                    return round(float(v), 2)
                except (TypeError, ValueError):
                    return None
    return None


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
            datetime.date.fromisoformat(date)
            close = float(row.get("close"))
        except (TypeError, ValueError):
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
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main() -> int:
    seen = load_history_dates()
    existing = load_existing()
    stocks = existing.get("stocks", {})
    today = datetime.date.today().isoformat()
    # (symbol, date) pairs that 同花顺 could not answer; resolved in one
    # batched iWenCai pass after the loop.
    pending_iwencai: list[tuple[str, str]] = []
    # (symbol, first_seen) pairs whose K-line came from the Tencent WAF
    # fallback; resolved in one batched BaoStock pass after the loop.
    pending_baostock: list[tuple[str, str]] = []
    # symbol -> resolved daily bars
    bar_source: dict[str, list[dict]] = {}

    for symbol, dates in sorted(seen.items()):
        first = min(dates)
        last = max(dates)
        rec = stocks.setdefault(symbol, {"name": "", "first_seen": first, "last_seen": last, "industry": "", "daily": []})
        # 更新 first/last_seen（保留最早加入日）
        rec["first_seen"] = min(rec.get("first_seen") or first, first)
        rec["last_seen"] = last
        rec["daily"] = normalize_daily(rec.get("daily"), rec["first_seen"])

        # 固定追踪窗口：加入日基准 + 加入后的前 20 个交易日；完成后停止请求新数据
        if len(rec.get("daily") or []) >= MAX_STORED_BARS:
            rec["daily"] = sorted(rec["daily"], key=lambda x: x["date"])[:MAX_STORED_BARS]
            rec["tracking_complete"] = True
            continue
        try:
            bars = tencent_daily(symbol, rec["first_seen"], today)
        except RuntimeError as exc:
            # 腾讯 fqkline 被网关 WAF 拦截（HTTP 501）。备用源在下面统一批量取，
            # 逐只调用每次都要重新登录 BaoStock，377 只约 8.5 分钟。
            print(f"  {symbol}: tencent daily unavailable ({exc}), queued for batched fallback", flush=True)
            pending_baostock.append((symbol, rec["first_seen"]))
            bars = []
        if bars:
            bar_source[symbol] = bars
        time.sleep(0.2)

    # 腾讯被 WAF 拦截的标的统一批量取 BaoStock：一次登录覆盖 20 只，
    # 实测 377 只 ≈ 70s；逐只调用（每次重新登录）需要 ≈ 8.5 分钟。
    if pending_baostock:
        try:
            batched = baostock_daily_batch(pending_baostock, today)
        except Exception as exc:  # noqa: BLE001
            print(f"batch baostock fallback failed for {len(pending_baostock)} symbols: {exc}", flush=True)
            batched = {}
        resolved_count = 0
        for symbol, bars in batched.items():
            if not bars:
                continue
            bar_source[symbol] = bars
            resolved_count += 1
        print(
            f"batched baostock fallback: {resolved_count}/{len(pending_baostock)} 只取到 K 线",
            flush=True,
        )

    for symbol, dates in sorted(seen.items()):
        rec = stocks.get(symbol)
        if rec is None:
            continue
        bars = bar_source.get(symbol)
        if not bars:
            # 上游无有效数据，保留已有行，下次重试
            rec["daily"] = sorted(rec.get("daily", []), key=lambda x: x["date"])[:MAX_STORED_BARS]
            rec["tracking_complete"] = len(rec["daily"]) >= MAX_STORED_BARS
            print(f"  {symbol}: no bars from any source, preserving {len(rec['daily'])} existing rows", flush=True)
            continue
        target_bars = bars[:MAX_STORED_BARS]
        have = {d["date"] for d in rec["daily"]}
        new_rows = []
        # 获利盘优先走同花顺 chip_list 曲线复算（无鉴权、无每日配额），失败 fallback iWenCai。
        # 窗口锚定 first_seen：同花顺只返回窗口内最近 40 个交易日，固定 90 天窗口
        # 会漏掉早期入池标的的 bar，导致逐 (标的, 日期) 回退 iWenCai，
        # 2026-10-28 实测 300+ 次调用 ≈ 30 分钟，直接撞穿 1800s 超时。
        profit_map = chip_list_profit_ratios(symbol, start=rec.get("first_seen") or None)
        for bar in target_bars:
            if bar["date"] in have:
                continue
            pr = profit_map.get(bar["date"].replace("-", ""))
            if pr is None:
                # Queue the gap; the whole batch is resolved after the loop
                # below. A per-symbol call here is what blew the time budget.
                pending_iwencai.append((symbol, bar["date"]))
            new_rows.append({**bar, "profit_ratio": pr})
            have.add(bar["date"])
        rec["daily"].extend(new_rows)
        rec["daily"].sort(key=lambda x: x["date"])
        # 固定首段窗口，禁止滚动成“最近 20 日”而改变加入以来口径
        rec["daily"] = rec["daily"][:MAX_STORED_BARS]
        rec["tracking_complete"] = len(rec["daily"]) >= MAX_STORED_BARS

    # 同花顺覆盖不到的 (标的, 日期) 用 iWenCai 批量补齐：一次查询覆盖同一天的
    # 全部标的（实测 30 只 ≈ 7s），逐只查询则是每只 ≈ 6s。
    if pending_iwencai:
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

    # 清理：少于 MIN_TRACK_DAYS 天数的股票（刚加入、数据不足）
    for sym in [s for s, r in stocks.items() if len(r.get("daily", [])) < MIN_TRACK_DAYS]:
        print(f"  drop {sym}: only {len(stocks[sym]['daily'])} bars", flush=True)
        stocks.pop(sym, None)

    # 补名称/行业（从历史快照的 enrichments 或 periods）
    snaps = sorted(HISTORY_DIR.glob("????-??-??.json"), reverse=True)
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
        entry_snapshot = resolve_entry_snapshot(HISTORY_DIR, r["first_seen"], sym)
        entry_enrichment, entry_metrics = load_entry_enrichment(entry_snapshot, sym)
        r["entry_features"] = {
            "quality_shareholder": bool(entry_enrichment.get("quality_shareholder")),
            "quality_shareholder_names": list(entry_enrichment.get("quality_shareholder_names") or []),
            "institutional_shareholder": bool(entry_enrichment.get("institutional_shareholder")),
            "institutional_shareholder_names": list(entry_enrichment.get("institutional_shareholder_names") or []),
            "chip_focus": entry_metrics.get("chip_focus") or "",
            "main_force": entry_metrics.get("main_force"),
            "main_force_label": entry_metrics.get("main_force_label") or "",
        }
        r["entry_financials"] = dict(entry_enrichment.get("financials") or {})
        r["entry_technical"] = dict(entry_enrichment.get("technical") or {})

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
    sys.exit(main())
