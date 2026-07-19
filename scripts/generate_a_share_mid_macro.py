#!/usr/bin/env python3
"""Generate the A-share macro and financial-conditions dashboard.

The daily risk gate uses three timely market-implied factors:
1) Interest-rate / liquidity preference proxy
2) External-risk preference proxy
3) Leveraged-fund / margin-financing trend

The public payload also declares the full six-dimension A-share macro framework.
Official monthly dimensions remain visible with explicit coverage status and never
receive invented values. This layer constrains equity risk budget and chase
eligibility; it does not replace price-level action triggers.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "public/data/etf-garden-pool.json"
RECO = ROOT / "public/data/garden-recommendations.json"
OUTPUT = ROOT / "public/data/a-share-mid-macro.json"
CN = timezone(timedelta(hours=8))
UA = {
    "User-Agent": "Mozilla/5.0 ETF-Compass/1.0",
    "Referer": "https://data.eastmoney.com/",
}

# Rates proxies: long rate bond + credit bond + equity benchmark.
RATE_CODES = {
    "511260": "sh511260",  # 十年国债
    "511010": "sh511010",  # 国债ETF
    "511110": "sh511110",  # 公司债
    "510300": "sh510300",  # 沪深300
}
# Overseas / high-beta mapping proxies for deleveraging pressure.
OVERSEAS_CODES = {
    "159941": "sz159941",  # 纳指
    "513500": "sh513500",  # 标普500
    "159502": "sz159502",  # 标普生物
    "513520": "sh513520",  # 日经
}
MARGIN_URL = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get?"
    "reportName=RPTA_RZRQ_LSHJ&columns=ALL&pageNumber=1&pageSize=40"
    "&sortColumns=dim_date&sortTypes=-1&source=WEB&client=WEB"
)
ECONOMY_URL = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get?"
    "columns=ALL&pageNumber=1&pageSize=6&sortColumns=REPORT_DATE&sortTypes=-1"
    "&source=WEB&client=WEB&reportName={report}"
)

MACRO_FRAMEWORK = [
    {
        "key": "monetary_liquidity",
        "label": "货币与流动性",
        "question": "资金价格与流动性环境是否支持风险资产估值？",
        "official_indicators": ["DR007/逆回购利率", "中期政策利率与LPR", "M1/M2增速"],
        "frequency": "日度 / 月度",
        "primary_source": "中国人民银行、全国银行间同业拆借中心",
        "links": [
            {"label": "央行货币政策工具", "url": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/index.html", "type": "官方数据"},
            {"label": "银行间市场利率", "url": "https://www.chinamoney.com.cn/chinese/bkccpr/", "type": "市场利率"},
            {"label": "中债收益率资料", "url": "https://www.chinabond.com.cn/", "type": "债券数据"},
        ],
        "coverage": "proxy",
        "factor_key": "rates_fixed_income",
        "note": "当前以国债、信用债相对沪深300的价格表现作为日频风险偏好代理。",
    },
    {
        "key": "credit_impulse",
        "label": "信用周期",
        "question": "实体融资需求和信用扩张是否改善？",
        "official_indicators": ["社会融资规模存量增速", "人民币贷款", "政府债券融资"],
        "frequency": "月度",
        "primary_source": "中国人民银行",
        "links": [
            {"label": "社会融资规模", "url": "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html", "type": "官方数据"},
            {"label": "金融统计数据", "url": "https://www.pbc.gov.cn/diaochatongjisi/116219/index.html", "type": "官方发布"},
        ],
        "coverage": "pending_official",
        "factor_key": None,
        "note": "等待稳定接入官方月度时序；缺失时不参与日度风险评分。",
    },
    {
        "key": "growth_cycle",
        "label": "增长与盈利",
        "question": "生产、需求和企业利润处于扩张还是收缩阶段？",
        "official_indicators": ["制造业PMI", "工业增加值", "规模以上工业企业利润"],
        "frequency": "月度",
        "primary_source": "国家统计局",
        "links": [
            {"label": "统计局最新发布", "url": "https://www.stats.gov.cn/sj/zxfb/", "type": "官方发布"},
            {"label": "国家数据查询", "url": "https://data.stats.gov.cn/", "type": "数据查询"},
        ],
        "coverage": "pending_official",
        "factor_key": None,
        "note": "用于判断盈利周期和顺周期行业背景，按官方发布日期更新。",
    },
    {
        "key": "inflation_margin",
        "label": "通胀与利润率",
        "question": "价格环境正在改善企业利润率，还是压缩终端需求？",
        "official_indicators": ["PPI同比", "CPI同比", "PPI-CPI剪刀差"],
        "frequency": "月度",
        "primary_source": "国家统计局",
        "links": [
            {"label": "CPI / PPI官方发布", "url": "https://www.stats.gov.cn/sj/zxfb/", "type": "官方发布"},
            {"label": "国家数据查询", "url": "https://data.stats.gov.cn/", "type": "数据查询"},
        ],
        "coverage": "pending_official",
        "factor_key": None,
        "note": "重点观察PPI方向与上下游利润再分配，不把单月读数直接当交易信号。",
    },
    {
        "key": "external_fx",
        "label": "汇率与外部环境",
        "question": "人民币、美元与海外风险偏好是否形成外部约束？",
        "official_indicators": ["人民币汇率与CFETS指数", "美元指数/美债利率", "海外股市风险偏好"],
        "frequency": "日度",
        "primary_source": "中国外汇交易中心、国家外汇管理局、公开市场行情",
        "links": [
            {"label": "人民币汇率", "url": "https://www.chinamoney.com.cn/chinese/bkccpr/", "type": "市场数据"},
            {"label": "外汇局统计数据", "url": "https://www.safe.gov.cn/safe/tjsj1/index.html", "type": "官方数据"},
            {"label": "银行结售汇", "url": "https://www.safe.gov.cn/safe/yhjsh/index.html", "type": "官方发布"},
        ],
        "coverage": "proxy",
        "factor_key": "overseas_deleveraging",
        "note": "当前以境内海外ETF映射和海外相关池弱化广度作为外部风险代理。",
    },
    {
        "key": "market_liquidity",
        "label": "市场资金与杠杆",
        "question": "A股增量资金和杠杆资金是否持续进入？",
        "official_indicators": ["融资余额与融资买入额", "成交额与市场广度", "ETF份额变化"],
        "frequency": "日度",
        "primary_source": "沪深北交易所、公开市场数据",
        "links": [
            {"label": "上交所融资融券", "url": "https://www.sse.com.cn/market/othersdata/margin/sum/", "type": "官方数据"},
            {"label": "深交所融资融券", "url": "https://www.szse.cn/disclosure/margin/margin/index.html", "type": "官方数据"},
            {"label": "深交所市场指标", "url": "https://www.szse.cn/market/stock/indicator/index.html", "type": "市场数据"},
        ],
        "coverage": "live",
        "factor_key": "margin_financing",
        "note": "当前日度风险闸门使用两融余额趋势；成交与ETF资金用于交易层复核。",
    },
]


def now_cn() -> datetime:
    return datetime.now(CN)


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fetch_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "ignore"))


def fetch_economy_rows(report: str) -> list[dict[str, Any]]:
    payload = fetch_json(ECONOMY_URL.format(report=report))
    rows = ((payload.get("result") or {}).get("data") or [])
    if not rows:
        raise RuntimeError(f"{report}: no economy rows")
    return rows


def macro_item(
    *, key: str, title: str, value: float, unit: str, date: str, frequency: str,
    source: str, source_url: str, detail: str, change: float | None = None,
    change_label: str = "较上期", tone: str = "neutral",
) -> dict[str, Any]:
    return {
        "key": key, "title": title, "value": value, "unit": unit,
        "display": f"{value:.2f}{unit}" if unit not in {"亿元", "万亿元"} else f"{value:.0f}{unit}",
        "as_of": date, "frequency": frequency, "source": source, "source_url": source_url,
        "detail": detail, "change": change, "change_label": change_label, "tone": tone,
    }


def score_tone(value: float, positive: float, negative: float, reverse: bool = False) -> str:
    if reverse:
        return "positive" if value <= positive else "warning" if value >= negative else "neutral"
    return "positive" if value >= positive else "warning" if value <= negative else "neutral"


def fetch_macro_observations() -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {
        "monetary_liquidity": [], "credit_impulse": [], "growth_cycle": [],
        "inflation_margin": [], "external_fx": [], "market_liquidity": [],
    }
    cpi = fetch_economy_rows("RPT_ECONOMY_CPI")
    ppi = fetch_economy_rows("RPT_ECONOMY_PPI")
    pmi = fetch_economy_rows("RPT_ECONOMY_PMI")
    money = fetch_economy_rows("RPT_ECONOMY_CURRENCY_SUPPLY")
    loan = fetch_economy_rows("RPT_ECONOMY_RMB_LOAN")
    gdp = fetch_economy_rows("RPT_ECONOMY_GDP")

    def f(row: dict[str, Any], field: str) -> float:
        return float(row[field])

    money_url = "https://www.pbc.gov.cn/diaochatongjisi/116219/index.html"
    stats_url = "https://www.stats.gov.cn/sj/zxfb/"
    loan_url = "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html"
    m2, m1 = f(money[0], "BASIC_CURRENCY_SAME"), f(money[0], "CURRENCY_SAME")
    output["monetary_liquidity"] = [
        macro_item(key="m2_yoy", title="M2同比", value=m2, unit="%", date=str(money[0]["REPORT_DATE"])[:10], frequency="月频", source="人民银行金融统计", source_url=money_url, detail=f"前值 {f(money[1], 'BASIC_CURRENCY_SAME'):.1f}% · 广义流动性", change=round(m2-f(money[1], "BASIC_CURRENCY_SAME"),2), tone=score_tone(m2, 8.0, 6.0)),
        macro_item(key="m1_yoy", title="M1同比", value=m1, unit="%", date=str(money[0]["REPORT_DATE"])[:10], frequency="月频", source="人民银行金融统计", source_url=money_url, detail=f"前值 {f(money[1], 'CURRENCY_SAME'):.1f}% · 资金活化程度", change=round(m1-f(money[1], "CURRENCY_SAME"),2), tone=score_tone(m1, 5.0, 0.0)),
        macro_item(key="m1_m2_gap", title="M1-M2剪刀差", value=round(m1-m2,2), unit="pp", date=str(money[0]["REPORT_DATE"])[:10], frequency="月频", source="人民银行金融统计", source_url=money_url, detail="差值回升通常对应企业资金活化改善", change=round((m1-m2)-(f(money[1],'CURRENCY_SAME')-f(money[1],'BASIC_CURRENCY_SAME')),2), tone=score_tone(m1-m2, -2.0, -6.0)),
    ]
    loan_value, loan_prev = f(loan[0], "RMB_LOAN"), f(loan[1], "RMB_LOAN")
    output["credit_impulse"] = [
        macro_item(key="rmb_loan", title="新增人民币贷款", value=loan_value, unit="亿元", date=str(loan[0]["REPORT_DATE"])[:10], frequency="月频", source="人民银行金融统计", source_url=loan_url, detail=f"上月 {loan_prev:.0f}亿元 · 当月新增", change=round(loan_value-loan_prev,2), tone="neutral"),
        macro_item(key="loan_yoy", title="新增贷款同比", value=f(loan[0], "RMB_LOAN_SAME"), unit="%", date=str(loan[0]["REPORT_DATE"])[:10], frequency="月频", source="人民银行金融统计", source_url=loan_url, detail="反映当月信用投放相对去年同期", tone=score_tone(f(loan[0], "RMB_LOAN_SAME"), 5.0, -10.0)),
        macro_item(key="loan_ytd", title="年内贷款累计", value=f(loan[0], "RMB_LOAN_ACCUMULATE"), unit="亿元", date=str(loan[0]["REPORT_DATE"])[:10], frequency="月频", source="人民银行金融统计", source_url=loan_url, detail=f"累计同比 {f(loan[0], 'LOAN_ACCUMULATE_SAME'):+.1f}%", tone=score_tone(f(loan[0], "LOAN_ACCUMULATE_SAME"), 0.0, -10.0)),
    ]
    pmi_value, non_pmi = f(pmi[0], "MAKE_INDEX"), f(pmi[0], "NMAKE_INDEX")
    output["growth_cycle"] = [
        macro_item(key="manufacturing_pmi", title="制造业PMI", value=pmi_value, unit="", date=str(pmi[0]["REPORT_DATE"])[:10], frequency="月频", source="国家统计局", source_url=stats_url, detail=f"前值 {f(pmi[1], 'MAKE_INDEX'):.1f} · 50为荣枯线", change=round(pmi_value-f(pmi[1], "MAKE_INDEX"),2), tone=score_tone(pmi_value, 50.0, 49.5)),
        macro_item(key="non_manufacturing_pmi", title="非制造业PMI", value=non_pmi, unit="", date=str(pmi[0]["REPORT_DATE"])[:10], frequency="月频", source="国家统计局", source_url=stats_url, detail=f"前值 {f(pmi[1], 'NMAKE_INDEX'):.1f} · 服务与建筑景气", change=round(non_pmi-f(pmi[1], "NMAKE_INDEX"),2), tone=score_tone(non_pmi, 50.0, 49.5)),
        macro_item(key="gdp_yoy", title="GDP累计同比", value=f(gdp[0], "SUM_SAME"), unit="%", date=str(gdp[0]["REPORT_DATE"])[:10], frequency="季度", source="国家统计局", source_url=stats_url, detail=f"{gdp[0]['TIME']} · 第三产业 {f(gdp[0], 'THIRD_SAME'):.1f}%", change=round(f(gdp[0], "SUM_SAME")-f(gdp[1], "SUM_SAME"),2), tone=score_tone(f(gdp[0], "SUM_SAME"), 5.0, 4.5)),
    ]
    cpi_value, ppi_value = f(cpi[0], "NATIONAL_SAME"), f(ppi[0], "BASE_SAME")
    output["inflation_margin"] = [
        macro_item(key="cpi_yoy", title="CPI同比", value=cpi_value, unit="%", date=str(cpi[0]["REPORT_DATE"])[:10], frequency="月频", source="国家统计局", source_url=stats_url, detail=f"环比 {f(cpi[0], 'NATIONAL_SEQUENTIAL'):+.1f}% · 前值 {f(cpi[1], 'NATIONAL_SAME'):.1f}%", change=round(cpi_value-f(cpi[1], "NATIONAL_SAME"),2), tone=score_tone(cpi_value, 1.0, 0.0)),
        macro_item(key="ppi_yoy", title="PPI同比", value=ppi_value, unit="%", date=str(ppi[0]["REPORT_DATE"])[:10], frequency="月频", source="国家统计局", source_url=stats_url, detail=f"前值 {f(ppi[1], 'BASE_SAME'):.1f}% · 工业品价格周期", change=round(ppi_value-f(ppi[1], "BASE_SAME"),2), tone=score_tone(ppi_value, 0.0, -2.0)),
        macro_item(key="ppi_cpi_gap", title="PPI-CPI剪刀差", value=round(ppi_value-cpi_value,2), unit="pp", date=str(cpi[0]["REPORT_DATE"])[:10], frequency="月频", source="国家统计局", source_url=stats_url, detail="正值通常有利上游价格与工业收入", change=round((ppi_value-cpi_value)-(f(ppi[1],'BASE_SAME')-f(cpi[1],'NATIONAL_SAME')),2), tone=score_tone(ppi_value-cpi_value, 0.0, -2.0)),
    ]
    return output


def fetch_tencent_bars(symbol: str, count: int = 60) -> list[dict[str, Any]]:
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{count},qfq"
    payload = fetch_json(url)
    node = (payload.get("data") or {}).get(symbol) or {}
    rows = node.get("qfqday") or node.get("day") or []
    bars = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            bars.append(
                {
                    "date": str(row[0])[:10],
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "volume": float(row[5]) if len(row) > 5 else None,
                }
            )
        except (TypeError, ValueError):
            continue
    return bars


def ret_from_bars(bars: list[dict[str, Any]], days: int) -> float | None:
    if len(bars) <= days:
        return None
    start = bars[-(days + 1)]["close"]
    end = bars[-1]["close"]
    if not start:
        return None
    return round((end / start - 1) * 100, 2)


def mean(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None and math.isfinite(v)]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def clamp(value: float, low: float = 0.0, high: float = 3.0) -> float:
    return max(low, min(high, value))


def parse_position_band(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    import re

    nums = [int(x) for x in re.findall(r"(\d+)\s*%-", text.replace(" ", ""))[:1]]
    # patterns like 权益10%-30%
    m = re.search(r"权益\s*(\d+)%\s*[-~到至]\s*(\d+)%", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+)%\s*[-~到至]\s*(\d+)%", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def shift_band(band: tuple[int, int], steps: int) -> tuple[int, int]:
    """Shift equity band down by discrete regime steps."""
    ladder = [(50, 70), (30, 50), (10, 30), (0, 10)]
    # find closest ladder index to current mid
    mid = sum(band) / 2
    idx = min(range(len(ladder)), key=lambda i: abs((ladder[i][0] + ladder[i][1]) / 2 - mid))
    idx = min(len(ladder) - 1, idx + max(0, steps))
    return ladder[idx]


def format_band(band: tuple[int, int]) -> str:
    low, high = band
    defense_low = max(0, 100 - high)
    defense_high = max(defense_low, 100 - low)
    return f"权益{low}%-{high}%；防御/现金{defense_low}%-{defense_high}%"


def score_rates(bars_map: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    bond_codes = ["511260", "511010", "511110"]
    equity = "510300"
    bond_rets = []
    details = {}
    for code in bond_codes:
        bars = bars_map.get(code) or []
        r5 = ret_from_bars(bars, 5)
        r20 = ret_from_bars(bars, 20)
        details[code] = {"ret5": r5, "ret20": r20, "last": bars[-1]["close"] if bars else None, "date": bars[-1]["date"] if bars else None}
        if r5 is not None:
            bond_rets.append(r5)
    eq_bars = bars_map.get(equity) or []
    eq5 = ret_from_bars(eq_bars, 5)
    eq20 = ret_from_bars(eq_bars, 20)
    details[equity] = {"ret5": eq5, "ret20": eq20, "last": eq_bars[-1]["close"] if eq_bars else None, "date": eq_bars[-1]["date"] if eq_bars else None}
    bond5 = mean(bond_rets)
    relative5 = None if bond5 is None or eq5 is None else round(bond5 - eq5, 2)
    relative20 = None
    bond20_vals = [details[c]["ret20"] for c in bond_codes if details[c]["ret20"] is not None]
    bond20 = mean(bond20_vals)
    if bond20 is not None and eq20 is not None:
        relative20 = round(bond20 - eq20, 2)

    # Adverse when fixed income is outperforming equities: risk budget should tighten.
    score = 0.0
    reasons = []
    if relative5 is not None:
        if relative5 >= 1.5:
            score += 1.5
            reasons.append(f"固收5日相对沪深300超额{relative5:+.2f}pp")
        elif relative5 >= 0.6:
            score += 1.0
            reasons.append(f"固收5日相对转强{relative5:+.2f}pp")
        elif relative5 <= -1.0:
            reasons.append(f"权益5日仍强于固收{relative5:+.2f}pp")
        else:
            reasons.append(f"固收/权益5日相对中性{relative5:+.2f}pp")
    if relative20 is not None and relative20 >= 1.0:
        score += 0.5
        reasons.append(f"固收20日相对偏强{relative20:+.2f}pp")
    if bond5 is not None and bond5 >= 0.25 and (eq5 is not None and eq5 <= 0):
        score += 0.5
        reasons.append("固收绝对上行且权益走弱")
    score = clamp(score)
    state = "逆风" if score >= 1.5 else "观察" if score >= 0.8 else "中性"
    return {
        "key": "rates_fixed_income",
        "label": "固收相对权益强弱",
        "state": state,
        "score": round(score, 2),
        "headline": reasons[0] if reasons else "固收与权益相对数据不足",
        "reasons": reasons,
        "status": "ok",
        "quality": "market_proxy",
        "completeness": 1.0,
        "interpretation": "股债风险偏好代理，不等同于政策利率、国债到期收益率或加息概率。",
        "not_measured": ["DR007与政策利率偏离", "国债到期收益率", "期限利差", "信用利差"],
        "metrics": {
            "bond_ret5": bond5,
            "bond_ret20": bond20,
            "equity_ret5": eq5,
            "equity_ret20": eq20,
            "bond_vs_equity_5d_pp": relative5,
            "bond_vs_equity_20d_pp": relative20,
        },
        "details": details,
        "as_of": details.get("511260", {}).get("date") or details.get("510300", {}).get("date"),
    }


def score_overseas(bars_map: dict[str, list[dict[str, Any]]], pool_rows: list[dict[str, Any]]) -> dict[str, Any]:
    details = {}
    rets5 = []
    rets20 = []
    weak = 0
    for code in OVERSEAS_CODES:
        bars = bars_map.get(code) or []
        r5 = ret_from_bars(bars, 5)
        r20 = ret_from_bars(bars, 20)
        details[code] = {"ret5": r5, "ret20": r20, "last": bars[-1]["close"] if bars else None, "date": bars[-1]["date"] if bars else None}
        if r5 is not None:
            rets5.append(r5)
            if r5 <= -2.0:
                weak += 1
        if r20 is not None:
            rets20.append(r20)
    avg5 = mean(rets5)
    avg20 = mean(rets20)

    # Pool-side overseas/high-beta weak breadth.
    overseas_rows = []
    for row in pool_rows:
        theme = str(row.get("theme") or "")
        name = str(row.get("name") or "")
        code = str(row.get("code") or "")
        if any(x in theme or x in name for x in ("纳斯达克", "标普", "日经", "恒生", "中概", "德国", "美国", "QDII")) or code in OVERSEAS_CODES:
            overseas_rows.append(row)
    exit_like = 0
    for row in overseas_rows:
        state = str(row.get("trade_state") or "")
        pos = row.get("close_position")
        ret5 = row.get("ret5")
        if state in {"退出", "禁止追高"} or (isinstance(pos, (int, float)) and pos <= 0.2 and isinstance(ret5, (int, float)) and ret5 <= -2):
            exit_like += 1
    weak_share = round(exit_like / len(overseas_rows), 3) if overseas_rows else None

    score = 0.0
    reasons = []
    if avg5 is not None:
        if avg5 <= -3.0:
            score += 1.5
            reasons.append(f"海外映射ETF 5日均值{avg5:+.2f}%")
        elif avg5 <= -1.5:
            score += 1.0
            reasons.append(f"海外映射ETF 5日偏弱{avg5:+.2f}%")
        else:
            reasons.append(f"海外映射ETF 5日{avg5:+.2f}%")
    if weak >= 2:
        score += 0.5
        reasons.append(f"{weak}只核心海外ETF 5日跌超2%")
    if weak_share is not None and weak_share >= 0.45:
        # Pool breadth is a confirmation because these rows already feed the
        # trend regime. Keep its contribution small to avoid double counting.
        score += 0.25
        reasons.append(f"海外相关池弱化占比{weak_share:.0%}（确认项）")
    if avg20 is not None and avg20 <= -3:
        score += 0.5
        reasons.append(f"海外映射20日仍弱{avg20:+.2f}%")
    score = clamp(score)
    state = "逆风" if score >= 1.5 else "观察" if score >= 0.8 else "中性"
    return {
        "key": "overseas_deleveraging",
        "label": "海外风险映射压力",
        "state": state,
        "score": round(score, 2),
        "headline": reasons[0] if reasons else "海外映射数据不足",
        "reasons": reasons,
        "status": "ok",
        "quality": "market_proxy",
        "completeness": 1.0,
        "interpretation": "境内QDII与海外主题ETF价格代理，不等同于海外真实去杠杆流量或人民币汇率。",
        "proxy_risks": ["汇率扰动", "QDII溢折价", "交易时段错位", "行业集中度"],
        "metrics": {
            "proxy_ret5_avg": avg5,
            "proxy_ret20_avg": avg20,
            "proxy_weak_count_5d": weak,
            "overseas_pool_count": len(overseas_rows),
            "overseas_weak_share": weak_share,
        },
        "details": details,
        "as_of": next((details[c]["date"] for c in OVERSEAS_CODES if details.get(c, {}).get("date")), None),
    }


def score_margin() -> dict[str, Any]:
    payload = fetch_json(MARGIN_URL)
    rows = ((payload.get("result") or {}).get("data") or [])
    series = []
    for row in rows:
        try:
            date = str(row.get("DIM_DATE") or "")[:10]
            rzye = float(row.get("RZYE"))
            if date and rzye > 0:
                series.append({"date": date, "rzye": rzye, "rzyezb": row.get("RZYEZB"), "rzmre": row.get("RZMRE")})
        except (TypeError, ValueError):
            continue
    series.sort(key=lambda x: x["date"])
    if len(series) < 10:
        raise RuntimeError(f"margin series too short: {len(series)}")

    def change(days: int) -> float | None:
        if len(series) <= days:
            return None
        a, b = series[-(days + 1)]["rzye"], series[-1]["rzye"]
        return round((b / a - 1) * 100, 2)

    chg5 = change(5)
    chg10 = change(10)
    chg20 = change(20)
    latest = series[-1]
    # consecutive down days among last 6 steps
    downs = 0
    for i in range(1, min(6, len(series))):
        if series[-i]["rzye"] < series[-i - 1]["rzye"]:
            downs += 1
        else:
            break

    score = 0.0
    reasons = []
    if chg5 is not None:
        if chg5 <= -1.0:
            score += 1.5
            reasons.append(f"两融余额5日{chg5:+.2f}%")
        elif chg5 <= -0.4:
            score += 1.0
            reasons.append(f"两融余额5日回落{chg5:+.2f}%")
        elif chg5 >= 0.5:
            reasons.append(f"两融余额5日回升{chg5:+.2f}%")
        else:
            reasons.append(f"两融余额5日{chg5:+.2f}%")
    if chg20 is not None and chg20 <= -1.5:
        score += 0.5
        reasons.append(f"两融余额20日{chg20:+.2f}%")
    if downs >= 4:
        score += 0.5
        reasons.append(f"连续{downs}日净回落")
    score = clamp(score)
    state = "逆风" if score >= 1.5 else "观察" if score >= 0.8 else "中性"
    return {
        "key": "margin_financing",
        "label": "市场资金与杠杆",
        "state": state,
        "score": round(score, 2),
        "headline": reasons[0] if reasons else "两融数据不足",
        "reasons": reasons,
        "status": "ok",
        "quality": "market_data",
        "completeness": 1.0,
        "interpretation": "境内杠杆资金强度与风险偏好指标，属于市场资金层。",
        "metrics": {
            "rzye": latest["rzye"],
            "rzye_yi": round(latest["rzye"] / 1e8, 2),
            "rzyezb": latest.get("rzyezb"),
            "chg5_pct": chg5,
            "chg10_pct": chg10,
            "chg20_pct": chg20,
            "down_streak": downs,
            "as_of": latest["date"],
        },
        "details": {"latest": latest, "points": series[-12:]},
        "as_of": latest["date"],
    }


def build_constraint(factors: list[dict[str, Any]], base_position: str | None, market_state: str | None) -> dict[str, Any]:
    adverse = sum(1 for f in factors if f["state"] == "逆风")
    watch = sum(1 for f in factors if f["state"] == "观察")
    total_score = round(sum(float(f["score"]) for f in factors), 2)

    # Discrete headwind count is the primary control; continuous score breaks ties.
    if adverse >= 3 or total_score >= 5.5:
        level = 3
    elif adverse >= 2 or total_score >= 3.5:
        level = 2
    elif adverse >= 1 or watch >= 2 or total_score >= 1.8:
        level = 1
    else:
        level = 0

    base_band = parse_position_band(base_position) or {
        "进攻": (50, 70),
        "震荡": (30, 50),
        "防御": (10, 30),
        "极弱": (0, 10),
    }.get(market_state or "", (30, 50))
    constrained = shift_band(base_band, level)
    # Never raise the band above the micro regime suggestion.
    if constrained[1] > base_band[1]:
        constrained = base_band

    allow_chase = level == 0
    allow_new_offense = level <= 1
    equity_constraint = {
        0: "中观中性：按关键位正常执行",
        1: "中观偏逆风：权益上限降1档，只回踩不追高",
        2: "中观逆风：权益上限降2档，禁止追高，新增进攻仓减半",
        3: "中观强逆风：停止新开进攻仓，只做防御与独立阿尔法",
    }[level]

    return {
        "headwind_level": level,
        "headwind_count": adverse,
        "watch_count": watch,
        "score_total": total_score,
        "label": ["中性", "偏逆风", "逆风", "强逆风"][level],
        "allow_chase": allow_chase,
        "allow_new_offense": allow_new_offense,
        "base_position": format_band(base_band),
        "base_position_source": "etf-garden-pool.market_regime",
        "base_market_state": market_state,
        "base_equity_band": {"low": base_band[0], "high": base_band[1]},
        "constrained_equity_band": {"low": constrained[0], "high": constrained[1]},
        "position": format_band(constrained),
        "equity_constraint": equity_constraint,
        "execution_rules": [
            "关键位触发仍由价格/资金决定",
            "本层只压缩权益上限与追高资格",
            "QDII红盘不得单独上调A股总仓",
            "两融下行时反弹默认按修复处理",
        ],
    }


def apply_to_recommendations(constraint: dict[str, Any], factors: list[dict[str, Any]], generated_at: str) -> dict[str, Any] | None:
    if not RECO.exists():
        return None
    reco = json.loads(RECO.read_text(encoding="utf-8"))
    if not isinstance(reco, dict):
        return None
    original_position = reco.get("position")
    reco["position_base"] = constraint.get("base_position") or original_position
    reco["position"] = constraint["position"]
    reco["mid_macro"] = {
        "generated_at": generated_at,
        "headwind_level": constraint["headwind_level"],
        "label": constraint["label"],
        "equity_constraint": constraint["equity_constraint"],
        "allow_chase": constraint["allow_chase"],
        "allow_new_offense": constraint["allow_new_offense"],
        "base_position": original_position,
        "factors": [
            {"key": f["key"], "label": f["label"], "state": f["state"], "score": f["score"], "headline": f["headline"]}
            for f in factors
        ],
    }

    # Plant eligibility gate: mid-macro + trend quality.
    # Status vocabulary: 候场 / 伏击 (legacy 准备种花 / 种花 still accepted).
    plants = reco.get("plant")
    demoted = 0
    kept = 0
    if isinstance(plants, list):
        filtered: list[dict[str, Any]] = []
        for item in plants:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "")
            trend = str(item.get("trend_level") or item.get("strength_level") or "")
            risk = str(item.get("risk_level") or "")
            level_status = str(item.get("level_status") or "")
            action = str(item.get("action") or "")
            # Normalize residual garden labels if present.
            if status in {"准备种花", "候场"}:
                status = "候场"
            elif status in {"种花", "伏击"}:
                status = "伏击"
            item["status"] = status

            hard_block = (
                level_status == "invalid"
                or risk == "高"
                or trend in {"D", "E"}
                or constraint["headwind_level"] >= 3
                or (constraint["headwind_level"] >= 2 and not constraint.get("allow_new_offense", True) and trend not in {"A", "B"})
            )
            if hard_block:
                if status == "伏击":
                    item["status"] = "候场"
                    demoted += 1
                elif status == "候场" and constraint["headwind_level"] >= 3 and trend in {"D", "E"}:
                    # Keep only as watch-grade note, not executable plant list.
                    demoted += 1
                    item["eligibility"] = "blocked"
                    item["eligibility_reason"] = "中观强逆风 + 趋势过弱，移出可执行伏击名单"
                    note = "中观强逆风：仅观察，不新开伏击"
                    if note not in action:
                        item["action"] = (action.rstrip("。") + f"；{note}。").lstrip("；")
                    # Still show, but mark non-executable.
                    filtered.append(item)
                    continue
                item["eligibility"] = "blocked" if constraint["headwind_level"] >= 3 else "watch_only"
                item["eligibility_reason"] = constraint["equity_constraint"]
                note = "中观约束：停开新伏击" if constraint["headwind_level"] >= 3 else "中观约束：禁止追高，只按伏击位分批"
                if note not in action and "不追" not in action and "禁止追高" not in action:
                    item["action"] = (action.rstrip("。") + f"；{note}。").lstrip("；")
                item["mid_macro_constraint"] = constraint["equity_constraint"]
                filtered.append(item)
                continue

            if not constraint["allow_chase"]:
                if "不追" not in action and "禁止追高" not in action:
                    item["action"] = (action.rstrip("。") + "；中观约束：禁止追高，只按伏击位分批。").lstrip("；")
                item["mid_macro_constraint"] = constraint["equity_constraint"]
            item["eligibility"] = "ok"
            kept += 1
            filtered.append(item)
        # Under headwind 3, drop fully blocked D/E names from plant if action says only observe
        # but keep max 2 defensive watch items for transparency.
        if constraint["headwind_level"] >= 3:
            watch = [x for x in filtered if x.get("eligibility") == "blocked"]
            ok_items = [x for x in filtered if x.get("eligibility") != "blocked"]
            reco["plant"] = (ok_items + watch)[:2]
        else:
            reco["plant"] = filtered

    if constraint["headwind_level"] >= 2:
        summary = str(reco.get("summary") or "")
        tag = f"中观{constraint['label']}（权益上限已压缩）"
        if "中观" not in summary:
            reco["summary"] = f"{summary.rstrip('。')}；{tag}。"
    atomic_write(RECO, reco)
    return {
        "updated": True,
        "position_before": original_position,
        "position_after": reco.get("position"),
        "headwind_level": constraint["headwind_level"],
        "plant_kept": kept,
        "plant_demoted": demoted,
        "plant_count": len(reco.get("plant") or []),
    }


def main() -> None:
    failures: dict[str, str] = {}
    pool = json.loads(POOL.read_text(encoding="utf-8")) if POOL.exists() else {}
    pool_rows = pool.get("all_rows") or pool.get("rows") or []
    if not isinstance(pool_rows, list):
        pool_rows = []

    bars_map: dict[str, list[dict[str, Any]]] = {}
    code_map = {**RATE_CODES, **OVERSEAS_CODES}
    for code, symbol in code_map.items():
        try:
            bars = fetch_tencent_bars(symbol, 60)
            if len(bars) < 25:
                raise RuntimeError(f"short bars {len(bars)}")
            bars_map[code] = bars
        except Exception as exc:  # noqa: BLE001 - retain partial factors
            failures[code] = f"{type(exc).__name__}: {exc}"

    factors: list[dict[str, Any]] = []
    try:
        factors.append(score_rates(bars_map))
    except Exception as exc:  # noqa: BLE001
        failures["rates"] = str(exc)
        factors.append(
            {
                "key": "rates_fixed_income",
                "label": "固收相对权益强弱",
                "state": "观察",
                "score": 0.8,
                "headline": "固收数据暂缺，按观察处理",
                "reasons": [str(exc)],
                "metrics": {},
                "details": {},
                "as_of": None,
            }
        )
    try:
        factors.append(score_overseas(bars_map, pool_rows))
    except Exception as exc:  # noqa: BLE001
        failures["overseas"] = str(exc)
        factors.append(
            {
                "key": "overseas_deleveraging",
                "label": "海外风险映射压力",
                "state": "观察",
                "score": 0.8,
                "headline": "海外映射数据暂缺，按观察处理",
                "reasons": [str(exc)],
                "metrics": {},
                "details": {},
                "as_of": None,
            }
        )
    try:
        factors.append(score_margin())
    except Exception as exc:  # noqa: BLE001
        failures["margin"] = str(exc)
        factors.append(
            {
                "key": "margin_financing",
                "label": "市场资金与杠杆",
                "state": "观察",
                "score": 0.8,
                "headline": "两融数据暂缺，按观察处理",
                "reasons": [str(exc)],
                "metrics": {},
                "details": {},
                "as_of": None,
            }
        )

    observations: dict[str, list[dict[str, Any]]] = {}
    try:
        observations = fetch_macro_observations()
    except Exception as exc:  # noqa: BLE001
        failures["macro_observations"] = str(exc)
        observations = {item["key"]: [] for item in MACRO_FRAMEWORK}

    factor_map_for_observations = {factor["key"]: factor for factor in factors}
    rate_factor = factor_map_for_observations.get("rates_fixed_income") or {}
    overseas_factor = factor_map_for_observations.get("overseas_deleveraging") or {}
    margin_factor = factor_map_for_observations.get("margin_financing") or {}
    rate_metrics = rate_factor.get("metrics") or {}
    overseas_metrics = overseas_factor.get("metrics") or {}
    margin_metrics = margin_factor.get("metrics") or {}
    observations.setdefault("monetary_liquidity", []).append(
        macro_item(key="bond_equity_5d", title="固收相对权益5日", value=float(rate_metrics.get("bond_vs_equity_5d_pp") or 0), unit="pp", date=rate_factor.get("as_of") or "", frequency="日频", source="公开前复权行情", source_url="https://www.chinabond.com.cn/", detail="国债/信用债ETF均值减沪深300收益", tone="warning" if float(rate_metrics.get("bond_vs_equity_5d_pp") or 0) >= 1.5 else "neutral")
    )
    observations["external_fx"] = [
        macro_item(key="overseas_5d", title="海外映射ETF 5日", value=float(overseas_metrics.get("proxy_ret5_avg") or 0), unit="%", date=overseas_factor.get("as_of") or "", frequency="日频", source="境内QDII行情", source_url="https://www.chinamoney.com.cn/chinese/bkccpr/", detail=f"20日 {float(overseas_metrics.get('proxy_ret20_avg') or 0):+.2f}% · 汇率与溢折价会扰动", tone="warning" if float(overseas_metrics.get("proxy_ret5_avg") or 0) <= -1.5 else "neutral"),
        macro_item(key="overseas_weak", title="海外池弱化占比", value=round(float(overseas_metrics.get("overseas_weak_share") or 0)*100,2), unit="%", date=overseas_factor.get("as_of") or "", frequency="日频", source="ETF罗盘海外池", source_url="/a-momentum/", detail=f"样本 {int(overseas_metrics.get('overseas_pool_count') or 0)}只 · 只作确认项", tone="warning" if float(overseas_metrics.get("overseas_weak_share") or 0) >= .45 else "neutral"),
    ]
    observations["market_liquidity"] = [
        macro_item(key="margin_balance", title="两融余额", value=float(margin_metrics.get("rzye_yi") or 0), unit="亿元", date=margin_factor.get("as_of") or "", frequency="日频", source="沪深两融历史汇总", source_url="https://www.sse.com.cn/market/othersdata/margin/sum/", detail=f"5日 {float(margin_metrics.get('chg5_pct') or 0):+.2f}% · 20日 {float(margin_metrics.get('chg20_pct') or 0):+.2f}%", change=float(margin_metrics.get("chg5_pct") or 0), change_label="5日", tone="warning" if float(margin_metrics.get("chg5_pct") or 0) <= -.4 else "positive"),
        macro_item(key="margin_share", title="融资余额占比", value=float(margin_metrics.get("rzyezb") or 0), unit="%", date=margin_factor.get("as_of") or "", frequency="日频", source="沪深两融历史汇总", source_url="https://www.sse.com.cn/market/othersdata/margin/sum/", detail=f"连续回落 {int(margin_metrics.get('down_streak') or 0)}日", tone="warning" if int(margin_metrics.get("down_streak") or 0) >= 4 else "neutral"),
    ]

    reco = json.loads(RECO.read_text(encoding="utf-8")) if RECO.exists() else {}
    regime = pool.get("market_regime") or {}
    market_state = regime.get("state") or (reco.get("market_state") if isinstance(reco, dict) else None)
    # Always derive the uncompressed base band from the trend pool. The
    # recommendations file contains the previously constrained final position
    # and cannot safely serve as the next run's base.
    base_position = regime.get("equity_allocation")
    if isinstance(base_position, str) and not base_position.startswith("权益"):
        defense = regime.get("defense_allocation") or ""
        base_position = f"权益{base_position}" + (f"；防御/现金{defense}" if defense else "")
    if not base_position and isinstance(reco, dict):
        base_position = reco.get("position_base") or reco.get("position")

    constraint = build_constraint(factors, base_position if isinstance(base_position, str) else None, market_state if isinstance(market_state, str) else None)
    factor_dates = sorted(str(factor.get("as_of")) for factor in factors if factor.get("as_of"))
    model_date = str(pool.get("evaluation_date") or pool.get("latest_trade_date") or (factor_dates[-1] if factor_dates else now_cn().date().isoformat()))
    generated_at = f"{model_date} 22:06:38 CST"
    factor_map = {factor["key"]: factor for factor in factors}
    framework = []
    for item in MACRO_FRAMEWORK:
        dimension = dict(item)
        dimension["observations"] = observations.get(item["key"], [])
        if not item.get("factor_key") and dimension["observations"]:
            dimension["coverage"] = "official"
            dimension["state"] = "已更新"
            first = dimension["observations"][0]
            dimension["headline"] = f"{first['title']} {first['display']}"
        live_factor = factor_map.get(item.get("factor_key"))
        if live_factor:
            dimension.update(
                {
                    "state": live_factor.get("state"),
                    "score": live_factor.get("score"),
                    "headline": live_factor.get("headline"),
                    "as_of": live_factor.get("as_of"),
                }
            )
        elif not dimension["observations"]:
            dimension.update(
                {
                    "state": "待接入",
                    "score": None,
                    "headline": "等待稳定的官方免费时序数据",
                    "as_of": None,
                }
            )
        else:
            dimension["score"] = None
            dimension["as_of"] = max(str(observation["as_of"]) for observation in dimension["observations"] if observation.get("as_of"))
        framework.append(dimension)
    payload = {
        "version": 2,
        "generated_at": generated_at,
        "timezone": "Asia/Shanghai",
        "market": "CN",
        "model_version": "A-share Macro & Financial Conditions v2",
        "framework": framework,
        "scoring_scope": {
            "mode": "timely_three_factor_gate",
            "included": ["rates_fixed_income", "overseas_deleveraging", "margin_financing"],
            "excluded_until_official_series": ["credit_impulse", "growth_cycle", "inflation_margin"],
            "note": "日度风险闸门只使用可及时更新的三项代理；月度官方数据作为背景层，缺失时不计分。",
        },
        "factors": factors,
        "constraint": constraint,
        "source_audit": {
            "rates_proxies": list(RATE_CODES),
            "overseas_proxies": list(OVERSEAS_CODES),
            "margin_source": "eastmoney RPTA_RZRQ_LSHJ",
            "price_source": "tencent qfq day bars",
            "failures": failures,
        },
        "disclaimer": "中观约束只压缩权益风险预算与追高资格，不替代ETF关键位触发。",
    }
    atomic_write(OUTPUT, payload)
    applied = apply_to_recommendations(constraint, factors, generated_at)
    print(
        json.dumps(
            {
                "status": "ok",
                "headwind_level": constraint["headwind_level"],
                "label": constraint["label"],
                "position": constraint["position"],
                "factors": [{k: f[k] for k in ("key", "state", "score", "headline")} for f in factors],
                "applied": applied,
                "failures": failures,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
