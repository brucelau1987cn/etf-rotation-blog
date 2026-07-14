#!/usr/bin/env python3
"""Generate A-share mid-horizon macro constraint dashboard.

Three factors only:
1) Rates / fixed-income relative strength
2) Overseas ETF deleveraging pressure
3) Margin financing inventory trend

This layer constrains equity risk budget and chase eligibility.
It does not replace price-level action triggers.
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
        "label": "加息预期/固收相对强弱",
        "state": state,
        "score": round(score, 2),
        "headline": reasons[0] if reasons else "固收与权益相对数据不足",
        "reasons": reasons,
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
        score += 0.5
        reasons.append(f"海外相关池弱化占比{weak_share:.0%}")
    if avg20 is not None and avg20 <= -3:
        score += 0.5
        reasons.append(f"海外映射20日仍弱{avg20:+.2f}%")
    score = clamp(score)
    state = "逆风" if score >= 1.5 else "观察" if score >= 0.8 else "中性"
    return {
        "key": "overseas_deleveraging",
        "label": "国际市场ETF去杠杆压力",
        "state": state,
        "score": round(score, 2),
        "headline": reasons[0] if reasons else "海外映射数据不足",
        "reasons": reasons,
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
        "label": "两融余额趋势",
        "state": state,
        "score": round(score, 2),
        "headline": reasons[0] if reasons else "两融数据不足",
        "reasons": reasons,
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
        "base_position": base_position,
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
                "label": "加息预期/固收相对强弱",
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
                "label": "国际市场ETF去杠杆压力",
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
                "label": "两融余额趋势",
                "state": "观察",
                "score": 0.8,
                "headline": "两融数据暂缺，按观察处理",
                "reasons": [str(exc)],
                "metrics": {},
                "details": {},
                "as_of": None,
            }
        )

    reco = json.loads(RECO.read_text(encoding="utf-8")) if RECO.exists() else {}
    base_position = reco.get("position") if isinstance(reco, dict) else None
    market_state = reco.get("market_state") if isinstance(reco, dict) else None
    if not market_state:
        market_state = (pool.get("market_regime") or {}).get("state")
    if not base_position:
        base_position = (pool.get("market_regime") or {}).get("equity_allocation")
        if isinstance(base_position, str) and not base_position.startswith("权益"):
            # pool stores equity_allocation like 10%-30%
            defense = (pool.get("market_regime") or {}).get("defense_allocation") or ""
            base_position = f"权益{base_position}" + (f"；防御/现金{defense}" if defense else "")

    constraint = build_constraint(factors, base_position if isinstance(base_position, str) else None, market_state if isinstance(market_state, str) else None)
    generated_at = now_cn().strftime("%Y-%m-%d %H:%M:%S CST")
    payload = {
        "version": 1,
        "generated_at": generated_at,
        "timezone": "Asia/Shanghai",
        "market": "CN",
        "model_version": "A-share Mid-Macro Constraint v1",
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
