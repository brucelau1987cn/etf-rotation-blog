#!/usr/bin/env python3
"""Generate the top briefing block for /futures-compass/."""
from __future__ import annotations

import argparse
import calendar
import json
import tempfile
import os
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "public/data/futures-compass-briefing.json"
MCP_CALENDAR_URL = "https://etf.peekabo.cc/api/public/v1/jin10-mcp-calendar"
UA = "Mozilla/5.0 ETF-Compass-Futures-Briefing/1.0"

ASSET_KEYWORDS = {
    "碳酸锂": ("碳酸锂", "锂矿", "锂电", "新能源材料"),
    "多晶硅": ("多晶硅", "光伏", "硅料"),
    "工业硅": ("工业硅", "有机硅"),
    "黄金": ("黄金", "贵金属"),
    "白银": ("白银", "贵金属"),
    "铜": ("铜", "有色金属"),
    "沪铜": ("铜", "有色金属"),
    "铝": ("铝", "有色金属"),
    "沪铝": ("铝", "有色金属"),
    "原油": ("原油", "石油", "OPEC", "油气"),
    "生猪": ("生猪", "猪肉", "养殖"),
    "猪肉": ("生猪", "猪肉", "养殖"),
    "焦煤": ("焦煤", "炼焦煤", "煤炭", "双焦"),
}
POLICY_KEYWORDS = ("政策", "规划", "国务院", "发改委", "工信部", "财政部", "商务部", "央行", "国家能源局", "补贴", "产能", "储备", "关税", "出口", "进口")
FED_KEYWORDS = ("美联储", "FOMC", "鲍威尔", "非农", "失业率", "CPI", "PCE", "零售销售", "GDP", "初请失业金", "联邦基金利率")
FED_COUNTRY_PREFIX = "美国"


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=35) as response:
        return json.load(response)


def third_friday(year: int, month: int) -> date:
    month_calendar = calendar.monthcalendar(year, month)
    fridays = [week[calendar.FRIDAY] for week in month_calendar if week[calendar.FRIDAY]]
    return date(year, month, fridays[2])


def next_cffex_delivery(today: date) -> dict[str, Any]:
    delivery = third_friday(today.year, today.month)
    if today > delivery:
        year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        delivery = third_friday(year, month)
    return {
        "month": delivery.strftime("%Y-%m"),
        "date": delivery.isoformat(),
        "weekday": "周五",
        "days_note": "中国金融期货交易所股指期货合约交割日通常为合约到期月份的第三个周五；遇国家法定假日顺延。",
        "symbols": ["IF", "IH", "IC", "IM"],
    }


def item_date(raw_time: Any) -> str:
    text = str(raw_time or "")
    return text[:10] if len(text) >= 10 else date.today().isoformat()


