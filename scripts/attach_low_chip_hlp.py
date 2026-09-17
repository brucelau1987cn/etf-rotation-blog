#!/usr/bin/env python3
"""Attach HLP metrics, preferring official THS chip curves."""
from __future__ import annotations

import contextlib
import fcntl
import io
import json
import math
import os
import random
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / 'reference' / 'a-stock-data'))
from chip_distribution import chip_distribution
try:
    from cf_baostock_client import CFBaoStockClient
except ModuleNotFoundError:
    from scripts.cf_baostock_client import CFBaoStockClient

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'public/data/a-low-chip-stocks.json'
LOOKBACK_DAYS = 240
THS_LOOKBACK_DAYS = 365
CF_BATCH_SIZE = 5
LOCAL_LOCK = Path('/root/.hermes/state/baostock-local.lock')
SERIES_CACHE = Path('/root/.hermes/state/low-chip-hlp-series.json')
THS_CHIP_URL = 'https://dq.10jqka.com.cn/fuyao/chip_shape_stock_selection/stock/v1/chip_list'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


def _frame(rows) -> pd.DataFrame:
    columns = ('date', 'high', 'low', 'close', 'turn', 'tradestatus')
    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        return frame
    for column in ('high', 'low', 'close', 'turn'):
        frame[column] = pd.to_numeric(frame[column], errors='coerce')
    return frame[frame.tradestatus.astype(str) == '1'].dropna().reset_index(drop=True)


def _timestamp_ms(day: str) -> int:
    return int(datetime.strptime(day, '%Y-%m-%d').replace(hour=0, minute=0).timestamp() * 1000)


def fetch_ths_chip_profit_series(symbol: str, start: str, end: str, *, opener=urllib.request.urlopen) -> list[dict]:
    code = symbol.split('.')[0]
    market = '17' if symbol.endswith('.SH') else '33'
    url = (
        f'{THS_CHIP_URL}?chip_type=all&stock_code={code}&stock_market={market}'
        f'&start_date={_timestamp_ms(start)}&end_date={_timestamp_ms(end)}'
    )
    request = urllib.request.Request(url, headers=UA)
    with opener(request, timeout=20) as response:
        payload = json.loads(response.read())
    if payload.get('status_code') != 0:
        raise RuntimeError(f'THS chip status={payload.get("status_code")}')
    entries = (payload.get('data') or {}).get('list') or {}
    result = []
    for compact_day in sorted(entries):
        item = entries.get(compact_day) or {}
        summary = item.get('summary') or {}
        curve = (item.get('curve_data') or {}).get('list') or []
        try:
            close = float(summary.get('close_price'))
            average_cost = float(summary.get('average_cost'))
        except (TypeError, ValueError):
            continue
        total = below = 0.0
        for row in curve:
            try:
                price = float(row.get('price'))
                weight = float(row.get('jeton'))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(price) or not math.isfinite(weight) or weight < 0:
                continue
            total += weight
            if price <= close:
                below += weight
        if total <= 0 or not math.isfinite(close) or close <= 0:
            continue
        day = f'{compact_day[:4]}-{compact_day[4:6]}-{compact_day[6:8]}'
        if start <= day <= end:
            result.append({
                'date': day,
                'profit_ratio': round(below / total * 100, 6),
                'close': close,
                'average_cost': average_cost,
            })
    return result


def load_series_cache(path: Path | None = None) -> dict[str, list[dict]]:
    path = path or SERIES_CACHE
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    if payload.get('schema_version') != 'low-chip-hlp-series-v1':
        return {}
    return payload.get('symbols') if isinstance(payload.get('symbols'), dict) else {}


def merge_profit_series(cached: list[dict], fresh: list[dict], limit: int = 120) -> list[dict]:
    merged = {}
    for row in [*(cached or []), *(fresh or [])]:
        day = str(row.get('date') or '')
        try:
            profit = float(row.get('profit_ratio'))
            close = float(row.get('close'))
            average_cost = float(row.get('average_cost'))
        except (TypeError, ValueError):
            continue
        if len(day) == 10 and math.isfinite(profit) and 0 <= profit <= 100:
            merged[day] = {'date': day, 'profit_ratio': profit, 'close': close, 'average_cost': average_cost}
    return [merged[day] for day in sorted(merged)][-limit:]


