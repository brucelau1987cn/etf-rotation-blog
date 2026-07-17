#!/usr/bin/env python3
"""Backtest ETF Garden red/green prediction records against historical closes.

Outputs:
- public/data/etf-garden-backtest.json
- public/data/etf-garden-backtest.md

Definition:
- Red / 准备种花: success when target-day close > previous close, and separately when next close > target close.
- Green / 准备摘花: success when target-day close < previous close, and separately when next close < target close.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import importlib.util
import json
import math
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BLOG_DIR = ROOT / "src" / "content" / "blog"
OUT_JSON = ROOT / "public" / "data" / "etf-garden-backtest.json"
OUT_MD = ROOT / "public" / "data" / "etf-garden-backtest.md"
STOCK_API_PACKAGE = "stock-api@2.7.3"

spec = importlib.util.spec_from_file_location("generate_garden_pool", ROOT / "scripts" / "generate_garden_pool.py")
if spec is None or spec.loader is None:
    raise RuntimeError("Cannot load scripts/generate_garden_pool.py")
garden = importlib.util.module_from_spec(spec)
spec.loader.exec_module(garden)
POOL = {x["code"]: x for x in garden.GARDEN_POOL}

SECTION_RED = re.compile(r"^###\s*🔴.*?(?:预测|推荐)?.*?(?:准备种花|种花|红色主名单)")
SECTION_GREEN = re.compile(r"^###\s*🟢.*?(?:预测|推荐)?.*?(?:准备摘花|摘花|绿色主名单)")
SECTION_STOP = re.compile(r"^###\s+")
HEADING = re.compile(r"^##\s+(.+?)\s*$")
CODE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
DATE_CN = re.compile(r"(\d{1,2})月(\d{1,2})日")
DATE_ISO = re.compile(r"20\d{2}-\d{2}-\d{2}")
TARGET_DATE = re.compile(r"目标交易日\s*[=＝:]?\s*(20\d{2}-\d{2}-\d{2})")


def safe_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return math.nan


def pct(a: float, b: float) -> float:
    return (a / b - 1) * 100 if b and math.isfinite(a) and math.isfinite(b) else math.nan


def infer_date_from_heading(heading: str, fallback_year: int = 2026) -> str | None:
    m = DATE_ISO.search(heading)
    if m:
        return m.group(0)
    m = DATE_CN.search(heading)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        return dt.date(fallback_year, month, day).isoformat()
    return None


def extract_predictions_from_text(path: Path, text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current_date: str | None = None
    current_heading = ""
    current_side: str | None = None
    current_section = ""
    file_date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", path.name)
    file_date = file_date_match.group(1) if file_date_match else None

    for line in text.splitlines():
        hm = HEADING.match(line)
        if hm:
            current_heading = hm.group(1)
            hdate = infer_date_from_heading(current_heading)
            current_date = hdate or file_date
            tm = TARGET_DATE.search(line)
            if tm:
                current_date = tm.group(1)
            current_side = None
            current_section = ""
            continue

        # target date often sits in the paragraph below the heading
        if current_date and "目标交易日" in line:
            tm = TARGET_DATE.search(line)
            if tm:
                current_date = tm.group(1)

        if SECTION_RED.match(line):
            current_side = "red"
            current_section = re.sub(r"^#+\s*", "", line).strip()
            continue
        if SECTION_GREEN.match(line):
            current_side = "green"
            current_section = re.sub(r"^#+\s*", "", line).strip()
            continue
        if SECTION_STOP.match(line):
            current_side = None
            current_section = ""
            continue
        if not current_side or not current_date:
            continue
        if not line.lstrip().startswith("-"):
            continue
        for code in CODE.findall(line):
            if code not in POOL:
                continue
            records.append({
                "source_file": str(path.relative_to(ROOT)),
                "source_heading": current_heading,
                "section": current_section,
                "target_date": current_date,
                "side": current_side,
                "code": code,
                "name": POOL[code]["name"],
                "raw": line.strip(),
            })
    # dedupe same file/date/side/code, keeping first mention
    seen = set()
    out = []
    for r in records:
        key = (r["source_file"], r["target_date"], r["side"], r["code"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def extract_predictions() -> list[dict[str, Any]]:
    files = [BLOG_DIR / "garden" / "etf-garden-archive.md"]
    files += sorted(BLOG_DIR.glob("2026-*.md"))
    records: list[dict[str, Any]] = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        records.extend(extract_predictions_from_text(p, text))
    # global dedupe: same target date/side/code may appear in archive and daily page; keep archive/manual first.
    seen = set()
    out = []
    for r in records:
        key = (r["target_date"], r["side"], r["code"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return sorted(out, key=lambda x: (x["target_date"], x["side"], x["code"]))


def market_prefix(item: dict[str, str]) -> str:
    return "SH" if item["market"] == "XSHG" else "SZ"


def fetch_klines(code: str, count: int = 180) -> list[dict[str, Any]]:
    item = POOL[code]
    stock_code = f"{market_prefix(item)}{code}"
    cmd = ["npx", "-y", STOCK_API_PACKAGE, "get-klines", stock_code, "--period", "day", "--count", str(count), "--adjust", "qfq", "--source", "auto"]
    for attempt in range(3):
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=90)
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                out = json.loads(proc.stdout)
                rows = out if isinstance(out, list) else out.get("klines") or out.get("data") or out.get("rows") or []
                parsed = []
                for k in rows:
                    if isinstance(k, dict) and k.get("date"):
                        parsed.append({"date": str(k.get("date")), "open": safe_float(k.get("open")), "close": safe_float(k.get("close")), "high": safe_float(k.get("high")), "low": safe_float(k.get("low"))})
                parsed = sorted([x for x in parsed if math.isfinite(x["close"])], key=lambda x: x["date"])
                if parsed:
                    return parsed
            except Exception:
                pass
        time.sleep(0.5 * (attempt + 1))
    # Direct Tencent fallback avoids losing the public scorecard when the CLI's
    # provider auto-detection or one upstream source is temporarily degraded.
    symbol = stock_code.lower()
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + urllib.parse.urlencode({"param": f"{symbol},day,,,{count},qfq"})
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.load(response)
            block = payload.get("data", {}).get(symbol, {})
            rows = block.get("qfqday") or block.get("day") or []
            parsed = []
            for row in rows:
                if len(row) < 5:
                    continue
                parsed.append({"date": str(row[0]), "open": safe_float(row[1]), "close": safe_float(row[2]), "high": safe_float(row[3]), "low": safe_float(row[4])})
            parsed = sorted([x for x in parsed if math.isfinite(x["close"])], key=lambda x: x["date"])
            if parsed:
                return parsed
        except Exception:
            time.sleep(0.6 * (attempt + 1))
    return []


def attach_returns(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    codes = sorted({r["code"] for r in records})
    kmap: dict[str, list[dict[str, Any]]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fetch_klines, code): code for code in codes}
        for fut in concurrent.futures.as_completed(futs):
            code = futs[fut]
            try:
                kmap[code] = fut.result()
            except Exception:
                kmap[code] = []
    enriched = []
    for r in records:
        rows = kmap.get(r["code"], [])
        idx = next((i for i, x in enumerate(rows) if x["date"] >= r["target_date"]), None)
        if idx is None or idx <= 0 or idx >= len(rows):
            continue
        target = rows[idx]
        prev = rows[idx - 1]
        next1 = rows[idx + 1] if idx + 1 < len(rows) else None
        next3_rows = rows[idx + 1: idx + 4]
        day_ret = pct(target["close"], prev["close"])
        next1_ret = pct(next1["close"], target["close"]) if next1 else math.nan
        next3_ret = pct(next3_rows[-1]["close"], target["close"]) if len(next3_rows) >= 3 else math.nan
        next3_min = min((pct(x["close"], target["close"]) for x in next3_rows), default=math.nan)
        next3_max = max((pct(x["close"], target["close"]) for x in next3_rows), default=math.nan)
        side = r["side"]
        day_hit = day_ret > 0 if side == "red" else day_ret < 0
        next1_hit = next1_ret > 0 if side == "red" else next1_ret < 0
        next3_hit = next3_ret > 0 if side == "red" else next3_ret < 0
        excursion_hit = next3_max > 1.0 if side == "red" else next3_min < -1.0
        enriched.append({**r,
            "actual_trade_date": target["date"],
            "prev_close": round(prev["close"], 4),
            "target_close": round(target["close"], 4),
            "next_close": round(next1["close"], 4) if next1 else None,
            "day_ret_pct": round(day_ret, 2),
            "next1_ret_pct": round(next1_ret, 2) if math.isfinite(next1_ret) else None,
            "next3_ret_pct": round(next3_ret, 2) if math.isfinite(next3_ret) else None,
            "next3_min_pct": round(next3_min, 2) if math.isfinite(next3_min) else None,
            "next3_max_pct": round(next3_max, 2) if math.isfinite(next3_max) else None,
            "day_hit": bool(day_hit),
            "next1_hit": bool(next1_hit) if math.isfinite(next1_ret) else None,
            "next3_hit": bool(next3_hit) if math.isfinite(next3_ret) else None,
            "excursion_hit": bool(excursion_hit) if math.isfinite(next3_min) else None,
        })
    return enriched, kmap


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    def bucket(rows: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(rows)
        if not n:
            return {"count": 0}
        def hit_rate(key: str) -> float:
            vals = [r[key] for r in rows if r.get(key) is not None]
            return round(sum(bool(x) for x in vals) / len(vals) * 100, 1) if vals else math.nan
        def avg_key(key: str) -> float:
            vals = [safe_float(r.get(key)) for r in rows]
            vals = [x for x in vals if math.isfinite(x)]
            return round(sum(vals) / len(vals), 2) if vals else math.nan
        return {
            "count": n,
            "day_hit_rate": hit_rate("day_hit"),
            "next1_hit_rate": hit_rate("next1_hit"),
            "next3_hit_rate": hit_rate("next3_hit"),
            "excursion_hit_rate": hit_rate("excursion_hit"),
            "avg_day_ret_pct": avg_key("day_ret_pct"),
            "avg_next1_ret_pct": avg_key("next1_ret_pct"),
            "avg_next3_ret_pct": avg_key("next3_ret_pct"),
        }
    by_side = {side: bucket([r for r in records if r["side"] == side]) for side in ["red", "green"]}
    by_date = {d: bucket([r for r in records if r["target_date"] == d]) for d in sorted({r["target_date"] for r in records})}
    by_code = []
    code_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        code_groups[r["code"]].append(r)
    for code, rows in code_groups.items():
        b = bucket(rows)
        b.update({"code": code, "name": rows[0]["name"], "red_count": sum(1 for x in rows if x["side"] == "red"), "green_count": sum(1 for x in rows if x["side"] == "green")})
        by_code.append(b)
    by_code.sort(key=lambda x: (x.get("count", 0), x.get("day_hit_rate", 0)), reverse=True)
    misses = [r for r in records if r.get("day_hit") is False]
    misses.sort(key=lambda r: abs(safe_float(r.get("day_ret_pct"))), reverse=True)
    best = sorted(records, key=lambda r: safe_float(r.get("day_ret_pct")) * (1 if r["side"] == "red" else -1), reverse=True)[:12]
    worst = sorted(records, key=lambda r: safe_float(r.get("day_ret_pct")) * (1 if r["side"] == "red" else -1))[:12]
    return {
        "overall": bucket(records),
        "by_side": by_side,
        "by_date": by_date,
        "top_codes": by_code[:20],
        "worst_day_misses": misses[:20],
        "best_cases": best,
        "worst_cases": worst,
        "side_counts": Counter(r["side"] for r in records),
    }


def md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    head = rows[0]
    out = ["| " + " | ".join(map(str, head)) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for row in rows[1:]:
        out.append("| " + " | ".join(map(str, row)) + " |")
    return "\n".join(out)


def render_md(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    overall = s["overall"]
    side = s["by_side"]
    now = payload["generated_at"]
    lines = [
        "# ETF花园预测回测 v1",
        "",
        f"> 生成时间：{now}；样本来自 ETF花园归档与每日复盘 Markdown；行情源：stock-api package v2.7.3。",
        "",
        "## 总览",
        "",
        md_table([
            ["口径", "样本数", "当日命中", "次日命中", "3日方向命中", "3日触达命中", "当日均值", "次日均值"],
            ["全部", overall["count"], f'{overall["day_hit_rate"]}%', f'{overall["next1_hit_rate"]}%', f'{overall["next3_hit_rate"]}%', f'{overall["excursion_hit_rate"]}%', f'{overall["avg_day_ret_pct"]}%', f'{overall["avg_next1_ret_pct"]}%'],
            ["🔴 种花", side["red"]["count"], f'{side["red"]["day_hit_rate"]}%', f'{side["red"]["next1_hit_rate"]}%', f'{side["red"]["next3_hit_rate"]}%', f'{side["red"]["excursion_hit_rate"]}%', f'{side["red"]["avg_day_ret_pct"]}%', f'{side["red"]["avg_next1_ret_pct"]}%'],
            ["🟢 摘花", side["green"]["count"], f'{side["green"]["day_hit_rate"]}%', f'{side["green"]["next1_hit_rate"]}%', f'{side["green"]["next3_hit_rate"]}%', f'{side["green"]["excursion_hit_rate"]}%', f'{side["green"]["avg_day_ret_pct"]}%', f'{side["green"]["avg_next1_ret_pct"]}%'],
        ]),
        "",
        "## 按日期",
        "",
    ]
    date_rows = [["日期", "样本", "当日命中", "次日命中", "当日均值", "次日均值"]]
    for d, b in s["by_date"].items():
        date_rows.append([d, b.get("count"), f'{b.get("day_hit_rate")}% ', f'{b.get("next1_hit_rate")}% ', f'{b.get("avg_day_ret_pct")}% ', f'{b.get("avg_next1_ret_pct")}% '])
    lines.append(md_table(date_rows))
    lines += ["", "## 高频ETF表现", ""]
    code_rows = [["代码", "名称", "样本", "红/绿", "当日命中", "当日均值", "次日命中"]]
    for b in s["top_codes"][:15]:
        code_rows.append([b["code"], b["name"], b["count"], f'{b["red_count"]}/{b["green_count"]}', f'{b.get("day_hit_rate")}% ', f'{b.get("avg_day_ret_pct")}% ', f'{b.get("next1_hit_rate")}% '])
    lines.append(md_table(code_rows))
    lines += ["", "## 最大反向样本", ""]
    miss_rows = [["日期", "侧", "代码", "名称", "当日", "次日", "来源"]]
    for r in s["worst_day_misses"][:12]:
        miss_rows.append([r["target_date"], "🔴" if r["side"] == "red" else "🟢", r["code"], r["name"], f'{r["day_ret_pct"]}%', f'{r.get("next1_ret_pct")}% ', Path(r["source_file"]).name])
    lines.append(md_table(miss_rows))
    lines += ["", "## 方法说明", "", "- 🔴 种花当日命中：目标交易日收盘价高于前一交易日收盘价。", "- 🟢 摘花当日命中：目标交易日收盘价低于前一交易日收盘价。", "- 次日命中：目标交易日收盘到下一交易日收盘的方向验证。", "- 3日触达命中：🔴 后3日最高涨幅超过 +1%；🟢 后3日最低跌幅超过 -1%。"]
    return "\n".join(lines) + "\n"


def main() -> int:
    start = time.time()
    raw = extract_predictions()
    enriched, kmap = attach_returns(raw)
    payload = {
        "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S UTC+08:00"),
        "strategy": "ETF Garden red/green prediction backtest v1",
        "source_files": ["src/content/blog/garden/etf-garden-archive.md", "src/content/blog/2026-*.md"],
        "quote_source": STOCK_API_PACKAGE + " (qfq adjusted klines)",
        "raw_prediction_count": len(raw),
        "evaluated_count": len(enriched),
        "failed_codes": sorted([c for c, rows in kmap.items() if not rows]),
        "summary": summarize(enriched),
        "records": enriched,
    }
    def json_safe(value: Any) -> Any:
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key: json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [json_safe(item) for item in value]
        return value

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(json_safe(payload), ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    OUT_MD.write_text(render_md(payload), encoding="utf-8")
    print(f"✅ ETF花园回测完成：原始信号 {len(raw)}，可评估 {len(enriched)}，输出 {OUT_JSON.relative_to(ROOT)} + {OUT_MD.relative_to(ROOT)}，耗时 {time.time()-start:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
