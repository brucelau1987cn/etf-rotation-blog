#!/usr/bin/env python3
"""Attach HLP chip top/bottom signals to the current low-chip snapshot."""
from __future__ import annotations
import json
from datetime import timedelta
from pathlib import Path
import pandas as pd
import baostock as bs
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / 'reference' / 'a-stock-data'))
from chip_distribution import chip_distribution

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
LOOKBACK_DAYS = 240

def fetch(symbol: str, start: str, end: str) -> pd.DataFrame:
    code = symbol.split('.')[0]
    market = 'sh' if symbol.endswith('.SH') else 'sz'
    rs = bs.query_history_k_data_plus(f'{market}.{code}', 'date,high,low,close,turn,tradestatus', start_date=start, end_date=end, frequency='d', adjustflag='2')
    rows = []
    while rs.next(): rows.append(rs.get_row_data())
    if rs.error_code != '0' or not rows: return pd.DataFrame()
    df = pd.DataFrame(rows, columns=rs.fields)
    for c in ('high','low','close','turn'): df[c] = pd.to_numeric(df[c], errors='coerce')
    return df[df.tradestatus == '1'].dropna().reset_index(drop=True)

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

def main() -> int:
    payload=json.loads(DATA.read_text(encoding='utf-8'))
    end=payload['data_as_of']; start=(pd.Timestamp(end)-timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%d')
    codes=payload.get('intersection') or {}
    login=bs.login()
    if login.error_code != '0': raise RuntimeError(login.error_msg)
    errors={}; computed=0
    try:
        for symbol in codes:
            try:
                df=fetch(symbol,start,end)
                if len(df)<100: raise RuntimeError(f'only {len(df)} bars')
                payload['enrichments'].setdefault(symbol,{})['hlp_metrics']=calc(df); computed+=1
            except Exception as exc: errors[symbol]=f'{type(exc).__name__}: {exc}'
    finally: bs.logout()
    payload['hlp_contract']={'formula':'WINNER(C)*100; HLP15=MA(HLP,15); HLP60=MA(HLP,60); HLP100=MA(HLP,100)','source':'BaoStock前复权日K+换手率，本地三角分布估算','window_days':LOOKBACK_DAYS,'errors':errors}
    DATA.write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
    print(json.dumps({'status':'ok' if not errors else 'degraded','total':len(codes),'computed':computed,'errors':len(errors)},ensure_ascii=False))
    return 0
if __name__=='__main__': raise SystemExit(main())
