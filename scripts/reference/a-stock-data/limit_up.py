#!/usr/bin/env python3
"""打板层 — 涨停/炸板/跌停/昨涨停四池 + 同花顺涨停揭秘 + 连板梯队 + 重点监控 + 日内异动。

来源：https://github.com/simonlin1212/a-stock-data（SKILL.md Layer 8，V3.6.0）
License：Apache 2.0（原项目），抽取整合为自包含脚本，供参考/对比，未接入生产。

数据源：东财 push2ex（涨停四池 / 重点监控 / 日内异动，零鉴权）+ 同花顺 data.10jqka（涨停揭秘）。
东财系接口有访问频率风控，所有请求统一走 em_get() 串行限流防封。

用法：
  python3 limit_up.py 20260821          # 指定交易日（YYYYMMDD）
  python3 limit_up.py                   # 最近交易日（自动推断）
  python3 limit_up.py 20260821 --sentiment-only   # 只要情绪温度计

依赖：requests
"""
from __future__ import annotations

import argparse
import random
import time
from datetime import datetime, timedelta, timezone

import requests

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# ──────────────────────────────────────────────────────────────────────────
# 东财防封：全局节流 + 会话复用（push2 / datacenter / push2ex / search 有风控）
# 实测阈值：每秒 >5 次 / 单 IP 并发 ≥10 / 1 分钟 ≥200 次 → 临时封 IP
# ──────────────────────────────────────────────────────────────────────────
EM_SESSION = requests.Session()
EM_SESSION.headers.update({"User-Agent": UA})
try:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    _em_adapter = HTTPAdapter(max_retries=Retry(
        total=3, connect=3, backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"]))
    EM_SESSION.mount("https://", _em_adapter)
    EM_SESSION.mount("http://", _em_adapter)
except Exception:
    pass

EM_MIN_INTERVAL = 1.0          # 两次东财请求最小间隔(秒)；批量建议调大到 1.5~2
_em_last_call = [0.0]


def em_get(url: str, params: dict | None = None, headers: dict | None = None,
           timeout: int = 15, **kwargs):
    """东财统一请求入口：自动节流 + 复用 session + 默认 UA。"""
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    try:
        return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()


# ──────────────────────────────────────────────────────────────────────────
# 8.1 东财涨停板池 — 涨停 / 炸板 / 跌停 / 昨日涨停（push2ex）
# ──────────────────────────────────────────────────────────────────────────
ZTB_UT = "7eea3edcaed734bea9cbfc24409ed989"


def _fmt_zt_time(t) -> str:
    """涨停板时间整数 → HH:MM:SS（92500 → 09:25:00）。"""
    s = str(t).zfill(6)
    return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"


def _em_zt_api(endpoint: str, sort: str, date: str) -> list[dict]:
    url = f"https://push2ex.eastmoney.com/{endpoint}"
    params = {"ut": ZTB_UT, "dpt": "wz.ztzt", "Pageindex": 0,
              "pagesize": 10000, "sort": sort, "date": date}
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        return (r.json().get("data") or {}).get("pool") or []
    except Exception as e:
        print(f"[WARN] 涨停板池 {endpoint} 请求失败: {e}")
        return []


def em_zt_pool(date: str) -> list[dict]:
    """涨停池。返回 code/name/price/pct/limit_days/first_seal/last_seal/seal_fund/break_times/industry/zt_stat"""
    out = []
    for p in _em_zt_api("getTopicZTPool", "fbt:asc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                    "pct": round(p["zdp"], 2), "amount": p["amount"], "float_cap": p["ltsz"],
                    "turnover": round(p["hs"], 2), "limit_days": p["lbc"],
                    "first_seal": _fmt_zt_time(p["fbt"]), "last_seal": _fmt_zt_time(p["lbt"]),
                    "seal_fund": p["fund"], "break_times": p["zbc"], "industry": p.get("hybk", ""),
                    "zt_stat": f'{(p.get("zttj") or {}).get("days", "?")}天{(p.get("zttj") or {}).get("ct", "?")}板'})
    return out


def em_zb_pool(date: str) -> list[dict]:
    """炸板池（涨停后开板）。"""
    out = []
    for p in _em_zt_api("getTopicZBPool", "fbt:asc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                    "limit_price": p["ztp"] / 1000, "pct": round(p["zdp"], 2),
                    "turnover": round(p["hs"], 2), "first_seal": _fmt_zt_time(p["fbt"]),
                    "break_times": p["zbc"], "amplitude": round(p["zf"], 2),
                    "speed": round(p["zs"], 2), "industry": p.get("hybk", ""),
                    "zt_stat": f'{(p.get("zttj") or {}).get("days", "?")}天{(p.get("zttj") or {}).get("ct", "?")}板'})
    return out


