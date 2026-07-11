#!/usr/bin/env python3
"""Deterministic, stateful A-share/US ETF paper trader (stdlib only)."""
from __future__ import annotations
import argparse, copy, datetime as dt, fcntl, json, math, os, tempfile, urllib.parse, urllib.request
from contextlib import contextmanager
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TZ = {"A": ZoneInfo("Asia/Shanghai"), "US": ZoneInfo("America/New_York")}
DEFAULT_STATE = Path("/root/.hermes/state/etf-paper-trading.json")
EXPORT = ROOT / "public/data/paper-trading.json"
SOURCES = {"A": ROOT / "public/data/garden-recommendations.json", "US": ROOT / "public/data/us-etf-garden.json"}
CONFIG = {"A": {"initial": 150000.0, "currency": "CNY", "reserve": .20, "lot": 100}, "US": {"initial": 20000.0, "currency": "USD", "reserve": .15, "lot": 1}}
DISCLAIMER = "模拟交易仅用于验证规则，不构成投资建议；行情可能延迟，滑点与实际成交存在差异。"


def now_iso(value=None):
    if value:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc).isoformat()
    return dt.datetime.now(dt.timezone.utc).isoformat()

def new_account(market, ts=None):
    c=CONFIG[market]
    inception=market_day(market, now_iso(ts))
    return {"market":market,"currency":c["currency"],"initial_capital":c["initial"],"inception_date":inception,"cash":c["initial"],"positions":{},"equity":c["initial"],"realized_pnl":0.0,"unrealized_pnl":0.0,"daily_return":0.0,"cumulative_return":0.0,"max_drawdown":0.0,"peak_equity":c["initial"],"cash_ratio":1.0,"history":[],"events":[],"pending_signals":[],"benchmark":{"symbol":"510300" if market=="A" else "SPY","cumulative_return":None,"excess_return":None},"processed_event_ids":[],"consumed_signal_ids":[],"armed_signals":{}}

def parse_now(value=None):
    return dt.datetime.fromisoformat(now_iso(value))

def market_day(market, value=None):
    return parse_now(value).astimezone(TZ[market]).date().isoformat()

def intraday_window(market, value=None):
    local=parse_now(value).astimezone(TZ[market]); t=local.time().replace(tzinfo=None)
    if local.weekday() >= 5: return False
    if market=="A": return dt.time(9,30) <= t <= dt.time(11,30) or dt.time(13,0) <= t <= dt.time(15,0)
    return dt.time(9,30) <= t <= dt.time(16,0)

def quote_day(market, bar):
    stamp=bar.get("timestamp")
    if market=="A":
        digits="".join(ch for ch in str(stamp or "") if ch.isdigit())
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits)>=8 else None
    try: return dt.datetime.fromtimestamp(float(stamp),dt.timezone.utc).astimezone(TZ[market]).date().isoformat()
    except (TypeError,ValueError,OSError): return None

def quote_age_seconds(market, bar, now):
    try:
        if market=="A":
            digits="".join(ch for ch in str(bar.get("timestamp") or "") if ch.isdigit())
            local=dt.datetime.strptime(digits[:14],"%Y%m%d%H%M%S").replace(tzinfo=TZ[market])
        else: local=dt.datetime.fromtimestamp(float(bar["timestamp"]),dt.timezone.utc)
        return max(0.0,(parse_now(now)-local.astimezone(dt.timezone.utc)).total_seconds())
    except (TypeError,ValueError,OSError): return float("inf")


def new_state(ts=None):
    return {"version":1,"updated_at":now_iso(ts),"accounts":{"A":new_account("A",ts),"US":new_account("US",ts)},"disclaimer":DISCLAIMER}

def costs(market, side, price, qty):
    gross=price*qty
    commission=max(5.0,gross*.00025) if market=="A" else 1.0
    slippage=gross*.0005
    # Buys pay upward slippage; sells receive downward slippage. Return total adverse cost.
    return round(commission+slippage, 6)