def build_policy_items(news_rows: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in news_rows:
        title = " ".join(str(row.get("title") or "").split())
        summary = " ".join(str(row.get("summary") or row.get("content") or "").split())
        combined = f"{title} {summary}"
        scopes = [name for name, keywords in ASSET_KEYWORDS.items() if any(keyword.lower() in combined.lower() for keyword in keywords)]
        if not scopes or not any(keyword in combined for keyword in POLICY_KEYWORDS):
            continue
        key = title.lower()
        if not title or key in seen:
            continue
        output.append({
            "title": title[:100],
            "scope": " · ".join(scopes[:4]),
            "impact": (summary or f"政策涉及{'、'.join(scopes[:4])}，用于复核相关期货品种的供需预期。")[:180],
            "as_of": item_date(row.get("time") or row.get("published_at")),
            "source": "金十数据",
            "url": str(row.get("url") or "/futures-compass/jin10/"),
        })
        seen.add(key)
        if len(output) >= limit:
            break
    return output


def value_summary(row: dict[str, Any]) -> str:
    parts = []
    if row.get("previous") not in (None, ""):
        parts.append(f"前值 {row['previous']}")
    if row.get("consensus") not in (None, ""):
        parts.append(f"预期 {row['consensus']}")
    if row.get("actual") not in (None, ""):
        parts.append(f"公布 {row['actual']}")
    return " · ".join(parts) or "等待数据公布"


def build_fed_watch(rows: list[dict[str, Any]], today: date | None = None, limit: int = 2) -> dict[str, Any]:
    current = today or date.today()
    filtered = []
    for row in rows:
        title = str(row.get("title") or "")
        relevant = any(keyword.lower() in title.lower() for keyword in FED_KEYWORDS)
        us_or_fed = title.startswith(FED_COUNTRY_PREFIX) or any(keyword.lower() in title.lower() for keyword in ("美联储", "FOMC", "鲍威尔"))
        if int(row.get("star") or 0) >= 3 and relevant and us_or_fed:
            filtered.append(row)
    filtered.sort(key=lambda row: (str(row.get("time") or "") < current.isoformat(), str(row.get("time") or "")), reverse=False)
    future = [row for row in filtered if str(row.get("time") or "")[:10] >= current.isoformat()]
    past = sorted((row for row in filtered if str(row.get("time") or "")[:10] < current.isoformat()), key=lambda row: str(row.get("time") or ""), reverse=True)
    selected = (future + past)[:limit]
    latest = []
    for row in selected:
        direction = str(row.get("impact") or row.get("affect_txt") or "影响待确认")
        latest.append({
            "time": str(row.get("time") or "") + " 北京时间",
            "event": str(row.get("title") or "未命名事项"),
            "result": value_summary(row),
            "impact": f"金十方向：{direction}；结合美元、贵金属、工业金属及原油走势复核。",
        })
    return {
        "latest": latest,
        "next_focus": "重点跟踪高星级美国通胀、就业、增长数据及美联储决议与官员讲话。",
        "source": "金十数据 API",
        "calendar_url": "https://rili.jin10.com/",
    }


def fetch_calendar_rows() -> list[dict[str, Any]]:
    payload = get_json(MCP_CALENDAR_URL)
    rows = payload.get("items")
    if payload.get("status") != "ok" or not isinstance(rows, list):
        raise RuntimeError("Jin10 MCP calendar unavailable")
    return rows


def fetch_mcp_news(tool: str, keyword: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"tool": tool, "keyword": keyword})
    payload = get_json(f"{MCP_CALENDAR_URL}?{query}")
    rows = payload.get("items") or []
    return rows if isinstance(rows, list) else []


def fetch_policy_news() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for keyword in ("多晶硅", "碳酸锂", "工业硅", "有色金属", "原油", "生猪"):
        try:
            rows.extend(fetch_mcp_news("search_news", keyword))
        except Exception:
            continue
    return rows


def build_briefing(today: date, calendar_rows: list[dict[str, Any]], news_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "index_delivery": next_cffex_delivery(today),
        "industry_policy": build_policy_items(news_rows),
        "fed_watch": build_fed_watch(calendar_rows, today=today),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    previous = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {}
    failures: dict[str, str] = {}
    try:
        calendar_rows = fetch_calendar_rows()
    except Exception as exc:
        failures["calendar"] = str(exc)
        calendar_rows = []
    try:
        news_rows = fetch_policy_news()
    except Exception as exc:
        failures["news"] = str(exc)
        news_rows = []
    payload = build_briefing(args.date, calendar_rows, news_rows)
    if not payload["industry_policy"]:
        payload["industry_policy"] = previous.get("industry_policy", [])
    if not payload["fed_watch"]["latest"]:
        payload["fed_watch"] = previous.get("fed_watch", payload["fed_watch"])
    payload["data_quality"] = {"failed": len(failures), "failures": failures}
    atomic_json(args.output, payload)
    print(json.dumps({"delivery": payload["index_delivery"]["date"], "policy": len(payload["industry_policy"]), "fed": len(payload["fed_watch"].get("latest", [])), "failed": len(failures)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