def em_dt_pool(date: str) -> list[dict]:
    """跌停池。"""
    out = []
    for p in _em_zt_api("getTopicDTPool", "fund:asc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                    "pct": round(p["zdp"], 2), "turnover": round(p["hs"], 2), "pe": p.get("pe"),
                    "seal_fund": p["fund"], "last_seal": _fmt_zt_time(p["lbt"]),
                    "board_amount": p.get("fba"), "dt_days": p.get("days"),
                    "open_times": p.get("oc"), "industry": p.get("hybk", "")})
    return out


def em_yzt_pool(date: str) -> list[dict]:
    """昨日涨停池（昨涨停今表现，算晋级率/赚钱效应）。"""
    out = []
    for p in _em_zt_api("getYesterdayZTPool", "zs:desc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                    "pct": round(p["zdp"], 2), "turnover": round(p["hs"], 2),
                    "amplitude": round(p["zf"], 2), "speed": round(p["zs"], 2),
                    "y_first_seal": _fmt_zt_time(p["yfbt"]), "y_limit_days": p["ylbc"],
                    "industry": p.get("hybk", ""),
                    "zt_stat": f'{(p.get("zttj") or {}).get("days", "?")}天{(p.get("zttj") or {}).get("ct", "?")}板'})
    return out


# ──────────────────────────────────────────────────────────────────────────
# 8.2 同花顺涨停揭秘 — 涨停原因题材 + 封板成功率 + 板型
# ──────────────────────────────────────────────────────────────────────────
def ths_limit_up_pool(date: str) -> list[dict]:
    """同花顺涨停揭秘。返回 code/name/price/pct/reason/board_type/seal_rate/break_times/seal_amount/high_days"""
    url = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
    params = {"page": 1, "limit": 200,
              "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004",
              "filter": "HS,GEM2STAR", "order_field": "330324", "order_type": "0", "date": date}
    try:
        r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=10)
        info = (r.json().get("data") or {}).get("info", [])
    except Exception as e:
        print(f"[WARN] 同花顺涨停揭秘请求失败: {e}")
        return []
    out = []
    for it in info:
        ft = it.get("first_limit_up_time")
        out.append({"code": it.get("code"), "name": it.get("name"),
                    "price": it.get("latest"), "pct": it.get("change_rate"),
                    "reason": it.get("reason_type", ""), "board_type": it.get("limit_up_type", ""),
                    "seal_rate": it.get("limit_up_suc_rate"), "break_times": it.get("open_num") or 0,
                    "seal_amount": it.get("order_amount"), "high_days": it.get("high_days", ""),
                    "first_time": datetime.fromtimestamp(int(ft)).strftime("%H:%M:%S") if ft else "",
                    "is_again": it.get("is_again_limit")})
    return out


# ──────────────────────────────────────────────────────────────────────────
# 8.3 打板情绪速算 — 炸板率 / 连板高度 / 连板梯队
# ──────────────────────────────────────────────────────────────────────────
def limit_up_sentiment(date: str) -> dict:
    zt, zb, dt = em_zt_pool(date), em_zb_pool(date), em_dt_pool(date)
    ladder = {}
    for s in zt:
        ladder[s["limit_days"]] = ladder.get(s["limit_days"], 0) + 1
    zt_n, zb_n = len(zt), len(zb)
    return {"date": date, "zt_count": zt_n, "zb_count": zb_n, "dt_count": len(dt),
            "break_rate": round(zb_n / (zt_n + zb_n) * 100, 1) if (zt_n + zb_n) else 0,
            "max_height": max((s["limit_days"] for s in zt), default=0),
            "ladder": dict(sorted(ladder.items()))}