def size_order(market, equity, cash, price, position_count=0):
    if price<=0 or position_count>=10: return 0
    c=CONFIG[market]; spend=min(equity*.10, max(0.0,cash-equity*c["reserve"]))
    lot=c["lot"]
    qty=int(spend/(price*lot))*lot
    while qty>0 and price*qty+costs(market,"buy",price,qty)>spend: qty-=lot
    return max(0,qty)

def touched(bar, level):
    return level is not None and float(bar.get("low",bar["price"])) <= float(level) <= float(bar.get("high",bar["price"]))

def event_id(market, symbol, action, bar, reason):
    stamp=bar.get("timestamp") or bar.get("date") or "unknown"
    return f"{market}:{symbol}:{action}:{stamp}:{reason}"

def execute(account, symbol, name, side, price, qty, ts, reason, eid, target=None, stop=None, level_basis=None, signal_date=None):
    if eid in account["processed_event_ids"]: return None
    fee=costs(account["market"],side,price,qty); gross=price*qty
    if side=="buy":
        if gross+fee>account["cash"]+1e-8: return None
        account["cash"]-=gross+fee
        account["positions"][symbol]={"symbol":symbol,"name":name,"quantity":qty,"entry_price":price,"entry_cost":fee,"entry_at":ts,"target":target,"stop":stop,"level_basis":level_basis,"signal_date":signal_date,"last_price":price}
    else:
        pos=account["positions"].pop(symbol); account["cash"]+=gross-fee
        account["realized_pnl"]+=(price-pos["entry_price"])*qty-pos["entry_cost"]-fee
    event={"id":eid,"timestamp":ts,"market":account["market"],"symbol":symbol,"name":name,"side":side,"quantity":qty,"price":round(price,6),"cost":fee,"reason":reason}
    account["events"].append(event); account["processed_event_ids"].append(eid)
    account["processed_event_ids"]=account["processed_event_ids"][-5000:]
    return event

def normalize_signals(market,data):
    source_date=data.get("date")
    if market=="A":
        buys=[dict(x,symbol=x.get("code"),kind="plant",_source_date=source_date) for x in data.get("plant",[]) if x.get("status")=="种花"]
        sells=[dict(x,symbol=x.get("code"),kind="harvest",_source_date=source_date) for x in data.get("harvest",[]) if x.get("status")=="摘花"]
    else:
        fs=data.get("flower_signals",{})
        buys=[dict(x,kind="plant",_source_date=source_date) for x in fs.get("plant",[]) if x.get("signal")=="种花"]
        sells=[dict(x,kind="exit",_source_date=source_date) for x in fs.get("exit",[]) if x.get("signal")=="失效退出"]+[dict(x,kind="harvest",_source_date=source_date) for x in fs.get("harvest",[]) if x.get("signal")=="摘花"]
    for x in buys+sells:
        x["_signal_id"]=f"{market}:{x.get('symbol')}:{x.get('_source_date')}:{x.get('kind')}:{x.get('support')}:{x.get('target')}:{x.get('stop')}"
    return buys,sells

def valid_signals(market, signals, today):
    """A signals are explicitly dated for that session; US close signals live for the next session only."""
    out=[]; now_day=dt.datetime.strptime(today,"%Y-%m-%d").date()
    for sig in signals:
        try: source=dt.datetime.strptime(str(sig.get("_source_date")),"%Y-%m-%d").date()
        except (TypeError,ValueError): continue
        age=(now_day-source).days
        if (market=="A" and age==0) or (market=="US" and 1 <= age <= 4): out.append(sig)
    return out

def signal_key(market, sig):
    return sig.get("_signal_id") or f"{market}:{sig.get('symbol')}:{sig.get('_source_date') or sig.get('price_date') or sig.get('trade_date')}:{sig.get('kind','plant')}:{sig.get('support')}:{sig.get('target')}:{sig.get('stop')}"


