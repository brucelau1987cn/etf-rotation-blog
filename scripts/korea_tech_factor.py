#!/usr/bin/env python3
"""Research-only Korea Tech Factor backtest and shadow snapshot."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import urllib.parse
import urllib.request
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data/local/korea-tech-factor-history.json"
OUTPUT = ROOT / "public/data/korea-tech-factor-shadow.json"
FORWARD_HISTORY = ROOT / "data/local/korea-tech-factor-forward.json"
SH_TZ = ZoneInfo("Asia/Shanghai")

FACTORS = {"kospi":"^KS11","samsung":"005930.KS","sk_hynix":"000660.KS","krw":"KRW=X","soxx":"SOXX"}
TARGETS = {"512480":"512480.SS","561980":"561980.SS","515880":"515880.SS","515000":"515000.SS","513310":"513310.SS","159995":"159995.SZ","588000":"588000.SS"}
NAMES = {"512480":"半导体ETF","561980":"半导体设备ETF","515880":"通信ETF","515000":"科技ETF","513310":"中韩半导体ETF","159995":"芯片ETF","588000":"科创50ETF"}
WEIGHTS = {"hynix_relative":.30,"samsung_relative":.20,"soxx_trend":.25,"kospi_trend":.15,"won_strength":.10}
HORIZONS = (1,5,20)


def now_iso(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

def num(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except (TypeError,ValueError): return None


def fetch(symbol, period="5y"):
    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?interval=1d&range={period}&events=div%2Csplits"
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    obj=json.load(urllib.request.urlopen(req,timeout=25))["chart"]["result"][0]
    q=obj["indicators"]["quote"][0]
    adj=(obj["indicators"].get("adjclose") or [{}])[0].get("adjclose") or q["close"]
    out=[]
    for i,stamp in enumerate(obj.get("timestamp") or []):
        close=num(adj[i] if i<len(adj) else None)
        if close is None: continue
        out.append({"date":datetime.fromtimestamp(stamp,timezone.utc).date().isoformat(),"close":close})
    return out


def return_map(rows,h=20):
    out={}
    for i in range(h,len(rows)):
        a=num(rows[i-h]["close"]); b=num(rows[i]["close"])
        if a and b: out[rows[i]["date"]]=b/a-1
    return out


def relative_map(asset,benchmark,h=20):
    a=return_map(asset,h); b=return_map(benchmark,h)
    return {d:a[d]-b[d] for d in sorted(set(a)&set(b))}


def percentile_map(values,window=252,min_periods=60):
    dates=sorted(values); out={}
    for i,d in enumerate(dates):
        hist=sorted(values[x] for x in dates[max(0,i-window+1):i+1])
        if len(hist)>=min_periods: out[d]=bisect_right(hist,values[d])/len(hist)
    return out


def latest_before(mapping,day):
    dates=sorted(mapping); i=bisect_left(dates,day)-1
    return (dates[i],mapping[dates[i]]) if i>=0 else (None,None)


def component_maps(series):
    raw={
        "hynix_relative":relative_map(series["sk_hynix"],series["kospi"]),
        "samsung_relative":relative_map(series["samsung"],series["kospi"]),
        "soxx_trend":return_map(series["soxx"]),
        "kospi_trend":return_map(series["kospi"]),
        "won_strength":{d:-v for d,v in return_map(series["krw"]).items()},
    }
    return {k:percentile_map(v) for k,v in raw.items()}


def score_for_day(day,maps):
    components={}; cutoffs={}
    for k,m in maps.items():
        date,value=latest_before(m,day)
        if value is not None:
            components[k]=value*100; cutoffs[k]=date
    available=sum(WEIGHTS[k] for k in components)
    if available<.75: return None
    score=sum(components[k]/100*WEIGHTS[k] for k in components)/available*100
    return {"date":day,"score":round(score,4),"regime":"strong" if score>=70 else "weak" if score<=35 else "neutral","components":{k:round(v,4) for k,v in components.items()},"source_cutoffs":cutoffs,"available_weight":round(available,4)}


def factor_history(series,maps):
    calendar=[x["date"] for x in series["target:512480"]]
    return [x for d in calendar if (x:=score_for_day(d,maps))]


def target_rows(series,history):
    f={x["date"]:x for x in history}; out=[]
    for code in TARGETS:
        prices=series[f"target:{code}"]
        for i,p in enumerate(prices):
            if p["date"] not in f or i+max(HORIZONS)>=len(prices): continue
            row={"date":p["date"],"code":code,"score":f[p["date"]]["score"],"regime":f[p["date"]]["regime"]}
            for h in HORIZONS: row[f"t{h}"]=prices[i+h]["close"]/p["close"]-1
            out.append(row)
    return out


def rank(values):
    order=sorted(range(len(values)),key=values.__getitem__); out=[0.]*len(values); i=0
    while i<len(order):
        j=i
        while j+1<len(order) and values[order[j+1]]==values[order[i]]: j+=1
        r=(i+j+2)/2
        for k in range(i,j+1): out[order[k]]=r
        i=j+1
    return out


def corr(x,y):
    if len(x)<3:return None
    mx=statistics.mean(x); my=statistics.mean(y)
    a=[v-mx for v in x]; b=[v-my for v in y]
    den=math.sqrt(sum(v*v for v in a)*sum(v*v for v in b))
    return sum(u*v for u,v in zip(a,b))/den if den else None


def spearman(x,y): return corr(rank(x),rank(y))

def summary(values):
    if not values:return {"n":0,"mean":None,"median":None,"win_rate":None}
    return {"n":len(values),"mean":round(statistics.mean(values),6),"median":round(statistics.median(values),6),"win_rate":round(sum(v>0 for v in values)/len(values),6)}


def backtest(rows):
    result={"horizons":{},"regimes":{},"targets":{}}
    for h in HORIZONS:
        key=f"t{h}"; valid=[r for r in rows if r.get(key) is not None]
        bydate={}
        for r in valid: bydate.setdefault(r["date"],[]).append(r)
        basket=[{"score":v[0]["score"],"ret":statistics.mean(x[key] for x in v)} for v in bydate.values()]
        strong=[r[key] for r in valid if r["regime"]=="strong"]; weak=[r[key] for r in valid if r["regime"]=="weak"]
        result["horizons"][key]={
            "basket_rank_ic":round(spearman([x["score"] for x in basket],[x["ret"] for x in basket]) or 0,6),
            "basket_pearson_ic":round(corr([x["score"] for x in basket],[x["ret"] for x in basket]) or 0,6),
            "pooled_rank_ic":round(spearman([r["score"] for r in valid],[r[key] for r in valid]) or 0,6),
            "strong_minus_weak":round(statistics.mean(strong)-statistics.mean(weak),6) if strong and weak else None,
            "all":summary([r[key] for r in valid]),
        }
        for regime in ("strong","neutral","weak"):
            result["regimes"].setdefault(regime,{})[key]=summary([r[key] for r in valid if r["regime"]==regime])
        for code in TARGETS:
            items=[r for r in valid if r["code"]==code]
            result["targets"].setdefault(code,{})[key]={"all":summary([r[key] for r in items]),"strong":summary([r[key] for r in items if r["regime"]=="strong"]),"weak":summary([r[key] for r in items if r["regime"]=="weak"])}
    return result


def download():
    out={}
    for k,s in FACTORS.items(): out[k]=fetch(s)
    for code,s in TARGETS.items(): out[f"target:{code}"]=fetch(s)
    return out


def write(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n"); tmp.replace(path)


def freeze_forward(path,payload):
    """Append today's research snapshot once; never overwrite prior frozen rows."""
    if path.exists(): history=json.loads(path.read_text())
    else: history={"version":1,"snapshots":[]}
    snapshots=history.setdefault("snapshots",[]); day=payload["factor"]["decision_date"]
    if not any(x.get("decision_date")==day for x in snapshots):
        snapshots.append({
            "decision_date":day,"frozen_at":payload["generated_at"],"score":payload["factor"]["score"],
            "regime":payload["factor"]["regime"],"components":payload["factor"]["components"],
            "source_cutoffs":payload["factor"]["source_cutoffs"],"outcomes":{},
        })
        write(path,history)
    return history