# ──────────────────────────────────────────────────────────────────────────
# 8.4 东财重点监控池（零鉴权静态 JSON）
# ──────────────────────────────────────────────────────────────────────────
CN_TZ = timezone(timedelta(hours=8))
MONITOR_URL = "https://mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json"
_MONITOR_MARKET = {"1": "SH", "0": "SZ", "B": "BJ"}   # 三值且含字母 B（北交所）


def cn_today() -> str:
    return datetime.now(CN_TZ).date().isoformat()


def em_stock_monitor(only_active: bool = True) -> list[dict]:
    r = em_get(MONITOR_URL, headers={"Referer": "https://vipmoney.eastmoney.com/"}, timeout=20)
    rows = r.json() or []
    today = cn_today()
    out = []
    for x in rows:
        start, end = x.get("VALIDATESTARTDATE", ""), x.get("VALIDATEENDDATE", "")
        if only_active and not (start <= today <= end):
            continue
        raw_mkt = str(x.get("MARKET", "")).upper()
        out.append({"code": x.get("STKCODE", ""), "name": x.get("STKNAME", ""),
                    "market": _MONITOR_MARKET.get(raw_mkt, f"?{raw_mkt}"),
                    "start": start, "end": end, "link": x.get("LINK_URL", "")})
    return out


# ──────────────────────────────────────────────────────────────────────────
# 8.5 东财日内异动池 — 严重异常波动
# ──────────────────────────────────────────────────────────────────────────
ANOMALY_BASE = "https://dycalchis.eastmoney.com/price-anomaly"
HQ_PARAMS = {"team": "h5", "product": "EastMoney", "client": "WAP",
             "version": "9001", "name": "WAP", "user": "123"}

ANOMALY_RULES = {
    1: "主板连续10个交易日内4次出现同向异常波动",
    2: "创业板连续10个交易日内3次出现同向异常波动",
    3: "科创板连续10个交易日内3次出现同向异常波动",
    4: "连续十个交易日内日收盘价涨跌幅偏离值累计达到+100%",
    5: "连续十个交易日内日收盘价涨跌幅偏离值累计达到-50%",
    6: "连续三十个交易日内日收盘价涨跌幅偏离值累计达到+200%",
    7: "连续三十个交易日内日收盘价涨跌幅偏离值累计达到-70%",
    8: "北交所连续10个交易日内3次出现同向异常波动",
    40: "连续十个交易日内日收盘价涨跌幅偏离值累计达到+150%",
    50: "连续十个交易日内日收盘价涨跌幅偏离值累计达到-60%",
    60: "连续30个交易日内日收盘价涨跌幅偏离值累计达到+300%",
    70: "连续30个交易日内日收盘价涨跌幅偏离值累计达到-75%",
}


def _anomaly_market(code, m, board=None) -> str:
    c = str(code or "")
    if c.startswith("920") or c[:2] in ("43", "83", "87") or board == 8:
        return "BJ"
    return "SH" if m == 1 else "SZ"


def _anomaly_get(path: str, page_size: int, page_no: int, **extra) -> dict:
    params = {**HQ_PARAMS, "pageSize": str(page_size), "pageNo": str(page_no), **extra}
    r = em_get(f"{ANOMALY_BASE}/{path}", params=params,
               headers={"Referer": "https://vipmoney.eastmoney.com/"}, timeout=20)
    d = r.json()
    if d.get("result") != 0:
        raise RuntimeError(f"东财异动接口拒绝: result={d.get('result')} msg={d.get('msg')!r}")
    return d