def eligible_buys(account, buys, quotes, ts):
    """Arm new signals first so an earlier session low can never create a retrospective fill."""
    armed=account.setdefault("armed_signals",{}); consumed=set(account.setdefault("consumed_signal_ids",[])); eligible=[]; live={x["symbol"] for x in buys}
    for symbol in list(armed):
        if symbol not in live or symbol in account["positions"]: armed.pop(symbol,None)
    for sig in buys:
        symbol=sig["symbol"]; bar=quotes.get(symbol); trigger=sig.get("support",sig.get("action_level")); signal_id=signal_key(account["market"],sig)
        if not bar or trigger is None or symbol in account["positions"] or not signal_id or signal_id in consumed: continue
        fingerprint=signal_id
        low=float(bar.get("low",bar["price"])); current=float(bar["price"]); item=armed.get(symbol)
        if not item or item.get("fingerprint")!=fingerprint:
            armed[symbol]={"fingerprint":fingerprint,"armed_at":ts,"baseline_low":low}
            if current <= float(trigger): eligible.append(sig)
            continue
        prior_low=float(item.get("baseline_low",low)); item["baseline_low"]=min(prior_low,low)
        if current <= float(trigger) or (low < prior_low and low <= float(trigger)): eligible.append(sig)
    return eligible


def process_bar(account, signals, quotes, ts):
    """Risk exits first, then formal source sells, then buys. Pure and idempotent."""
    trades=[]; buys,sells=signals; sellmap={x["symbol"]:x for x in sells}; exited=set(); consumed=set(account.setdefault("consumed_signal_ids",[]))
    for symbol,pos in list(account["positions"].items()):
        bar=quotes.get(symbol)
        if not bar: continue
        pos["last_price"]=float(bar["price"])
        # Conservative same-bar rule: stop always wins over target/source harvest.
        reason=px=None
        if pos.get("stop") is not None and float(bar.get("low",bar["price"])) <= float(pos["stop"]): reason,px="stop",min(float(pos["stop"]),float(bar["price"]))
        elif pos.get("target") is not None and float(bar.get("high",bar["price"])) >= float(pos["target"]): reason,px="target",max(float(pos["target"]),float(bar["price"]))
        elif symbol in sellmap: reason,px=sellmap[symbol].get("kind","signal"),float(bar["price"])
        if reason:
            eid=event_id(account["market"],symbol,"sell",bar,reason)
            ev=execute(account,symbol,pos["name"],"sell",px,pos["quantity"],ts,reason,eid)
            if ev: trades.append(ev); exited.add(symbol)
    for sig in buys:
        symbol=sig["symbol"]; bar=quotes.get(symbol); signal_id=signal_key(account["market"],sig)
        if not bar or symbol in account["positions"] or symbol in exited or not signal_id or signal_id in consumed: continue
        trigger=sig.get("support",sig.get("action_level"))
        if trigger is None or float(bar.get("low",bar["price"])) > float(trigger): continue
        price=min(float(trigger),float(bar["price"])); qty=size_order(account["market"],account.get("equity",account["cash"]),account["cash"],price,len(account["positions"]))
        if not qty: continue
        eid=event_id(account["market"],symbol,"buy",bar,"plant")
        ev=execute(account,symbol,sig.get("name",symbol),"buy",price,qty,ts,"plant",eid,sig.get("target"),sig.get("stop"),sig.get("level_basis") or sig.get("trigger_price_basis"),sig.get("price_date") or sig.get("trade_date"))
        if ev:
            trades.append(ev); consumed.add(signal_id); account["consumed_signal_ids"]=list(consumed)[-5000:]; account.setdefault("armed_signals",{}).pop(symbol,None)
    return trades

def mark(account, quotes, ts, close=False):
    value=0.0; basis=0.0
    for symbol,p in account["positions"].items():
        if symbol in quotes: p["last_price"]=float(quotes[symbol]["price"])
        value+=p["quantity"]*p["last_price"]; basis+=p["quantity"]*p["entry_price"]+p["entry_cost"]
    equity=account["cash"]+value; account["equity"]=round(equity,6); account["unrealized_pnl"]=round(value-basis,6); account["cash_ratio"]=round(account["cash"]/equity,8) if equity else 0.0
    account["cumulative_return"]=round(equity/account["initial_capital"]-1,8)
    day=market_day(account["market"],ts)
    prior_rows=[row for row in account["history"] if row["date"] != day]
    prior=prior_rows[-1]["equity"] if prior_rows else account["initial_capital"]
    account["daily_return"]=round(equity/prior-1,8)
    if close:
        account["peak_equity"]=max(account.get("peak_equity",equity),equity)
        dd=equity/account["peak_equity"]-1; account["max_drawdown"]=round(min(account.get("max_drawdown",0),dd),8)
        row={"date":day,"equity":round(equity,6),"cash":round(account["cash"],6),"daily_return":account["daily_return"],"cumulative_return":account["cumulative_return"],"max_drawdown":account["max_drawdown"]}
        if account["history"] and account["history"][-1]["date"]==day: account["history"][-1]=row
        else: account["history"].append(row)

