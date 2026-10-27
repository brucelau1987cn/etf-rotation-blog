#!/usr/bin/env python3
from __future__ import annotations
import json
import math
import os
import re
import sqlite3
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Iterable

try:
    from etf_bar_cache import connect, get_bars
except ModuleNotFoundError:
    from scripts.etf_bar_cache import connect, get_bars
try:
    from cf_baostock_client import CFBaoStockClient
except ModuleNotFoundError:
    from scripts.cf_baostock_client import CFBaoStockClient

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
CACHE_DB = ROOT / "data/local/etf-compass.db"
HISTORY_START = "2022-01-01"
HISTORY_BARS = 640
RISE_FALL = 0.15
MIN_SPACING = 10


def calculate_touchstone(closes: Iterable[float], dates: Iterable[str]) -> dict:
    prices, day_list = [float(x) for x in closes], list(dates)
    if len(prices) != len(day_list): raise ValueError("closes and dates must have equal length")
    if not prices: return _empty_metrics(0)
    direction, zig_high, zig_low = 0, 0, 0
    previous_trough = last_confirmation = None
    for i, close in enumerate(prices):
        if direction == 0:
            if close > prices[zig_high]: zig_high = i
            if close < prices[zig_low]: zig_low = i
            rise = close >= prices[zig_low] * (1 + RISE_FALL)
            fall = close <= prices[zig_high] * (1 - RISE_FALL)
            if rise: direction, zig_high = 1, i
            elif fall: direction, zig_low = -1, i
        elif direction == 1:
            if close >= prices[zig_high]: zig_high = i
            elif close <= prices[zig_high] * (1 - RISE_FALL): direction, zig_low = -1, i
        elif close <= prices[zig_low]:
            zig_low = i
        elif close >= prices[zig_low] * (1 + RISE_FALL):
            last_confirmation = _trough_event(zig_low, i, prices, day_list, previous_trough)
            previous_trough, direction, zig_high = zig_low, 1, i
    result = _empty_metrics(len(prices))
    if last_confirmation:
        result.update({k: v for k, v in last_confirmation.items() if k != "confirmation_index"})
        result["last_confirmation_date"] = last_confirmation["confirmation_date"]
    current = last_confirmation if last_confirmation and last_confirmation["confirmation_index"] == len(prices) - 1 else None
    result["bottom_alert"] = bool(current and current["spacing_qualified"])
    result["bottom_confirmed"] = result["bottom_alert"]
    candidate_trough_allowed = previous_trough is None or (zig_low - 1 - previous_trough) >= MIN_SPACING
    candidate_active = (
        direction == -1
        and zig_low < len(prices) - 1
        and prices[-1] > prices[zig_low]
        and candidate_trough_allowed
    )
    result["candidate_bottom_active"] = candidate_active
    result["candidate_anchor_date"] = day_list[zig_low] if direction == -1 else None
    result["candidate_anchor_close"] = prices[zig_low] if direction == -1 else None
    result["candidate_rebound_pct"] = round((prices[-1] / prices[zig_low] - 1) * 100, 4) if direction == -1 else None
    result["touchstone_hit"] = candidate_active or result["bottom_alert"]
    return result


def _empty_metrics(coverage):
    return {"candidate_bottom_active": False, "candidate_anchor_date": None, "candidate_anchor_close": None, "candidate_rebound_pct": None, "touchstone_hit": False, "bottom_alert": False, "bottom_confirmed": False, "confirmation_date": None, "anchor_date": None, "anchor_close": None, "confirmation_close": None, "rise_pct": None, "last_confirmation_date": None, "coverage_bars": coverage, "model": "ZIG15-close-state-machine", "source": "qfq daily close (local cache/Tencent/THS qfq/CF BaoStock/local BaoStock)"}


def _trough_event(anchor, confirmation, prices, dates, previous_trough):
    return {"confirmation_index": confirmation, "confirmation_date": dates[confirmation], "anchor_date": dates[anchor], "anchor_close": prices[anchor], "confirmation_close": prices[confirmation], "rise_pct": round((prices[confirmation] / prices[anchor] - 1) * 100, 4), "spacing_qualified": previous_trough is None or (anchor - 1 - previous_trough) >= MIN_SPACING}


def _item(symbol):
    code, market = symbol.split('.') if '.' in symbol else (symbol, 'SZ')
    return {'code': code, 'market': 'XSHG' if market.upper() == 'SH' else 'XSHE'}


