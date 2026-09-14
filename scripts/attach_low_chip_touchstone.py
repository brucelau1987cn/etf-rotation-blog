#!/usr/bin/env python3
from __future__ import annotations
import json
import math
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

from etf_bar_cache import connect, get_bars

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
    return {"candidate_bottom_active": False, "candidate_anchor_date": None, "candidate_anchor_close": None, "candidate_rebound_pct": None, "touchstone_hit": False, "bottom_alert": False, "bottom_confirmed": False, "confirmation_date": None, "anchor_date": None, "anchor_close": None, "confirmation_close": None, "rise_pct": None, "last_confirmation_date": None, "coverage_bars": coverage, "model": "ZIG15-close-state-machine", "source": "qfq daily close (local cache/Tencent/BaoStock)"}


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
    payload['touchstone_contract'] = {'model': 'ZIG15-close-state-machine', 'timeframe': 'daily', 'signal_field': 'touchstone_hit', 'candidate_field': 'candidate_bottom_active', 'formal_field': 'bottom_alert', 'filter_rule': 'candidate_bottom_active OR bottom_alert', 'candidate_definition': 'lowest close anchor on the active down leg after any rebound and before 15% confirmation', 'source': 'local qfq cache → Tencent qfq daily → BaoStock qfq daily', 'history_start': HISTORY_START, 'bars_requested': HISTORY_BARS, 'minimum_bars': 1, 'errors': errors, 'coverage': coverage}
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