def fetch_a(symbols):
    keys=[("sh" if s.startswith(("5","6")) else "sz")+s for s in symbols]
    if not keys:return {}
    req=urllib.request.Request("https://qt.gtimg.cn/q="+",".join(keys),headers={"Referer":"https://finance.qq.com/","User-Agent":"Mozilla/5.0"})
    text=urllib.request.urlopen(req,timeout=12).read().decode("gbk","ignore"); out={}
    for row in text.split(";"):
        if '="' not in row: continue
        fields=row.split('="',1)[1].rstrip('"').split("~")
        if len(fields)>34:
            sym=fields[2]; price=float(fields[3]); out[sym]={"price":price,"high":price,"low":price,"timestamp":fields[30]}
    return out

def fetch_us(symbols):
    out={}
    for sym in symbols:
        url=f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?interval=5m&range=1d"
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"}); obj=json.load(urllib.request.urlopen(req,timeout=12))["chart"]["result"][0]
        q=obj["indicators"]["quote"][0]; valid=[i for i,x in enumerate(q["close"]) if x is not None and q["high"][i] is not None and q["low"][i] is not None]
        if valid:
            i=valid[-1]; out[sym]={"price":q["close"][i],"high":q["high"][i],"low":q["low"][i],"timestamp":obj["timestamp"][i]}
    return out

@contextmanager
def locked(path):
    path.parent.mkdir(parents=True,exist_ok=True); lock=open(str(path)+".lock","a+")
    fcntl.flock(lock,fcntl.LOCK_EX)
    try: yield
    finally: fcntl.flock(lock,fcntl.LOCK_UN); lock.close()
