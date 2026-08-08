#!/usr/bin/env python3
"""Track daily price + profit-ratio history for every stock that ever entered the low-chip screen.

Data source:
- Daily OHLCV (qfq): Tencent fqkline API (web.ifzq.gtimg.cn)
- Daily 收盘获利盘 ratio: iWenCai per-stock query `收盘获利[YYYYMMDD]` (historical dates supported)

Output: public/data/low-chip-tracking.json
  {
    "schema_version": "low-chip-tracking-v1",
    "generated_at": "...",
    "stocks": {
      "600363.SH": {
        "name": "联创光电",
        "first_seen": "2026-08-05",   // first date the stock appeared in a low-chip snapshot
        "last_seen": "2026-08-07",
        "industry": "...",
        "daily": [
          {"date": "2026-08-05", "close": 24.64, "change_pct": -10.0, "profit_ratio": 12.2},
          ...
        ],
        "latest": {"week": ..., "month": ..., "quarter": ...}  // current 周/月/季线获利 (if available)
      }
    }
  }
"""
from __future__ import annotations

import datetime
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/low-chip-tracking.json"
HISTORY_DIR = ROOT / "public/data/low-chip-history"
MIN_TRACK_DAYS = 1  # 至少 1 天数据才展示（刚加入当天即开始记录）
TRACK_WINDOW_DAYS = 14  # 追踪统计窗口：最近 2 周（约 10 个交易日）


def load_history_dates() -> dict[str, list[str]]:
    """Map symbol -> sorted list of dates it appeared in low-chip snapshots."""
    seen: dict[str, list[str]] = {}
    for path in sorted(HISTORY_DIR.glob("????-??-??.json")):
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        day = path.stem
        for code in snap.get("intersection") or []:
            seen.setdefault(code, []).append(day)
    return seen


def tencent_daily(symbol: str, start: str, end: str) -> list[dict]:
    """Daily qfq bars from Tencent. Returns [{date, close, change_pct}]."""
    code = symbol.split(".")[0]
    ex = "sh" if symbol.endswith(".SH") else "sz"
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
        f"param={ex}{code},day,{start},{end},640,qfq"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read())
    data = d.get("data", {}).get(f"{ex}{code}", {})
    kl = data.get("qfqday") or data.get("day") or []
    bars = []
    prev_close = None
    for row in kl:
        # [date, open, close, high, low, volume]
        date, _, close, _, _, _ = row[:6]
        try:
            close_f = float(close)
        except (TypeError, ValueError):
            continue
        chg = None
        if prev_close:
            chg = round((close_f - prev_close) / prev_close * 100, 2)
        bars.append({"date": date, "close": close_f, "change_pct": chg})
        prev_close = close_f
    return bars


def iwencai_profit_ratio(symbol: str, date: str) -> float | None:
    """收盘获利盘 ratio for one symbol at one date (iWenCai historical query)."""
    ymd = date.replace("-", "")
    q = f"{symbol.split('.')[0]} 收盘获利[{ymd}]"
    r = subprocess.run(
        ["/root/.hermes/scripts/iwencai-market-query", "-q", q, "--limit", "2", "--timeout", "45"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return None
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    for row in d.get("datas") or []:
        if str(row.get("股票代码") or "").split(".")[0] != symbol.split(".")[0]:
            continue
        for k, v in row.items():
            if "收盘获利" in k:
                try:
                    return round(float(v), 2)
                except (TypeError, ValueError):
                    return None
    return None


def load_existing() -> dict:
    if DATA.exists():
        try:
            return json.loads(DATA.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"schema_version": "low-chip-tracking-v1", "generated_at": "", "stocks": {}}


def main() -> int:
    seen = load_history_dates()
    existing = load_existing()
    stocks = existing.get("stocks", {})
    today = datetime.date.today().isoformat()

    for symbol, dates in sorted(seen.items()):
        first = min(dates)
        last = max(dates)
        rec = stocks.setdefault(symbol, {"name": "", "first_seen": first, "last_seen": last, "industry": "", "daily": []})
        # 更新 first/last_seen（保留最早加入日）
        rec["first_seen"] = min(rec.get("first_seen") or first, first)
        rec["last_seen"] = last

        # 已有 daily 的日期集合
        have = {d["date"] for d in rec["daily"]}
        # 回填：从 first_seen 到 今天（持续追踪，即使已跌出低筹码池）；
        # 至少覆盖最近 2 周窗口（TRACK_WINDOW_DAYS），窗口起点取更早者
        window_start = (datetime.date.today() - datetime.timedelta(days=TRACK_WINDOW_DAYS)).isoformat()
        backfill_start = min(rec["first_seen"], window_start)
        bars = tencent_daily(symbol, backfill_start, today)
        new_rows = []
        for bar in bars:
            if bar["date"] in have:
                continue
            pr = iwencai_profit_ratio(symbol, bar["date"])
            new_rows.append({**bar, "profit_ratio": pr})
            print(f"  {symbol} {bar['date']}: close={bar['close']} chg={bar['change_pct']} profit={pr}", flush=True)
        rec["daily"].extend(new_rows)
        rec["daily"].sort(key=lambda x: x["date"])

    # 清理：少于 MIN_TRACK_DAYS 天数的股票（刚加入、数据不足）
    for sym in [s for s, r in stocks.items() if len(r.get("daily", [])) < MIN_TRACK_DAYS]:
        print(f"  drop {sym}: only {len(stocks[sym]['daily'])} bars", flush=True)
        stocks.pop(sym, None)

    # 补名称/行业（从历史快照的 enrichments 或 periods）
    snaps = sorted(HISTORY_DIR.glob("????-??-??.json"), reverse=True)
    name_map: dict[str, str] = {}
    industry_map: dict[str, str] = {}
    for p in snaps:
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        enr = s.get("enrichments") or {}
        for sym, e in enr.items():
            industry_map.setdefault(sym, e.get("industry", ""))
        for period_rows in s.get("periods", {}).values():
            for row in period_rows:
                name_map.setdefault(row["symbol"], row.get("name", ""))
    for sym, r in stocks.items():
        r["name"] = r.get("name") or name_map.get(sym, "")
        r["industry"] = r.get("industry") or industry_map.get(sym, "")

    DATA.write_text(json.dumps({"schema_version": "low-chip-tracking-v1", "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "stocks": stocks}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"stocks": len(stocks), "total_bars": sum(len(r["daily"]) for r in stocks.values())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