def load_history(item, end, db_path=CACHE_DB):
    """Read the deterministic 640-bar cache path; use bounded network fallback."""
    item = _item(item) if isinstance(item, str) else item
    if Path(db_path).exists():
        try:
            with connect(Path(db_path)) as db:
                cached = get_bars(db, item['market'], item['code'], 'qfq', HISTORY_BARS)
            cached = _normalize_history(cached, end)
            if cached:
                return cached
        except Exception:
            pass
    try:
        rows = fetch_tencent_history(item, HISTORY_BARS)
    except Exception:
        rows = []
    if not rows:
        # 同花顺前复权：腾讯被 WAF 501 时的第一顺位备源（0.03s，全市场）
        try:
            rows = fetch_ths_history(item, HISTORY_BARS, end)
        except Exception:
            rows = []
    if not rows:
        try:
            rows = fetch_cf_baostock_history(item, HISTORY_START, str(end)[:10])
        except Exception:
            rows = []
    if not rows:
        try:
            rows = fetch_baostock_history(item, HISTORY_START, str(end)[:10])
        except Exception:
            rows = []
    if not rows:
        try:
            rows = fetch_stock_api_history(item, HISTORY_BARS)
        except Exception:
            rows = []
    return _normalize_history(rows, end)


def _normalize_history(rows, end):
    """Apply one no-lookahead and valid-close contract to every source."""
    cutoff = str(end)[:10]
    by_date = {}
    for row in rows or []:
        day = str(row.get('trade_date') or row.get('date') or '')[:10]
        try:
            close = float(row.get('close'))
        except (TypeError, ValueError):
            continue
        if not day or day > cutoff or not math.isfinite(close) or close <= 0:
            continue
        by_date[day] = {'trade_date': day, 'close': close}
    return [by_date[day] for day in sorted(by_date)]


def fetch_tencent_history(item, count=HISTORY_BARS):
    from update_a_share_bar_cache import fetch_tencent_history as fetch
    return fetch(item, count)


def fetch_ths_history(item, count=HISTORY_BARS, end=None):
    """同花顺前复权日线（01=前复权，02=后复权，00=不复权）。

    单次 0.03s，全市场覆盖；腾讯 WAF 501 与 BaoStock 挂死时的第一顺位备源。
    按年份分段拉取（/YYYY.js）以覆盖长历史，年份端点偶发 502 限流，
    因此每个年份重试一次，并始终合并 /last.js（近 140 根）作为兜底。
    """
    import datetime as _dt
    import time as _time

    code = item['code']
    last_day = str(end or _dt.date.today().isoformat())[:10]
    last_year = int(last_day[:4])
    # 640 根日线 ≈ 2.6 年；多取一年保证覆盖，且不无谓多拉历史
    years = list(range(last_year - 3, last_year + 1))
    bars: dict[str, float] = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Referer': 'https://stockpage.10jqka.com.cn/',
    }

    def _fetch(url: str) -> str:
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.read().decode('gbk', errors='ignore')
            except Exception:
                if attempt == 0:
                    _time.sleep(0.2)
        return ''

    def _absorb(text: str) -> None:
        match = re.search(r'\((.*)\)', text, re.S)
        if not match:
            return
        try:
            payload = json.loads(match.group(1))
        except Exception:
            return
        for entry in str(payload.get('data') or '').split(';'):
            if not entry:
                continue
            parts = entry.split(',')
            if len(parts) < 5 or len(parts[0]) != 8:
                continue
            raw = parts[0]
            day = f'{raw[:4]}-{raw[4:6]}-{raw[6:]}'
            try:
                close = float(parts[4])
            except (TypeError, ValueError):
                continue
            if math.isfinite(close) and close > 0:
                bars[day] = close

    # 传输协议统一走 HTTPS（2026-10-27）：与仓库内其它同花顺客户端
    # （functions/api/public/v1/_ths.js、public/js/price-volume-tag.js、
    # scripts/update_precious_inventory.py）保持一致，避免明文 HTTP 传输行情数据。
    for year in years:
        _absorb(_fetch(f'https://d.10jqka.com.cn/v6/line/hs_{code}/01/{year}.js'))
    # 年份端点全部 502 时仍能拿到近 140 根，避免单只股票整体掉成 0 根
    if len(bars) < count:
        _absorb(_fetch(f'https://d.10jqka.com.cn/v6/line/hs_{code}/01/last.js'))

    ordered = sorted(bars.items())[-count:]
    return [{'trade_date': day, 'close': close} for day, close in ordered]