def load(path): return json.loads(path.read_text()) if path.exists() else new_state()
def atomic_write(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=".paper-",text=True)
    try:
        with os.fdopen(fd,"w") as f: json.dump(obj,f,ensure_ascii=False,indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
def public_view(state):
    out=copy.deepcopy(state)
    for a in out["accounts"].values():
        a.pop("processed_event_ids",None)
        a.pop("armed_signals",None)
        a.pop("consumed_signal_ids",None)
    return out

def self_test():
    assert costs("A","buy",1,100)==5.05 and size_order("A",150000,150000,1)==14900
    a=new_account("A"); sig=([{"symbol":"510000","name":"x","support":1,"target":1.1,"stop":.9}],[]); q={"510000":{"price":1,"low":.99,"high":1.01,"timestamp":"t"}}
    assert len(process_bar(a,sig,q,"2020-01-01T00:00:00+00:00"))==1 and not process_bar(a,sig,q,"2020-01-01T00:00:00+00:00")
    print("paper_trade_runner self-test: OK")

def format_trade_notice(market, trades, account):
    if not trades: return ""
    unit="¥" if market=="A" else "$"; lines=[f"{'🇨🇳 A股' if market=='A' else '🇺🇸 美股'}ETF虚拟交易"]
    for x in trades:
        action="伏击" if x["side"]=="buy" else ("撤退" if x["reason"]=="stop" else "兑现")
        reason={"plant":"正式伏击信号","target":"触及兑现位","stop":"跌破防守线"}.get(x["reason"],x["reason"])
        icon="🎯" if x["side"]=="buy" else ("🛑" if x["reason"]=="stop" else "✅")
        lines.append(f"{icon} {action} {x['symbol']}｜{x['quantity']}份 × {unit}{x['price']:.3f}｜费用 {unit}{x['cost']:.2f}｜{reason}")
    lines.append(f"账户权益 {unit}{account['equity']:,.2f}｜现金占比 {account.get('cash_ratio',1)*100:.1f}%")
    lines.append("⚠️ 虚拟交易，不是实盘订单。")
    return "\n".join(lines)

def format_close_notice(market, account, trades):
    unit="¥" if market=="A" else "$"; title="A股" if market=="A" else "美股"; day=account.get("history",[{}])[-1].get("date") if account.get("history") else None
    today_trades=sum(1 for x in account.get("events",[]) if market_day(market,x.get("timestamp"))==day) if day else len(trades)
    return (f"📊 {title}ETF虚拟账户收盘｜权益 {unit}{account['equity']:,.2f}｜"
            f"当日 {account['daily_return']*100:+.2f}%｜累计 {account['cumulative_return']*100:+.2f}%｜"
            f"最大回撤 {account['max_drawdown']*100:.2f}%｜持仓 {len(account['positions'])}｜今日成交 {today_trades}\n"
            "⚠️ 虚拟交易，不是实盘订单。")

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--market",choices=["A","US"]); p.add_argument("--mode",choices=["init","intraday","close"]); p.add_argument("--state",type=Path,default=DEFAULT_STATE); p.add_argument("--now"); p.add_argument("--dry-run",action="store_true"); p.add_argument("--self-test",action="store_true")
    args=p.parse_args(argv)
    if args.self_test:return self_test()
    if not args.market or not args.mode:p.error("--market and --mode are required")
    if args.mode=="intraday" and not intraday_window(args.market,args.now): return
    ts=now_iso(args.now)
    with locked(args.state):
        state=load(args.state)
        if args.mode=="init":
            state["accounts"][args.market]=new_account(args.market,ts); trades=[]
        else:
            data=json.loads(SOURCES[args.market].read_text()); raw_signals=normalize_signals(args.market,data)
            account=state["accounts"][args.market]
            account.setdefault("consumed_signal_ids",[]); account.setdefault("armed_signals",{}); account.setdefault("processed_event_ids",[])
            today=market_day(args.market,ts)
            signals=(valid_signals(args.market,raw_signals[0],today),valid_signals(args.market,raw_signals[1],today))
            held=set(account["positions"])
            symbols=held if args.mode=="close" else ({x["symbol"] for group in signals for x in group}|held)
            quotes=(fetch_a if args.market=="A" else fetch_us)(sorted(symbols))
            max_age=(1200 if args.market=="A" else 3600) if args.mode=="close" else (180 if args.market=="A" else 900)
            quotes={symbol:bar for symbol,bar in quotes.items() if quote_day(args.market,bar)==today and quote_age_seconds(args.market,bar,ts)<=max_age}
            # A close snapshot is valid only when every held position has a fresh quote.
            if held-set(quotes): return
            if args.mode=="intraday":
                trade_signals=(eligible_buys(account,signals[0],quotes,ts),signals[1])
                trades=process_bar(account,trade_signals,quotes,ts)
            else: trades=[]
            mark(account,quotes,ts,args.mode=="close")
            held=set(account["positions"])
            account["pending_signals"]=[{"symbol":x["symbol"],"name":x.get("name",x["symbol"]),"support":x.get("support"),"target":x.get("target"),"stop":x.get("stop"),"signal_date":x.get("_source_date")} for x in signals[0] if x["symbol"] not in held and x.get("_signal_id") not in account["consumed_signal_ids"]]
        state["updated_at"]=ts
        if not args.dry_run:
            atomic_write(args.state,state)
            if args.mode in {"init","close"}: atomic_write(EXPORT,public_view(state))
    account=state["accounts"][args.market]
    visible=trades if args.market=="A" or args.mode=="close" else [x for x in trades if x["side"]=="sell" and x["reason"]=="stop"]
    message=format_close_notice(args.market,account,trades) if args.mode=="close" else format_trade_notice(args.market,visible,account)
    if message: print(message)
if __name__=="__main__": main()