def save_series_cache(cache: dict[str, list[dict]], path: Path | None = None) -> None:
    path = path or SERIES_CACHE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'schema_version': 'low-chip-hlp-series-v1', 'symbols': cache}
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(',', ':'))
            handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def calc_profit_series(rows: list[dict]) -> dict:
    if len(rows) < 100:
        raise RuntimeError(f'only {len(rows)} THS chip sessions')
    values = pd.Series([float(row['profit_ratio']) for row in rows])
    h, h15, h60, h100 = map(float, (values.iloc[-1], values.tail(15).mean(), values.tail(60).mean(), values.tail(100).mean()))
    signals = []
    if h < .3: signals.append('低进')
    if h > 99: signals.append('高抛')
    if h < .3 and h15 < 1: signals.append('大底')
    if h60 < 5: signals.append('全仓底')
    if h100 < 5: signals.append('极品底')
    latest = rows[-1]
    return {
        'hlp': round(h, 4), 'hlp15': round(h15, 4), 'hlp60': round(h60, 4),
        'hlp100': round(h100, 4), 'chip_signals': signals,
        'average_cost': round(float(latest['average_cost']), 4),
        'close': round(float(latest['close']), 4),
        'source': 'ths-chip-list', 'coverage_sessions': len(rows),
    }


def fetch_cf_batch(symbols: list[str], start: str, end: str, *, client=None) -> dict[str, pd.DataFrame]:
    gateway = client or CFBaoStockClient.from_env()
    fields = ('date', 'high', 'low', 'close', 'turn', 'tradestatus')
    rows_by_symbol = gateway.klines(symbols, start, end, fields=fields)
    frames = {symbol: _frame(rows_by_symbol.get(symbol) or []) for symbol in symbols}
    for frame in frames.values():
        frame.attrs['source'] = 'cf-baostock'
    return frames


def fetch_cf_baostock(symbol: str, start: str, end: str, *, client=None) -> pd.DataFrame:
    return fetch_cf_batch([symbol], start, end, client=client)[symbol]


def fetch_local_baostock(symbol: str, start: str, end: str) -> pd.DataFrame:
    frames, _errors = fetch_local_batch([symbol], start, end)
    return frames.get(symbol, pd.DataFrame())


def fetch_local_batch(symbols: list[str], start: str, end: str, *, sleeper=time.sleep) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """One locked BaoStock session; preserve per-symbol successes."""
    import baostock as bs  # type: ignore[import-not-found]

    LOCAL_LOCK.parent.mkdir(parents=True, exist_ok=True)
    results: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    with LOCAL_LOCK.open('a+') as handle, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        fcntl.flock(handle, fcntl.LOCK_EX)
        login = bs.login()
        if login.error_code != '0':
            raise RuntimeError(login.error_msg)
        try:
            for index, symbol in enumerate(symbols, 1):
                try:
                    code = symbol.split('.')[0]
                    market = 'sh' if symbol.endswith('.SH') else 'sz'
                    rs = bs.query_history_k_data_plus(
                        f'{market}.{code}', 'date,high,low,close,turn,tradestatus',
                        start_date=start, end_date=end, frequency='d', adjustflag='2',
                    )
                    rows = []
                    while rs.next():
                        rows.append(rs.get_row_data())
                    if rs.error_code != '0':
                        raise RuntimeError(rs.error_msg)
                    frame = _frame(rows)
                    if frame.empty:
                        raise RuntimeError('empty history')
                    frame.attrs['source'] = 'local-baostock'
                    results[symbol] = frame
                except Exception as exc:
                    errors[symbol] = f'{type(exc).__name__}: {exc}'
                if index < len(symbols):
                    sleeper(random.uniform(0.35, 0.60))
        finally:
            bs.logout()
            fcntl.flock(handle, fcntl.LOCK_UN)
    return results, errors


def fetch(symbol: str, start: str, end: str, *, client=None) -> pd.DataFrame:
    try:
        frame = fetch_cf_baostock(symbol, start, end, client=client)
    except Exception:
        frame = pd.DataFrame()
    return frame if not frame.empty else fetch_local_baostock(symbol, start, end)


def calc(df: pd.DataFrame) -> dict:
    values = []
    for i in range(len(df)):
        result = chip_distribution(df.iloc[:i + 1], grid_size=300, decay=1.0)
        values.append(float(result['profit_ratio'] * 100))
    series = pd.Series(values)
    h, h15, h60, h100 = map(float, (series.iloc[-1], series.tail(15).mean(), series.tail(60).mean(), series.tail(100).mean()))
    signals = []
    if h < .3: signals.append('低进')
    if h > 99: signals.append('高抛')
    if h < .3 and h15 < 1: signals.append('大底')
    if h60 < 5: signals.append('全仓底')
    if h100 < 5: signals.append('极品底')
    return {'hlp': round(h, 4), 'hlp15': round(h15, 4), 'hlp60': round(h60, 4), 'hlp100': round(h100, 4), 'chip_signals': signals, 'source': str(df.attrs.get('source') or 'baostock-cyq')}


