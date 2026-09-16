#!/usr/bin/env python3
"""Attach HLP chip top/bottom signals to the current low-chip snapshot."""
from __future__ import annotations
import json
import os
import tempfile
from datetime import timedelta
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
DATA = ROOT / "public/data/a-low-chip-stocks.json"
LOOKBACK_DAYS = 240
CF_BATCH_SIZE = 5

def _frame(rows) -> pd.DataFrame:
    columns = ('date', 'high', 'low', 'close', 'turn', 'tradestatus')
    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        return frame
    for column in ('high', 'low', 'close', 'turn'):
        frame[column] = pd.to_numeric(frame[column], errors='coerce')
    return frame[frame.tradestatus.astype(str) == '1'].dropna().reset_index(drop=True)


def fetch_cf_batch(symbols: list[str], start: str, end: str, *, client=None) -> dict[str, pd.DataFrame]:
    gateway = client or CFBaoStockClient.from_env()
    fields = ('date', 'high', 'low', 'close', 'turn', 'tradestatus')
    rows_by_symbol = gateway.klines(symbols, start, end, fields=fields)
    return {symbol: _frame(rows_by_symbol.get(symbol) or []) for symbol in symbols}


def fetch_cf_baostock(symbol: str, start: str, end: str, *, client=None) -> pd.DataFrame:
    return fetch_cf_batch([symbol], start, end, client=client)[symbol]


def fetch_local_baostock(symbol: str, start: str, end: str) -> pd.DataFrame:
    import baostock as bs  # type: ignore[import-not-found]

    code = symbol.split('.')[0]
    market = 'sh' if symbol.endswith('.SH') else 'sz'
    login = bs.login()
    if login.error_code != '0':
        raise RuntimeError(login.error_msg)
    try:
        rs = bs.query_history_k_data_plus(f'{market}.{code}', 'date,high,low,close,turn,tradestatus', start_date=start, end_date=end, frequency='d', adjustflag='2')
        rows = []
        while rs.next(): rows.append(rs.get_row_data())
        if rs.error_code != '0':
            raise RuntimeError(rs.error_msg)
        return _frame(rows)
    finally:
        bs.logout()


def fetch(symbol: str, start: str, end: str, *, client=None) -> pd.DataFrame:
    try:
        frame = fetch_cf_baostock(symbol, start, end, client=client)
    except Exception:
        frame = pd.DataFrame()
    return frame if not frame.empty else fetch_local_baostock(symbol, start, end)

def calc(df: pd.DataFrame) -> dict:
    values=[]
    for i in range(len(df)):
        r=chip_distribution(df.iloc[:i+1], grid_size=300, decay=1.0)
        values.append(float(r['profit_ratio']*100))
    s=pd.Series(values)
    h,h15,h60,h100=map(float,(s.iloc[-1],s.tail(15).mean(),s.tail(60).mean(),s.tail(100).mean()))
    signals=[]
    if h < .3: signals.append('低进')
    if h > 99: signals.append('高抛')
    if h < .3 and h15 < 1: signals.append('大底')
    if h60 < 5: signals.append('全仓底')
    if h100 < 5: signals.append('极品底')
    return {'hlp':round(h,4),'hlp15':round(h15,4),'hlp60':round(h60,4),'hlp100':round(h100,4),'chip_signals':signals}

def build_and_publish(path=DATA, history_loader=None, *, client=None) -> dict:
    original=json.loads(Path(path).read_text(encoding='utf-8'))
    payload=json.loads(json.dumps(original))
    end=payload['data_as_of']; start=(pd.Timestamp(end)-timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%d')
    codes=list(payload.get('intersection') or [])
    errors={}; computed=0
    enrichments=payload.setdefault('enrichments', {})
    for symbol in codes:
        enrichments.setdefault(symbol, {}).pop('hlp_metrics', None)

    frames: dict[str, pd.DataFrame] = {}
    if history_loader is not None:
        for symbol in codes:
            try:
                frames[symbol] = history_loader(symbol, start, end)
            except Exception as exc:
                errors[symbol] = f'{type(exc).__name__}: {exc}'
    else:
        gateway = client or CFBaoStockClient.from_env()
        for offset in range(0, len(codes), CF_BATCH_SIZE):
            batch = codes[offset:offset + CF_BATCH_SIZE]
            try:
                frames.update(fetch_cf_batch(batch, start, end, client=gateway))
            except Exception:
                # Isolate a transient/bad symbol so one five-symbol request does
                # not discard the rest of the batch.
                for symbol in batch:
                    try:
                        frames[symbol] = fetch_cf_baostock(symbol, start, end, client=gateway)
                    except Exception as exc:
                        errors[symbol] = f'{type(exc).__name__}: {exc}'

    for symbol in codes:
        try:
            df=frames.get(symbol, pd.DataFrame())
            if df.empty and history_loader is None:
                df=fetch_local_baostock(symbol,start,end)
            if len(df)<100: raise RuntimeError(f'only {len(df)} bars')
            enrichments[symbol]['hlp_metrics']=calc(df); computed+=1
            errors.pop(symbol, None)
        except Exception as exc: errors[symbol]=f'{type(exc).__name__}: {exc}'
    coverage={'requested':len(codes),'computed':computed,'failed':len(errors)}
    payload['hlp_contract']={'formula':'WINNER(C)*100; HLP15=MA(HLP,15); HLP60=MA(HLP,60); HLP100=MA(HLP,100)','source':'CF BaoStock前复权日K+换手率 → 本地BaoStock最终fallback，本地三角分布估算','window_days':LOOKBACK_DAYS,'errors':errors,'coverage':coverage}
    if errors or computed != len(codes):
        raise RuntimeError(f'hlp coverage incomplete: {coverage}')
    target=Path(path)
    fd,name=tempfile.mkstemp(prefix=f'.{target.name}.',dir=target.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            json.dump(payload,handle,ensure_ascii=False,separators=(',',':'))
            handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
        os.replace(name,target)
    finally:
        Path(name).unlink(missing_ok=True)
    return coverage


def main() -> int:
    coverage=build_and_publish()
    print(json.dumps({'status':'ok',**coverage},ensure_ascii=False))
    return 0
if __name__=='__main__': raise SystemExit(main())