def em_price_anomaly(page_size: int = 200, page_no: int = 1) -> dict:
    d = _anomaly_get("list", page_size, page_no)
    items = []
    for x in d.get("data") or []:
        e = x.get("e")
        key = e * 10 if (x.get("s") == 6 and e in (4, 5, 6, 7)) else e
        items.append({"code": x.get("c"), "name": x.get("n"),
                      "market": _anomaly_market(x.get("c"), x.get("m"), x.get("s")),
                      "change_pct": x.get("a"), "deviation": x.get("x"), "days": x.get("d"),
                      "board": x.get("s"), "rule_code": key,
                      "rule": ANOMALY_RULES.get(key, f"未知规则码 {key}"),
                      "is_today": x.get("o") != 2})
    return {"date": str(d.get("date", "")), "pages": d.get("pages", 0), "items": items}


def em_price_anomaly_count(page_size: int = 50, page_no: int = 1,
                           sort_key: str = "", sort_dir: str = "") -> dict:
    d = _anomaly_get("count", page_size, page_no, sortKey=sort_key, sortDir=sort_dir)
    items = [{"code": x.get("c"), "name": x.get("n"),
              "market": _anomaly_market(x.get("c"), x.get("m"), x.get("s")),
              "price": x.get("p"), "change_pct": x.get("a"), "times": x.get("t"),
              "deviation": x.get("x"), "days": x.get("d"), "board": x.get("s")}
             for x in d.get("data") or []]
    return {"date": str(d.get("date", "")), "pages": d.get("pages", 0), "items": items}


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="打板层数据（a-stock-data 参考实现）")
    parser.add_argument("date", nargs="?", default=None, help="交易日 YYYYMMDD，默认最近交易日")
    parser.add_argument("--sentiment-only", action="store_true", help="只输出情绪温度计")
    args = parser.parse_args()

    date = args.date or datetime.now(CN_TZ).strftime("%Y%m%d")
    if args.sentiment_only:
        s = limit_up_sentiment(date)
        print(f"{s['date']} 涨停{s['zt_count']} 炸板{s['zb_count']}(炸板率{s['break_rate']}%) "
              f"跌停{s['dt_count']} 最高{s['max_height']}连板")
        print(f"连板梯队: {s['ladder']}")
        return 0

    zt = em_zt_pool(date)
    print(f"=== {date} 涨停池 {len(zt)} 只 ===")
    for s in zt[:10]:
        print(f"  {s['name']:8} {s['code']} {s['zt_stat']} 封板{s.get('seal_fund', 0)/1e8:.2f}亿 {s['industry']}")

    zb = em_zb_pool(date)
    dt = em_dt_pool(date)
    zt_n, zb_n = len(zt), len(zb)
    break_rate = round(zb_n / (zt_n + zb_n) * 100, 1) if (zt_n + zb_n) else 0
    max_height = max((s["limit_days"] for s in zt), default=0)
    print(f"\n情绪: 涨停{zt_n} 炸板{zb_n}(炸板率{break_rate}%) 跌停{len(dt)} 最高{max_height}连板")

    print(f"\n=== 同花顺涨停揭秘（题材归因） ===")
    ths = ths_limit_up_pool(date)
    for s in ths[:8]:
        print(f"  {s['name']:8} {s['high_days']} | {s['reason']} | 封板率{s['seal_rate']}")

    print(f"\n=== 重点监控池 ===")
    pool = em_stock_monitor()
    print(f"  当前 {len(pool)} 只")
    for s in pool[:5]:
        print(f"  {s['code']} {s['name']}({s['market']}) {s['start']}~{s['end']}")

    print(f"\n=== 日内异动（严重异常波动） ===")
    try:
        a = em_price_anomaly(page_size=200)
        print(f"  {a['date']} 异动 {len(a['items'])} 条")
        for s in a["items"][:5]:
            print(f"  {s['code']} {s['name']} {s['change_pct']}% 偏离{s['deviation']}%/{s['days']}日 | {s['rule']}")
    except Exception as e:
        print(f"  [WARN] 日内异动失败: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