def _baostock_fallback(codes: list[str], start: str, end: str, *, client=None) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    frames: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    try:
        local_frames, local_errors = fetch_local_batch(codes, start, end)
        frames.update(local_frames)
        errors.update(local_errors)
    except Exception as exc:
        errors['local-baostock'] = f'{type(exc).__name__}: {exc}'
    missing = [symbol for symbol in codes if frames.get(symbol, pd.DataFrame()).empty]
    if missing:
        gateway = client or CFBaoStockClient.from_env()
        for offset in range(0, len(missing), CF_BATCH_SIZE):
            batch = missing[offset:offset + CF_BATCH_SIZE]
            try:
                frames.update(fetch_cf_batch(batch, start, end, client=gateway))
            except Exception as exc:
                for symbol in batch:
                    errors[symbol] = f'{type(exc).__name__}: {exc}'
    return frames, errors


def build_and_publish(path=DATA, history_loader=None, *, client=None) -> dict:
    original = json.loads(Path(path).read_text(encoding='utf-8'))
    payload = json.loads(json.dumps(original))
    end = payload['data_as_of']
    ths_start = (pd.Timestamp(end) - timedelta(days=THS_LOOKBACK_DAYS)).strftime('%Y-%m-%d')
    bs_start = (pd.Timestamp(end) - timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%d')
    codes = list(payload.get('intersection') or [])
    errors: dict[str, str] = {}
    computed = 0
    enrichments = payload.setdefault('enrichments', {})
    for symbol in codes:
        enrichments.setdefault(symbol, {}).pop('hlp_metrics', None)

    pending: list[str] = []
    ths_series: dict[str, list[dict]] = {}
    series_cache = load_series_cache()
    cache_changed = False
    if history_loader is None:
        for index, symbol in enumerate(codes, 1):
            try:
                fresh = fetch_ths_chip_profit_series(symbol, ths_start, end)
                if not fresh:
                    raise RuntimeError('empty THS chip series')
                if fresh[-1].get('date') != end:
                    raise RuntimeError(f'stale THS chip series: last={fresh[-1].get("date")} expected={end}')
                rows = merge_profit_series(series_cache.get(symbol, []), fresh)
                if rows != series_cache.get(symbol, []):
                    series_cache[symbol] = rows
                    cache_changed = True
                if len(rows) < 100:
                    raise RuntimeError(f'only {len(rows)} THS chip sessions')
                ths_series[symbol] = rows
            except Exception as exc:
                errors[symbol] = f'THS {type(exc).__name__}: {exc}'
                pending.append(symbol)
            if index % 5 == 0 or index == len(codes):
                print(f'[ths-chip] {index}/{len(codes)} ready={len(ths_series)} fallback={len(pending)}', flush=True)
            if index < len(codes):
                time.sleep(0.12)
    else:
        pending = list(codes)

    if cache_changed:
        save_series_cache(series_cache)

    fallback_frames: dict[str, pd.DataFrame] = {}
    if pending:
        if history_loader is not None:
            for symbol in pending:
                try:
                    fallback_frames[symbol] = history_loader(symbol, bs_start, end)
                except Exception as exc:
                    errors[symbol] = f'{type(exc).__name__}: {exc}'
        else:
            fallback_frames, fallback_errors = _baostock_fallback(pending, bs_start, end, client=client)
            errors.update(fallback_errors)

    for symbol in codes:
        try:
            if symbol in ths_series:
                metrics = calc_profit_series(ths_series[symbol])
            else:
                frame = fallback_frames.get(symbol, pd.DataFrame())
                if len(frame) < 100:
                    raise RuntimeError(f'only {len(frame)} fallback bars')
                metrics = calc(frame)
            enrichments[symbol]['hlp_metrics'] = metrics
            computed += 1
            errors.pop(symbol, None)
        except Exception as exc:
            errors[symbol] = f'{type(exc).__name__}: {exc}'

    coverage = {'requested': len(codes), 'computed': computed, 'failed': len([key for key in errors if key in codes])}
    payload['hlp_contract'] = {
        'formula': 'HLP=THS closing_profit; HLP15/60/100=MA(HLP,N)',
        'source': '同花顺官方 chip-list → 本地BaoStock单会话 → CF BaoStock补缺',
        'window_days': THS_LOOKBACK_DAYS, 'errors': errors, 'coverage': coverage,
    }
    if coverage['failed'] or computed != len(codes):
        raise RuntimeError(f'hlp coverage incomplete: {coverage}')
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


def main() -> int:
    coverage = build_and_publish()
    print(json.dumps({'status': 'ok', **coverage}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