def fetch_cf_baostock_history(item, start=HISTORY_START, end='2099-12-31', *, client=None):
    """Fetch qfq closes from CF before using the local BaoStock session."""
    symbol = f"{item['code']}.{'SH' if item['market'] == 'XSHG' else 'SZ'}"
    gateway = client or CFBaoStockClient.from_env()
    rows = gateway.klines(
        [symbol], start, end, fields=('date', 'close', 'tradestatus'),
    )[symbol]
    result = []
    for row in rows:
        if str(row.get('tradestatus', '1')) != '1' or not row.get('date'):
            continue
        try:
            close = float(row.get('close'))
        except (TypeError, ValueError):
            continue
        if math.isfinite(close) and close > 0:
            result.append({'trade_date': str(row['date']), 'close': close})
    return result


def fetch_baostock_history(item, start=HISTORY_START, end='2099-12-31'):
    import baostock as bs
    import contextlib
    import io
    market = 'sh' if item['market'] == 'XSHG' else 'sz'
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        login = bs.login()
        logged_in = getattr(login, 'error_code', '1') == '0'
        if not logged_in:
            raise RuntimeError(f'BaoStock login failed: {getattr(login, "error_msg", "unknown error")}')
        try:
            rs = bs.query_history_k_data_plus(
                f'{market}.{item["code"]}', 'date,close,tradestatus',
                start_date=start, end_date=end, frequency='d', adjustflag='2')
            rows = []
            while rs.error_code == '0' and rs.next():
                rows.append(rs.get_row_data())
            if getattr(rs, 'error_code', '1') != '0' or not rows:
                return []
            result = []
            for row in rows:
                if len(row) >= 3 and row[0] and row[2] == '1':
                    try:
                        close = float(row[1])
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(close) and close > 0:
                        result.append({'trade_date': str(row[0]), 'close': close})
            return result
        finally:
            bs.logout()


def fetch_stock_api_history(item, count=HISTORY_BARS):
    """Use stock-api with its working canonical Tencent adapter."""
    market_code = ("SH" if item["market"] == "XSHG" else "SZ") + item["code"]
    proc = subprocess.run(
        ["npx", "-y", "stock-api@2.7.3", "get-klines", market_code,
         "--period", "day", "--count", str(count), "--adjust", "qfq", "--source", "tencent"],
        cwd=ROOT, text=True, capture_output=True, timeout=120,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError((proc.stderr or proc.stdout or "stock-api failed")[-500:])
    return json.loads(proc.stdout)


def build_and_publish(path=DATA, history_loader=load_history):
    original = json.loads(Path(path).read_text(encoding='utf-8'))
    payload = json.loads(json.dumps(original))
    codes = payload.get('intersection') or []
    enrichments = payload.setdefault('enrichments', {})
    for code in codes:
        enrichments.setdefault(code, {}).pop('touchstone_metrics', None)
    errors, computed = {}, 0
    for code in codes:
        try:
            rows = history_loader(code, payload['data_as_of'])
            if isinstance(rows, tuple): rows = rows[0]
            if not rows: raise RuntimeError('no history bars')
            dates = [str(x.get('trade_date') or x.get('date'))[:10] for x in rows]
            closes = [x['close'] for x in rows]
            enrichments[code]['touchstone_metrics'] = calculate_touchstone(closes, dates)
            computed += 1
        except Exception as exc:
            errors[code] = f'{type(exc).__name__}: {exc}'
    coverage = {'requested': len(codes), 'computed': computed, 'failed': len(errors)}
    payload['touchstone_contract'] = {'model': 'ZIG15-close-state-machine', 'timeframe': 'daily', 'signal_field': 'touchstone_hit', 'candidate_field': 'candidate_bottom_active', 'formal_field': 'bottom_alert', 'filter_rule': 'candidate_bottom_active OR bottom_alert', 'candidate_definition': 'lowest close anchor on the active down leg after any rebound and before 15% confirmation', 'source': 'local qfq cache → Tencent qfq daily → THS qfq daily → CF BaoStock qfq daily → local BaoStock qfq daily → stock-api Tencent', 'history_start': HISTORY_START, 'bars_requested': HISTORY_BARS, 'minimum_bars': 1, 'errors': errors, 'coverage': coverage}
    if errors or computed != len(codes):
        raise RuntimeError(f'touchstone coverage incomplete: {coverage}')
    target = Path(path)
    fd, name = tempfile.mkstemp(prefix=f'.{target.name}.', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(',', ':'))
            handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)
    return coverage


def main():
    coverage = build_and_publish()
    print(json.dumps({'status': 'ok', **coverage}, ensure_ascii=False))
    return 0


if __name__ == '__main__': raise SystemExit(main())