def build(series):
    maps=component_maps(series); history=factor_history(series,maps); rows=target_rows(series,history)
    decision=datetime.now(SH_TZ).date().isoformat(); latest=score_for_day(decision,maps) or history[-1]
    return {
        "version":1,"generated_at":now_iso(),"mode":"shadow_research_only","production_weights_changed":False,
        "point_in_time":"for A-share date D, every external component uses the latest completed observation strictly before D",
        "factor":{"name":"Korea Tech Factor","decision_date":decision,"score":latest["score"],"regime":latest["regime"],"components":latest["components"],"source_cutoffs":latest["source_cutoffs"],"weights":WEIGHTS},
        "universe":[{"code":c,"name":NAMES[c]} for c in TARGETS],"backtest":backtest(rows),
        "coverage":{"factor_start":history[0]["date"],"factor_end":history[-1]["date"],"factor_days":len(history),"observation_rows":len(rows)},
        "history_tail":history[-40:],
        "notes":["First version uses liquid market proxies. Official monthly Korean semiconductor exports will be added only after release-date alignment.","Common factor IC is a time-series basket/pooled IC, not a same-day cross-sectional stock-picking IC.","Research-only; formal rankings, key levels, positions and ambush eligibility remain unchanged."],
    }


def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--refresh",action="store_true"); p.add_argument("--cache",type=Path,default=CACHE); p.add_argument("--output",type=Path,default=OUTPUT); p.add_argument("--forward-history",type=Path,default=FORWARD_HISTORY); p.add_argument("--no-freeze",action="store_true"); args=p.parse_args(argv)
    if args.refresh or not args.cache.exists():
        series=download(); write(args.cache,{"generated_at":now_iso(),"series":series})
    else: series=json.loads(args.cache.read_text())["series"]
    payload=build(series)
    if not args.no_freeze:
        forward=freeze_forward(args.forward_history,payload)
        payload["forward_test"]={"frozen_samples":len(forward.get("snapshots",[])),"history_path":"data/local/korea-tech-factor-forward.json"}
    write(args.output,payload); f=payload["factor"]
    print(f"Korea Tech Factor {f['score']:.1f} ({f['regime']}) | decision {f['decision_date']} | rows {payload['coverage']['observation_rows']}")

if __name__=="__main__": main()
